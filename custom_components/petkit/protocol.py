"""Pure D4sh state, schedule, and authentication helpers."""

from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, time, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from .const import FOOD_STATUS_MAP


def encode_account(account: str) -> str:
    """Match the vendor's URL-safe intermediate Base64 representation."""
    encoded = base64.b64encode(account.encode()).decode()
    return encoded.replace("+", "%2B").replace("/", "%2F")


def password_digest(password: str) -> str:
    """Return the lowercase MD5 value expected by the PETKIT API."""
    return hashlib.md5(password.encode()).hexdigest()


def client_json(
    *,
    timezone_offset: float,
    timezone_id: str,
    locale: str = "en_US",
    version: str,
    model: str = "sdk_gphone64_arm64",
    brand: str = "google",
    os_version: str = "13",
) -> str:
    """Build the account client descriptor used by login and refresh."""
    return json.dumps(
        {
            "platform": "android",
            "name": model,
            "osVersion": os_version,
            "version": version,
            "source": "app.petkit-android",
            "phoneBrand": brand,
            "locale": locale,
            "timezone": timezone_offset,
            "timezoneId": timezone_id,
        },
        separators=(",", ":"),
    )


def device_timezone(device: dict[str, Any]) -> tzinfo:
    """Resolve the device timezone without assuming the HA host timezone."""
    locale = device.get("locale")
    if isinstance(locale, str) and locale:
        try:
            return ZoneInfo(locale)
        except (KeyError, ValueError):
            pass

    offset = device.get("timezone")
    try:
        return timezone(timedelta(hours=float(offset)))
    except (TypeError, ValueError):
        return UTC


def device_day(device: dict[str, Any], now: datetime | None = None) -> str:
    """Return YYYYMMDD in the device timezone for date-scoped D4sh APIs."""
    instant = now or datetime.now(UTC)
    return instant.astimezone(device_timezone(device)).strftime("%Y%m%d")


def normalize_schedule(plan: Any) -> list[dict[str, Any]]:
    """Return a stable copy of the seven-day plan list."""
    if not isinstance(plan, dict):
        return []
    days = plan.get("feedDailyList")
    if not isinstance(days, list):
        return []
    normalized = [deepcopy(day) for day in days if isinstance(day, dict)]
    normalized.sort(key=lambda day: int(day.get("repeats") or 0))
    return normalized


def schedule_hash(plan: Any) -> str:
    """Hash only schedule content so unrelated device state does not conflict."""
    payload = normalize_schedule(plan)
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def validate_schedule(days: Any) -> list[dict[str, Any]]:
    """Validate and normalize a full seven-day schedule."""
    if not isinstance(days, list) or len(days) != 7:
        raise ValueError("schedule_requires_seven_days")

    normalized: list[dict[str, Any]] = []
    repeats_seen: set[int] = set()
    for day in days:
        if not isinstance(day, dict):
            raise ValueError("invalid_schedule_day")  # noqa: TRY004
        repeats = int(day.get("repeats", 0))
        if repeats < 1 or repeats > 7 or repeats in repeats_seen:
            raise ValueError("invalid_schedule_day")
        repeats_seen.add(repeats)

        suspended = int(day.get("suspended", 0))
        if suspended not in (0, 1):
            raise ValueError("invalid_suspended_state")

        items = day.get("items")
        if not isinstance(items, list):
            raise ValueError("invalid_schedule_items")  # noqa: TRY004

        normalized_items: list[dict[str, Any]] = []
        times_seen: set[int] = set()
        for raw_item in items:
            if not isinstance(raw_item, dict):
                raise ValueError("invalid_schedule_item")  # noqa: TRY004
            item = deepcopy(raw_item)
            item_time = int(item.get("time", -1))
            amount1 = int(item.get("amount1", 0))
            amount2 = int(item.get("amount2", 0))
            if not 0 <= item_time <= 86399 or item_time % 60:
                raise ValueError("invalid_feed_time")
            if not 0 <= amount1 <= 10 or not 0 <= amount2 <= 10:
                raise ValueError("invalid_feed_amount")
            if amount1 + amount2 <= 0:
                raise ValueError("empty_feed_item")
            if item_time in times_seen:
                raise ValueError("duplicate_feed_time")
            times_seen.add(item_time)
            item["time"] = item_time
            item["amount1"] = amount1
            item["amount2"] = amount2
            item["id"] = str(item_time)
            name = item.get("name")
            if name is not None:
                item["name"] = str(name)[:64]
            normalized_items.append(item)

        normalized_items.sort(key=lambda item: int(item["time"]))
        day_copy = deepcopy(day)
        day_copy["repeats"] = repeats
        day_copy["suspended"] = suspended
        day_copy["items"] = normalized_items
        normalized.append(day_copy)

    normalized.sort(key=lambda day: int(day["repeats"]))
    return normalized


