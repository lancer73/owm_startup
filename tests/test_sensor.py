"""Tests for the air quality sensors."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from custom_components.owm_startup.api import OwmConnectionError
from custom_components.owm_startup.sensor import (
    SENSOR_TYPES,
    OwmAirQualityForecastSensor,
    band_for,
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


async def test_band_sensors_exist_for_every_scaled_pollutant(
    hass: HomeAssistant, setup_integration
) -> None:
    """Bands cover the index and the six pollutants OpenWeather scales."""
    for pollutant in (
        "air_quality",
        "pm2_5_level",
        "pm10_level",
        "ozone_level",
        "nitrogen_dioxide_level",
        "sulphur_dioxide_level",
        "carbon_monoxide_level",
    ):
        assert hass.states.get(f"sensor.zoetermeer_{pollutant}") is not None, pollutant
        for day in ("today", "tomorrow"):
            entity_id = f"sensor.zoetermeer_{pollutant}_{day}"
            assert hass.states.get(entity_id) is not None, entity_id


async def test_background_sensors_exist_and_are_labelled_apart(
    hass: HomeAssistant, setup_integration
) -> None:
    """NH3 and NO are scored against background, under their own names."""
    for entity_id in (
        "sensor.zoetermeer_ammonia_vs_background",
        "sensor.zoetermeer_ammonia_vs_background_today",
        "sensor.zoetermeer_nitrogen_monoxide_vs_background",
        "sensor.zoetermeer_nitrogen_monoxide_vs_background_tomorrow",
    ):
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert state.attributes["options"] == ["low", "typical", "elevated", "high"]

    # And no health-scale entity was created for them.
    assert hass.states.get("sensor.zoetermeer_ammonia_level") is None
    # They remain available as current numbers.
    assert hass.states.get("sensor.zoetermeer_ammonia").state == "0.9"


async def test_numeric_forecast_sensors_are_gone(
    hass: HomeAssistant, setup_integration
) -> None:
    """Forecasts are bands only.

    A microgram figure two days out reads as precision the model lacks.
    """
    for entity_id in (
        "sensor.zoetermeer_air_quality_index_forecast_today",
        "sensor.zoetermeer_pm2_5_forecast_today",
        "sensor.zoetermeer_pm2_5_forecast_tomorrow",
    ):
        assert hass.states.get(entity_id) is None, entity_id


async def test_current_numbers_are_kept(hass: HomeAssistant, setup_integration) -> None:
    """Current readings stay numeric so they can be graphed."""
    state = hass.states.get("sensor.zoetermeer_pm2_5")
    assert state.state == "8.4"
    assert state.attributes["state_class"] == "measurement"


async def test_current_band_carries_its_value(
    hass: HomeAssistant, setup_integration
) -> None:
    """The band names the number, and the number stays as an attribute."""
    aqi = hass.states.get("sensor.zoetermeer_air_quality")
    assert aqi.state == "fair"
    assert aqi.attributes["index"] == 2

    pm25 = hass.states.get("sensor.zoetermeer_pm2_5_level")
    # 8.4 µg/m³ is below the 10 boundary, so Good.
    assert pm25.state == "good"
    assert pm25.attributes["value"] == 8.4


async def test_forecast_band_reports_the_window_peak(
    hass: HomeAssistant, setup_integration
) -> None:
    """The band is the worst expected in the window, with the peak attached."""
    state = hass.states.get("sensor.zoetermeer_air_quality_today")
    assert state.state in state.attributes["options"]
    assert state.attributes["window"] == "today"
    assert state.attributes["peak_at"] is not None
    assert state.attributes["index"] is not None


async def test_forecast_band_windows_are_calendar_days(
    hass: HomeAssistant, setup_integration
) -> None:
    """Today and tomorrow cover their own local dates."""
    today = hass.states.get("sensor.zoetermeer_air_quality_today")
    tomorrow = hass.states.get("sensor.zoetermeer_air_quality_tomorrow")

    assert (
        today.attributes["window_end"]
        == (dt_util.start_of_local_day() + timedelta(days=1)).isoformat()
    )
    assert (
        tomorrow.attributes["window_start"]
        == (dt_util.start_of_local_day() + timedelta(days=1)).isoformat()
    )

    times = [
        dt_util.parse_datetime(item["datetime"])
        for item in tomorrow.attributes["forecast"]
    ]
    assert len(times) == 24
    assert all(
        moment.date() == dt_util.now().date() + timedelta(days=1) for moment in times
    )


async def test_bands_have_no_state_class(
    hass: HomeAssistant, setup_integration
) -> None:
    """An enum must stay out of statistics, and carries no unit."""
    for entity_id in (
        "sensor.zoetermeer_air_quality",
        "sensor.zoetermeer_pm10_level_tomorrow",
    ):
        state = hass.states.get(entity_id)
        assert "state_class" not in state.attributes, entity_id
        assert "unit_of_measurement" not in state.attributes, entity_id
        assert state.attributes["device_class"] == "enum"


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


@pytest.mark.parametrize(
    ("component", "value", "expected"),
    [
        # Boundaries from OpenWeather's published scale.
        ("pm2_5", 0, "good"),
        ("pm2_5", 9.9, "good"),
        ("pm2_5", 10, "fair"),
        ("pm2_5", 24.9, "fair"),
        ("pm2_5", 25, "moderate"),
        ("pm2_5", 50, "poor"),
        ("pm2_5", 75, "very_poor"),
        ("pm2_5", 1000, "very_poor"),
        ("pm10", 20, "fair"),
        ("o3", 59, "good"),
        ("o3", 180, "very_poor"),
        ("no2", 40, "fair"),
        ("so2", 350, "very_poor"),
        ("co", 4399, "good"),
        ("co", 15400, "very_poor"),
        ("aqi", 1, "good"),
        ("aqi", 5, "very_poor"),
    ],
)
def test_band_for_matches_the_published_scale(component, value, expected) -> None:
    """Each boundary is inclusive at the lower edge, per OpenWeather's table."""
    assert band_for(component, value) == expected


