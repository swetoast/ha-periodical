"""Binary sensor platform for Periodical."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_USER_ID, CONF_USER_NAME, DATA_ABSENCES, DATA_API_HEALTH, DATA_SCHEDULE_TODAY, DATA_STATUS, DOMAIN
from .coordinator import ENDPOINT_NAMES, REFRESH_TIERS, PeriodicalCoordinator
from .helpers import normalize_absence_items, schedule_code

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class PeriodicalBinarySensorDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[dict[str, Any]], bool | None]
    attr_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _is_working_today(data: dict[str, Any]) -> bool | None:
    status_data = data.get(DATA_STATUS) or data.get(DATA_SCHEDULE_TODAY)
    if status_data is None:
        return None
    code = schedule_code(status_data)
    if code in {"OFF", "SEM", "LEAVE", "SICK", "VAB"}:
        return False
    status_str = status_data.get("status") if isinstance(status_data, dict) else None
    if isinstance(status_str, str):
        return status_str.lower() == "working"
    shift = status_data.get("shift") if isinstance(status_data, dict) else None
    return bool(isinstance(shift, dict) and shift.get("start_time"))


def _working_today_attrs(data: dict[str, Any]) -> dict[str, Any]:
    st = data.get(DATA_STATUS) or data.get(DATA_SCHEDULE_TODAY) or {}
    attrs = {"status": st.get("status"), "rotation_week": st.get("rotation_week"), "ob_total": st.get("ob_total"), "schedule_code": schedule_code(st)}
    shift = st.get("shift")
    if isinstance(shift, dict):
        attrs.update({"shift_code": shift.get("code"), "shift_label": shift.get("label"), "shift_color": shift.get("color"), "start_time": shift.get("start_time"), "end_time": shift.get("end_time")})
    return {key: value for key, value in attrs.items() if value is not None}


def _has_absence_today(data: dict[str, Any]) -> bool | None:
    if data.get(DATA_ABSENCES) is None:
        return None
    today_str = date.today().isoformat()
    for absence in normalize_absence_items(data.get(DATA_ABSENCES)):
        start = absence.get("start_date") or absence.get("from") or absence.get("date") or ""
        end = absence.get("end_date") or absence.get("to") or start
        if start <= today_str <= end:
            return True
    return False


def _api_problem(data: dict[str, Any]) -> bool | None:
    health = data.get(DATA_API_HEALTH)
    if not isinstance(health, dict):
        return None
    api = health.get("api") if isinstance(health.get("api"), dict) else {}
    return bool(not health.get("connected", False) or health.get("partial_failure") or health.get("using_stale_data") or health.get("failed_endpoints") or api.get("circuit_open"))


def _api_health_attrs(data: dict[str, Any]) -> dict[str, Any]:
    health = data.get(DATA_API_HEALTH)
    if not isinstance(health, dict):
        return {}
    api = health.get("api") if isinstance(health.get("api"), dict) else {}
    attrs = {
        "connected": health.get("connected"), "partial_failure": health.get("partial_failure"), "using_stale_data": health.get("using_stale_data"),
        "failed_endpoints": health.get("failed_endpoints"), "stale_keys": health.get("stale_keys"), "success_count": health.get("success_count"),
        "failure_count": health.get("failure_count"), "last_update_success": health.get("last_update_success"), "last_error": health.get("last_error"),
        "endpoint_stats": health.get("endpoint_stats"), "optimization": health.get("optimization"), "api_base_url": api.get("base_url"),
        "api_circuit_open": api.get("circuit_open"), "api_circuit_open_seconds": api.get("circuit_open_seconds"), "api_network_backoff_seconds": api.get("network_backoff_seconds"),
        "api_last_success": api.get("last_success"), "api_last_error_time": api.get("last_error_time"), "api_last_error": api.get("last_error"),
        "api_last_error_path": api.get("last_error_path"), "api_last_http_status": api.get("last_http_status"), "api_total_requests": api.get("total_requests"),
        "api_total_failures": api.get("total_failures"), "api_total_retries": api.get("total_retries"), "api_dns_failures": api.get("dns_failures"),
        "api_timeout_failures": api.get("timeout_failures"), "api_connection_failures": api.get("connection_failures"), "api_endpoint_stats": api.get("endpoint_stats"),
    }
    return {key: value for key, value in attrs.items() if value is not None}


def _endpoint_available_fn(endpoint_key: str) -> Callable[[dict[str, Any]], bool | None]:
    def _available(data: dict[str, Any]) -> bool | None:
        health = data.get(DATA_API_HEALTH)
        if not isinstance(health, dict):
            return None
        stats = health.get("endpoint_stats")
        if not isinstance(stats, dict):
            return None
        endpoint = stats.get(endpoint_key)
        if not isinstance(endpoint, dict):
            return None
        if endpoint.get("enabled") is False:
            return None
        available = endpoint.get("available")
        return bool(available) if available is not None else None
    return _available


def _endpoint_attrs_fn(endpoint_key: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def _attrs(data: dict[str, Any]) -> dict[str, Any]:
        health = data.get(DATA_API_HEALTH)
        stats = health.get("endpoint_stats") if isinstance(health, dict) else None
        endpoint = stats.get(endpoint_key) if isinstance(stats, dict) else None
        return endpoint if isinstance(endpoint, dict) else {}
    return _attrs


BINARY_SENSOR_DESCRIPTIONS: tuple[PeriodicalBinarySensorDescription, ...] = (
    PeriodicalBinarySensorDescription(key="working_today", translation_key="working_today", name="Working Today", icon="mdi:briefcase-check", device_class=BinarySensorDeviceClass.OCCUPANCY, is_on_fn=_is_working_today, attr_fn=_working_today_attrs),
    PeriodicalBinarySensorDescription(key="absent_today", translation_key="absent_today", name="Absent Today", icon="mdi:account-off", device_class=BinarySensorDeviceClass.PROBLEM, is_on_fn=_has_absence_today),
    PeriodicalBinarySensorDescription(key="api_problem", translation_key="api_problem", name="API Problem", icon="mdi:cloud-alert", device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC, is_on_fn=_api_problem, attr_fn=_api_health_attrs),
) + tuple(
    PeriodicalBinarySensorDescription(key=f"api_{endpoint_key}_available", translation_key=f"api_{endpoint_key}_available", name=f"API {ENDPOINT_NAMES.get(endpoint_key, endpoint_key.replace('_', ' ').title())} Available", icon="mdi:api", device_class=BinarySensorDeviceClass.CONNECTIVITY, entity_category=EntityCategory.DIAGNOSTIC, is_on_fn=_endpoint_available_fn(endpoint_key), attr_fn=_endpoint_attrs_fn(endpoint_key))
    for endpoint_key in REFRESH_TIERS
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: PeriodicalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(PeriodicalBinarySensor(coordinator, entry, description) for description in BINARY_SENSOR_DESCRIPTIONS)


class PeriodicalBinarySensor(CoordinatorEntity[PeriodicalCoordinator], BinarySensorEntity):
    entity_description: PeriodicalBinarySensorDescription
    _attr_has_entity_name = True

    def __init__(self, coordinator: PeriodicalCoordinator, entry: ConfigEntry, description: PeriodicalBinarySensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        user_name = entry.data.get(CONF_USER_NAME, "Periodical")
        user_id = entry.data[CONF_USER_ID]
        self._attr_unique_id = f"{DOMAIN}_{user_id}_{description.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, str(user_id))}, name=user_name, manufacturer="Periodical", model="Periodical API")

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        try:
            return self.entity_description.is_on_fn(self.coordinator.data)
        except Exception:
            _LOGGER.debug("Failed to calculate binary sensor %s", self.entity_description.key, exc_info=True)
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data is None or self.entity_description.attr_fn is None:
            return {}
        try:
            return self.entity_description.attr_fn(self.coordinator.data) or {}
        except Exception:
            _LOGGER.debug("Failed to calculate attributes for %s", self.entity_description.key, exc_info=True)
            return {}
