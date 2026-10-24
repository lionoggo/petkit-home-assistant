"""Identified pet activity event entity helpers."""

import pytest

pytest.importorskip("homeassistant.components.event")

from custom_components.petkit.event import _activity_key


def test_activity_key_tracks_start_and_finish() -> None:
    assert _activity_key(None) is None
    assert _activity_key({"event_id": "event-one"}) == "event-one:started"
    assert (
        _activity_key({"event_id": "event-one", "ended_at": "2026-10-24T00:00:00Z"})
        == "event-one:finished"
    )
