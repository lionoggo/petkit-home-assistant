"""Event entities for identified PETKIT D4sh activities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
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
        PetkitPetActivityEvent(coordinator)
        for coordinator in entry.runtime_data.coordinators.values()
    )


class PetkitPetActivityEvent(PetkitEntity, EventEntity):
    """Emit identified eating start and finish events."""

    _attr_translation_key = "pet_activity"
    _attr_event_types = ("eating_started", "eating_finished")

    def __init__(self, coordinator: PetkitDeviceCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"petkit_d4sh_{coordinator.device_id}_pet_activity"
        )
        self._known_activity_key = _activity_key(
            (coordinator.data or {}).get("eating_event")
        )

    def _handle_coordinator_update(self) -> None:
        event = (self.coordinator.data or {}).get("eating_event")
        if isinstance(event, dict) and event.get("event_id"):
            finished = bool(event.get("ended_at"))
            activity_key = _activity_key(event)
            if activity_key != self._known_activity_key:
                self._known_activity_key = activity_key
                self._trigger_event(
                    "eating_finished" if finished else "eating_started",
                    {
                        "pet_id": event.get("pet_id"),
                        "pet_name": event.get("pet_name"),
                        "event_id": event["event_id"],
                        "started_at": event.get("started_at"),
                        "ended_at": event.get("ended_at"),
                        "duration_seconds": event.get("duration_seconds"),
                        "identity_source": event.get("identity_source"),
                        "nearby_pet_id": event.get("nearby_pet_id"),
                        "nearby_pet_name": event.get("nearby_pet_name"),
                    },
                )
        super()._handle_coordinator_update()


def _activity_key(event: Any) -> str | None:
    if not isinstance(event, dict) or not event.get("event_id"):
        return None
    state = "finished" if event.get("ended_at") else "started"
    return f"{event['event_id']}:{state}"
