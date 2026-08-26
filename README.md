# OpenWeatherMap (Startup plan) for Home Assistant

A Home Assistant custom integration that uses only the endpoints included in the
OpenWeatherMap **Startup** subscription of the classic 2.5 collection. It does
**not** use One Call 3.0/4.0, which is a separate pay-per-call product.

## What it provides

| Entity | Source endpoint |
| --- | --- |
| `weather.*` — current conditions, Home Assistant hourly forecast carrying OpenWeather's 3-hour points (5 days), daily forecast up to 16 days | `/data/2.5/weather`, `/data/2.5/forecast`, `/data/2.5/forecast/daily` |
| 9 current air quality sensors | `/data/2.5/air_pollution` |
| 9 current air quality band sensors | `/data/2.5/air_pollution` |
| 18 forecast air quality band sensors (today and tomorrow) | `/data/2.5/air_pollution/forecast` |
| 2 weather map images (temperature, clouds) | `tile.openweathermap.org/map` |
| 2 animated map images, last 12 hours | frames kept from the above |

Air quality sensors: AQI, PM2.5, PM10, O₃, NO₂, NO, SO₂, CO, NH₃.

### Bands

Every pollutant OpenWeather publishes a scale for — the index plus PM2.5, PM10,
O₃, NO₂, SO₂ and CO — also gets an **enum** sensor carrying the qualitative
band: Good, Fair, Moderate, Poor, Very poor. Boundaries are OpenWeather's own,
from the table in their Air Pollution documentation.

**NH₃ and NO are scored differently.** OpenWeather lists them as parameters that
do not affect the index and publishes no scale for them, and there is no ambient
health limit for either — the only published ammonia figures are occupational
limits for an 8-hour shift, roughly 17,000 µg/m³, which is 87× the maximum this
API can report. A health band built on those would read "Good" at every possible
value.

Instead they are scored **against Dutch ambient background**, with a deliberately
different vocabulary — Low, Typical, Elevated, High — so a dashboard cannot
mistake them for the index. Entities are named `... vs background`.

| | Low | Typical | Elevated | High |
| --- | --- | --- | --- | --- |
| NH₃ | < 2 | 2–8 | 8–15 | ≥ 15 |
| NO | < 2 | 2–10 | 10–25 | ≥ 25 |

µg/m³. NH₃ comes from RIVM/CLO measurements: the national mean across 35 sites
was 5.4 µg/m³ in 2024 (4.8 in 2023, 6.7 in 2022), the lowest values 1–2 at the
coast, rising to about 15 in intensive livestock areas. NO is derived rather than
published: RIVM reports NOx and NO₂ separately, and the difference gives roughly
2 µg/m³ regional, 5 urban and 15 traffic-exposed once converted from
NO₂-equivalent. Treat the NO boundaries as the softer of the two.

These are annual means applied to hourly model values, so read them as "how does
this hour compare with a normal year around here", not as a measurement or a
health verdict.

Sensor states are the untranslated keys (`good`, `fair`, `moderate`, `poor`,
`very_poor`), so templates and automations stay stable while the frontend shows
the translated label. The number behind each band is kept as an attribute:
`index` for the index, `value` for a pollutant.

The health band sensors change icon with severity — an empty gauge at Good
through a full one at Poor, and an alert octagon at Very poor. The background
sensors deliberately keep one glyph: escalating them would imply a health
judgement that scale does not make.

**Colour cannot come from the integration.** Home Assistant's icon translations
set the glyph, not its colour, so state colouring is a dashboard job. With
Mushroom:

```yaml
type: custom:mushroom-template-card
entity: sensor.<name>_pm2_5_level
icon: mdi:blur
icon_color: >-
  {{ {'good': 'green', 'fair': 'light-green', 'moderate': 'orange',
      'poor': 'red', 'very_poor': 'purple'}[states(entity)] }}
primary: PM2.5
secondary: "{{ states(entity) }}"
```

Or with `card_mod` on a standard entities card, keyed on the same states. Either
way the states are the stable untranslated keys, so the mapping survives a
language change.

### Charting the forecast

Past and future come from different places. The **numeric** index sensor,
`sensor.<name>_air_quality_index`, is recorded by Home Assistant like any other
number, so apexcharts-card can pull its history. The two forecast band sensors
each carry their window's hourly timeline in a `forecast` attribute, as
`{"datetime": ..., "aqi": ...}` pairs in local time. Three series therefore
cover yesterday, today and tomorrow as one line:

