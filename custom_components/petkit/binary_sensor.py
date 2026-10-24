"""Binary sensors for PETKIT D4sh feeders."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    entities: list[BinarySensorEntity] = []
    for coordinator in entry.runtime_data.coordinators.values():
        entities.extend(
            [
                PetkitOnlineSensor(coordinator),
                PetkitFeedingSensor(coordinator),
                PetkitEatingSensor(coordinator),
                PetkitPlanEnabledSensor(coordinator),
                PetkitLowBatterySensor(coordinator),
                PetkitProblemSensor(coordinator),
            ]
        )
    async_add_entities(entities)


class PetkitBinaryEntity(PetkitEntity, BinarySensorEntity):
    _key = ""

    def __init__(self, coordinator: PetkitDeviceCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = self._key
        self._attr_unique_id = (
            f"petkit_d4sh_{coordinator.device_id}_{self._key}"
        )


class PetkitOnlineSensor(PetkitBinaryEntity):
    _key = "online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def is_on(self) -> bool | None:
        value = (self.coordinator.data or {}).get("online")
        return value if isinstance(value, bool) else None


class PetkitFeedingSensor(PetkitBinaryEntity):
    _key = "feeding"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("feeding"))


class PetkitEatingSensor(PetkitBinaryEntity):
    _key = "eating"
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("eating"))


class PetkitPlanEnabledSensor(PetkitBinaryEntity):
    _key = "plan_enabled"

    @property
    def is_on(self) -> bool | None:
        value = (self.coordinator.data or {}).get("plan_enabled")
        return value if isinstance(value, bool) else None


class PetkitLowBatterySensor(PetkitBinaryEntity):
    _key = "low_battery"
    _attr_device_class = BinarySensorDeviceClass.BATTERY

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("battery_low"))


class PetkitProblemSensor(PetkitBinaryEntity):
    _key = "problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("has_error"))

    @property
    def extra_state_attributes(self) -> dict[str, str | int]:
        data = self.coordinator.data or {}
        return {
            "error_code": str(data.get("error_code") or ""),
            "error_message": str(data.get("error_message") or ""),
            "error_level": int(data.get("error_level") or 0),
        }
