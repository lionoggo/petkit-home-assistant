"""Shared PETKIT entity helpers."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PetkitDeviceCoordinator


class PetkitEntity(CoordinatorEntity[PetkitDeviceCoordinator]):
    """Base entity associated with one PETKIT D4sh device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PetkitDeviceCoordinator) -> None:
        super().__init__(coordinator)
        device = coordinator.device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            manufacturer="PETKIT",
            model="D4sh Smart Feeder",
            name=device.name,
            serial_number=device.serial_number or device.device_id,
            sw_version=device.firmware,
        )

    @property
    def available(self) -> bool:
        return super().available
