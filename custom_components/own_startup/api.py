"""Thin async client for the OpenWeatherMap 2.5 collection.

Only endpoints included in the Startup subscription are implemented:
  - /data/2.5/weather                current conditions
  - /data/2.5/forecast               3-hourly forecast, up to 5 days
  - /data/2.5/forecast/daily         daily forecast, up to 16 days
  - /data/2.5/air_pollution          current air quality
  - /data/2.5/air_pollution/forecast hourly air quality forecast
  - tile.openweathermap.org/map     Weather Maps 1.0 raster tiles

One Call (3.0/4.0) is deliberately not used: it is a separate pay-per-call
subscription and is not covered by the Startup plan.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from aiohttp import ClientResponseError, ClientSession

from .redaction import redact, register_secret

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.openweathermap.org/data/2.5"
MAP_URL = "https://tile.openweathermap.org/map"
REQUEST_TIMEOUT = 30


class OwmError(Exception):
    """Base error for this client."""


class OwmAuthError(OwmError):
    """The API key was rejected (invalid, not yet active, or wrong product)."""


class OwmRateLimitError(OwmError):
    """The account exceeded its call allowance."""


class OwmConnectionError(OwmError):
    """The API could not be reached."""


class OwmApiClient:
    """Minimal client for the OpenWeatherMap 2.5 endpoints."""

    def __init__(
        self,
        session: ClientSession,
        api_key: str,
        latitude: float,
        longitude: float,
        language: str = "en",
    ) -> None:
        """Initialise the client."""
        self._session = session
        self._api_key = api_key
        register_secret(api_key)
        self._latitude = latitude
        self._longitude = longitude
        self._language = language

    async def _request(self, path: str, **params: Any) -> dict[str, Any]:
        """Fetch a JSON endpoint."""
        return await self._fetch(
            f"{BASE_URL}/{path}",
            path,
            {
                "lat": self._latitude,
                "lon": self._longitude,
                "appid": self._api_key,
                **params,
            },
            as_json=True,
        )

    async def _fetch(
        self, url: str, label: str, params: dict[str, Any], *, as_json: bool
    ) -> Any:
        """Perform a single GET request and return the decoded body.

        Every error path here is written so the API key cannot escape:

        - messages are built from the endpoint label and status only, never the
          URL, which carries the key as the `appid` query parameter;
        - third-party exceptions are never chained (`from None`), because
          aiohttp's `ClientResponseError` carries `request_info.url` and would
          expose the key in any logged traceback;
        - the original error is still available at debug level, passed through
          `redact()` first.

        Only expected transport and decoding failures are converted. A bug in
        this integration must surface as itself rather than being disguised as
        an API error; the log filter in `redaction` is the backstop that keeps
        the key out of whatever gets logged.
        """
        _LOGGER.debug("Requesting %s", label)
        try:
            async with (
                asyncio.timeout(REQUEST_TIMEOUT),
                self._session.get(url, params=params) as response,
            ):
                if response.status in (401, 403):
                    # Also raised when the key is valid but the plan does not
                    # cover the endpoint (e.g. forecast/daily on the free tier).
                    raise OwmAuthError(
                        f"OpenWeatherMap rejected the request for {label} "
                        f"(HTTP {response.status})"
                    )
                if response.status == 429:
                    raise OwmRateLimitError(
                        f"OpenWeatherMap call allowance exceeded on {label}"
                    )
                response.raise_for_status()
                if not as_json and _LOGGER.isEnabledFor(logging.DEBUG):
                    # Tile vintage. When a grid comes back visibly mismatched
                    # these headers are the evidence for which tiles are stale.
                    _LOGGER.debug(
                        "%s: last-modified=%s age=%s date=%s",
                        label,
                        response.headers.get("Last-Modified"),
                        response.headers.get("Age"),
                        response.headers.get("Date"),
                    )
                return await (response.json() if as_json else response.read())
        except OwmError:
            raise
        except TimeoutError:
            raise OwmConnectionError(f"Timeout calling {label}") from None
        except ClientResponseError as err:
            self._log_cause(label, err)
            raise OwmError(f"HTTP {err.status} calling {label}") from None
        except aiohttp.ClientError as err:
            self._log_cause(label, err)
            raise OwmConnectionError(f"Error calling {label}") from None
        except ValueError as err:
            # Includes JSONDecodeError: a 200 whose body is not what we expect.
            self._log_cause(label, err)
            raise OwmError(f"Malformed response from {label}") from None

    @staticmethod
    def _log_cause(label: str, err: BaseException) -> None:
        """Log the underlying error at debug level, with secrets removed."""
        _LOGGER.debug(
            "Request to %s failed: %s", label, redact(f"{type(err).__name__}: {err}")
        )

    async def async_get_map_tile(self, layer: str, z: int, x: int, y: int) -> bytes:
        """Return one weather map tile as PNG bytes."""
        return await self._fetch(
            f"{MAP_URL}/{layer}/{z}/{x}/{y}.png",
            f"map/{layer}/{z}/{x}/{y}",
            {"appid": self._api_key},
            as_json=False,
        )

    async def async_get_current(self) -> dict[str, Any]:
        """Return current conditions."""
        return await self._request("weather", units="metric", lang=self._language)

    async def async_get_daily_forecast(self, days: int) -> dict[str, Any]:
        """Return the daily forecast for up to 16 days."""
        return await self._request(
            "forecast/daily", cnt=days, units="metric", lang=self._language
        )

    async def async_get_hourly_forecast(self, steps: int) -> dict[str, Any]:
        """Return the 3-hourly forecast; `steps` is the number of timestamps."""
        return await self._request(
            "forecast", cnt=steps, units="metric", lang=self._language
        )

    async def async_get_air_pollution(self) -> dict[str, Any]:
        """Return current air quality."""
        return await self._request("air_pollution")

    async def async_get_air_pollution_forecast(self) -> dict[str, Any]:
        """Return the hourly air quality forecast."""
        return await self._request("air_pollution/forecast")

    async def async_validate(self, days: int) -> None:
        """Verify that the key works and that the plan covers forecast/daily."""
        await self.async_get_current()
        await self.async_get_daily_forecast(days)
