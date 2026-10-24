"""Config and reauthentication flows for PETKIT."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import (
    PetkitApiError,
    PetkitAuthError,
    PetkitClient,
    PetkitError,
    PetkitVerificationRequiredError,
)
from .const import (
    CONF_ACCOUNT,
    CONF_MEDIA_SIDECAR_TOKEN,
    CONF_MEDIA_SIDECAR_URL,
    CONF_PASSWORD,
    CONF_PASSWORD_HASH,
    DOMAIN,
    PENDING_MEDIA_SIDECAR,
)
from .media_client import PetkitMediaError, PetkitMediaSidecarClient
from .protocol import password_digest

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACCOUNT): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})
OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_MEDIA_SIDECAR_URL): str,
        vol.Optional(CONF_MEDIA_SIDECAR_TOKEN): str,
    }
)


class PetkitConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a PETKIT account."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return PetkitOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            account = user_input[CONF_ACCOUNT].strip()
            password = user_input[CONF_PASSWORD]
            error = await self._async_validate(account, password)
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(_account_key(account))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=_account_title(account),
                    data={
                        CONF_ACCOUNT: account,
                        CONF_PASSWORD_HASH: password_digest(password),
                    },
                    options=self._media_sidecar_options(),
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, user_input or {}
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            error = await self._async_validate(entry.data[CONF_ACCOUNT], password)
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_PASSWORD_HASH: password_digest(password),
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={
                CONF_ACCOUNT: _mask_account(entry.data[CONF_ACCOUNT])
            },
        )

    async def async_step_hassio(
        self, discovery_info: Any
    ) -> config_entries.ConfigFlowResult:
        """Configure the media sidecar supplied by the HAOS app."""
        raw_config = getattr(discovery_info, "config", {})
        if not isinstance(raw_config, Mapping):
            return self.async_abort(reason="sidecar_invalid_discovery")

        host = str(raw_config.get("host", "")).strip()
        token = str(raw_config.get("token", "")).strip()
        try:
            port = int(raw_config.get("port", 8099))
        except (TypeError, ValueError):
            return self.async_abort(reason="sidecar_invalid_discovery")

        if not host or not token or not 1 <= port <= 65535:
            return self.async_abort(reason="sidecar_invalid_discovery")

        options = {
            CONF_MEDIA_SIDECAR_URL: f"http://{host}:{port}",
            CONF_MEDIA_SIDECAR_TOKEN: token,
        }
        if not await _async_validate_sidecar(
            self.hass, options[CONF_MEDIA_SIDECAR_URL], token
        ):
            return self.async_abort(reason="sidecar_unavailable")

        entries = self.hass.config_entries.async_entries(DOMAIN)
        if entries:
            for entry in entries:
                self.hass.config_entries.async_update_entry(
                    entry,
                    options={**entry.options, **options},
                )
            return self.async_abort(reason="sidecar_configured")

        self.hass.data.setdefault(DOMAIN, {})[PENDING_MEDIA_SIDECAR] = options
        return self.async_abort(reason="account_required")

    async def _async_validate(self, account: str, password: str) -> str | None:
        session = async_get_clientsession(self.hass)
        client = PetkitClient(
            session,
            account=account,
            password_hash=password_digest(password),
        )
        try:
            await client.async_login()
            if not await client.async_discover_devices():
                return "no_devices"
            return None
        except PetkitVerificationRequiredError:
            return "verification_required"
        except PetkitAuthError:
            return "invalid_auth"
        except PetkitApiError as err:
            if err.code in (121, 122):
                return "invalid_auth"
            if err.code == 124:
                return "verification_required"
            return "cannot_connect"
        except (PetkitError, TimeoutError):
            return "cannot_connect"

    def _media_sidecar_options(self) -> dict[str, str]:
        """Return matching discovery data or an existing sidecar configuration."""
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        pending = domain_data.pop(PENDING_MEDIA_SIDECAR, None)
        if _is_complete_sidecar_config(pending):
            return dict(pending)

        for entry in self.hass.config_entries.async_entries(DOMAIN):
            options = {
                CONF_MEDIA_SIDECAR_URL: entry.options.get(CONF_MEDIA_SIDECAR_URL),
                CONF_MEDIA_SIDECAR_TOKEN: entry.options.get(
                    CONF_MEDIA_SIDECAR_TOKEN
                ),
            }
            if _is_complete_sidecar_config(options):
                return dict(options)
        return {}


class PetkitOptionsFlow(config_entries.OptionsFlow):
    """Configure the optional native media sidecar."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = str(user_input.get(CONF_MEDIA_SIDECAR_URL, "")).strip()
            token = str(user_input.get(CONF_MEDIA_SIDECAR_TOKEN, "")).strip()
            if bool(url) != bool(token):
                errors["base"] = "sidecar_incomplete"
            elif not url and not token:
                return self.async_create_entry(title="", data={})
            elif not await _async_validate_sidecar(self.hass, url, token):
                errors["base"] = "sidecar_unavailable"
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_MEDIA_SIDECAR_URL: url,
                        CONF_MEDIA_SIDECAR_TOKEN: token,
                    },
                )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA,
                user_input or self._entry.options,
            ),
            errors=errors,
        )


def _account_key(account: str) -> str:
    return hashlib.sha256(account.casefold().strip().encode()).hexdigest()


def _mask_account(account: str) -> str:
    compact = account.strip()
    digits = "".join(char for char in compact if char.isdigit())
    if len(digits) >= 7:
        return f"••••{digits[-4:]}"
    if "@" in compact:
        local, domain = compact.split("@", 1)
        return f"{local[:2]}••@{domain}"
    return compact[:2] + "••" if len(compact) > 2 else "••"


def _account_title(account: str) -> str:
    return f"PETKIT {_mask_account(account)}"


async def _async_validate_sidecar(
    hass: Any, url: str, token: str
) -> bool:
    """Check that a sidecar URL and token are usable."""
    session = async_get_clientsession(hass)
    try:
        client = PetkitMediaSidecarClient(session, base_url=url, token=token)
        await client.async_health()
    except (PetkitMediaError, ValueError):
        return False
    return True


def _is_complete_sidecar_config(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and bool(value.get(CONF_MEDIA_SIDECAR_URL))
        and bool(value.get(CONF_MEDIA_SIDECAR_TOKEN))
    )
