"""Tests for the config and options flows."""

from __future__ import annotations

from unittest.mock import patch

from custom_components.owm_startup.api import OwmAuthError, OwmConnectionError
from custom_components.owm_startup.const import (
    CONF_CONTRAST_STRETCH_CLOUDS,
    CONF_CONTRAST_STRETCH_TEMPERATURE,
    CONF_LANGUAGE,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_KEY, CONF_LATITUDE, CONF_LONGITUDE, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

USER_INPUT = {
    CONF_NAME: "Zoetermeer",
    CONF_API_KEY: "0123456789abcdef0123456789abcdef",
    "location": {CONF_LATITUDE: 52.06, CONF_LONGITUDE: 4.49},
    CONF_LANGUAGE: "en",
}

VALIDATE = "custom_components.owm_startup.api.OwmApiClient.async_validate"


async def test_user_flow(hass: HomeAssistant, mock_api) -> None:
    """A valid key creates an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    with patch(VALIDATE, return_value=None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Zoetermeer"
    assert result["data"][CONF_LATITUDE] == 52.06


async def test_invalid_auth(hass: HomeAssistant) -> None:
    """A rejected key surfaces invalid_auth rather than creating an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(VALIDATE, side_effect=OwmAuthError("401")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_cannot_connect(hass: HomeAssistant) -> None:
    """A transport failure surfaces cannot_connect."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(VALIDATE, side_effect=OwmConnectionError("boom")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_duplicate_location_aborts(
    hass: HomeAssistant, config_entry, mock_api
) -> None:
    """The same coordinates cannot be configured twice."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(VALIDATE, return_value=None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(hass: HomeAssistant, setup_integration) -> None:
    """Language is the only option; it is stored and the entry reloads."""
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    assert result["type"] is FlowResultType.FORM

    assert set(result["data_schema"].schema) == {
        CONF_LANGUAGE,
        CONF_CONTRAST_STRETCH_CLOUDS,
        CONF_CONTRAST_STRETCH_TEMPERATURE,
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_LANGUAGE: "nl",
            CONF_CONTRAST_STRETCH_TEMPERATURE: True,
            CONF_CONTRAST_STRETCH_CLOUDS: False,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_integration.options[CONF_LANGUAGE] == "nl"
