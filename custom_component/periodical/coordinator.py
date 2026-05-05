"""DataUpdateCoordinator for Periodical."""
from __future__ import annotations

import asyncio
import logging
from datetime import date as _date, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
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
    DATA_SCHEDULE_WEEK,
    DATA_SCHEDULE_YEAR,
    DATA_STATUS,
    DATA_VACATION_BALANCE,
    DEFAULT_BASE_URL,
    DOMAIN,
    SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


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
        self._last_update_success = False
        self._last_failed_endpoints: list[str] = []
        self._last_error: str | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch all data sources concurrently and keep last good data on partial failures."""
        uid = self.user_id
        today = _date.today().isoformat()
        tomorrow = (_date.today() + timedelta(days=1)).isoformat()

        keys = (
            DATA_ME,
            DATA_STATUS,
            DATA_SCHEDULE_TODAY,
            DATA_SCHEDULE_WEEK,
            DATA_SCHEDULE_MONTH,
            DATA_SCHEDULE_YEAR,
            DATA_NEXT_SHIFT,
            DATA_NEXT_SHIFT_TOMORROW,
            DATA_VACATION_BALANCE,
            DATA_PAY_MONTH,
            DATA_ABSENCES,
        )

        results = await asyncio.gather(
            self.api.get_me(),
            self.api.get_user_status(uid),
            self.api.get_schedule_today(uid),
            self.api.get_schedule_week(uid, today),
            self.api.get_schedule_month(uid),
            self.api.get_schedule_year(uid),
            self.api.get_next_shift(uid),
            self.api.get_next_shift(uid, date=tomorrow, time="00:00"),
            self.api.get_vacation_balance(uid),
            self.api.get_pay_month(uid),
            self.api.get_absences(uid),
            return_exceptions=True,
        )

        data: dict[str, Any] = {}
        failed_endpoints: list[str] = []
        errors: list[str] = []
        success_count = 0
        stale_keys: list[str] = []

        for key, result in zip(keys, results, strict=True):
            if isinstance(result, Exception):
                failed_endpoints.append(key)
                errors.append(f"{key}: {result}")

                if isinstance(result, PeriodicalAuthError) and key == DATA_ME and not self._last_good_data:
                    raise UpdateFailed(f"Authentication error: {result}") from result

                if key in self._last_good_data:
                    data[key] = self._last_good_data[key]
                    stale_keys.append(key)
                else:
                    data[key] = None

                _LOGGER.debug("Failed to fetch %s, using stale data if available: %s", key, result)
                continue

            data[key] = result
            if result is not None:
                self._last_good_data[key] = result
                success_count += 1

        if success_count == 0 and not self._last_good_data:
            err = errors[0] if errors else "all Periodical API requests failed"
            raise UpdateFailed(err)

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
            "api": self.api.diagnostics,
        }

        return data