```yaml
type: custom:apexcharts-card
experimental:
  color_threshold: true
graph_span: 72h
span:
  start: day
  offset: "-1d"
header:
  show: true
  title: Air quality
  show_states: true
  colorize_states: true
now:
  show: true
  label: Now
yaxis:
  - min: 1
    max: 5
    decimals: 0
    apex_config:
      tickAmount: 4
      labels:
        formatter: |
          EVAL:function (value) {
            return ["", "Good", "Fair", "Moderate", "Poor", "Very poor"][value] || "";
          }
all_series_config:
  type: area
  curve: stepline
  stroke_width: 2
  opacity: 0.35
  # Without this the last known value is padded out to the end of the graph
  # span, so each series runs across the ones after it.
  extend_to: false
  # Boundaries of OpenWeather's own index, so the colours mean what the
  # sensor means.
  color_threshold:
    - value: 1
      color: "#4caf50"
    - value: 2
      color: "#8bc34a"
    - value: 3
      color: "#ff9800"
    - value: 4
      color: "#f44336"
    - value: 5
      color: "#9c27b0"
series:
  # Measured: recorder history of the numeric sensor, up to now.
  - entity: sensor.zoetermeer_air_quality_index
    name: Recorded
    group_by:
      func: max
      duration: 1h
  # Forecast: the two windows, from now to the end of tomorrow.
  - entity: sensor.zoetermeer_air_quality_today
    name: Today
    stroke_width: 2
    data_generator: |
      const points = (entity.attributes.forecast || []).map((point) => {
        return [new Date(point.datetime).getTime(), point.aqi];
      });
      // Close the final step at the end of the window, otherwise the last
      // hour is drawn with no width.
      if (points.length && entity.attributes.window_end) {
        points.push([
          new Date(entity.attributes.window_end).getTime(),
          points[points.length - 1][1],
        ]);
      }
      return points;
  - entity: sensor.zoetermeer_air_quality_tomorrow
    name: Tomorrow
    data_generator: |
      const points = (entity.attributes.forecast || []).map((point) => {
        return [new Date(point.datetime).getTime(), point.aqi];
      });
      if (points.length && entity.attributes.window_end) {
        points.push([
          new Date(entity.attributes.window_end).getTime(),
          points[points.length - 1][1],
        ]);
      }
      return points;
```

Notes on it:

- The history series must point at `..._air_quality_index`, the number. The band
  sensor `..._air_quality` is an enum and has no numeric history to plot.
- `group_by: max` over an hour matches the hourly resolution of the forecast
  windows, so the recorded half does not look busier than the predicted half.
  `max` rather than `mean` because the index is an ordinal 1-5: averaging Good
  and Moderate into "Fair" would state something the scale does not mean.
- How far back the recorded line reaches depends on your recorder retention. If
  `purge_keep_days` is under two, yesterday will be short or empty.
- `extend_to: false` matters. The default is `end`, which pads a series' last
  known value out to the end of the graph span, so every series would run
  across the ones after it. If your card predates apexcharts-card 2.0 the
  option is spelled `extend_to_end: false` instead.
- The y-axis is the index, 1 to 5, not a concentration; the formatter puts
  OpenWeather's band names on the ticks so it reads the same way the sensors
  do.
- Today's forecast series shortens as the day goes on — its window runs from now
  to local midnight — so the handover from recorded to forecast tracks the
  current time on its own.

The same pattern works for any pollutant if you swap the entities, but only the
AQI sensors carry the hourly `forecast` attribute — the others expose the peak
and its time, not a timeline.

**Forecasts are bands only.** A microgram figure two days out reads as a
precision the model does not have, while "Moderate tomorrow" is something you
can act on. The peak concentration behind each forecast band is still there as
an attribute, along with `peak_at` and the window bounds. Current readings stay
numeric so they can be graphed and kept in long-term statistics.

OpenWeather's air pollution forecast runs hourly for about four days. Limiting
the sensors to today and tomorrow is a deliberate choice — those are the windows
worth automating on — not a plan restriction.

Forecast air quality sensors report the **worst (highest) value expected within
their window**. Two windows are created per pollutant, **today** and
**tomorrow**, as local calendar days. Today's window runs from now to local
midnight, so it shortens as the day goes on and can end up empty late at night,
at which point the sensor reads `unknown` rather than reporting a stale peak.

Attributes on every forecast sensor:

- `peak_at` — local time of the peak, or `null` if the window is empty
- `window` — `today` or `tomorrow`
- `window_start`, `window_end` — local bounds of the window

The AQI forecast sensors additionally carry their hourly timeline in a
`forecast` attribute, with local timestamps.

