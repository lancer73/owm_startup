"""The OpenWeatherMap Startup-plan integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_API_KEY,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import OwmApiClient
from .const import CONF_LANGUAGE, DEFAULT_LANGUAGE, DOMAIN
from .coordinator import OwmStartupCoordinator
from .redaction import unregister_secret

PLATFORMS: list[Platform] = [Platform.IMAGE, Platform.SENSOR, Platform.WEATHER]

OwmStartupConfigEntry = ConfigEntry  # typed as ConfigEntry[OwmStartupCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry."""
    options = entry.options
    client = OwmApiClient(
        session=async_get_clientsession(hass),
        api_key=entry.data[CONF_API_KEY],
        latitude=entry.data[CONF_LATITUDE],
        longitude=entry.data[CONF_LONGITUDE],
        language=options.get(
            CONF_LANGUAGE, entry.data.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
        ),
    )

    coordinator = OwmStartupCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not any(
        other.entry_id != entry.entry_id
        and other.data.get(CONF_API_KEY) == entry.data[CONF_API_KEY]
        for other in hass.config_entries.async_entries(DOMAIN)
    ):
        unregister_secret(entry.data[CONF_API_KEY])
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
