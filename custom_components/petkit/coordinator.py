"""Per-device polling and control coordinator for D4sh feeders."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .client import DeviceInfo, PetkitAuthError, PetkitClient, PetkitError
from .const import EATING_POLL_INTERVAL, PLAN_REFRESH_INTERVAL, SCAN_INTERVAL
from .media_client import PetkitMediaSidecarClient
from .protocol import (
    device_day,
    device_timezone,
    merge_device_record_responses,
    normalize_eating_activity,
    normalize_snapshot,
    schedule_hash,
    validate_schedule,
)

LOGGER = logging.getLogger(__name__)


class PetkitScheduleConflict(PetkitError):
    """The cloud schedule changed after the card loaded it."""


class PetkitDeviceCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Own state, schedule, and actions for one D4sh device."""

    def __init__(
        self,
        hass,
        client: PetkitClient,
        device: DeviceInfo,
        config_entry: ConfigEntry | None = None,
        media: PetkitMediaSidecarClient | None = None,
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=f"PETKIT {device.name}",
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.device = device
        self.media = media
        self._plan: dict[str, Any] = {}
        self._plan_fetched_at = 0.0
        self._eating_activity: dict[str, Any] = {}
        self._eating_poll_task: asyncio.Task[None] | None = None

    @property
    def device_id(self) -> str:
        return self.device.device_id

    async def async_initialize(self) -> None:
        """Load stable device metadata and the initial schedule."""
        try:
            detail, _ = await self.client.async_fetch_device_detail(
                self.device_id
            )
            self.device = detail
            self._plan = await self.client.async_fetch_plan(self.device_id)
            self._plan_fetched_at = time.monotonic()
        except PetkitAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except PetkitError as err:
            LOGGER.debug("Initial detail fetch failed for %s: %s", self.device_id, err)

    async def async_start_eating_poll(self) -> None:
        """Start the low-latency identified-eating event poll."""
        if self._eating_poll_task is not None:
            return
        await self._async_refresh_eating_activity()
        self._eating_poll_task = asyncio.create_task(
            self._eating_poll_loop(),
            name=f"petkit_eating_{self.device_id}",
        )

    async def async_shutdown(self) -> None:
        """Stop the identified-eating poll before coordinator shutdown."""
        if self._eating_poll_task is not None:
            self._eating_poll_task.cancel()
            try:
                await self._eating_poll_task
            except asyncio.CancelledError:
                pass
            self._eating_poll_task = None
        await super().async_shutdown()

    async def _eating_poll_loop(self) -> None:
        delay = EATING_POLL_INTERVAL.total_seconds()
        while True:
            await asyncio.sleep(delay)
            refreshed = await self._async_refresh_eating_activity()
            delay = (
                EATING_POLL_INTERVAL.total_seconds()
                if refreshed
                else min(delay * 2, 30)
            )

    async def _async_refresh_eating_activity(self) -> bool:
        now = datetime.now(UTC)
        device = self._device_payload()
        local_now = now.astimezone(device_timezone(device))
        try:
            records = await self.client.async_fetch_device_record(
                self.device_id,
                day=local_now.strftime("%Y%m%d"),
            )
        except PetkitAuthError as err:
            LOGGER.debug("Eating poll authentication failed: %s", err)
            return False
        except PetkitError as err:
            LOGGER.debug("Eating poll failed for %s: %s", self.device_id, err)
            return False

        if local_now.hour < 2:
            previous_day = (local_now - timedelta(days=1)).strftime("%Y%m%d")
            try:
                previous_records = await self.client.async_fetch_device_record(
                    self.device_id,
                    day=previous_day,
                )
            except PetkitError as err:
                LOGGER.debug(
                    "Previous-day eating poll failed for %s: %s",
                    self.device_id,
                    err,
                )
            else:
                records = merge_device_record_responses(
                    records, previous_records
                )

        self._eating_activity = normalize_eating_activity(
            records,
            device=device,
        )
        if self.data is not None:
            data = dict(self.data)
            data.update(self._eating_activity)
            self.data = data
            self.async_update_listeners()
        return True

    async def _async_update_data(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        device = self._device_payload()
        try:
            state, home = await self.client.async_fetch_snapshot(
                self.device_id,
                day=device_day(device, now),
            )
        except PetkitAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except PetkitError as err:
            raise UpdateFailed(str(err)) from err

        if (
            not self._plan
            or time.monotonic() - self._plan_fetched_at
            >= PLAN_REFRESH_INTERVAL.total_seconds()
        ):
            try:
                self._plan = await self.client.async_fetch_plan(self.device_id)
                self._plan_fetched_at = time.monotonic()
            except PetkitError as err:
                if not self._plan:
                    raise UpdateFailed(str(err)) from err
                LOGGER.debug(
                    "Keeping cached schedule for %s: %s", self.device_id, err
                )

        data = normalize_snapshot(
            device=device,
            state_response=state,
            home_response=home,
            plan=self._plan,
            now=now,
        )
        data.update(self._eating_activity)
        return data

    async def async_feed_now(self, amount1: int, amount2: int) -> Any:
        """Request one immediate feeding. This method never retries."""
        await self.client.async_feed_now(
            self.device_id,
            amount1,
            amount2,
            day=device_day(self._device_payload()),
        )
        await self.async_request_refresh()

    async def async_refresh_plan(self) -> dict[str, Any]:
        self._plan = await self.client.async_fetch_plan(self.device_id)
        self._plan_fetched_at = time.monotonic()
        return self._plan

    async def async_save_schedule(
        self,
        days: list[dict[str, Any]],
        expected_hash: str,
    ) -> None:
        """Save after an optimistic-concurrency check."""
        latest = await self.async_refresh_plan()
        if schedule_hash(latest) != expected_hash:
            raise PetkitScheduleConflict(
                "The feeding schedule changed in another client"
            )
        normalized = validate_schedule(days)
        await self.client.async_save_schedule(self.device_id, normalized)
        self._plan = {"feedDailyList": normalized}
        self._plan_fetched_at = time.monotonic()
        await self.async_request_refresh()

    async def async_set_manual_lock(self, enabled: bool) -> None:
        await self.client.async_update_settings(
            self.device_id, {"manualLock": 1 if enabled else 0}
        )
        data = dict(self.data or {})
        data["manual_lock"] = enabled
        self.async_set_updated_data(data)
        await self.async_request_refresh()

    async def async_get_camera_access(self, quality: int) -> dict[str, Any]:
        return await self.client.async_get_camera_access(self.device_id, quality)

    def _device_payload(self) -> dict[str, Any]:
        return {
            "id": self.device.device_id,
            "name": self.device.name,
            "modelCode": self.device.model_code,
            "p2pType": self.device.p2p_type,
            "firmware": self.device.firmware,
            "sn": self.device.serial_number,
            "hardware": self.device.hardware,
            "timezone": self.device.timezone,
            "locale": self.device.locale,
        }
