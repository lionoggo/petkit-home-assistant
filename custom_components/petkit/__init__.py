"""Home Assistant integration for PETKIT D4sh feeders."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import voluptuous as vol
from aiohttp import ClientSession
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import PetkitClient, PetkitError, PetkitVerificationRequiredError
from .const import (
    ATTR_AMOUNT1,
    ATTR_AMOUNT2,
    ATTR_DAYS,
    ATTR_DEVICE_ID,
    ATTR_EXPECTED_HASH,
    ATTR_QUALITY,
    ATTR_SESSION_ID,
    CONF_ACCOUNT,
    CONF_MEDIA_SIDECAR_TOKEN,
    CONF_MEDIA_SIDECAR_URL,
    CONF_PASSWORD_HASH,
    DOMAIN,
    PLATFORMS,
    SERVICE_FEED_NOW,
    SERVICE_GET_CAMERA_ACCESS,
    SERVICE_REFRESH_SCHEDULE,
    SERVICE_SAVE_SCHEDULE,
    SERVICE_START_CAMERA_STREAM,
    SERVICE_STOP_CAMERA_STREAM,
    STATIC_REGISTERED,
    STATIC_URL_PATH,
)
from .coordinator import PetkitDeviceCoordinator, PetkitScheduleConflict
from .media_client import PetkitMediaError, PetkitMediaSidecarClient
from .media_http import (
    PetkitMediaProxyView,
    async_register_media_session,
    async_remove_media_session,
)
from .protocol import validate_schedule

LOGGER = logging.getLogger(__name__)
FRONTEND_DIR = Path(__file__).parent / "frontend"
MEDIA_VIEW_REGISTERED = "media_view_registered"


@dataclass(slots=True)
class PetkitRuntimeData:
    """Objects shared by all entities in one account entry."""

    client: PetkitClient
    coordinators: dict[str, PetkitDeviceCoordinator]
    session: ClientSession
    media: PetkitMediaSidecarClient | None
    media_sessions: set[str]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register integration services once."""
    if not hass.data.get(MEDIA_VIEW_REGISTERED):
        hass.http.register_view(PetkitMediaProxyView(hass))
        hass.data[MEDIA_VIEW_REGISTERED] = True

    if not hass.services.has_service(DOMAIN, SERVICE_FEED_NOW):
        hass.services.async_register(
            DOMAIN,
            SERVICE_FEED_NOW,
            partial(_async_feed_now, hass),
            schema=vol.Schema(
                {
                    vol.Optional(ATTR_DEVICE_ID): str,
                    vol.Required(
                        ATTR_AMOUNT1, default=0
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=10)),
                    vol.Required(
                        ATTR_AMOUNT2, default=0
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=10)),
                }
            ),
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SAVE_SCHEDULE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SAVE_SCHEDULE,
            partial(_async_save_schedule, hass),
            schema=vol.Schema(
                {
                    vol.Optional(ATTR_DEVICE_ID): str,
                    vol.Required(ATTR_DAYS): list,
                    vol.Required(ATTR_EXPECTED_HASH): str,
                }
            ),
        )
    if not hass.services.has_service(DOMAIN, SERVICE_GET_CAMERA_ACCESS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_CAMERA_ACCESS,
            partial(_async_get_camera_access, hass),
            schema=vol.Schema(
                {
                    vol.Optional(ATTR_DEVICE_ID): str,
                    vol.Required(
                        ATTR_QUALITY, default=2
                    ): vol.All(vol.Coerce(int), vol.In([1, 2])),
                }
            ),
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_START_CAMERA_STREAM):
        hass.services.async_register(
            DOMAIN,
            SERVICE_START_CAMERA_STREAM,
            partial(_async_start_camera_stream, hass),
            schema=vol.Schema(
                {
                    vol.Optional(ATTR_DEVICE_ID): str,
                    vol.Required(
                        ATTR_QUALITY, default=2
                    ): vol.All(vol.Coerce(int), vol.In([1, 2])),
                }
            ),
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_STOP_CAMERA_STREAM):
        hass.services.async_register(
            DOMAIN,
            SERVICE_STOP_CAMERA_STREAM,
            partial(_async_stop_camera_stream, hass),
            schema=vol.Schema({vol.Required(ATTR_SESSION_ID): str}),
        )
    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_SCHEDULE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_REFRESH_SCHEDULE,
            partial(_async_refresh_schedule, hass),
            schema=vol.Schema({vol.Optional(ATTR_DEVICE_ID): str}),
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one PETKIT account and all of its D4sh devices."""
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    if not hass.data.get(STATIC_REGISTERED):
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    STATIC_URL_PATH,
                    str(FRONTEND_DIR),
                    cache_headers=False,
                )
            ]
        )
        hass.data[STATIC_REGISTERED] = True

    session = async_get_clientsession(hass)
    media: PetkitMediaSidecarClient | None = None
    sidecar_url = str(entry.options.get(CONF_MEDIA_SIDECAR_URL, "")).strip()
    sidecar_token = str(entry.options.get(CONF_MEDIA_SIDECAR_TOKEN, "")).strip()
    if sidecar_url and sidecar_token:
        candidate = PetkitMediaSidecarClient(
            session,
            base_url=sidecar_url,
            token=sidecar_token,
        )
        try:
            await candidate.async_health()
            media = candidate
        except PetkitMediaError as err:
            LOGGER.warning("PETKIT media sidecar is unavailable: %s", err)
    client = PetkitClient(
        session,
        account=entry.data[CONF_ACCOUNT],
        password_hash=entry.data[CONF_PASSWORD_HASH],
    )
    try:
        await client.async_login()
        devices = await client.async_discover_devices()
        if not devices:
            raise ConfigEntryNotReady("No PETKIT D4sh devices found")
    except PetkitVerificationRequiredError as err:
        raise ConfigEntryNotReady(
            "PETKIT account requires interactive verification"
        ) from err
    except PetkitError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinators: dict[str, PetkitDeviceCoordinator] = {}
    try:
        for device in devices:
            coordinator = PetkitDeviceCoordinator(
                hass,
                client,
                device,
                config_entry=entry,
                media=media,
            )
            coordinators[device.device_id] = coordinator
            await coordinator.async_initialize()
            await coordinator.async_config_entry_first_refresh()
            await coordinator.async_start_eating_poll()
    except Exception:
        for coordinator in coordinators.values():
            await coordinator.async_shutdown()
        await client.async_close()
        raise

    entry.runtime_data = PetkitRuntimeData(
        client,
        coordinators,
        session,
        media,
        set(),
    )
    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform(platform) for platform in PLATFORMS]
    )
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Reload when sidecar options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload entities and release this entry's HTTP session."""
    unloaded = await hass.config_entries.async_unload_platforms(
        entry, [Platform(platform) for platform in PLATFORMS]
    )
    if unloaded:
        runtime: PetkitRuntimeData = entry.runtime_data
        if runtime.media is not None:
            for session_id in tuple(runtime.media_sessions):
                try:
                    await runtime.media.async_delete_session(session_id)
                except PetkitMediaError:
                    pass
                async_remove_media_session(hass, session_id)
            runtime.media_sessions.clear()
        for coordinator in runtime.coordinators.values():
            await coordinator.async_shutdown()
        await runtime.client.async_close()
    return unloaded


