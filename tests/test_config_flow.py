"""Config-flow tests for Periodical."""
from unittest.mock import patch

import pytest

from custom_components.periodical.config_flow import normalize_base_url
from custom_components.periodical.const import DOMAIN

SERVER = "https://periodical.example.com/api/v1"


@pytest.mark.asyncio
async def test_user_flow_success(hass, mock_api) -> None:
    # Creating the entry sets it up; without this the coordinator would try to
    # reach the real API from inside the test.
    with patch("custom_components.periodical.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
            data={"api_key": "token", "base_url": SERVER},
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
            data={"api_key": "bad", "base_url": SERVER},
        )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.asyncio
async def test_user_flow_rejects_non_http_address(hass, mock_api) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={"api_key": "token", "base_url": "periodical.example.com"},
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_url"}
    mock_api.get_me.assert_not_awaited()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://periodical.example.com", "https://periodical.example.com/api/v1"),
        ("https://periodical.example.com/", "https://periodical.example.com/api/v1"),
        (" https://periodical.example.com/api/v1/ ", "https://periodical.example.com/api/v1"),
        ("http://10.0.0.5:8000", "http://10.0.0.5:8000/api/v1"),
        ("https://example.com/custom/api", "https://example.com/custom/api"),
        ("periodical.example.com", None),
        ("ftp://periodical.example.com", None),
        ("", None),
    ],
)
def test_normalize_base_url(raw: str, expected: str | None) -> None:
    assert normalize_base_url(raw) == expected
