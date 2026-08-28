"""OpenAPI-aligned client contract tests."""
from unittest.mock import AsyncMock

import pytest

from custom_components.periodical.api import (
    POLICY_AUTH_FAIL,
    POLICY_FAIL,
    POLICY_FORBIDDEN,
    PeriodicalApi,
)


@pytest.fixture
def api() -> PeriodicalApi:
    return PeriodicalApi("https://example.test/api/v1", "token", AsyncMock())


@pytest.mark.asyncio
async def test_status_simulation_parameters(api: PeriodicalApi) -> None:
    api._get = AsyncMock(return_value={})
    await api.get_user_status(7, "2026-08-28", "20:15")
    api._get.assert_awaited_once_with(
        "/users/7/status",
        params={"date": "2026-08-28", "time": "20:15"},
    )


@pytest.mark.asyncio
async def test_status_omits_empty_simulation_parameters(api: PeriodicalApi) -> None:
    api._get = AsyncMock(return_value={})
    await api.get_user_status(7)
    api._get.assert_awaited_once_with("/users/7/status", params=None)


@pytest.mark.asyncio
async def test_next_shift_simulation_parameters(api: PeriodicalApi) -> None:
    api._get = AsyncMock(return_value={})
    await api.get_next_shift(7, "2026-08-28", "20:15")
    api._get.assert_awaited_once_with(
        "/users/7/next-shift",
        params={"date": "2026-08-28", "time": "20:15"},
    )


def test_http_status_policies_are_distinct() -> None:
    assert PeriodicalApi._status_policy(401) == POLICY_AUTH_FAIL
    assert PeriodicalApi._status_policy(403) == POLICY_FORBIDDEN
    assert PeriodicalApi._status_policy(422) == POLICY_FAIL
