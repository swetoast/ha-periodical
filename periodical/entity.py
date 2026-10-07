"""Shared entity plumbing for Periodical.

Both platforms build their entities the same way (same unique id scheme, same
device, same guarded evaluation of the description callables), so that lives
here rather than being duplicated and drifting.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription, async_generate_entity_id
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_USER_ID, CONF_USER_NAME, DOMAIN
from .coordinator import PeriodicalCoordinator

_LOGGER = logging.getLogger(__name__)

# Entities removed in a later version.  Left in the registry they would sit
# permanently "unavailable", so setup purges them once.
RETIRED_KEYS: dict[str, tuple[str, ...]] = {
    # Exact duplicates of pay_month_netto and vacation_remaining.
    "sensor": ("pay_summary", "vacation_summary"),
    "binary_sensor": (),
}


def _owns_short_ids(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Whether this entry owns the unprefixed `periodical_<key>` entity ids.

    The lowest entry_id wins, which is stable across restarts, so one entry keeps
    the short ids and any additional account gets ids scoped by its numeric user
    id instead of colliding and being handed an `_2` suffix by Home Assistant.
    """
    entry_ids = [
        candidate.entry_id
        for candidate in hass.config_entries.async_entries(DOMAIN)
        if candidate.source != "ignore"
    ]
    return not entry_ids or min(entry_ids) == entry.entry_id


def build_object_id(hass: HomeAssistant, entry: ConfigEntry, key: str) -> str:
    """Deterministic object id for one description key."""
    if _owns_short_ids(hass, entry):
        return f"{DOMAIN}_{key}"
    return f"{DOMAIN}_{entry.data[CONF_USER_ID]}_{key}"


def build_unique_id(entry: ConfigEntry, key: str) -> str:
    """Registry-stable unique id, always scoped by user."""
    return f"{DOMAIN}_{entry.data[CONF_USER_ID]}_{key}"


def async_cleanup_registry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    descriptions: Iterable[EntityDescription],
    platform: str,
) -> None:
    """Rename stale entity ids onto the current scheme and drop retired entities.

    Assigning `self.entity_id` in __init__ only affects brand-new entities; once
    an entity with a unique_id is in the registry, Home Assistant treats the
    registry's entity_id as authoritative.  Entities first created under an older
    naming therefore keep it forever unless renamed here.  Safe to run on every
    setup; it no-ops once the ids already match.
    """
    registry = er.async_get(hass)
    _purge_retired(registry, entry, platform)
    for description in descriptions:
        _rename_to_current_scheme(registry, hass, entry, platform, description.key)


def _purge_retired(registry: er.EntityRegistry, entry: ConfigEntry, platform: str) -> None:
    """Delete registry rows for entities this version no longer creates."""
    for key in RETIRED_KEYS.get(platform, ()):
        if entity_id := registry.async_get_entity_id(platform, DOMAIN, build_unique_id(entry, key)):
            _LOGGER.info("Removing retired Periodical entity %s", entity_id)
            registry.async_remove(entity_id)


def _rename_to_current_scheme(
    registry: er.EntityRegistry,
    hass: HomeAssistant,
    entry: ConfigEntry,
    platform: str,
    key: str,
) -> None:
    """Move one registered entity onto the current entity_id, if it is free."""
    current = registry.async_get_entity_id(platform, DOMAIN, build_unique_id(entry, key))
    if current is None:
        return
    desired = f"{platform}.{build_object_id(hass, entry, key)}"
    if current == desired:
        return
    if registry.async_get(desired) is not None:
        _LOGGER.debug("Cannot migrate %s -> %s: target id already exists", current, desired)
        return
    _LOGGER.info("Migrating entity_id %s -> %s", current, desired)
    try:
        registry.async_update_entity(current, new_entity_id=desired)
    except Exception:
        _LOGGER.warning("Failed to migrate %s -> %s", current, desired, exc_info=True)


class PeriodicalEntity(CoordinatorEntity[PeriodicalCoordinator]):
    """Common identity, device info and guarded description evaluation."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PeriodicalCoordinator,
        entry: ConfigEntry,
        description: EntityDescription,
        entity_id_format: str,
    ) -> None:
        """Wire up identity and device info from the entry and description."""
        super().__init__(coordinator)
        self.entity_description = description

        user_id = entry.data[CONF_USER_ID]

        self._attr_unique_id = build_unique_id(entry, description.key)
        self.entity_id = async_generate_entity_id(
            entity_id_format,
            build_object_id(coordinator.hass, entry, description.key),
            hass=coordinator.hass,
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(user_id))},
            name=entry.data.get(CONF_USER_NAME, "Periodical"),
            manufacturer="Periodical",
            model="Periodical API",
            entry_type=DeviceEntryType.SERVICE,
        )

    def _evaluate(self, func: Callable[[dict[str, Any]], Any] | None, what: str) -> Any:
        """Run a description callable, logging rather than silently swallowing.

        One malformed field must not take the whole platform down, but it should
        still be traceable in the debug log.
        """
        if func is None or self.coordinator.data is None:
            return None
        try:
            return func(self.coordinator.data)
        except Exception:
            _LOGGER.debug(
                "Failed to compute %s for %s",
                what,
                self.entity_description.key,
                exc_info=True,
            )
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Attributes from the description's attr_fn, if it has one."""
        return self._evaluate(getattr(self.entity_description, "attr_fn", None), "attributes") or {}