**The hourly forecast is not hourly.** The Startup plan has no hourly product:
`/data/2.5/forecast` returns 3-hour steps. Those are exposed through Home
Assistant's `FORECAST_HOURLY`, because that is the API for sub-daily forecasts
and dropping the feature would lose five days of forecast for the sake of a
label. So the entity advertises an hourly forecast whose points are three hours
apart, and cards, templates and `weather.get_forecasts` will all show that
spacing. The setup dialog says so too, so it is not only buried here.

The paid alternative is OpenWeather's 4-day hourly product, which is not part of
the Startup plan.

The polling interval is **30 minutes**, giving ten calls per hour, about 240 per
day. OpenWeather's pricing table gives a 2-hour data update frequency for this
plan — though that figure describes the weather *data* products, and the map
tiles have been observed changing about every three hours, which is consistent
with a model that runs on 3-hourly steps.

Polling is not aligned to that refresh, so the interval adds its own length to
the worst case: at 60 minutes it was up to two hours of data age plus an hour of
waiting for the next poll. Thirty minutes halves the second term. OpenWeather
asks that a location not be polled more often than every 10 minutes, so this
sits comfortably inside both limits, and 240 calls a day is nothing against a
10M/month allowance.

Weather map tiles are **not** part of that figure. They are fetched when the
frontend requests an image, and the rendered result is cached until the next
coordinator update. A dashboard that displays both maps continuously costs up to
36 tile calls per hour on top (nine tiles per map, two maps, twice an hour), and
nothing at all if nobody is looking.

## Requirements

- Home Assistant 2024.11 or newer (uses `entry.runtime_data` and
  `_get_reauth_entry`).
- An OpenWeatherMap API key on a plan that includes `/forecast/daily`. The free
  tier does **not**; Startup and above do. Uploading your own station data and
  then asking at info@openweathermap.org is one route onto the Startup plan —
  see the [wiki](https://github.com/lancer73/owm_startup/wiki).

## Development

```bash
scripts/setup   # virtualenv + test dependencies
scripts/test    # pytest with coverage
scripts/lint    # ruff check + format --check
```

Tests mock the API client, so no key is needed and no requests leave your
machine. See `CLAUDE.md` for the conventions this repository follows.

## Installation

Copy `custom_components/owm_startup/` into your Home Assistant `config/custom_components/`
directory and restart. Then add the integration via
**Settings → Devices & services → Add integration → OpenWeatherMap (Startup plan)**.

## Weather maps

Two image entities render Weather Maps 1.0 layers over a basemap: temperature
and clouds. The cloud map also carries a wind arrow at your location. A 3x3 tile grid is fetched at zoom 8 and a 512 px
window is cropped from it, centred exactly on your coordinates and marked with a
ring — about 190 km
across at Dutch latitudes. The images refresh on the same half-hourly cycle as
everything else.

Tiles are fetched and composited **server-side**, so the API key never reaches
the browser. A Lovelace card pointed straight at the tile URL would put the key
in your dashboard config and in every client request.

Each image carries a **legend** below the map, plus the attribution strip and a
timestamp.

The timestamp is when the tiles were **fetched**, not when the data is valid
for. Weather Maps 1.0 tiles carry no validity time, so that is the most that can
honestly be claimed. In practice the two are close: tiles are fetched when the
frontend requests an image and the render is discarded on every coordinator
update.

The legend is not the full palette. OpenWeather's temperature scale runs from
-65 to +30 °C, while a 200 km view typically spans a few degrees, so plotting
the whole thing would waste the bar. Instead the rendered overlay is sampled,
each painted colour is matched back to a value through the documented palette,
and the bar is drawn as a continuous gradient over the range actually in view,
with rounded ticks. The top 2% and bottom 2% of pixels are trimmed first so
resampling artefacts at colour boundaries do not stretch the range.

The title says `range in view`, or `no data in view — full scale` when the layer
is empty. Matching is approximate — OpenWeather resamples tiles server-side, so
boundary pixels land between palette entries. Read the range as indicative.

### Contrast stretch

On by default. OpenWeather's palettes cover the whole globe, so a 200 km view of
a 3 °C spread renders as very nearly one colour. With the stretch on, every
painted pixel is matched back to a value and re-mapped across a ramp fitted to
the observed range — blue to red for temperature, opacity for cloud, blue to
violet for precipitation — and the legend bar shows that same ramp.

Two things worth knowing:

- The recoverable precision is set by the tiles, not by us. Across a couple of
  degrees OpenWeather's palette moves one integer colour step every ~0.15 °C, so
  the stretch will show banding at roughly that spacing. That banding is the
  real resolution of the source, which the unstretched view simply hides.
- Overlay opacity is kept below full so the basemap still reads through.

Turn it off in the options to get OpenWeather's own colours.

Legend text is drawn into the image, so Home Assistant cannot translate it.
It follows the integration's configured language instead (English, Dutch,
German and French are covered; anything else falls back to English).

