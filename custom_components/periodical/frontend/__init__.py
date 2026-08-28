"""Serve and register the bundled Periodical Lovelace card."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later

_LOGGER = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent
MANIFEST_PATH = FRONTEND_DIR.parent / "manifest.json"
CARD_FILENAME = "periodical-card.js"
CARD_URL_BASE = "/periodical"
CARD_URL = f"{CARD_URL_BASE}/{CARD_FILENAME}"

with MANIFEST_PATH.open(encoding="utf-8") as manifest_file:
    INTEGRATION_VERSION = str(json.load(manifest_file).get("version", "0.0.0"))


class PeriodicalFrontendRegistration:
    """Manage the bundled frontend module registration."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._retry_unsub: Any = None

    async def async_register(self) -> None:
        """Expose the card and register it in storage-mode Lovelace."""
        await self._async_register_static_path()
        await self._async_register_lovelace_resource()

    async def _async_register_static_path(self) -> None:
        """Expose the integration frontend directory over HTTP."""
        try:
            await self.hass.http.async_register_static_paths(
                [StaticPathConfig(CARD_URL_BASE, str(FRONTEND_DIR), False)]
            )
        except RuntimeError:
            _LOGGER.debug("Periodical frontend path is already registered")

    async def _async_register_lovelace_resource(self) -> None:
        """Create or update the Lovelace module in storage mode."""
        lovelace = self.hass.data.get("lovelace")
        if lovelace is None:
            _LOGGER.debug("Lovelace is unavailable; card remains available at %s", CARD_URL)
            return

        mode = getattr(lovelace, "mode", getattr(lovelace, "resource_mode", "yaml"))
        if mode != "storage":
            _LOGGER.info(
                "Periodical card is available at %s?v=%s; add it as a module "
                "under lovelace.resources because Lovelace uses YAML mode",
                CARD_URL,
                INTEGRATION_VERSION,
            )
            return

        resources = getattr(lovelace, "resources", None)
        if resources is None:
            _LOGGER.debug("Lovelace resources are unavailable")
            return
        if not resources.loaded:
            if self._retry_unsub is None:
                self._retry_unsub = async_call_later(
                    self.hass, 5, self._async_retry_registration
                )
            return

        self._retry_unsub = None
        versioned_url = f"{CARD_URL}?v={INTEGRATION_VERSION}"
        matching = [
            item
            for item in resources.async_items()
            if str(item.get("url", "")).split("?", 1)[0] == CARD_URL
        ]

        if matching:
            current = matching[0]
            if current.get("url") != versioned_url or current.get("res_type") != "module":
                await resources.async_update_item(
                    current["id"], {"res_type": "module", "url": versioned_url}
                )
            for duplicate in matching[1:]:
                await resources.async_delete_item(duplicate["id"])
            return

        await resources.async_create_item(
            {"res_type": "module", "url": versioned_url}
        )

    async def _async_retry_registration(self, _now: Any) -> None:
        """Retry after Lovelace has finished loading its resource collection."""
        self._retry_unsub = None
        await self._async_register_lovelace_resource()
