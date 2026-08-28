"""Binary sensor platform for Periodical."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.binary_sensor import (
    ENTITY_ID_FORMAT,
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DATA_ABSENCES, DATA_API_HEALTH, DATA_ME, DOMAIN
from .coordinator import PeriodicalCoordinator
from .entity import PeriodicalEntity, async_cleanup_registry
from .sensor import _absence_items, _day_is_absence, _day_is_working, _day_status, _today_day

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class PeriodicalBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a Periodical binary sensor."""

    is_on_fn: Callable[[dict[str, Any]], bool | None]
    attr_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _account_active(data: dict[str, Any]) -> bool | None:
    """/me is_active flag — whether the authenticated account is active."""
    me = data.get(DATA_ME)
    if not isinstance(me, dict):
        return None
    val = me.get("is_active")
    return bool(val) if val is not None else None


def _is_working_today(data: dict[str, Any]) -> bool | None:
    day = _today_day(data)
    return None if day is None else _day_is_working(day)


def _has_absence_today(data: dict[str, Any]) -> bool | None:
    """Absent today.

    The authoritative signal is the day's own status — the API marks vacation,
    sick, VAB and leave days there while still attaching the rotation shift.
    /absences is only consulted as a secondary source because it is empty for
    schedule-driven absences, so relying on it alone reported "not absent"
    throughout a booked holiday.
    """
    day = _today_day(data)
    if day is not None and _day_is_absence(day):
        return True

    if day is None and data.get(DATA_ABSENCES) is None:
        return None

    today = dt_util.now().date().isoformat()
    for absence in _absence_items(data):
        start = absence.get("start_date") or absence.get("from") or absence.get("date") or ""
        end = absence.get("end_date") or absence.get("to") or start
        if start and start <= today <= end:
            return True

    return False


def _absence_attrs(data: dict[str, Any]) -> dict[str, Any]:
    day = _today_day(data)
    if day is None:
        return {}
    status = _day_status(day)
    return {
        "status": day.get("status"),
        "absence_type": status if _day_is_absence(day) else None,
        "date": day.get("date"),
    }


def _working_attrs(data: dict[str, Any]) -> dict[str, Any]:
    day = _today_day(data)
    if day is None:
        return {}
    shift = day.get("shift") if isinstance(day.get("shift"), dict) else {}
    return {
        "status": day.get("status"),
        "scheduled_shift_code": shift.get("code"),
        "scheduled_shift_label": shift.get("label"),
    }


def _api_problem(data: dict[str, Any]) -> bool | None:
    health = data.get(DATA_API_HEALTH)
    if not isinstance(health, dict):
        return None
    api = health.get("api") if isinstance(health.get("api"), dict) else {}
    return bool(
        not health.get("connected", False)
        or health.get("partial_failure")
        or health.get("using_stale_data")
        or health.get("failed_endpoints")
        or api.get("circuit_open")
    )


def _api_health_attrs(data: dict[str, Any]) -> dict[str, Any]:
    health = data.get(DATA_API_HEALTH)
    if not isinstance(health, dict):
        return {}
    api = health.get("api") if isinstance(health.get("api"), dict) else {}
    # Operational status only.  Granular counters (request/retry/dns/timeout
    # totals, backoff timers) stay on the client for the debug log.
    attrs: dict[str, Any] = {
        "connected": health.get("connected"),
        "partial_failure": health.get("partial_failure"),
        "using_stale_data": health.get("using_stale_data"),
        "failed_endpoints": health.get("failed_endpoints"),
        "stale_endpoints": health.get("stale_endpoints"),
        "last_error": health.get("last_error"),
        "api_circuit_open": api.get("circuit_open"),
        "api_last_success": api.get("last_success"),
    }
    return {key: value for key, value in attrs.items() if value is not None}


BINARY_SENSOR_DESCRIPTIONS: tuple[PeriodicalBinarySensorDescription, ...] = (
    PeriodicalBinarySensorDescription(
        key="working_today",
        translation_key="working_today",
        icon="mdi:briefcase-check",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        is_on_fn=_is_working_today,
        attr_fn=_working_attrs,
    ),
    PeriodicalBinarySensorDescription(
        key="absent_today",
        translation_key="absent_today",
        icon="mdi:account-off",
        is_on_fn=_has_absence_today,
        attr_fn=_absence_attrs,
    ),
    PeriodicalBinarySensorDescription(
        key="api_problem",
        translation_key="api_problem",
        icon="mdi:cloud-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=_api_problem,
        attr_fn=_api_health_attrs,
    ),
    PeriodicalBinarySensorDescription(
        key="account_active",
        translation_key="account_active",
        icon="mdi:account-check",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=_account_active,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Periodical binary sensors."""
    coordinator: PeriodicalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_cleanup_registry(hass, entry, BINARY_SENSOR_DESCRIPTIONS, "binary_sensor")
    async_add_entities(
        PeriodicalBinarySensor(coordinator, entry, description)
        for description in BINARY_SENSOR_DESCRIPTIONS
    )


class PeriodicalBinarySensor(PeriodicalEntity, BinarySensorEntity):
    """A Periodical binary sensor."""

    entity_description: PeriodicalBinarySensorDescription

    def __init__(
        self,
        coordinator: PeriodicalCoordinator,
        entry: ConfigEntry,
        description: PeriodicalBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, description, ENTITY_ID_FORMAT)

    @property
    def is_on(self) -> bool | None:
        return self._call(self.entity_description.is_on_fn, "state")
