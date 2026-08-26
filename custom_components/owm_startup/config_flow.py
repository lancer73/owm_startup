"""Config flow for the OpenWeatherMap Startup-plan integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_API_KEY,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_NAME,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    LocationSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .api import OwmApiClient, OwmAuthError, OwmError, OwmRateLimitError
from .const import (
    CONF_BASEMAP_ATTRIBUTION,
    CONF_BASEMAP_URL,
    CONF_CONTRAST_STRETCH,
    CONF_LANGUAGE,
    DEFAULT_BASEMAP_ATTRIBUTION,
    DEFAULT_BASEMAP_URL,
    DEFAULT_CONTRAST_STRETCH,
    DEFAULT_LANGUAGE,
    DEFAULT_NAME,
    DOMAIN,
    FORECAST_DAYS,
    LANGUAGES,
)

CONF_LOCATION_KEY = "location"


class OwmStartupConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the user and reauth flows."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            location = user_input[CONF_LOCATION_KEY]
            latitude = location[CONF_LATITUDE]
            longitude = location[CONF_LONGITUDE]

            await self.async_set_unique_id(f"{latitude}-{longitude}")
            self._abort_if_unique_id_configured()

            errors = await _async_validate(
                self.hass,
                user_input[CONF_API_KEY],
                latitude,
                longitude,
                user_input[CONF_LANGUAGE],
            )
            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={
                        CONF_API_KEY: user_input[CONF_API_KEY],
                        CONF_LATITUDE: latitude,
                        CONF_LONGITUDE: longitude,
                        CONF_LANGUAGE: user_input[CONF_LANGUAGE],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                    vol.Required(CONF_API_KEY): str,
                    vol.Required(
                        CONF_LOCATION_KEY,
                        default={
                            CONF_LATITUDE: self.hass.config.latitude,
                            CONF_LONGITUDE: self.hass.config.longitude,
                        },
                    ): LocationSelector(),
                    vol.Required(
                        CONF_LANGUAGE, default=DEFAULT_LANGUAGE
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=LANGUAGES, mode=SelectSelectorMode.DROPDOWN
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after the key was rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a replacement API key."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            errors = await _async_validate(
                self.hass,
                user_input[CONF_API_KEY],
                entry.data[CONF_LATITUDE],
                entry.data[CONF_LONGITUDE],
                entry.data.get(CONF_LANGUAGE, DEFAULT_LANGUAGE),
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_KEY: user_input[CONF_API_KEY]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return OwmStartupOptionsFlow(config_entry)


async def _async_validate(
    hass, api_key: str, latitude: float, longitude: float, language: str
) -> dict[str, str]:
    """Check the key against the endpoints this integration needs."""
    client = OwmApiClient(
        session=async_get_clientsession(hass),
        api_key=api_key,
        latitude=latitude,
        longitude=longitude,
        language=language,
    )
    try:
        await client.async_validate(FORECAST_DAYS)
    except OwmAuthError:
        return {"base": "invalid_auth"}
    except OwmRateLimitError:
        return {"base": "rate_limited"}
    except OwmError:
        return {"base": "cannot_connect"}
    return {}


class OwmStartupOptionsFlow(OptionsFlow):
    """Handle the options flow.

    Language, plus the basemap used behind the weather maps. Forecast length,
    air quality windows, map zoom and the poll interval are fixed: each is
    already at the value the Startup plan makes sensible.
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Store the entry being configured."""
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_LANGUAGE: user_input[CONF_LANGUAGE],
                    CONF_BASEMAP_URL: user_input[CONF_BASEMAP_URL].strip(),
                    CONF_BASEMAP_ATTRIBUTION: user_input[
                        CONF_BASEMAP_ATTRIBUTION
                    ].strip(),
                    CONF_CONTRAST_STRETCH: user_input[CONF_CONTRAST_STRETCH],
                }
            )

        options = self._entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_LANGUAGE,
                        default=options.get(
                            CONF_LANGUAGE,
                            self._entry.data.get(CONF_LANGUAGE, DEFAULT_LANGUAGE),
                        ),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=LANGUAGES, mode=SelectSelectorMode.DROPDOWN
                        )
                    ),
                    vol.Optional(
                        CONF_BASEMAP_URL,
                        default=options.get(CONF_BASEMAP_URL, DEFAULT_BASEMAP_URL),
                    ): TextSelector(),
                    vol.Optional(
                        CONF_BASEMAP_ATTRIBUTION,
                        default=options.get(
                            CONF_BASEMAP_ATTRIBUTION, DEFAULT_BASEMAP_ATTRIBUTION
                        ),
                    ): TextSelector(),
                    vol.Required(
                        CONF_CONTRAST_STRETCH,
                        default=options.get(
                            CONF_CONTRAST_STRETCH, DEFAULT_CONTRAST_STRETCH
                        ),
                    ): BooleanSelector(),
                }
            ),
        )
