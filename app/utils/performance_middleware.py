"""Request performance instrumentation helpers.

This module provides helpers to measure request lifecycle timings, SQL query
durations, external HTTP calls, template rendering, and arbitrary custom spans.
It is designed to be light-weight and only emit detailed logs when a request
exceeds a configured threshold.
"""

from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from flask import Flask, g, request
from sqlalchemy import event
from sqlalchemy.engine import Engine


class PerformanceTracker:
    """Collects timing metrics for a single request."""

    def __init__(self, threshold_ms: float, request_id: Optional[str] = None) -> None:
        self.threshold_ms = threshold_ms
        self.started_at = time.perf_counter()
        self.completed_at: Optional[float] = None
        self.total_duration_ms: Optional[float] = None
        self.sql_time_ms = 0.0
        self.sql_queries: List[Dict[str, Any]] = []
        self.external_calls: List[Dict[str, Any]] = []
        self.template_renders: List[Dict[str, Any]] = []
        self.custom_spans: List[Dict[str, Any]] = []
        self.commit_durations_ms: List[float] = []
        self.request_id = request_id or uuid.uuid4().hex[:12]
        self._lock = threading.Lock()

    def finish(self) -> None:
        if self.completed_at is None:
            self.completed_at = time.perf_counter()
            self.total_duration_ms = (self.completed_at - self.started_at) * 1000.0

    def record_query(self, statement: str, duration_ms: float, parameters: Any) -> None:
        # Otimização: Apenas normalizar se o request for considerado lento ou estiver em debug
        # para economizar CPU em requests rápidos.
        if duration_ms < 50 and not current_app.debug:
            normalized_statement = statement[:200] + "..." if len(statement) > 200 else statement
        else:
            normalized_statement = " ".join(statement.split())
            if len(normalized_statement) > 200:
                normalized_statement = f"{normalized_statement[:200]}..."
        with self._lock:
            self.sql_time_ms += duration_ms
            self.sql_queries.append(
                {
                    "sql": normalized_statement,
                    "duration_ms": round(duration_ms, 2),
                    "parameters": parameters if isinstance(parameters, dict) else None,
                }
            )

    def record_external(self, name: str, duration_ms: float, status: Optional[int]) -> None:
        with self._lock:
            self.external_calls.append(
                {
                    "name": name,
                    "duration_ms": round(duration_ms, 2),
                    "status": status,
                }
            )

    def record_template(self, template_name: str, duration_ms: float) -> None:
        with self._lock:
            self.template_renders.append(
                {
                    "template": template_name,
                    "duration_ms": round(duration_ms, 2),
                }
            )

    def record_custom_span(self, category: str, name: str, duration_ms: float) -> None:
        with self._lock:
            self.custom_spans.append(
                {
                    "category": category,
                    "name": name,
                    "duration_ms": round(duration_ms, 2),
                }
            )

    def record_commit(self, duration_ms: float) -> None:
        with self._lock:
            self.commit_durations_ms.append(round(duration_ms, 2))

    def to_log_payload(self) -> Dict[str, Any]:
        self.finish()
        payload: Dict[str, Any] = {
            "request_id": self.request_id,
            "path": request.path,
            "method": request.method,
            "status_code": getattr(g, "performance_response_status", None),
            "duration_ms": round(self.total_duration_ms or 0.0, 2),
            "sql_time_ms": round(self.sql_time_ms, 2),
            "sql_count": len(self.sql_queries),
            "external_count": len(self.external_calls),
            "template_count": len(self.template_renders),
            "commit_count": len(self.commit_durations_ms),
        }
        if (self.total_duration_ms or 0.0) >= self.threshold_ms:
            payload["sql_queries"] = self.sql_queries
            payload["external_calls"] = self.external_calls
            payload["template_renders"] = self.template_renders
            payload["custom_spans"] = self.custom_spans
            payload["commit_durations_ms"] = self.commit_durations_ms
        return payload


def _get_tracker() -> Optional[PerformanceTracker]:
    return getattr(g, "performance_tracker", None)


def get_request_tracker() -> Optional[PerformanceTracker]:
    """Expose the tracker for other modules (e.g. background telemetry)."""
    return _get_tracker()


@contextmanager
def track_external_call(name: str, status_getter=None):
    """Measure an external I/O call and record it on the tracker if available."""
    tracker = _get_tracker()
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        status = status_getter() if callable(status_getter) else None
        if tracker:
            tracker.record_external(name, elapsed_ms, status)


@contextmanager
def track_custom_span(category: str, name: str):
    """General purpose span recorder."""
    tracker = _get_tracker()
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if tracker:
            tracker.record_custom_span(category, name, elapsed_ms)


def track_commit_start() -> float:
    """Return a timestamp for commit tracking."""
    return time.perf_counter()


def track_commit_end(started_at: float) -> None:
    tracker = _get_tracker()
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    if tracker:
        tracker.record_commit(elapsed_ms)


_templates_instrumented = False


def _instrument_templates(app: Flask) -> None:
    global _templates_instrumented
    if _templates_instrumented:
        return

    from flask.templating import _render as flask_render  # type: ignore

    def _instrumented_render(template, context, app):
        tracker = _get_tracker()
        start = time.perf_counter()
        try:
            return flask_render(template, context, app)
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            if tracker:
                tracker.record_template(getattr(template, "name", "unknown"), elapsed_ms)

    from flask import templating as flask_templating

    flask_templating._render = _instrumented_render  # type: ignore
    _templates_instrumented = True


def _install_sql_listeners(engine: Engine) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def _before_cursor_execute(_conn, _cursor, _statement, _parameters, context, _executemany):
        context._perf_start_time = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _after_cursor_execute(_conn, _cursor, statement, parameters, context, _executemany):
        start_time = getattr(context, "_perf_start_time", None)
        if start_time is None:
            return
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        tracker = _get_tracker()
        if tracker:
            tracker.record_query(statement, elapsed_ms, parameters)


def register_performance_middleware(app: Flask, db) -> None:
    """Register before/after hooks and instrument SQLAlchemy for telemetry."""

    threshold_ms = float(app.config.get("SLOW_REQUEST_THRESHOLD_MS", 750))

    @app.before_request
    def _perf_before_request():
        tracker = PerformanceTracker(threshold_ms=threshold_ms)
        g.performance_tracker = tracker
        g.request_id = tracker.request_id
        try:
            request.request_id = tracker.request_id  # type: ignore[attr-defined]
        except Exception:  # pylint: disable=broad-except
            pass
        request.environ["request_id"] = tracker.request_id

    @app.after_request
    def _perf_after_request(response):
        tracker = _get_tracker()
        if tracker:
            g.performance_response_status = response.status_code
            tracker.finish()
            payload = tracker.to_log_payload()
            total_ms = tracker.total_duration_ms or 0.0
            logger = app.logger
            response.headers.setdefault("X-Request-ID", tracker.request_id)
            if total_ms >= threshold_ms:
                logger.warning(
                    "SLOW REQUEST [%s]: %s",
                    tracker.request_id,
                    payload,
                    extra={"request_id": tracker.request_id},
                )
            else:
                logger.debug(
                    "REQUEST PERF [%s]: %s",
                    tracker.request_id,
                    payload,
                    extra={"request_id": tracker.request_id},
                )
        return response

    _install_sql_listeners(db.engine)
    _instrument_templates(app)
