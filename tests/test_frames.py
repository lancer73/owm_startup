"""Tests for the animated map sequence."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from custom_components.owm_startup.frames import (
    FrameStore,
    grid_hash,
    image_hash,
)
from homeassistant.core import HomeAssistant


def _png(colour: tuple[int, int, int, int]) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGBA", (64, 64), colour).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def store(hass: HomeAssistant, tmp_path: Path) -> FrameStore:
    """Return a store writing into a temporary directory."""
    return FrameStore(hass, tmp_path, "clouds_new", "1:plain")


def test_image_hash_ignores_the_container() -> None:
    """The same pixels re-encoded must hash the same.

    Hashing file bytes would store a duplicate frame every time upstream
    re-encoded an unchanged tile.
    """
    from PIL import Image

    original = _png((10, 20, 30, 255))
    buffer = io.BytesIO()
    with Image.open(io.BytesIO(original)) as image:
        image.save(buffer, format="PNG", optimize=True, compress_level=1)
    reencoded = buffer.getvalue()

    assert original != reencoded
    assert image_hash(original) == image_hash(reencoded)


def test_image_hash_separates_different_pixels() -> None:
    """A real change must change the hash."""
    assert image_hash(_png((10, 20, 30, 255))) != image_hash(_png((10, 20, 31, 255)))


def test_grid_hash_covers_every_tile() -> None:
    """A change in any tile changes the grid hash, wherever it is."""
    base = {(x, y): _png((10, 20, 30, 255)) for x in range(3) for y in range(3)}
    assert grid_hash(base) == grid_hash(dict(base))

    for corner in ((0, 0), (2, 2), (1, 1)):
        changed = dict(base)
        changed[corner] = _png((10, 20, 99, 255))
        assert grid_hash(changed) != grid_hash(base), corner


async def test_duplicate_frames_are_not_stored(store: FrameStore) -> None:
    """Polling runs faster than the data changes; duplicates are dropped."""
    image = _png((1, 2, 3, 255))

    assert await store.async_add(image, "hash-a") is True
    assert await store.async_add(image, "hash-a") is False
    assert await store.async_add(image, "hash-b") is True

    assert len(store.frames()) == 2


async def test_frames_outside_the_window_are_pruned(store: FrameStore, freezer) -> None:
    """Only the configured window is kept on disk."""
    from datetime import timedelta

    await store.async_add(_png((1, 2, 3, 255)), "old")
    freezer.tick(timedelta(hours=13))
    await store.async_add(_png((4, 5, 6, 255)), "new")

    frames = store.frames()
    assert len(frames) == 1


async def test_single_frame_is_served_as_a_still(store: FrameStore) -> None:
    """A filling sequence must not look like a broken entity.

    Upstream changes every couple of hours, so returning nothing until the
    second frame leaves a broken image on the dashboard for that long.
    """
    from PIL import Image

    await store.async_add(_png((1, 2, 3, 255)), "one")
    data = store.build_animation()

    assert data is not None
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "WEBP"
        assert not getattr(image, "is_animated", False)


async def test_no_frames_yields_nothing(store: FrameStore) -> None:
    """Before the first capture there is genuinely nothing to show."""
    assert store.build_animation() is None


async def test_single_frame_has_no_progress_bar(store: FrameStore) -> None:
    """With one frame there is no span for a bar to describe."""
    from custom_components.owm_startup.const import MAP_VIEW

    await store.async_add(_flat_frame(MAP_VIEW, MAP_VIEW + 66), "one")
    data = store.build_animation()

    assert _bar_fraction(data, 0) == 0.0


async def test_animation_is_an_animated_webp(store: FrameStore) -> None:
    """The assembled result plays, and holds every frame."""
    from PIL import Image

    for index in range(4):
        await store.async_add(_png((index * 40, 20, 30, 255)), f"frame-{index}")

    data = store.build_animation()
    assert data is not None

    with Image.open(io.BytesIO(data)) as animation:
        assert animation.format == "WEBP"
        assert animation.is_animated
        assert animation.n_frames == 4


async def test_animation_orders_frames_oldest_first(store: FrameStore, freezer) -> None:
    """Frames must play in the order they were captured."""
    from datetime import timedelta

    for index in range(3):
        await store.async_add(_png((index * 60, 0, 0, 255)), f"frame-{index}")
        freezer.tick(timedelta(minutes=30))

    frames = store.frames()
    assert [frame.taken_at for frame in frames] == sorted(
        frame.taken_at for frame in frames
    )


def _flat_frame(width: int, height: int) -> bytes:
    """Return a plain frame the progress bar can be measured against."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (90, 90, 90)).save(buffer, format="PNG")
    return buffer.getvalue()


