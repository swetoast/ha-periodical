"""Periodical integration for Home Assistant."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    ALL_ENDPOINT_KEYS,
    CONF_ENABLED_ENDPOINTS,
    CONF_MAX_CONCURRENT_REQUESTS,
    CONF_REQUEST_TIMEOUT_SECONDS,
    CONF_RETRY_ATTEMPTS,
    CONF_UPDATE_INTERVAL_MINUTES,
    DEFAULT_MAX_CONCURRENT_REQUESTS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_RETRY_ATTEMPTS,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
)
from .coordinator import PeriodicalCoordinator
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "binary_sensor", "button"]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    async_register_services(hass)
    coordinator = PeriodicalCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.version == 1:
        options = dict(entry.options)
        options.setdefault(CONF_ENABLED_ENDPOINTS, list(ALL_ENDPOINT_KEYS))
        options.setdefault(CONF_UPDATE_INTERVAL_MINUTES, DEFAULT_UPDATE_INTERVAL_MINUTES)
        options.setdefault(CONF_REQUEST_TIMEOUT_SECONDS, DEFAULT_REQUEST_TIMEOUT_SECONDS)
        options.setdefault(CONF_RETRY_ATTEMPTS, DEFAULT_RETRY_ATTEMPTS)
        options.setdefault(CONF_MAX_CONCURRENT_REQUESTS, DEFAULT_MAX_CONCURRENT_REQUESTS)
        hass.config_entries.async_update_entry(entry, options=options)
    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
