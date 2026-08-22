"""Data update coordinator for the OpenWeatherMap Startup-plan integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import OwmApiClient, OwmAuthError, OwmError, OwmRateLimitError
from .const import DOMAIN, FORECAST_DAYS, FORECAST_STEPS, SCAN_INTERVAL_MINUTES

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class OwmData:
    """Payload shared with all platforms."""

    current: dict[str, Any]
    daily: list[dict[str, Any]]
    hourly: list[dict[str, Any]]
    air: dict[str, Any] | None
    air_forecast: list[dict[str, Any]]


class OwmStartupCoordinator(DataUpdateCoordinator[OwmData]):
    """Fetch all endpoints in one pass."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: OwmApiClient,
    ) -> None:
        """Initialise the coordinator."""
        self.client = client
        self.forecast_days = FORECAST_DAYS
        self.forecast_steps = FORECAST_STEPS
        self._air_warned = False
        self._hourly_warned = False
        self._quota_warned = False
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(minutes=SCAN_INTERVAL_MINUTES),
        )

    async def _async_update_data(self) -> OwmData:
        """Fetch weather and air quality data."""
        current, daily, hourly, air, air_forecast = await asyncio.gather(
            self.client.async_get_current(),
            self.client.async_get_daily_forecast(self.forecast_days),
            self.client.async_get_hourly_forecast(self.forecast_steps),
            self.client.async_get_air_pollution(),
            self.client.async_get_air_pollution_forecast(),
            return_exceptions=True,
        )

        # A quota failure anywhere means the account is over its allowance, so
        # say so plainly instead of letting it read as a generic fetch error.
        # It is reported once per episode: the coordinator retries on its own.
        rate_limited = next(
            (
                result
                for result in (current, daily, hourly, air, air_forecast)
                if isinstance(result, OwmRateLimitError)
            ),
            None,
        )
        if rate_limited is not None:
            if not self._quota_warned:
                _LOGGER.warning(
                    "OpenWeatherMap call allowance exceeded; data will be stale "
                    "until it resets: %s",
                    rate_limited,
                )
                self._quota_warned = True
            raise UpdateFailed(str(rate_limited))
        self._quota_warned = False

        if isinstance(hourly, OwmAuthError):
            raise ConfigEntryAuthFailed(str(hourly))

        for result in (current, daily):
            if isinstance(result, OwmAuthError):
                raise ConfigEntryAuthFailed(str(result))
            if isinstance(result, Exception):
                raise UpdateFailed(str(result)) from result

        # Air quality is treated as non-fatal: a failure there should not take
        # the weather entity offline.
        air_data: dict[str, Any] | None = None
        air_forecast_data: list[dict[str, Any]] = []
        hourly_data: list[dict[str, Any]] = []

        # The 3-hourly forecast is likewise non-fatal: the daily forecast
        # remains usable without it.
        if isinstance(hourly, Exception):
            if not self._hourly_warned:
                _LOGGER.warning("3-hourly forecast unavailable: %s", hourly)
                self._hourly_warned = True
        else:
            self._hourly_warned = False
            hourly_data = hourly.get("list") or []

        if isinstance(air, Exception) or isinstance(air_forecast, Exception):
            if not self._air_warned:
                failure = air if isinstance(air, Exception) else air_forecast
                if isinstance(failure, OwmAuthError):
                    # Deliberately not ConfigEntryAuthFailed. The weather
                    # endpoints authenticated on this same key moments ago, so
                    # the key is valid; taking the whole entry down and asking
                    # for reauth would be the wrong diagnosis and would lose
                    # the forecast too.
                    _LOGGER.warning(
                        "Air quality endpoints rejected this key while the "
                        "weather endpoints accepted it, so air quality sensors "
                        "will be unavailable: %s",
                        failure,
                    )
                else:
                    _LOGGER.warning("Air quality data unavailable: %s", failure)
                self._air_warned = True
        else:
            self._air_warned = False

        if not isinstance(air, Exception):
            entries = air.get("list") or []
            air_data = entries[0] if entries else None
        if not isinstance(air_forecast, Exception):
            air_forecast_data = air_forecast.get("list") or []

        if isinstance(daily, OwmError):  # defensive, handled above
            raise UpdateFailed(str(daily))

        return OwmData(
            current=current,
            daily=daily.get("list") or [],
            hourly=hourly_data,
            air=air_data,
            air_forecast=air_forecast_data,
        )
