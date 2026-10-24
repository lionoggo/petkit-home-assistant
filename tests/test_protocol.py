"""Protocol and schedule normalization tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from custom_components.petkit.protocol import (
    client_json,
    device_day,
    encode_account,
    food_status,
    merge_device_record_responses,
    next_feed_datetime,
    normalize_eating_activity,
    normalize_snapshot,
    password_digest,
    schedule_hash,
    validate_schedule,
)


def sample_schedule() -> list[dict]:
    return [
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
                },
                {
                    "id": "43200",
                    "time": 43200,
                    "amount1": 1,
                    "amount2": 1,
                    "name": "Lunch",
                },
            ],
        }
        for day in range(1, 8)
    ]


def test_account_encoding_matches_android_intermediate_form() -> None:
    assert encode_account("user+name/test=") == "dXNlcituYW1lL3Rlc3Q9"
    assert encode_account("ÿÿ") == "w7%2FDvw=="
    assert password_digest("secret") == "5ebe2294ecd0e0f08eab7690d2a6ee69"


def test_client_descriptor_uses_vendor_accepted_android_identity() -> None:
    payload = json.loads(
        client_json(
            timezone_offset=8,
            timezone_id="Asia/Taipei",
            version="14.1.0",
        )
    )
    assert payload["name"] == "sdk_gphone64_arm64"
    assert payload["phoneBrand"] == "google"
    assert payload["osVersion"] == "13"


def test_food_status_mapping() -> None:
    assert food_status(0) == "empty"
    assert food_status(1) == "low"
    assert food_status(2) == "normal"
    assert food_status(99) is None


def test_device_day_uses_device_timezone_across_utc_midnight() -> None:
    now = datetime(2026, 10, 19, 17, 30, tzinfo=UTC)
    assert device_day(
        {"locale": "Asia/Shanghai", "timezone": 8}, now
    ) == "20261020"
    assert device_day(
        {"locale": "invalid_zone", "timezone": -7}, now
    ) == "20261019"


def test_schedule_validation_and_hash_are_stable() -> None:
    schedule = validate_schedule(sample_schedule())
    first = schedule_hash({"feedDailyList": schedule})
    second = schedule_hash({"feedDailyList": list(reversed(schedule))})
    assert first == second
    assert schedule[0]["items"][0]["id"] == "27000"


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (lambda days: days.pop(), "schedule_requires_seven_days"),
        (
            lambda days: days[0]["items"][0].update({"amount1": 0, "amount2": 0}),
            "empty_feed_item",
        ),
        (
            lambda days: days[0]["items"][1].update({"time": 27000}),
            "duplicate_feed_time",
        ),
    ],
)
def test_schedule_validation_rejects_unsafe_payloads(mutation, error) -> None:
    days = sample_schedule()
    mutation(days)
    with pytest.raises(ValueError, match=error):
        validate_schedule(days)


def test_next_feed_skips_paused_days() -> None:
    schedule = sample_schedule()
    schedule[0]["suspended"] = 1
    now = datetime(2026, 10, 19, 20, 0, tzinfo=UTC)  # Monday
    next_feed = next_feed_datetime(schedule, now)
    assert next_feed is not None
    assert next_feed.isoweekday() == 2
    assert next_feed.hour == 7
    assert next_feed.minute == 30


def test_normalize_snapshot_merges_flat_and_home_state() -> None:
    schedule = sample_schedule()
    data = normalize_snapshot(
        device={
            "id": "453354",
            "name": "Feeder",
            "timezone": 8,
            "locale": "Asia/Shanghai",
        },
        state_response={
            "food1": 2,
            "food2": 1,
            "feeding": 0,
            "eating": 1,
            "desiccantLeftDays": 25,
            "feedState": {"realAmountTotal": 4, "eatCount": 2},
        },
        home_response={
            "serviceStatus": 2,
            "settings": {"manualLock": 1, "camera": 1},
            "status": {"cameraStatus": 1, "batteryStatus": 2},
            "dailyFeed": {},
        },
        plan={"feedDailyList": schedule},
        now=datetime(2026, 10, 20, 1, 0, tzinfo=UTC),
    )
    assert data["food_bin_1"] == "normal"
    assert data["food_bin_2"] == "low"
    assert data["today_feed"] == 4
    assert data["today_eat"] == 2
    assert data["manual_lock"] is True
    assert data["battery_low"] is True
    assert data["service_expired"] is True
    assert data["plan_enabled"] is True
    assert data["desiccant_days"] == 25


def test_normalize_snapshot_keeps_missing_measurements_unknown() -> None:
    data = normalize_snapshot(
        device={"id": "453354", "locale": "UTC", "timezone": 0},
        state_response={},
        home_response={},
        plan={},
        now=datetime(2026, 10, 20, 1, 0, tzinfo=UTC),
    )
    assert data["desiccant_days"] is None
    assert data["today_feed"] is None
    assert data["today_eat"] is None


def test_normalize_snapshot_sums_bin_totals_and_uses_pim() -> None:
    data = normalize_snapshot(
        device={"id": "453354", "locale": "UTC", "timezone": 0},
        state_response={
            "pim": 1,
            "feedState": {
                "realAmountTotal1": 1,
                "realAmountTotal2": 2,
                "eatCount": 3,
            },
        },
        home_response={},
        plan={},
        now=datetime(2026, 10, 20, 1, 0, tzinfo=UTC),
    )
    assert data["online"] is True
    assert data["today_feed"] == 3

def test_normalize_eating_activity_selects_latest_identified_cat() -> None:
    activity = normalize_eating_activity(
        {
            "eat": [
                {
                    "items": [
                        {
                            "eventId": "older",
                            "petId": "1",
                            "petName": "Older cat",
                            "eatStartTime": 1_792_685_801,
                            "eatEndTime": 1_792_686_017,
                        },
                        {
                            "eventId": "latest",
                            "petId": "2",
                            "petName": "Latest cat",
                            "eatStartTime": 1_792_686_046,
                            "eatEndTime": 1_792_686_088,
                        },
                    ]
                }
            ]
        },
        device={"locale": "Asia/Shanghai", "timezone": 8},
    )
    assert activity["last_eating_event_id"] == "latest"
    assert activity["last_eating_pet_id"] == "2"
    assert activity["last_eating_pet_name"] == "Latest cat"
    assert activity["last_eating_duration_seconds"] == 42
    assert activity["eating_pet_name"] is None

def test_normalize_eating_activity_tracks_open_event() -> None:
    activity = normalize_eating_activity(
        {
            "eat": [
                {
                    "items": [
                        {
                            "eventId": "active",
                            "petId": "2",
                            "petName": "Latest cat",
                            "eatStartTime": 1_792_686_046,
                            "endTime": -1,
                        }
                    ]
                }
            ]
        },
        device={"locale": "Asia/Shanghai", "timezone": 8},
    )
    assert activity["eating_pet_id"] == "2"
    assert activity["eating_pet_name"] == "Latest cat"
    assert activity["eating_event_id"] == "active"
    assert activity["eating_event"]["is_active"] is True

def test_last_identified_pet_ignores_newer_unlabelled_event() -> None:
    activity = normalize_eating_activity(
        {
            "eat": [
                {
                    "items": [
                        {
                            "eventId": "identified",
                            "petId": "2",
                            "petName": "Latest cat",
                            "eatStartTime": 1_792_764_474,
                            "eatEndTime": 1_792_764_518,
                        },
                        {
                            "eventId": "unknown",
                            "eatStartTime": 1_792_765_266,
                            "eatEndTime": 1_792_765_310,
                        },
                    ]
                }
            ]
        },
        device={"locale": "Asia/Shanghai", "timezone": 8},
    )
    assert activity["eating_event"]["event_id"] == "unknown"
    assert activity["last_eating_event_id"] == "identified"
    assert activity["last_eating_pet_name"] == "Latest cat"

def test_open_unlabelled_event_keeps_nearby_pet_as_context_only() -> None:
    activity = normalize_eating_activity(
        {
            "eat": [
                {
                    "items": [
                        {
                            "eventId": "eating",
                            "eatStartTime": 1_792_771_516,
                        }
                    ]
                }
            ],
            "pet": [
                {
                    "items": [
                        {
                            "petId": "2697859",
                            "petName": "啾啾",
                            "timestamp": 1_792_771_268,
                        }
                    ]
                }
            ],
        },
        device={"locale": "Asia/Shanghai", "timezone": 8},
    )
    assert activity["eating_pet_id"] is None
    assert activity["eating_pet_name"] is None
    assert activity["eating_identity_source"] == "unknown"
    assert activity["eating_nearby_pet_id"] == "2697859"
    assert activity["eating_nearby_pet_name"] == "啾啾"

def test_merge_device_record_responses_keeps_cross_midnight_events() -> None:
    merged = merge_device_record_responses(
        {"eat": [{"items": [{"eventId": "today"}]}]},
        {"eat": [{"items": [{"eventId": "yesterday"}]}]},
    )
    assert [item["eventId"] for item in merged["eat"][0]["items"]] == [
        "today"
    ]
    assert [item["eventId"] for item in merged["eat"][1]["items"]] == [
        "yesterday"
    ]
