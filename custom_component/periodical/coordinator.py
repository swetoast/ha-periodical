"""DataUpdateCoordinator for Periodical with a tiered refresh strategy."""
from __future__ import annotations

import asyncio
import logging
import time as _time
from datetime import date, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import PeriodicalApi, PeriodicalAuthError
from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_USER_ID,
    DATA_ABSENCES,
    DATA_API_HEALTH,
    DATA_ME,
    DATA_NEXT_SHIFT,
    DATA_PAY_MONTH,
    DATA_SCHEDULE_MONTH,
    DATA_SCHEDULE_WEEK,
    DATA_SCHEDULE_WINDOW,
    DATA_SCHEDULE_YEAR,
    DATA_SHIFTS,
    DATA_STATUS,
    DATA_VACATION_BALANCE,
    DEFAULT_BASE_URL,
    DOMAIN,
    SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# Tiered refresh intervals — keeps API load proportional to how fast each
# endpoint's answer actually changes.
REFRESH_REALTIME = timedelta(minutes=15)
REFRESH_HOURLY = timedelta(hours=1)
REFRESH_FOUR_HOURS = timedelta(hours=4)
REFRESH_DAILY = timedelta(hours=24)

# A scheduled cycle can fire slightly before a full interval has elapsed (and
# _last_refresh is stamped at cycle start, not completion).  Without slack, a
# tier whose interval equals SCAN_INTERVAL would skip every other cycle.
REFRESH_TOLERANCE = timedelta(seconds=60)

REFRESH_TIERS: dict[str, timedelta] = {
    # Real-time: what is happening right now.
    DATA_STATUS: REFRESH_REALTIME,
    DATA_SCHEDULE_WINDOW: REFRESH_REALTIME,
    DATA_NEXT_SHIFT: REFRESH_REALTIME,
    # Hourly: may change during the day, but not minute to minute.
    DATA_SCHEDULE_WEEK: REFRESH_HOURLY,
    DATA_ABSENCES: REFRESH_HOURLY,
    # Every four hours.
    DATA_SCHEDULE_MONTH: REFRESH_FOUR_HOURS,
    DATA_VACATION_BALANCE: REFRESH_FOUR_HOURS,
    # Daily.
    DATA_ME: REFRESH_DAILY,
    DATA_SCHEDULE_YEAR: REFRESH_DAILY,
    DATA_PAY_MONTH: REFRESH_DAILY,
    DATA_SHIFTS: REFRESH_DAILY,
}

# Endpoints whose answer is scoped to the current day / month / year on the
# server side.  When the local calendar rolls over, their cached answer is stale
# by definition regardless of how recently it was fetched, so the tier gate is
# bypassed for one cycle.  Without this, /pay/month serves last month's payslip
# for up to 24 h after midnight on the 1st.
DAY_SCOPED = frozenset({DATA_STATUS, DATA_SCHEDULE_WINDOW, DATA_NEXT_SHIFT, DATA_SCHEDULE_WEEK})
MONTH_SCOPED = frozenset({DATA_SCHEDULE_MONTH, DATA_PAY_MONTH})
YEAR_SCOPED = frozenset({DATA_SCHEDULE_YEAR, DATA_VACATION_BALANCE, DATA_ABSENCES})


class PeriodicalCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch and cache all Periodical data for one user."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=SCAN_INTERVAL,
        )
        self.entry = entry
        session = async_get_clientsession(hass)
        self.api = PeriodicalApi(
            base_url=entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
            api_key=entry.data[CONF_API_KEY],
            session=session,
        )
        self.user_id: int = entry.data[CONF_USER_ID]
        self._last_good_data: dict[str, Any] = {}
        self._last_failed_endpoints: list[str] = []
        self._last_error: str | None = None
        # Monotonic timestamps; immune to wall-clock and DST jumps.
        self._last_refresh: dict[str, float] = {}
        self._calendar_day: date | None = None

    def _should_refresh(self, key: str, now: float, forced: frozenset[str]) -> bool:
        """Whether an endpoint is due.  Pure — no counters, no side effects."""
        if key in forced or key not in self._last_refresh:
            return True
        interval = REFRESH_TIERS.get(key, REFRESH_REALTIME)
        # Subtract a small tolerance so a cycle firing a hair early still counts
        # the interval as elapsed instead of slipping to the next cycle.
        threshold = max(interval.total_seconds() - REFRESH_TOLERANCE.total_seconds(), 0.0)
        return (now - self._last_refresh[key]) >= threshold

    def _rollover_keys(self, today: date) -> frozenset[str]:
        """Keys invalidated because the local calendar advanced since last cycle."""
        previous = self._calendar_day
        self._calendar_day = today
        if previous is None or previous == today:
            return frozenset()

        forced = set(DAY_SCOPED)
        if (previous.year, previous.month) != (today.year, today.month):
            forced |= MONTH_SCOPED
        if previous.year != today.year:
            forced |= YEAR_SCOPED
        _LOGGER.debug(
            "Periodical calendar rollover %s -> %s, forcing refresh of %s",
            previous,
            today,
            ", ".join(sorted(forced)),
        )
        return frozenset(forced)

    async def _async_update_data(self) -> dict[str, Any]:
        uid = self.user_id
        now = _time.monotonic()
        today = dt_util.now().date()
        forced = self._rollover_keys(today)

        yesterday = (today - timedelta(days=1)).isoformat()
        tomorrow = (today + timedelta(days=1)).isoformat()

        planned: dict[str, Any] = {
            DATA_STATUS: lambda: self.api.get_user_status(uid),
            # One range call covers yesterday (to anchor a still-running overnight
            # shift), today, and tomorrow.
            DATA_SCHEDULE_WINDOW: lambda: self.api.get_schedule_range(uid, yesterday, tomorrow),
            DATA_NEXT_SHIFT: lambda: self.api.get_next_shift(uid),
            DATA_SCHEDULE_WEEK: lambda: self.api.get_schedule_week(uid, today.isoformat()),
            DATA_ABSENCES: lambda: self.api.get_absences(uid),
            DATA_SCHEDULE_MONTH: lambda: self.api.get_schedule_month(uid),
            DATA_VACATION_BALANCE: lambda: self.api.get_vacation_balance(uid),
            DATA_ME: lambda: self.api.get_me(),
            DATA_SCHEDULE_YEAR: lambda: self.api.get_schedule_year(uid),
            DATA_PAY_MONTH: lambda: self.api.get_pay_month(uid),
            DATA_SHIFTS: lambda: self.api.get_shifts(),
        }

        fetch_keys = [key for key in planned if self._should_refresh(key, now, forced)]
        skipped_keys = [key for key in planned if key not in fetch_keys]

        if skipped_keys:
            _LOGGER.debug(
                "Periodical refresh: fetching %d/%d endpoints (skipping %s, still within interval)",
                len(fetch_keys),
                len(planned),
                ", ".join(skipped_keys),
            )

        results: list[Any] = []
        if fetch_keys:
            results = await asyncio.gather(
                *(planned[key]() for key in fetch_keys), return_exceptions=True
            )

        data: dict[str, Any] = dict(self._last_good_data)
        failed_endpoints: list[str] = []
        errors: list[str] = []
        stale_keys: list[str] = []
        success_count = 0

        for key, result in zip(fetch_keys, results, strict=True):
            if isinstance(result, BaseException):
                # A rejected credential is not a transient endpoint failure: it
                # can never recover on its own, so hand it straight to the reauth
                # flow rather than quietly serving cached data forever.
                if isinstance(result, PeriodicalAuthError):
                    raise ConfigEntryAuthFailed(str(result)) from result

                failed_endpoints.append(key)
                errors.append(f"{key}: {result}")
                if key in self._last_good_data:
                    data[key] = self._last_good_data[key]
                    stale_keys.append(key)
                else:
                    data[key] = None
                _LOGGER.debug("Failed to fetch %s, falling back to cached data: %s", key, result)
                continue

            data[key] = result
            if result is not None:
                self._last_good_data[key] = result
                success_count += 1
            self._last_refresh[key] = now

        if success_count == 0 and not self._last_good_data:
            raise UpdateFailed(errors[0] if errors else "all Periodical API requests failed")

        self._last_failed_endpoints = failed_endpoints
        self._last_error = errors[0] if errors else None

        api_diagnostics = self.api.diagnostics
        data[DATA_API_HEALTH] = {
            # Healthy = circuit closed, nothing failed this cycle, and we have
            # something to serve.  A cycle that fetched nothing because every
            # endpoint was still within its interval is still "connected".
            "connected": (
                not api_diagnostics.get("circuit_open", False)
                and not failed_endpoints
                and bool(self._last_good_data)
            ),
            "partial_failure": bool(failed_endpoints) and success_count > 0,
            "using_stale_data": bool(stale_keys),
            "failed_endpoints": failed_endpoints,
            "stale_endpoints": stale_keys,
            "last_error": self._last_error,
            "api": api_diagnostics,
        }

        return data
