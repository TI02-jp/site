"""Structured logging configuration for production monitoring."""

import json
import logging
import os
import tempfile
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from time import perf_counter
from typing import Optional

from sqlalchemy import event


class MessageContainsFilter(logging.Filter):
    """Allow records that contain the configured substring."""

    def __init__(self, substring: str):
        super().__init__()
        self.substring = substring

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pylint: disable=broad-except
            return False
        return self.substring in message


class JsonFormatter(logging.Formatter):
    """JSON formatter for cleaner machine-parsable logs."""

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        payload = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
            "message": record.getMessage(),
        }
        if hasattr(record, "request_id"):
            payload["request_id"] = getattr(record, "request_id")
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class SafeTimedRotatingFileHandler(TimedRotatingFileHandler):
    """TimedRotatingFileHandler that tolerates Windows file-lock rollover failures.

    On Windows, log files can be held open by another process (commonly Flask/Werkzeug
    reloader), which makes os.rename() fail with WinError 32. In that case we skip
    rollover for this interval and reopen the base file so logging continues without
    spamming tracebacks.
    """

    def doRollover(self) -> None:  # type: ignore[override]
        try:
            super().doRollover()
        except PermissionError as exc:
            if getattr(exc, "winerror", None) != 32:
                raise

            try:
                self.stream = self._open()
            except Exception:  # pylint: disable=broad-except
                self.stream = None

            self.rolloverAt = int(time.time()) + self.interval


def _clear_logger_handlers(logger_name: str) -> logging.Logger:
    logger = logging.getLogger(logger_name)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # pylint: disable=broad-except
            pass
    return logger


def _resolve_log_dir(app) -> str:
    """Resolve a writable log directory, with fallback when the primary is unavailable."""
    # Prefer explicit environment variable
    log_dir = os.getenv("APP_LOG_DIR")
    if not log_dir:
        root_dir = os.path.abspath(os.path.join(app.root_path, ".."))
        log_dir = os.path.join(root_dir, "logs")

    try:
        os.makedirs(log_dir, exist_ok=True)
        test_path = os.path.join(log_dir, ".write-test")
        with open(test_path, "w", encoding="utf-8") as test_file:
            test_file.write("ok")
        os.remove(test_path)
        return log_dir
    except OSError:
        # Fall back to a temp location to keep the app running even if the primary path is not writable
        fallback_dir = os.path.join(tempfile.gettempdir(), "app-logs")
        os.makedirs(fallback_dir, exist_ok=True)
        return fallback_dir


def _register_slow_query_listener(engine, slow_query_logger: logging.Logger, threshold_ms: float) -> None:
    """Attach SQLAlchemy event listeners to emit slow queries to the dedicated logger."""

    @event.listens_for(engine, "before_cursor_execute")
    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        context._query_start_time = perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        start = getattr(context, "_query_start_time", None)
        if start is None:
            return
        duration_ms = (perf_counter() - start) * 1000
        if duration_ms < threshold_ms:
            return
        # Provide values expected by formatter
        slow_query_logger.warning(
            statement.replace("\n", " "),
            extra={
                "duration": duration_ms / 1000,  # formatter expects seconds
                "statement": statement,
            },
        )