@pytest.mark.parametrize(
    ("component", "value", "expected"),
    [
        # NH3 against Dutch ambient background: coastal 1-2, national mean
        # about 5, livestock areas up to 15.
        ("nh3", 1.0, "low"),
        ("nh3", 2, "typical"),
        ("nh3", 5.4, "typical"),
        ("nh3", 8, "elevated"),
        ("nh3", 15, "high"),
        ("nh3", 50, "high"),
        ("no", 1.0, "low"),
        ("no", 5, "typical"),
        ("no", 10, "elevated"),
        ("no", 25, "high"),
    ],
)
def test_background_bands(component, value, expected) -> None:
    """NH3 and NO are scored against background, not against health limits."""
    assert band_for(component, value) == expected


def test_background_bands_use_a_separate_vocabulary() -> None:
    """A background band must never read like a health verdict.

    OpenWeather publishes no health scale for these two, so borrowing
    "Good"/"Poor" would assert something no source supports.
    """
    from custom_components.owm_startup.sensor import band_options

    health = set(band_options("pm2_5"))
    background = set(band_options("nh3"))

    assert background == {"low", "typical", "elevated", "high"}
    assert not health & background
    assert band_options("no") == band_options("nh3")


def test_unknown_components_have_no_band() -> None:
    """Anything without a sourced scale stays unscored."""
    assert band_for("nox", 50) is None


def test_band_for_handles_missing_values() -> None:
    """A missing reading has no band rather than a wrong one."""
    assert band_for("pm2_5", None) is None
    assert band_for("aqi", None) is None


def test_health_bands_have_a_state_icon_ramp() -> None:
    """Severity has to read through the glyph: icons carry no colour.

    An integration can supply state-based icon translations but nothing in
    that schema sets a colour, so the glyph is the only signal available
    without dashboard configuration.
    """
    import json
    from pathlib import Path

    icons = json.loads(
        (
            Path(__file__).parent.parent
            / "custom_components"
            / "owm_startup"
            / "icons.json"
        ).read_text()
    )["entity"]["sensor"]

    for key in ("aqi_level", "pm2_5_level_today", "co_level_tomorrow"):
        states = icons[key]["state"]
        assert set(states) == {"good", "fair", "moderate", "poor", "very_poor"}
        assert len(set(states.values())) == 5, key


