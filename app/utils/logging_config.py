"""Structured logging configuration for production monitoring."""

import json
import logging
import os
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler


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


def setup_logging(app):
    """Configure structured logging with rotation for production use.

    Creates logs in the 'logs' directory with:
    - app.log: General application logs (rotated daily, keeps 30 days)
    - error.log: Error-level logs only (rotated daily, keeps 90 days)
    - slow_requests.log: Extracted slow requests (rotated daily, keeps 30 days)
    - slow_queries.log: Database queries >1s (rotated weekly)
    """
    # Create logs directory if it doesn't exist
    log_dir = os.path.join(app.root_path, '..', 'logs')
    os.makedirs(log_dir, exist_ok=True)

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

    # 1. General application log (rotated daily, keeps 30 days)
    app_log_path = os.path.join(log_dir, 'app.log')
    app_handler = TimedRotatingFileHandler(
        app_log_path,
        when='midnight',
        interval=1,
        backupCount=30,
        encoding='utf-8'
    )
    app_handler.setLevel(logging.INFO)
    app_handler.setFormatter(text_formatter)
    app.logger.addHandler(app_handler)

    # 2. Structured JSON log for ingest into observability tools
    json_log_path = os.path.join(log_dir, 'app.jsonl')
    json_handler = TimedRotatingFileHandler(
        json_log_path,
        when='midnight',
        interval=1,
        backupCount=14,
        encoding='utf-8'
    )
    json_handler.setLevel(logging.INFO)
    json_handler.setFormatter(json_formatter)
    app.logger.addHandler(json_handler)

    # 3. Error log (rotated daily, keeps 90 days for compliance)
    error_log_path = os.path.join(log_dir, 'error.log')
    error_handler = TimedRotatingFileHandler(
        error_log_path,
        when='midnight',
        interval=1,
        backupCount=90,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(text_formatter)
    app.logger.addHandler(error_handler)

    # 4. Slow request log (daily rotation)
    slow_log_path = os.path.join(log_dir, 'slow_requests.log')
    slow_handler = TimedRotatingFileHandler(
        slow_log_path,
        when='midnight',
        interval=1,
        backupCount=30,
        encoding='utf-8'
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
    slow_query_handler = TimedRotatingFileHandler(
        slow_query_log_path,
        when='W0',  # Rotate weekly on Monday
        interval=1,
        backupCount=12,  # Keep 3 months
        encoding='utf-8'
    )
    slow_query_formatter = logging.Formatter(
        '[%(asctime)s] SLOW QUERY (%(duration).3fs): %(statement)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    slow_query_handler.setFormatter(slow_query_formatter)

    # Create slow query logger
    slow_query_logger = logging.getLogger('sqlalchemy.slow_queries')
    slow_query_logger.setLevel(logging.WARNING)
    slow_query_logger.addHandler(slow_query_handler)
    slow_query_logger.propagate = False

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
