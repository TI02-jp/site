"""Simple background job executor for fire-and-forget tasks."""

from __future__ import annotations

import atexit
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from flask import current_app

_DEFAULT_MAX_WORKERS = 4
_executor: ThreadPoolExecutor | None = None


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is not None:
        return _executor

    app = current_app._get_current_object()
    max_workers = int(app.config.get("BACKGROUND_MAX_WORKERS", _DEFAULT_MAX_WORKERS))
    _executor = ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="portal-bg",
    )

    def _shutdown_executor() -> None:
        try:
            _executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            # Suppress shutdown errors during interpreter finalization
            pass

    atexit.register(_shutdown_executor)
    return _executor


def submit_background_job(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> bool:
    """Schedule ``func`` to run in a thread with the current application context.

    Returns ``True`` if the job was successfully queued. When queuing fails,
    it falls back to running the job synchronously and returns ``False``.
    """
    app = current_app._get_current_object()

    def _runner() -> None:
        with app.app_context():
            try:
                func(*args, **kwargs)
            except Exception:
                app.logger.exception("Background job %s failed", getattr(func, "__name__", repr(func)))

    try:
        _get_executor().submit(_runner)
        return True
    except Exception:
        app.logger.exception("Failed to submit background job %s; running synchronously", getattr(func, "__name__", repr(func)))
        _runner()
        return False

