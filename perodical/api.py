"""Async API client for Periodical."""
from __future__ import annotations

import asyncio
import logging
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

DEFAULT_REQUEST_TIMEOUT_SECONDS = 30
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0
MAX_RETRY_DELAY_SECONDS = 60

HTTP_AUTH_STATUSES = frozenset({401, 403})

HTTP_RETRY_STATUSES = frozenset(
    {
        408,
        421,
        425,
        429,
        500,
        502,
        503,
        504,
        520,
        521,
        522,
        523,
        524,
    }
)

HTTP_FAIL_STATUSES = frozenset(
    status
    for status in range(400, 600)
    if status not in HTTP_AUTH_STATUSES and status not in HTTP_RETRY_STATUSES
)

HTTP_SUCCESS_RANGE = range(200, 300)
HTTP_INFORMATIONAL_RANGE = range(100, 200)
HTTP_REDIRECT_RANGE = range(300, 400)

HTTP_STATUS_POLICY: dict[int, str] = {
    **{status: "ignore_informational" for status in HTTP_INFORMATIONAL_RANGE},
    **{status: "success" for status in HTTP_SUCCESS_RANGE},
    **{status: "fail_redirect" for status in HTTP_REDIRECT_RANGE},
    **{status: "fail" for status in HTTP_FAIL_STATUSES},
    **{status: "auth_fail" for status in HTTP_AUTH_STATUSES},
    **{status: "retry" for status in HTTP_RETRY_STATUSES},
}


class PeriodicalApiError(Exception):
    """Raised when the API returns an error."""


class PeriodicalAuthError(PeriodicalApiError):
    """Raised on authentication failures."""