def setup_logging(app, engine: Optional[object] = None):
    """Configure structured logging with rotation for production use.

    Creates logs in the 'logs' directory with:
    - app.log: General application logs (rotated daily, keeps 60 days)
    - error.log: Error-level logs only (rotated daily, keeps 90 days)
    - warnings.log: Warning-level logs only (rotated daily, keeps 90 days)
    - slow_requests.log: Extracted slow requests (rotated daily, keeps 60 days)
    - slow_queries.log: Database queries >1s (rotated weekly)
    - user_actions.log: User actions audit trail (rotated daily, keeps 180 days)
    - user_actions.jsonl: User actions in JSON format (rotated daily, keeps 180 days)
    """
    # Create logs directory if it doesn't exist
    log_dir = _resolve_log_dir(app)

    # Clear default handlers to avoid duplicates
    app.logger.handlers.clear()

    # Set base logging level
    app.logger.setLevel(logging.INFO)

    # Formatters
    text_formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s in %(module)s (%(funcName)s:%(lineno)d): %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    json_formatter = JsonFormatter()

    # 1. General application log (rotated daily, keeps 60 days)
    app_log_path = os.path.join(log_dir, 'app.log')
    app_handler = SafeTimedRotatingFileHandler(
        app_log_path,
        when='midnight',
        interval=1,
        backupCount=60,
        encoding='utf-8',
        delay=True,
    )
    app_handler.setLevel(logging.INFO)
    app_handler.setFormatter(text_formatter)
    app.logger.addHandler(app_handler)

    # 2. Structured JSON log for ingest into observability tools
    json_log_path = os.path.join(log_dir, 'app.jsonl')
    json_handler = SafeTimedRotatingFileHandler(
        json_log_path,
        when='midnight',
        interval=1,
        backupCount=60,
        encoding='utf-8',
        delay=True,
    )
    json_handler.setLevel(logging.INFO)
    json_handler.setFormatter(json_formatter)
    app.logger.addHandler(json_handler)

    # 3. Error log (rotated daily, keeps 90 days for compliance)
    error_log_path = os.path.join(log_dir, 'error.log')
    error_handler = SafeTimedRotatingFileHandler(
        error_log_path,
        when='midnight',
        interval=1,
        backupCount=90,
        encoding='utf-8',
        delay=True,
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(text_formatter)
    app.logger.addHandler(error_handler)

    # 4. Warning log (rotated daily, keeps 90 days)
    warnings_log_path = os.path.join(log_dir, 'warnings.log')
    warnings_handler = SafeTimedRotatingFileHandler(
        warnings_log_path,
        when='midnight',
        interval=1,
        backupCount=90,
        encoding='utf-8',
        delay=True,
    )
    warnings_handler.setLevel(logging.WARNING)
    # Filter to only capture WARNING level (not ERROR or CRITICAL)
    warnings_handler.addFilter(lambda record: record.levelno == logging.WARNING)
    warnings_handler.setFormatter(text_formatter)
    app.logger.addHandler(warnings_handler)

    # 5. Slow request log (daily rotation)
    slow_log_path = os.path.join(log_dir, 'slow_requests.log')
    slow_handler = SafeTimedRotatingFileHandler(
        slow_log_path,
        when='midnight',
        interval=1,
        backupCount=60,
        encoding='utf-8',
        delay=True,
    )
    slow_handler.setLevel(logging.WARNING)
    slow_handler.setFormatter(text_formatter)
    slow_handler.addFilter(MessageContainsFilter("SLOW REQUEST"))
    app.logger.addHandler(slow_handler)

    # 5. Console handler for development (only if DEBUG is enabled)
    if app.debug:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(text_formatter)
        app.logger.addHandler(console_handler)

    # 6. SQLAlchemy slow query logging (queries > 1 second)
    slow_query_log_path = os.path.join(log_dir, 'slow_queries.log')
    slow_query_handler = SafeTimedRotatingFileHandler(
        slow_query_log_path,
        when='W0',  # Rotate weekly on Monday
        interval=1,
        backupCount=12,  # Keep 3 months
        encoding='utf-8',
        delay=True,
    )
    slow_query_formatter = logging.Formatter(
        '[%(asctime)s] SLOW QUERY (%(duration).3fs): %(statement)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    slow_query_handler.setFormatter(slow_query_formatter)

    # Create slow query logger
    slow_query_logger = _clear_logger_handlers('sqlalchemy.slow_queries')
    slow_query_logger.setLevel(logging.WARNING)
    slow_query_logger.addHandler(slow_query_handler)
    slow_query_logger.propagate = False
    slow_query_threshold_ms = float(
        app.config.get("SLOW_QUERY_THRESHOLD_MS", os.getenv("SLOW_QUERY_THRESHOLD_MS", "1000"))
    )
    if engine is not None:
        _register_slow_query_listener(engine, slow_query_logger, slow_query_threshold_ms)

    # 7. User actions log - Text format (rotated daily, keeps 180 days for compliance)
    user_actions_log_path = os.path.join(log_dir, 'user_actions.log')
    user_actions_handler = SafeTimedRotatingFileHandler(
        user_actions_log_path,
        when='midnight',
        interval=1,
        backupCount=180,  # 6 months retention for audit compliance
        encoding='utf-8',
        delay=True,
    )
    user_actions_handler.setLevel(logging.INFO)
    user_actions_handler.setFormatter(text_formatter)

    # 8. User actions log - JSON format (rotated daily, keeps 180 days)
    user_actions_json_path = os.path.join(log_dir, 'user_actions.jsonl')
    user_actions_json_handler = SafeTimedRotatingFileHandler(
        user_actions_json_path,
        when='midnight',
        interval=1,
        backupCount=180,
        encoding='utf-8',
        delay=True,
    )
    user_actions_json_handler.setLevel(logging.INFO)
    user_actions_json_handler.setFormatter(json_formatter)

    # Create separate user actions logger
    user_actions_logger = _clear_logger_handlers('user_actions')
    user_actions_logger.setLevel(logging.INFO)
    user_actions_logger.addHandler(user_actions_handler)
    user_actions_logger.addHandler(user_actions_json_handler)
    user_actions_logger.propagate = False  # Don't propagate to root logger

    # Log initial startup message
    banner = "=" * 80
    app.logger.info(banner)
    app.logger.info("Application started", extra={"request_id": "startup"})
    app.logger.info(f"Environment: {'Development' if app.debug else 'Production'}")
    app.logger.info(f"Logging configured - logs directory: {log_dir}")
    app.logger.info(banner)

    return app.logger


def log_request_info(request, response, duration_ms, request_id=None):
    """Log request information for monitoring.

    Args:
        request: Flask request object
        response: Flask response object
        duration_ms: Request duration in milliseconds
        request_id: Optional correlation identifier
    """
    from flask import current_app

    prefix = f"[req_id={request_id}]" if request_id else "[req_id=na]"

    if duration_ms > 2000:
        current_app.logger.warning(
            "%s SLOW REQUEST (%s ms): %s %s from %s -> %s",
            prefix,
            f"{duration_ms:.0f}",
            request.method,
            request.path,
            request.remote_addr,
            response.status_code,
            extra={"request_id": request_id},
        )
    elif response.status_code >= 500:
        current_app.logger.error(
            "%s ERROR RESPONSE: %s %s from %s -> %s",
            prefix,
            request.method,
            request.path,
            request.remote_addr,
            response.status_code,
            extra={"request_id": request_id},
        )
    elif response.status_code == 429:
        current_app.logger.warning(
            "%s RATE LIMIT HIT: %s %s from %s",
            prefix,
            request.method,
            request.path,
            request.remote_addr,
            extra={"request_id": request_id},
        )
    elif current_app.debug:
        current_app.logger.debug(
            "%s %s %s -> %s (%s ms)",
            prefix,
            request.method,
            request.path,
            response.status_code,
            f"{duration_ms:.0f}",
            extra={"request_id": request_id},
        )


def log_exception(error, request=None):
    """Log exception with full context and stack trace.

    Args:
        error: Exception object
        request: Flask request object (optional)
    """
    from flask import current_app
    import traceback

    error_msg = f"EXCEPTION: {type(error).__name__}: {str(error)}"

    request_id = getattr(request, "request_id", None) if request else None

    if request:
        error_msg += f"\nRequest: {request.method} {request.path}"
        error_msg += f"\nUser-Agent: {request.headers.get('User-Agent', 'N/A')}"
        error_msg += f"\nIP: {request.remote_addr}"

        if request.form:
            # Don't log sensitive data like passwords
            safe_form = {k: v for k, v in request.form.items()
                        if 'password' not in k.lower() and 'token' not in k.lower()}
            if safe_form:
                error_msg += f"\nForm data: {safe_form}"

    error_msg += f"\n{'='*80}\nStack trace:\n{traceback.format_exc()}"
    error_msg += f"{'='*80}"

    current_app.logger.error(error_msg, extra={"request_id": request_id})
