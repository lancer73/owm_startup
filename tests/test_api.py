"""Tests for the API client's error mapping and secret handling."""

from __future__ import annotations

import pytest

from custom_components.owm_startup.api import (
    OwmApiClient,
    OwmAuthError,
    OwmConnectionError,
    OwmError,
    OwmRateLimitError,
)

API_KEY = "0123456789abcdef0123456789abcdef"


@pytest.fixture
async def client(hass, aioclient_mock) -> OwmApiClient:
    """Return a client bound to the mocked session.

    The mocker's own session is used rather than Home Assistant's shared one,
    which would leave a shutdown thread behind and trip the cleanup check.
    """
    session = aioclient_mock.create_session(hass.loop)
    yield OwmApiClient(session, API_KEY, 52.06, 4.49, "en")
    await session.close()


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, OwmAuthError),
        (403, OwmAuthError),
        (429, OwmRateLimitError),
        (500, OwmError),
    ],
)
async def test_status_mapping(client, aioclient_mock, status, expected) -> None:
    """HTTP statuses map onto the client's exception types."""
    aioclient_mock.get(
        "https://api.openweathermap.org/data/2.5/weather", status=status, json={}
    )
    with pytest.raises(expected):
        await client.async_get_current()


async def test_api_key_not_in_error_message(client, aioclient_mock) -> None:
    """Error messages must never leak the key."""
    aioclient_mock.get(
        "https://api.openweathermap.org/data/2.5/weather", status=500, json={}
    )
    with pytest.raises(OwmError) as err:
        await client.async_get_current()
    assert API_KEY not in str(err.value)


async def test_connection_error(client, aioclient_mock) -> None:
    """Transport failures raise OwmConnectionError."""
    aioclient_mock.get(
        "https://api.openweathermap.org/data/2.5/weather", exc=TimeoutError
    )
    with pytest.raises(OwmConnectionError):
        await client.async_get_current()


def test_manifest_version_is_in_the_changelog() -> None:
    """A released version must have a dated changelog entry.

    Guards the release step: bumping the manifest without writing the entry, or
    writing the entry without bumping, both fail here.
    """
    import json
    from pathlib import Path

    root = Path(__file__).parent.parent
    version = json.loads(
        (root / "custom_components" / "owm_startup" / "manifest.json").read_text()
    )["version"]
    changelog = (root / "CHANGELOG.md").read_text()

    assert f"## [{version}] - " in changelog, version
    assert f"[{version}]: https://" in changelog, version


async def test_html_body_reports_as_malformed_not_http_zero(
    client, aioclient_mock
) -> None:
    """A 200 serving HTML must not be reported as "HTTP 0".

    aiohttp raises ContentTypeError from json(), which is a ClientResponseError
    subclass but carries status 0 because it was not raised by
    raise_for_status().
    """
    aioclient_mock.get(
        "https://api.openweathermap.org/data/2.5/weather",
        status=200,
        text="<html>maintenance</html>",
        headers={"Content-Type": "text/html"},
    )
    with pytest.raises(OwmError) as err:
        await client.async_get_current()

    assert "Malformed response" in str(err.value)
    assert "HTTP 0" not in str(err.value)
