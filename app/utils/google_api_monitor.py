"""Wrappers for Google API calls with performance logging."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import requests

from app.utils.performance_middleware import track_external_call

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = (3.05, 10.0)  # (connect, read)


def instrumented_request(
    method: str,
    url: str,
    *,
    timeout: Optional[float] = None,
    stream: bool = False,
    **kwargs: Any,
) -> requests.Response:
    """Wrapper around requests.request with logging, timeout, and timing data."""
    request_timeout = timeout or DEFAULT_TIMEOUT
    status_holder = {"code": None}
    with track_external_call(
        f"{method.upper()} {url}",
        status_getter=lambda: status_holder["code"],
    ):
        response = requests.request(
            method,
            url,
            timeout=request_timeout,
            stream=stream,
            **kwargs,
        )
        status_holder["code"] = response.status_code
    logger.debug(
        "External request completed",
        extra={
            "method": method,
            "url": url,
            "status_code": response.status_code,
            "timeout": request_timeout,
        },
    )
    return response


def monitor_google_call(name: str, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Execute a Google API function capturing timings and logging failures."""
    with track_external_call(name):
        try:
            result = func(*args, **kwargs)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Google API call failed: %s (%s)", name, exc)
            raise
    return result


def instrumented_batch_execute(
    batch_request: Any,
    name: str = "google_batch_execute",
) -> Any:
    """Instrument Google batch requests by timing their execution."""
    with track_external_call(name):
        return batch_request.execute()