The basemap defaults to CARTO's **dark** style — the same source the Home
Assistant frontend uses, but the dark variant, because the cloud layer is white
and precipitation is pale blue. It is fetched once and cached on disk under
`.storage/owm_startup_basemap/<style>/`. The cache key includes the tile URL, so
changing the basemap fetches the new style rather than reusing the old tiles,
and stale styles are pruned. Tiles older than 30 days are refetched.
For a light dashboard, swap the option to
`https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png` — the temperature
map still reads well, the other two much less so.

**Do not point the basemap at `tile.openstreetmap.org`.** Their tile usage
policy forbids distributing an application that fetches from their servers, and
treats fetching tiles ahead of viewing them as bulk downloading. Use CARTO, a
keyed provider such as MapTiler or Thunderforest, or your own tile server.

Attribution for both the basemap and OpenWeather is burned into the corner of
each image, which is the only reliable way to satisfy ODbL on a dashboard card.

### Animated maps

Each layer also gets an animated WebP of the last 12 hours:
`image.*_temperature_map_last_12_hours` and the cloud equivalent.

**The sequence only builds forwards.** Maps 1.0 tiles have no time parameter and
historical tiles are a Maps 2.0 product, so there is no way to backfill.

A fresh install therefore starts with nothing, then one frame, then a sequence.
The single-frame stage is served as a **still image** rather than as nothing:
upstream only changes every couple of hours, so returning nothing would leave a
broken image on the dashboard for that long, which looks like a fault instead of
a sequence still filling. The `animating` attribute says which state it is in,
alongside `frames`, `oldest_frame` and `newest_frame`.

Only the very first period — before any frame has been captured — has no image
at all.

Frames are captured on the coordinator's schedule so the sequence keeps filling
while nobody is watching, and this is kept cheap two ways:

- **Probe first.** One tile — the centre one, containing your coordinates — is
  fetched and compared with the last. Only if it differs are the other eight
  pulled. Tiles change every two to three hours while polling runs every thirty
  minutes, so four refreshes in five or more cost a single tile instead of nine.
- **Opportunistic capture.** When the still map is rendered for the frontend all
  nine tiles are already in hand, so that frame is stored for free.

Comparison is on decoded pixels, not file bytes: an upstream re-encode of
unchanged data would otherwise store a duplicate every time.

The probe's limitation is real — a change confined to the edge of the view is
missed until it reaches the centre tile, or until somebody opens the map. That
is the price of one tile instead of nine.

The newest frame is held roughly three times as long as the others, since that
is the current weather and the frame people actually read.

A progress bar runs along the seam between the map and the legend, filling from
the first frame to the last. It tracks **elapsed time**, not frame number, so it
advances unevenly — and that is the point. A large jump means a gap: either the
weather held still for hours, or the integration was not running. Each frame
also carries its own capture time, burned in by the renderer, so the exact
timing of a gap is readable.

### Outages

- Frames outside the 12-hour window are filtered at playback as well as pruned
  on write. A long outage leaves stale files on disk, and they must not play
  simply because nothing has been written since.
- The probe hash only advances once a frame has actually been stored. If the
  probe succeeds but the grid fetch then fails, the next refresh retries rather
  than treating the missed frame as captured.
- The probe and frame hashes are persisted, so a restart does not look like a
  change and store a duplicate of the frame already on disk.
- A frame file that will not open is dropped and deleted rather than failing
  the whole animation.
- A failed write, a full disk or an unreachable API costs a frame and nothing
  else: capture runs unawaited and swallows its errors deliberately.

Removing the integration deletes its captured frames. The basemap cache is
shared between entries rather than keyed by entry, so it is deleted only when
the last entry goes.

WebP rather than GIF: the stretched temperature ramp over a basemap needs more
than 256 colours, and every current browser plays animated WebP in a plain
`img` tag. Frames live in `.storage/owm_startup_frames/` and anything older than
12 hours is pruned on write.

### Wind arrow

The cloud map draws the current wind at your coordinates: an arrow pointing the
way the wind is **going**, with the speed beside it. OpenWeather reports `deg`
as the direction the wind comes *from*, so a southwesterly draws an arrow
pointing northeast — the convention used on weather maps rather than on a wind
vane.

