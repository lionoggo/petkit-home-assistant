"""Coordinator schedule conflict tests using a minimal HA stub."""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from custom_components.petkit.client import DeviceInfo, PetkitAuthError
from custom_components.petkit.protocol import schedule_hash
from tests.test_protocol import sample_schedule


class FakeDataUpdateCoordinator:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, *args, **kwargs) -> None:
        self.data = None
        self.refresh_calls = 0
        self.listener_updates = 0
        self.manual_updates = 0

    async def async_request_refresh(self) -> None:
        self.refresh_calls += 1

    def async_set_updated_data(self, data) -> None:
        self.data = data
        self.manual_updates += 1

    def async_update_listeners(self) -> None:
        self.listener_updates += 1


class FakeUpdateFailed(Exception):
    pass


def _install_ha_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    homeassistant = types.ModuleType("homeassistant")
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    exceptions = types.ModuleType("homeassistant.exceptions")
    exceptions.ConfigEntryAuthFailed = type(
        "ConfigEntryAuthFailed", (Exception,), {}
    )
    helpers = types.ModuleType("homeassistant.helpers")
    update_coordinator = types.ModuleType(
        "homeassistant.helpers.update_coordinator"
    )
    update_coordinator.DataUpdateCoordinator = FakeDataUpdateCoordinator
    update_coordinator.UpdateFailed = FakeUpdateFailed
    helpers.update_coordinator = update_coordinator
    monkeypatch.setitem(sys.modules, "homeassistant", homeassistant)
    monkeypatch.setitem(
        sys.modules, "homeassistant.config_entries", config_entries
    )
    monkeypatch.setitem(sys.modules, "homeassistant.exceptions", exceptions)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers", helpers)
    monkeypatch.setitem(
        sys.modules,
        "homeassistant.helpers.update_coordinator",
        update_coordinator,
    )
    monkeypatch.delitem(
        sys.modules, "custom_components.petkit.coordinator", raising=False
    )


def test_schedule_conflict_does_not_write(monkeypatch) -> None:
    _install_ha_stubs(monkeypatch)
    from custom_components.petkit.coordinator import (
        PetkitDeviceCoordinator,
        PetkitScheduleConflict,
    )

    class FakeClient:
        def __init__(self) -> None:
            self.saved = False

        async def async_fetch_plan(self, device_id):
            return {"feedDailyList": sample_schedule()}

        async def async_save_schedule(self, device_id, days):
            self.saved = True

    async def run() -> None:
        client = FakeClient()
        coordinator = PetkitDeviceCoordinator(
            None, client, DeviceInfo("453354", "Feeder")
        )
        with pytest.raises(PetkitScheduleConflict):
            await coordinator.async_save_schedule(
                sample_schedule(), schedule_hash({"feedDailyList": []})
            )
        assert client.saved is False

    asyncio.run(run())


def test_poll_auth_failure_starts_reauth(monkeypatch) -> None:
    _install_ha_stubs(monkeypatch)
    from custom_components.petkit.coordinator import PetkitDeviceCoordinator

    class FakeClient:
        async def async_fetch_snapshot(self, device_id, *, day):
            raise PetkitAuthError("expired")

    async def run() -> None:
        coordinator = PetkitDeviceCoordinator(
            None, FakeClient(), DeviceInfo("453354", "Feeder")
        )
        with pytest.raises(
            sys.modules["homeassistant.exceptions"].ConfigEntryAuthFailed
        ):
            await coordinator._async_update_data()

    asyncio.run(run())

def test_eating_poll_merges_latest_identified_cat(monkeypatch) -> None:
    _install_ha_stubs(monkeypatch)
    from custom_components.petkit.coordinator import PetkitDeviceCoordinator

    class FakeClient:
        async def async_fetch_device_record(self, device_id, *, day):
            return {
                "eat": [
                    {
                        "items": [
                            {
                                "eventId": "active-event",
                                "petId": "2697859",
                                "petName": "啾啾",
                                "eatStartTime": 1_792_686_046,
                            }
                        ]
                    }
                ]
            }

    async def run() -> None:
        coordinator = PetkitDeviceCoordinator(
            None,
            FakeClient(),
            DeviceInfo("453354", "Feeder", locale="Asia/Taipei", timezone=8),
        )
        coordinator.data = {"eating": True, "online": True}
        assert await coordinator._async_refresh_eating_activity()
        assert coordinator.data["eating_pet_name"] == "啾啾"
        assert coordinator.data["last_eating_pet_name"] == "啾啾"
        assert coordinator.data["online"] is True
        assert coordinator.listener_updates == 1
        assert coordinator.manual_updates == 0

    asyncio.run(run())
