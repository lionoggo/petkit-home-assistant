"""Redacted diagnostics for PETKIT."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ACCOUNT,
    CONF_MEDIA_SIDECAR_TOKEN,
    CONF_MEDIA_SIDECAR_URL,
    CONF_PASSWORD_HASH,
    DOMAIN,
)

_SAFE_STATE_KEYS = {
    "online",
    "feeding",
    "eating",
    "manual_lock",
    "camera_enabled",
    "camera_status",
    "battery_low",
    "food_bin_1",
    "food_bin_2",
    "desiccant_days",
    "today_feed",
    "today_eat",
    "error_code",
    "error_level",
    "service_status",
    "service_expired",
    "plan_enabled",
    "next_feed",
    "timezone",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return account and device state without credentials or media URLs."""
    runtime = entry.runtime_data
    devices: dict[str, Any] = {}
    for device_id, coordinator in runtime.coordinators.items():
        data = coordinator.data or {}
        devices[_mask_identifier(device_id)] = {
            "name": coordinator.device.name,
            "model": coordinator.device.model,
            "model_code": coordinator.device.model_code,
            "p2p_type": coordinator.device.p2p_type,
            "firmware": coordinator.device.firmware,
            "serial_number": _mask_identifier(
                coordinator.device.serial_number or ""
            ),
            "last_update_success": coordinator.last_update_success,
            "state": {
                key: data.get(key)
                for key in sorted(_SAFE_STATE_KEYS)
                if key in data
            },
            "schedule_day_count": len(data.get("schedule") or []),
            "schedule_hash": data.get("schedule_hash"),
        }
    return {
        "integration": DOMAIN,
        "entry": {
            "title": entry.title,
            "account": _mask_account(entry.data.get(CONF_ACCOUNT, "")),
            "password_hash": "<redacted>" if entry.data.get(CONF_PASSWORD_HASH) else None,
            "media_sidecar_configured": bool(
                entry.options.get(CONF_MEDIA_SIDECAR_URL)
                and entry.options.get(CONF_MEDIA_SIDECAR_TOKEN)
            ),
        },
        "devices": devices,
    }


def _mask_identifier(value: str) -> str:
    text = str(value)
    if len(text) <= 4:
        return "••••"
    return f"{'•' * min(4, len(text) - 4)}{text[-4:]}"


def _mask_account(value: str) -> str:
    text = str(value).strip()
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) >= 7:
        return f"••••{digits[-4:]}"
    if "@" in text:
        local, domain = text.split("@", 1)
        return f"{local[:2]}••@{domain}"
    return text[:2] + "••" if len(text) > 2 else "••"
