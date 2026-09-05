# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]


## [2.2.0] - 2026-09-05

The basemap now comes from Home Assistant itself, which raises the minimum
version to 2026.9.0 and removes the last third-party dependency.

### Changed

- **The basemap comes from Home Assistant's map tiles integration** rather than
  CARTO, so there is no API key and no third-party terms. That integration
  proxies OpenStreetMap's raster tiles server-side with an identifying
  User-Agent, which is what their policy asks for and what a browser cannot
  send. Its access token rotates every thirty minutes and is added per request,
  so it neither lives in the stored option nor churns the tile cache.
- Those tiles are the light OpenStreetMap style, so they are inverted,
  hue-rotated and desaturated on the way into the cache. The weather overlay is
  then the only thing on the image carrying colour: a green field or a red
  motorway under a blue-to-red temperature ramp reads as data that is not
  there.
- **The basemap source and attribution options are gone.** There is one correct
  source, it is free and it ships with Home Assistant, so a provider option
  could only be set to something worse. Saved values are ignored.
- **Minimum Home Assistant version is now 2026.9.0**, where the map tiles
  integration arrived.
- Weather map overlays are much less opaque. The new basemap carries far more
  detail and no colour of its own, so the overlay can sit lighter over it and
  still read clearly.
- The test workflow reports the Home Assistant version it resolved and fails if
  it is older than the minimum in `hacs.json`. The test harness pins whichever
  version it was built against, so a stale resolution would otherwise produce a
  green run that says nothing about the supported version.

### Fixed

- Cached basemap tiles survived a change to the transform applied to them. The
  tiles are stored already darkened, but the cache was keyed only on their
  source, so changing that transform left the old form in place for the thirty
  days of its lifetime. The render revision is now part of the cache directory
  name, and stale directories are pruned on the next fetch.
- Stored animation frames survived a change of renderer, so switching basemap
  left the animation mixing old and new styles for up to twelve hours. Frames
  now carry a signature covering the render revision and the contrast stretch,
  and are discarded when it no longer matches.
- Credential-looking query parameters are scrubbed from log messages by pattern
  as well as by registered value. The proxy's rotating token could not be
  registered without the secret set growing without bound.

## [2.1.1] - 2026-08-28

### Fixed

- Tile seam detection missed most real mismatches. It ran on the raw tiles,
  where a mixed grid can differ by as little as one step of OpenWeather's
  palette — 7 units of summed RGBA against a floor of 8 — while the contrast
  stretch makes that same difference obvious on screen. It now runs on the
  stretched pixels, which is what the reader sees. It also averaged each seam
  over its full length, hiding a step covering only part of a boundary; seams
  are now examined in segments.

### Changed

- README restructured: what the integration provides, then installation and
  configuration, then dashboard use, then how to obtain a Startup plan.
  Reasoning about the polling interval, which was internal design rationale
  rather than anything a reader acts on, has been dropped, and troubleshooting
  gathered into one section.

## [2.1.0] - 2026-08-27

### Added

- A weather map whose tiles disagree across a seam is no longer captured into
  the animation. The map is still shown, and the next scheduled refresh
  re-renders it with the probe tile skipped, so an unchanged centre tile cannot
  hold a bad map in place. Bounded at two attempts, so a sharp front sitting on
  a tile boundary cannot starve the sequence.
- Second README chart example, for a single pollutant across four days: a
  measured line from recorder history, one marked point per forecast window,
  and annotation lines at OpenWeather's band boundaries.
- README and options-dialog instructions for the CARTO API key, required on
  their raster basemaps since 26 August 2026. Unkeyed requests return
  watermarked tiles, which this integration would otherwise cache for 30 days
  and bake into the animation. Changing the URL changes the cache key, so the
  watermarked tiles are dropped automatically.
- Credential-looking query parameters in the basemap URL (`key`, `api_key`,
  `token` and similar) are registered for log redaction, so a transport error
  quoting the URL cannot leak them.

### Changed

- The temperature map's contrast stretch is fitted to today's forecast range,
  widened to cover anything in view, instead of to each frame's own contents.
  Consecutive frames now share a scale, so an animation shows the day warming
  rather than every frame looking the same.
- The contrast stretch is set per layer rather than for both maps at once, and
  defaults to on for temperature and off for cloud. Cloud cover already uses
  the full range of its palette, so stretching it amplifies noise, which
  flickers between frames in the animation. The single `contrast_stretch`
  option is replaced by `contrast_stretch_temperature` and
  `contrast_stretch_clouds`; a previously saved value is not carried over, so
  check the options page after upgrading.

