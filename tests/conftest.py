"""Shared Periodical tests."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/periodical in every test."""
    return


@pytest.fixture
def me_payload() -> dict:
    return {"id": 7, "name": "Test User", "is_active": True}


@pytest.fixture
def mock_api(monkeypatch, me_payload):
    api = AsyncMock()
    api.get_me.return_value = me_payload
    monkeypatch.setattr(
        "custom_components.periodical.config_flow.PeriodicalApi",
        lambda **kwargs: api,
    )
    return api
