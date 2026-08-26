"""Weather entity backed by the OpenWeatherMap Startup plan."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.components.weather import (
    ATTR_CONDITION_CLEAR_NIGHT,
    ATTR_CONDITION_CLOUDY,
    ATTR_CONDITION_EXCEPTIONAL,
    ATTR_CONDITION_FOG,
    ATTR_CONDITION_LIGHTNING,
    ATTR_CONDITION_LIGHTNING_RAINY,
    ATTR_CONDITION_PARTLYCLOUDY,
    ATTR_CONDITION_POURING,
    ATTR_CONDITION_RAINY,
    ATTR_CONDITION_SNOWY,
    ATTR_CONDITION_SNOWY_RAINY,
    ATTR_CONDITION_SUNNY,
    ATTR_CONDITION_WINDY,
    Forecast,
    SingleCoordinatorWeatherEntity,
    WeatherEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfLength,
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTRIBUTION, DEVICE_MODEL, DOMAIN, MANUFACTURER
from .coordinator import OwmStartupCoordinator


def _condition(weather_id: int, is_daytime: bool) -> str | None:
    """Map an OpenWeatherMap condition id onto a Home Assistant condition."""
    if 200 <= weather_id <= 232:
        if weather_id in (210, 211, 212, 221):
            return ATTR_CONDITION_LIGHTNING
        return ATTR_CONDITION_LIGHTNING_RAINY
    if 300 <= weather_id <= 321:
        return ATTR_CONDITION_RAINY
    if weather_id == 511:
        return ATTR_CONDITION_SNOWY_RAINY
    if weather_id in (500, 501):
        return ATTR_CONDITION_RAINY
    if 502 <= weather_id <= 531:
        return ATTR_CONDITION_POURING
    if 611 <= weather_id <= 616:
        return ATTR_CONDITION_SNOWY_RAINY
    if 600 <= weather_id <= 622:
        return ATTR_CONDITION_SNOWY
    if weather_id == 771:
        return ATTR_CONDITION_WINDY
    if weather_id == 781:
        return ATTR_CONDITION_EXCEPTIONAL
    if 701 <= weather_id <= 762:
        return ATTR_CONDITION_FOG
    if weather_id == 800:
        return ATTR_CONDITION_SUNNY if is_daytime else ATTR_CONDITION_CLEAR_NIGHT
    if weather_id in (801, 802):
        return ATTR_CONDITION_PARTLYCLOUDY
    if weather_id in (803, 804):
        return ATTR_CONDITION_CLOUDY
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the weather entity."""
    async_add_entities([OwmStartupWeather(entry.runtime_data, entry)])


