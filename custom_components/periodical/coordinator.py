"""DataUpdateCoordinator for Periodical with a tiered refresh strategy."""

from __future__ import annotations

import asyncio
import logging
import time as _time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import partial
from typing import Any, Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import PeriodicalApi, PeriodicalAuthError, PeriodicalForbiddenError
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
    DOMAIN,
    SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# Tiered refresh intervals keep API load proportional to how fast each
# endpoint's answer actually changes.
REFRESH_REALTIME: Final = timedelta(minutes=15)
REFRESH_HOURLY: Final = timedelta(hours=1)
REFRESH_FOUR_HOURS: Final = timedelta(hours=4)
REFRESH_DAILY: Final = timedelta(hours=24)

# A scheduled cycle can fire slightly before a full interval has elapsed (and
# _last_refresh is stamped at cycle start, not completion).  Without slack, a
# tier whose interval equals SCAN_INTERVAL would skip every other cycle.
REFRESH_TOLERANCE: Final = timedelta(seconds=60)

REFRESH_TIERS: Final[dict[str, timedelta]] = {
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
# server side.  When the local calendar rolls over their cached answer is stale
# by definition, however recently it was fetched, so the tier gate is bypassed
# for one cycle.  Without this, /pay/month serves last month's payslip for up to
# 24 h after midnight on the 1st.
DAY_SCOPED: Final = frozenset(
    {DATA_STATUS, DATA_SCHEDULE_WINDOW, DATA_NEXT_SHIFT, DATA_SCHEDULE_WEEK}
)
MONTH_SCOPED: Final = frozenset({DATA_SCHEDULE_MONTH, DATA_PAY_MONTH})
YEAR_SCOPED: Final = frozenset({DATA_SCHEDULE_YEAR, DATA_VACATION_BALANCE, DATA_ABSENCES})

Fetcher = Callable[[], Awaitable[Any]]


@dataclass(slots=True)
class CycleOutcome:
    """How one refresh cycle went, for the health summary."""

    failed: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    succeeded: int = 0


class PeriodicalCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch and cache all Periodical data for one user."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Build the API client and refresh bookkeeping for one config entry."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=SCAN_INTERVAL,
        )
        self.entry = entry
        self.api = PeriodicalApi(
            base_url=entry.data[CONF_BASE_URL],
            api_key=entry.data[CONF_API_KEY],
            session=async_get_clientsession(hass),
        )
        self.user_id: int = entry.data[CONF_USER_ID]
        self._last_good_data: dict[str, Any] = {}
        # Monotonic timestamps; immune to wall-clock and DST jumps.
        self._last_refresh: dict[str, float] = {}
        self._calendar_day: date | None = None

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def _should_refresh(self, key: str, now: float, forced: frozenset[str]) -> bool:
        """Whether an endpoint is due.  Pure: no counters, no side effects."""
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
            "Calendar rollover %s -> %s, forcing refresh of %s",
            previous,
            today,
            ", ".join(sorted(forced)),
        )
        return frozenset(forced)

    def _build_fetchers(self, today: date) -> dict[str, Fetcher]:
        """Every endpoint this integration reads, bound to today's parameters."""
        uid = self.user_id
        yesterday = (today - timedelta(days=1)).isoformat()
        tomorrow = (today + timedelta(days=1)).isoformat()
        return {
            DATA_STATUS: partial(self.api.get_user_status, uid),
            # One range call covers yesterday (to anchor a still-running
            # overnight shift), today, and tomorrow.
            DATA_SCHEDULE_WINDOW: partial(self.api.get_schedule_range, uid, yesterday, tomorrow),
            DATA_NEXT_SHIFT: partial(self.api.get_next_shift, uid),
            DATA_SCHEDULE_WEEK: partial(self.api.get_schedule_week, uid, today.isoformat()),
            DATA_ABSENCES: partial(self.api.get_absences, uid),
            DATA_SCHEDULE_MONTH: partial(self.api.get_schedule_month, uid),
            DATA_VACATION_BALANCE: partial(self.api.get_vacation_balance, uid),
            DATA_ME: self.api.get_me,
            DATA_SCHEDULE_YEAR: partial(self.api.get_schedule_year, uid),
            DATA_PAY_MONTH: partial(self.api.get_pay_month, uid),
            DATA_SHIFTS: self.api.get_shifts,
        }

    # ------------------------------------------------------------------
    # Update cycle
    # ------------------------------------------------------------------

    def _plan(self, today: date, now: float) -> tuple[dict[str, Fetcher], list[str]]:
        """Every endpoint bound to today's parameters, plus which ones are due."""
        fetchers = self._build_fetchers(today)
        forced = self._rollover_keys(today)
        due = [key for key in fetchers if self._should_refresh(key, now, forced)]

        if skipped := [key for key in fetchers if key not in due]:
            _LOGGER.debug(
                "Fetching %d/%d endpoints (skipping %s, still within interval)",
                len(due),
                len(fetchers),
                ", ".join(skipped),
            )
        return fetchers, due

    async def _async_update_data(self) -> dict[str, Any]:
        """Refresh whichever endpoints are due and merge them over the cache."""
        now = _time.monotonic()
        fetchers, due = self._plan(dt_util.now().date(), now)

        results: list[Any] = []
        if due:
            results = await asyncio.gather(
                *(fetchers[key]() for key in due), return_exceptions=True
            )

        data: dict[str, Any] = dict(self._last_good_data)
        outcome = CycleOutcome()

        for key, result in zip(due, results, strict=True):
            self._merge(key, result, now, data, outcome)

        if outcome.succeeded == 0 and not self._last_good_data:
            raise UpdateFailed(
                outcome.errors[0] if outcome.errors else "all Periodical API requests failed"
            )

        data[DATA_API_HEALTH] = self._health(outcome)
        return data

    def _merge(
        self,
        key: str,
        result: Any,
        now: float,
        data: dict[str, Any],
        outcome: CycleOutcome,
    ) -> None:
        """Fold one gathered result into the payload, recording how it went."""
        if not isinstance(result, BaseException):
            data[key] = result
            if result is not None:
                self._last_good_data[key] = result
                outcome.succeeded += 1
            self._last_refresh[key] = now
            return

        # A rejected credential is not a transient endpoint failure: it cannot
        # recover on its own, so hand it straight to the reauth flow rather than
        # quietly serving cached data forever.  A 403 is normally scoped to one
        # endpoint (pay, vacation and absences are own-user-or-admin), but any
        # valid key can read its own /me, so a 403 there means the key is dead.
        if isinstance(result, PeriodicalAuthError) or (
            key == DATA_ME and isinstance(result, PeriodicalForbiddenError)
        ):
            raise ConfigEntryAuthFailed(str(result)) from result

        outcome.failed.append(key)
        outcome.errors.append(f"{key}: {result}")
        if key in self._last_good_data:
            data[key] = self._last_good_data[key]
            outcome.stale.append(key)
        else:
            data[key] = None
        _LOGGER.debug("Failed to fetch %s, falling back to cached data: %s", key, result)

    def _health(self, outcome: CycleOutcome) -> dict[str, Any]:
        """Summary consumed by the API Problem diagnostic entity."""
        api = self.api.diagnostics
        return {
            # Healthy = circuit closed, nothing failed this cycle, and we have
            # something to serve.  A cycle that fetched nothing because every
            # endpoint was still within its interval is still "connected".
            "connected": (
                not api.get("circuit_open", False)
                and not outcome.failed
                and bool(self._last_good_data)
            ),
            "partial_failure": bool(outcome.failed) and outcome.succeeded > 0,
            "using_stale_data": bool(outcome.stale),
            "failed_endpoints": outcome.failed,
            "stale_endpoints": outcome.stale,
            "last_error": outcome.errors[0] if outcome.errors else None,
            "api": api,
        }
