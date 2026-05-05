"""Binary sensor platform for Periodical."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_USER_ID,
    CONF_USER_NAME,
    DATA_ABSENCES,
    DATA_API_HEALTH,
    DATA_SCHEDULE_TODAY,
    DATA_STATUS,
    DOMAIN,
)
from .coordinator import PeriodicalCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class PeriodicalBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a Periodical binary sensor."""

    is_on_fn: Callable[[dict[str, Any]], bool | None]
    attr_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _is_working_today(data: dict[str, Any]) -> bool | None:
    status_data = data.get(DATA_STATUS) or data.get(DATA_SCHEDULE_TODAY)
    if status_data is None:
        return None

    status_str = status_data.get("status") if isinstance(status_data, dict) else None
    if isinstance(status_str, str):
        return status_str.lower() == "working"

    shift = status_data.get("shift") if isinstance(status_data, dict) else None
    return bool(isinstance(shift, dict) and shift.get("start_time"))


def _working_today_attrs(data: dict[str, Any]) -> dict[str, Any]:
    st = data.get(DATA_STATUS) or data.get(DATA_SCHEDULE_TODAY) or {}
    attrs: dict[str, Any] = {
        "status": st.get("status"),
        "rotation_week": st.get("rotation_week"),
        "ob_total": st.get("ob_total"),
    }
    shift = st.get("shift")
    if isinstance(shift, dict):
        attrs["shift_code"] = shift.get("code")
        attrs["shift_label"] = shift.get("label")
        attrs["shift_color"] = shift.get("color")
        attrs["start_time"] = shift.get("start_time")
        attrs["end_time"] = shift.get("end_time")
    return {k: v for k, v in attrs.items() if v is not None}


def _has_absence_today(data: dict[str, Any]) -> bool | None:
    today_str = date.today().isoformat()
    absences = data.get(DATA_ABSENCES)
    if absences is None:
        return None

    items: list[Any] = []
    if isinstance(absences, list):
        items = absences
    elif isinstance(absences, dict):
        items = absences.get("absences") or absences.get("items") or []

    for ab in items:
        if not isinstance(ab, dict):
            continue
        start = ab.get("start_date") or ab.get("from") or ab.get("date") or ""
        end = ab.get("end_date") or ab.get("to") or start
        if start <= today_str <= end:
            return True

    return False


def _api_connected(data: dict[str, Any]) -> bool | None:
    health = data.get(DATA_API_HEALTH)
    if not isinstance(health, dict):
        return None
    return bool(health.get("connected"))


def _api_problem(data: dict[str, Any]) -> bool | None:
    health = data.get(DATA_API_HEALTH)
    if not isinstance(health, dict):
        return None
    api = health.get("api") if isinstance(health.get("api"), dict) else {}
    return bool(
        health.get("partial_failure")
        or health.get("using_stale_data")
        or health.get("failed_endpoints")
        or api.get("circuit_open")
    )


def _api_health_attrs(data: dict[str, Any]) -> dict[str, Any]:
    health = data.get(DATA_API_HEALTH)
    return health if isinstance(health, dict) else {}


BINARY_SENSOR_DESCRIPTIONS: tuple[PeriodicalBinarySensorDescription, ...] = (
    PeriodicalBinarySensorDescription(
        key="working_today",
        translation_key="working_today",
        name="Working Today",
        icon="mdi:briefcase-check",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        is_on_fn=_is_working_today,
        attr_fn=_working_today_attrs,
    ),
    PeriodicalBinarySensorDescription(
        key="absent_today",
        translation_key="absent_today",
        name="Absent Today",
        icon="mdi:account-off",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=_has_absence_today,
        attr_fn=None,
    ),
    PeriodicalBinarySensorDescription(
        key="api_connected",
        translation_key="api_connected",
        name="API Connected",
        icon="mdi:cloud-check",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        is_on_fn=_api_connected,
        attr_fn=_api_health_attrs,
    ),
    PeriodicalBinarySensorDescription(
        key="api_problem",
        translation_key="api_problem",
        name="API Problem",
        icon="mdi:cloud-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=_api_problem,
        attr_fn=_api_health_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Periodical binary sensors."""
    coordinator: PeriodicalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        PeriodicalBinarySensor(coordinator, entry, description)
        for description in BINARY_SENSOR_DESCRIPTIONS
    )


class PeriodicalBinarySensor(CoordinatorEntity[PeriodicalCoordinator], BinarySensorEntity):
    """A Periodical binary sensor."""

    entity_description: PeriodicalBinarySensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PeriodicalCoordinator,
        entry: ConfigEntry,
        description: PeriodicalBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        user_name = entry.data.get(CONF_USER_NAME, "Periodical")
        user_id = entry.data[CONF_USER_ID]
        self._attr_unique_id = f"{DOMAIN}_{user_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(user_id))},
            name=user_name,
            manufacturer="Periodical",
            model="Periodical API",
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        try:
            return self.entity_description.is_on_fn(self.coordinator.data)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Failed to calculate binary sensor %s", self.entity_description.key, exc_info=True)
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data is None or self.entity_description.attr_fn is None:
            return {}
        try:
            return self.entity_description.attr_fn(self.coordinator.data) or {}
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Failed to calculate attributes for %s", self.entity_description.key, exc_info=True)
            return {}
