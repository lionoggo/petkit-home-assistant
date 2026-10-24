"""Home Assistant runtime smoke tests; skipped without the HA test plugin."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.petkit.client import DeviceInfo, PetkitClient
from custom_components.petkit.config_flow import PetkitConfigFlow
from custom_components.petkit.const import DOMAIN, PENDING_MEDIA_SIDECAR
from custom_components.petkit.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.petkit.media_client import PetkitMediaSidecarClient


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


async def test_services_register_in_home_assistant(hass) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    assert hass.services.has_service(DOMAIN, "feed_now")
    assert hass.services.has_service(DOMAIN, "save_schedule")
    assert hass.services.has_service(DOMAIN, "refresh_schedule")
    assert hass.services.has_service(DOMAIN, "get_camera_access")
    assert hass.services.has_service(DOMAIN, "start_camera_stream")
    assert hass.services.has_service(DOMAIN, "stop_camera_stream")


async def test_config_flow_creates_account_entry(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    with patch.object(
        PetkitConfigFlow,
        "_async_validate",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"account": "petkit-user@example.com", "password": "secret"},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["account"] == "petkit-user@example.com"
    assert result["data"]["password_hash"] == (
        "5ebe2294ecd0e0f08eab7690d2a6ee69"
    )


async def test_options_flow_configures_media_sidecar(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="PETKIT test",
        data={
            "account": "petkit-user@example.com",
            "password_hash": "5ebe2294ecd0e0f08eab7690d2a6ee69",
        },
    )
    entry.add_to_hass(hass)
    with patch.object(
        PetkitMediaSidecarClient,
        "async_health",
        new=AsyncMock(return_value={"ok": True}),
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "media_sidecar_url": "http://petkit-media:8099",
                "media_sidecar_token": "sidecar-token-with-enough-length",
            },
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["media_sidecar_url"] == "http://petkit-media:8099"

async def test_hassio_discovery_waits_for_account(hass) -> None:
    hass.data.pop(DOMAIN, None)
    flow = PetkitConfigFlow()
    flow.hass = hass
    discovery = SimpleNamespace(
        config={
            "host": "local-petkit-media",
            "port": 8099,
            "token": "discovered-sidecar-token",
        }
    )

    with patch.object(
        PetkitMediaSidecarClient,
        "async_health",
        new=AsyncMock(return_value={"ok": True}),
    ):
        result = await flow.async_step_hassio(discovery)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "account_required"
    assert hass.data[DOMAIN][PENDING_MEDIA_SIDECAR] == {
        "media_sidecar_url": "http://local-petkit-media:8099",
        "media_sidecar_token": "discovered-sidecar-token",
    }

async def test_hassio_discovery_updates_existing_entries(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="PETKIT test",
        data={
            "account": "petkit-user@example.com",
            "password_hash": "5ebe2294ecd0e0f08eab7690d2a6ee69",
        },
        options={},
    )
    entry.add_to_hass(hass)
    flow = PetkitConfigFlow()
    flow.hass = hass
    discovery = SimpleNamespace(
        config={
            "host": "local-petkit-media",
            "port": 8099,
            "token": "replacement-sidecar-token",
        }
    )

    with patch.object(
        PetkitMediaSidecarClient,
        "async_health",
        new=AsyncMock(return_value={"ok": True}),
    ):
        result = await flow.async_step_hassio(discovery)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "sidecar_configured"
    assert entry.options["media_sidecar_url"] == "http://local-petkit-media:8099"
    assert entry.options["media_sidecar_token"] == "replacement-sidecar-token"

def test_pending_sidecar_is_applied_to_a_new_account(hass) -> None:
    hass.data[DOMAIN] = {
        PENDING_MEDIA_SIDECAR: {
            "media_sidecar_url": "http://local-petkit-media:8099",
            "media_sidecar_token": "pending-token",
        }
    }
    flow = PetkitConfigFlow()
    flow.hass = hass

    assert flow._media_sidecar_options() == {
        "media_sidecar_url": "http://local-petkit-media:8099",
        "media_sidecar_token": "pending-token",
    }
    assert PENDING_MEDIA_SIDECAR not in hass.data[DOMAIN]


async def test_entry_creates_entities_and_unloads(hass) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    device = DeviceInfo(
        device_id="453354",
        name="Kitchen feeder",
        model_code=2,
        p2p_type=2,
        firmware="916",
        serial_number="SN123",
        timezone=8,
        locale="Asia/Shanghai",
    )
    schedule = [
        {
            "repeats": day,
            "suspended": 0,
            "items": [
                {
                    "id": "27000",
                    "time": 27000,
                    "amount1": 0,
                    "amount2": 2,
                    "name": "Breakfast",
                }
            ],
        }
        for day in range(1, 8)
    ]
    detail = {
        "id": 453354,
        "name": "Kitchen feeder",
        "sn": "SN123",
        "firmware": "916",
        "modelCode": 2,
        "p2pType": 2,
        "timezone": 8,
        "locale": "Asia/Shanghai",
        "settings": {"manualLock": 0},
    }
    home = {
        "serviceStatus": 1,
        "settings": {"manualLock": 0, "camera": 1},
        "status": {"food1": 2, "food2": 2, "feedState": {}},
        "dailyFeed": {},
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="PETKIT test",
        data={
            "account": "petkit-user@example.com",
            "password_hash": "5ebe2294ecd0e0f08eab7690d2a6ee69",
        },
        unique_id="test-account",
    )
    entry.add_to_hass(hass)
    with (
        patch.object(PetkitClient, "async_login", new=AsyncMock()),
        patch.object(
            PetkitClient,
            "async_discover_devices",
            new=AsyncMock(return_value=[device]),
        ),
        patch.object(
            PetkitClient,
            "async_fetch_device_detail",
            new=AsyncMock(return_value=(device, detail)),
        ),
        patch.object(
            PetkitClient,
            "async_fetch_plan",
            new=AsyncMock(return_value={"feedDailyList": schedule}),
        ),
        patch.object(
            PetkitClient,
            "async_fetch_device_record",
            new=AsyncMock(
                return_value={
                    "eat": [
                        {
                            "items": [
                                {
                                    "eventId": "event-one",
                                    "petId": "pet-one",
                                    "petName": "Test cat",
                                    "eatStartTime": 1_792_686_046,
                                    "eatEndTime": 1_792_686_088,
                                }
                            ]
                        }
                    ]
                }
            ),
        ),
        patch.object(
            PetkitClient,
            "async_fetch_snapshot",
            new=AsyncMock(
                return_value=(
                    {
                        "food1": 2,
                        "food2": 2,
                        "feeding": 0,
                        "eating": 0,
                        "feedState": {"realAmountTotal": 2, "eatCount": 1},
                    },
                    home,
                )
            ),
        ),
        patch.object(PetkitClient, "async_close", new=AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        entity_ids = hass.states.async_entity_ids()
        assert any(entity_id.startswith("sensor.") for entity_id in entity_ids)
        assert any(entity_id.startswith("binary_sensor.") for entity_id in entity_ids)
        assert any(entity_id.startswith("button.") for entity_id in entity_ids)
        assert any(entity_id.startswith("event.") for entity_id in entity_ids)
        assert any(entity_id.startswith("switch.") for entity_id in entity_ids)

        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        assert diagnostics["entry"]["password_hash"] == "<redacted>"
        assert "petkit-user@example.com" not in str(diagnostics)
        assert "SN123" not in str(diagnostics)

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.NOT_LOADED
