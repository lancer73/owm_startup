"""Tests proving the API key cannot reach the logs or diagnostics."""

from __future__ import annotations

import logging

from aiohttp import ClientResponseError, RequestInfo
import pytest
from yarl import URL

from custom_components.owm_startup.api import OwmApiClient, OwmError
from custom_components.owm_startup.redaction import REDACTED, redact, register_secret
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

API_KEY = "0123456789abcdef0123456789abcdef"


async def get_diagnostics(hass: HomeAssistant, hass_client, entry) -> dict:
    """Fetch diagnostics through the HTTP view, as the UI download does."""
    assert await async_setup_component(hass, "diagnostics", {})
    await hass.async_block_till_done()
    client = await hass_client()
    response = await client.get(f"/api/diagnostics/config_entry/{entry.entry_id}")
    assert response.status == 200
    return (await response.json())["data"]


@pytest.fixture
async def client(hass, aioclient_mock) -> OwmApiClient:
    """Return a client bound to the mocked session."""
    session = aioclient_mock.create_session(hass.loop)
    yield OwmApiClient(session, API_KEY, 52.06, 4.49, "en")
    await session.close()


async def test_debug_logging_omits_key(
    client, aioclient_mock, caplog: pytest.LogCaptureFixture
) -> None:
    """A successful request logs nothing containing the key."""
    caplog.set_level(logging.DEBUG)
    aioclient_mock.get(
        "https://api.openweathermap.org/data/2.5/weather", json={"ok": True}
    )
    await client.async_get_current()
    assert "Requesting weather" in caplog.text
    assert API_KEY not in caplog.text


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_failure_logging_omits_key(
    client, aioclient_mock, caplog: pytest.LogCaptureFixture, status
) -> None:
    """Failing requests log nothing containing the key, at any level."""
    caplog.set_level(logging.DEBUG)
    aioclient_mock.get(
        "https://api.openweathermap.org/data/2.5/weather", status=status, json={}
    )
    with pytest.raises(Exception):  # noqa: B017 - type varies by status
        await client.async_get_current()
    assert API_KEY not in caplog.text


async def test_exception_is_not_chained(client, aioclient_mock) -> None:
    """Third-party errors are not chained: their request URL holds the key."""
    aioclient_mock.get(
        "https://api.openweathermap.org/data/2.5/weather", status=500, json={}
    )
    with pytest.raises(OwmError) as err:
        await client.async_get_current()
    assert err.value.__cause__ is None
    assert err.value.__suppress_context__ is True


async def test_filter_scrubs_leaked_key(caplog: pytest.LogCaptureFixture) -> None:
    """Even a careless log call is scrubbed by the filter."""
    caplog.set_level(logging.DEBUG)
    register_secret(API_KEY)
    logging.getLogger("custom_components.owm_startup.api").error(
        "oops appid=%s", API_KEY
    )
    assert API_KEY not in caplog.text
    assert REDACTED in caplog.text


async def test_filter_scrubs_client_response_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A logged aiohttp error carrying the URL is scrubbed."""
    caplog.set_level(logging.DEBUG)
    register_secret(API_KEY)
    url = URL(f"https://api.openweathermap.org/data/2.5/weather?appid={API_KEY}")
    err = ClientResponseError(
        RequestInfo(url, "GET", {}, url), (), status=500, message="boom"
    )
    logging.getLogger("custom_components.owm_startup.coordinator").warning(
        "failed: %s", err
    )
    assert API_KEY not in caplog.text


async def test_redact_helper() -> None:
    """The helper replaces registered secrets anywhere in a string."""
    register_secret(API_KEY)
    assert API_KEY not in redact(f"prefix {API_KEY} suffix")


async def test_diagnostics_redacts_key_and_location(
    hass: HomeAssistant, hass_client, setup_integration
) -> None:
    """Diagnostics must not expose the key, coordinates or place names."""
    result = await get_diagnostics(hass, hass_client, setup_integration)
    serialised = str(result)

    assert setup_integration.data["api_key"] not in serialised
    assert "52.06" not in serialised
    assert "4.49" not in serialised
    assert "Zoetermeer" not in serialised

    assert result["entry"]["data"]["api_key"] == REDACTED
    assert result["entry"]["data"]["latitude"] == REDACTED


async def test_diagnostics_contains_useful_state(
    hass: HomeAssistant, hass_client, setup_integration
) -> None:
    """Diagnostics still carry enough to debug a mapping problem."""
    result = await get_diagnostics(hass, hass_client, setup_integration)
    assert result["coordinator"]["last_update_success"] is True
    assert result["counts"]["daily"] == 16
    assert result["counts"]["hourly"] == 40
    assert result["data"]["daily_first"]["temp"]["max"] == 21.7