def current_day_plan(
    schedule: list[dict[str, Any]], now: datetime
) -> dict[str, Any] | None:
    """Return the schedule row for the device-local weekday."""
    weekday = now.isoweekday()
    return next(
        (
            day
            for day in schedule
            if int(day.get("repeats") or 0) == weekday
        ),
        None,
    )


def next_feed_datetime(
    schedule: list[dict[str, Any]], now: datetime
) -> datetime | None:
    """Calculate the next scheduled feed in the device timezone."""
    for day_offset in range(8):
        local_day = now + timedelta(days=day_offset)
        day_plan = current_day_plan(schedule, local_day)
        if not day_plan or int(day_plan.get("suspended") or 0) != 0:
            continue
        for item in day_plan.get("items") or []:
            try:
                seconds = int(item.get("time"))
            except (TypeError, ValueError):
                continue
            if not 0 <= seconds <= 86399:
                continue
            candidate = datetime.combine(
                local_day.date(),
                time(hour=seconds // 3600, minute=seconds % 3600 // 60),
                tzinfo=now.tzinfo,
            )
            if day_offset > 0 or candidate > now:
                return candidate
    return None


def food_status(value: Any) -> str | None:
    """Map a D4sh bin status code to a stable HA enum."""
    try:
        return FOOD_STATUS_MAP.get(int(value))
    except (TypeError, ValueError):
        return None


def normalize_snapshot(
    *,
    device: dict[str, Any],
    state_response: dict[str, Any],
    home_response: dict[str, Any],
    plan: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Merge the vendor's state, home, settings, and plan representations."""
    data = home_response if isinstance(home_response, dict) else {}
    if isinstance(data.get("data"), dict):
        data = data["data"]
    status = data.get("status")
    status = status if isinstance(status, dict) else {}
    flat_state = dict(status)
    if isinstance(state_response, dict):
        flat_state.update(
            {key: value for key, value in state_response.items() if value is not None}
        )

    settings = data.get("settings")
    settings = settings if isinstance(settings, dict) else {}
    daily_feed = data.get("dailyFeed")
    daily_feed = daily_feed if isinstance(daily_feed, dict) else {}
    feed_state = flat_state.get("feedState")
    feed_state = feed_state if isinstance(feed_state, dict) else {}
    schedule = normalize_schedule(plan)

    tz = device_timezone(device)
    local_now = (now or datetime.now(UTC)).astimezone(tz)
    today_plan = current_day_plan(schedule, local_now)
    next_feed = next_feed_datetime(schedule, local_now)

    online = data.get("onlineStatus")
    if not isinstance(online, bool):
        online = flat_state.get("onlineStatus")
    if not isinstance(online, bool):
        pim = _optional_int(flat_state.get("pim"))
        online = None if pim is None else pim != 0

    today_feed = _feed_total(feed_state)
    if today_feed is None:
        today_feed = _feed_total(daily_feed)
    today_eat = feed_state.get("eatCount")
    if today_eat is None:
        today_eat = daily_feed.get("eatCount")

    return {
        "device": dict(device),
        "settings": settings,
        "plan": plan if isinstance(plan, dict) else {},
        "schedule": schedule,
        "schedule_hash": schedule_hash(plan),
        "today_plan": today_plan,
        "plan_enabled": (
            int(today_plan.get("suspended") or 0) == 0 if today_plan else None
        ),
        "next_feed": next_feed.isoformat() if next_feed else None,
        "timezone": str(local_now.tzinfo or UTC),
        "online": online,
        "feeding": int(flat_state.get("feeding") or 0) == 1,
        "eating": int(flat_state.get("eating") or 0) == 1,
        "manual_lock": int(settings.get("manualLock") or 0) == 1,
        "camera_enabled": int(settings.get("camera") or 0) == 1,
        "camera_status": int(flat_state.get("cameraStatus") or 0),
        "battery_low": int(flat_state.get("batteryStatus") or 0) == 2,
        "food_bin_1": food_status(flat_state.get("food1")),
        "food_bin_2": food_status(flat_state.get("food2")),
        "desiccant_days": _optional_int(flat_state.get("desiccantLeftDays")),
        "today_feed": _optional_int(today_feed),
        "today_eat": _optional_int(today_eat),
        "error_code": str(flat_state.get("errorCode") or ""),
        "error_message": str(flat_state.get("errorMsg") or ""),
        "error_level": int(flat_state.get("errorLevel") or 0),
        "has_error": bool(flat_state.get("errorCode")),
        "service_status": int(data.get("serviceStatus") or 0),
        "service_expired": int(data.get("serviceStatus") or 0) == 2,
        "raw_state": flat_state,
        "raw_home": data,
    }


def normalize_eating_activity(
    record_response: Any,
    *,
    device: dict[str, Any],
) -> dict[str, Any]:
    """Return the latest identified eating event from a daily record response."""
    if not isinstance(record_response, dict):
        return _empty_eating_activity()

    candidates: dict[str, dict[str, Any]] = {}
    for group in record_response.get("eat") or []:
        if not isinstance(group, dict):
            continue
        for raw in group.get("items") or []:
            if not isinstance(raw, dict):
                continue
            event_id = _optional_text(raw.get("eventId"))
            if not event_id:
                continue
            started_epoch = _event_epoch(raw.get("eatStartTime"))
            if started_epoch is None:
                started_epoch = _event_epoch(raw.get("timestamp"))
            ended_epoch = _event_epoch(raw.get("eatEndTime"))
            if ended_epoch is None and started_epoch is not None:
                ended_epoch = _event_time_of_day(
                    raw.get("endTime"),
                    started_epoch,
                    device_timezone(device),
                )

            started_at = _epoch_datetime(started_epoch)
            ended_at = _epoch_datetime(ended_epoch)
            duration = None
            if started_epoch is not None and ended_epoch is not None:
                duration = max(0, int(round(ended_epoch - started_epoch)))

            candidates[event_id] = {
                "event_id": event_id,
                "pet_id": _optional_text(raw.get("petId")),
                "pet_name": _optional_text(raw.get("petName")),
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_seconds": duration,
                "is_active": ended_at is None,
                "identity_source": (
                    "event"
                    if raw.get("petId") or raw.get("petName")
                    else "unknown"
                ),
                "_started_epoch": started_epoch,
            }

    if not candidates:
        return _empty_eating_activity()

    latest = max(
        candidates.values(),
        key=lambda item: (
            item.get("started_at") or "",
            item["event_id"],
        ),
    )
    pet_presence = _normalize_pet_presence(record_response)
    nearby_pet: dict[str, Any] | None = None
    if not latest.get("pet_id") and not latest.get("pet_name"):
        nearby = [
            item
            for item in pet_presence
            if latest["_started_epoch"] is not None
            and latest["_started_epoch"] - 600
            <= item["timestamp"]
            <= latest["_started_epoch"] + 60
        ]
        nearby_pet_ids = {
            item["pet_id"] for item in nearby if item.get("pet_id")
        }
        if len(nearby_pet_ids) == 1:
            nearby_pet = nearby[-1]

    identified = [
        item
        for item in candidates.values()
        if item.get("pet_id") or item.get("pet_name")
    ]
    last_identified = (
        max(
            identified,
            key=lambda item: (
                item.get("started_at") or "",
                item["event_id"],
            ),
        )
        if identified
        else None
    )
    activity = _empty_eating_activity()
    activity["eating_event"] = latest
    if last_identified is not None:
        activity.update(
            {
                "last_eating_pet_id": last_identified["pet_id"],
                "last_eating_pet_name": last_identified["pet_name"],
                "last_eating_event_id": last_identified["event_id"],
                "last_eating_started_at": last_identified["started_at"],
                "last_eating_ended_at": last_identified["ended_at"],
                "last_eating_duration_seconds": last_identified[
                    "duration_seconds"
                ],
                "last_eating_identity_source": last_identified[
                    "identity_source"
                ],
            }
        )
    if latest["is_active"]:
        latest["nearby_pet_id"] = (
            nearby_pet["pet_id"] if nearby_pet else None
        )
        latest["nearby_pet_name"] = (
            nearby_pet["pet_name"] if nearby_pet else None
        )
        activity.update(
            {
                "eating_pet_id": latest["pet_id"],
                "eating_pet_name": latest["pet_name"],
                "eating_event_id": latest["event_id"],
                "eating_started_at": latest["started_at"],
                "eating_is_active": True,
                "eating_identity_source": latest["identity_source"],
                "eating_nearby_pet_id": (
                    nearby_pet["pet_id"] if nearby_pet else None
                ),
                "eating_nearby_pet_name": (
                    nearby_pet["pet_name"] if nearby_pet else None
                ),
            }
        )
    for item in candidates.values():
        item.pop("_started_epoch", None)
    return activity

def _empty_eating_activity() -> dict[str, Any]:
    return {
        "eating_event": None,
        "eating_pet_id": None,
        "eating_pet_name": None,
        "eating_event_id": None,
        "eating_started_at": None,
        "eating_is_active": False,
        "eating_identity_source": None,
        "eating_nearby_pet_id": None,
        "eating_nearby_pet_name": None,
        "last_eating_pet_id": None,
        "last_eating_pet_name": None,
        "last_eating_event_id": None,
        "last_eating_started_at": None,
        "last_eating_ended_at": None,
        "last_eating_duration_seconds": None,
        "last_eating_identity_source": None,
    }

def merge_device_record_responses(
    *responses: dict[str, Any],
) -> dict[str, Any]:
    """Merge daily record groups while preserving each event item."""
    merged: dict[str, Any] = {}
    for response in responses:
        for group, rows in response.items():
            if not isinstance(rows, list):
                continue
            target = merged.setdefault(group, [])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                items = list(row.get("items") or [])
                target.append({**row, "items": items})
    return merged

def _normalize_pet_presence(
    record_response: dict[str, Any],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for group in record_response.get("pet") or []:
        if not isinstance(group, dict):
            continue
        for raw in group.get("items") or []:
            if not isinstance(raw, dict):
                continue
            timestamp = _event_epoch(raw.get("timestamp"))
            content = raw.get("content")
            content = content if isinstance(content, dict) else {}
            pet_id = _optional_text(raw.get("petId") or content.get("petId"))
            pet_name = _optional_text(raw.get("petName"))
            if timestamp is not None and (pet_id or pet_name):
                events.append(
                    {
                        "timestamp": timestamp,
                        "pet_id": pet_id,
                        "pet_name": pet_name,
                    }
                )
    events.sort(key=lambda item: item["timestamp"])
    return events

def _event_epoch(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric <= 0:
        return None
    return numeric / 1000 if numeric > 10_000_000_000 else numeric

def _epoch_datetime(value: float | None) -> str | None:
    return (
        datetime.fromtimestamp(value, UTC).isoformat()
        if value is not None
        else None
    )

def _event_time_of_day(
    value: Any, started_epoch: float, tz: tzinfo
) -> float | None:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    start = datetime.fromtimestamp(started_epoch, tz)
    candidate = start.replace(
        hour=seconds // 3600,
        minute=seconds % 3600 // 60,
        second=seconds % 60,
        microsecond=0,
    )
    if candidate < start:
        candidate += timedelta(days=1)
    return candidate.timestamp()

def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _feed_total(values: dict[str, Any]) -> int | None:
    combined = _optional_int(values.get("realAmountTotal"))
    if combined is not None:
        return combined
    first = _optional_int(values.get("realAmountTotal1"))
    second = _optional_int(values.get("realAmountTotal2"))
    if first is None and second is None:
        return None
    return (first or 0) + (second or 0)
