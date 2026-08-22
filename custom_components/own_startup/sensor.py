"""Air quality sensors for the OpenWeatherMap Startup-plan integration.

Three sets of entities are created:
  - current air quality, one entity per pollutant plus the OWM index
  - forecast air quality for the rest of today
  - forecast air quality for tomorrow

Forecast entities report the worst (highest) value expected inside their
window, with the local time of that peak as an attribute. Windows are local
calendar days, so today's shortens as the day goes on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONCENTRATION_MICROGRAMS_PER_CUBIC_METER
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    AQ_DAY_SLUGS,
    AQ_FORECAST_DAYS,
    AQI_LABELS,
    ATTRIBUTION,
    DEVICE_MODEL,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import OwmStartupCoordinator

AQI_KEY = "aqi"


@dataclass(frozen=True, kw_only=True)
class OwmAirSensorDescription(SensorEntityDescription):
    """Describes an air quality sensor."""

    component: str


SENSOR_TYPES: tuple[OwmAirSensorDescription, ...] = (
    OwmAirSensorDescription(
        key=AQI_KEY,
        component=AQI_KEY,
        translation_key="air_quality_index",
        device_class=SensorDeviceClass.AQI,
    ),
    OwmAirSensorDescription(
        key="pm2_5",
        component="pm2_5",
        device_class=SensorDeviceClass.PM25,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        suggested_display_precision=1,
    ),
    OwmAirSensorDescription(
        key="pm10",
        component="pm10",
        device_class=SensorDeviceClass.PM10,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        suggested_display_precision=1,
    ),
    OwmAirSensorDescription(
        key="o3",
        component="o3",
        device_class=SensorDeviceClass.OZONE,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        suggested_display_precision=1,
    ),
    OwmAirSensorDescription(
        key="no2",
        component="no2",
        device_class=SensorDeviceClass.NITROGEN_DIOXIDE,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        suggested_display_precision=1,
    ),
    OwmAirSensorDescription(
        key="no",
        component="no",
        device_class=SensorDeviceClass.NITROGEN_MONOXIDE,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        suggested_display_precision=1,
    ),
    OwmAirSensorDescription(
        key="so2",
        component="so2",
        device_class=SensorDeviceClass.SULPHUR_DIOXIDE,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        suggested_display_precision=1,
    ),
    # OpenWeatherMap reports CO in µg/m³. The Home Assistant CO device class
    # expects ppm, so it is left unset rather than mislabelling the unit.
    OwmAirSensorDescription(
        key="co",
        component="co",
        translation_key="carbon_monoxide",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        suggested_display_precision=1,
    ),
    OwmAirSensorDescription(
        key="nh3",
        component="nh3",
        translation_key="ammonia",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        suggested_display_precision=1,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the air quality sensors."""
    coordinator: OwmStartupCoordinator = entry.runtime_data

    entities: list[SensorEntity] = [
        OwmAirQualitySensor(coordinator, entry, description)
        for description in SENSOR_TYPES
    ]
    for day_offset in AQ_FORECAST_DAYS:
        entities.extend(
            OwmAirQualityForecastSensor(coordinator, entry, description, day_offset)
            for description in SENSOR_TYPES
        )
    async_add_entities(entities)


def _local_iso(timestamp: int) -> str:
    """Return an epoch timestamp as a local-time ISO string."""
    return dt_util.as_local(datetime.fromtimestamp(timestamp, tz=UTC)).isoformat()


def _value(entry: dict[str, Any], component: str) -> float | int | None:
    """Extract one pollutant value from an air_pollution list entry."""
    if component == AQI_KEY:
        return (entry.get("main") or {}).get("aqi")
    return (entry.get("components") or {}).get(component)


