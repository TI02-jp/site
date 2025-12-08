"""Lightweight IO task executor for offloading blocking calls."""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable, Any

_logger = logging.getLogger(__name__)

_max_workers = int(os.getenv("TASK_QUEUE_MAX_WORKERS", "4") or 4)
_executor = ThreadPoolExecutor(max_workers=_max_workers)


def submit_io_task(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
    """Submit a blocking IO task to the shared executor.

    Designed for quick offloading of emails/HTTP calls without delaying the request
    thread. Errors are logged asynchronously to avoid crashing the caller.
    """

    future = _executor.submit(func, *args, **kwargs)

    def _log_outcome(fut: Future) -> None:
        exc = fut.exception()
        if exc:
            _logger.error("Background task %s failed: %s", getattr(func, '__name__', func), exc, exc_info=exc)

    future.add_done_callback(_log_outcome)
    return future
