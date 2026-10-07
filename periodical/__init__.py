"""Periodical integration for Home Assistant."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change

from .const import CONF_USER_ID, DOMAIN
from .coordinator import PeriodicalCoordinator
from .services import ALL_SERVICES, async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


def _async_migrate_unique_id(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the base URL from the entry unique id.

    Early versions keyed the entry on `<domain>_<base_url>_<user_id>`, so simply
    correcting the API host produced a second entry for the same person.
    """
    desired = f"{DOMAIN}_{entry.data[CONF_USER_ID]}"
    if entry.unique_id == desired:
        return
    # The base-url bug could have produced two entries for one user.  Only one
    # may own the new id; the other keeps its old one rather than colliding.
    taken = any(
        other.unique_id == desired
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.entry_id != entry.entry_id
    )
    if taken:
        _LOGGER.warning(
            "Config entry %s duplicates Periodical user %s; remove one of them",
            entry.title,
            entry.data[CONF_USER_ID],
        )
        return
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

    # Day-scoped sensors (today, tomorrow, status) would otherwise keep showing
    # yesterday for up to one polling interval after midnight.
    async def _refresh_after_midnight(_now: datetime) -> None:
        await coordinator.async_request_refresh()

    entry.async_on_unload(
        async_track_time_change(hass, _refresh_after_midnight, hour=0, minute=0, second=30)
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    loaded = hass.data.get(DOMAIN, {})
    loaded.pop(entry.entry_id, None)
    # Drop the services once the last entry is gone so they do not linger as
    # no-ops that report "no loaded config entry".
    if not loaded:
        hass.data.pop(DOMAIN, None)
        async_unregister_services(hass)
    return True