### Fixed

- The temperature stretch anchor was computed but never passed to the renderer,
  so the maps were still being fitted per frame.

## [2.0.0] - 2026-08-26

Air quality is now published as qualitative bands, and the maps gained a
12-hour animation. Entity ids and unique ids changed; see **Removed** and
**Changed** before upgrading.

### Added

- Qualitative band sensors for every pollutant OpenWeather publishes a scale
  for: the index plus PM2.5, PM10, O₃, NO₂, SO₂ and CO, for the current reading
  and for both forecast windows. Boundaries come from OpenWeather's published
  table.
- Background band sensors for NH₃ and NO, which OpenWeather scores but does not
  scale. These compare against Dutch ambient measurements rather than health
  limits, and use a separate vocabulary (Low, Typical, Elevated, High) so they
  cannot be read as an air quality verdict.
- State-based icons on the health band sensors, escalating from an empty gauge
  at Good to an alert at Very poor. Icon colour is not settable by an
  integration; the README shows how to colour them on a dashboard.
- Animated map images covering the last 12 hours, one per layer, as animated
  WebP. Frames accumulate going forwards only, since Weather Maps 1.0 has no
  time parameter. Capture is driven by a single probe tile, so an unchanged
  refresh costs one call instead of nine, and any render requested by the
  frontend is stored for free. A sequence holding a single frame is served as a
  still, so a filling sequence does not look like a broken entity.
- Progress bar on the animated maps, tracking elapsed time between the first
  and last frame, so a gap in capture shows as a jump. The newest frame is held
  about three times as long as the others.
- Tile seam detection on the weather maps. OpenWeather occasionally serves a
  grid assembled from more than one model run, leaving a straight step along a
  tile boundary; the map carries a banner saying so instead of presenting the
  step as weather, and logs a warning.
- Per-tile `Last-Modified`, `Age` and `Date` headers logged at debug level, as
  evidence for which tiles in a mismatched grid are stale.
- Removing a config entry deletes its captured frames, and the basemap cache
  once the last entry is gone. Frames are keyed by entry id, so a
  remove-and-re-add previously left an unreachable directory of images behind.
- README example for charting air quality across yesterday, today and tomorrow
  with apexcharts-card, taking the past from the numeric index sensor's
  recorded history and the future from the forecast attributes. A test pins the
  attribute names it depends on.

### Changed

- The setup dialog states that the sub-daily forecast is published through Home
  Assistant's hourly forecast but carries 3-hour points, which is what the
  Startup plan supplies.

### Removed

- The numeric air quality forecast sensors. Forecasts are published as bands
  only; the peak concentration behind each band remains as an attribute.
- Registry cleanup for entities removed in earlier versions.

### Fixed

- A map render could be started twice at once, by a frontend request arriving
  while a background capture was in flight, and a capture slower than the
  refresh interval could stack up. Renders are now serialised per layer and an
  overlapping capture is skipped rather than queued. Background captures are
  registered against the config entry so they are cancelled on unload.
- A 200 response carrying HTML instead of JSON was reported as "HTTP 0".
  `aiohttp` raises `ContentTypeError` from `json()`, which is a
  `ClientResponseError` subclass carrying status 0; it is now caught first and
  reported as a malformed response.
- The animated map reported a frame count one refresh out of date. Capture is
  scheduled from the coordinator update rather than run inside it, so the
  attributes were published before the frame landed. The store now notifies the
  entity, which republishes and drops its cached animation.
- Hashing the tile grid decoded nine PNGs on the event loop.
- A tile that arrived truncated raised out of Pillow inside the executor, past
  the fetch error handling, and surfaced as a traceback. Undecodable tiles now
  fail the render cleanly with a logged warning.

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

[Unreleased]: https://github.com/lancer73/owm_startup/compare/v2.2.0...HEAD
[2.2.0]: https://github.com/lancer73/owm_startup/compare/v2.1.1...v2.2.0
[2.1.1]: https://github.com/lancer73/owm_startup/compare/v2.1.0...v2.1.1
[2.1.0]: https://github.com/lancer73/owm_startup/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/lancer73/owm_startup/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/lancer73/owm_startup/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/lancer73/owm_startup/releases/tag/v0.1.0
