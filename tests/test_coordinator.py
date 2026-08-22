"""Tests for setup, refresh and error handling."""

from __future__ import annotations

from datetime import timedelta

from custom_components.owm_startup.api import (
    OwmAuthError,
    OwmConnectionError,
    OwmError,
    OwmRateLimitError,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant


async def test_setup_and_unload(hass: HomeAssistant, setup_integration) -> None:
    """The entry sets up and unloads cleanly."""
    assert setup_integration.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()
    assert setup_integration.state is ConfigEntryState.NOT_LOADED


async def test_auth_failure_triggers_reauth(
    hass: HomeAssistant, config_entry, mock_api
) -> None:
    """A rejected key puts the entry into the reauth state."""
    mock_api["current"] = OwmAuthError("401")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_weather_failure_is_fatal(
    hass: HomeAssistant, config_entry, mock_api
) -> None:
    """A failing /weather call causes a retry, not a partial setup."""
    mock_api["current"] = OwmConnectionError("boom")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_fixed_parameters(hass: HomeAssistant, setup_integration) -> None:
    """Forecast length and step count are fixed, not configurable."""
    coordinator = setup_integration.runtime_data
    assert coordinator.forecast_days == 16
    assert coordinator.forecast_steps == 40


async def test_default_update_interval(hass: HomeAssistant, setup_integration) -> None:
    """The default poll interval is one hour."""
    assert setup_integration.runtime_data.update_interval == timedelta(minutes=60)


async def test_refresh_propagates_to_entities(
    hass: HomeAssistant, setup_integration, mock_api
) -> None:
    """New API data reaches the entities on refresh."""
    mock_api["current"]["main"]["temp"] = 25.0
    mock_api["air"]["list"][0]["main"]["aqi"] = 5
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("weather.zoetermeer").attributes["temperature"] == 25.0
    assert hass.states.get("sensor.zoetermeer_air_quality_index").state == "5"


async def test_rate_limit_is_reported_explicitly(
    hass: HomeAssistant, config_entry, mock_api, caplog
) -> None:
    """A 429 must read as a quota problem, not a generic fetch failure."""
    mock_api["current"] = OwmRateLimitError("allowance exceeded on weather")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert "call allowance exceeded" in caplog.text


async def test_rate_limit_on_air_endpoint_is_also_fatal(
    hass: HomeAssistant, config_entry, mock_api
) -> None:
    """Quota is an account-wide condition, wherever it surfaces."""
    mock_api["air_forecast"] = OwmRateLimitError("allowance exceeded on air")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_air_auth_failure_does_not_demand_reauth(
    hass: HomeAssistant, config_entry, mock_api, caplog
) -> None:
    """The weather endpoints just authenticated on the same key.

    Treating this as a credential problem would take the whole entry down and
    ask the user to re-enter a key that demonstrably works.
    """
    mock_api["air"] = OwmAuthError("401")
    mock_api["air_forecast"] = OwmAuthError("401")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("weather.zoetermeer").state == "partlycloudy"
    assert hass.states.get("sensor.zoetermeer_pm2_5").state == "unavailable"
    assert "rejected this key" in caplog.text


async def test_malformed_response_is_not_fatal_to_diagnosis(
    hass: HomeAssistant, config_entry, mock_api
) -> None:
    """A 200 with an unusable body retries rather than crashing setup."""
    mock_api["current"] = OwmError("Malformed response from weather")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
