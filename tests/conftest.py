"""Shared fixtures for the owm_startup test suite."""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.owm_startup.const import CONF_LANGUAGE, DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY, CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant

FIXTURE_DIR = Path(__file__).parent / "fixtures"

# The epoch the fixture timestamps were generated against.
FIXTURE_EPOCH = 1755800000


def _rebase(node: Any, offset: int) -> Any:
    """Shift every timestamp field so fixtures stay relative to now."""
    if isinstance(node, dict):
        return {
            key: (value + offset)
            if key in ("dt", "sunrise", "sunset") and isinstance(value, int)
            else _rebase(value, offset)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_rebase(item, offset) for item in node]
    return node


def load_fixture(name: str) -> dict[str, Any]:
    """Return a decoded JSON fixture with timestamps rebased onto now.

    Fixtures ship with fixed timestamps for readability; rebasing keeps
    horizon and day/night logic testable without freezing the clock.
    """
    payload = json.loads((FIXTURE_DIR / f"{name}.json").read_text())
    return _rebase(payload, int(time.time()) - FIXTURE_EPOCH)


@pytest.fixture(scope="session", autouse=True)
def warm_dns_resolver() -> None:
    """Spawn pycares' daemon thread before cleanup baselines are taken.

    aiohttp's async resolver starts a background thread the first time a
    channel is created. Home Assistant's verify_cleanup fixture would
    otherwise attribute that thread to whichever test happened to run first.
    """
    try:
        import pycares
    except ImportError:
        return
    pycares.Channel()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of custom integrations in every test."""
    return


@pytest.fixture(autouse=True)
async def unload_entries_after_test(hass: HomeAssistant):
    """Unload entries so the coordinator's refresh timer does not linger."""
    yield
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
def api_payloads() -> dict[str, dict[str, Any]]:
    """Return the full set of endpoint payloads, mutable per test."""
    return {
        "current": load_fixture("current"),
        "daily": load_fixture("forecast_daily"),
        "hourly": load_fixture("forecast_hourly"),
        "air": load_fixture("air_pollution"),
        "air_forecast": load_fixture("air_pollution_forecast"),
    }


@pytest.fixture
def mock_api(api_payloads):
    """Patch the API client so no network calls are made.

    Set a payload to an exception instance to simulate that endpoint failing:

        api_payloads["air"] = OwmConnectionError("boom")
    """

    def _side_effect(key):
        async def _call(*args, **kwargs):
            value = api_payloads[key]
            if isinstance(value, Exception):
                raise value
            return value

        return _call

    target = "custom_components.owm_startup.api.OwmApiClient"
    with (
        patch(f"{target}.async_get_current", side_effect=_side_effect("current")),
        patch(f"{target}.async_get_daily_forecast", side_effect=_side_effect("daily")),
        patch(
            f"{target}.async_get_hourly_forecast", side_effect=_side_effect("hourly")
        ),
        patch(f"{target}.async_get_air_pollution", side_effect=_side_effect("air")),
        patch(
            f"{target}.async_get_air_pollution_forecast",
            side_effect=_side_effect("air_forecast"),
        ),
    ):
        yield api_payloads


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry for the integration."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Zoetermeer",
        unique_id="52.06-4.49",
        data={
            CONF_API_KEY: "0123456789abcdef0123456789abcdef",
            CONF_LATITUDE: 52.06,
            CONF_LONGITUDE: 4.49,
            CONF_LANGUAGE: "en",
        },
    )


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api
) -> MockConfigEntry:
    """Set up the integration with mocked API responses."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
