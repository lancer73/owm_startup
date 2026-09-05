"""Frame storage for the animated weather maps.

Weather Maps 1.0 tiles carry no time parameter: a request returns whatever is
current. Historical tiles are a Maps 2.0 feature the Startup plan does not
include, so the only way to build a sequence is to keep frames as they are
rendered. An animation therefore starts empty and fills over the following
hours.

Two things keep the cost down:

- A frame is only assembled when the data has actually changed. Upstream
  refreshes every two hours while polling runs every thirty minutes, so three
  refreshes in four would otherwise store a duplicate.
- Change is detected by fetching a single probe tile and comparing it with the
  last one. Only if it differs are the remaining eight fetched. That turns a
  no-change refresh from nine calls into one.

The probe is the centre tile, the one containing the configured coordinates.
That is the tile the reader cares about most, but it does mean a change
confined to the edges of the view is missed until it reaches the centre. The
opportunistic path covers part of that gap: whenever the still map is rendered
for the frontend, the full grid is already in hand and is offered to the store
for nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import io
import json
import logging
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    ANIMATION_FRAME_MS,
    ANIMATION_HOLD_FACTOR,
    ANIMATION_MIN_FRAMES,
    FRAME_WINDOW_HOURS,
    MAP_VIEW,
    PROGRESS_BAR_HEIGHT,
)

_LOGGER = logging.getLogger(__name__)

STATE_FILE = "state.json"


@dataclass(slots=True)
class Frame:
    """One stored frame."""

    path: Path
    taken_at: datetime


def image_hash(data: bytes) -> str:
    """Return a hash of an image's pixels.

    Decoded pixels, not the file bytes: a re-encode upstream would change the
    bytes while the weather stayed identical, and that would store a duplicate
    frame every time.
    """
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        return hashlib.sha256(image.convert("RGBA").tobytes()).hexdigest()


def grid_hash(tiles: dict[tuple[int, int], bytes]) -> str:
    """Return a hash covering every tile of a grid."""
    digest = hashlib.sha256()
    for position in sorted(tiles):
        digest.update(image_hash(tiles[position]).encode())
    return digest.hexdigest()


class FrameStore:
    """Keeps the recent frames for one layer on disk."""

    def __init__(
        self, hass: HomeAssistant, root: Path, layer: str, signature: str
    ) -> None:
        """Initialise the store."""
        self.hass = hass
        self.directory = root / layer
        self.layer = layer
        # Identifies the look of a frame. Frames captured under a different
        # signature cannot play alongside these ones.
        self.signature = signature
        self.probe_hash: str | None = None
        self.frame_hash: str | None = None
        self._loaded = False
        self._listeners: list[Callable[[], None]] = []

    def add_listener(self, listener: Callable[[], None]) -> None:
        """Register a callback to run when the stored frames change.

        Capture is scheduled from the coordinator update rather than run inside
        it, so an entity that reported its frame count during that update would
        always be one cycle behind what is on disk.
        """
        self._listeners.append(listener)

    def _notify(self) -> None:
        """Tell listeners the sequence changed."""
        for listener in self._listeners:
            listener()

    async def async_load(self) -> None:
        """Restore the hashes recorded at the last capture.

        Without this a restart looks like a change: both hashes would be unset,
        the next refresh would fetch the full grid and store a frame identical
        to the one already on disk.
        """
        if self._loaded:
            return
        self._loaded = True
        state = await self.hass.async_add_executor_job(self._read_state)

        if state.get("signature") != self.signature:
            # The renderer changed: a basemap swap, a palette change, or the
            # contrast stretch being toggled. Old frames would play as a jump
            # in the middle of the animation.
            if state:
                _LOGGER.info(
                    "Discarding stored %s frames: they were captured by a "
                    "different renderer",
                    self.layer,
                )
            await self.hass.async_add_executor_job(self._discard)
            return

        # Never clobber a hash already set in this session: a capture may have
        # run before the entity finished loading.
        self.probe_hash = self.probe_hash or state.get("probe_hash")
        self.frame_hash = self.frame_hash or state.get("frame_hash")

    def _read_state(self) -> dict[str, str]:
        """Read the persisted hashes. Runs in an executor."""
        try:
            return json.loads((self.directory / STATE_FILE).read_text())
        except (OSError, ValueError):
            return {}

    def _discard(self) -> None:
        """Delete every stored frame and the state beside it."""
        self.probe_hash = None
        self.frame_hash = None
        try:
            for path in self.directory.glob("*.webp"):
                path.unlink(missing_ok=True)
            (self.directory / STATE_FILE).unlink(missing_ok=True)
        except OSError as err:
            _LOGGER.debug("Could not discard %s frames: %s", self.layer, err)

    def _write_state(self) -> None:
        """Persist the hashes. Runs in an executor."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            (self.directory / STATE_FILE).write_text(
                json.dumps(
                    {
                        "signature": self.signature,
                        "probe_hash": self.probe_hash,
                        "frame_hash": self.frame_hash,
                    }
                )
            )
        except OSError as err:
            _LOGGER.debug("Could not persist %s frame state: %s", self.layer, err)

    async def async_add(self, image: bytes, frame_hash: str) -> bool:
        """Store a rendered frame, unless it repeats the last one."""
        await self.async_load()
        if frame_hash == self.frame_hash:
            return False
        self.frame_hash = frame_hash
        await self.hass.async_add_executor_job(self._write, image)
        await self.hass.async_add_executor_job(self._write_state)
        self._notify()
        return True

    def _write(self, image: bytes) -> None:
        """Write a frame and drop anything outside the window."""
        from PIL import Image

        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            # Sub-second precision: a probe capture and an opportunistic one
            # can land in the same second and would otherwise overwrite.
            stamp = f"{dt_util.utcnow().timestamp():.6f}"
            with Image.open(io.BytesIO(image)) as frame:
                frame.convert("RGB").save(
                    self.directory / f"{stamp}.webp", format="WEBP", quality=88
                )
        except OSError as err:
            _LOGGER.warning("Could not store a %s frame: %s", self.layer, err)
            return
        self._prune()

    def _prune(self) -> None:
        """Delete frames older than the window."""
        cutoff = (dt_util.utcnow() - timedelta(hours=FRAME_WINDOW_HOURS)).timestamp()
        try:
            for path in self.directory.glob("*.webp"):
                if _timestamp(path) < cutoff:
                    path.unlink(missing_ok=True)
        except OSError as err:
            _LOGGER.debug("Could not prune %s frames: %s", self.layer, err)

    def frames(self) -> list[Frame]:
        """Return the frames inside the window, oldest first.

        Filtered here as well as on write: pruning only runs when a frame is
        stored, so after an outage longer than the window the directory still
        holds frames that should no longer play.
        """
        cutoff = (dt_util.utcnow() - timedelta(hours=FRAME_WINDOW_HOURS)).timestamp()
        try:
            paths = sorted(self.directory.glob("*.webp"), key=_timestamp)
        except OSError:
            return []
        return [
            Frame(path, dt_util.utc_from_timestamp(_timestamp(path)))
            for path in paths
            if _timestamp(path) >= cutoff
        ]

    def build_animation(self) -> bytes | None:
        """Assemble the stored frames into a WebP.

        With a single frame the result is a still: an entity that serves
        nothing reads as broken, and a sequence that is merely still filling is
        not broken. The progress bar is omitted in that case, since there is no
        span for it to describe.

        Runs in an executor: Pillow is blocking.
        """
        from PIL import Image

        frames = self.frames()
        if len(frames) < ANIMATION_MIN_FRAMES:
            return None

        # A frame that will not open must not take the animation with it: drop
        # it and carry on, so one bad write does not cost twelve hours.
        usable: list[tuple[Frame, object]] = []
        for frame in frames:
            try:
                with Image.open(frame.path) as stored:
                    usable.append((frame, stored.convert("RGB").copy()))
            except (OSError, ValueError) as err:
                _LOGGER.warning(
                    "Dropping unreadable %s frame %s: %s",
                    self.layer,
                    frame.path.name,
                    err,
                )
                frame.path.unlink(missing_ok=True)

        if len(usable) < ANIMATION_MIN_FRAMES:
            return None

        span = (usable[-1][0].taken_at - usable[0][0].taken_at).total_seconds()
        images = []
        for frame, image in usable:
            if len(usable) > 1:
                elapsed = (frame.taken_at - usable[0][0].taken_at).total_seconds()
                _draw_progress(image, elapsed / span if span > 0 else 1.0)
            images.append(image)

        # Per-frame durations, so the newest frame is held. Pillow accepts a
        # list here and writes it into each ANMF chunk, though it does not
        # expose the values again on read.
        durations = [ANIMATION_FRAME_MS] * len(images)
        durations[-1] = ANIMATION_FRAME_MS * ANIMATION_HOLD_FACTOR

        buffer = io.BytesIO()
        first, rest = images[0], images[1:]
        first.save(
            buffer,
            format="WEBP",
            save_all=True,
            append_images=rest,
            duration=durations,
            loop=0,
            quality=85,
            method=4,
        )
        return buffer.getvalue()


def _draw_progress(image, fraction: float) -> None:
    """Draw the playback position along the bottom edge of the map.

    Filled by frame index rather than by elapsed time. Frames are captured only
    when the data changes, so they are unevenly spaced in time, while playback
    gives each of them the same duration. A time-proportional bar would stutter
    against even playback and read as a fault. The absolute time of each frame
    is already burned into it by the renderer, so nothing is lost.
    """
    from PIL import ImageDraw

    width = image.width
    top = min(MAP_VIEW, image.height) - PROGRESS_BAR_HEIGHT
    bottom = top + PROGRESS_BAR_HEIGHT
    draw = ImageDraw.Draw(image)

    draw.rectangle((0, top, width, bottom), fill=(30, 32, 36))
    filled = max(1, round(width * min(max(fraction, 0.0), 1.0)))
    draw.rectangle((0, top, filled, bottom), fill=(235, 235, 240))


def _timestamp(path: Path) -> float:
    """Return the epoch a frame file was taken at, from its name."""
    try:
        return float(path.stem)
    except ValueError:
        return 0.0
