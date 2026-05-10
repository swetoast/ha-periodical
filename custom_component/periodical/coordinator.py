"""DataUpdateCoordinator for Periodical."""
from __future__ import annotations

import asyncio
import logging
from datetime import date as _date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PeriodicalApi, PeriodicalAuthError
from .const import (
    ALL_ENDPOINT_KEYS,
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_ENABLED_ENDPOINTS,
    CONF_MAX_CONCURRENT_REQUESTS,
    CONF_REQUEST_TIMEOUT_SECONDS,
    CONF_RETRY_ATTEMPTS,
    CONF_UPDATE_INTERVAL_MINUTES,
    CONF_USER_ID,
    DATA_ABSENCES,
    DATA_API_HEALTH,
    DATA_ME,
    DATA_NEXT_SHIFT,
    DATA_NEXT_SHIFT_TOMORROW,
    DATA_PAY_MONTH,
    DATA_SCHEDULE_MONTH,
    DATA_SCHEDULE_TODAY,
    DATA_SCHEDULE_WEEK,
    DATA_SCHEDULE_YEAR,
    DATA_STATUS,
    DATA_VACATION_BALANCE,
    DEFAULT_BASE_URL,
    DEFAULT_MAX_CONCURRENT_REQUESTS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_RETRY_ATTEMPTS,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

REFRESH_REALTIME = timedelta(minutes=15)
REFRESH_HOURLY = timedelta(hours=1)
REFRESH_FOUR_HOURS = timedelta(hours=4)
REFRESH_DAILY = timedelta(hours=24)

REFRESH_TIERS: dict[str, timedelta] = {
    DATA_STATUS: REFRESH_REALTIME,
    DATA_SCHEDULE_TODAY: REFRESH_REALTIME,
    DATA_NEXT_SHIFT: REFRESH_REALTIME,
    DATA_SCHEDULE_WEEK: REFRESH_HOURLY,
    DATA_NEXT_SHIFT_TOMORROW: REFRESH_HOURLY,
    DATA_ABSENCES: REFRESH_HOURLY,
    DATA_SCHEDULE_MONTH: REFRESH_FOUR_HOURS,
    DATA_VACATION_BALANCE: REFRESH_FOUR_HOURS,
    DATA_ME: REFRESH_DAILY,
    DATA_SCHEDULE_YEAR: REFRESH_DAILY,
    DATA_PAY_MONTH: REFRESH_DAILY,
}

ENDPOINT_NAMES: dict[str, str] = {
    DATA_STATUS: "Status",
    DATA_SCHEDULE_TODAY: "Schedule Today",
    DATA_NEXT_SHIFT: "Next Shift",
    DATA_SCHEDULE_WEEK: "Schedule Week",
    DATA_NEXT_SHIFT_TOMORROW: "Next Shift Tomorrow",
    DATA_ABSENCES: "Absences",
    DATA_SCHEDULE_MONTH: "Schedule Month",
    DATA_VACATION_BALANCE: "Vacation Balance",
    DATA_ME: "Profile",
    DATA_SCHEDULE_YEAR: "Schedule Year",
    DATA_PAY_MONTH: "Pay Month",
}


class PeriodicalCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch and cache Periodical data for one user."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        update_minutes = int(entry.options.get(CONF_UPDATE_INTERVAL_MINUTES, DEFAULT_UPDATE_INTERVAL_MINUTES))
        super().__init__(hass, _LOGGER, name=f"{DOMAIN}_{entry.entry_id}", update_interval=timedelta(minutes=update_minutes))
        self.entry = entry
        session = async_get_clientsession(hass)
        self.api = PeriodicalApi(
            base_url=entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
            api_key=entry.data[CONF_API_KEY],
            session=session,
            request_timeout_seconds=int(entry.options.get(CONF_REQUEST_TIMEOUT_SECONDS, DEFAULT_REQUEST_TIMEOUT_SECONDS)),
            retry_attempts=int(entry.options.get(CONF_RETRY_ATTEMPTS, DEFAULT_RETRY_ATTEMPTS)),
            max_concurrent_requests=int(entry.options.get(CONF_MAX_CONCURRENT_REQUESTS, DEFAULT_MAX_CONCURRENT_REQUESTS)),
        )
        self.user_id: int = entry.data[CONF_USER_ID]
        self._last_good_data: dict[str, Any] = {}
        self._last_update_success = False
        self._last_failed_endpoints: list[str] = []
        self._last_error: str | None = None
        self._last_refresh: dict[str, datetime] = {}
        self._fetch_count: dict[str, int] = {key: 0 for key in REFRESH_TIERS}
        self._skip_count: dict[str, int] = {key: 0 for key in REFRESH_TIERS}
        self._refresh_lock = asyncio.Lock()
        self._force_refresh_once = False
        self._forced_endpoints: set[str] = set()
        self._endpoint_stats: dict[str, dict[str, Any]] = {
            key: {"enabled": True, "available": None, "stale": False, "last_success": None, "last_failure": None, "failure_count": 0, "success_count": 0, "last_error": None, "last_duration_ms": None}
            for key in ALL_ENDPOINT_KEYS
        }

    @property
    def enabled_endpoints(self) -> set[str]:
        enabled = self.entry.options.get(CONF_ENABLED_ENDPOINTS)
        return set(enabled) if isinstance(enabled, list) else set(ALL_ENDPOINT_KEYS)

    def endpoint_name(self, key: str) -> str:
        return ENDPOINT_NAMES.get(key, key.replace("_", " ").title())

    async def async_force_refresh(self, endpoints: set[str] | None = None) -> None:
        self._force_refresh_once = endpoints is None
        self._forced_endpoints = endpoints or set()
        await self.async_request_refresh()

    def _should_refresh(self, key: str) -> bool:
        enabled = key in self.enabled_endpoints
        self._endpoint_stats[key]["enabled"] = enabled
        if not enabled:
            return False
        if self._force_refresh_once or key in self._forced_endpoints:
            return True
        if key not in self._last_refresh:
            return True
        should_refresh = (datetime.now() - self._last_refresh[key]) >= REFRESH_TIERS.get(key, REFRESH_REALTIME)
        if should_refresh:
            self._fetch_count[key] = self._fetch_count.get(key, 0) + 1
        else:
            self._skip_count[key] = self._skip_count.get(key, 0) + 1
        return should_refresh

    def _mark_refreshed(self, key: str) -> None:
        self._last_refresh[key] = datetime.now()

    def _record_endpoint_success(self, key: str, duration_ms: float) -> None:
        stat = self._endpoint_stats[key]
        stat["available"] = True
        stat["stale"] = False
        stat["last_success"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        stat["success_count"] += 1
        stat["last_error"] = None
        stat["last_duration_ms"] = round(duration_ms, 1)

    def _record_endpoint_failure(self, key: str, err: Exception, stale: bool) -> None:
        stat = self._endpoint_stats[key]
        stat["available"] = False
        stat["stale"] = stale
        stat["last_failure"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        stat["failure_count"] += 1
        stat["last_error"] = str(err)

    async def _call_endpoint(self, key: str, call: Awaitable[Any]) -> tuple[str, Any, float]:
        started = datetime.now()
        result = await call
        duration_ms = (datetime.now() - started).total_seconds() * 1000
        return key, result, duration_ms

    async def _async_update_data(self) -> dict[str, Any]:
        async with self._refresh_lock:
            try:
                return await self._async_update_data_locked()
            finally:
                self._force_refresh_once = False
                self._forced_endpoints.clear()

    async def _async_update_data_locked(self) -> dict[str, Any]:
        uid = self.user_id
        today = _date.today().isoformat()
        tomorrow = (_date.today() + timedelta(days=1)).isoformat()
        calls: dict[str, Callable[[], Awaitable[Any]]] = {
            DATA_STATUS: lambda: self.api.get_user_status(uid),
            DATA_SCHEDULE_TODAY: lambda: self.api.get_schedule_today(uid),
            DATA_NEXT_SHIFT: lambda: self.api.get_next_shift(uid),
            DATA_SCHEDULE_WEEK: lambda: self.api.get_schedule_week(uid, today),
            DATA_NEXT_SHIFT_TOMORROW: lambda: self.api.get_next_shift(uid, date=tomorrow, time="00:00"),
            DATA_ABSENCES: lambda: self.api.get_absences(uid),
            DATA_SCHEDULE_MONTH: lambda: self.api.get_schedule_month(uid),
            DATA_VACATION_BALANCE: lambda: self.api.get_vacation_balance(uid),
            DATA_ME: lambda: self.api.get_me(),
            DATA_SCHEDULE_YEAR: lambda: self.api.get_schedule_year(uid),
            DATA_PAY_MONTH: lambda: self.api.get_pay_month(uid),
        }
        fetch_tasks = [(key, self._call_endpoint(key, call())) for key, call in calls.items() if self._should_refresh(key)]
        fetching_keys = [key for key, _ in fetch_tasks]
        skipped_keys = [key for key in REFRESH_TIERS if key not in fetching_keys]
        results = await asyncio.gather(*(task for _, task in fetch_tasks), return_exceptions=True) if fetch_tasks else []
        data: dict[str, Any] = dict(self._last_good_data)
        failed_endpoints: list[str] = []
        errors: list[str] = []
        success_count = 0
        stale_keys: list[str] = []
        fresh_keys: list[str] = []
        auth_failed = False
        for original_key, result in zip(fetching_keys, results, strict=True):
            key = original_key
            duration_ms = 0.0
            if isinstance(result, tuple):
                key, result, duration_ms = result
            if isinstance(result, Exception):
                failed_endpoints.append(key)
                errors.append(f"{key}: {result}")
                auth_failed = auth_failed or isinstance(result, PeriodicalAuthError)
                stale = key in self._last_good_data
                self._record_endpoint_failure(key, result, stale)
                data[key] = self._last_good_data.get(key)
                if stale:
                    stale_keys.append(key)
                continue
            data[key] = result
            if result is not None:
                self._last_good_data[key] = result
                success_count += 1
                fresh_keys.append(key)
                self._record_endpoint_success(key, duration_ms)
            self._mark_refreshed(key)
        if auth_failed:
            ir.async_create_issue(self.hass, DOMAIN, "invalid_auth", is_fixable=True, severity=ir.IssueSeverity.ERROR, translation_key="invalid_auth")
            if not self._last_good_data:
                raise UpdateFailed(errors[0])
        else:
            ir.async_delete_issue(self.hass, DOMAIN, "invalid_auth")
        if not fetch_tasks and not self._last_good_data:
            raise UpdateFailed("No data available and no endpoints scheduled for refresh")
        if success_count == 0 and not self._last_good_data:
            raise UpdateFailed(errors[0] if errors else "all Periodical API requests failed")
        self._last_update_success = not failed_endpoints
        self._last_failed_endpoints = failed_endpoints
        self._last_error = errors[0] if errors else None
        data[DATA_API_HEALTH] = {
            "connected": success_count > 0 and not self.api.diagnostics.get("circuit_open", False),
            "partial_failure": bool(failed_endpoints) and success_count > 0,
            "using_stale_data": bool(stale_keys),
            "failed_endpoints": failed_endpoints,
            "stale_keys": stale_keys,
            "success_count": success_count,
            "failure_count": len(failed_endpoints),
            "last_update_success": self._last_update_success,
            "last_error": self._last_error,
            "endpoint_stats": self._endpoint_stats,
            "api": self.api.diagnostics,
            "optimization": {"endpoints_fetched": len(fetch_tasks), "endpoints_total": len(REFRESH_TIERS), "endpoints_skipped": len(skipped_keys), "fresh_keys": fresh_keys, "skipped_keys": skipped_keys, "enabled_endpoints": sorted(self.enabled_endpoints), "fetch_counts": dict(self._fetch_count), "skip_counts": dict(self._skip_count)},
        }
        return data
