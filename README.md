# OpenWeatherMap (Startup plan) for Home Assistant

A Home Assistant custom integration that uses only the endpoints included in the
OpenWeatherMap **Startup** subscription of the classic 2.5 collection. It does
**not** use One Call 3.0/4.0, which is a separate pay-per-call product.

## What it provides

| Entity | Source endpoint |
| --- | --- |
| `weather.*` — current conditions, Home Assistant hourly forecast carrying OpenWeather's 3-hour points (5 days), daily forecast up to 16 days | `/data/2.5/weather`, `/data/2.5/forecast`, `/data/2.5/forecast/daily` |
| 9 current air quality sensors | `/data/2.5/air_pollution` |
| 18 air quality forecast sensors (today and tomorrow) | `/data/2.5/air_pollution/forecast` |
| 3 weather map images (temperature, clouds, precipitation) | `tile.openweathermap.org/map` |

Air quality sensors: AQI, PM2.5, PM10, O₃, NO₂, NO, SO₂, CO, NH₃.

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

The Startup plan has no true hourly product. `/data/2.5/forecast` returns
3-hour steps, exposed through Home Assistant's `FORECAST_HOURLY` because that is
the API for sub-daily forecasts — the entity advertises an hourly forecast, but
the points in it are three hours apart. Cards and templates will show them at
that spacing.

The polling interval is **60 minutes**, giving five calls per hour, about 120
per day. OpenWeather's pricing table gives a 2-hour data update frequency for
this plan and their documentation asks that a location not be polled more often
than every 10 minutes; hourly sits comfortably inside both.

Weather map tiles are **not** part of that figure. They are fetched when the
frontend requests an image, and the rendered result is cached until the next
coordinator update. A dashboard that displays all three maps continuously costs
up to 27 tile calls per hour on top (nine tiles per map), and nothing at all if
nobody is looking.

## Requirements

- Home Assistant 2024.11 or newer (uses `entry.runtime_data` and
  `_get_reauth_entry`).
- An OpenWeatherMap API key on a plan that includes `/forecast/daily`. The free
  tier does **not**; Startup and above do. Uploading your own station data and
  then asking at info@openweathermap.org is one route onto the Startup plan —
  see the [wiki](https://github.com/lancer73/owm_startup/wiki/Home.md).

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

Three image entities render Weather Maps 1.0 layers over a basemap: temperature,
clouds and precipitation. A 3x3 tile grid is fetched at zoom 8 and a 512 px
window is cropped from it, centred exactly on your coordinates — about 190 km
across at Dutch latitudes. The images refresh on the same hourly cycle as
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

### Blank-looking maps

The precipitation layer is an **intensity, not an accumulation**: it shows what
is falling at one model timestep. A day that accumulated 7 mm in your rain gauge
will still show an empty map if it is dry at the moment the tile was generated.
Do not expect it to agree with a daily total.

OpenWeather does not state the unit for this layer outright. The palette tops
out at 140, which only makes sense as a rate, and their Maps 2.0 equivalent is
documented as intensity — so it is labelled `mm/h` here. Treat that as a
reasoned reading rather than a documented fact.

Per OpenWeather's published palette, **every precipitation stop below 1 has zero
alpha**. Light rain is drawn in colour but at zero opacity, so it is invisible on
the raw layer even though the data is there. With the contrast stretch on, this
integration counts those pixels and brings them back — a drizzle band that
OpenWeather's own map shows as blank will render here. The legend says
`nothing above 1.0 mm/h in view` rather than `no data` when that is the case.

The cloud layer runs from transparent at 0% to opaque white at 100%, so light
cloud is faint by design. That exception is *not* applied to clouds: a
zero-alpha white pixel there means genuinely clear sky, and counting it would
peg every cloud range to zero.

To tell an empty layer from an invisible one, enable debug logging: each render
reports both the fraction of visibly painted pixels and the fraction carrying
colour below the visible threshold.

```yaml
logger:
  logs:
    custom_components.owm_startup.image: debug
```

If both figures are 0.0% while it is raining outside, the layer genuinely has
nothing for that timestep — which for a model field at this resolution is
possible even when your gauge disagrees. This is not a radar product;
OpenWeather's radar is a Maps 2.0 endpoint on the Professional plan.

## Options

- Language
- Basemap tile URL (blank disables the basemap)
- Basemap attribution

Forecast length, air quality windows and the update interval are fixed at the
values the Startup plan supports: a 16-day daily forecast, a 120-hour 3-hourly
forecast, air quality peaks for today and tomorrow, and hourly updates. There is
no useful reason to run this integration with less than the plan gives you, so
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