def _bar_fraction(data: bytes, frame_index: int) -> float:
    """Return how far along the bar is filled on one frame."""
    from PIL import Image

    from custom_components.owm_startup.const import MAP_VIEW, PROGRESS_BAR_HEIGHT

    row = MAP_VIEW - PROGRESS_BAR_HEIGHT + 1
    with Image.open(io.BytesIO(data)) as animation:
        animation.seek(frame_index)
        frame = animation.convert("RGB")
    filled = sum(1 for x in range(frame.width) if sum(frame.getpixel((x, row))) > 500)
    return filled / frame.width


async def test_progress_bar_tracks_elapsed_time(store: FrameStore, freezer) -> None:
    """The bar reports position in time, so a gap shows as a jump.

    Frames here are captured at 0h, 1h and 5h of a 5 hour span, so the middle
    frame must sit near 20 per cent rather than near the 50 per cent an
    index-based bar would give it.
    """
    from datetime import timedelta

    from custom_components.owm_startup.const import MAP_VIEW

    image = _flat_frame(MAP_VIEW, MAP_VIEW + 66)

    await store.async_add(image, "frame-0")
    freezer.tick(timedelta(hours=1))
    await store.async_add(image, "frame-1")
    freezer.tick(timedelta(hours=4))
    await store.async_add(image, "frame-2")

    data = store.build_animation()
    assert data is not None

    assert _bar_fraction(data, 0) < 0.05
    assert 0.12 < _bar_fraction(data, 1) < 0.30
    assert _bar_fraction(data, 2) > 0.95


async def test_progress_bar_survives_identical_timestamps(
    store: FrameStore,
) -> None:
    """A zero-length span must not divide by zero."""
    from custom_components.owm_startup.const import MAP_VIEW

    image = _flat_frame(MAP_VIEW, MAP_VIEW + 66)
    for index in range(2):
        await store.async_add(image, f"frame-{index}")

    assert store.build_animation() is not None


async def test_progress_bar_does_not_cover_the_map_or_the_legend(
    store: FrameStore,
) -> None:
    """The bar sits on the seam, so neither data nor text is obscured."""
    from PIL import Image

    from custom_components.owm_startup.const import MAP_VIEW, PROGRESS_BAR_HEIGHT

    for index in range(2):
        buffer = io.BytesIO()
        Image.new("RGB", (MAP_VIEW, MAP_VIEW + 66), (90, 90, 90)).save(
            buffer, format="PNG"
        )
        await store.async_add(buffer.getvalue(), f"frame-{index}")

    data = store.build_animation()
    with Image.open(io.BytesIO(data)) as animation:
        animation.seek(1)
        frame = animation.convert("RGB")

    # Above the bar is still map, below it is still legend area.
    assert abs(sum(frame.getpixel((10, MAP_VIEW - PROGRESS_BAR_HEIGHT - 3))) - 270) < 60
    assert abs(sum(frame.getpixel((10, MAP_VIEW + 10))) - 270) < 60


async def test_hashes_survive_a_restart(hass: HomeAssistant, tmp_path: Path) -> None:
    """A restart must not look like a change.

    Otherwise the first refresh after every restart fetches the full grid and
    stores a frame identical to the one already on disk.
    """
    first = FrameStore(hass, tmp_path, "clouds_new", "1:plain")
    await first.async_load()
    first.probe_hash = "probe-1"
    await first.async_add(_png((1, 2, 3, 255)), "frame-1")

    second = FrameStore(hass, tmp_path, "clouds_new", "1:plain")
    await second.async_load()

    assert second.probe_hash == "probe-1"
    assert second.frame_hash == "frame-1"
    assert await second.async_add(_png((1, 2, 3, 255)), "frame-1") is False


