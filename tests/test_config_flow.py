"""Config-flow tests for Periodical."""
from unittest.mock import patch

import pytest

from custom_components.periodical.const import DEFAULT_BASE_URL, DOMAIN


@pytest.mark.asyncio
async def test_user_flow_success(hass, mock_api) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={"api_key": "token", "base_url": DEFAULT_BASE_URL},
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "Test User"
    assert result["data"]["user_id"] == 7


@pytest.mark.asyncio
async def test_user_flow_invalid_auth(hass) -> None:
    from custom_components.periodical.api import PeriodicalAuthError

    with patch(
        "custom_components.periodical.config_flow.PeriodicalApi.get_me",
        side_effect=PeriodicalAuthError,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
            data={"api_key": "bad", "base_url": DEFAULT_BASE_URL},
        )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_auth"}
