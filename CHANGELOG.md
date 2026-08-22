# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- 3-hourly forecast (up to 5 days) from `/data/2.5/forecast`, exposed through
  `WeatherEntityFeature.FORECAST_HOURLY`, with a configurable horizon.
- ODbL licence notice in the setup and options dialogs, and on the device page
  via the device model field.
- Wiki pages covering plan differences, ODbL obligations and related projects.
- Brand icon shipped in `custom_components/owm_startup/brand/`, original
  artwork rather than the OpenWeather logo.
- `icons.json` with entity icons for the air quality sensors.
- Test suite using `pytest-homeassistant-custom-component`, with mocked API
  responses and JSON fixtures, plus `scripts/setup`, `scripts/test` and
  `scripts/lint`.
- GitHub Actions workflows for hassfest, HACS validation, ruff and pytest.
- `LICENSE` (MIT) and `SECURITY.md`, matching the versions used in
  ha-weather-uploader, and `CLAUDE.md`.
- Weather map image entities for temperature, clouds and precipitation, from
  the Weather Maps 1.0 tile service. A 3x3 tile grid is composited server-side
  over a cached basemap and cropped to a 512 px window centred on the
  configured coordinates, with attribution burned into the image.
- Debug logging of how much of each map layer carries data, so an empty
  precipitation layer can be told apart from a failed fetch.
- Contrast stretch for the weather maps, on by default and switchable in the
  options: values present in the view are re-mapped across a per-layer ramp so
  a narrow range is actually visible, with the legend following the same ramp.
- Fetch timestamp in the top right of each map's legend strip, so a stale
  image is recognisable as one.
- Legend strip below each weather map. The bar is a continuous gradient scaled
  to the value range actually present in the view, detected by matching painted
  colours back through OpenWeather's documented palette, with rounded ticks.
  Attribution moved into the strip so it no longer sits over the map.
- Options for the basemap tile URL and its attribution, defaulting to the CARTO
  style the Home Assistant frontend uses.
- Diagnostics download for a config entry, with the API key, coordinates and
  place names redacted.
- Log filter that scrubs the API key from every record this package emits,
  including those written by the update coordinator.

### Removed

- Options for forecast days, 3-hourly forecast horizon, air quality forecast
  horizon and update interval. These are now fixed at 16 days, 120 hours,
  today/tomorrow windows and 60 minutes. Language remains the only option.

### Fixed

- Precipitation below 1 mm/h is painted by OpenWeather at zero opacity. Those
  pixels were skipped entirely, so light rain reported as "no data in view" and
  never appeared. They are now counted as data and, with the contrast stretch
  on, drawn. The exception is scoped to precipitation: a zero-alpha pixel in
  the cloud layer means clear sky.
- The precipitation legend read `mm`, implying an accumulation. The layer is an
  intensity, so it now reads `mm/h`.
- Overlay sampling for range detection used interpolating resampling, which
  invented colours between palette bands and widened the reported range.

- Map tiles were fetched one at a time: nine sequential round trips per map,
  three maps. They are now fetched concurrently. The call count is unchanged.
- HTTP responses were read without a context manager, so connections were
  released late. Both the API client and basemap fetches now use `async with`.
- A 429 anywhere in the refresh now reports as a quota problem in its own right
  instead of reading as a generic fetch failure.
- The API client no longer converts arbitrary exceptions into API errors. Only
  transport and decoding failures are wrapped, so bugs in this integration
  surface as themselves.

- Basemap tiles were cached under a key that ignored the tile URL, so changing
  the basemap style left previously cached tiles in place and produced a view
  that was part light, part dark. The style is now part of the cache key and
  unused styles are pruned.
- Legend text was always English; it now follows the configured language.
- Tick labels were rounded by magnitude, so a one degree range printed the same
  label several times. Precision now follows the tick spacing.
- Font selection tries several system paths and Pillow's sized default before
  the bitmap fallback, which cannot render "·" and collapses spacing. The
  middle dot is gone from all legend text regardless.

### Changed

- Air quality forecast sensors now come in two sets covering local calendar
  days: today and tomorrow. Today's window runs from now to local midnight and
  reads `unknown` once the day is over.
- Forecast sensor attribute `horizon_hours` replaced by `window`,
  `window_start` and `window_end`. `peak_at` and the AQI timeline are now local
  timestamps rather than UTC.
- Default basemap is now CARTO's dark style. The cloud layer is white with
  rising alpha and precipitation is pale blue, so both were nearly invisible
  over the light style.
- An authentication failure limited to the air quality endpoints no longer
  triggers reauthentication: the weather endpoints authenticated on the same
  key, so it is logged and the sensors go unavailable instead.
- Weather map overlays are less opaque, so place names and coastlines on the
  basemap stay readable underneath.
- Basemap tiles expire after 30 days.
- Attribution now reads "Weather data © OpenWeather, licensed under ODbL".
- API client no longer chains aiohttp exceptions: `ClientResponseError` carries
  the request URL, which contains the key as a query parameter. The underlying
  error is logged at debug level with secrets removed instead.
- Device pages link to the OpenWeather pricing page.

## [0.1.0] - 2026-08-21

### Added

- Weather entity with current conditions and a daily forecast of up to 16 days,
  sourced from `/data/2.5/weather` and `/data/2.5/forecast/daily`.
- Nine current air quality sensors (AQI, PM2.5, PM10, O₃, NO₂, NO, SO₂, CO, NH₃)
  from `/data/2.5/air_pollution`.
- Nine air quality forecast sensors reporting the peak value within a
  configurable horizon, from `/data/2.5/air_pollution/forecast`.
- Config flow with API key validation against the daily forecast endpoint, and a
  reauthentication flow.
- Options flow for forecast days, air quality horizon, update interval and
  language.
- English and Dutch translations.

[Unreleased]: https://github.com/lancer73/owm_startup/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/lancer73/owm_startup/releases/tag/v0.1.0
