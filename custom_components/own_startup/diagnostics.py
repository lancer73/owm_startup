"""Diagnostics support for the OpenWeatherMap Startup-plan integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant

from .coordinator import OwmStartupCoordinator

# The key is the obvious secret. Coordinates, place names and station ids are
# personal data: a diagnostics file is often pasted into a public issue.
TO_REDACT = {
    CONF_API_KEY,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    "coord",
    "lat",
    "lon",
    "id",
    "name",
    "city",
    "timezone",
    "sunrise",
    "sunset",
    "unique_id",
    "title",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: OwmStartupCoordinator = entry.runtime_data
    data = coordinator.data

    return async_redact_data(
        {
            "entry": {
                "title": entry.title,
                "unique_id": entry.unique_id,
                "data": dict(entry.data),
                "options": dict(entry.options),
                "version": entry.version,
            },
            "coordinator": {
                "last_update_success": coordinator.last_update_success,
                "update_interval": str(coordinator.update_interval),
                "forecast_days": coordinator.forecast_days,
                "forecast_steps": coordinator.forecast_steps,
                "last_exception": (
                    str(coordinator.last_exception)
                    if coordinator.last_exception
                    else None
                ),
            },
            "counts": {
                "daily": len(data.daily) if data else 0,
                "hourly": len(data.hourly) if data else 0,
                "air_forecast": len(data.air_forecast) if data else 0,
                "air_available": bool(data and data.air),
            },
            "data": {
                "current": data.current if data else None,
                # Only the first entry of each series: enough to diagnose a
                # mapping problem without shipping a full forecast.
                "daily_first": data.daily[0] if data and data.daily else None,
                "hourly_first": data.hourly[0] if data and data.hourly else None,
                "air": data.air if data else None,
                "air_forecast_first": (
                    data.air_forecast[0] if data and data.air_forecast else None
                ),
            },
        },
        TO_REDACT,
    )
