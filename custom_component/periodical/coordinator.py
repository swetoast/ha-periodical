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

from .api import PeriodicalApi, PeriodicalApiError, PeriodicalAuthError
from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_USER_ID,
    DATA_ABSENCES,
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

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch all data sources concurrently."""
        uid = self.user_id
        today    = _date.today().isoformat()
        tomorrow = (_date.today() + timedelta(days=1)).isoformat()

        try:
            (
                me,
                status,
                schedule_today,
                schedule_week,
                schedule_month,
                schedule_year,
                next_shift,
                next_shift_tomorrow,
                vacation_balance,
                pay_month,
                absences,
            ) = await asyncio.gather(
                self.api.get_me(),
                self.api.get_user_status(uid),
                self.api.get_schedule_today(uid),
                # Current ISO week — pass today so the API returns the week
                # that contains today's date.
                self.api.get_schedule_week(uid, today),
                self.api.get_schedule_month(uid),
                # Full year schedule — used for yearly shift/hour totals.
                self.api.get_schedule_year(uid),
                # No date param → next upcoming shift from right now
                # (today's if not yet started, otherwise tomorrow's).
                self.api.get_next_shift(uid),
                # Pinned to tomorrow → always tomorrow's calendar shift.
                self.api.get_next_shift(uid, date=tomorrow, time="00:00"),
                self.api.get_vacation_balance(uid),
                self.api.get_pay_month(uid),
                self.api.get_absences(uid),
                return_exceptions=True,
            )
        except PeriodicalAuthError as err:
            raise UpdateFailed(f"Authentication error: {err}") from err
        except PeriodicalApiError as err:
            raise UpdateFailed(f"API error: {err}") from err

        def _unwrap(result: Any, key: str) -> Any:
            """Return result or log and return None on individual fetch failure."""
            if isinstance(result, Exception):
                _LOGGER.warning("Failed to fetch %s: %s", key, result)
                return None
            return result

        return {
            DATA_ME:                  _unwrap(me,                   DATA_ME),
            DATA_STATUS:              _unwrap(status,               DATA_STATUS),
            DATA_SCHEDULE_TODAY:      _unwrap(schedule_today,       DATA_SCHEDULE_TODAY),
            DATA_SCHEDULE_WEEK:       _unwrap(schedule_week,        DATA_SCHEDULE_WEEK),
            DATA_SCHEDULE_MONTH:      _unwrap(schedule_month,       DATA_SCHEDULE_MONTH),
            DATA_SCHEDULE_YEAR:       _unwrap(schedule_year,        DATA_SCHEDULE_YEAR),
            DATA_NEXT_SHIFT:          _unwrap(next_shift,           DATA_NEXT_SHIFT),
            DATA_NEXT_SHIFT_TOMORROW: _unwrap(next_shift_tomorrow,  DATA_NEXT_SHIFT_TOMORROW),
            DATA_VACATION_BALANCE:    _unwrap(vacation_balance,     DATA_VACATION_BALANCE),
            DATA_PAY_MONTH:           _unwrap(pay_month,            DATA_PAY_MONTH),
            DATA_ABSENCES:            _unwrap(absences,             DATA_ABSENCES),
        }