def test_background_bands_have_no_severity_ramp() -> None:
    """A background band is not a verdict, so it does not escalate visually."""
    import json
    from pathlib import Path

    icons = json.loads(
        (
            Path(__file__).parent.parent
            / "custom_components"
            / "owm_startup"
            / "icons.json"
        ).read_text()
    )["entity"]["sensor"]

    for key in ("nh3_background_level", "no_background_level_today"):
        assert "state" not in icons[key], key


async def test_forecast_attributes_match_the_documented_chart_example(
    hass: HomeAssistant, setup_integration
) -> None:
    """The README's apexcharts example reads these names directly.

    A rename here would break that example silently, since nothing else in the
    codebase consumes the timeline.
    """
    for day in ("today", "tomorrow"):
        state = hass.states.get(f"sensor.zoetermeer_air_quality_{day}")
        timeline = state.attributes["forecast"]

        assert timeline, day
        for point in timeline:
            assert set(point) == {"datetime", "aqi"}
            assert 1 <= point["aqi"] <= 5
            # Local time with an offset, so new Date() in the browser is right.
            parsed = dt_util.parse_datetime(point["datetime"])
            assert parsed is not None
            assert parsed.tzinfo is not None


async def test_window_end_is_available_for_charting(
    hass: HomeAssistant, setup_integration
) -> None:
    """The chart example closes each step at the window boundary.

    Without it the final hour of a stepline is drawn with no width, so the
    attribute is part of the documented interface too.
    """
    for day in ("today", "tomorrow"):
        state = hass.states.get(f"sensor.zoetermeer_air_quality_{day}")
        window_end = dt_util.parse_datetime(state.attributes["window_end"])
        last_point = dt_util.parse_datetime(
            state.attributes["forecast"][-1]["datetime"]
        )

        assert window_end is not None
        assert window_end.tzinfo is not None
        # The boundary must sit after the last hourly point, or closing the
        # step would draw backwards.
        assert window_end > last_point


async def test_numeric_index_is_recordable_for_the_chart_history(
    hass: HomeAssistant, setup_integration
) -> None:
    """The README chart plots the recorded past from the numeric index.

    That only works while the sensor stays numeric with a measurement state
    class; the band sensor beside it is an enum and has no history to plot.
    """
    numeric = hass.states.get("sensor.zoetermeer_air_quality_index")
    assert numeric.state == "2"
    assert numeric.attributes["state_class"] == "measurement"
    assert "options" not in numeric.attributes

    band = hass.states.get("sensor.zoetermeer_air_quality")
    assert band.attributes["device_class"] == "enum"
    assert "state_class" not in band.attributes


async def test_pollutant_band_attributes_match_the_documented_chart_example(
    hass: HomeAssistant, setup_integration
) -> None:
    """The second README chart plots one point per window from these names.

    A pollutant band has no hourly timeline, so the peak and its time are the
    whole of what can be charted; renaming either would break the example
    silently.
    """
    for day in ("today", "tomorrow"):
        state = hass.states.get(f"sensor.zoetermeer_pm2_5_level_{day}")

        assert "forecast" not in state.attributes, "pollutants carry no timeline"
        assert isinstance(state.attributes["value"], (int, float))
        peak_at = dt_util.parse_datetime(state.attributes["peak_at"])
        assert peak_at is not None
        assert peak_at.tzinfo is not None


async def test_pollutant_annotations_match_the_band_table(
    hass: HomeAssistant, setup_integration
) -> None:
    """The chart's annotation lines are OpenWeather's PM2.5 boundaries.

    The README hard-codes them, so a change to the table must fail here rather
    than leave the chart quietly disagreeing with the sensors beside it.
    """
    from custom_components.owm_startup.const import POLLUTANT_BANDS

    assert POLLUTANT_BANDS["pm2_5"] == (10, 25, 50, 75)
