# OpenWeather plans and this integration

This integration deliberately uses only the **classic 2.5 collection** endpoints:

| Endpoint | Free | Startup |
| --- | --- | --- |
| `/data/2.5/weather` | yes | yes |
| `/data/2.5/forecast` (3-hourly, 5 days) | yes | yes |
| `/data/2.5/forecast/daily` (16 days) | **no** | yes |
| `/data/2.5/air_pollution` | yes | yes |
| `/data/2.5/air_pollution/forecast` | yes | yes |
| Weather Maps 1.0 tiles | yes | yes |
| Weather Maps 2.0 tiles | no | no (Developer and up) |

The 16-day daily forecast is the endpoint that requires a paid plan, which is
why the config flow validates against it specifically.

**One Call API 3.0/4.0 is not used.** It is a separate pay-per-call product with
its own subscription, not a feature of the Startup plan. The feature rows in the
pricing table link to the One Call documentation, which makes it easy to
misread as being included. Check your account's billing page rather than the
marketing table.

Plans and limits: https://openweathermap.org/full-price

## Getting onto the Startup plan by contributing station data

This is how the maintainer of this integration obtained a Startup plan: upload
data from your own weather station to OpenWeather for a while, then email
**info@openweathermap.org** and ask.

Our [Weather Network Uploader](https://github.com/lancer73/ha-weather-uploader)
integration can push your Home Assistant weather sensors to OpenWeather along
with Weather Underground, WOW-BE, CWOP, PWSWeather and Windy.

Two caveats: this is one person's experience rather than published policy, and
OpenWeather is under no obligation to grant it. Upload consistently first — a
request backed by a real contribution history is the part that makes the case.

## Licensing

All self-service OpenWeather products are provided under the Open Database
License (ODbL). Two consequences:

- Attribution must be **visible where the data is shown**. Attribution buried in
  documentation or a legal page does not satisfy this.
- A derived database that you distribute must be offered under ODbL as well
  (share-alike).

ODbL covers the data. It does not grant any rights to the OpenWeather name or
logo — that is a separate trademark question.
