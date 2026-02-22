"""
Shared HTTP retry configuration for all satellite data fetchers.

Retries on transient errors only (5xx, 429, timeouts, connection errors).
Never retries 4xx client errors (bad request, not found, etc.).
"""
from __future__ import annotations

import logging

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

_log = logging.getLogger(__name__)


def _is_transient(exc: Exception) -> bool:
    """Return True for errors that are worth retrying."""
    if isinstance(exc, httpx.HTTPStatusError):
        # Retry server errors (5xx) and rate limiting (429); never retry 4xx
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, (
        httpx.TimeoutException,
        httpx.ConnectError,
        httpx.RemoteProtocolError,
    ))


# Decorator: 3 attempts, 2 s → 4 s → 8 s back-off, logs each retry at WARNING.
# Apply this to any async HTTP function that should survive transient failures.
http_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception(_is_transient),
    before_sleep=before_sleep_log(_log, logging.WARNING),
    reraise=True,
)
