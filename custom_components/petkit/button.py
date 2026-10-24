"""Buttons for PETKIT D4sh feeders."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PetkitDeviceCoordinator
from .entity import PetkitEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entities: list[ButtonEntity] = []
    for coordinator in entry.runtime_data.coordinators.values():
        entities.extend(
            [
                PetkitFeedButton(coordinator, bin_number=1),
                PetkitFeedButton(coordinator, bin_number=2),
                PetkitRefreshButton(coordinator),
            ]
        )
    async_add_entities(entities)


class PetkitFeedButton(PetkitEntity, ButtonEntity):
    def __init__(
        self, coordinator: PetkitDeviceCoordinator, *, bin_number: int
    ) -> None:
        super().__init__(coordinator)
        self._bin_number = bin_number
        self._attr_translation_key = f"feed_bin_{bin_number}"
        self._attr_unique_id = (
            f"petkit_d4sh_{coordinator.device_id}_feed_bin_{bin_number}"
        )
        self._attr_icon = "mdi:food-variant"

    async def async_press(self) -> None:
        await self.coordinator.async_feed_now(
            1 if self._bin_number == 1 else 0,
            1 if self._bin_number == 2 else 0,
        )


class PetkitRefreshButton(PetkitEntity, ButtonEntity):
    _attr_translation_key = "refresh"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: PetkitDeviceCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"petkit_d4sh_{coordinator.device_id}_refresh"

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
