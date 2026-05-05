"""Services for Periodical — expose extra API endpoints as HA services."""
from __future__ import annotations

import logging
import voluptuous as vol
from datetime import date

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .api import PeriodicalApiError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_GET_SCHEDULE_DATE = "get_schedule_date"
SERVICE_GET_SCHEDULE_WEEK = "get_schedule_week"
SERVICE_GET_SCHEDULE_RANGE = "get_schedule_range"
SERVICE_GET_PAY_MONTH = "get_pay_month"
SERVICE_GET_VACATION_BALANCE = "get_vacation_balance"

SCHEMA_DATE = vol.Schema(
    {
        vol.Required("date"): cv.date,
    }
)
SCHEMA_WEEK = vol.Schema(
    {
        vol.Required("date"): cv.date,
    }
)
SCHEMA_RANGE = vol.Schema(
    {
        vol.Required("from_date"): cv.date,
        vol.Required("to_date"): cv.date,
    }
)
SCHEMA_PAY_MONTH = vol.Schema(
    {
        vol.Optional("year"): vol.Coerce(int),
        vol.Optional("month"): vol.All(vol.Coerce(int), vol.Range(min=1, max=12)),
    }
)
SCHEMA_VACATION_YEAR = vol.Schema(
    {
        vol.Optional("year"): vol.Coerce(int),
    }
)


def async_register_services(hass: HomeAssistant) -> None:
    """Register Periodical services."""

    async def _handle_schedule_date(call: ServiceCall) -> None:
        result_date: date = call.data["date"]
        date_str = result_date.isoformat()
        for coordinator in hass.data.get(DOMAIN, {}).values():
            try:
                data = await coordinator.api.get_schedule_date(coordinator.user_id, date_str)
                hass.bus.async_fire(
                    f"{DOMAIN}_schedule_date",
                    {"user_id": coordinator.user_id, "date": date_str, "data": data},
                )
            except PeriodicalApiError as err:
                _LOGGER.error("get_schedule_date failed: %s", err)

    async def _handle_schedule_week(call: ServiceCall) -> None:
        result_date: date = call.data["date"]
        date_str = result_date.isoformat()
        for coordinator in hass.data.get(DOMAIN, {}).values():
            try:
                data = await coordinator.api.get_schedule_week(coordinator.user_id, date_str)
                hass.bus.async_fire(
                    f"{DOMAIN}_schedule_week",
                    {"user_id": coordinator.user_id, "date": date_str, "data": data},
                )
            except PeriodicalApiError as err:
                _LOGGER.error("get_schedule_week failed: %s", err)

    async def _handle_schedule_range(call: ServiceCall) -> None:
        from_str: str = call.data["from_date"].isoformat()
        to_str: str = call.data["to_date"].isoformat()
        for coordinator in hass.data.get(DOMAIN, {}).values():
            try:
                data = await coordinator.api.get_schedule_range(
                    coordinator.user_id, from_str, to_str
                )
                hass.bus.async_fire(
                    f"{DOMAIN}_schedule_range",
                    {
                        "user_id": coordinator.user_id,
                        "from_date": from_str,
                        "to_date": to_str,
                        "data": data,
                    },
                )
            except PeriodicalApiError as err:
                _LOGGER.error("get_schedule_range failed: %s", err)

    async def _handle_pay_month(call: ServiceCall) -> None:
        year = call.data.get("year")
        month = call.data.get("month")
        for coordinator in hass.data.get(DOMAIN, {}).values():
            try:
                data = await coordinator.api.get_pay_month(coordinator.user_id, year, month)
                hass.bus.async_fire(
                    f"{DOMAIN}_pay_month",
                    {"user_id": coordinator.user_id, "year": year, "month": month, "data": data},
                )
            except PeriodicalApiError as err:
                _LOGGER.error("get_pay_month failed: %s", err)

    async def _handle_vacation_balance(call: ServiceCall) -> None:
        year = call.data.get("year")
        for coordinator in hass.data.get(DOMAIN, {}).values():
            try:
                data = await coordinator.api.get_vacation_balance(coordinator.user_id, year)
                hass.bus.async_fire(
                    f"{DOMAIN}_vacation_balance",
                    {"user_id": coordinator.user_id, "year": year, "data": data},
                )
            except PeriodicalApiError as err:
                _LOGGER.error("get_vacation_balance failed: %s", err)

    hass.services.async_register(DOMAIN, SERVICE_GET_SCHEDULE_DATE, _handle_schedule_date, schema=SCHEMA_DATE)
    hass.services.async_register(DOMAIN, SERVICE_GET_SCHEDULE_WEEK, _handle_schedule_week, schema=SCHEMA_WEEK)
    hass.services.async_register(DOMAIN, SERVICE_GET_SCHEDULE_RANGE, _handle_schedule_range, schema=SCHEMA_RANGE)
    hass.services.async_register(DOMAIN, SERVICE_GET_PAY_MONTH, _handle_pay_month, schema=SCHEMA_PAY_MONTH)
    hass.services.async_register(DOMAIN, SERVICE_GET_VACATION_BALANCE, _handle_vacation_balance, schema=SCHEMA_VACATION_YEAR)
