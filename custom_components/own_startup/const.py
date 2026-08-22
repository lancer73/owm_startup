"""Constants for the OpenWeatherMap Startup-plan integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "owm_startup"
MANUFACTURER: Final = "OpenWeather"
ATTRIBUTION: Final = "Weather data © OpenWeather, licensed under ODbL"
# Shown on the device page, which is the only place in the integrations UI
# where free-form text from a custom integration is rendered.
DEVICE_MODEL: Final = "Startup plan — data © OpenWeather (ODbL)"

DEFAULT_NAME: Final = "OpenWeatherMap"

CONF_LANGUAGE: Final = "language"
DEFAULT_LANGUAGE: Final = "en"

CONF_BASEMAP_URL: Final = "basemap_url"
CONF_BASEMAP_ATTRIBUTION: Final = "basemap_attribution"
# CARTO, the same source the Home Assistant frontend uses. OpenStreetMap's own
# tile servers are deliberately not the default: their usage policy forbids
# distributing an application that fetches from them.
#
# The dark style, not the light one HA defaults to: the cloud layer is white
# with rising alpha and precipitation is pale blue, so both are close to
# invisible over a light basemap.
DEFAULT_BASEMAP_URL: Final = "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"
DEFAULT_BASEMAP_ATTRIBUTION: Final = "© OpenStreetMap contributors © CARTO"

# Weather map rendering. A 3x3 grid is fetched and a MAP_VIEW-sized window is
# cropped from it, centred exactly on the configured coordinates. A 2x2 grid
# cannot centre: the point sits wherever it happens to fall inside its tile.
# At zoom 8 the 512 px view is about 190 km across at Dutch latitudes.
MAP_ZOOM: Final = 8
MAP_GRID: Final = 3
MAP_TILE_SIZE: Final = 256
MAP_VIEW: Final = 512
LEGEND_HEIGHT: Final = 66
# Fraction of painted pixels trimmed from each end before deciding the range
# to plot, so resampling artefacts at colour boundaries do not stretch it.
LEGEND_TRIM: Final = 0.02
LEGEND_TICKS: Final = 6

CONF_CONTRAST_STRETCH: Final = "contrast_stretch"
DEFAULT_CONTRAST_STRETCH: Final = True

# Ramps used when the contrast stretch is on. OpenWeather's own palettes barely
# move over the few degrees or millimetres a 200 km view actually spans, so the
# observed range is re-mapped across one of these instead.
#
# Each ramp is a list of RGBA stops spread evenly from the low end of the
# observed range to the high end. Alpha is kept well below opaque so place
# names and coastlines on the basemap stay readable underneath.
STRETCH_RAMPS: Final = {
    # Diverging blue to red: cold reads cold, warm reads warm.
    "temp_new": (
        (49, 54, 149, 125),
        (116, 173, 209, 125),
        (224, 243, 248, 125),
        (254, 224, 144, 125),
        (244, 109, 67, 130),
        (165, 0, 38, 140),
    ),
    # White throughout, as the source layer is; only the opacity is stretched.
    "clouds_new": (
        (255, 255, 255, 25),
        (255, 255, 255, 90),
        (255, 255, 255, 160),
        (255, 255, 255, 235),
    ),
    "precipitation_new": (
        (160, 225, 255, 110),
        (80, 160, 255, 150),
        (30, 80, 245, 185),
        (140, 30, 200, 205),
    ),
}

# OpenWeather's documented default palettes for the Weather Maps 1.0 layers.
# Precipitation stops below 1 mm have zero alpha, which is why a drizzly day
# still renders an empty tile.
LEGENDS: Final = {
    "temp_new": {
        "title": "Temperature",
        "unit": "°C",
        "stops": (
            (-65.0, (130, 22, 146, 255)),
            (-40.0, (130, 22, 146, 255)),
            (-30.0, (130, 87, 219, 255)),
            (-20.0, (32, 140, 236, 255)),
            (-10.0, (32, 196, 232, 255)),
            (0.0, (35, 221, 221, 255)),
            (10.0, (194, 255, 40, 255)),
            (20.0, (255, 240, 40, 255)),
            (25.0, (255, 194, 40, 255)),
            (30.0, (252, 128, 20, 255)),
        ),
    },
    "clouds_new": {
        "title": "Cloud cover",
        "unit": "%",
        "stops": (
            (0.0, (255, 255, 255, 0)),
            (10.0, (253, 253, 255, 26)),
            (20.0, (252, 251, 255, 51)),
            (30.0, (250, 250, 255, 77)),
            (40.0, (249, 248, 255, 102)),
            (50.0, (247, 247, 255, 128)),
            (60.0, (246, 245, 255, 191)),
            (70.0, (244, 244, 255, 255)),
            (80.0, (243, 242, 255, 255)),
            (90.0, (242, 241, 255, 255)),
            (100.0, (240, 240, 255, 255)),
        ),
    },
    "precipitation_new": {
        "title": "Precipitation",
        # An intensity, not an accumulation: the scale tops out at 140, which
        # only makes sense as a rate. OpenWeather does not state the unit for
        # this layer outright; mm/h is the reading consistent with the stops
        # and with the Maps 2.0 intensity layer.
        "unit": "mm/h",
        # Stops below 1 are painted with zero alpha. Pixels carrying colour but
        # no opacity are still data, and are counted as such for this layer.
        "zero_alpha_is_data": True,
        "visible_from": 1.0,
        "stops": (
            (0.0, (225, 200, 100, 0)),
            (0.1, (200, 150, 150, 0)),
            (0.2, (150, 150, 170, 0)),
            (0.5, (120, 120, 190, 0)),
            (1.0, (110, 110, 205, 77)),
            (10.0, (80, 80, 225, 179)),
            (140.0, (20, 20, 255, 230)),
        ),
    },
}
USER_AGENT: Final = "owm_startup (+https://github.com/lancer73/owm_startup)"
# Basemap tiles are static, but not permanently: refetch after this long.
BASEMAP_MAX_AGE: Final = 30 * 24 * 3600

# Fixed operating parameters. These are not configurable: each one is already
# at the maximum the Startup plan usefully supports, and polling faster than
# the upstream refresh only burns call quota.
FORECAST_DAYS: Final = 16
FORECAST_STEP_HOURS: Final = 3
# /data/2.5/forecast returns 3-hour steps, at most 40 of them (120 hours).
FORECAST_STEPS: Final = 40
SCAN_INTERVAL_MINUTES: Final = 60

# Air quality forecast windows, as offsets in local calendar days from today.
# Today's window shortens as the day goes on, which reads more naturally than
# a rolling 24-hour horizon.
AQ_FORECAST_DAYS: Final = (0, 1)
AQ_DAY_SLUGS: Final = {0: "today", 1: "tomorrow"}

# Languages accepted by the OpenWeatherMap `lang` parameter.
LANGUAGES: Final = [
    "af",
    "al",
    "ar",
    "az",
    "bg",
    "ca",
    "cz",
    "da",
    "de",
    "el",
    "en",
    "eu",
    "fa",
    "fi",
    "fr",
    "gl",
    "he",
    "hi",
    "hr",
    "hu",
    "id",
    "it",
    "ja",
    "kr",
    "la",
    "lt",
    "mk",
    "no",
    "nl",
    "pl",
    "pt",
    "pt_br",
    "ro",
    "ru",
    "sk",
    "sl",
    "sp",
    "sr",
    "sv",
    "th",
    "tr",
    "ua",
    "uz",
    "vi",
    "zh_cn",
    "zh_tw",
    "zu",
]

# OpenWeatherMap air quality index scale (1 = Good ... 5 = Very Poor).
AQI_LABELS: Final = {
    1: "good",
    2: "fair",
    3: "moderate",
    4: "poor",
    5: "very_poor",
}
