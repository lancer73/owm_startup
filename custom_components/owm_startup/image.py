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
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import legend as legend_module
from .api import REQUEST_TIMEOUT, OwmError
from .const import (
    ANIMATION_MIN_FRAMES,
    ATTRIBUTION,
    BASEMAP_ATTRIBUTION,
    BASEMAP_CACHE_STYLE,
    BASEMAP_MAX_AGE,
    BASEMAP_PATH,
    CONF_CONTRAST_STRETCH_CLOUDS,
    CONF_CONTRAST_STRETCH_TEMPERATURE,
    CONF_LANGUAGE,
    DARKEN_MATRIX,
    DEFAULT_CONTRAST_STRETCH_CLOUDS,
    DEFAULT_CONTRAST_STRETCH_TEMPERATURE,
    DEFAULT_LANGUAGE,
    DEVICE_MODEL,
    DOMAIN,
    FRAME_WINDOW_HOURS,
    HA_TILE_PROXY_DOMAIN,
    LEGEND_HEIGHT,
    MANUFACTURER,
    MAP_GRID,
    MAP_TILE_SIZE,
    MAP_VIEW,
    MAP_ZOOM,
    MIXED_TILE_MAX_RETRIES,
    RENDER_REVISION,
    SEAM_FLOOR,
    SEAM_RATIO,
    SEAM_SEGMENTS,
    USER_AGENT,
    WIND_ARROW_BASE,
    WIND_ARROW_LAYERS,
    WIND_ARROW_MAX_MS,
    WIND_ARROW_PER_MS,
)
from .coordinator import OwmStartupCoordinator
from .frames import FrameStore, grid_hash, image_hash
from .redaction import redact, scrub_query_secrets

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class Render:
    """A composed map, and whether its tiles disagreed across a seam."""

    image: bytes
    mixed_tiles: bool


@dataclass(frozen=True, kw_only=True)
class OwmMapDescription(ImageEntityDescription):
    """Describes a weather map image."""

    layer: str
    stretch_option: str
    stretch_default: bool
    # Widen the stretch to cover the day's forecast range as well as what is
    # in view, so consecutive frames share a scale and can be compared.
    daily_range: bool = False


