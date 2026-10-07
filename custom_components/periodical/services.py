"""Services for Periodical: the API endpoints that are not worth an entity.

Each handler queries every configured account unless the call names one with
`config_entry_id`.  Results come back both as a service response (for
`response_variable`) and as a `periodical_*` event, so older automations built
on the event bus keep working.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date
from functools import partial
from typing import Any, Final

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api import PeriodicalApiError
from .const import DOMAIN, MAX_SCHEDULE_RANGE_DAYS
from .coordinator import PeriodicalCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_GET_SCHEDULE_DATE: Final = "get_schedule_date"
SERVICE_GET_SCHEDULE_WEEK: Final = "get_schedule_week"
SERVICE_GET_SCHEDULE_RANGE: Final = "get_schedule_range"
SERVICE_GET_PAY_MONTH: Final = "get_pay_month"
SERVICE_GET_VACATION_BALANCE: Final = "get_vacation_balance"

CONF_ENTRY_ID: Final = "config_entry_id"

# Optional targeting: without it a call fans out to every configured Periodical
# account, which is wrong as soon as a second user is added.
_BASE_SCHEMA: Final = {vol.Optional(CONF_ENTRY_ID): cv.string}

SCHEMA_DATE: Final = vol.Schema({**_BASE_SCHEMA, vol.Required("date"): cv.date})
SCHEMA_RANGE: Final = vol.Schema(
    {**_BASE_SCHEMA, vol.Required("from_date"): cv.date, vol.Required("to_date"): cv.date}
)
SCHEMA_PAY_MONTH: Final = vol.Schema(
    {
        **_BASE_SCHEMA,
        vol.Optional("year"): vol.Coerce(int),
        vol.Optional("month"): vol.All(vol.Coerce(int), vol.Range(min=1, max=12)),
    }
)
SCHEMA_YEAR: Final = vol.Schema({**_BASE_SCHEMA, vol.Optional("year"): vol.Coerce(int)})

Fetch = Callable[[PeriodicalCoordinator], Awaitable[Any]]
Handler = Callable[[HomeAssistant, ServiceCall], Awaitable[dict[str, Any]]]


def _targets(hass: HomeAssistant, call: ServiceCall) -> list[PeriodicalCoordinator]:
    """Coordinators the call applies to, honouring an optional config_entry_id."""
    loaded: dict[str, PeriodicalCoordinator] = hass.data.get(DOMAIN, {})
    entry_id = call.data.get(CONF_ENTRY_ID)
    if entry_id is None:
        return list(loaded.values())
    if (coordinator := loaded.get(entry_id)) is None:
        raise HomeAssistantError(f"No loaded Periodical config entry with id {entry_id!r}")
    return [coordinator]


async def _run(
    hass: HomeAssistant,
    call: ServiceCall,
    event: str,
    fetch: Fetch,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Fetch per targeted account, fire an event and collect the payloads."""
    results: dict[str, Any] = {}
    for coordinator in _targets(hass, call):
        try:
            data = await fetch(coordinator)
        except PeriodicalApiError as err:
            # PeriodicalApiError already carries path, status and body; a
            # traceback of the await chain would add nothing.
            _LOGGER.error(  # noqa: TRY400
                "%s failed for user %s: %s", event, coordinator.user_id, err
            )
            results[str(coordinator.user_id)] = {"error": str(err)}
            continue
        hass.bus.async_fire(
            f"{DOMAIN}_{event}",
            {"user_id": coordinator.user_id, **context, "data": data},
        )
        results[str(coordinator.user_id)] = data
    return {"results": results}


async def _handle_schedule_date(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    """Schedule for one specific date."""
    day: date = call.data["date"]
    return await _run(
        hass,
        call,
        "schedule_date",
        lambda c: c.api.get_schedule_date(c.user_id, day.isoformat()),
        {"date": day.isoformat()},
    )


async def _handle_schedule_week(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    """Schedule for the ISO week containing a date."""
    day: date = call.data["date"]
    return await _run(
        hass,
        call,
        "schedule_week",
        lambda c: c.api.get_schedule_week(c.user_id, day.isoformat()),
        {"date": day.isoformat()},
    )


async def _handle_schedule_range(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    """Schedule across a date range, rejecting spans the API would refuse."""
    start: date = call.data["from_date"]
    end: date = call.data["to_date"]
    if end < start:
        raise HomeAssistantError(f"to_date {end} precedes from_date {start}")
    span = (end - start).days + 1
    if span > MAX_SCHEDULE_RANGE_DAYS:
        raise HomeAssistantError(
            f"Requested {span} days; the Periodical API accepts at most {MAX_SCHEDULE_RANGE_DAYS}."
        )
    return await _run(
        hass,
        call,
        "schedule_range",
        lambda c: c.api.get_schedule_range(c.user_id, start.isoformat(), end.isoformat()),
        {"from_date": start.isoformat(), "to_date": end.isoformat()},
    )


async def _handle_pay_month(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    """Pay summary for a month, defaulting to the current one."""
    year = call.data.get("year")
    month = call.data.get("month")
    return await _run(
        hass,
        call,
        "pay_month",
        lambda c: c.api.get_pay_month(c.user_id, year, month),
        {"year": year, "month": month},
    )


async def _handle_vacation_balance(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    """Vacation balance for a year, defaulting to the current one."""
    year = call.data.get("year")
    return await _run(
        hass,
        call,
        "vacation_balance",
        lambda c: c.api.get_vacation_balance(c.user_id, year),
        {"year": year},
    )


SERVICES: Final[tuple[tuple[str, Handler, vol.Schema], ...]] = (
    (SERVICE_GET_SCHEDULE_DATE, _handle_schedule_date, SCHEMA_DATE),
    (SERVICE_GET_SCHEDULE_WEEK, _handle_schedule_week, SCHEMA_DATE),
    (SERVICE_GET_SCHEDULE_RANGE, _handle_schedule_range, SCHEMA_RANGE),
    (SERVICE_GET_PAY_MONTH, _handle_pay_month, SCHEMA_PAY_MONTH),
    (SERVICE_GET_VACATION_BALANCE, _handle_vacation_balance, SCHEMA_YEAR),
)

ALL_SERVICES: Final = tuple(name for name, _handler, _schema in SERVICES)


def async_register_services(hass: HomeAssistant) -> None:
    """Register the Periodical services."""
    for name, handler, schema in SERVICES:
        hass.services.async_register(
            DOMAIN,
            name,
            partial(handler, hass),
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )


def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove the Periodical services (called when the last entry unloads)."""
    for service in ALL_SERVICES:
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
