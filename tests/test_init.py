"""Set up the whole integration against a mocked API in a real Home Assistant."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.periodical.const import DOMAIN
from custom_components.periodical.binary_sensor import BINARY_SENSOR_DESCRIPTIONS
from custom_components.periodical.sensor import SENSOR_DESCRIPTIONS

TODAY = {
    "date": "2026-10-07",
    "status": "working",
    "shift": {"code": "N2", "start_time": "14:00", "end_time": "22:30", "overnight": False},
    "rotation_week": 10,
    "overtime": None,
    "ob_total": 285.0,
    "coworkers": [{"id": 4, "name": "Colleague", "shift_code": "N2"}],
}


def _api() -> AsyncMock:
    api = AsyncMock()
    api.diagnostics = {"circuit_open": False}
    api.get_me.return_value = {"id": 7, "name": "Test User", "is_active": True}
    api.get_shifts.return_value = [TODAY["shift"]]
    api.get_user_status.return_value = TODAY
    api.get_schedule_range.return_value = {"days": [TODAY]}
    api.get_next_shift.return_value = {"date": "2026-10-08", "shift": TODAY["shift"]}
    api.get_schedule_week.return_value = {"days": [TODAY]}
    api.get_absences.return_value = []
    api.get_schedule_month.return_value = {"month": 10, "year": 2026, "days": [TODAY]}
    api.get_vacation_balance.return_value = {"remaining_days": 7, "used_days": 18}
    api.get_schedule_year.return_value = {"year": 2026, "days": [TODAY]}
    api.get_pay_month.return_value = {"netto_pay": 42270.17, "brutto_pay": 57420.17}
    return api


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_7",
        title="Test User",
        data={"api_key": "token", "base_url": "https://example.test/api/v1",
              "user_id": 7, "user_name": "Test User"},
    )
    config_entry.add_to_hass(hass)
    return config_entry


@pytest.mark.asyncio
async def test_setup_creates_every_entity_and_unloads(hass: HomeAssistant, entry) -> None:
    with patch("custom_components.periodical.coordinator.PeriodicalApi", return_value=_api()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED

    for description in SENSOR_DESCRIPTIONS:
        assert hass.states.get(f"sensor.{DOMAIN}_{description.key}") is not None, description.key
    for description in BINARY_SENSOR_DESCRIPTIONS:
        assert hass.states.get(f"binary_sensor.{DOMAIN}_{description.key}") is not None, description.key

    assert hass.states.get(f"sensor.{DOMAIN}_status_today").state == "working"
    assert hass.states.get(f"binary_sensor.{DOMAIN}_working_today").state == "on"
    assert hass.states.get(f"binary_sensor.{DOMAIN}_on_call_today").state == "off"
    assert hass.services.has_service(DOMAIN, "get_pay_month")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not hass.services.has_service(DOMAIN, "get_pay_month")


@pytest.mark.asyncio
async def test_revoked_key_starts_reauth(hass: HomeAssistant, entry) -> None:
    from custom_components.periodical.api import PeriodicalAuthError

    api = _api()
    api.get_me.side_effect = PeriodicalAuthError("GET /me failed: HTTP 401 Unauthorized")
    with patch("custom_components.periodical.coordinator.PeriodicalApi", return_value=api):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)
