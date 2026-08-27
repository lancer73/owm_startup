"""Legend rendering for the weather map images.

The documented OpenWeather palettes span the whole plausible range of each
quantity: temperature runs from -65 to +30 °C. Across a 200 km view the actual
spread is usually a few degrees, so plotting the full palette wastes the bar and
tells the reader nothing.

Instead the rendered overlay is sampled, each painted colour is matched back to
a value through the palette, and the legend is drawn as a continuous gradient
over the range actually present — trimmed at both ends so resampling artefacts
at colour boundaries do not stretch it.

Matching is approximate: tiles are resampled server-side by OpenWeather, so
pixels on a colour boundary land between palette entries. Treat the range as
indicative, not as a measurement.
"""

from __future__ import annotations

import logging
import math

from .const import (
    LEGEND_HEIGHT,
    LEGEND_TICKS,
    LEGEND_TRIM,
    LEGENDS,
    MAP_VIEW,
    STRETCH_RAMPS,
)

_LOGGER = logging.getLogger(__name__)

Colour = tuple[int, int, int, int]

# Resolution used when sampling the overlay for its value range.
SAMPLE_SIZE = 128

# The legend is drawn into the image, so Home Assistant's own translation
# machinery cannot reach it. These are the only strings involved.
TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "temp_new": "Temperature",
        "clouds_new": "Cloud cover",
        "range": "range in view",
        "stretched": "range in view, contrast stretched",
        "empty": "no data in view, full scale",
        "fetched": "fetched",
        "day": "today's range, contrast stretched",
        "mixed": "tiles from different updates",
    },
    "nl": {
        "temp_new": "Temperatuur",
        "clouds_new": "Bewolking",
        "range": "bereik in beeld",
        "stretched": "bereik in beeld, contrast opgerekt",
        "empty": "geen data in beeld, volledige schaal",
        "fetched": "opgehaald",
        "day": "bereik van vandaag, contrast opgerekt",
        "mixed": "tegels uit verschillende updates",
    },
    "de": {
        "temp_new": "Temperatur",
        "clouds_new": "Bewölkung",
        "range": "Bereich im Bild",
        "stretched": "Bereich im Bild, Kontrast gespreizt",
        "empty": "keine Daten im Bild, volle Skala",
        "fetched": "abgerufen",
        "day": "Bereich von heute, Kontrast gespreizt",
        "mixed": "Kacheln aus verschiedenen Aktualisierungen",
    },
    "fr": {
        "temp_new": "Température",
        "clouds_new": "Couverture nuageuse",
        "range": "plage visible",
        "stretched": "plage visible, contraste étiré",
        "empty": "aucune donnée visible, échelle complète",
        "fetched": "récupéré",
        "day": "plage du jour, contraste étiré",
        "mixed": "tuiles de mises à jour différentes",
    },
}

# Fonts to try before falling back. Pillow ships none of its own.
FONT_PATHS = (
    "DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
)


def translate(language: str, key: str) -> str:
    """Return a legend string in the configured language, falling back to English."""
    table = TRANSLATIONS.get(language.lower().split("_")[0], TRANSLATIONS["en"])
    return table.get(key) or TRANSLATIONS["en"].get(key, key)


def colour_at(stops: tuple[tuple[float, Colour], ...], value: float) -> Colour:
    """Return the palette colour for a value, interpolating between stops."""
    if value <= stops[0][0]:
        return stops[0][1]
    if value >= stops[-1][0]:
        return stops[-1][1]
    for (low, low_colour), (high, high_colour) in zip(stops, stops[1:], strict=False):
        if low <= value <= high:
            span = high - low
            ratio = 0.0 if span == 0 else (value - low) / span
            return tuple(  # type: ignore[return-value]
                round(a + (b - a) * ratio)
                for a, b in zip(low_colour, high_colour, strict=True)
            )
    return stops[-1][1]


