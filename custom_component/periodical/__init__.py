"""Periodical integration for Home Assistant."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_USER_ID, DOMAIN
from .coordinator import PeriodicalCoordinator
from .services import (
    ALL_SERVICES,
    async_register_services,
    async_unregister_services,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


def _async_migrate_unique_id(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the base URL from the entry unique id.

    Early versions keyed the entry on `<domain>_<base_url>_<user_id>`, so simply
    correcting the API host produced a second entry for the same person.
    """
    desired = f"{DOMAIN}_{entry.data[CONF_USER_ID]}"
    if entry.unique_id != desired:
        _LOGGER.debug("Migrating config entry unique_id %s -> %s", entry.unique_id, desired)
        hass.config_entries.async_update_entry(entry, unique_id=desired)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Periodical from a config entry."""
    _async_migrate_unique_id(hass, entry)

    coordinator = PeriodicalCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # async_setup_entry runs per config entry; the services are global.
    if not all(hass.services.has_service(DOMAIN, name) for name in ALL_SERVICES):
        async_register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options or data change (e.g. after reauth)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    entries = hass.data.get(DOMAIN, {})
    entries.pop(entry.entry_id, None)
    # Drop the services once the last entry is gone so they do not linger as no-ops.
    if not entries:
        hass.data.pop(DOMAIN, None)
        async_unregister_services(hass)
    return True
