"""Sensors for PETKIT D4sh feeders."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import FOOD_STATUS_OPTIONS
from .coordinator import PetkitDeviceCoordinator
from .entity import PetkitEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entities: list[SensorEntity] = []
    for coordinator in entry.runtime_data.coordinators.values():
        entities.extend(
            [
                PetkitFoodBinSensor(coordinator, 1),
                PetkitFoodBinSensor(coordinator, 2),
                PetkitDesiccantSensor(coordinator),
                PetkitTodayFeedSensor(coordinator),
                PetkitTodayEatSensor(coordinator),
                PetkitNextFeedSensor(coordinator),
                PetkitPlanSensor(coordinator),
                PetkitEatingPetSensor(coordinator),
                PetkitLastEatingPetSensor(coordinator),
            ]
        )
    async_add_entities(entities)


class PetkitFoodBinSensor(PetkitEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = FOOD_STATUS_OPTIONS

    def __init__(self, coordinator: PetkitDeviceCoordinator, bin_number: int) -> None:
        super().__init__(coordinator)
        self._bin_number = bin_number
        self._attr_translation_key = f"food_bin_{bin_number}"
        self._attr_unique_id = (
            f"petkit_d4sh_{coordinator.device_id}_food_bin_{bin_number}"
        )
        self._attr_icon = "mdi:food-variant"

    @property
    def native_value(self) -> str | None:
        return (self.coordinator.data or {}).get(f"food_bin_{self._bin_number}")


class PetkitDesiccantSensor(PetkitEntity, SensorEntity):
    _attr_translation_key = "desiccant_days"
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:water-percent"

    def __init__(self, coordinator: PetkitDeviceCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"petkit_d4sh_{coordinator.device_id}_desiccant"

    @property
    def native_value(self) -> int | None:
        return (self.coordinator.data or {}).get("desiccant_days")


class PetkitTodayFeedSensor(PetkitEntity, SensorEntity):
    _attr_translation_key = "today_feed"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:food"

    def __init__(self, coordinator: PetkitDeviceCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"petkit_d4sh_{coordinator.device_id}_today_feed"

    @property
    def native_value(self) -> int | None:
        return (self.coordinator.data or {}).get("today_feed")


class PetkitTodayEatSensor(PetkitEntity, SensorEntity):
    _attr_translation_key = "today_eat"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:cat"

    def __init__(self, coordinator: PetkitDeviceCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"petkit_d4sh_{coordinator.device_id}_today_eat"

    @property
    def native_value(self) -> int | None:
        return (self.coordinator.data or {}).get("today_eat")


class PetkitNextFeedSensor(PetkitEntity, SensorEntity):
    _attr_translation_key = "next_feed"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator: PetkitDeviceCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"petkit_d4sh_{coordinator.device_id}_next_feed"

    @property
    def native_value(self) -> datetime | None:
        value = (self.coordinator.data or {}).get("next_feed")
        return datetime.fromisoformat(value) if value else None


class PetkitPlanSensor(PetkitEntity, SensorEntity):
    _attr_translation_key = "feed_plan"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ("enabled", "paused", "unknown")
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: PetkitDeviceCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"petkit_d4sh_{coordinator.device_id}_plan"

    @property
    def native_value(self) -> str:
        enabled = (self.coordinator.data or {}).get("plan_enabled")
        if enabled is None:
            return "unknown"
        return "enabled" if enabled else "paused"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "device_id": self.coordinator.device_id,
            "hash": data.get("schedule_hash"),
            "schedule": data.get("schedule", []),
            "timezone": data.get("timezone"),
            "today_enabled": data.get("plan_enabled"),
            "service_expired": data.get("service_expired", False),
        }

class PetkitEatingPetSensor(PetkitEntity, SensorEntity):
    """The cat currently identified at the food bowl."""

    _attr_translation_key = "eating_pet"
    _attr_icon = "mdi:cat"

    def __init__(self, coordinator: PetkitDeviceCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"petkit_d4sh_{coordinator.device_id}_eating_pet"
        )

    @property
    def native_value(self) -> str:
        data = self.coordinator.data or {}
        if not data.get("eating_is_active"):
            return "none"
        return data.get("eating_pet_name") or "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "pet_id": data.get("eating_pet_id"),
            "event_id": data.get("eating_event_id"),
            "started_at": data.get("eating_started_at"),
            "identity_source": data.get("eating_identity_source"),
            "nearby_pet_id": data.get("eating_nearby_pet_id"),
            "nearby_pet_name": data.get("eating_nearby_pet_name"),
        }

class PetkitLastEatingPetSensor(PetkitEntity, SensorEntity):
    """The last cat with an identified eating event."""

    _attr_translation_key = "last_eating_pet"
    _attr_icon = "mdi:cat"

    def __init__(self, coordinator: PetkitDeviceCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"petkit_d4sh_{coordinator.device_id}_last_eating_pet"
        )

    @property
    def native_value(self) -> str:
        return (self.coordinator.data or {}).get(
            "last_eating_pet_name"
        ) or "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "pet_id": data.get("last_eating_pet_id"),
            "event_id": data.get("last_eating_event_id"),
            "started_at": data.get("last_eating_started_at"),
            "ended_at": data.get("last_eating_ended_at"),
            "duration_seconds": data.get("last_eating_duration_seconds"),
            "identity_source": data.get("last_eating_identity_source"),
        }
