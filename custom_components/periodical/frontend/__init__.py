"""Serve and register the bundled Periodical Lovelace card."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent
MANIFEST_PATH = FRONTEND_DIR.parent / "manifest.json"
CARD_FILENAME = "periodical-card.js"
CARD_URL_BASE = "/periodical-static"
CARD_URL = f"{CARD_URL_BASE}/{CARD_FILENAME}"

with MANIFEST_PATH.open(encoding="utf-8") as manifest_file:
    INTEGRATION_VERSION = str(json.load(manifest_file).get("version", "0.0.0"))


class PeriodicalFrontendRegistration:
    """Manage the process-global frontend module registration."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._static_path_registered = False

    async def async_register(self) -> None:
        """Expose the card and register it in storage-mode Lovelace."""
        await self._async_register_static_path()
        await self._async_register_lovelace_resource()

    async def _async_register_static_path(self) -> None:
        """Expose the integration frontend directory over HTTP once."""
        if self._static_path_registered:
            return
        await self.hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL_BASE, str(FRONTEND_DIR), False)]
        )
        self._static_path_registered = True

    async def _async_register_lovelace_resource(self) -> None:
        """Create or update the resource without risking stored resources."""
        lovelace = self.hass.data.get("lovelace")
        resources = getattr(lovelace, "resources", None) if lovelace is not None else None
        if resources is None:
            _LOGGER.info(
                "Periodical card is available at %s?v=%s; add it as a module "
                "under lovelace.resources when using YAML mode",
                CARD_URL,
                INTEGRATION_VERSION,
            )
            return

        # The collection is lazy-loaded. Reading or writing before async_load()
        # can operate on an empty in-memory collection and replace stored data.
        if not resources.loaded:
            await resources.async_load()

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

        await resources.async_create_item({"res_type": "module", "url": versioned_url})
