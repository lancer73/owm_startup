"""The OpenWeatherMap Startup-plan integration."""

from __future__ import annotations

import logging
from pathlib import Path
import shutil

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

_LOGGER = logging.getLogger(__name__)

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


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the data this entry left on disk.

    Captured frames are keyed by entry id, so without this a remove-and-re-add
    leaves an unreachable directory of images behind. The basemap cache is
    keyed by tile URL rather than by entry and is shared, so it only goes once
    the last entry has been removed.
    """
    await hass.async_add_executor_job(
        _remove_tree,
        Path(hass.config.path(".storage", f"{DOMAIN}_frames", entry.entry_id)),
    )

    frames_root = Path(hass.config.path(".storage", f"{DOMAIN}_frames"))
    await hass.async_add_executor_job(_remove_if_empty, frames_root)

    if not hass.config_entries.async_entries(DOMAIN):
        await hass.async_add_executor_job(
            _remove_tree, Path(hass.config.path(".storage", f"{DOMAIN}_basemap"))
        )


def _remove_tree(path: Path) -> None:
    """Delete a directory and its contents. Runs in an executor."""
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        return
    except OSError as err:
        _LOGGER.warning("Could not remove %s: %s", path, err)


def _remove_if_empty(path: Path) -> None:
    """Delete a directory only if nothing is left in it. Runs in an executor."""
    try:
        path.rmdir()
    except OSError:
        # Not empty, or not there: either is fine.
        return


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