def observed_range(overlay, layer: str) -> tuple[float, float] | None:
    """Return the value range painted in an overlay image, or None if empty.

    `overlay` is the cropped, overlay-only RGBA image: what the reader can
    actually see, not the whole fetched grid.
    """
    legend = LEGENDS.get(layer)
    if legend is None:
        return None

    stops = legend["stops"]
    # Nearest-neighbour: interpolating would invent colours between palette
    # bands and widen the range with values that are not in the data.
    sample = overlay.resize((SAMPLE_SIZE, SAMPLE_SIZE), resample=0)
    colours = sample.getcolors(SAMPLE_SIZE * SAMPLE_SIZE) or []

    counted: list[tuple[float, int]] = []
    total = 0
    for count, colour in colours:
        if colour[3] == 0:
            continue  # nothing painted here
        value = value_for_colour(stops, colour)
        counted.append((value, count))
        total += count

    if not counted or total == 0:
        return None

    counted.sort()
    trim = total * LEGEND_TRIM
    seen = 0
    low = high = counted[0][0]
    for value, count in counted:
        if seen <= trim:
            low = value
        if seen <= total - trim:
            high = value
        seen += count
    return (low, high) if high > low else (low, low)


def value_for_colour(stops: tuple[tuple[float, Colour], ...], colour: Colour) -> float:
    """Invert the palette: recover the value that produced a pixel.

    Each pair of adjacent stops is a straight line in RGBA space. The pixel is
    projected onto every segment and the closest one wins, which recovers a
    continuous value rather than snapping to the nearest tabulated step. That
    matters here: over a 3 degree window a tabulated palette would quantise the
    whole view into a handful of values.
    """
    best_value = stops[0][0]
    best_distance = None

    for (low, low_colour), (high, high_colour) in zip(stops, stops[1:], strict=False):
        delta = [b - a for a, b in zip(low_colour, high_colour, strict=True)]
        length = sum(component**2 for component in delta)
        if length == 0:
            ratio = 0.0
        else:
            offset = [c - a for a, c in zip(low_colour, colour, strict=True)]
            ratio = sum(a * b for a, b in zip(delta, offset, strict=True)) / length
            ratio = min(max(ratio, 0.0), 1.0)

        projected = [a + ratio * d for a, d in zip(low_colour, delta, strict=True)]
        distance = sum((a - b) ** 2 for a, b in zip(projected, colour, strict=True))
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_value = low + ratio * (high - low)

    return best_value


def tick_step(low: float, high: float, count: int = LEGEND_TICKS) -> float:
    """Return the rounded spacing used between ticks."""
    if high <= low:
        return 0.0
    raw = (high - low) / max(1, count - 1)
    magnitude = 10 ** math.floor(math.log10(raw))
    step = magnitude
    for multiple in (1, 2, 2.5, 5, 10):
        step = magnitude * multiple
        if step >= raw:
            break
    return step


def nice_ticks(low: float, high: float, count: int = LEGEND_TICKS) -> list[float]:
    """Return rounded tick values spanning a range."""
    if high <= low:
        return [low]
    step = tick_step(low, high, count)
    start = math.ceil(low / step) * step
    ticks = []
    value = start
    while value <= high + step * 0.01:
        ticks.append(round(value, 6))
        value += step
    return ticks or [low, high]


def format_tick(value: float, step: float = 1.0) -> str:
    """Format a tick label with enough precision to distinguish neighbours.

    Precision follows the tick spacing, not the magnitude: over a one degree
    range, formatting 17.4 and 17.6 by magnitude alone would print "17" twice.
    """
    if step >= 1:
        decimals = 0
    elif step >= 0.1:
        decimals = 1
    else:
        decimals = 2
    return f"{value:.{decimals}f}"


def pad_range(bounds: tuple[float, float]) -> tuple[float, float]:
    """Widen a degenerate range so the bar and the stretch stay usable."""
    low, high = bounds
    if high > low:
        return low, high
    pad = max(abs(low) * 0.05, 0.5)
    return low - pad, high + pad


def ramp_colour(ramp: tuple[Colour, ...], position: float) -> Colour:
    """Return the ramp colour at a position between 0 and 1."""
    position = min(max(position, 0.0), 1.0)
    if len(ramp) == 1:
        return ramp[0]
    scaled = position * (len(ramp) - 1)
    index = min(int(scaled), len(ramp) - 2)
    ratio = scaled - index
    return tuple(  # type: ignore[return-value]
        round(a + (b - a) * ratio)
        for a, b in zip(ramp[index], ramp[index + 1], strict=True)
    )


