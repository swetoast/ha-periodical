"""DataUpdateCoordinator for Periodical with optimized tiered refresh strategy."""
from __future__ import annotations

import asyncio
import logging
import time as _time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PeriodicalApi, PeriodicalAuthError
from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_USER_ID,
    DATA_ABSENCES,
    DATA_API_HEALTH,
    DATA_ME,
    DATA_NEXT_SHIFT,
    DATA_NEXT_SHIFT_TOMORROW,
    DATA_PAY_MONTH,
    DATA_SCHEDULE_MONTH,
    DATA_SCHEDULE_TODAY,
    DATA_SHIFTS,
    DATA_SCHEDULE_WEEK,
    DATA_SCHEDULE_YEAR,
    DATA_STATUS,
    DATA_VACATION_BALANCE,
    DEFAULT_BASE_URL,
    DOMAIN,
    SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# Tiered refresh intervals - optimized to reduce API load
REFRESH_REALTIME = timedelta(minutes=15)      # Critical: status, today's schedule, next shift
REFRESH_HOURLY = timedelta(hours=1)           # Semi-dynamic: weekly schedule, tomorrow, absences
REFRESH_FOUR_HOURS = timedelta(hours=4)       # Relatively static: monthly schedule, vacation
REFRESH_DAILY = timedelta(hours=24)           # Static: user profile, yearly schedule, pay data
# Gating tolerance: a scheduled cycle can fire slightly before a full interval has
# elapsed (and _last_refresh is stamped at cycle start, not completion).  Without
# slack, a tier whose interval equals SCAN_INTERVAL would skip every other cycle.
REFRESH_TOLERANCE = timedelta(seconds=60)

# Endpoint refresh tiers - maps data keys to their refresh intervals
REFRESH_TIERS: dict[str, timedelta] = {
    # Tier 1: Real-time data (every 15 min) - things that change throughout the day
    DATA_STATUS: REFRESH_REALTIME,
    DATA_SCHEDULE_TODAY: REFRESH_REALTIME,
    DATA_NEXT_SHIFT: REFRESH_REALTIME,
    
    # Tier 2: Hourly data - things that might change but not constantly
    DATA_SCHEDULE_WEEK: REFRESH_HOURLY,
    DATA_NEXT_SHIFT_TOMORROW: REFRESH_HOURLY,
    DATA_ABSENCES: REFRESH_HOURLY,
    
    # Tier 3: Every 4 hours - relatively stable data
    DATA_SCHEDULE_MONTH: REFRESH_FOUR_HOURS,
    DATA_VACATION_BALANCE: REFRESH_FOUR_HOURS,
    
    # Tier 4: Daily data - rarely changes
    # /me identifies the user at setup (config flow stores the id); refreshed
    # daily here so the Account sensor can surface current profile info.
    DATA_ME: REFRESH_DAILY,
    DATA_SCHEDULE_YEAR: REFRESH_DAILY,
    DATA_PAY_MONTH: REFRESH_DAILY,
    DATA_SHIFTS: REFRESH_DAILY,
}


class PeriodicalCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch and cache all Periodical data for one user with optimized refresh intervals."""

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
        self._last_update_success = False
        self._last_failed_endpoints: list[str] = []
        self._last_error: str | None = None
        
        # Track last refresh time for each endpoint (monotonic clock, in seconds).
        # Monotonic is immune to wall-clock/DST jumps.
        self._last_refresh: dict[str, float] = {}
        self._fetch_count: dict[str, int] = {key: 0 for key in REFRESH_TIERS}
        self._skip_count: dict[str, int] = {key: 0 for key in REFRESH_TIERS}

    def _should_refresh(self, key: str, now: float) -> bool:
        """Whether an endpoint is due, based on its tier and a monotonic timestamp."""
        # Always fetch on first run
        if key not in self._last_refresh:
            self._fetch_count[key] = self._fetch_count.get(key, 0) + 1
            return True

        interval = REFRESH_TIERS.get(key, REFRESH_REALTIME)
        # Subtract a small tolerance so a cycle firing a hair early still counts the
        # interval as elapsed, instead of slipping the fetch to the next cycle.
        threshold = max(interval.total_seconds() - REFRESH_TOLERANCE.total_seconds(), 0.0)
        should_refresh = (now - self._last_refresh[key]) >= threshold

        if should_refresh:
            self._fetch_count[key] = self._fetch_count.get(key, 0) + 1
        else:
            self._skip_count[key] = self._skip_count.get(key, 0) + 1

        return should_refresh

    def _mark_refreshed(self, key: str, now: float) -> None:
        """Mark an endpoint as refreshed at the given cycle-start timestamp."""
        self._last_refresh[key] = now

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data with tiered refresh strategy to reduce API load."""
        uid = self.user_id
        now = _time.monotonic()
        local_today = dt_util.now().date()
        today = local_today.isoformat()
        tomorrow = (local_today + timedelta(days=1)).isoformat()

        # Build list of endpoints to fetch based on refresh tiers
        fetch_tasks: list[tuple[str, Any]] = []
        
        # Tier 1: Real-time (every 15 min)
        if self._should_refresh(DATA_STATUS, now):
            fetch_tasks.append((DATA_STATUS, self.api.get_user_status(uid)))
        if self._should_refresh(DATA_SCHEDULE_TODAY, now):
            fetch_tasks.append((DATA_SCHEDULE_TODAY, self.api.get_schedule_today(uid)))
        if self._should_refresh(DATA_NEXT_SHIFT, now):
            fetch_tasks.append((DATA_NEXT_SHIFT, self.api.get_next_shift(uid)))
        
        # Tier 2: Hourly
        if self._should_refresh(DATA_SCHEDULE_WEEK, now):
            fetch_tasks.append((DATA_SCHEDULE_WEEK, self.api.get_schedule_week(uid, today)))
        if self._should_refresh(DATA_NEXT_SHIFT_TOMORROW, now):
            fetch_tasks.append((DATA_NEXT_SHIFT_TOMORROW, self.api.get_next_shift(uid, date=tomorrow, time="00:00")))
        if self._should_refresh(DATA_ABSENCES, now):
            fetch_tasks.append((DATA_ABSENCES, self.api.get_absences(uid)))
        
        # Tier 3: Every 4 hours
        if self._should_refresh(DATA_SCHEDULE_MONTH, now):
            fetch_tasks.append((DATA_SCHEDULE_MONTH, self.api.get_schedule_month(uid)))
        if self._should_refresh(DATA_VACATION_BALANCE, now):
            fetch_tasks.append((DATA_VACATION_BALANCE, self.api.get_vacation_balance(uid)))
        
        # Tier 4: Daily
        if self._should_refresh(DATA_ME, now):
            fetch_tasks.append((DATA_ME, self.api.get_me()))
        if self._should_refresh(DATA_SCHEDULE_YEAR, now):
            fetch_tasks.append((DATA_SCHEDULE_YEAR, self.api.get_schedule_year(uid)))
        if self._should_refresh(DATA_PAY_MONTH, now):
            fetch_tasks.append((DATA_PAY_MONTH, self.api.get_pay_month(uid)))
        if self._should_refresh(DATA_SHIFTS, now):
            fetch_tasks.append((DATA_SHIFTS, self.api.get_shifts()))

        # Log what we're fetching
        fetching_keys = [key for key, _ in fetch_tasks]
        all_keys = list(REFRESH_TIERS.keys())
        skipped_keys = [key for key in all_keys if key not in fetching_keys]
        
        if skipped_keys:
            _LOGGER.debug(
                "Periodical refresh: fetching %d/%d endpoints (skipping %s due to refresh intervals)",
                len(fetch_tasks),
                len(all_keys),
                ", ".join(skipped_keys),
            )

        # Fetch all required endpoints concurrently
        if fetch_tasks:
            keys, tasks = zip(*fetch_tasks, strict=True)
            results = await asyncio.gather(*tasks, return_exceptions=True)
        else:
            keys, results = [], []

        # Start with cached data
        data: dict[str, Any] = dict(self._last_good_data)
        failed_endpoints: list[str] = []
        errors: list[str] = []
        success_count = 0
        stale_keys: list[str] = []
        fresh_keys: list[str] = []

        # Process fetched results
        for key, result in zip(keys, results, strict=True):
            if isinstance(result, Exception):
                failed_endpoints.append(key)
                errors.append(f"{key}: {result}")

                # Auth errors on critical endpoints should fail the whole update
                if isinstance(result, PeriodicalAuthError) and key == DATA_ME and not self._last_good_data:
                    raise UpdateFailed(f"Authentication error: {result}") from result

                # Use stale data if available
                if key in self._last_good_data:
                    data[key] = self._last_good_data[key]
                    stale_keys.append(key)
                else:
                    data[key] = None

                _LOGGER.debug("Failed to fetch %s, using stale data if available: %s", key, result)
                continue

            # Success - update data and mark as refreshed
            data[key] = result
            if result is not None:
                self._last_good_data[key] = result
                success_count += 1
                fresh_keys.append(key)
            self._mark_refreshed(key, now)

        # If we didn't fetch anything (all skipped), ensure we have something to return
        if not fetch_tasks and not self._last_good_data:
            raise UpdateFailed("No data available and no endpoints scheduled for refresh")

        # Validate we have at least some data
        if success_count == 0 and not self._last_good_data:
            err = errors[0] if errors else "all Periodical API requests failed"
            raise UpdateFailed(err)

        self._last_update_success = not failed_endpoints
        self._last_failed_endpoints = failed_endpoints
        self._last_error = errors[0] if errors else None

        # Enhanced API health diagnostics
        data[DATA_API_HEALTH] = {
            # Healthy = API circuit closed, no failures this cycle, and we have data to
            # serve.  A cycle that fetched nothing (everything within its interval) is
            # still "connected" — it must not trip the API Problem sensor.
            "connected": (
                not self.api.diagnostics.get("circuit_open", False)
                and not failed_endpoints
                and bool(data)
            ),
            "partial_failure": bool(failed_endpoints) and success_count > 0,
            "using_stale_data": bool(stale_keys),
            "failed_endpoints": failed_endpoints,
            "stale_keys": stale_keys,
            "success_count": success_count,
            "failure_count": len(failed_endpoints),
            "last_update_success": self._last_update_success,
            "last_error": self._last_error,
            "api": self.api.diagnostics,
            # Add optimization metrics
            "optimization": {
                "endpoints_fetched": len(fetch_tasks),
                "endpoints_total": len(REFRESH_TIERS),
                "endpoints_skipped": len(skipped_keys),
                "fresh_keys": fresh_keys,
                "skipped_keys": skipped_keys,
                "fetch_counts": dict(self._fetch_count),
                "skip_counts": dict(self._skip_count),
            },
        }

        return data
