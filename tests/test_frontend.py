"""Tests for bundled frontend registration."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.periodical.frontend import (
    CARD_URL,
    INTEGRATION_VERSION,
    PeriodicalFrontendRegistration,
)


@pytest.mark.asyncio
async def test_loads_resources_before_creating() -> None:
    resources = Mock()
    resources.loaded = False
    resources.async_load = AsyncMock()
    resources.async_items.return_value = []
    resources.async_create_item = AsyncMock()
    hass = SimpleNamespace(
        data={"lovelace": SimpleNamespace(resources=resources)},
        http=SimpleNamespace(async_register_static_paths=AsyncMock()),
    )

    await PeriodicalFrontendRegistration(hass).async_register()

    resources.async_load.assert_awaited_once()
    resources.async_create_item.assert_awaited_once_with(
        {"res_type": "module", "url": f"{CARD_URL}?v={INTEGRATION_VERSION}"}
    )


@pytest.mark.asyncio
async def test_updates_existing_and_removes_duplicates() -> None:
    resources = Mock()
    resources.loaded = True
    resources.async_items.return_value = [
        {"id": "1", "url": f"{CARD_URL}?v=old", "res_type": "module"},
        {"id": "2", "url": CARD_URL, "res_type": "js"},
    ]
    resources.async_update_item = AsyncMock()
    resources.async_delete_item = AsyncMock()
    hass = SimpleNamespace(
        data={"lovelace": SimpleNamespace(resources=resources)},
        http=SimpleNamespace(async_register_static_paths=AsyncMock()),
    )

    await PeriodicalFrontendRegistration(hass).async_register()

    resources.async_update_item.assert_awaited_once()
    resources.async_delete_item.assert_awaited_once_with("2")