def stretch(overlay, layer: str, bounds: tuple[float, float]):
    """Re-colour an overlay so the observed range spans a full ramp.

    OpenWeather's palettes are built for the whole globe: over a 200 km view
    the colours barely change. Every painted pixel is matched back to a value
    and re-mapped across the layer's ramp instead.

    Pixels are remapped through a lookup built from the image's unique colours,
    so the cost scales with the number of distinct colours rather than with the
    pixel count.
    """
    legend = LEGENDS.get(layer)
    ramp = STRETCH_RAMPS.get(layer)
    if legend is None or ramp is None:
        return overlay

    low, high = pad_range(bounds)
    stops = legend["stops"]
    span = high - low

    mapping: dict[Colour, Colour] = {}
    for _count, colour in overlay.getcolors(overlay.width * overlay.height) or []:
        if colour[3] == 0:
            mapping[colour] = colour  # nothing painted; leave it clear
            continue
        value = value_for_colour(stops, colour)
        mapping[colour] = ramp_colour(ramp, (value - low) / span)

    stretched = overlay.copy()
    stretched.putdata([mapping.get(pixel, pixel) for pixel in overlay.getdata()])
    return stretched


def draw(
    canvas,
    layer: str,
    bounds: tuple[float, float] | None,
    attribution: str,
    *,
    stretched: bool,
    language: str = "en",
    fetched_at: str | None = None,
    day_scaled: bool = False,
) -> None:
    """Draw the legend strip below the map. Runs in an executor.

    `fetched_at` is when the tiles were retrieved, not when the data is valid
    for: Weather Maps 1.0 tiles carry no validity time, so that is the most
    this can honestly claim.
    """
    from PIL import Image, ImageDraw

    draw_context = ImageDraw.Draw(canvas)
    top = MAP_VIEW
    legend = LEGENDS.get(layer)

    if fetched_at:
        stamp = f"{translate(language, 'fetched')} {fetched_at}"
        stamp_font = _font(11)
        width = draw_context.textlength(stamp, font=stamp_font)
        draw_context.text(
            (MAP_VIEW - width - 6, top + 4),
            stamp,
            font=stamp_font,
            fill=(190, 190, 190),
        )

    if legend is not None:
        stops = legend["stops"]
        ramp = STRETCH_RAMPS.get(layer) if stretched else None
        if bounds is None:
            low, high = stops[0][0], stops[-1][0]
            ramp = None
            note = translate(language, "empty")
        else:
            low, high = pad_range(bounds)
            if ramp and day_scaled:
                note = translate(language, "day")
            else:
                note = translate(language, "stretched" if ramp else "range")

        name = translate(language, layer)
        title = f"{name} ({legend['unit']}) - {note}"
        draw_context.text((6, top + 4), title, font=_font(11), fill=(232, 232, 232))

        bar_top = top + 19
        bar_height = 12
        for x in range(MAP_VIEW):
            position = x / max(1, MAP_VIEW - 1)
            colour = (
                ramp_colour(ramp, position)
                if ramp
                else colour_at(stops, low + (high - low) * position)
            )
            column = Image.new("RGBA", (1, bar_height), colour)
            canvas.alpha_composite(column, (x, bar_top))

        label_font = _font(10)
        step = tick_step(low, high)
        for tick in nice_ticks(low, high):
            position = (tick - low) / (high - low) if high > low else 0.0
            x = round(position * (MAP_VIEW - 1))
            draw_context.line(
                [(x, bar_top), (x, bar_top + bar_height)], fill=(255, 255, 255, 120)
            )
            label = format_tick(tick, step)
            offset = 0 if x < MAP_VIEW - 30 else -len(label) * 6
            draw_context.text(
                (min(max(x + 2 + offset, 2), MAP_VIEW - 24), bar_top + bar_height + 1),
                label,
                font=label_font,
                fill=(210, 210, 210),
            )

    draw_context.text(
        (6, top + LEGEND_HEIGHT - 14),
        attribution,
        font=_font(10),
        fill=(150, 150, 150),
    )


def _font(size: int):
    """Return the best available font at a given size.

    Pillow ships no fonts and a Home Assistant container may have none
    installed. The unsized bitmap default is the last resort: it cannot render
    "·" and spaces around "©" collapse, so it is only reached if everything
    else fails.
    """
    from PIL import ImageFont

    for path in FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()
