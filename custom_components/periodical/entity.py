"""Shared entity plumbing for Periodical.

Sensor and binary_sensor previously duplicated their __init__ and reached across
platforms to borrow the entity-id migration helper.  Both now live here.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription, async_generate_entity_id
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_USER_ID, CONF_USER_NAME, DOMAIN
from .coordinator import PeriodicalCoordinator

_LOGGER = logging.getLogger(__name__)

# Entities removed in a later version.  Left in the registry they would sit
# permanently "unavailable", so setup purges them once.
REMOVED_UNIQUE_ID_KEYS: dict[str, tuple[str, ...]] = {
    # Exact duplicates of pay_month_netto and vacation_remaining.
    "sensor": ("pay_summary", "vacation_summary"),
    "binary_sensor": (),
}


def _is_primary_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Whether this entry owns the unprefixed `periodical_<key>` entity ids.

    Sorting by entry_id is stable across restarts, so the same entry keeps the
    short ids and any additional user gets ids scoped by their numeric user id
    instead of colliding and being handed an `_2` suffix by Home Assistant.
    """
    entries = sorted(
        (e for e in hass.config_entries.async_entries(DOMAIN) if e.source != "ignore"),
        key=lambda e: e.entry_id,
    )
    return not entries or entries[0].entry_id == entry.entry_id


def build_object_id(hass: HomeAssistant, entry: ConfigEntry, key: str) -> str:
    """Deterministic object id for one description key."""
    if _is_primary_entry(hass, entry):
        return f"{DOMAIN}_{key}"
    return f"{DOMAIN}_{entry.data[CONF_USER_ID]}_{key}"


def build_unique_id(entry: ConfigEntry, key: str) -> str:
    return f"{DOMAIN}_{entry.data[CONF_USER_ID]}_{key}"


def async_cleanup_registry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    descriptions: Iterable[EntityDescription],
    platform: str,
) -> None:
    """Rename stale entity ids onto the current scheme and drop removed entities.

    Setting `self.entity_id` in __init__ only affects brand-new entities; once an
    entity with a unique_id is in the registry, Home Assistant treats the
    registry's entity_id as authoritative.  Entities first created under an older
    naming therefore keep it forever unless renamed here.  Safe to run on every
    setup — it no-ops once the ids already match.
    """
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)

    for key in REMOVED_UNIQUE_ID_KEYS.get(platform, ()):
        existing = registry.async_get_entity_id(platform, DOMAIN, build_unique_id(entry, key))
        if existing:
            _LOGGER.info("Removing retired Periodical entity %s", existing)
            registry.async_remove(existing)

    for description in descriptions:
        unique_id = build_unique_id(entry, description.key)
        existing = registry.async_get_entity_id(platform, DOMAIN, unique_id)
        if not existing:
            continue
        desired = f"{platform}.{build_object_id(hass, entry, description.key)}"
        if existing == desired:
            continue
        if registry.async_get(desired) is not None:
            _LOGGER.debug("Cannot migrate %s -> %s: target id already exists", existing, desired)
            continue
        _LOGGER.info("Migrating entity_id %s -> %s", existing, desired)
        try:
            registry.async_update_entity(existing, new_entity_id=desired)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Failed to migrate %s -> %s", existing, desired, exc_info=True)


class PeriodicalEntity(CoordinatorEntity[PeriodicalCoordinator]):
    """Common identity, device info and safe accessor plumbing."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PeriodicalCoordinator,
        entry: ConfigEntry,
        description: EntityDescription,
        entity_id_format: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description

        user_id = entry.data[CONF_USER_ID]
        user_name = entry.data.get(CONF_USER_NAME, "Periodical")

        self._attr_unique_id = build_unique_id(entry, description.key)
        self.entity_id = async_generate_entity_id(
            entity_id_format,
            build_object_id(coordinator.hass, entry, description.key),
            hass=coordinator.hass,
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(user_id))},
            name=user_name,
            manufacturer="Periodical",
            model="Periodical API",
        )

    def _call(self, func: Any, what: str) -> Any:
        """Run a description callable, logging rather than swallowing failures."""
        if self.coordinator.data is None or func is None:
            return None
        try:
            return func(self.coordinator.data)
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "Failed to compute %s for %s",
                what,
                self.entity_description.key,
                exc_info=True,
            )
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attr_fn = getattr(self.entity_description, "attr_fn", None)
        if attr_fn is None:
            return {}
        return self._call(attr_fn, "attributes") or {}
