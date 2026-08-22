# owm_startup wiki

Home Assistant integration for the OpenWeatherMap 2.5 collection on a Startup
(or higher) subscription.

- [OpenWeather plans and this integration](OpenWeather-plans) — which endpoints
  come with which plan, why One Call is not used, ODbL obligations, and how
  contributing station data may get you onto the Startup plan.
- [Related projects](Related-projects)

## Entities

- `weather.*` — current conditions, 3-hourly forecast up to 5 days, daily
  forecast up to 16 days
- 9 current air quality sensors
- 9 air quality forecast sensors (peak within a configurable horizon)
