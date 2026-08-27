# Working on this repository

Instructions for Claude Code and other AI assistants working in this repo.

## What this is

A Home Assistant custom integration (`custom_components/owm_startup`) for
OpenWeatherMap's classic 2.5 collection on a Startup-or-higher subscription.

## Hard rules

- **Never cut a version.** Do not change `version` in `manifest.json` and do not
  add a dated release heading in `CHANGELOG.md` unless the maintainer explicitly
  asks. New work goes under `## [Unreleased]`.
- **Never use One Call API 3.0 or 4.0.** It is a separate pay-per-call
  subscription, not part of the Startup plan. The whole point of this
  integration is to stay inside the 2.5 collection. Endpoints in use:
  `/weather`, `/forecast`, `/forecast/daily`, `/air_pollution`,
  `/air_pollution/forecast`.
- **Never log or interpolate the API key**, at any level including debug.
  Error messages are built from the endpoint path and status only. Never chain
  an aiohttp exception (`raise ... from err`): `ClientResponseError` carries
  `request_info.url`, which contains the key. Use `from None` and log the
  cause at debug through `redaction.redact()`. New secrets must be passed to
  `redaction.register_secret()`. `tests/test_redaction.py` covers this; keep it
  passing.
- **Diagnostics must stay redacted.** Anything added to `diagnostics.py` needs
  checking against `TO_REDACT` — coordinates and place names count as personal
  data, not just the key.
- **Minimal diffs.** Change what the task requires and nothing else. No drive-by
  reformatting, renaming, or restructuring.

## Conventions

