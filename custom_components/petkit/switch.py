"""Switches for PETKIT D4sh feeders."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
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
    async_add_entities(
        [
            PetkitManualLockSwitch(coordinator)
            for coordinator in entry.runtime_data.coordinators.values()
        ]
    )


class PetkitManualLockSwitch(PetkitEntity, SwitchEntity):
    _attr_translation_key = "manual_lock"
    _attr_icon = "mdi:lock"

    def __init__(self, coordinator: PetkitDeviceCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"petkit_d4sh_{coordinator.device_id}_manual_lock"
        )

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("manual_lock"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_manual_lock(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_manual_lock(False)
