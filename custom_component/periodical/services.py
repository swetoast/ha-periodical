"""Services for Periodical — expose the extra API endpoints as HA services."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api import PeriodicalApiError
from .const import DOMAIN, MAX_SCHEDULE_RANGE_DAYS

_LOGGER = logging.getLogger(__name__)

SERVICE_GET_SCHEDULE_DATE = "get_schedule_date"
SERVICE_GET_SCHEDULE_WEEK = "get_schedule_week"
SERVICE_GET_SCHEDULE_RANGE = "get_schedule_range"
SERVICE_GET_PAY_MONTH = "get_pay_month"
SERVICE_GET_VACATION_BALANCE = "get_vacation_balance"

ALL_SERVICES = (
    SERVICE_GET_SCHEDULE_DATE,
    SERVICE_GET_SCHEDULE_WEEK,
    SERVICE_GET_SCHEDULE_RANGE,
    SERVICE_GET_PAY_MONTH,
    SERVICE_GET_VACATION_BALANCE,
)

CONF_ENTRY_ID = "config_entry_id"

# Optional targeting: without it a service call fans out to every configured
# Periodical account, which is wrong as soon as a second user is added.
_BASE = {vol.Optional(CONF_ENTRY_ID): cv.string}

SCHEMA_DATE = vol.Schema({**_BASE, vol.Required("date"): cv.date})
SCHEMA_WEEK = vol.Schema({**_BASE, vol.Required("date"): cv.date})
SCHEMA_RANGE = vol.Schema(
    {**_BASE, vol.Required("from_date"): cv.date, vol.Required("to_date"): cv.date}
)
SCHEMA_PAY_MONTH = vol.Schema(
    {
        **_BASE,
        vol.Optional("year"): vol.Coerce(int),
        vol.Optional("month"): vol.All(vol.Coerce(int), vol.Range(min=1, max=12)),
    }
)
SCHEMA_VACATION_YEAR = vol.Schema({**_BASE, vol.Optional("year"): vol.Coerce(int)})


def _coordinators(hass: HomeAssistant, call: ServiceCall) -> list[Any]:
    """Coordinators the call targets, honouring an optional config_entry_id."""
    entries: dict[str, Any] = hass.data.get(DOMAIN, {})
    entry_id = call.data.get(CONF_ENTRY_ID)
    if entry_id is None:
        return list(entries.values())
    coordinator = entries.get(entry_id)
    if coordinator is None:
        raise HomeAssistantError(
            f"No loaded Periodical config entry with id {entry_id!r}"
        )
    return [coordinator]


def async_register_services(hass: HomeAssistant) -> None:
    """Register Periodical services."""

    async def _run(
        call: ServiceCall,
        event: str,
        fetch,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Fetch per targeted account, fire an event and return the payload.

        The event is kept for existing automations; the returned dict is what
        `response_variable` picks up.
        """
        results: dict[str, Any] = {}
        for coordinator in _coordinators(hass, call):
            try:
                data = await fetch(coordinator)
            except PeriodicalApiError as err:
                _LOGGER.error("%s failed for user %s: %s", event, coordinator.user_id, err)
                results[str(coordinator.user_id)] = {"error": str(err)}
                continue
            payload = {"user_id": coordinator.user_id, **context, "data": data}
            hass.bus.async_fire(f"{DOMAIN}_{event}", payload)
            results[str(coordinator.user_id)] = data
        return {"results": results}

    async def _handle_schedule_date(call: ServiceCall) -> dict[str, Any]:
        day: date = call.data["date"]
        return await _run(
            call,
            "schedule_date",
            lambda c: c.api.get_schedule_date(c.user_id, day.isoformat()),
            {"date": day.isoformat()},
        )

    async def _handle_schedule_week(call: ServiceCall) -> dict[str, Any]:
        day: date = call.data["date"]
        return await _run(
            call,
            "schedule_week",
            lambda c: c.api.get_schedule_week(c.user_id, day.isoformat()),
            {"date": day.isoformat()},
        )

    async def _handle_schedule_range(call: ServiceCall) -> dict[str, Any]:
        start: date = call.data["from_date"]
        end: date = call.data["to_date"]
        if end < start:
            raise HomeAssistantError(f"to_date {end} precedes from_date {start}")
        span = (end - start).days + 1
        if span > MAX_SCHEDULE_RANGE_DAYS:
            raise HomeAssistantError(
                f"Requested {span} days; the Periodical API accepts at most "
                f"{MAX_SCHEDULE_RANGE_DAYS}."
            )
        return await _run(
            call,
            "schedule_range",
            lambda c: c.api.get_schedule_range(c.user_id, start.isoformat(), end.isoformat()),
            {"from_date": start.isoformat(), "to_date": end.isoformat()},
        )

    async def _handle_pay_month(call: ServiceCall) -> dict[str, Any]:
        year = call.data.get("year")
        month = call.data.get("month")
        return await _run(
            call,
            "pay_month",
            lambda c: c.api.get_pay_month(c.user_id, year, month),
            {"year": year, "month": month},
        )

    async def _handle_vacation_balance(call: ServiceCall) -> dict[str, Any]:
        year = call.data.get("year")
        return await _run(
            call,
            "vacation_balance",
            lambda c: c.api.get_vacation_balance(c.user_id, year),
            {"year": year},
        )

    handlers = (
        (SERVICE_GET_SCHEDULE_DATE, _handle_schedule_date, SCHEMA_DATE),
        (SERVICE_GET_SCHEDULE_WEEK, _handle_schedule_week, SCHEMA_WEEK),
        (SERVICE_GET_SCHEDULE_RANGE, _handle_schedule_range, SCHEMA_RANGE),
        (SERVICE_GET_PAY_MONTH, _handle_pay_month, SCHEMA_PAY_MONTH),
        (SERVICE_GET_VACATION_BALANCE, _handle_vacation_balance, SCHEMA_VACATION_YEAR),
    )
    for name, handler, schema in handlers:
        hass.services.async_register(
            DOMAIN,
            name,
            handler,
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )


def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove Periodical services (called when the last config entry unloads)."""
    for service in ALL_SERVICES:
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
