"""Tests for the weather entity."""

from __future__ import annotations

import pytest

from custom_components.owm_startup.api import OwmConnectionError
from homeassistant.components.weather import (
    SERVICE_GET_FORECASTS,
    WeatherEntityFeature,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant

ENTITY_ID = "weather.zoetermeer"


async def test_current_conditions(hass: HomeAssistant, setup_integration) -> None:
    """Current conditions are mapped from /weather."""
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == "partlycloudy"
    assert state.attributes["temperature"] == 19.4
    assert state.attributes["humidity"] == 72
    assert state.attributes["pressure"] == 1013
    assert state.attributes["wind_bearing"] == 230
    assert state.attributes["cloud_coverage"] == 40
    assert state.attributes["visibility"] == 10.0
    assert "ODbL" in state.attributes["attribution"]


async def test_supported_features(hass: HomeAssistant, setup_integration) -> None:
    """Both daily and hourly forecasts are advertised."""
    features = hass.states.get(ENTITY_ID).attributes[ATTR_SUPPORTED_FEATURES]
    assert features & WeatherEntityFeature.FORECAST_DAILY
    assert features & WeatherEntityFeature.FORECAST_HOURLY


@pytest.mark.parametrize(
    ("forecast_type", "expected_count"),
    [("daily", 16), ("hourly", 40)],
)
async def test_forecast_length(
    hass: HomeAssistant, setup_integration, forecast_type, expected_count
) -> None:
    """Both forecast types return the expected number of entries."""
    result = await hass.services.async_call(
        "weather",
        SERVICE_GET_FORECASTS,
        {ATTR_ENTITY_ID: ENTITY_ID, "type": forecast_type},
        blocking=True,
        return_response=True,
    )
    assert len(result[ENTITY_ID]["forecast"]) == expected_count


async def test_daily_forecast_mapping(hass: HomeAssistant, setup_integration) -> None:
    """Daily entries carry high/low, precipitation and UV index."""
    result = await hass.services.async_call(
        "weather",
        SERVICE_GET_FORECASTS,
        {ATTR_ENTITY_ID: ENTITY_ID, "type": "daily"},
        blocking=True,
        return_response=True,
    )
    first = result[ENTITY_ID]["forecast"][0]
    assert first["condition"] == "rainy"
    assert first["temperature"] == 21.7
    assert first["templow"] == 11.2
    assert first["precipitation"] == 2.35
    assert first["precipitation_probability"] == 42
    assert first["uv_index"] == 5.1


async def test_hourly_forecast_uses_pod_for_night(
    hass: HomeAssistant, setup_integration
) -> None:
    """A clear step with pod=n maps to clear-night, not sunny."""
    result = await hass.services.async_call(
        "weather",
        SERVICE_GET_FORECASTS,
        {ATTR_ENTITY_ID: ENTITY_ID, "type": "hourly"},
        blocking=True,
        return_response=True,
    )
    assert result[ENTITY_ID]["forecast"][0]["condition"] == "clear-night"


async def test_hourly_failure_is_not_fatal(
    hass: HomeAssistant, config_entry, mock_api
) -> None:
    """A failing /forecast call leaves the weather entity available."""
    mock_api["hourly"] = OwmConnectionError("boom")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == "partlycloudy"

    result = await hass.services.async_call(
        "weather",
        SERVICE_GET_FORECASTS,
        {ATTR_ENTITY_ID: ENTITY_ID, "type": "daily"},
        blocking=True,
        return_response=True,
    )
    assert len(result[ENTITY_ID]["forecast"]) == 16
