"""Tests for the weather map image entities."""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest

from custom_components.owm_startup.api import OwmConnectionError
from custom_components.owm_startup.const import CONF_BASEMAP_URL
from custom_components.owm_startup.image import tile_grid
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

TEMPERATURE = "image.zoetermeer_temperature_map"


def _png(colour: tuple[int, int, int, int]) -> bytes:
    """Return a single-tile PNG of one colour."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGBA", (256, 256), colour).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def mock_tiles(mock_api):
    """Serve fake weather tiles and basemap tiles."""
    weather = _png((255, 0, 0, 128))
    basemap = _png((200, 200, 200, 255))

    class _Response:
        """Stands in for aiohttp's request context manager."""

        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return

        @staticmethod
        def raise_for_status() -> None:
            return

        @staticmethod
        async def read() -> bytes:
            return basemap

    def _get(*args, **kwargs):
        return _Response()

    with (
        patch(
            "custom_components.owm_startup.api.OwmApiClient.async_get_map_tile",
            return_value=weather,
        ) as tile_mock,
        # Patch the name the module imported, not the helper's home: the
        # module-level import binds it at import time.
        patch(
            "custom_components.owm_startup.image.async_get_clientsession"
        ) as session_mock,
    ):
        session_mock.return_value.get = _get
        yield tile_mock


def test_point_sits_in_the_centre_tile() -> None:
    """The 3x3 grid is built around the tile containing the point."""
    x0, y0, focus_x, focus_y = tile_grid(52.06, 4.49, 8, 3)
    assert (x0, y0) == (130, 83)
    # The point falls inside the middle tile of the block.
    assert 256 <= focus_x < 512
    assert 256 <= focus_y < 512


