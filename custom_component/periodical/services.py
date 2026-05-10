"""Services for Periodical."""
from __future__ import annotations

import logging
from datetime import date

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .api import PeriodicalApiError
from .const import (
    DOMAIN,
    SERVICE_GET_PAY_MONTH,
    SERVICE_GET_SCHEDULE_DATE,
    SERVICE_GET_SCHEDULE_RANGE,
    SERVICE_GET_SCHEDULE_WEEK,
    SERVICE_GET_VACATION_BALANCE,
    SERVICE_REFRESH,
    SERVICE_REFRESH_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)
SCHEMA_DATE = vol.Schema({vol.Required("date"): cv.date})
SCHEMA_WEEK = vol.Schema({vol.Required("date"): cv.date})
SCHEMA_RANGE = vol.Schema({vol.Required("from_date"): cv.date, vol.Required("to_date"): cv.date})
SCHEMA_PAY_MONTH = vol.Schema({vol.Optional("year"): vol.Coerce(int), vol.Optional("month"): vol.All(vol.Coerce(int), vol.Range(min=1, max=12))})
SCHEMA_VACATION_YEAR = vol.Schema({vol.Optional("year"): vol.Coerce(int)})
SCHEMA_REFRESH_ENDPOINT = vol.Schema({vol.Required("endpoint"): str})


def _coordinators(hass: HomeAssistant):
    return hass.data.get(DOMAIN, {}).values()


def async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        return

    async def _handle_refresh(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass):
            await coordinator.async_force_refresh()

    async def _handle_refresh_endpoint(call: ServiceCall) -> None:
        endpoint = call.data["endpoint"]
        for coordinator in _coordinators(hass):
            await coordinator.async_force_refresh({endpoint})

    async def _handle_schedule_date(call: ServiceCall) -> None:
        date_str = call.data["date"].isoformat()
        for coordinator in _coordinators(hass):
            try:
                data = await coordinator.api.get_schedule_date(coordinator.user_id, date_str)
                hass.bus.async_fire(f"{DOMAIN}_schedule_date", {"user_id": coordinator.user_id, "date": date_str, "data": data})
            except PeriodicalApiError as err:
                _LOGGER.error("get_schedule_date failed: %s", err)

    async def _handle_schedule_week(call: ServiceCall) -> None:
        date_str = call.data["date"].isoformat()
        for coordinator in _coordinators(hass):
            try:
                data = await coordinator.api.get_schedule_week(coordinator.user_id, date_str)
                hass.bus.async_fire(f"{DOMAIN}_schedule_week", {"user_id": coordinator.user_id, "date": date_str, "data": data})
            except PeriodicalApiError as err:
                _LOGGER.error("get_schedule_week failed: %s", err)

    async def _handle_schedule_range(call: ServiceCall) -> None:
        from_str = call.data["from_date"].isoformat()
        to_str = call.data["to_date"].isoformat()
        for coordinator in _coordinators(hass):
            try:
                data = await coordinator.api.get_schedule_range(coordinator.user_id, from_str, to_str)
                hass.bus.async_fire(f"{DOMAIN}_schedule_range", {"user_id": coordinator.user_id, "from_date": from_str, "to_date": to_str, "data": data})
            except PeriodicalApiError as err:
                _LOGGER.error("get_schedule_range failed: %s", err)

    async def _handle_pay_month(call: ServiceCall) -> None:
        year = call.data.get("year")
        month = call.data.get("month")
        for coordinator in _coordinators(hass):
            try:
                data = await coordinator.api.get_pay_month(coordinator.user_id, year, month)
                hass.bus.async_fire(f"{DOMAIN}_pay_month", {"user_id": coordinator.user_id, "year": year, "month": month, "data": data})
            except PeriodicalApiError as err:
                _LOGGER.error("get_pay_month failed: %s", err)

    async def _handle_vacation_balance(call: ServiceCall) -> None:
        year = call.data.get("year")
        for coordinator in _coordinators(hass):
            try:
                data = await coordinator.api.get_vacation_balance(coordinator.user_id, year)
                hass.bus.async_fire(f"{DOMAIN}_vacation_balance", {"user_id": coordinator.user_id, "year": year, "data": data})
            except PeriodicalApiError as err:
                _LOGGER.error("get_vacation_balance failed: %s", err)

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_ENDPOINT, _handle_refresh_endpoint, schema=SCHEMA_REFRESH_ENDPOINT)
    hass.services.async_register(DOMAIN, SERVICE_GET_SCHEDULE_DATE, _handle_schedule_date, schema=SCHEMA_DATE)
    hass.services.async_register(DOMAIN, SERVICE_GET_SCHEDULE_WEEK, _handle_schedule_week, schema=SCHEMA_WEEK)
    hass.services.async_register(DOMAIN, SERVICE_GET_SCHEDULE_RANGE, _handle_schedule_range, schema=SCHEMA_RANGE)
    hass.services.async_register(DOMAIN, SERVICE_GET_PAY_MONTH, _handle_pay_month, schema=SCHEMA_PAY_MONTH)
    hass.services.async_register(DOMAIN, SERVICE_GET_VACATION_BALANCE, _handle_vacation_balance, schema=SCHEMA_VACATION_YEAR)
