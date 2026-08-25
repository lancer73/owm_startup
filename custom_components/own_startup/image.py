"""Weather map images for the OpenWeatherMap Startup-plan integration.

Two image entities are created: temperature and clouds, from the Weather Maps
1.0 tile service included in the Startup plan.

Design notes:

- Tiles are fetched and composited server-side, so the API key never reaches
  the browser. A Lovelace card pointed at the tile URL would put the key in the
  dashboard config and in every client request instead.
- The basemap is static, so it is fetched once and cached on disk. The default
  is the same CARTO style the Home Assistant frontend uses; OpenStreetMap's own
  tile servers are deliberately not used, as their policy forbids distributing
  an application that fetches from them.
- A 3x3 tile grid is fetched and a 512 px window is cropped from it, centred on
  the configured coordinates. At zoom 8 that is roughly 190 km across at Dutch
  latitudes, which is the useful range for a regional weather picture.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import hashlib
import io
import logging
import math
from pathlib import Path
import time
from typing import Any

import aiohttp

from homeassistant.components.image import ImageEntity, ImageEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import legend as legend_module
from .api import REQUEST_TIMEOUT, OwmError
from .const import (
    ANIMATION_MIN_FRAMES,
    ATTRIBUTION,
    BASEMAP_MAX_AGE,
    CONF_BASEMAP_ATTRIBUTION,
    CONF_BASEMAP_URL,
    CONF_CONTRAST_STRETCH,
    CONF_LANGUAGE,
    DEFAULT_BASEMAP_ATTRIBUTION,
    DEFAULT_BASEMAP_URL,
    DEFAULT_CONTRAST_STRETCH,
    DEFAULT_LANGUAGE,
    DEVICE_MODEL,
    DOMAIN,
    FRAME_WINDOW_HOURS,
    LEGEND_HEIGHT,
    MANUFACTURER,
    MAP_GRID,
    MAP_TILE_SIZE,
    MAP_VIEW,
    MAP_ZOOM,
    SEAM_FLOOR,
    SEAM_RATIO,
    USER_AGENT,
    WIND_ARROW_BASE,
    WIND_ARROW_LAYERS,
    WIND_ARROW_MAX_MS,
    WIND_ARROW_PER_MS,
)
from .coordinator import OwmStartupCoordinator
from .frames import FrameStore, grid_hash, image_hash
from .redaction import redact

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class OwmMapDescription(ImageEntityDescription):
    """Describes a weather map image."""

    layer: str


MAP_TYPES: tuple[OwmMapDescription, ...] = (
    OwmMapDescription(
        key="temperature_map",
        layer="temp_new",
        translation_key="temperature_map",
    ),
    OwmMapDescription(
        key="clouds_map",
        layer="clouds_new",
        translation_key="clouds_map",
    ),
)


def tile_grid(
    latitude: float, longitude: float, zoom: int, grid: int
) -> tuple[int, int, float, float]:
    """Return the grid origin and the point's pixel position inside the grid.

    The origin is the top-left tile of a `grid` x `grid` block whose centre
    tile contains the point. The returned pixel position is relative to that
    block, and is what the rendered view is centred on.
    """
    scale = 2**zoom
    x = (longitude + 180.0) / 360.0 * scale
    y = (1.0 - math.asinh(math.tan(math.radians(latitude))) / math.pi) / 2.0 * scale

    half = grid // 2
    limit = scale - grid
    x0 = max(0, min(limit, math.floor(x) - half))
    y0 = max(0, min(limit, math.floor(y) - half))
    return x0, y0, (x - x0) * MAP_TILE_SIZE, (y - y0) * MAP_TILE_SIZE


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the weather map images."""
    coordinator: OwmStartupCoordinator = entry.runtime_data
    root = Path(hass.config.path(".storage", f"{DOMAIN}_frames", entry.entry_id))

    entities: list[ImageEntity] = []
    for description in MAP_TYPES:
        store = FrameStore(hass, root, description.layer)
        entities.append(OwmMapImage(coordinator, entry, description, store))
        entities.append(OwmMapAnimation(coordinator, entry, description, store))
    async_add_entities(entities)