This is one vector for one point, not a wind field. A field of arrows across the
map is a Weather Maps 2.0 feature (the `WND`/`WNDUV` layers with `arrow_step`),
which needs a Developer subscription; the Maps 1.0 `wind_new` layer available on
Startup encodes speed as colour only, with no direction to recover. The arrow
costs no extra API calls — the vector is already in the current-weather payload
the coordinator fetches.

### Mismatched tiles

OpenWeather sometimes serves a grid assembled from more than one model run,
leaving a straight brightness step along a tile boundary. It shows up on both
layers at once, at the same fetch time, and clears on the next update. Nothing
in the rendering path can cause it — the crop, stretch and compositing all work
on the assembled canvas, so a bug there would smear across seams rather than
align to them. The contrast stretch makes it more obvious, because a small
offset in the source gets spread across the full ramp.

A failed tile is not a possible cause: the nine fetches are gathered without
`return_exceptions`, so one failure aborts the whole render and the image proxy
returns an error. There is no per-tile cache for the data layers and no
fallback to an earlier tile — a mixed-vintage map would look plausible and be
wrong, which is worse than no map. The same applies to a tile that arrives
truncated.

Each render checks its tile seams: a step much larger than the gradient beside
it means the tiles disagree rather than the weather does. When that happens the
map gets a red banner reading *tiles from different updates*, and a warning is
logged. The map is still drawn — it is mostly right — but the banner is there so
a seam is not read as a weather front.

With debug logging on, each tile request also logs its `Last-Modified`, `Age`
and `Date` headers, which is the evidence to attach if you want to report it to
OpenWeather.

### Blank-looking maps

The cloud layer runs from transparent at 0% to opaque white at 100%, so light
cloud is faint by design. To tell an empty layer from an invisible one, enable
debug logging: each render reports both the fraction of visibly painted pixels
and the fraction carrying colour below the visible threshold.



```yaml
logger:
  logs:
    custom_components.owm_startup.image: debug
```

There is deliberately no precipitation map. The Weather Maps 1.0 precipitation
layer is a coarse model field rather than radar, it paints nothing below
1 mm/h, and it disagreed with ground observation often enough not to be worth
showing. OpenWeather's radar is a Maps 2.0 endpoint on the Professional plan.
For nowcasting in the Netherlands, use a Buienradar or Buienalarm integration
instead.

## Options

- Language
- Basemap tile URL (blank disables the basemap)
- Basemap attribution

Forecast length, air quality windows and the update interval are fixed at the
values the Startup plan supports: a 16-day daily forecast, a 120-hour 3-hourly
forecast, air quality peaks for today and tomorrow, and half-hourly updates.
There is no useful reason to run this integration with less than the plan gives you, so
these are not exposed as settings.

## Notes and limitations

- **Attribution is mandatory.** The ODbL licence requires a visible
  "Weather data © OpenWeather" on the screen where the data appears. The
  entities carry it as an `attribution` attribute, but that is not visible on
  most dashboard cards — add it to your Lovelace view yourself.
- **No sub-3-hour forecast.** The Startup plan has no minutely nowcast, no true
  hourly forecast, and no government weather alerts. Those live behind One Call,
  which is a separate pay-per-call subscription.
- **CO has no device class.** OpenWeatherMap reports CO in µg/m³ while the
  Home Assistant `carbon_monoxide` device class expects ppm, so the sensor is
  left without a device class rather than mislabelling the unit.
- **Recorder.** The AQI forecast sensor's `forecast` attribute holds one entry
  per hour of the horizon. Consider excluding it:

  ```yaml
  recorder:
    exclude:
      entity_globs:
        - sensor.*_air_quality_index_forecast_*
  ```

- **API key handling.** The key lives in the config entry and never reaches the
  log at any level. Three mechanisms: error messages are built from the
  endpoint path and HTTP status only; aiohttp exceptions are never chained,
  since `ClientResponseError` carries the request URL with the key in it; and a
  log filter scrubs any remaining occurrence from every record this package
  emits, including the update coordinator's. There are tests for all three.
- **Diagnostics.** Download from the device page or the integration's ⋮ menu.
  The key, coordinates, place names and station ids are redacted; only the
  first entry of each forecast series is included.

## Documentation

See the [wiki](https://github.com/lancer73/owm_startup/wiki) for plan
comparisons, ODbL obligations, and related projects — including
[Weather Network Uploader](https://github.com/lancer73/ha-weather-uploader),
which uploads your own station data to OpenWeather and other networks.

## Security

See [SECURITY.md](SECURITY.md) for how to report a vulnerability privately.

## Licence

MIT — see [LICENSE](LICENSE). This covers the integration code. Weather data © OpenWeather, provided under ODbL.
