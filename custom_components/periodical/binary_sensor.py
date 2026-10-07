"""Binary sensor platform for Periodical."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

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

from . import schedule as sched
from .const import DATA_ABSENCES, DATA_API_HEALTH, DATA_ME, DOMAIN
from .coordinator import PeriodicalCoordinator
from .entity import PeriodicalEntity, async_cleanup_registry
from .schedule import Attrs, Data


@dataclass(frozen=True, kw_only=True)
class PeriodicalBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a Periodical binary sensor."""

    is_on_fn: Callable[[Data], bool | None]
    attr_fn: Callable[[Data], Attrs] | None = None


def _working_attrs(data: Data) -> Attrs:
    day = sched.today_day(data)
    if day is None:
        return {}
    shift = sched.scheduled_shift(day) or {}
    active = sched.active_shift(data)
    attrs: Attrs = {
        "status": day.get("status"),
        "scheduled_shift_code": shift.get("code"),
        "scheduled_shift_label": shift.get("label"),
        "carried_over_shift": bool(active and active.carried_over),
    }
    if overtime := sched.overtime_hours(day):
        attrs["overtime_hours"] = overtime
    return attrs


def _on_call_attrs(data: Data) -> Attrs:
    day = sched.today_day(data)
    if day is None:
        return {}
    return {
        "date": day.get("date"),
        "called_in": sched.overtime_hours(day) > 0,
        "overtime_hours": sched.overtime_hours(day),
    }


def _is_absent_today(data: Data) -> bool | None:
    """Absent today.

    The authoritative signal is the day's own status: the API marks vacation,
    sick, VAB and leave days there while still attaching the rotation shift.
    /absences is only a secondary source because it stays empty for
    schedule-driven absences, so relying on it alone reported "not absent"
    throughout a booked holiday.
    """
    absent = sched.is_absent_today(data)
    if absent is not None:
        if absent:
            return True
        # A day that is formally absent but still finishing last night's shift
        # is not absent yet; /absences must not override that either.
        day = sched.today_day(data)
        if day is not None and sched.day_is_absence(day):
            return False

    if absent is None and data.get(DATA_ABSENCES) is None:
        return None

    today = dt_util.now().date().isoformat()
    return any(sched.absence_covers(absence, today) for absence in sched.absence_items(data))


def _absence_attrs(data: Data) -> Attrs:
    day = sched.today_day(data)
    if day is None:
        return {}
    attrs: Attrs = {"status": day.get("status"), "date": day.get("date")}
    if sched.day_is_absence(day):
        attrs["absence_type"] = sched.day_status(day)
    return attrs


def _account_active(data: Data) -> bool | None:
    """The is_active flag from /me: whether the account is still enabled."""
    me = data.get(DATA_ME)
    if not isinstance(me, dict):
        return None
    value = me.get("is_active")
    return None if value is None else bool(value)


def _api_problem(data: Data) -> bool | None:
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


def _api_health_attrs(data: Data) -> Attrs:
    health = data.get(DATA_API_HEALTH)
    if not isinstance(health, dict):
        return {}
    api = health.get("api") if isinstance(health.get("api"), dict) else {}
    # Operational status only.  Granular counters (request/retry/DNS/timeout
    # totals, backoff timers) stay on the client for the debug log.
    attrs: Attrs = {
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
        is_on_fn=sched.is_working_now,
        attr_fn=_working_attrs,
    ),
    PeriodicalBinarySensorDescription(
        key="on_call_today",
        translation_key="on_call_today",
        icon="mdi:phone-in-talk",
        is_on_fn=sched.is_on_call_today,
        attr_fn=_on_call_attrs,
    ),
    # Deliberately no device_class: being on holiday is not a PROBLEM.
    PeriodicalBinarySensorDescription(
        key="absent_today",
        translation_key="absent_today",
        icon="mdi:account-off",
        is_on_fn=_is_absent_today,
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
        """Initialise the binary sensor from its description."""
        super().__init__(coordinator, entry, description, ENTITY_ID_FORMAT)

    @property
    def is_on(self) -> bool | None:
        """State, or None if the payload could not be read."""
        return self._evaluate(self.entity_description.is_on_fn, "state")