class OwmStartupWeather(SingleCoordinatorWeatherEntity[OwmStartupCoordinator]):
    """Current conditions plus a daily forecast of up to 16 days."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _attr_name = None
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_native_wind_speed_unit = UnitOfSpeed.METERS_PER_SECOND
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_native_visibility_unit = UnitOfLength.KILOMETERS
    _attr_supported_features = (
        WeatherEntityFeature.FORECAST_DAILY | WeatherEntityFeature.FORECAST_HOURLY
    )

    def __init__(self, coordinator: OwmStartupCoordinator, entry: ConfigEntry) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=DEVICE_MODEL,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://openweathermap.org/price",
        )

    @property
    def _current(self) -> dict[str, Any]:
        return self.coordinator.data.current

    @property
    def _is_daytime(self) -> bool:
        """Return whether the observation timestamp falls between sun events."""
        sys_data = self._current.get("sys") or {}
        now = self._current.get("dt")
        sunrise = sys_data.get("sunrise")
        sunset = sys_data.get("sunset")
        if None in (now, sunrise, sunset):
            return True
        return bool(sunrise <= now < sunset)

    @property
    def condition(self) -> str | None:
        """Return the current condition."""
        weather = self._current.get("weather") or []
        if not weather:
            return None
        return _condition(weather[0].get("id", 0), self._is_daytime)

    @property
    def native_temperature(self) -> float | None:
        """Return the current temperature."""
        return (self._current.get("main") or {}).get("temp")

    @property
    def native_apparent_temperature(self) -> float | None:
        """Return the apparent temperature."""
        return (self._current.get("main") or {}).get("feels_like")

    @property
    def native_pressure(self) -> float | None:
        """Return the barometric pressure."""
        return (self._current.get("main") or {}).get("pressure")

    @property
    def humidity(self) -> float | None:
        """Return the relative humidity."""
        return (self._current.get("main") or {}).get("humidity")

    @property
    def cloud_coverage(self) -> float | None:
        """Return the cloud coverage."""
        return (self._current.get("clouds") or {}).get("all")

    @property
    def native_wind_speed(self) -> float | None:
        """Return the wind speed."""
        return (self._current.get("wind") or {}).get("speed")

    @property
    def native_wind_gust_speed(self) -> float | None:
        """Return the wind gust speed."""
        return (self._current.get("wind") or {}).get("gust")

    @property
    def wind_bearing(self) -> float | None:
        """Return the wind bearing."""
        return (self._current.get("wind") or {}).get("deg")

    @property
    def native_visibility(self) -> float | None:
        """Return the visibility in kilometres."""
        visibility = self._current.get("visibility")
        if visibility is None:
            return None
        return visibility / 1000

    @callback
    def _async_forecast_daily(self) -> list[Forecast] | None:
        """Return the daily forecast."""
        forecast: list[Forecast] = []
        for day in self.coordinator.data.daily:
            weather = day.get("weather") or []
            temp = day.get("temp") or {}
            precipitation = (day.get("rain") or 0) + (day.get("snow") or 0)
            entry: Forecast = {
                "datetime": datetime.fromtimestamp(day["dt"], tz=UTC).isoformat(),
                "condition": _condition(weather[0]["id"], True) if weather else None,
                "native_temperature": temp.get("max"),
                "native_templow": temp.get("min"),
                "native_apparent_temperature": (day.get("feels_like") or {}).get("day"),
                "native_precipitation": round(precipitation, 2),
                "precipitation_probability": round((day.get("pop") or 0) * 100),
                "native_wind_speed": day.get("speed"),
                "native_wind_gust_speed": day.get("gust"),
                "wind_bearing": day.get("deg"),
                "humidity": day.get("humidity"),
                "native_pressure": day.get("pressure"),
                "cloud_coverage": day.get("clouds"),
                "uv_index": day.get("uvi"),
            }
            forecast.append(entry)
        return forecast

    @callback
    def _async_forecast_hourly(self) -> list[Forecast] | None:
        """Return the 3-hourly forecast.

        The Startup plan has no true hourly product; /data/2.5/forecast returns
        3-hour steps, which Home Assistant renders as an hourly forecast.
        """
        forecast: list[Forecast] = []
        for step in self.coordinator.data.hourly:
            weather = step.get("weather") or []
            main = step.get("main") or {}
            wind = step.get("wind") or {}
            precipitation = (step.get("rain") or {}).get("3h", 0) + (
                step.get("snow") or {}
            ).get("3h", 0)
            is_daytime = (step.get("sys") or {}).get("pod", "d") == "d"
            visibility = step.get("visibility")
            entry: Forecast = {
                "datetime": datetime.fromtimestamp(step["dt"], tz=UTC).isoformat(),
                "condition": (
                    _condition(weather[0]["id"], is_daytime) if weather else None
                ),
                "native_temperature": main.get("temp"),
                "native_apparent_temperature": main.get("feels_like"),
                "native_precipitation": round(precipitation, 2),
                "precipitation_probability": round((step.get("pop") or 0) * 100),
                "native_wind_speed": wind.get("speed"),
                "native_wind_gust_speed": wind.get("gust"),
                "wind_bearing": wind.get("deg"),
                "humidity": main.get("humidity"),
                "native_pressure": main.get("pressure"),
                "cloud_coverage": (step.get("clouds") or {}).get("all"),
                "native_visibility": (
                    visibility / 1000 if visibility is not None else None
                ),
            }
            forecast.append(entry)
        return forecast