class PeriodicalApi:
    """Async client for the Periodical REST API."""

    def __init__(self, base_url: str, api_key: str, session: aiohttp.ClientSession) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT_SECONDS)

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        }

    @staticmethod
    def _status_policy(status: int) -> str:
        if status in HTTP_STATUS_POLICY:
            return HTTP_STATUS_POLICY[status]
        if 100 <= status <= 199:
            return "ignore_informational"
        if 200 <= status <= 299:
            return "success"
        if 300 <= status <= 399:
            return "fail_redirect"
        if 400 <= status <= 499:
            return "fail"
        if 500 <= status <= 599:
            return "retry"
        return "fail"

    @staticmethod
    def _retry_after_seconds(value: str | None) -> float | None:
        if not value:
            return None

        try:
            seconds = float(value)
            if seconds >= 0:
                return min(seconds, MAX_RETRY_DELAY_SECONDS)
        except (TypeError, ValueError):
            pass

        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at is None:
                return None
            delay = retry_at.timestamp() - asyncio.get_running_loop().time()
            if delay > 0:
                return min(delay, MAX_RETRY_DELAY_SECONDS)
        except (TypeError, ValueError, OverflowError):
            return None

        return None

    @staticmethod
    async def _response_text(resp: aiohttp.ClientResponse) -> str:
        try:
            text = await resp.text()
        except Exception:  # noqa: BLE001
            return ""
        return text.strip()

    def _retry_delay(self, attempt: int, retry_after: str | None = None) -> float:
        retry_after_delay = self._retry_after_seconds(retry_after)
        if retry_after_delay is not None:
            return retry_after_delay

        delay = DEFAULT_RETRY_BACKOFF_SECONDS * (2 ** max(attempt - 1, 0))
        return min(delay, MAX_RETRY_DELAY_SECONDS)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._base_url}{path}"
        last_error: Exception | None = None

        for attempt in range(1, DEFAULT_RETRY_ATTEMPTS + 1):
            try:
                async with self._session.get(
                    url,
                    headers=self._headers,
                    params=params,
                    timeout=self._timeout,
                ) as resp:
                    status = resp.status
                    policy = self._status_policy(status)

                    if policy == "success":
                        try:
                            return await resp.json(content_type=None)
                        except Exception as err:  # noqa: BLE001
                            text = await self._response_text(resp)
                            raise PeriodicalApiError(
                                f"Invalid JSON response from {path}: HTTP {status}: {text}"
                            ) from err

                    text = await self._response_text(resp)

                    if policy == "auth_fail":
                        if status == 401:
                            raise PeriodicalAuthError(f"HTTP 401 Unauthorized: Invalid API key: {text}")
                        raise PeriodicalAuthError(f"HTTP 403 Forbidden: Access denied: {text}")

                    if policy == "retry":
                        last_error = PeriodicalApiError(
                            f"HTTP {status} retryable error on {path}: {text}"
                        )

                        if attempt < DEFAULT_RETRY_ATTEMPTS:
                            delay = self._retry_delay(attempt, resp.headers.get("Retry-After"))
                            _LOGGER.warning(
                                "Periodical API retry %s/%s for %s after HTTP %s, waiting %.1fs",
                                attempt,
                                DEFAULT_RETRY_ATTEMPTS,
                                path,
                                status,
                                delay,
                            )
                            await asyncio.sleep(delay)
                            continue

                        raise PeriodicalApiError(
                            f"HTTP {status} retryable error on {path}, retries exhausted: {text}"
                        )

                    if policy == "fail_redirect":
                        location = resp.headers.get("Location")
                        raise PeriodicalApiError(
                            f"HTTP {status} redirect returned for {path}; Location={location}; body={text}"
                        )

                    raise PeriodicalApiError(f"HTTP {status} non-retryable error on {path}: {text}")

            except (asyncio.TimeoutError, aiohttp.ServerTimeoutError) as err:
                last_error = PeriodicalApiError(
                    f"Timeout after {DEFAULT_REQUEST_TIMEOUT_SECONDS}s on {path}"
                )

                if attempt < DEFAULT_RETRY_ATTEMPTS:
                    delay = self._retry_delay(attempt)
                    _LOGGER.warning(
                        "Periodical API timeout retry %s/%s for %s, waiting %.1fs",
                        attempt,
                        DEFAULT_RETRY_ATTEMPTS,
                        path,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                raise PeriodicalApiError(
                    f"Timeout after {DEFAULT_REQUEST_TIMEOUT_SECONDS}s on {path}, retries exhausted"
                ) from err

            except aiohttp.ClientError as err:
                last_error = PeriodicalApiError(f"Connection error on {path}: {err}")

                if attempt < DEFAULT_RETRY_ATTEMPTS:
                    delay = self._retry_delay(attempt)
                    _LOGGER.warning(
                        "Periodical API connection retry %s/%s for %s, waiting %.1fs: %s",
                        attempt,
                        DEFAULT_RETRY_ATTEMPTS,
                        path,
                        delay,
                        err,
                    )
                    await asyncio.sleep(delay)
                    continue

                raise PeriodicalApiError(
                    f"Connection error on {path}, retries exhausted: {err}"
                ) from err

        if last_error is not None:
            raise last_error

        raise PeriodicalApiError(f"Unknown API error on {path}")

    async def get_me(self) -> dict[str, Any]:
        """GET /me — basic info about the authenticated user."""
        return await self._get("/me")

    async def list_users(self) -> list[dict[str, Any]]:
        """GET /users — all active users."""
        return await self._get("/users")

    async def get_user_status(self, user_id: int) -> dict[str, Any]:
        """GET /users/{user_id}/status — today's status."""
        return await self._get(f"/users/{user_id}/status")

    async def get_schedule_today(self, user_id: int) -> dict[str, Any]:
        """GET /users/{user_id}/schedule/today — today's schedule."""
        return await self._get(f"/users/{user_id}/schedule/today")

    async def get_schedule_month(self, user_id: int) -> dict[str, Any]:
        """GET /users/{user_id}/schedule/month — current month schedule."""
        return await self._get(f"/users/{user_id}/schedule/month")

    async def get_schedule_year(self, user_id: int, year: int | None = None) -> dict[str, Any]:
        """GET /users/{user_id}/schedule/year — full year schedule."""
        params = {"year": year} if year else None
        return await self._get(f"/users/{user_id}/schedule/year", params=params)

    async def get_schedule_week(self, user_id: int, date: str) -> dict[str, Any]:
        """GET /users/{user_id}/schedule/week/{date} — week schedule."""
        return await self._get(f"/users/{user_id}/schedule/week/{date}")

    async def get_schedule_range(
        self,
        user_id: int,
        from_date: str,
        to_date: str,
    ) -> dict[str, Any]:
        """GET /users/{user_id}/schedule — date range schedule."""
        return await self._get(
            f"/users/{user_id}/schedule",
            params={"from_date": from_date, "to_date": to_date},
        )

    async def get_schedule_date(self, user_id: int, date: str) -> dict[str, Any]:
        """GET /users/{user_id}/schedule/{date} — specific date schedule."""
        return await self._get(f"/users/{user_id}/schedule/{date}")

    async def get_pay_month(
        self,
        user_id: int,
        year: int | None = None,
        month: int | None = None,
    ) -> dict[str, Any]:
        """GET /users/{user_id}/pay/month — monthly pay summary."""
        params: dict[str, Any] = {}

        if year:
            params["year"] = year
        if month:
            params["month"] = month

        return await self._get(f"/users/{user_id}/pay/month", params=params or None)

    async def get_vacation_balance(
        self,
        user_id: int,
        year: int | None = None,
    ) -> dict[str, Any]:
        """GET /users/{user_id}/vacation/balance — vacation balance."""
        params = {"year": year} if year else None
        return await self._get(f"/users/{user_id}/vacation/balance", params=params)

    async def get_absences(
        self,
        user_id: int,
        year: int | None = None,
    ) -> dict[str, Any]:
        """GET /users/{user_id}/absences — list of absences."""
        params = {"year": year} if year else None
        return await self._get(f"/users/{user_id}/absences", params=params)

    async def get_next_shift(
        self,
        user_id: int,
        date: str | None = None,
        time: str | None = None,
    ) -> dict[str, Any]:
        """GET /users/{user_id}/next-shift — next working day."""
        params: dict[str, Any] = {}

        if date:
            params["date"] = date
        if time:
            params["time"] = time

        return await self._get(f"/users/{user_id}/next-shift", params=params or None)