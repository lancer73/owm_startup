"""Redaction helpers.

Two layers protect the API key:

1. The client never puts the key into an exception message, and never chains a
   third-party exception that might carry the full request URL (aiohttp's
   `ClientResponseError` holds `request_info.url`, query string included).
2. A log filter scrubs any remaining occurrence from this package's log
   records. Home Assistant's `DataUpdateCoordinator` logs through the logger it
   is handed, so coordinator errors pass through the filter too.

Note on layer 2: a filter attached to a logger only runs for records created by
that logger — unlike handlers, filters are not inherited by child loggers. The
filter is therefore attached to every logger in this package, not just the
package root.
"""

from __future__ import annotations

import logging
import re

REDACTED = "**REDACTED**"

# Credential-looking query parameters, scrubbed by pattern rather than by
# registering each value. Home Assistant's map tiles proxy rotates its access
# token every 30 minutes, so registering them would grow without bound.
_QUERY_SECRET = re.compile(r"(?i)\b(token|api[_-]?key|key|access_token)=[^&\s\"\']+")


class SecretFilter(logging.Filter):
    """Replace known secrets in log records with a placeholder."""

    def __init__(self) -> None:
        """Initialise with an empty secret set."""
        super().__init__()
        self.secrets: set[str] = set()

    def filter(self, record: logging.LogRecord) -> bool:
        """Scrub the record in place; never drop it."""
        if not self.secrets:
            return True
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - logging must never raise
            return True
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


SECRET_FILTER = SecretFilter()


def redact(text: str) -> str:
    """Return `text` with every registered secret replaced."""
    for secret in SECRET_FILTER.secrets:
        text = text.replace(secret, REDACTED)
    return scrub_query_secrets(text)


def scrub_query_secrets(text: str) -> str:
    """Replace credential-looking query parameter values in a string."""
    return _QUERY_SECRET.sub(lambda match: f"{match.group(1)}={REDACTED}", text)


def _package_loggers() -> list[logging.Logger]:
    """Return this package's logger and all of its existing children."""
    root_name = __package__
    loggers = [logging.getLogger(root_name)]
    loggers.extend(
        logger
        for name, logger in logging.Logger.manager.loggerDict.items()
        if name.startswith(f"{root_name}.") and isinstance(logger, logging.Logger)
    )
    return loggers


def register_secret(secret: str) -> None:
    """Start scrubbing a secret from this package's log output."""
    if not secret:
        return
    SECRET_FILTER.secrets.add(secret)
    for logger in _package_loggers():
        if SECRET_FILTER not in logger.filters:
            logger.addFilter(SECRET_FILTER)


def unregister_secret(secret: str) -> None:
    """Stop scrubbing a secret."""
    SECRET_FILTER.secrets.discard(secret)