async def test_frames_outside_the_window_do_not_play(
    store: FrameStore, freezer
) -> None:
    """Pruning happens on write, so a long outage leaves stale frames on disk.

    They must not appear in the animation just because nothing has been
    written since.
    """
    from datetime import timedelta

    await store.async_add(_png((1, 2, 3, 255)), "old-1")
    freezer.tick(timedelta(minutes=30))
    await store.async_add(_png((4, 5, 6, 255)), "old-2")

    # Integration down for a day; nothing written, so nothing pruned.
    freezer.tick(timedelta(hours=25))

    assert len(list(store.directory.glob("*.webp"))) == 2
    assert store.frames() == []
    assert store.build_animation() is None


async def test_unreadable_frame_is_dropped_not_fatal(
    store: FrameStore, freezer
) -> None:
    """One truncated file must not cost the whole sequence."""
    from datetime import timedelta

    for index in range(3):
        await store.async_add(_png((index * 60, 0, 0, 255)), f"frame-{index}")
        freezer.tick(timedelta(minutes=30))

    corrupt = store.frames()[1].path
    corrupt.write_bytes(b"not an image")

    assert store.build_animation() is not None
    # The bad file is removed so it is not retried on every assembly.
    assert not corrupt.exists()


async def test_write_failure_is_survivable(store: FrameStore) -> None:
    """A full or read-only disk must not raise into the caller."""
    store.directory.parent.mkdir(parents=True, exist_ok=True)
    store.directory.write_text("this is a file, not a directory")

    assert await store.async_add(_png((1, 2, 3, 255)), "frame") is True
    assert store.frames() == []


def _frame_durations(data: bytes) -> list[int]:
    """Read per-frame durations out of the WebP container.

    Pillow writes them but does not expose them on read, so the ANMF chunks
    have to be walked directly.
    """
    import struct

    durations: list[int] = []
    position = 12
    while position + 8 <= len(data):
        tag = data[position : position + 4]
        size = struct.unpack("<I", data[position + 4 : position + 8])[0]
        body = position + 8
        if tag == b"ANMF":
            durations.append(int.from_bytes(data[body + 12 : body + 15], "little"))
        position = body + size + (size & 1)
    return durations


async def test_last_frame_is_held_longer(store: FrameStore, freezer) -> None:
    """The newest frame is the current weather and is what people look at."""
    from datetime import timedelta

    from custom_components.owm_startup.const import (
        ANIMATION_FRAME_MS,
        ANIMATION_HOLD_FACTOR,
    )

    for index in range(4):
        await store.async_add(_png((index * 50, 0, 0, 255)), f"frame-{index}")
        freezer.tick(timedelta(minutes=30))

    durations = _frame_durations(store.build_animation())

    assert len(durations) == 4
    assert durations[:3] == [ANIMATION_FRAME_MS] * 3
    assert durations[-1] == ANIMATION_FRAME_MS * ANIMATION_HOLD_FACTOR


async def test_frames_from_a_different_renderer_are_discarded(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """A basemap or palette change makes stored frames unplayable.

    Mixing them into the animation reads as a fault: the map jumps style
    halfway through.
    """
    old = FrameStore(hass, tmp_path, "clouds_new", "1:plain")
    await old.async_add(_png((1, 2, 3, 255)), "frame-1")
    assert len(old.frames()) == 1

    new = FrameStore(hass, tmp_path, "clouds_new", "2:plain")
    await new.async_load()

    assert new.frames() == []
    assert new.frame_hash is None
    assert new.probe_hash is None


async def test_toggling_the_stretch_discards_frames(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """The stretch changes the look as much as a palette change does."""
    stretched = FrameStore(hass, tmp_path, "temp_new", "2:stretched")
    await stretched.async_add(_png((1, 2, 3, 255)), "frame-1")

    plain = FrameStore(hass, tmp_path, "temp_new", "2:plain")
    await plain.async_load()

    assert plain.frames() == []


async def test_matching_signature_keeps_the_frames(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """An ordinary restart must not throw the sequence away."""
    first = FrameStore(hass, tmp_path, "temp_new", "2:stretched")
    await first.async_add(_png((1, 2, 3, 255)), "frame-1")

    second = FrameStore(hass, tmp_path, "temp_new", "2:stretched")
    await second.async_load()

    assert len(second.frames()) == 1
    assert second.frame_hash == "frame-1"
