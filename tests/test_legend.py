"""Tests for legend range detection and tick selection."""

from __future__ import annotations

import pytest

from custom_components.owm_startup.const import LEGENDS
from custom_components.owm_startup.legend import (
    colour_at,
    format_tick,
    nice_ticks,
    observed_range,
)

TEMP_STOPS = LEGENDS["temp_new"]["stops"]


def _field(values: list[float], layer: str = "temp_new"):
    """Return an RGBA image painted with the palette colours for `values`."""
    from PIL import Image

    stops = LEGENDS[layer]["stops"]
    image = Image.new("RGBA", (len(values), 8))
    pixels = image.load()
    for x, value in enumerate(values):
        for y in range(8):
            pixels[x, y] = colour_at(stops, value)
    return image


def test_colour_at_interpolates_between_stops() -> None:
    """A value between two stops sits between their colours."""
    low = colour_at(TEMP_STOPS, 10.0)
    high = colour_at(TEMP_STOPS, 20.0)
    middle = colour_at(TEMP_STOPS, 15.0)
    for index in range(3):
        assert (
            min(low[index], high[index])
            <= middle[index]
            <= max(low[index], high[index])
        )


def test_colour_at_clamps_outside_the_palette() -> None:
    """Values beyond the ends return the end colours."""
    assert colour_at(TEMP_STOPS, -200.0) == TEMP_STOPS[0][1]
    assert colour_at(TEMP_STOPS, 200.0) == TEMP_STOPS[-1][1]


def test_observed_range_tracks_a_narrow_field() -> None:
    """A 5 degree spread yields a range near those bounds, not the full scale."""
    values = [16.0 + index * 5.0 / 63 for index in range(64)]
    low, high = observed_range(_field(values), "temp_new")

    assert 14.0 < low < 18.0
    assert 19.0 < high < 23.0
    # The point of the exercise: nothing like the -65..30 palette span.
    assert high - low < 10


def test_observed_range_is_none_when_nothing_is_painted() -> None:
    """A fully transparent overlay has no range."""
    from PIL import Image

    empty = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    assert observed_range(empty, "temp_new") is None


def test_observed_range_handles_a_uniform_field() -> None:
    """A single-valued field returns a degenerate range rather than failing."""
    low, high = observed_range(_field([18.0] * 32), "temp_new")
    assert abs(high - low) < 1.0


@pytest.mark.parametrize(
    ("low", "high", "expected_span"),
    [(16.5, 21.5, 5.0), (0.0, 100.0, 100.0), (0.9, 1.4, 0.5)],
)
def test_nice_ticks_stay_inside_the_range(low, high, expected_span) -> None:
    """Ticks are rounded, ordered and never fall outside the bar."""
    ticks = nice_ticks(low, high)
    assert ticks == sorted(ticks)
    assert ticks[0] >= low
    assert ticks[-1] <= high
    assert 2 <= len(ticks) <= 12


def test_nice_ticks_on_a_degenerate_range() -> None:
    """A zero-width range does not loop forever."""
    assert nice_ticks(5.0, 5.0) == [5.0]


@pytest.mark.parametrize(
    ("value", "step", "expected"),
    [
        (17.0, 1.0, "17"),
        (-3.0, 1.0, "-3"),
        (17.4, 0.2, "17.4"),
        (1.25, 0.05, "1.25"),
    ],
)
def test_format_tick(value, step, expected) -> None:
    """Tick labels carry enough precision for the spacing in use."""
    assert format_tick(value, step) == expected


def test_tick_labels_are_distinct_on_a_narrow_range() -> None:
    """Neighbouring ticks must never print the same label.

    A one degree window rounded by magnitude produced "17, 18, 18, 18".
    """
    from custom_components.owm_startup.legend import tick_step

    low, high = 17.3, 18.4
    step = tick_step(low, high)
    labels = [format_tick(tick, step) for tick in nice_ticks(low, high)]
    assert len(labels) == len(set(labels)), labels


def test_stretch_spreads_a_narrow_range_across_the_ramp() -> None:
    """A 3 degree field becomes a full ramp instead of near-uniform colour."""
    from custom_components.owm_startup.legend import observed_range, stretch

    values = [17.0 + index * 3.0 / 63 for index in range(64)]
    overlay = _field(values)
    bounds = observed_range(overlay, "temp_new")
    result = stretch(overlay, "temp_new", bounds)

    def spread(image) -> int:
        colours = [colour for _count, colour in image.getcolors(4096)]
        return max(
            max(colour[channel] for colour in colours)
            - min(colour[channel] for colour in colours)
            for channel in range(3)
        )

    # The source barely moves across 3 degrees; the stretch must do better.
    assert spread(result) > spread(overlay) * 3

    left = result.getpixel((0, 0))
    right = result.getpixel((result.width - 1, 0))
    # Cold end blue-dominant, warm end red-dominant.
    assert left[2] > left[0]
    assert right[0] > right[2]


def test_stretch_leaves_unpainted_pixels_clear() -> None:
    """Transparent pixels must not be coloured in."""
    from PIL import Image

    from custom_components.owm_startup.legend import stretch

    overlay = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    result = stretch(overlay, "clouds_new", (10.0, 50.0))
    assert result.getpixel((4, 4))[3] == 0


