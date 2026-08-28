"""Periodical integration for Home Assistant."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_USER_ID,
    DATA_FRONTEND,
    DATA_FRONTEND_LOCK,
    DOMAIN,
)
from .coordinator import PeriodicalCoordinator
from .frontend import PeriodicalFrontendRegistration
from .services import ALL_SERVICES, async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]
type PeriodicalConfigEntry = ConfigEntry[PeriodicalCoordinator]


def _async_migrate_unique_id(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the base URL from the entry unique id."""
    desired = f"{DOMAIN}_{entry.data[CONF_USER_ID]}"
    if entry.unique_id != desired:
        _LOGGER.debug("Migrating config entry unique_id %s -> %s", entry.unique_id, desired)
        hass.config_entries.async_update_entry(entry, unique_id=desired)


async def _async_setup_frontend(hass: HomeAssistant) -> None:
    """Set up the optional bundled card once without blocking integration setup."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    lock = domain_data.setdefault(DATA_FRONTEND_LOCK, asyncio.Lock())
    async with lock:
        if DATA_FRONTEND in domain_data:
            return
        registration = PeriodicalFrontendRegistration(hass)
        try:
            await registration.async_register()
        except (OSError, RuntimeError, ValueError):
            _LOGGER.exception(
                "Unable to register the bundled Periodical card; "
                "the integration will continue without automatic card registration"
            )
            return
        domain_data[DATA_FRONTEND] = registration


async def async_setup_entry(
    hass: HomeAssistant, entry: PeriodicalConfigEntry
) -> bool:
    """Set up Periodical from a config entry."""
    _async_migrate_unique_id(hass, entry)

    coordinator = PeriodicalCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # The frontend is optional. Failure must not block sensors or services.
    await _async_setup_frontend(hass)

    if not all(hass.services.has_service(DOMAIN, name) for name in ALL_SERVICES):
        async_register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(
    hass: HomeAssistant, entry: PeriodicalConfigEntry
) -> None:
    """Reload the entry when its options or data change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: PeriodicalConfigEntry
) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    if not any(
        loaded.entry_id != entry.entry_id
        for loaded in hass.config_entries.async_loaded_entries(DOMAIN)
    ):
        async_unregister_services(hass)
    return True
