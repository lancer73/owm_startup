# OpenWeatherMap (Startup plan) for Home Assistant

A Home Assistant integration for the OpenWeatherMap **Startup** subscription.

It uses only the classic 2.5 endpoints that subscription includes, and
deliberately does **not** use One Call 3.0/4.0, which is a separate
pay-per-call product. The point is to get everything the Startup plan already
pays for — a 16-day forecast, air quality with forecasts, and weather maps —
without a second bill.

## What you get

**Weather** — one `weather.*` entity with current conditions, a daily forecast
of up to 16 days, and a sub-daily forecast.

**Air quality** — 9 numeric sensors for the current reading (AQI, PM2.5, PM10,
O₃, NO₂, NO, SO₂, CO, NH₃), plus 27 sensors giving OpenWeather's qualitative
band — Good, Fair, Moderate, Poor, Very poor — for now, today and tomorrow.

**Weather maps** — temperature and cloud cover, as images centred on your
location, plus an animation of the last 12 hours of each.

Everything is fetched server-side. Your API key never reaches the browser.

### Air quality bands

Every pollutant OpenWeather publishes a scale for — the index plus PM2.5,
PM10, O₃, NO₂, SO₂ and CO — gets an **enum** sensor carrying the band, using
OpenWeather's own boundaries from their Air Pollution documentation.

Sensor states are the untranslated keys (`good`, `fair`, `moderate`, `poor`,
`very_poor`), so templates and automations keep working when the frontend
language changes, while the UI shows the translated label. The number behind
each band is kept as an attribute: `index` for the index, `value` for a
pollutant.

**Forecasts are bands only.** A microgram figure two days out reads as a
precision the model does not have, while "Moderate tomorrow" is something you
can act on. The peak concentration behind each forecast band is still there as
an attribute, along with `peak_at` and the window bounds. Current readings stay
numeric so they can be graphed and kept in long-term statistics.

Forecast windows are **local calendar days**. Today's runs from now to
midnight, so it shortens through the day and reads `unknown` once the day is
over; tomorrow's covers the whole day.

#### NH₃ and NO are scored differently

OpenWeather lists these two as parameters that do not affect the index and
publishes no scale for them, and there is no ambient health limit for either.
The only published ammonia figures are occupational limits for an 8-hour
shift, around 17,000 µg/m³ — roughly 87× the maximum this API can report. A
health band built on those would read "Good" at every possible value.

Instead they are scored **against Dutch ambient background**, with a
deliberately different vocabulary — Low, Typical, Elevated, High — so a
dashboard cannot mistake them for the index. Their entities are named
`... vs background`.

| µg/m³ | Low | Typical | Elevated | High |
| --- | --- | --- | --- | --- |
| NH₃ | < 2 | 2–8 | 8–15 | ≥ 15 |
| NO | < 2 | 2–10 | 10–25 | ≥ 25 |

NH₃ comes from RIVM/CLO measurements: the national mean across 35 sites was
5.4 µg/m³ in 2024, 4.8 in 2023 and 6.7 in 2022, with 1–2 at the coast rising
to about 15 in intensive livestock areas. NO is derived rather than published:
RIVM reports NOx and NO₂ separately, and the difference gives roughly 2 µg/m³
regional, 5 urban and 15 traffic-exposed once converted from NO₂-equivalent.
Treat the NO boundaries as the softer of the two.

These are annual means applied to hourly model values. Read them as "how does
this hour compare with a normal year around here", not as a measurement or a
health verdict.

### The hourly forecast is not hourly

The Startup plan has no hourly product: `/data/2.5/forecast` returns 3-hour
steps. Those are exposed through Home Assistant's `FORECAST_HOURLY`, because
that is the API for sub-daily forecasts and dropping the feature would lose
five days of forecast for the sake of a label. So the entity advertises an
hourly forecast whose points are three hours apart, and cards, templates and
`weather.get_forecasts` will all show that spacing.