def test_stretch_is_a_no_op_for_an_unknown_layer() -> None:
    """A layer without a ramp is returned untouched."""
    from custom_components.owm_startup.legend import stretch

    overlay = _field([18.0] * 8)
    assert stretch(overlay, "pressure_new", (17.0, 19.0)) is overlay


def test_ramp_colour_clamps() -> None:
    """Positions outside 0..1 return the ramp ends."""
    from custom_components.owm_startup.const import STRETCH_RAMPS
    from custom_components.owm_startup.legend import ramp_colour

    ramp = STRETCH_RAMPS["temp_new"]
    assert ramp_colour(ramp, -1.0) == ramp[0]
    assert ramp_colour(ramp, 2.0) == ramp[-1]


def test_palette_inversion_round_trips() -> None:
    """A colour produced by a value must invert back to roughly that value."""
    from custom_components.owm_startup.legend import value_for_colour

    for expected in (-12.0, 0.0, 7.5, 17.3, 22.0, 28.0):
        recovered = value_for_colour(TEMP_STOPS, colour_at(TEMP_STOPS, expected))
        assert abs(recovered - expected) < 0.5


def test_palette_inversion_resolves_to_the_limit_of_the_colours() -> None:
    """Inversion must not quantise beyond what 8-bit colour already does.

    Across 17-19 degrees the palette moves one integer channel step roughly
    every 0.15 degrees, so about 13 distinct values are recoverable. A
    tabulated lookup collapsed the same window into a handful; this guards
    that regression without claiming more precision than the tiles carry.
    """
    from custom_components.owm_startup.legend import value_for_colour

    recovered = {
        round(
            value_for_colour(TEMP_STOPS, colour_at(TEMP_STOPS, 17.0 + step * 0.05)), 3
        )
        for step in range(40)
    }
    assert len(recovered) >= 12


def test_legend_strings_are_translated() -> None:
    """The legend is drawn into the image, so it must translate itself."""
    from custom_components.owm_startup.legend import TRANSLATIONS, translate

    assert translate("nl", "temp_new") == "Temperatuur"
    assert translate("nl", "range") == "bereik in beeld"
    # Region variants and case resolve to the base language.
    assert translate("NL", "clouds_new") == translate("nl_BE", "clouds_new")
    # Unknown languages fall back to English rather than failing.
    assert translate("xx", "clouds_new") == TRANSLATIONS["en"]["clouds_new"]


def test_every_language_covers_every_key() -> None:
    """A partial translation must not leave a blank in the rendered image."""
    from custom_components.owm_startup.legend import TRANSLATIONS

    expected = set(TRANSLATIONS["en"])
    for language, table in TRANSLATIONS.items():
        assert set(table) == expected, language
        assert all(value for value in table.values()), language


def test_legend_title_uses_the_requested_language() -> None:
    """The rendered strip carries the translated title, not the English one."""
    from PIL import Image

    from custom_components.owm_startup.const import LEGEND_HEIGHT, MAP_VIEW
    from custom_components.owm_startup.legend import draw

    def render(language: str) -> bytes:
        canvas = Image.new("RGBA", (MAP_VIEW, MAP_VIEW + LEGEND_HEIGHT), (0, 0, 0, 255))
        draw(
            canvas, "temp_new", (16.0, 20.0), "attr", stretched=True, language=language
        )
        return canvas.tobytes()

    assert render("nl") != render("en")


def test_legend_avoids_characters_the_fallback_font_cannot_draw() -> None:
    """No middle dot: Pillow's bitmap fallback renders it as a box."""
    from custom_components.owm_startup.legend import TRANSLATIONS

    for table in TRANSLATIONS.values():
        for value in table.values():
            assert "·" not in value


def test_clear_sky_does_not_count_as_cloud_data() -> None:
    """Zero alpha means no data, including where the palette has a colour.

    The cloud palette is white at zero alpha for 0% cover; counting that as
    data would peg every cloud range to zero.
    """
    from PIL import Image

    from custom_components.owm_startup.const import LEGENDS
    from custom_components.owm_startup.legend import observed_range

    clear = colour_at(LEGENDS["clouds_new"]["stops"], 0.0)
    overlay = Image.new("RGBA", (32, 32), clear)
    assert observed_range(overlay, "clouds_new") is None


def test_timestamp_is_drawn_when_supplied() -> None:
    """The strip carries the fetch time, and omits it when there is none."""
    from PIL import Image

    from custom_components.owm_startup.const import LEGEND_HEIGHT, MAP_VIEW
    from custom_components.owm_startup.legend import draw

    def render(fetched_at: str | None) -> bytes:
        canvas = Image.new("RGBA", (MAP_VIEW, MAP_VIEW + LEGEND_HEIGHT), (0, 0, 0, 255))
        draw(
            canvas,
            "temp_new",
            (16.0, 20.0),
            "attr",
            stretched=True,
            language="en",
            fetched_at=fetched_at,
        )
        return canvas.tobytes()

    assert render("22 Aug 16:05") != render(None)


def test_timestamp_label_is_translated() -> None:
    """The word beside the time follows the configured language."""
    from custom_components.owm_startup.legend import translate

    assert translate("nl", "fetched") == "opgehaald"
    assert translate("en", "fetched") == "fetched"