MAP_TYPES: tuple[OwmMapDescription, ...] = (
    OwmMapDescription(
        key="temperature_map",
        layer="temp_new",
        translation_key="temperature_map",
        stretch_option=CONF_CONTRAST_STRETCH_TEMPERATURE,
        stretch_default=DEFAULT_CONTRAST_STRETCH_TEMPERATURE,
        daily_range=True,
    ),
    OwmMapDescription(
        key="clouds_map",
        layer="clouds_new",
        translation_key="clouds_map",
        stretch_option=CONF_CONTRAST_STRETCH_CLOUDS,
        stretch_default=DEFAULT_CONTRAST_STRETCH_CLOUDS,
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
        # The stretch setting changes how a frame looks as much as a palette
        # change does, so it belongs in the signature alongside the revision.
        stretch = entry.options.get(
            description.stretch_option, description.stretch_default
        )
        store = FrameStore(
            hass,
            root,
            description.layer,
            f"{RENDER_REVISION}:{'stretched' if stretch else 'plain'}",
        )
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
        # One render at a time per layer: the frontend and the background
        # capture both call async_image, and each render is nine tile fetches.
        self._render_lock = asyncio.Lock()
        self._capturing = False
        self._retries = 0
        # Set when a grid came back mixed, so the next scheduled refresh
        # re-renders it instead of trusting an unchanged probe tile.
        self._force_next = False
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

    def _reference_bounds(self) -> tuple[float, float] | None:
        """Return today's forecast low and high, in the layer's own units.

        Fitting each frame to its own observed range makes the colours mean
        something different from one frame to the next, which is exactly what
        an animation should not do. Anchoring to the day's forecast range gives
        a scale that only moves when the forecast does.
        """
        if not self.entity_description.daily_range:
            return None
        daily = self.coordinator.data.daily
        if not daily:
            return None
        temperatures = daily[0].get("temp") or {}
        low, high = temperatures.get("min"), temperatures.get("max")
        if low is None or high is None:
            return None
        return float(low), float(high)

    @property
    def _contrast_stretch(self) -> bool:
        """Return whether this layer is stretched, per its own option."""
        return self._entry.options.get(
            self.entity_description.stretch_option,
            self.entity_description.stretch_default,
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
        # would only fill while somebody had the map on screen. Registered
        # against the config entry so it is cancelled on unload rather than
        # left running against a torn-down entity.
        force = self._force_next
        self._force_next = False
        self.coordinator.config_entry.async_create_background_task(
            self.hass,
            self.async_capture_if_changed(force=force),
            name=f"{DOMAIN} capture {self.entity_description.layer}",
        )
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        """Return the composited map as PNG bytes.

        Guarded by a lock so a frontend request arriving while a background
        capture is mid-flight waits for that render instead of fetching a
        second grid of its own.
        """
        if self._rendered is not None:
            return self._rendered

        async with self._render_lock:
            # Another caller may have rendered while this one waited.
            if self._rendered is not None:
                return self._rendered
            return await self._async_render()

    async def _async_render(self) -> bytes | None:
        """Fetch the tiles and compose the map. Callers hold the render lock."""
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
            render = await self.hass.async_add_executor_job(
                self._compose,
                basemap,
                overlay,
                self._focus,
                f"{BASEMAP_ATTRIBUTION} - {ATTRIBUTION}",
                self.entity_description.layer,
                self._contrast_stretch,
                self._language,
                fetched_at,
                wind,
                self._reference_bounds(),
            )
            self._rendered = render.image

            if render.mixed_tiles and self._retries < MIXED_TILE_MAX_RETRIES:
                # Show it -- it is mostly right -- but do not commit it to the
                # sequence, where it would flicker for twelve hours. The hashes
                # are left untouched so the retry re-renders from scratch.
                self._retries += 1
                _LOGGER.debug(
                    "The %s grid mixes model runs; not capturing it, and "
                    "forcing a re-render on the next refresh (attempt %d of %d)",
                    self.entity_description.layer,
                    self._retries,
                    MIXED_TILE_MAX_RETRIES,
                    RENDER_REVISION,
                )
                self._force_next = True
                return self._rendered

            if render.mixed_tiles:
                _LOGGER.warning(
                    "The %s grid is still mixed after %d retries; storing it "
                    "anyway rather than starving the sequence",
                    self.entity_description.layer,
                    self._retries,
                )
            self._retries = 0
            self._force_next = False

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

    async def async_capture_if_changed(self, force: bool = False) -> None:
        """Fetch one tile, and the rest only if the weather moved.

        Called on the coordinator's schedule so the sequence keeps filling
        while nobody is looking at the map. A no-change refresh costs one tile
        instead of nine.
        """
        if self._capturing:
            # A capture is already running. Queueing another would repeat work
            # that is about to complete, and on a slow API the two could
            # overlap indefinitely.
            _LOGGER.debug(
                "Capture for %s already in progress; skipping",
                self.entity_description.layer,
            )
            return

        self._capturing = True
        x0, y0 = self._origin
        offset = MAP_GRID // 2
        try:
            if not force:
                # A retry skips the probe: the mismatch may have been in a tile
                # the probe does not cover, so an unchanged probe proves
                # nothing about the rest of the grid.
                probe = await self.coordinator.client.async_get_map_tile(
                    self.entity_description.layer,
                    MAP_ZOOM,
                    x0 + offset,
                    y0 + offset,
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
        finally:
            self._capturing = False

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

    def _resolve_tile_url(self, url: str) -> str | None:
        """Expand the basemap path against this instance.

        The proxy takes its access token in the query string and rotates it
        every thirty minutes, so the token is added per request.
        """
        tokens = self.hass.data.get(HA_TILE_PROXY_DOMAIN)
        if not tokens:
            _LOGGER.warning(
                "The basemap points at %s but the Home Assistant map tiles "
                "integration is not loaded, so no basemap will be drawn",
                url,
            )
            return None

        try:
            base = get_url(self.hass, allow_external=False, prefer_external=False)
        except NoURLAvailableError:
            _LOGGER.warning(
                "No internal URL is configured, so the Home Assistant tile "
                "proxy cannot be reached and no basemap will be drawn"
            )
            return None

        separator = "&" if "?" in url else "?"
        return f"{base}{url}{separator}token={tokens[-1]}"

    async def _async_basemap(self) -> dict[tuple[int, int], bytes] | None:
        """Return the basemap tiles, fetching them once and caching on disk."""
        x0, y0 = self._origin
        root = Path(self.hass.config.path(".storage", f"{DOMAIN}_basemap"))
        cache_dir = root / BASEMAP_CACHE_STYLE
        # Still pruned: earlier versions cached under a per-provider directory,
        # and those tiles are unreachable now.
        await self.hass.async_add_executor_job(
            _prune_other_styles, root, BASEMAP_CACHE_STYLE
        )
        session = async_get_clientsession(self.hass)

        positions = [(dx, dy) for dx in range(MAP_GRID) for dy in range(MAP_GRID)]

        async def _tile(dx: int, dy: int) -> bytes | None:
            x, y = x0 + dx, y0 + dy
            path = cache_dir / f"{MAP_ZOOM}_{x}_{y}.png"
            data = await self.hass.async_add_executor_job(_read_cached, path)
            if data is not None:
                return data

            url = self._resolve_tile_url(BASEMAP_PATH.format(z=MAP_ZOOM, x=x, y=y))
            if url is None:
                return None
            try:
                async with (
                    asyncio.timeout(REQUEST_TIMEOUT),
                    session.get(url, headers={"User-Agent": USER_AGENT}) as response,
                ):
                    response.raise_for_status()
                    data = await response.read()
            except (TimeoutError, aiohttp.ClientError) as err:
                # Scrubbed: an aiohttp error quotes the URL it failed on, and
                # that URL carries the proxy token or a provider key.
                _LOGGER.warning(
                    "Could not fetch basemap tile: %s", scrub_query_secrets(str(err))
                )
                return None

            # The proxy serves the light OpenStreetMap style; these maps need a
            # dark backdrop. Darkened before caching, so it happens once per
            # tile rather than on every render.
            data = await self.hass.async_add_executor_job(_darken, data)
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
        reference: tuple[float, float] | None = None,
    ) -> Render:
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
        bounds = legend_module.observed_range(overlay_view, layer)
        # Union with the day's forecast range, so the scale holds still between
        # frames while never clipping something that is actually in view.
        day_scaled = False
        if reference is not None:
            bounds = (
                (min(bounds[0], reference[0]), max(bounds[1], reference[1]))
                if bounds is not None
                else reference
            )
            day_scaled = True
        stretched = contrast_stretch and bounds is not None
        if stretched:
            overlay_view = legend_module.stretch(overlay_view, layer, bounds)

        # Checked after the stretch, on the pixels the reader actually sees.
        # A one-step difference in OpenWeather's palette is invisible in raw
        # form but glaring once the range is spread across a full ramp, and
        # that is precisely the case that kept slipping through.
        mixed_tiles = seam_mismatch(overlay_view, seams)
        if mixed_tiles:
            _LOGGER.warning(
                "The %s tiles do not line up across a tile boundary. This is "
                "an upstream artefact: OpenWeather served the grid from more "
                "than one model run. The map is labelled accordingly and "
                "should correct itself on the next update",
                layer,
            )

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
            day_scaled=day_scaled,
        )

        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG", optimize=True)
        return Render(buffer.getvalue(), mixed_tiles)


def seam_mismatch(overlay, seams: list[tuple[str, int]]) -> bool:
    """Return whether the layer steps discontinuously across a tile seam.

    OpenWeather sometimes serves a grid assembled from two model runs, leaving
    a straight step along a tile boundary. Weather fields are smooth at this
    scale, so a jump at a seam far larger than the gradient just beside it
    means the tiles disagree rather than the weather does.

    Each seam is checked in segments. Two model runs differ only where the
    weather is doing something, so a step typically covers part of a boundary
    and would be averaged away by the identical remainder if the whole line
    were taken at once.
    """
    pixels = overlay.convert("RGBA")
    width, height = pixels.size

    def delta(axis: str, position: int, low: int, high: int) -> float:
        """Mean absolute difference across one seam, over one segment."""
        if axis == "x":
            if position < 1 or position >= width:
                return 0.0
            before = pixels.crop((position - 1, low, position, high))
            after = pixels.crop((position, low, position + 1, high))
        else:
            if position < 1 or position >= height:
                return 0.0
            before = pixels.crop((low, position - 1, high, position))
            after = pixels.crop((low, position, high, position + 1))
        return _mean_delta(before, after)

    for axis, position in seams:
        length = height if axis == "x" else width
        span = max(1, length // SEAM_SEGMENTS)
        for low in range(0, length, span):
            high = min(low + span, length)
            step = delta(axis, position, low, high)
            if step <= SEAM_FLOOR:
                continue
            neighbours = [
                delta(axis, position + offset, low, high) for offset in (-4, -3, 3, 4)
            ]
            baseline = sum(neighbours) / len(neighbours)
            if step > baseline * SEAM_RATIO:
                _LOGGER.debug(
                    "Tile seam at %s=%d steps by %.1f over %d-%d against a "
                    "local gradient of %.1f; the grid is probably mixing "
                    "model runs",
                    axis,
                    position,
                    step,
                    low,
                    high,
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


def _darken(data: bytes) -> bytes:
    """Return a light basemap tile as a dark, grey one. Runs in an executor.

    Inversion turns land dark but swings every hue to its opposite; rotating
    180 degrees puts them back. The result is then desaturated, so the weather
    overlay is the only thing on the image carrying colour.
    """
    from PIL import Image, ImageChops, ImageOps

    with Image.open(io.BytesIO(data)) as tile:
        rotated = ImageChops.invert(tile.convert("RGB")).convert("RGB", DARKEN_MATRIX)
        dark = ImageOps.grayscale(rotated).convert("RGB")

    buffer = io.BytesIO()
    dark.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


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
        # One render at a time per layer: the frontend and the background
        # capture both call async_image, and each render is nine tile fetches.
        self._render_lock = asyncio.Lock()
        self._capturing = False
        self._retries = 0
        # Set when a grid came back mixed, so the next scheduled refresh
        # re-renders it instead of trusting an unchanged probe tile.
        self._force_next = False
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
