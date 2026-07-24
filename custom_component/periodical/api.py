"""Async API client for Periodical."""
from __future__ import annotations

import asyncio
import logging
import random
import socket
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp

from .const import MAX_SCHEDULE_RANGE_DAYS

_LOGGER = logging.getLogger(__name__)

DEFAULT_REQUEST_TIMEOUT_SECONDS = 30
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_CONNECT_RETRY_ATTEMPTS = 5
DEFAULT_DNS_RETRY_ATTEMPTS = 6
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0
DEFAULT_DNS_BACKOFF_SECONDS = 5.0
MAX_RETRY_DELAY_SECONDS = 60.0
MAX_ERROR_BODY_LENGTH = 500
MAX_CONCURRENT_REQUESTS = 3
CIRCUIT_FAILURE_WINDOW_SECONDS = 60.0
CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_OPEN_SECONDS = 300.0

HTTP_AUTH_STATUSES = frozenset({401, 403})
HTTP_RETRY_STATUSES = frozenset({408, 421, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524})

POLICY_SUCCESS = "success"
POLICY_AUTH_FAIL = "auth_fail"
POLICY_RETRY = "retry"
POLICY_REDIRECT = "redirect"
POLICY_FAIL = "fail"


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
        self._timeout = aiohttp.ClientTimeout(
            total=DEFAULT_REQUEST_TIMEOUT_SECONDS,
            connect=10,
            sock_connect=10,
            sock_read=20,
        )
        self._request_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        self._network_backoff_until = 0.0
        self._circuit_open_until = 0.0
        self._network_failures: list[float] = []
        self._last_success_utc: str | None = None
        self._last_error_utc: str | None = None
        self._last_error: str | None = None
        self._last_error_path: str | None = None
        self._last_http_status: int | None = None
        self._total_requests = 0
        self._total_attempts = 0
        self._total_failures = 0
        self._total_retries = 0
        self._dns_failures = 0
        self._timeout_failures = 0
        self._connection_failures = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        """Snapshot of client health.  Must be called from the event loop."""
        now = asyncio.get_running_loop().time()
        circuit_open_seconds = max(0.0, self._circuit_open_until - now)
        network_backoff_seconds = max(0.0, self._network_backoff_until - now)
        return {
            "base_url": self._base_url,
            "connected": self._last_success_utc is not None and circuit_open_seconds <= 0,
            "circuit_open": circuit_open_seconds > 0,
            "circuit_open_seconds": round(circuit_open_seconds, 1),
            "network_backoff_seconds": round(network_backoff_seconds, 1),
            "last_success": self._last_success_utc,
            "last_error_time": self._last_error_utc,
            "last_error": self._last_error,
            "last_error_path": self._last_error_path,
            "last_http_status": self._last_http_status,
            "total_requests": self._total_requests,
            "total_attempts": self._total_attempts,
            "total_failures": self._total_failures,
            "total_retries": self._total_retries,
            "dns_failures": self._dns_failures,
            "timeout_failures": self._timeout_failures,
            "connection_failures": self._connection_failures,
        }

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "User-Agent": "ha-periodical (Home Assistant integration)",
        }

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _trim_text(text: str | None) -> str:
        if not text:
            return ""
        text = text.strip()
        return text if len(text) <= MAX_ERROR_BODY_LENGTH else f"{text[:MAX_ERROR_BODY_LENGTH]}..."

    @staticmethod
    def _status_policy(status: int) -> str:
        """Classify an HTTP status.  Single source of truth, total over all ints."""
        if status in HTTP_AUTH_STATUSES:
            return POLICY_AUTH_FAIL
        if status in HTTP_RETRY_STATUSES:
            return POLICY_RETRY
        if 200 <= status < 300:
            return POLICY_SUCCESS
        if 300 <= status < 400:
            return POLICY_REDIRECT
        return POLICY_FAIL

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
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at is None:
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        delay = retry_at.timestamp() - datetime.now(timezone.utc).timestamp()
        if delay > 0:
            return min(delay, MAX_RETRY_DELAY_SECONDS)
        return None

    @staticmethod
    async def _response_text(resp: aiohttp.ClientResponse) -> str:
        try:
            return PeriodicalApi._trim_text(await resp.text())
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _is_dns_error(err: BaseException) -> bool:
        dns_error_cls = getattr(aiohttp, "ClientConnectorDNSError", None)
        if dns_error_cls is not None and isinstance(err, dns_error_cls):
            return True
        if isinstance(err, aiohttp.ClientConnectorError):
            os_error = getattr(err, "os_error", None)
            if isinstance(os_error, socket.gaierror):
                return True
            msg = str(err).lower()
            return any(
                marker in msg
                for marker in (
                    "dns",
                    "name or service not known",
                    "temporary failure in name resolution",
                    "server returned answer with no data",
                    "no address associated with hostname",
                    "nodename nor servname provided",
                    "failed to resolve",
                )
            )
        return False

    @staticmethod
    def _is_connect_error(err: BaseException) -> bool:
        return isinstance(
            err,
            (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError, aiohttp.ClientOSError),
        )

    def _max_attempts_for_error(self, err: BaseException) -> int:
        if self._is_dns_error(err):
            return DEFAULT_DNS_RETRY_ATTEMPTS
        if self._is_connect_error(err):
            return DEFAULT_CONNECT_RETRY_ATTEMPTS
        return DEFAULT_RETRY_ATTEMPTS

    def _retry_delay(
        self,
        attempt: int,
        retry_after: str | None = None,
        dns_error: bool = False,
    ) -> float:
        retry_after_delay = self._retry_after_seconds(retry_after)
        if retry_after_delay is not None:
            return retry_after_delay
        base = DEFAULT_DNS_BACKOFF_SECONDS if dns_error else DEFAULT_RETRY_BACKOFF_SECONDS
        delay = base * (2 ** max(attempt - 1, 0))
        jitter = random.uniform(0.0, min(1.0, delay * 0.25))
        return min(delay + jitter, MAX_RETRY_DELAY_SECONDS)

    async def _wait_for_network_backoff(self, path: str) -> None:
        wait_time = self._network_backoff_until - asyncio.get_running_loop().time()
        if wait_time > 0:
            _LOGGER.debug("Periodical API network backoff active for %s, waiting %.1fs", path, wait_time)
            await asyncio.sleep(wait_time)

    def _set_network_backoff(self, delay: float) -> None:
        loop = asyncio.get_running_loop()
        self._network_backoff_until = max(
            self._network_backoff_until,
            loop.time() + min(delay, MAX_RETRY_DELAY_SECONDS),
        )

    def _record_success(self, path: str, status: int | None = None) -> None:
        self._last_success_utc = self._utc_now()
        self._last_error = None
        self._last_error_path = None
        if status is not None:
            self._last_http_status = status
        self._network_failures.clear()
        self._circuit_open_until = 0.0
        # Clear any lingering 429/DNS backoff so recovery is immediate, not delayed
        # until the previously-scheduled backoff window happens to expire.
        self._network_backoff_until = 0.0

    def _record_failure(
        self,
        path: str,
        err: BaseException | str,
        status: int | None = None,
        dns_error: bool = False,
        timeout_error: bool = False,
        connection_error: bool = False,
    ) -> None:
        self._total_failures += 1
        self._last_error_utc = self._utc_now()
        self._last_error = self._trim_text(str(err))
        self._last_error_path = path
        # Only overwrite the last status when this failure actually carried one,
        # otherwise a transport error erases the last known HTTP response code.
        if status is not None:
            self._last_http_status = status
        if dns_error:
            self._dns_failures += 1
            self._record_network_failure()
        elif timeout_error:
            self._timeout_failures += 1
            self._record_network_failure()
        elif connection_error:
            self._connection_failures += 1
            self._record_network_failure()

    def _record_network_failure(self) -> None:
        now = asyncio.get_running_loop().time()
        cutoff = now - CIRCUIT_FAILURE_WINDOW_SECONDS
        self._network_failures = [ts for ts in self._network_failures if ts >= cutoff]
        self._network_failures.append(now)
        if len(self._network_failures) >= CIRCUIT_FAILURE_THRESHOLD:
            self._circuit_open_until = max(self._circuit_open_until, now + CIRCUIT_OPEN_SECONDS)

    def _circuit_error(self, path: str) -> PeriodicalApiError | None:
        remaining = self._circuit_open_until - asyncio.get_running_loop().time()
        if remaining > 0:
            return PeriodicalApiError(f"GET {path} skipped: API circuit open for {remaining:.0f}s")
        return None

    async def _parse_success(self, resp: aiohttp.ClientResponse, path: str, status: int) -> Any:
        """Decode a 2xx response body.  Raises PeriodicalApiError on bad payloads."""
        if status == 204:
            self._record_success(path, status)
            return {}

        text = await resp.text()
        if not text.strip():
            err = PeriodicalApiError(f"GET {path} failed: HTTP {status} returned empty body")
            self._record_failure(path, err, status=status)
            raise err

        try:
            data = await resp.json(content_type=None)
        except Exception as err:  # noqa: BLE001
            content_type = resp.headers.get("Content-Type", "unknown")
            api_err = PeriodicalApiError(
                f"GET {path} failed: invalid JSON from HTTP {status}, "
                f"content-type={content_type}, body={self._trim_text(text)}"
            )
            self._record_failure(path, api_err, status=status)
            raise api_err from err

        if not isinstance(data, (dict, list)):
            api_err = PeriodicalApiError(
                f"GET {path} failed: expected JSON object/list, got {type(data).__name__}"
            )
            self._record_failure(path, api_err, status=status)
            raise api_err

        self._record_success(path, status)
        return data

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        circuit_error = self._circuit_error(path)
        if circuit_error is not None:
            raise circuit_error

        url = f"{self._base_url}{path}"
        self._total_requests += 1
        attempt = 0

        # Every branch below either returns or raises, so the loop needs no
        # secondary exit condition and there is no unreachable tail to fall into.
        while True:
            attempt += 1
            self._total_attempts += 1
            await self._wait_for_network_backoff(path)

            try:
                async with self._request_semaphore:
                    async with self._session.get(
                        url,
                        headers=self._headers,
                        params=params,
                        timeout=self._timeout,
                    ) as resp:
                        status = resp.status
                        policy = self._status_policy(status)

                        if policy == POLICY_SUCCESS:
                            return await self._parse_success(resp, path, status)

                        text = await self._response_text(resp)

                        if policy == POLICY_AUTH_FAIL:
                            msg = "HTTP 401 Unauthorized" if status == 401 else "HTTP 403 Forbidden"
                            api_err = PeriodicalAuthError(f"GET {path} failed: {msg}")
                            self._record_failure(path, api_err, status=status)
                            raise api_err

                        if policy == POLICY_RETRY:
                            if attempt < DEFAULT_RETRY_ATTEMPTS:
                                delay = self._retry_delay(attempt, resp.headers.get("Retry-After"))
                                self._total_retries += 1
                                if status == 429:
                                    self._set_network_backoff(delay)
                                _LOGGER.debug(
                                    "Periodical API HTTP retry %s/%s for %s after HTTP %s, waiting %.1fs",
                                    attempt,
                                    DEFAULT_RETRY_ATTEMPTS,
                                    path,
                                    status,
                                    delay,
                                )
                                await asyncio.sleep(delay)
                                continue
                            api_err = PeriodicalApiError(
                                f"GET {path} failed: HTTP {status}, retries exhausted: {text}"
                            )
                            self._record_failure(path, api_err, status=status)
                            raise api_err

                        if policy == POLICY_REDIRECT:
                            location = resp.headers.get("Location")
                            api_err = PeriodicalApiError(
                                f"GET {path} failed: HTTP {status} redirect, Location={location}"
                            )
                            self._record_failure(path, api_err, status=status)
                            raise api_err

                        api_err = PeriodicalApiError(
                            f"GET {path} failed: HTTP {status} non-retryable error: {text}"
                        )
                        self._record_failure(path, api_err, status=status)
                        raise api_err

            except (asyncio.TimeoutError, aiohttp.ServerTimeoutError) as err:
                if attempt < DEFAULT_RETRY_ATTEMPTS:
                    delay = self._retry_delay(attempt)
                    self._total_retries += 1
                    _LOGGER.debug(
                        "Periodical API timeout retry %s/%s for %s, waiting %.1fs",
                        attempt,
                        DEFAULT_RETRY_ATTEMPTS,
                        path,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                api_err = PeriodicalApiError(
                    f"GET {path} timed out after {DEFAULT_REQUEST_TIMEOUT_SECONDS}s, "
                    f"retries exhausted after {DEFAULT_RETRY_ATTEMPTS} attempts"
                )
                self._record_failure(path, api_err, timeout_error=True)
                raise api_err from err

            except aiohttp.ClientError as err:
                dns_error = self._is_dns_error(err)
                connect_error = self._is_connect_error(err)
                max_attempts = self._max_attempts_for_error(err)
                error_type = "DNS" if dns_error else "connection"

                if attempt < max_attempts:
                    delay = self._retry_delay(attempt, dns_error=dns_error)
                    self._total_retries += 1
                    if dns_error:
                        self._set_network_backoff(delay)
                    _LOGGER.debug(
                        "Periodical API %s retry %s/%s for %s, waiting %.1fs: %s",
                        error_type,
                        attempt,
                        max_attempts,
                        path,
                        delay,
                        err,
                    )
                    await asyncio.sleep(delay)
                    continue

                api_err = PeriodicalApiError(
                    f"GET {path} {error_type} error, retries exhausted after {max_attempts} attempts: {err}"
                )
                self._record_failure(path, api_err, dns_error=dns_error, connection_error=connect_error)
                raise api_err from err

    # ------------------------------------------------------------------
    # Endpoints.  Return types match the published OpenAPI schema.
    # ------------------------------------------------------------------

    async def get_me(self) -> dict[str, Any]:
        """GET /me — basic info about the authenticated user."""
        return await self._get("/me")

    async def get_shifts(self) -> list[dict[str, Any]]:
        """GET /shifts — all shift type definitions (code, label, times, color)."""
        return await self._get("/shifts")

    async def get_user_status(self, user_id: int) -> dict[str, Any]:
        """GET /users/{user_id}/status — today's status."""
        return await self._get(f"/users/{user_id}/status")

    async def get_schedule_month(self, user_id: int) -> dict[str, Any]:
        """GET /users/{user_id}/schedule/month — current month schedule."""
        return await self._get(f"/users/{user_id}/schedule/month")

    async def get_schedule_year(self, user_id: int, year: int | None = None) -> dict[str, Any]:
        """GET /users/{user_id}/schedule/year — full year schedule."""
        params = {"year": year} if year is not None else None
        return await self._get(f"/users/{user_id}/schedule/year", params=params)

    async def get_schedule_week(self, user_id: int, day: str) -> dict[str, Any]:
        """GET /users/{user_id}/schedule/week/{date} — ISO week containing `day`."""
        return await self._get(f"/users/{user_id}/schedule/week/{day}")

    async def get_schedule_range(self, user_id: int, from_date: str, to_date: str) -> dict[str, Any]:
        """GET /users/{user_id}/schedule — date range schedule (max 70 days)."""
        try:
            start = date.fromisoformat(from_date)
            end = date.fromisoformat(to_date)
        except (TypeError, ValueError) as err:
            raise PeriodicalApiError(
                f"schedule range needs ISO dates, got from_date={from_date!r} to_date={to_date!r}"
            ) from err
        if end < start:
            raise PeriodicalApiError(f"schedule range end {to_date} precedes start {from_date}")
        # The API answers 422 above this span; catch it here so the caller gets a
        # clear message instead of an opaque non-retryable HTTP failure.
        span = (end - start).days + 1
        if span > MAX_SCHEDULE_RANGE_DAYS:
            raise PeriodicalApiError(
                f"schedule range {from_date}..{to_date} spans {span} days, "
                f"maximum is {MAX_SCHEDULE_RANGE_DAYS}"
            )
        return await self._get(
            f"/users/{user_id}/schedule",
            params={"from_date": from_date, "to_date": to_date},
        )

    async def get_schedule_date(self, user_id: int, day: str) -> dict[str, Any]:
        """GET /users/{user_id}/schedule/{date} — specific date schedule."""
        return await self._get(f"/users/{user_id}/schedule/{day}")

    async def get_pay_month(
        self, user_id: int, year: int | None = None, month: int | None = None
    ) -> dict[str, Any]:
        """GET /users/{user_id}/pay/month — monthly pay summary."""
        params: dict[str, Any] = {}
        if year is not None:
            params["year"] = year
        if month is not None:
            params["month"] = month
        return await self._get(f"/users/{user_id}/pay/month", params=params or None)

    async def get_vacation_balance(self, user_id: int, year: int | None = None) -> dict[str, Any]:
        """GET /users/{user_id}/vacation/balance — vacation balance."""
        params = {"year": year} if year is not None else None
        return await self._get(f"/users/{user_id}/vacation/balance", params=params)

    async def get_absences(self, user_id: int, year: int | None = None) -> list[dict[str, Any]]:
        """GET /users/{user_id}/absences — list of absences (JSON array)."""
        params = {"year": year} if year is not None else None
        return await self._get(f"/users/{user_id}/absences", params=params)

    async def get_next_shift(self, user_id: int) -> dict[str, Any]:
        """GET /users/{user_id}/next-shift — next working day."""
        return await self._get(f"/users/{user_id}/next-shift")
