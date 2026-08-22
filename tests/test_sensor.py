"""Tests for the air quality sensors."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from custom_components.owm_startup.api import OwmConnectionError
from custom_components.owm_startup.sensor import (
    SENSOR_TYPES,
    OwmAirQualityForecastSensor,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util


async def test_current_air_quality(hass: HomeAssistant, setup_integration) -> None:
    """Current pollutant values come from /air_pollution."""
    assert hass.states.get("sensor.zoetermeer_air_quality_index").state == "2"
    assert hass.states.get("sensor.zoetermeer_pm2_5").state == "8.4"
    assert hass.states.get("sensor.zoetermeer_pm10").state == "11.9"
    assert hass.states.get("sensor.zoetermeer_ozone").state == "68.0"


async def test_aqi_level_attribute(hass: HomeAssistant, setup_integration) -> None:
    """The index sensor exposes a qualitative label."""
    state = hass.states.get("sensor.zoetermeer_air_quality_index")
    assert state.attributes["level"] == "fair"


async def test_forecast_today_reports_peak(
    hass: HomeAssistant, setup_integration
) -> None:
    """The today sensor reports the worst value left in the local day."""
    state = hass.states.get("sensor.zoetermeer_air_quality_index_forecast_today")
    assert state.attributes["window"] == "today"
    assert state.attributes["peak_at"] is not None
    assert state.state != "unknown"


async def test_both_day_sensors_exist(hass: HomeAssistant, setup_integration) -> None:
    """Every pollutant gets a today and a tomorrow sensor."""
    for pollutant in (
        "air_quality_index",
        "pm2_5",
        "pm10",
        "ozone",
        "nitrogen_dioxide",
        "nitrogen_monoxide",
        "sulphur_dioxide",
        "carbon_monoxide",
        "ammonia",
    ):
        for day in ("today", "tomorrow"):
            entity_id = f"sensor.zoetermeer_{pollutant}_forecast_{day}"
            assert hass.states.get(entity_id) is not None, entity_id


async def test_peak_time_is_local_and_inside_window(
    hass: HomeAssistant, setup_integration
) -> None:
    """peak_at is a local timestamp that falls inside the window."""
    for day in ("today", "tomorrow"):
        state = hass.states.get(f"sensor.zoetermeer_air_quality_index_forecast_{day}")
        peak_at = dt_util.parse_datetime(state.attributes["peak_at"])
        start = dt_util.parse_datetime(state.attributes["window_start"])
        end = dt_util.parse_datetime(state.attributes["window_end"])
        assert peak_at is not None
        assert start <= peak_at < end
        assert peak_at.tzinfo is not None


async def test_windows_are_calendar_days(
    hass: HomeAssistant, setup_integration
) -> None:
    """Today and tomorrow cover their own local dates and do not overlap."""
    today_state = hass.states.get("sensor.zoetermeer_air_quality_index_forecast_today")
    tomorrow_state = hass.states.get(
        "sensor.zoetermeer_air_quality_index_forecast_tomorrow"
    )

    today = dt_util.now().date()
    tomorrow = today + timedelta(days=1)

    today_times = [
        dt_util.parse_datetime(item["datetime"])
        for item in today_state.attributes["forecast"]
    ]
    tomorrow_times = [
        dt_util.parse_datetime(item["datetime"])
        for item in tomorrow_state.attributes["forecast"]
    ]

    assert today_times
    assert len(tomorrow_times) == 24
    assert all(moment.date() == today for moment in today_times)
    assert all(moment.date() == tomorrow for moment in tomorrow_times)
    assert not set(today_times) & set(tomorrow_times)


async def test_today_window_shrinks_during_the_day(
    hass: HomeAssistant, setup_integration
) -> None:
    """Today's window never reaches past local midnight."""
    state = hass.states.get("sensor.zoetermeer_air_quality_index_forecast_today")
    end = dt_util.parse_datetime(state.attributes["window_end"])
    assert end == dt_util.start_of_local_day() + timedelta(days=1)
    assert len(state.attributes["forecast"]) <= 25


async def test_forecast_has_no_state_class(
    hass: HomeAssistant, setup_integration
) -> None:
    """Forecast sensors must stay out of long-term statistics."""
    state = hass.states.get("sensor.zoetermeer_pm2_5_forecast_today")
    assert "state_class" not in state.attributes


async def test_air_failure_is_not_fatal(
    hass: HomeAssistant, config_entry, mock_api
) -> None:
    """Failing air quality calls leave sensors unavailable, not the entry."""
    mock_api["air"] = OwmConnectionError("boom")
    mock_api["air_forecast"] = OwmConnectionError("boom")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("weather.zoetermeer").state == "partlycloudy"
    assert hass.states.get("sensor.zoetermeer_pm2_5").state == "unavailable"


def _forecast_sensor(coordinator, description, day_offset):
    """Build a forecast sensor without going through Home Assistant setup."""
    from custom_components.owm_startup.const import AQ_DAY_SLUGS

    sensor = object.__new__(OwmAirQualityForecastSensor)
    sensor.coordinator = coordinator
    sensor.entity_description = description
    sensor._day_offset = day_offset
    sensor._slug = AQ_DAY_SLUGS[day_offset]
    return sensor


@pytest.mark.parametrize(
    ("frozen_time", "expected_today"),
    [
        ("2026-08-22 09:30:00", 15),  # 09:00 entry onwards
        ("2026-08-22 23:30:00", 1),  # only the 23:00 entry is left
        ("2026-08-22 00:00:00", 24),  # whole day ahead
    ],
)
async def test_today_window_by_time_of_day(
    hass: HomeAssistant, freezer, frozen_time, expected_today
) -> None:
    """Today's window shrinks as the local day advances."""
    # The test harness defaults to US/Pacific; pin it so "local day" is
    # unambiguous against the frozen UTC timestamps below.
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to(frozen_time)
    base = int(dt_util.parse_datetime("2026-08-22 00:00:00+00:00").timestamp())
    entries = [
        {
            "dt": base + hour * 3600,
            "main": {"aqi": 1 + hour % 4},
            "components": {"pm2_5": 5.0},
        }
        for hour in range(48)
    ]
    coordinator = SimpleNamespace(data=SimpleNamespace(air_forecast=entries))

    today = _forecast_sensor(coordinator, SENSOR_TYPES[0], 0)
    tomorrow = _forecast_sensor(coordinator, SENSOR_TYPES[0], 1)

    assert len(today._window()) == expected_today
    assert len(tomorrow._window()) == 24
    assert not {item["dt"] for item in today._window()} & {
        item["dt"] for item in tomorrow._window()
    }


async def test_today_window_empty_late_at_night(hass: HomeAssistant, freezer) -> None:
    """With nothing left today the sensor reports no value, not a stale one."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-22 23:59:00")
    base = int(dt_util.parse_datetime("2026-08-23 00:00:00+00:00").timestamp())
    entries = [
        {"dt": base + hour * 3600, "main": {"aqi": 3}, "components": {"pm2_5": 5.0}}
        for hour in range(24)
    ]
    coordinator = SimpleNamespace(data=SimpleNamespace(air_forecast=entries))

    today = _forecast_sensor(coordinator, SENSOR_TYPES[0], 0)
    assert today._window() == []
    assert today.native_value is None
    assert today.extra_state_attributes["peak_at"] is None