def test_crop_window_is_centred_on_the_point() -> None:
    """The cropped view puts the point at its centre, not in a corner."""
    from custom_components.owm_startup.const import MAP_GRID, MAP_TILE_SIZE, MAP_VIEW

    _, _, focus_x, focus_y = tile_grid(52.06, 4.49, 8, MAP_GRID)
    size = MAP_TILE_SIZE * MAP_GRID
    left = round(min(max(focus_x - MAP_VIEW // 2, 0), size - MAP_VIEW))
    top = round(min(max(focus_y - MAP_VIEW // 2, 0), size - MAP_VIEW))

    # Where the point ends up inside the 512 px view.
    assert abs((focus_x - left) - MAP_VIEW // 2) < 1
    assert abs((focus_y - top) - MAP_VIEW // 2) < 1


def test_grid_clamps_at_the_edges() -> None:
    """Grids near the antimeridian or poles stay in range."""
    assert tile_grid(85.0, -180.0, 8, 3)[:2] == (0, 0)
    assert tile_grid(-85.0, 179.9, 8, 3)[:2] == (253, 253)


async def test_maps_created(hass: HomeAssistant, setup_integration, mock_tiles) -> None:
    """Temperature and cloud images exist; precipitation was removed."""
    for key in ("temperature_map", "cloud_map"):
        assert hass.states.get(f"image.zoetermeer_{key}") is not None
    assert hass.states.get("image.zoetermeer_precipitation_map") is None


async def test_image_is_rendered_png(
    hass: HomeAssistant, hass_client, setup_integration, mock_tiles
) -> None:
    """The entity serves a composited PNG of the full grid."""
    client = await hass_client()
    state = hass.states.get(TEMPERATURE)
    response = await client.get(state.attributes["entity_picture"])
    assert response.status == 200
    body = await response.read()
    assert body.startswith(b"\x89PNG")

    from PIL import Image

    from custom_components.owm_startup.const import LEGEND_HEIGHT, MAP_VIEW

    image = Image.open(io.BytesIO(body))
    assert image.size == (MAP_VIEW, MAP_VIEW + LEGEND_HEIGHT)


async def test_api_key_not_in_state(
    hass: HomeAssistant, setup_integration, mock_tiles
) -> None:
    """The tile URL, and therefore the key, never reaches the frontend."""
    state = hass.states.get(TEMPERATURE)
    assert setup_integration.data["api_key"] not in str(state.attributes)
    assert "appid" not in str(state.attributes)


async def test_timestamp_updates_on_refresh(
    hass: HomeAssistant, setup_integration, mock_tiles
) -> None:
    """image_last_updated moves on coordinator refresh, forcing a refetch."""
    before = hass.states.get(TEMPERATURE).state
    await hass.async_block_till_done()

    with patch(
        "homeassistant.util.dt.utcnow",
        return_value=dt_util.utcnow() + __import__("datetime").timedelta(hours=1),
    ):
        await setup_integration.runtime_data.async_refresh()
        await hass.async_block_till_done()

    assert hass.states.get(TEMPERATURE).state != before


async def test_tile_failure_yields_no_image(
    hass: HomeAssistant, hass_client, setup_integration, mock_tiles
) -> None:
    """A failing tile fetch returns no image rather than a broken one."""
    mock_tiles.side_effect = OwmConnectionError("boom")
    client = await hass_client()
    state = hass.states.get(TEMPERATURE)
    response = await client.get(state.attributes["entity_picture"])
    assert response.status == 500


async def test_basemap_can_be_disabled(
    hass: HomeAssistant, hass_client, config_entry, mock_api, mock_tiles
) -> None:
    """With no basemap configured the overlay is still served."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options={CONF_BASEMAP_URL: ""})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    client = await hass_client()
    state = hass.states.get(TEMPERATURE)
    response = await client.get(state.attributes["entity_picture"])
    assert response.status == 200
    assert (await response.read()).startswith(b"\x89PNG")


def test_every_layer_has_a_legend() -> None:
    """Each rendered layer must have a documented colour scale."""
    from custom_components.owm_startup.const import LEGENDS
    from custom_components.owm_startup.image import MAP_TYPES
    from custom_components.owm_startup.legend import TRANSLATIONS

    for description in MAP_TYPES:
        assert description.layer in LEGENDS
        legend = LEGENDS[description.layer]
        assert legend["unit"]
        # Every rendered layer needs a name in every supported language.
        for language, table in TRANSLATIONS.items():
            assert description.layer in table, (description.layer, language)
        assert len(legend["stops"]) >= 2
        # Stops must be numeric and ascending for range detection to work.
        values = [value for value, _ in legend["stops"]]
        assert values == sorted(values)


def test_default_basemap_is_dark_and_not_openstreetmap() -> None:
    """The default must suit the overlays and respect the OSM tile policy."""
    from custom_components.owm_startup.const import DEFAULT_BASEMAP_URL

    assert "dark" in DEFAULT_BASEMAP_URL
    assert "tile.openstreetmap.org" not in DEFAULT_BASEMAP_URL


def test_legend_is_drawn_below_the_map(hass: HomeAssistant) -> None:
    """The legend strip sits under the map, not over the data."""
    from PIL import Image

    from custom_components.owm_startup.const import LEGEND_HEIGHT, MAP_VIEW
    from custom_components.owm_startup.image import OwmMapImage

    tiles = {(dx, dy): _png((10, 200, 10, 255)) for dx in range(3) for dy in range(3)}
    rendered = OwmMapImage._compose(
        tiles, tiles, (384.0, 384.0), "attr", "temp_new", False, "en"
    )
    image = Image.open(io.BytesIO(rendered)).convert("RGBA")

    assert image.size == (MAP_VIEW, MAP_VIEW + LEGEND_HEIGHT)
    # Bottom row of the map area is still map, first row below it is not.
    assert image.getpixel((256, MAP_VIEW - 1)) == (10, 200, 10, 255)
    assert image.getpixel((256, MAP_VIEW + 1)) != (10, 200, 10, 255)


def test_contrast_stretch_changes_the_render(hass: HomeAssistant) -> None:
    """The stretch option actually alters the pixels it is applied to."""
    from PIL import Image

    from custom_components.owm_startup.const import LEGENDS
    from custom_components.owm_startup.image import OwmMapImage
    from custom_components.owm_startup.legend import colour_at

    stops = LEGENDS["temp_new"]["stops"]

    def gradient(dx: int) -> bytes:
        """Return a tile spanning three degrees, as a 200 km view really does."""
        image = Image.new("RGBA", (256, 256))
        pixels = image.load()
        for x in range(256):
            colour = colour_at(stops, 17.0 + 3.0 * ((dx * 256 + x) / 768))
            for y in range(256):
                pixels[x, y] = colour
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    tiles = {(dx, dy): gradient(dx) for dx in range(3) for dy in range(3)}
    plain = OwmMapImage._compose(
        None, tiles, (384.0, 384.0), "attr", "temp_new", False, "en"
    )
    boosted = OwmMapImage._compose(
        None, tiles, (384.0, 384.0), "attr", "temp_new", True, "en"
    )

    assert plain != boosted

    def spread(data: bytes) -> int:
        """Return how far the map colours travel, away from the marker.

        The location marker is black and white, so measuring across the whole
        view would report its contrast rather than the layer's.
        """
        # Full width so the whole gradient is covered, but above the marker.
        image = Image.open(io.BytesIO(data)).convert("RGB").crop((0, 0, 512, 200))
        colours = [colour for _count, colour in image.getcolors(maxcolors=1 << 20)]
        return max(
            max(colour[channel] for colour in colours)
            - min(colour[channel] for colour in colours)
            for channel in range(3)
        )

    # Three degrees of OpenWeather palette is nearly one colour; stretched it
    # should cross a good part of the ramp. The threshold allows for the
    # overlay opacity, which is deliberately low so the basemap reads through:
    # raising it would make this test pass and the maps worse.
    assert spread(plain) < 60
    assert spread(boosted) > 75


async def test_map_tiles_are_fetched_concurrently(
    hass: HomeAssistant, setup_integration, mock_tiles
) -> None:
    """Nine tiles must not be nine sequential round trips."""
    import asyncio

    in_flight = 0
    peak = 0
    tile = _png((255, 0, 0, 128))

    async def _slow_tile(*args, **kwargs):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0)
            return tile
        finally:
            in_flight -= 1

    mock_tiles.side_effect = _slow_tile
    entity = hass.data["image"].get_entity(TEMPERATURE)
    entity._rendered = None
    await entity.async_image()

    assert mock_tiles.call_count == 9
    assert peak > 1, "tiles were fetched one at a time"


async def test_basemap_failure_falls_back_to_the_overlay(
    hass: HomeAssistant, hass_client, config_entry, mock_api
) -> None:
    """A basemap that will not load must not take the weather map with it."""
    import aiohttp

    weather = _png((255, 0, 0, 128))

    def _failing_get(*args, **kwargs):
        raise aiohttp.ClientConnectionError("basemap host unreachable")

    with (
        patch(
            "custom_components.owm_startup.api.OwmApiClient.async_get_map_tile",
            return_value=weather,
        ),
        patch(
            "custom_components.owm_startup.image.async_get_clientsession"
        ) as session_mock,
    ):
        session_mock.return_value.get = _failing_get
        config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        client = await hass_client()
        state = hass.states.get(TEMPERATURE)
        response = await client.get(state.attributes["entity_picture"])

    assert response.status == 200
    assert (await response.read()).startswith(b"\x89PNG")


async def test_render_carries_a_fetch_timestamp(
    hass: HomeAssistant, setup_integration, mock_tiles
) -> None:
    """Two renders an hour apart must not be byte-identical.

    The timestamp is what distinguishes them; without it a stale image would
    be indistinguishable from a fresh one.
    """
    from datetime import timedelta

    entity = hass.data["image"].get_entity(TEMPERATURE)
    first = await entity.async_image()

    with patch(
        "custom_components.owm_startup.image.dt_util.now",
        return_value=dt_util.now() + timedelta(hours=1),
    ):
        entity._rendered = None
        second = await entity.async_image()

    assert first != second


async def test_marker_is_drawn_at_the_configured_location(
    hass: HomeAssistant, setup_integration, mock_tiles
) -> None:
    """The marker sits at the centre of the view, over the coordinates."""
    from PIL import Image

    from custom_components.owm_startup.const import MAP_VIEW

    entity = hass.data["image"].get_entity(TEMPERATURE)
    entity._rendered = None
    image = Image.open(io.BytesIO(await entity.async_image())).convert("RGBA")

    centre = MAP_VIEW // 2
    # The ring is bright; the flat test tiles are not.
    ring = [
        image.getpixel((centre + dx, centre + dy))
        for dx, dy in ((0, -8), (0, 8), (-8, 0), (8, 0))
    ]
    assert any(pixel[0] > 200 and pixel[1] > 200 for pixel in ring), ring

    # Well away from the marker the map is untouched.
    assert image.getpixel((40, 40))[:3] != (255, 255, 255)


def test_marker_layer_does_not_punch_a_hole(hass: HomeAssistant) -> None:
    """The halo must shade the map, not make it transparent.

    ImageDraw replaces pixels rather than blending, so drawing a translucent
    halo straight onto the canvas would leave holes.
    """
    from PIL import Image

    from custom_components.owm_startup.image import OwmMapImage

    canvas = Image.new("RGBA", (64, 64), (10, 120, 10, 255))
    OwmMapImage._draw_marker(canvas, 32, 32)

    alphas = [pixel[3] for pixel in canvas.getdata()]
    assert min(alphas) == 255


async def test_obsolete_precipitation_entity_is_removed(
    hass: HomeAssistant, config_entry, mock_api, mock_tiles
) -> None:
    """An entity left over from an earlier version must not linger.

    Without this it would sit in the registry as permanently unavailable, with
    no obvious way for the user to tell a removed feature from a broken one.
    """
    from custom_components.owm_startup.const import DOMAIN
    from homeassistant.const import Platform
    from homeassistant.helpers import entity_registry as er

    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    stale = registry.async_get_or_create(
        Platform.IMAGE,
        DOMAIN,
        f"{config_entry.entry_id}_precipitation_map",
        config_entry=config_entry,
        suggested_object_id="zoetermeer_precipitation_map",
    )
    assert registry.async_get(stale.entity_id) is not None

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get(stale.entity_id) is None


CLOUDS = "image.zoetermeer_cloud_map"


def _wind_render(wind, layer="clouds_new"):
    """Compose a flat map with the given wind vector."""
    from custom_components.owm_startup.image import OwmMapImage

    tiles = {(dx, dy): _png((40, 40, 40, 255)) for dx in range(3) for dy in range(3)}
    return OwmMapImage._compose(
        tiles, tiles, (384.0, 384.0), "attr", layer, False, "en", None, wind
    )


def test_wind_arrow_points_downwind(hass: HomeAssistant) -> None:
    """A southwesterly wind travels northeast, so the arrow points up-right.

    `deg` is the direction the wind comes from; drawing it that way round is
    the classic mistake.
    """
    from PIL import Image

    from custom_components.owm_startup.const import MAP_VIEW

    image = Image.open(io.BytesIO(_wind_render((10.0, 225.0)))).convert("RGB")
    centre = MAP_VIEW // 2

    def brightness(dx: int, dy: int) -> int:
        box = image.crop(
            (centre + dx - 20, centre + dy - 20, centre + dx + 20, centre + dy + 20)
        )
        return max(sum(pixel) for pixel in box.getdata())

    # Up and to the right of the marker, not down and to the left.
    assert brightness(35, -35) > brightness(-35, 35)


def test_wind_arrow_length_tracks_speed(hass: HomeAssistant) -> None:
    """A stronger wind draws a longer arrow."""
    from PIL import Image

    def bright_pixels(data: bytes) -> int:
        image = Image.open(io.BytesIO(data)).convert("RGB").crop((0, 0, 512, 512))
        return sum(1 for pixel in image.getdata() if sum(pixel) > 600)

    assert bright_pixels(_wind_render((18.0, 90.0))) > bright_pixels(
        _wind_render((2.0, 90.0))
    )


def test_wind_arrow_is_scoped_to_the_configured_layers(hass: HomeAssistant) -> None:
    """Only the cloud map asks for a wind vector."""
    from custom_components.owm_startup.const import WIND_ARROW_LAYERS

    assert WIND_ARROW_LAYERS == ("clouds_new",)
    assert _wind_render((10.0, 225.0)) != _wind_render(None)


async def test_wind_arrow_absent_without_wind_data(
    hass: HomeAssistant, config_entry, mock_api, mock_tiles
) -> None:
    """A payload without wind must not break the render."""
    mock_api["current"]["wind"] = {}
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    entity = hass.data["image"].get_entity(CLOUDS)
    assert (await entity.async_image()).startswith(b"\x89PNG")