## Installation

Requires Home Assistant 2024.11 or newer, and an API key on a plan that
includes `/forecast/daily` — the free tier does not; Startup and above do. See
[Getting a Startup plan](#getting-a-startup-plan) at the end.

Copy `custom_components/owm_startup/` into your `config/custom_components/`
directory and restart, then add the integration under
**Settings → Devices & services → Add integration**.

You will be asked for a name, your API key, a location and a language. The key
is validated against `/forecast/daily` specifically, so a free-tier key fails
immediately rather than half-working.

## Configuration

Under **Configure** on the integration:

| Option | Default |
| --- | --- |
| Language | `en` |
| Basemap tile URL | CARTO dark (see below) |
| Basemap attribution | © OpenStreetMap contributors © CARTO |
| Stretch temperature map contrast | on |
| Stretch cloud map contrast | off |

Forecast length, air quality windows and the update interval are fixed. Each
is already at the value the Startup plan supports, and exposing them only
invited worse settings.

### The basemap needs a CARTO API key

Since 26 August 2026 CARTO requires an API key on its raster basemaps.
Requests without one still return tiles, but they carry a repeated **"API KEY
REQUIRED"** watermark — and this integration caches basemap tiles for 30 days
and bakes them into 12 hours of animation frames, so an unkeyed watermark
sticks around.

1. Request a key at <https://carto.com/basemaps/apikey>. It is free within
   their fair use limit, needs no CARTO account, and is emailed back
   immediately.
2. Append it to the basemap URL as a `key` parameter:

   ```
   https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png?key=YOUR_KEY
   ```

Changing the URL changes the cache key, so watermarked tiles you already have
are dropped and refetched. The key stays server-side and is redacted from this
integration's logs.

To skip the sign-up entirely, set the basemap URL to blank: the weather layers
then render over a plain background, readable but with no coastlines or place
names.

**Do not point the basemap at `tile.openstreetmap.org`.** Their usage policy
forbids distributing an application that fetches from their servers, and
treats fetching tiles ahead of viewing them as bulk downloading. CARTO,
MapTiler and Thunderforest all serve keyed raster tiles that are fine here.

### Contrast stretch

OpenWeather's temperature palette covers the whole globe, so a 200 km view of
a 3 °C spread renders as very nearly one colour. With the stretch on, every
painted pixel is matched back to a value and re-mapped across a full ramp.

For temperature the ramp is fitted to **today's forecast range**, widened if
necessary to cover anything in view:

```
low  = min(today's forecast minimum, lowest value in view)
high = max(today's forecast maximum, highest value in view)
```

Fitting each frame to its own contents would make the colours mean something
different from one frame to the next — a morning at 16 °C and an afternoon at
24 °C would both render mid-ramp, so an animation would show no warming at
all. Anchoring to the day means the scale only moves when the forecast does.

Cloud cover is different, and defaults to off: it already uses the full
0–100% of its palette, so stretching mostly amplifies noise, which flickers
between animation frames.

## Using it on a dashboard

### The maps

Two image entities render the weather layers over the basemap, centred on your
coordinates and marked with a ring. Each carries a legend, a fetch timestamp
and the required attribution, and the cloud map also carries a wind arrow.

Two more image entities animate the last 12 hours as animated WebP. They can
go straight into a picture card; the browser plays them.

The animations build **forwards only** — Weather Maps 1.0 tiles have no time
parameter, so there is nothing to backfill. A fresh install shows a single
still frame until a second one is captured. The `frames`, `animating`,
`oldest_frame` and `newest_frame` attributes report progress.

A progress bar along the bottom of each frame tracks **elapsed time**, so a
gap in capture shows as a jump rather than passing unnoticed.

### Charting air quality

The **numeric** index sensor is recorded like any other number, so
apexcharts-card can plot its history; the two forecast band sensors each carry
their window's hourly timeline in a `forecast` attribute. Three series cover
yesterday, today and tomorrow as one line:

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

- The history series must point at `..._air_quality_index`, the number. The
  band sensor `..._air_quality` is an enum and has no numeric history.
- `group_by: max` over an hour matches the hourly resolution of the forecast.
  `max` rather than `mean` because the index is an ordinal 1–5: averaging Good
  and Moderate into "Fair" would state something the scale does not mean.
- How far back the recorded line reaches depends on your recorder retention.
- `extend_to: false` matters. The default pads a series' last value to the end
  of the graph, so every series would run across the ones after it. On
  apexcharts-card before 2.0 the option is `extend_to_end: false`.

### Charting a single pollutant

The other pollutants have no hourly timeline — their band sensors expose the
window's peak and the time it falls, and nothing between. That still charts
usefully: a continuous measured line, then one marked point per forecast
window. Put one per pollutant on a page and the pattern across them reads at a
glance.

```yaml
type: custom:apexcharts-card
experimental:
  color_threshold: true
graph_span: 96h
span:
  start: day
  offset: "-2d"
header:
  show: true
  title: PM2.5
  show_states: true
  colorize_states: true
now:
  show: true
  label: Now
yaxis:
  - min: 0
    decimals: 0
    apex_config:
      title:
        text: µg/m³
apex_config:
  markers:
    # Nothing on the measured line, a dot on each forecast peak.
    size: [0, 7, 7]
    strokeWidth: 0
  annotations:
    # OpenWeather's own PM2.5 band boundaries, so the line can be read
    # against the same scale the band sensors use.
    yaxis:
      - y: 10
        borderColor: "#8bc34a"
        label:
          text: Fair
          style:
            background: "#8bc34a"
      - y: 25
        borderColor: "#ff9800"
        label:
          text: Moderate
          style:
            background: "#ff9800"
      - y: 50
        borderColor: "#f44336"
        label:
          text: Poor
          style:
            background: "#f44336"
      - y: 75
        borderColor: "#9c27b0"
        label:
          text: Very poor
          style:
            background: "#9c27b0"
series:
  # Measured: recorder history of the numeric sensor, up to now.
  - entity: sensor.zoetermeer_pm2_5
    name: Measured
    type: area
    curve: smooth
    stroke_width: 2
    opacity: 0.25
    extend_to: false
    group_by:
      func: max
      duration: 1h
    color_threshold:
      - value: 0
        color: "#4caf50"
      - value: 10
        color: "#8bc34a"
      - value: 25
        color: "#ff9800"
      - value: 50
        color: "#f44336"
      - value: 75
        color: "#9c27b0"
  # Forecast: one point per window, at the hour the peak is expected.
  - entity: sensor.zoetermeer_pm2_5_level_today
    name: Peak today
    type: line
    stroke_width: 0
    extend_to: false
    data_generator: |
      const value = entity.attributes.value;
      const at = entity.attributes.peak_at;
      // Late in the day today's window can be empty, and there is then no
      // peak to plot.
      if (value == null || !at) {
        return [];
      }
      return [[new Date(at).getTime(), value]];
  - entity: sensor.zoetermeer_pm2_5_level_tomorrow
    name: Peak tomorrow
    type: line
    stroke_width: 0
    extend_to: false
    data_generator: |
      const value = entity.attributes.value;
      const at = entity.attributes.peak_at;
      if (value == null || !at) {
        return [];
      }
      return [[new Date(at).getTime(), value]];
```

- The forecast series are `type: line` with `stroke_width: 0`, so a single
  point renders as a dot. Dot sizes come from the card-level `markers.size`
  array, one entry per series in order.
- The annotation lines and the colour thresholds are OpenWeather's PM2.5
  boundaries. Swap both together if you chart a different pollutant; the
  boundaries differ per pollutant and are in the band table above.
- `curve: smooth` rather than the `stepline` used for the index. This is a
  continuous concentration, not an ordinal band, so interpolating is honest.
- Today's peak can be in the past: the window runs from now to midnight, and
  the peak may already have passed.

### Colouring the band sensors

The health band sensors change icon with severity — an empty gauge at Good
through an alert at Very poor. Icon **colour** cannot come from an
integration, so state colouring is a dashboard job. With Mushroom:

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

The states are stable untranslated keys, so the mapping survives a language
change.

## Getting a Startup plan

The 16-day daily forecast is the endpoint that requires a paid plan, which is
why setup validates against it specifically. Everything else this integration
uses is available on the free tier.

Plans and limits: <https://openweathermap.org/full-price>

**One route worth knowing:** if you upload data from your own weather station
to OpenWeather, it is worth asking whether that qualifies you for a Startup
subscription. That is how this integration's maintainer obtained one — upload
consistently for a while, then email **info@openweathermap.org** and ask. It
is one person's experience rather than published policy, and OpenWeather is
under no obligation to grant it, but a request backed by a real contribution
history is what makes the case.

[Weather Network Uploader](https://github.com/lancer73/ha-weather-uploader) can
push your Home Assistant weather sensors to OpenWeather, alongside Weather
Underground, WOW-BE, CWOP, PWSWeather and Windy.

**One Call is not needed.** One Call 3.0/4.0 is a separate pay-per-call
subscription, not a feature of the Startup plan. The feature rows in
OpenWeather's pricing table link to the One Call documentation, which makes it
easy to misread as included.

## Troubleshooting

**A map looks like it is made of mismatched pieces.** OpenWeather sometimes
serves a grid assembled from more than one model run, leaving a straight step
along a tile boundary. The map is labelled with a red banner when this is
detected, is kept out of the animation, and is re-rendered on the next
refresh. It is upstream, not local.

**The cloud map looks nearly empty.** The cloud layer runs from transparent at
0% to opaque white at 100%, so light cloud is faint by design. Enable debug
logging and each render reports what fraction of the layer carries data:

```yaml
logger:
  logs:
    custom_components.owm_startup: debug
```

**The animation is not animating.** It needs two distinct frames. Check the
`frames` and `animating` attributes; a fresh install starts from nothing.

**Diagnostics.** Download from the device page. The API key, coordinates and
place names are redacted.

## Notes and limitations

- **Attribution is mandatory.** The ODbL licence requires visible attribution
  wherever the data appears. The maps burn it in; for other cards you need to
  add it yourself.
- **The maps are model fields, not radar.** OpenWeather's radar is a Maps 2.0
  endpoint on the Professional plan. For nowcasting in the Netherlands, use a
  Buienradar or Buienalarm integration. There is deliberately no precipitation
  map here: that layer paints nothing below 1 mm/h and disagreed with ground
  observation too often to be worth showing.
- **CO has no device class.** OpenWeather reports µg/m³ while Home Assistant's
  `carbon_monoxide` class expects ppm, so the sensor carries the unit without
  the class rather than mislabelling it.
- **CARTO are retiring raster basemaps** in favour of vector tiles, which this
  integration cannot composite. A different raster provider will be needed
  when that lands.
- **Recorder.** The AQI forecast sensors carry an hourly timeline attribute.
  Consider excluding them:

  ```yaml
  recorder:
    exclude:
      entity_globs:
        - sensor.*_air_quality_*
  ```

## Development

```bash
scripts/setup   # virtualenv + test dependencies
scripts/test    # pytest with coverage
scripts/lint    # ruff check + format --check
```

Tests mock the API client, so no key is needed and no requests leave your
machine. See `CLAUDE.md` for the conventions this repository follows, and the
[wiki](https://github.com/lancer73/owm_startup/wiki) for plan comparisons and
ODbL obligations.

## Security

See [SECURITY.md](SECURITY.md) for how to report a vulnerability privately.

## Licence

MIT — see [LICENSE](LICENSE). This covers the integration code. Weather data ©
OpenWeather, provided under ODbL.