class OwmAirBaseSensor(CoordinatorEntity[OwmStartupCoordinator], SensorEntity):
    """Shared plumbing for air quality sensors."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    entity_description: OwmAirSensorDescription

    def __init__(
        self,
        coordinator: OwmStartupCoordinator,
        entry: ConfigEntry,
        description: OwmAirSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=DEVICE_MODEL,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://openweathermap.org/price",
        )


class OwmAirQualitySensor(OwmAirBaseSensor):
    """Current air quality."""

    def __init__(
        self,
        coordinator: OwmStartupCoordinator,
        entry: ConfigEntry,
        description: OwmAirSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, entry, description)
        self._attr_unique_id = f"{entry.entry_id}_air_{description.key}"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def available(self) -> bool:
        """Return whether air quality data was retrieved."""
        return super().available and self.coordinator.data.air is not None

    @property
    def native_value(self) -> float | int | None:
        """Return the current value."""
        air = self.coordinator.data.air
        if air is None:
            return None
        return _value(air, self.entity_description.component)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the qualitative label for the index sensor."""
        if self.entity_description.key != AQI_KEY:
            return None
        value = self.native_value
        return {"level": AQI_LABELS.get(value) if value is not None else None}


class OwmAirQualityForecastSensor(OwmAirBaseSensor):
    """Worst expected air quality on one local calendar day."""

    def __init__(
        self,
        coordinator: OwmStartupCoordinator,
        entry: ConfigEntry,
        description: OwmAirSensorDescription,
        day_offset: int,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, entry, description)
        self._day_offset = day_offset
        self._slug = AQ_DAY_SLUGS[day_offset]
        base = description.translation_key or description.key
        self._attr_unique_id = (
            f"{entry.entry_id}_air_forecast_{self._slug}_{description.key}"
        )
        self._attr_translation_key = f"{base}_forecast_{self._slug}"
        # No state class: a rolling forecast maximum would distort long-term
        # statistics.
        self._attr_device_class = description.device_class

    def _bounds(self) -> tuple[datetime, datetime]:
        """Return the local start and end of this window.

        Today's window starts now rather than at midnight — a peak that has
        already passed is not a forecast. Tomorrow's covers the whole day.
        """
        day_start = dt_util.start_of_local_day() + timedelta(days=self._day_offset)
        day_end = day_start + timedelta(days=1)
        if self._day_offset == 0:
            # Keep the hour already in progress: its entry started before now.
            return max(day_start, dt_util.now() - timedelta(hours=1)), day_end
        return day_start, day_end

    def _window(self) -> list[dict[str, Any]]:
        """Return the forecast entries inside this local day."""
        start, end = self._bounds()
        start_ts = start.timestamp()
        end_ts = end.timestamp()
        return [
            entry
            for entry in self.coordinator.data.air_forecast
            if entry.get("dt") is not None and start_ts <= entry["dt"] < end_ts
        ]

    @property
    def available(self) -> bool:
        """Return whether forecast data was retrieved."""
        return super().available and bool(self.coordinator.data.air_forecast)

    @property
    def native_value(self) -> float | int | None:
        """Return the peak value inside the horizon."""
        values = [
            value
            for entry in self._window()
            if (value := _value(entry, self.entity_description.component)) is not None
        ]
        return max(values) if values else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the peak time and, for the index, the full timeline."""
        component = self.entity_description.component
        window = self._window()
        peak_at: str | None = None
        peak_value: float | int | None = None
        for entry in window:
            value = _value(entry, component)
            if value is None:
                continue
            if peak_value is None or value > peak_value:
                peak_value = value
                peak_at = _local_iso(entry["dt"])

        start, end = self._bounds()
        attributes: dict[str, Any] = {
            "window": self._slug,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "peak_at": peak_at,
        }
        if component == AQI_KEY:
            attributes["level"] = (
                AQI_LABELS.get(peak_value) if peak_value is not None else None
            )
            # Timeline is exposed only on the index sensor to keep recorder
            # attribute growth bounded.
            attributes["forecast"] = [
                {
                    "datetime": _local_iso(entry["dt"]),
                    "aqi": _value(entry, AQI_KEY),
                }
                for entry in window
            ]
        return attributes
