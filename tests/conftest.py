"""Load integration submodules without importing the Home Assistant entrypoint."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

package_root = Path(__file__).parents[1] / "custom_components"


@pytest.fixture(autouse=True)
def enable_event_loop_debug(event_loop):
    """Provide the current loop expected by HA's pytest plugin."""
    event_loop.set_debug(True)
    yield


@pytest.fixture(autouse=True)
def verify_cleanup(event_loop):
    """Use the same test loop through teardown."""
    yield
    if not event_loop.is_closed():
        event_loop.run_until_complete(event_loop.shutdown_default_executor())


try:
    import homeassistant  # noqa: F401
except ModuleNotFoundError:
    custom_components = types.ModuleType("custom_components")
    custom_components.__path__ = [str(package_root)]
    petkit = types.ModuleType("custom_components.petkit")
    petkit.__path__ = [str(package_root / "petkit")]

    sys.modules["custom_components"] = custom_components
    sys.modules["custom_components.petkit"] = petkit
    custom_components.petkit = petkit