async def _async_feed_now(hass: HomeAssistant, call: ServiceCall) -> None:
    amount1 = int(call.data[ATTR_AMOUNT1])
    amount2 = int(call.data[ATTR_AMOUNT2])
    if amount1 + amount2 <= 0:
        raise HomeAssistantError("At least one bin amount must be greater than zero")
    coordinator = _resolve_coordinator(hass, call.data.get(ATTR_DEVICE_ID))
    try:
        await coordinator.async_feed_now(amount1, amount2)
    except PetkitError as err:
        raise HomeAssistantError(f"PETKIT feeding failed: {err}") from err


async def _async_save_schedule(hass: HomeAssistant, call: ServiceCall) -> None:
    coordinator = _resolve_coordinator(hass, call.data.get(ATTR_DEVICE_ID))
    try:
        days = validate_schedule(call.data[ATTR_DAYS])
        await coordinator.async_save_schedule(
            days, str(call.data[ATTR_EXPECTED_HASH])
        )
    except PetkitScheduleConflict as err:
        raise HomeAssistantError(f"schedule_changed: {err}") from err
    except (PetkitError, ValueError) as err:
        raise HomeAssistantError(f"PETKIT schedule save failed: {err}") from err


async def _async_refresh_schedule(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    coordinator = _resolve_coordinator(hass, call.data.get(ATTR_DEVICE_ID))
    try:
        await coordinator.async_refresh_plan()
        await coordinator.async_request_refresh()
    except PetkitError as err:
        raise HomeAssistantError(f"PETKIT schedule refresh failed: {err}") from err


async def _async_get_camera_access(
    hass: HomeAssistant, call: ServiceCall
) -> dict[str, Any]:
    coordinator = _resolve_coordinator(hass, call.data.get(ATTR_DEVICE_ID))
    try:
        return await coordinator.async_get_camera_access(
            int(call.data[ATTR_QUALITY])
        )
    except PetkitError as err:
        raise HomeAssistantError(f"PETKIT camera access failed: {err}") from err


async def _async_start_camera_stream(
    hass: HomeAssistant, call: ServiceCall
) -> dict[str, Any]:
    runtime, coordinator = _resolve_runtime(
        hass, call.data.get(ATTR_DEVICE_ID)
    )
    media = runtime.media
    if media is None:
        raise HomeAssistantError("PETKIT native media sidecar is not configured")
    try:
        access = await coordinator.async_get_camera_access(
            int(call.data[ATTR_QUALITY])
        )
        session = await media.async_create_session(access)
    except (PetkitError, PetkitMediaError) as err:
        raise HomeAssistantError(f"PETKIT camera stream failed: {err}") from err

    session_id = str(session["session_id"])
    runtime.media_sessions.add(session_id)
    async_register_media_session(hass, session_id, media)
    return {
        "session_id": session_id,
        "video_playlist_url": f"/api/petkit/media/{session_id}/video.m3u8",
        "audio_playlist_url": f"/api/petkit/media/{session_id}/audio.m3u8",
        "expires_in": int(session.get("expires_in") or 900),
        "app_id": access["app_id"],
        "rtm_token": access["rtm_token"],
        "app_rtm_user_id": access["app_rtm_user_id"],
        "device_rtm_user_id": access["device_rtm_user_id"],
        "quality": access["quality"],
    }


async def _async_stop_camera_stream(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    session_id = str(call.data[ATTR_SESSION_ID])
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        if not isinstance(runtime, PetkitRuntimeData):
            continue
        if session_id not in runtime.media_sessions:
            continue
        if runtime.media is not None:
            try:
                await runtime.media.async_delete_session(session_id)
            finally:
                runtime.media_sessions.discard(session_id)
                async_remove_media_session(hass, session_id)
        return


def _resolve_coordinator(
    hass: HomeAssistant, device_id: str | None
) -> PetkitDeviceCoordinator:
    return _resolve_runtime(hass, device_id)[1]


def _resolve_runtime(
    hass: HomeAssistant, device_id: str | None
) -> tuple[PetkitRuntimeData, PetkitDeviceCoordinator]:
    matches: list[PetkitDeviceCoordinator] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        if not isinstance(runtime, PetkitRuntimeData):
            continue
        if device_id:
            coordinator = runtime.coordinators.get(str(device_id))
            if coordinator:
                matches.append(coordinator)
        else:
            matches.extend(runtime.coordinators.values())
    if not matches:
        raise HomeAssistantError("PETKIT D4sh device is not loaded")
    if len(matches) > 1:
        raise HomeAssistantError(
            "Multiple PETKIT devices are loaded; provide device_id"
        )
    coordinator = matches[0]
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        if (
            isinstance(runtime, PetkitRuntimeData)
            and runtime.coordinators.get(coordinator.device_id) is coordinator
        ):
            return runtime, coordinator
    raise HomeAssistantError("PETKIT runtime data is unavailable")