- Semantic Versioning; changelog in [Keep a Changelog](https://keepachangelog.com/)
  format.
- Ruff for linting and formatting — config is in `pyproject.toml`. Run
  `scripts/lint` before proposing changes.
- Home Assistant conventions: config flow with reauth, `DataUpdateCoordinator`,
  `entry.runtime_data`, `_attr_has_entity_name`, translation keys for every
  user-visible string.
- Every user-facing string goes in `strings.json` **and** both
  `translations/en.json` and `translations/nl.json`. `translations/en.json` is a
  copy of `strings.json`.
- New sensors need a `device_class` only when the unit actually matches what
  that device class expects. CO is reported in µg/m³ and therefore has no
  device class; do not "fix" this.
- Forecast length (16 days), 3-hourly steps (40), air quality windows
  (today and tomorrow, as local calendar days) and the poll interval (30 min)
  are fixed constants in
  `const.py`, deliberately not options. Do not turn them back into settings.
- `POLLUTANT_BANDS` in `const.py` is OpenWeather's published table, not a
  general air quality scale. Do not substitute EAQI, CAQI or EPA breakpoints:
  they differ, and the index this integration reports is OpenWeather's.
  Boundaries are `[lower, upper)`, so `band_for` uses `bisect_right`.
- NH3 and NO use `BACKGROUND_BANDS`, not `POLLUTANT_BANDS`, and the vocabulary
  `low/typical/elevated/high` rather than `good/fair/...`. This separation is
  the point: there is no ambient health limit for either, and the only
  published ammonia numbers are occupational limits some 87x above the maximum
  this API can report. Do not merge the two vocabularies, and do not relabel
  the background states as health states.
- The NH3 boundaries come from RIVM/CLO measurements; the NO boundaries are
  derived from the RIVM NOx-minus-NO2 difference and are the weaker of the two.
  If better NO figures turn up, update the constant and the citation together.
- The `forecast` attribute on the AQI band sensors is a documented interface:
  the README's chart example reads `datetime` and `aqi` from it by name. There
  is a test holding that shape; renaming either key breaks the example.
- Forecasts are bands, current readings are numbers. Do not add numeric
  forecast sensors back: a microgram figure two days out is false precision,
  and the peak value is already an attribute.
- Icon translations set the glyph only. There is no colour field in
  `icons.json`; do not add one or promise colour from the integration. The
  health bands escalate through the glyph, the background bands deliberately
  do not.
- The air quality band sensors are `SensorDeviceClass.ENUM`. Their states are
  the untranslated keys, never the display names: changing them would break
  every template that reads them. Translation happens in `strings.json`.
- Forecast sensors must not have a `state_class` — a rolling maximum would
  corrupt long-term statistics.

- **Never default the basemap to `tile.openstreetmap.org`.** Their usage policy
  forbids distributing an application that fetches from their servers, and
  counts pre-emptive fetching as bulk downloading. The default is CARTO, the
  same source the Home Assistant frontend uses, and basemap tiles are cached on
  disk after the first fetch.
- Map tiles are composited server-side so the API key stays out of the browser.
  Do not "simplify" this by handing the frontend a tile URL.
- Pillow work is blocking; keep it in an executor.
- The wind arrow points the way the wind is going: OpenWeather's `deg` is the
  direction it comes *from*, so the drawing adds 180 degrees. There is a test
  asserting the arrow lands up-right for a southwesterly; do not "fix" it.
- A field of wind arrows is not possible on this plan. Maps 2.0 `WND`/`WNDUV`
  with `arrow_step` renders them, but that is Developer tier; Maps 1.0
  `wind_new` is a speed raster with no direction in it.
- The animation progress bar is drawn at assembly time, not at capture time: a
  frame's position in the sequence changes as frames are added and pruned. It
  fills by elapsed time between the first and last frame, so a capture gap is
  visible as a jump. Do not switch it to frame index for smoothness; the
  unevenness is the signal.
- Anything written under `.storage/owm_startup_*` needs a matching branch in
  `async_remove_entry`. Frames are per entry; the basemap cache is shared and
  only goes with the last entry.
- Capture runs after the coordinator update, not during it. Anything reporting
  on the sequence must subscribe via `FrameStore.add_listener`, or it will be a
  cycle behind.
- Per-frame WebP durations are written by passing a list to `save(duration=)`.
  Pillow does not expose them again on read, so the test walks the ANMF chunks
  in the container. Do not "fix" that test to use `Image.info`; the value is
  not there.
- One frame is served as a still, not withheld. An image entity that returns
  None renders as broken, and "still filling" is not broken. Only zero frames
  returns None.
- `FrameStore.probe_hash` must only advance after a frame is stored. Setting it
  when the probe is read loses the frame for good if the grid fetch then fails.
- Frame capture compares decoded pixels, never file bytes: an upstream
  re-encode of identical data would store a duplicate frame on every poll.
- The probe fetches one tile before committing to nine. Do not "simplify" it
  into always fetching the grid; the saving is roughly 3 refreshes in 4.
- `async_capture_if_changed` runs unawaited off the coordinator update, so it
  catches broadly on purpose. That is the one place in this codebase where a
  bare `except Exception` is correct: the alternative is an unhandled task
  error and a missing frame either way.
- `async_image` holds `_render_lock` and re-checks the cache inside it. Both
  the frontend and the background capture call it, and each render is nine tile
  fetches; removing the double check reintroduces duplicate grids.
- `async_capture_if_changed` skips when `_capturing` is set rather than
  queueing. Two captures in flight each pull a grid.
- Catch `ContentTypeError` before `ClientResponseError`. It is a subclass but
  carries status 0, so the order matters for the message.
- Weather map tiles are all-or-nothing on purpose: no per-tile cache, no
  fallback to an earlier tile, no partial grid. A map assembled from tiles of
  different ages looks plausible and is wrong. Two tests hold this in place.
- A mixed grid is shown but not captured, and re-rendered on the next scheduled
  refresh via `_force_next`. Keep those two decisions apart: withholding the
  image breaks the dashboard, storing the frame poisons the animation for
  twelve hours. The retry passes `force=True` so it skips the probe, and must
  leave the store hashes untouched or it will look already captured.
- `async_create_background_task` is not awaited by `async_block_till_done`, so
  a test that schedules a capture and then asserts on stored frames is racing
  it. Assert on the call, or await `async_capture_if_changed` directly.
- Mismatched tiles across a seam are an upstream artefact, not a rendering bug.
  `seam_mismatch` compares the step at a tile boundary against the gradient
  beside it; tuning `SEAM_FLOOR` or `SEAM_RATIO` changes how readily the banner
  appears, but do not "fix" the render in response to a report of it.
- Draw translucent map furniture on its own layer and `alpha_composite` it.
  `ImageDraw` replaces pixels rather than blending, so a translucent halo drawn
  straight onto the canvas punches a hole in the map.
- Legend text is burned into the image, so it cannot use Home Assistant's
  translations. Add new strings to `legend.TRANSLATIONS` in every language
  there — a test enforces that the tables match. Keep the text ASCII-safe
  apart from "©": the bitmap fallback font cannot draw "·".
- The basemap cache key includes a hash of the tile URL. Do not simplify it
  back to z/x/y: switching styles then silently reuses the old tiles.
- The precipitation map was removed deliberately and completely. If it is ever
  reinstated, two findings from the first attempt are worth not rediscovering:
  `precipitation_new` is an intensity in mm/h rather than an accumulation, and
  every palette stop below 1 has zero alpha, so light rain is drawn in colour
  but invisible and needs special handling to register at all.
- Legend palettes in `const.LEGENDS` are OpenWeather's documented defaults for
  Weather Maps 1.0. Do not invent stops: if a layer is added, take its palette
  from their map legend page. Stops are (value, RGBA) pairs in ascending value
  order — range detection depends on that ordering.
- `legend.value_for_colour` inverts the palette by projecting a pixel onto each
  stop-to-stop segment. Do not replace it with a tabulated lookup: tabulating
  the full -65..30 range quantises a 3 degree window into a handful of values,
  which is exactly the bug the stretch exists to avoid.
- The legend is scaled to the range observed in the view, not the full palette.
  Range detection runs on the overlay alone (`overlay_only`), never on the
  composited image: matching colours through a basemap would be meaningless.
- The temperature stretch is anchored to the day's forecast range via
  `daily_range` on the description, unioned with the observed range. Do not
  drop the anchor back to per-frame fitting: identical temperatures would be
  drawn in different colours from frame to frame. Do not drop the union either:
  a value outside the forecast range would clip.
- The contrast stretch is per layer, via `stretch_option` and
  `stretch_default` on `OwmMapDescription`. Do not collapse it back into one
  option: it helps temperature and hurts cloud, especially in the animation
  where per-frame range fitting makes noise flicker.
- Maps fetch a 3x3 grid and crop a centred window. Do not "optimise" this to
  2x2: with an even grid the point cannot be centred, it lands wherever it
  falls inside its own tile.

- Do not wrap arbitrary exceptions in `OwmError`. Only transport and decoding
  failures are API errors; a broad `except Exception` hid a live `TypeError` in
  the basemap path for several revisions. The redaction log filter, not a
  catch-all, is what keeps the key out of the logs.
- Auth failures from the air quality endpoints are deliberately not
  `ConfigEntryAuthFailed`. The weather endpoints authenticate on the same key
  in the same refresh, so demanding reauth would be the wrong diagnosis.
- When patching `async_get_clientsession` in tests, patch it on the module that
  imported it, not on `homeassistant.helpers.aiohttp_client`.

## Testing

```bash
scripts/setup   # create .venv and install test dependencies
scripts/test    # pytest with coverage
scripts/lint    # ruff check + format --check
```

Tests use `pytest-homeassistant-custom-component`. API responses are mocked at
the client level via the `mock_api` fixture in `tests/conftest.py`; fixture
JSON lives in `tests/fixtures/` and has its timestamps rebased onto the current
time at load. To simulate an endpoint failing, assign an exception instance:

```python
mock_api["air"] = OwmConnectionError("boom")
```

Any behaviour change needs a test. Failure-handling changes especially: the
distinction between fatal (`/weather`, `/forecast/daily`) and non-fatal
(`/forecast`, air quality) endpoints is deliberate and load-bearing.

## Licensing and attribution

Weather data is ODbL. Attribution must stay visible in the setup dialog, the
options dialog, the device page (`model` field) and the entity `attribution`
attribute. Do not remove or shorten these.

The brand images in `custom_components/owm_startup/brand/` are original work.
Do not replace them with OpenWeather's logo — ODbL grants no trademark rights.
