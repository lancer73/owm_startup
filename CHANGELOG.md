# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-08-23

First stable release. Entity ids, attribute names and options changed from
0.1.0; see **Changed** and **Removed** below before upgrading.

### Added

- 3-hourly forecast from `/data/2.5/forecast`, exposed through
  `WeatherEntityFeature.FORECAST_HOURLY`. The entity advertises an hourly
  forecast whose points are three hours apart, which is what the plan provides.
- Weather map image entities for temperature and clouds, from the Weather Maps
  1.0 tile service. A 3x3 tile grid is fetched concurrently and composited
  server-side over a basemap, then cropped to a 512 px window centred on the
  configured coordinates. The API key never reaches the browser.
- Legend strip below each map: a continuous gradient scaled to the value range
  actually present in the view, with rounded ticks, the fetch time, and
  attribution. Legend text follows the configured language.
- Contrast stretch for the maps, on by default. OpenWeather's palettes cover
  the whole globe, so a 200 km view of a few degrees renders as nearly one
  colour; the observed range is re-mapped across a per-layer ramp instead, and
  the legend follows the same ramp.
- Marker at the configured location on both maps, and a wind arrow on the cloud
  map drawn from the current-weather payload at no extra API cost.
- Basemap fetched once and cached on disk, keyed by tile URL so changing the
  style refetches. Defaults to the CARTO dark style; configurable, or blank for
  no basemap.
- Enum sensors carrying OpenWeather's qualitative air quality band (Good
  through Very poor) for the current reading and for the today and tomorrow
  windows, with translated states and the numeric index as an attribute.
- Diagnostics download, with the API key, coordinates and place names redacted.
- Log filter that scrubs the API key from every record this package emits,
  including those written by the update coordinator.
- Brand icon in `custom_components/owm_startup/brand/`, original artwork rather
  than the OpenWeather logo, and `icons.json` for entity icons.
- ODbL notice in the setup and options dialogs, on the device page, and burned
  into each map image.
- Test suite using `pytest-homeassistant-custom-component`, with `scripts/setup`,
  `scripts/test` and `scripts/lint`, and GitHub Actions workflows for hassfest,
  HACS validation, ruff and pytest.
- `LICENSE` (MIT), `SECURITY.md`, `CLAUDE.md`, and wiki pages covering plan
  differences, ODbL obligations and related projects.

### Changed

- Air quality forecast sensors now cover local calendar days, today and
  tomorrow, instead of a configurable rolling horizon. Today's window runs from
  now to local midnight and reads `unknown` once the day is over. Entity ids
  and unique ids changed accordingly.
- Forecast sensor attribute `horizon_hours` replaced by `window`,
  `window_start` and `window_end`. `peak_at` and the AQI timeline are local
  timestamps rather than UTC.
- Poll interval fixed at 30 minutes. Upstream refreshes every two hours, but
  polling is not aligned to it, so a longer interval adds its own length to the
  worst-case staleness.
- An authentication failure limited to the air quality endpoints no longer
  triggers reauthentication: the weather endpoints authenticated on the same
  key in the same refresh, so it is logged and those sensors go unavailable
  instead.
- A 429 anywhere in the refresh reports as a quota problem in its own right
  rather than as a generic fetch failure.
- The API client converts only transport and decoding failures into API errors,
  so bugs in this integration surface as themselves. Responses are read through
  an `async with` block.
- Attribution reads "Weather data © OpenWeather, licensed under ODbL", and
  device pages link to the OpenWeather pricing page.

### Removed

- Options for forecast days, 3-hourly forecast horizon, air quality forecast
  horizon and update interval. Each was already at the value the Startup plan
  makes sensible, and exposing them only invited worse settings. Language, the
  basemap and the contrast stretch remain configurable.

### Fixed

- The API key could reach the logs through a chained `ClientResponseError`,
  which carries the request URL and therefore the `appid` parameter. Aiohttp
  exceptions are no longer chained; the cause is logged at debug level with
  secrets removed.

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

[Unreleased]: https://github.com/lancer73/owm_startup/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/lancer73/owm_startup/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/lancer73/owm_startup/releases/tag/v0.1.0
