"""Tests for what the integration leaves behind when it is removed."""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from custom_components.owm_startup import async_remove_entry
from custom_components.owm_startup.const import DOMAIN
from homeassistant.core import HomeAssistant


@pytest.fixture(autouse=True)
def clean_storage(hass: HomeAssistant):
    """Isolate these tests from each other.

    The test harness shares one config directory, so a directory left by the
    previous test would otherwise sit in the way of a removal being asserted.
    """
    yield
    for root in (_frames_root(hass), _basemap_root(hass)):
        shutil.rmtree(root, ignore_errors=True)


def _frames_root(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(".storage", f"{DOMAIN}_frames"))


def _basemap_root(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(".storage", f"{DOMAIN}_basemap"))


def _seed(directory: Path, name: str = "1700000000.000000.webp") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"not really an image")
    return path


async def test_frames_are_removed_with_the_entry(
    hass: HomeAssistant, config_entry
) -> None:
    """Frames are keyed by entry id, so they are unreachable once it is gone."""
    config_entry.add_to_hass(hass)
    mine = _frames_root(hass) / config_entry.entry_id / "temp_new"
    _seed(mine)

    await async_remove_entry(hass, config_entry)

    assert not (_frames_root(hass) / config_entry.entry_id).exists()


async def test_another_entrys_frames_are_left_alone(
    hass: HomeAssistant, config_entry
) -> None:
    """Removing one location must not wipe another's history."""
    config_entry.add_to_hass(hass)
    other = _frames_root(hass) / "some-other-entry" / "temp_new"
    _seed(other)
    _seed(_frames_root(hass) / config_entry.entry_id / "temp_new")

    await async_remove_entry(hass, config_entry)

    assert other.exists()
    assert _frames_root(hass).exists()


async def test_basemap_cache_survives_while_an_entry_remains(
    hass: HomeAssistant, config_entry
) -> None:
    """The basemap is shared, so it goes only with the last entry."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    config_entry.add_to_hass(hass)
    survivor = MockConfigEntry(domain=DOMAIN, title="Elsewhere", unique_id="1-1")
    survivor.add_to_hass(hass)

    tile = _seed(_basemap_root(hass) / "abc123", "8_130_83.png")
    await async_remove_entry(hass, config_entry)

    assert tile.exists()


async def test_basemap_cache_goes_with_the_last_entry(
    hass: HomeAssistant, config_entry
) -> None:
    """Nothing should be left on disk once the integration is gone."""
    config_entry.add_to_hass(hass)
    _seed(_basemap_root(hass) / "abc123", "8_130_83.png")
    _seed(_frames_root(hass) / config_entry.entry_id / "temp_new")

    await hass.config_entries.async_remove(config_entry.entry_id)
    await async_remove_entry(hass, config_entry)

    assert not _basemap_root(hass).exists()
    assert not _frames_root(hass).exists()


async def test_removal_survives_missing_directories(
    hass: HomeAssistant, config_entry
) -> None:
    """An entry that never rendered a map has nothing to clean up."""
    config_entry.add_to_hass(hass)
    await async_remove_entry(hass, config_entry)  # must not raise
