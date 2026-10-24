"""Asynchronous PETKIT account and D4sh API client."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import (
    ANDROID_CLIENT_BRAND,
    ANDROID_CLIENT_MODEL,
    ANDROID_CLIENT_OS_VERSION,
    APP_VERSION,
    AUTH_ERROR_CODES,
    BASE_URL,
    CAMERA_APP_ID,
    D4SH_MODEL,
    D4SH_TYPE_ID,
    SESSION_REFRESH_MARGIN,
    VERIFICATION_REQUIRED_CODES,
)
from .protocol import client_json, encode_account

LOGGER = logging.getLogger(__name__)


class PetkitError(Exception):
    """Base integration error."""


class PetkitAuthError(PetkitError):
    """Authentication or session validation failed."""


class PetkitVerificationRequiredError(PetkitAuthError):
    """The account requires SMS or another interactive verification."""


class PetkitApiError(PetkitError):
    """The vendor API returned a structured error."""

    def __init__(self, code: int | None, message: str, status: int = 200) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(slots=True, frozen=True)
class DeviceInfo:
    """Facts required to expose one D4sh device."""

    device_id: str
    name: str
    model: str = D4SH_MODEL
    model_code: int | None = None
    p2p_type: int | None = None
    firmware: str | None = None
    serial_number: str | None = None
    hardware: int | None = None
    timezone: float | None = None
    locale: str | None = None
    type_code: int = D4SH_TYPE_ID

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> DeviceInfo:
        device_id = str(raw.get("id") or raw.get("deviceId") or "")
        if not device_id:
            raise PetkitError("D4sh device response did not contain an id")
        return cls(
            device_id=device_id,
            name=str(raw.get("name") or f"PETKIT {device_id}"),
            model=str(raw.get("deviceType") or D4SH_MODEL),
            model_code=_optional_int(raw.get("modelCode")),
            p2p_type=_optional_int(raw.get("p2pType")),
            firmware=_optional_text(raw.get("firmware")),
            serial_number=_optional_text(raw.get("sn")),
            hardware=_optional_int(raw.get("hardware")),
            timezone=_optional_float(raw.get("timezone")),
            locale=_optional_text(raw.get("locale")),
        )

    def with_detail(self, raw: dict[str, Any]) -> DeviceInfo:
        return replace(
            self,
            name=str(raw.get("name") or self.name),
            model_code=_optional_int(raw.get("modelCode")) or self.model_code,
            p2p_type=_optional_int(raw.get("p2pType")) or self.p2p_type,
            firmware=_optional_text(raw.get("firmware")) or self.firmware,
            serial_number=_optional_text(raw.get("sn")) or self.serial_number,
            hardware=_optional_int(raw.get("hardware")) or self.hardware,
            timezone=(
                _optional_float(raw.get("timezone"))
                if raw.get("timezone") is not None
                else self.timezone
            ),
            locale=_optional_text(raw.get("locale")) or self.locale,
        )


class PetkitClient:
    """Maintain one cloud session for all D4sh devices on an account."""

    def __init__(
        self,
        session: ClientSession,
        *,
        account: str,
        password_hash: str,
    ) -> None:
        self._session = session
        self._account = account
        self._password_hash = password_hash.lower()
        self._session_id: str | None = None
        self._user_id: str | None = None
        self._session_expires_at: float = 0
        self._session_lock = asyncio.Lock()
        self._client_info = client_json(
            timezone_offset=_local_offset_hours(),
            timezone_id=_local_timezone_name(),
            version=APP_VERSION,
            model=ANDROID_CLIENT_MODEL,
            brand=ANDROID_CLIENT_BRAND,
            os_version=ANDROID_CLIENT_OS_VERSION,
        )

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def user_id(self) -> str | None:
        """Return the numeric PETKIT account ID used as the Agora RTC UID."""
        return self._user_id

    async def async_login(self) -> None:
        """Authenticate with the account and replace the current session."""
        async with self._session_lock:
            await self._async_login_locked()

    async def _async_login_locked(self) -> None:
        payload = await self._async_request_raw(
            "POST",
            "user/login",
            auth=False,
            params={
                "username": encode_account(self._account.strip()),
                "password": self._password_hash,
                "client": self._client_info,
                "encrypt": "1",
                "oldVersion": "",
            },
        )
        session = payload.get("session") if isinstance(payload, dict) else None
        if not isinstance(session, dict) or not session.get("id"):
            raise PetkitAuthError("Login response did not contain a session")
        user = payload.get("user")
        if isinstance(user, dict) and user.get("id") is not None:
            self._user_id = str(user["id"])
        self._session_id = str(session["id"])
        self._session_expires_at = _expiry_timestamp(session.get("expires"))

    async def _async_refresh_session_locked(self) -> bool:
        if not self._session_id:
            return False
        try:
            payload = await self._async_request_raw(
                "POST",
                "user/refreshsession",
                params={
                    "client": self._client_info,
                    "oldVersion": APP_VERSION,
                },
                form=True,
                auth=True,
            )
        except PetkitAuthError:
            self._session_id = None
            self._session_expires_at = 0
            return False

        session = payload.get("session") if isinstance(payload, dict) else None
        if isinstance(session, dict) and session.get("id"):
            self._session_id = str(session["id"])
            expires_in = _optional_int(session.get("expiresIn"))
            self._session_expires_at = (
                time.time() + expires_in
                if expires_in is not None
                else time.time() + 12 * 3600
            )
            return True
        return False

    async def _async_ensure_session(self) -> None:
        if (
            self._session_id
            and time.time() < self._session_expires_at - SESSION_REFRESH_MARGIN
        ):
            return
        async with self._session_lock:
            if (
                self._session_id
                and time.time() < self._session_expires_at - SESSION_REFRESH_MARGIN
            ):
                return
            if self._session_id:
                refreshed = await self._async_refresh_session_locked()
                if refreshed:
                    return
            await self._async_login_locked()

    async def async_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        form: bool = False,
        retry_auth: bool = True,
    ) -> Any:
        """Execute an authenticated request with safe reauthentication."""
        await self._async_ensure_session()
        try:
            return await self._async_request_raw(
                method,
                path,
                params=params,
                form=form,
                auth=True,
            )
        except PetkitAuthError:
            if not retry_auth:
                raise
            async with self._session_lock:
                self._session_id = None
                await self._async_login_locked()
            return await self._async_request_raw(
                method,
                path,
                params=params,
                form=form,
                auth=True,
            )

    async def _async_request_raw(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        form: bool = False,
        auth: bool,
    ) -> Any:
        headers = self._headers(include_session=auth)
        request_kwargs: dict[str, Any] = {
            "headers": headers,
            "timeout": ClientTimeout(total=25, connect=10),
        }
        if form:
            request_kwargs["data"] = params
        else:
            request_kwargs["params"] = params

        try:
            async with self._session.request(
                method, f"{BASE_URL}{path}", **request_kwargs
            ) as response:
                status = response.status
                text = await response.text()
        except TimeoutError:
            raise PetkitError("PETKIT cloud request timed out") from None
        except ClientError as err:
            LOGGER.debug(
                "PETKIT cloud request failed with %s", type(err).__name__
            )
            raise PetkitError(
                f"PETKIT cloud request failed ({type(err).__name__})"
            ) from None

        try:
            payload = json.loads(text) if text else {}
        except ValueError as err:
            raise PetkitApiError(
                None, f"PETKIT returned invalid JSON (HTTP {status})", status
            ) from err

        error = payload.get("error") if isinstance(payload, dict) else None
        code = _optional_int(error.get("code")) if isinstance(error, dict) else None
        if code in VERIFICATION_REQUIRED_CODES:
            raise PetkitVerificationRequiredError(
                str(error.get("msg") or "Account requires interactive verification")
            )
        if code in AUTH_ERROR_CODES or status in (401, 403):
            self._session_id = None
            message = (
                str(error.get("msg"))
                if isinstance(error, dict)
                else f"HTTP {status}"
            )
            raise PetkitAuthError(message)
        if status >= 400:
            message = (
                str(error.get("msg"))
                if isinstance(error, dict)
                else f"PETKIT HTTP error {status}"
            )
            raise PetkitApiError(code, message, status)
        if isinstance(error, dict):
            raise PetkitApiError(
                code, str(error.get("msg") or error.get("message") or "PETKIT error")
            )
        if isinstance(payload, dict) and "result" in payload:
            return payload["result"]
        return payload

    def _headers(self, *, include_session: bool) -> dict[str, str]:
        now = datetime.now().astimezone()
        headers = {
            "X-Api-Version": APP_VERSION,
            "X-Locale": "en_US",
            "X-Timezone": str(round(now.utcoffset().total_seconds() / 3600, 1)),
            "X-TimezoneId": str(now.tzinfo or "Etc/UTC"),
            "X-Img-Version": "1",
            "X-Client": f"Android(13;{ANDROID_CLIENT_MODEL})",
            "Accept": "application/json",
            "User-Agent": "okhttp/3.12.13",
        }
        if include_session and self._session_id:
            headers["X-Session"] = self._session_id
        return headers

    async def async_discover_devices(self) -> list[DeviceInfo]:
        result = await self.async_request("POST", "d4sh/owndevices")
        if isinstance(result, dict):
            result = result.get("devices") or []
        if not isinstance(result, list):
            raise PetkitError("Unexpected D4sh device list response")
        devices = [
            DeviceInfo.from_api(raw)
            for raw in result
            if isinstance(raw, dict)
            and str(raw.get("deviceType") or D4SH_MODEL) == D4SH_MODEL
        ]
        return devices

    async def async_fetch_device_detail(self, device_id: str) -> tuple[DeviceInfo, dict]:
        result = await self.async_request(
            "POST", "d4sh/device_detail", params={"id": device_id}
        )
        if not isinstance(result, dict):
            raise PetkitError("Unexpected D4sh device detail response")
        return DeviceInfo.from_api(result), result

    async def async_fetch_plan(self, device_id: str) -> dict[str, Any]:
        result = await self.async_request(
            "POST", "d4sh/feed", params={"deviceId": device_id}
        )
        return result if isinstance(result, dict) else {}

    async def async_fetch_device_record(
        self, device_id: str, *, day: str
    ) -> dict[str, Any]:
        """Fetch identified pet activity for one device-local day."""
        result = await self.async_request(
            "POST",
            "d4sh/getDeviceRecord",
            params={"deviceId": device_id, "days": day},
        )
        return result if isinstance(result, dict) else {}

    async def async_fetch_snapshot(
        self, device_id: str, *, day: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        state, home = await asyncio.gather(
            self.async_request(
                "POST", "d4sh/devicestate", params={"id": device_id}
            ),
            self.async_request(
                "POST",
                "d4sh/refreshHomeV2",
                params={"deviceId": device_id, "day": day},
            ),
        )
        if not isinstance(state, dict):
            state = {}
        if not isinstance(home, dict):
            home = {}
        devices = home.get("devices")
        if isinstance(devices, list):
            matching = next(
                (
                    item
                    for item in devices
                    if isinstance(item, dict)
                    and str((item.get("data") or {}).get("id")) == device_id
                ),
                devices[0] if devices and isinstance(devices[0], dict) else {},
            )
        else:
            matching = {}
        home_device = matching.get("data") if isinstance(matching, dict) else {}
        return state, home_device if isinstance(home_device, dict) else {}

    async def async_feed_now(
        self,
        device_id: str,
        amount1: int,
        amount2: int,
        *,
        day: str,
    ) -> Any:
        return await self.async_request(
            "POST",
            "d4sh/saveDailyFeed",
            params={
                "deviceId": device_id,
                "day": day,
                "time": "-1",
                "amount1": str(amount1),
                "amount2": str(amount2),
            },
            form=True,
            retry_auth=False,
        )

    async def async_save_schedule(
        self, device_id: str, days: list[dict[str, Any]]
    ) -> Any:
        return await self.async_request(
            "POST",
            "d4sh/saveFeed",
            params={
                "deviceId": device_id,
                "feedDailyList": json.dumps(
                    days, ensure_ascii=False, separators=(",", ":")
                ),
            },
            form=True,
            retry_auth=False,
        )

    async def async_update_settings(
        self, device_id: str, values: dict[str, Any]
    ) -> Any:
        return await self.async_request(
            "POST",
            "d4sh/updateSettings",
            params={
                "id": device_id,
                "kv": json.dumps(values, separators=(",", ":")),
            },
            retry_auth=False,
        )

    async def async_get_camera_access(
        self, device_id: str, quality: int
    ) -> dict[str, Any]:
        result = await self.async_request(
            "GET",
            "d4sh/start/live",
            params={"deviceId": device_id, "definition": str(quality)},
        )
        if not isinstance(result, dict) or not result.get("channelId"):
            raise PetkitError("Camera session response was incomplete")
        if not self._user_id:
            raise PetkitError("PETKIT account user ID is unavailable")
        return {
            "app_id": CAMERA_APP_ID,
            "channel_id": result.get("channelId"),
            "rtc_token": result.get("rtcToken"),
            "rtc_uid": self._user_id,
            "rtm_token": result.get("rtmToken"),
            "app_rtm_user_id": result.get("appRtmUserId"),
            "device_rtm_user_id": result.get("devRtmUserId"),
            "media_encryption": result.get("mediaEncryption"),
            "quality": quality,
            "expires_in": 900,
        }

    async def async_close(self) -> None:
        self._session_id = None
        self._user_id = None


def _expiry_timestamp(value: Any) -> float:
    if value is None:
        return time.time() + 12 * 3600
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric / 1000 if numeric > 10_000_000_000 else numeric
    text = str(value).strip()
    try:
        numeric = float(text)
        return numeric / 1000 if numeric > 10_000_000_000 else numeric
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()
    except ValueError:
        return time.time() + 12 * 3600


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _local_offset_hours() -> float:
    now = datetime.now().astimezone()
    offset = now.utcoffset()
    return round(offset.total_seconds() / 3600, 1) if offset else 0.0


def _local_timezone_name() -> str:
    return str(datetime.now().astimezone().tzinfo or "Etc/UTC")
