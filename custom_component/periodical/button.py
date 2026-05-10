"""Button platform for Periodical."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_USER_ID, CONF_USER_NAME, DOMAIN
from .coordinator import PeriodicalCoordinator

BUTTON_DESCRIPTIONS = (
    ButtonEntityDescription(key="refresh", translation_key="refresh", name="Refresh", icon="mdi:refresh", entity_category=EntityCategory.DIAGNOSTIC),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: PeriodicalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(PeriodicalRefreshButton(coordinator, entry, description) for description in BUTTON_DESCRIPTIONS)


class PeriodicalRefreshButton(CoordinatorEntity[PeriodicalCoordinator], ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: PeriodicalCoordinator, entry: ConfigEntry, description: ButtonEntityDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        user_name = entry.data.get(CONF_USER_NAME, "Periodical")
        user_id = entry.data[CONF_USER_ID]
        self._attr_unique_id = f"{DOMAIN}_{user_id}_{description.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, str(user_id))}, name=user_name, manufacturer="Periodical", model="Periodical API")

    async def async_press(self) -> None:
        await self.coordinator.async_force_refresh()