class OwmMapImage(CoordinatorEntity[OwmStartupCoordinator], ImageEntity):
    """A weather map layer composited over a basemap."""

    _attr_attribution = ATTRIBUTION
    _attr_content_type = "image/png"
    _attr_has_entity_name = True

    entity_description: OwmMapDescription

    def __init__(
        self,
        coordinator: OwmStartupCoordinator,
        entry: ConfigEntry,
        description: OwmMapDescription,
        store: FrameStore,
    ) -> None:
        """Initialise the image entity."""
        super().__init__(coordinator)
        ImageEntity.__init__(self, coordinator.hass)
        self.entity_description = description
        self._store = store
        self._entry = entry
        self._rendered: bytes | None = None
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=DEVICE_MODEL,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://openweathermap.org/price",
        )
        x0, y0, focus_x, focus_y = tile_grid(
            entry.data[CONF_LATITUDE],
            entry.data[CONF_LONGITUDE],
            MAP_ZOOM,
            MAP_GRID,
        )
        self._origin = (x0, y0)
        self._focus = (focus_x, focus_y)
        self._attr_image_last_updated = dt_util.utcnow()

    @property
    def _language(self) -> str:
        return self._entry.options.get(
            CONF_LANGUAGE, self._entry.data.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
        )

    @property
    def _basemap_url(self) -> str:
        return self._entry.options.get(CONF_BASEMAP_URL, DEFAULT_BASEMAP_URL)

    @property
    def _contrast_stretch(self) -> bool:
        return self._entry.options.get(CONF_CONTRAST_STRETCH, DEFAULT_CONTRAST_STRETCH)

    @property
    def _basemap_attribution(self) -> str:
        return self._entry.options.get(
            CONF_BASEMAP_ATTRIBUTION, DEFAULT_BASEMAP_ATTRIBUTION
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Invalidate the rendered image and mark it as updated.

        The timestamp must change here rather than inside `async_image`: the
        frontend only refetches when it does.
        """
        self._rendered = None
        self._attr_image_last_updated = dt_util.utcnow()
        # Top up the animation in the background. Without this the sequence
        # would only fill while somebody had the map on screen.
        self.hass.async_create_task(self.async_capture_if_changed())
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        """Return the composited map as PNG bytes."""
        if self._rendered is not None:
            return self._rendered

        try:
            overlay = await self._async_fetch_grid(
                lambda x, y: self.coordinator.client.async_get_map_tile(
                    self.entity_description.layer, MAP_ZOOM, x, y
                )
            )
        except OwmError as err:
            _LOGGER.warning(
                "Could not fetch the %s map: %s",
                self.entity_description.layer,
                redact(str(err)),
            )
            return None

        basemap = await self._async_basemap()

        # When the tiles were fetched. Weather Maps 1.0 offers no validity
        # time, so this is the only honest timestamp available.
        fetched_at = dt_util.now().strftime("%d %b %H:%M")

        wind = None
        if self.entity_description.layer in WIND_ARROW_LAYERS:
            current = self.coordinator.data.current.get("wind") or {}
            speed, bearing = current.get("speed"), current.get("deg")
            if speed is not None and bearing is not None:
                wind = (speed, bearing)

        try:
            self._rendered = await self.hass.async_add_executor_job(
                self._compose,
                basemap,
                overlay,
                self._focus,
                f"{self._basemap_attribution} - {ATTRIBUTION}",
                self.entity_description.layer,
                self._contrast_stretch,
                self._language,
                fetched_at,
                wind,
            )
            # Hashing decodes nine PNGs; that does not belong on the event loop.
            frame_hash = await self.hass.async_add_executor_job(grid_hash, overlay)
            await self._store.async_add(self._rendered, frame_hash)
            # The probe compares against the centre tile, so keep it in step
            # with what was just fetched.
            centre = overlay.get((MAP_GRID // 2, MAP_GRID // 2))
            if centre is not None:
                self._store.probe_hash = await self.hass.async_add_executor_job(
                    image_hash, centre
                )
        except (OSError, ValueError) as err:
            # A tile that arrived truncated or is not a PNG at all. Pillow
            # raises out of the executor, past the fetch error handling.
            # Better a missing image than a traceback or half a map.
            _LOGGER.warning(
                "Could not decode the %s tiles: %s",
                self.entity_description.layer,
                err,
            )
            return None
        return self._rendered

    async def async_added_to_hass(self) -> None:
        """Restore the capture state recorded before the last restart."""
        await super().async_added_to_hass()
        await self._store.async_load()

    async def async_capture_if_changed(self) -> None:
        """Fetch one tile, and the rest only if the weather moved.

        Called on the coordinator's schedule so the sequence keeps filling
        while nobody is looking at the map. A no-change refresh costs one tile
        instead of nine.
        """
        x0, y0 = self._origin
        offset = MAP_GRID // 2
        try:
            probe = await self.coordinator.client.async_get_map_tile(
                self.entity_description.layer, MAP_ZOOM, x0 + offset, y0 + offset
            )
            probe_hash = await self.hass.async_add_executor_job(image_hash, probe)
            if probe_hash == self._store.probe_hash:
                return

            # Something moved: render in full; async_image stores the frame and
            # advances the probe hash. The hash is deliberately not set here:
            # if the grid fetch then fails, the next refresh must try again
            # rather than treat the missed frame as already captured.
            self._rendered = None
            await self.async_image()
        except Exception as err:  # noqa: BLE001
            # Nothing awaits this: it runs as a background task off the
            # coordinator update. An escaping exception would surface as an
            # unhandled task error and tell the user nothing useful. The
            # animation simply misses a frame.
            _LOGGER.debug(
                "Could not top up the %s sequence: %s",
                self.entity_description.layer,
                redact(f"{type(err).__name__}: {err}"),
            )

    async def _async_fetch_grid(
        self, fetch: Callable[[int, int], Awaitable[bytes]]
    ) -> dict[tuple[int, int], bytes]:
        """Fetch every tile of the grid concurrently, keyed by its position.

        Nine sequential round trips per map, three maps, would make opening a
        dashboard noticeably slow. The call count is the same either way.
        """
        x0, y0 = self._origin
        positions = [(dx, dy) for dx in range(MAP_GRID) for dy in range(MAP_GRID)]
        results = await asyncio.gather(
            *(fetch(x0 + dx, y0 + dy) for dx, dy in positions)
        )
        return dict(zip(positions, results, strict=True))

    async def _async_basemap(self) -> dict[tuple[int, int], bytes] | None:
        """Return the basemap tiles, fetching them once and caching on disk."""
        template = self._basemap_url
        if not template:
            return None

        x0, y0 = self._origin
        # The style is part of the key. Without it, switching basemaps leaves
        # previously cached tiles in place and the view comes out half light,
        # half dark.
        style = hashlib.sha256(template.encode()).hexdigest()[:12]
        root = Path(self.hass.config.path(".storage", f"{DOMAIN}_basemap"))
        cache_dir = root / style
        await self.hass.async_add_executor_job(_prune_other_styles, root, style)
        session = async_get_clientsession(self.hass)

        positions = [(dx, dy) for dx in range(MAP_GRID) for dy in range(MAP_GRID)]

        async def _tile(dx: int, dy: int) -> bytes | None:
            x, y = x0 + dx, y0 + dy
            path = cache_dir / f"{MAP_ZOOM}_{x}_{y}.png"
            data = await self.hass.async_add_executor_job(_read_cached, path)
            if data is not None:
                return data

            url = template.format(z=MAP_ZOOM, x=x, y=y, s="a")
            try:
                async with (
                    asyncio.timeout(REQUEST_TIMEOUT),
                    session.get(url, headers={"User-Agent": USER_AGENT}) as response,
                ):
                    response.raise_for_status()
                    data = await response.read()
            except (TimeoutError, aiohttp.ClientError) as err:
                _LOGGER.warning("Could not fetch basemap tile: %s", err)
                return None
            await self.hass.async_add_executor_job(_write_cached, path, data)
            return data

        results = await asyncio.gather(*(_tile(dx, dy) for dx, dy in positions))
        if any(data is None for data in results):
            return None  # a partial basemap is worse than none
        return dict(zip(positions, results, strict=True))

    @staticmethod
    def _draw_marker(canvas, x: float, y: float) -> None:
        """Mark the configured location. Runs in an executor.

        Drawn on its own layer and composited: ImageDraw replaces pixels rather
        than blending them, so a translucent halo drawn directly would punch a
        hole in the map instead of shading it.
        """
        from PIL import Image, ImageDraw

        marker = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(marker)
        x, y = round(x), round(y)

        # Dark halo first so the ring reads over pale and saturated overlays
        # alike, then the ring, then the centre dot.
        draw.ellipse((x - 9, y - 9, x + 9, y + 9), outline=(0, 0, 0, 130), width=4)
        draw.ellipse(
            (x - 8, y - 8, x + 8, y + 8), outline=(255, 255, 255, 245), width=2
        )
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(255, 255, 255, 245))
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), outline=(0, 0, 0, 130), width=1)

        canvas.alpha_composite(marker)

    @staticmethod
    def _draw_notice(canvas, text: str) -> None:
        """Draw a warning banner across the top of the map. Runs in an executor."""
        from PIL import Image, ImageDraw

        from .legend import _font

        font = _font(11)
        banner = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(banner)
        width = draw.textlength(text, font=font) + 14
        draw.rectangle((0, 0, width, 20), fill=(120, 40, 40, 210))
        draw.text((7, 4), text, font=font, fill=(255, 235, 235, 255))
        canvas.alpha_composite(banner)

    @staticmethod
    def _draw_wind(canvas, x: float, y: float, speed: float, bearing: float) -> None:
        """Draw the wind vector at the configured location.

        `bearing` is the meteorological direction the wind comes *from*. The
        arrow is drawn pointing the way the wind is going, which is the
        convention on weather maps, and starts clear of the marker so it does
        not sit under it.
        """
        from PIL import Image, ImageDraw

        from .legend import _font

        heading = math.radians((bearing + 180) % 360)
        # Screen coordinates: north is up, so y decreases going north.
        unit_x, unit_y = math.sin(heading), -math.cos(heading)

        length = WIND_ARROW_BASE + min(speed, WIND_ARROW_MAX_MS) * WIND_ARROW_PER_MS
        start_x, start_y = x + unit_x * 14, y + unit_y * 14
        end_x, end_y = x + unit_x * (14 + length), y + unit_y * (14 + length)

        arrow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(arrow)

        # Dark shaft underneath a white one, so the arrow reads on any overlay.
        draw.line((start_x, start_y, end_x, end_y), fill=(0, 0, 0, 150), width=6)
        draw.line((start_x, start_y, end_x, end_y), fill=(255, 255, 255, 245), width=2)

        head = math.radians(150)
        barbs = [
            (
                end_x + math.sin(heading + sign * head) * 11,
                end_y - math.cos(heading + sign * head) * 11,
            )
            for sign in (1, -1)
        ]
        draw.polygon(
            [(end_x, end_y), *barbs], fill=(255, 255, 255, 245), outline=(0, 0, 0, 150)
        )

        label = f"{speed:.0f} m/s"
        font = _font(11)
        text_x, text_y = end_x + unit_x * 8 - 12, end_y + unit_y * 8 - 6
        for offset_x, offset_y in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            draw.text(
                (text_x + offset_x, text_y + offset_y),
                label,
                font=font,
                fill=(0, 0, 0, 190),
            )
        draw.text((text_x, text_y), label, font=font, fill=(255, 255, 255, 245))

        canvas.alpha_composite(arrow)

    @staticmethod
    def _compose(
        basemap: dict[tuple[int, int], bytes] | None,
        overlay: dict[tuple[int, int], bytes],
        focus: tuple[float, float],
        attribution: str,
        layer: str,
        contrast_stretch: bool,
        language: str,
        fetched_at: str | None = None,
        wind: tuple[float, float] | None = None,
    ) -> bytes:
        """Stitch the grid, crop to the view and burn in the attribution.

        Runs in an executor: Pillow is blocking.
        """
        from PIL import Image

        size = MAP_TILE_SIZE * MAP_GRID
        canvas = Image.new("RGBA", (size, size), (24, 26, 28, 255))
        # Kept separately as well: the legend describes the data layer alone,
        # not the data layer blended with whatever basemap is underneath.
        overlay_only = Image.new("RGBA", (size, size), (0, 0, 0, 0))

        if basemap is not None:
            for (dx, dy), data in basemap.items():
                tile = Image.open(io.BytesIO(data)).convert("RGBA")
                canvas.alpha_composite(tile, (dx * MAP_TILE_SIZE, dy * MAP_TILE_SIZE))

        for (dx, dy), data in overlay.items():
            tile = Image.open(io.BytesIO(data)).convert("RGBA")
            overlay_only.alpha_composite(tile, (dx * MAP_TILE_SIZE, dy * MAP_TILE_SIZE))

        _log_coverage(layer, overlay)

        half = MAP_VIEW // 2
        left = round(min(max(focus[0] - half, 0), size - MAP_VIEW))
        top = round(min(max(focus[1] - half, 0), size - MAP_VIEW))
        box = (left, top, left + MAP_VIEW, top + MAP_VIEW)
        view = canvas.crop(box)
        overlay_view = overlay_only.crop(box)

        # The range is measured on the data layer alone, never on the composite:
        # matching colours through a basemap would be meaningless.
        seams: list[tuple[str, int]] = [
            (axis, boundary - offset)
            for axis, offset in (("x", left), ("y", top))
            for boundary in (MAP_TILE_SIZE, MAP_TILE_SIZE * 2)
            if 0 < boundary - offset < MAP_VIEW
        ]
        mixed_tiles = seam_mismatch(overlay_view, seams)
        if mixed_tiles:
            _LOGGER.warning(
                "The %s tiles do not line up across a tile boundary. This is an "
                "upstream artefact: OpenWeather served the grid from more than "
                "one model run. The map is labelled accordingly and should "
                "correct itself on the next update",
                layer,
            )

        bounds = legend_module.observed_range(overlay_view, layer)
        stretched = contrast_stretch and bounds is not None
        if stretched:
            overlay_view = legend_module.stretch(overlay_view, layer, bounds)
        view.alpha_composite(overlay_view, (0, 0))

        # Legend and attribution sit below the map rather than over it, so
        # nothing covers the data.
        canvas = Image.new(
            "RGBA", (MAP_VIEW, MAP_VIEW + LEGEND_HEIGHT), (24, 26, 28, 255)
        )
        canvas.alpha_composite(view, (0, 0))
        # The configured location. Normally dead centre, but not when the grid
        # was clamped at a pole or the antimeridian, so it is placed from the
        # actual crop rather than assumed.
        if mixed_tiles:
            # On the map rather than in the legend title: it is a warning about
            # the data, and the title line has no room beside the timestamp.
            OwmMapImage._draw_notice(canvas, legend_module.translate(language, "mixed"))

        marker_x, marker_y = focus[0] - left, focus[1] - top
        if wind is not None:
            OwmMapImage._draw_wind(canvas, marker_x, marker_y, *wind)
        OwmMapImage._draw_marker(canvas, marker_x, marker_y)
        legend_module.draw(
            canvas,
            layer,
            bounds,
            attribution,
            stretched=stretched,
            language=language,
            fetched_at=fetched_at,
        )

        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()


def seam_mismatch(overlay, seams: list[tuple[str, int]]) -> bool:
    """Return whether the layer steps discontinuously across a tile seam.

    OpenWeather sometimes serves a grid assembled from two model runs, leaving
    a straight brightness step along a tile boundary. Weather fields are smooth
    at this scale, so a jump at a seam that is far larger than the gradient
    just beside it means the tiles disagree rather than the weather does.
    """
    pixels = overlay.convert("RGBA")
    width, height = pixels.size

    def column_delta(x: int) -> float:
        if x < 1 or x >= width:
            return 0.0
        left, right = (
            pixels.crop((x - 1, 0, x, height)),
            pixels.crop((x, 0, x + 1, height)),
        )
        return _mean_delta(left, right)

    def row_delta(y: int) -> float:
        if y < 1 or y >= height:
            return 0.0
        above, below = (
            pixels.crop((0, y - 1, width, y)),
            pixels.crop((0, y, width, y + 1)),
        )
        return _mean_delta(above, below)

    for axis, position in seams:
        delta = column_delta(position) if axis == "x" else row_delta(position)
        neighbours = [
            column_delta(position + offset)
            if axis == "x"
            else row_delta(position + offset)
            for offset in (-4, -3, 3, 4)
        ]
        baseline = sum(neighbours) / len(neighbours) if neighbours else 0.0
        if delta > SEAM_FLOOR and delta > baseline * SEAM_RATIO:
            _LOGGER.debug(
                "Tile seam at %s=%d steps by %.1f against a local gradient of "
                "%.1f; the grid is probably mixing model runs",
                axis,
                position,
                delta,
                baseline,
            )
            return True
    return False


def _mean_delta(first, second) -> float:
    """Mean absolute RGBA difference between two equal-sized strips."""
    a, b = list(first.getdata()), list(second.getdata())
    if not a:
        return 0.0
    total = sum(
        abs(p[0] - q[0]) + abs(p[1] - q[1]) + abs(p[2] - q[2]) + abs(p[3] - q[3])
        for p, q in zip(a, b, strict=True)
    )
    return total / len(a)


def _log_coverage(layer: str, overlay: dict[tuple[int, int], bytes]) -> None:
    """Report how much of a layer is actually painted.

    The cloud layer is legitimately near-transparent when there is little to
    show, which is indistinguishable from a broken fetch by eye.
    This makes the difference visible in the debug log.
    """
    if not _LOGGER.isEnabledFor(logging.DEBUG):
        return
    from PIL import Image

    painted = 0
    total = 0
    for data in overlay.values():
        tile = Image.open(io.BytesIO(data)).convert("RGBA")
        for count, colour in tile.getcolors(tile.width * tile.height) or []:
            total += count
            if colour[3] > 0:
                painted += count
    if total:
        _LOGGER.debug(
            "Layer %s: %.1f%% of pixels carry data", layer, painted / total * 100
        )


def _read_cached(path: Path) -> bytes | None:
    """Read a cached tile, or return None if missing or stale.

    Runs in an executor.
    """
    try:
        if time.time() - path.stat().st_mtime > BASEMAP_MAX_AGE:
            return None  # basemaps are static, but not forever
        return path.read_bytes()
    except OSError:
        return None


def _prune_other_styles(root: Path, keep: str) -> None:
    """Delete cached tiles for basemap styles no longer in use.

    Runs in an executor.
    """
    try:
        for child in root.iterdir():
            if child.is_dir() and child.name != keep:
                for tile in child.iterdir():
                    tile.unlink(missing_ok=True)
                child.rmdir()
    except OSError as err:
        _LOGGER.debug("Could not prune the basemap cache: %s", err)


def _write_cached(path: Path, data: bytes) -> None:
    """Write a tile to the cache, ignoring failures. Runs in an executor."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError as err:
        _LOGGER.debug("Could not cache basemap tile: %s", err)


class OwmMapAnimation(CoordinatorEntity[OwmStartupCoordinator], ImageEntity):
    """The recent history of a layer, as an animated WebP.

    WebP rather than GIF: the stretched temperature ramp over a basemap needs
    more than 256 colours, and every current browser plays animated WebP in a
    plain img tag.
    """

    _attr_attribution = ATTRIBUTION
    _attr_content_type = "image/webp"
    _attr_has_entity_name = True

    entity_description: OwmMapDescription

    def __init__(
        self,
        coordinator: OwmStartupCoordinator,
        entry: ConfigEntry,
        description: OwmMapDescription,
        store: FrameStore,
    ) -> None:
        """Initialise the animation entity."""
        super().__init__(coordinator)
        ImageEntity.__init__(self, coordinator.hass)
        self.entity_description = description
        self._store = store
        self._rendered: bytes | None = None
        self._attr_unique_id = f"{entry.entry_id}_{description.key}_animation"
        self._attr_translation_key = f"{description.key}_animation"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=DEVICE_MODEL,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://openweathermap.org/price",
        )
        self._attr_image_last_updated = dt_util.utcnow()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Report how much history has accumulated.

        Worth surfacing: the sequence starts empty after a fresh install and
        an entity that simply shows nothing would look broken.
        """
        frames = self._store.frames()
        return {
            "frames": len(frames),
            "window_hours": FRAME_WINDOW_HOURS,
            "oldest_frame": frames[0].taken_at.isoformat() if frames else None,
            "newest_frame": frames[-1].taken_at.isoformat() if frames else None,
            # One frame renders as a still; two or more animate.
            "animating": len(frames) > 1,
            "minimum_frames": ANIMATION_MIN_FRAMES,
        }

    async def async_added_to_hass(self) -> None:
        """Follow the store, so a new frame shows up without waiting a cycle."""
        await super().async_added_to_hass()
        await self._store.async_load()
        self._store.add_listener(self._handle_new_frame)

    @callback
    def _handle_new_frame(self) -> None:
        """Rebuild on the next request and publish the new frame count."""
        self._rendered = None
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Invalidate the animation and top up the sequence."""
        self._rendered = None
        self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        """Return the animation, or None until there is enough history."""
        if self._rendered is not None:
            return self._rendered
        self._rendered = await self.hass.async_add_executor_job(
            self._store.build_animation
        )
        return self._rendered
