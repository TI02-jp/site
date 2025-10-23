"""Structured logging configuration for production monitoring."""

import logging
import os
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from datetime import datetime


def setup_logging(app):
    """Configure structured logging with rotation for production use.

    Creates logs in the 'logs' directory with:
    - app.log: General application logs (rotated daily, keeps 30 days)
    - error.log: Error-level logs only (rotated daily, keeps 90 days)
    - slow_queries.log: Database queries >1s (rotated weekly)
    """
    # Create logs directory if it doesn't exist
    log_dir = os.path.join(app.root_path, '..', 'logs')
    os.makedirs(log_dir, exist_ok=True)

    # Clear default handlers to avoid duplicates
    app.logger.handlers.clear()

    # Set base logging level
    app.logger.setLevel(logging.INFO)

    # Format for structured logging
    formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s in %(module)s (%(funcName)s:%(lineno)d): %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

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
    app_handler.setFormatter(formatter)
    app.logger.addHandler(app_handler)

    # 2. Error log (rotated daily, keeps 90 days for compliance)
    error_log_path = os.path.join(log_dir, 'error.log')
    error_handler = TimedRotatingFileHandler(
        error_log_path,
        when='midnight',
        interval=1,
        backupCount=90,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    app.logger.addHandler(error_handler)

    # 3. Console handler for development (only if DEBUG is enabled)
    if app.debug:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(formatter)
        app.logger.addHandler(console_handler)

    # 4. SQLAlchemy slow query logging (queries > 1 second)
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
    app.logger.info("="*80)
    app.logger.info(f"Application started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    app.logger.info(f"Environment: {'Development' if app.debug else 'Production'}")
    app.logger.info(f"Logging configured - logs directory: {log_dir}")
    app.logger.info("="*80)

    return app.logger


def log_request_info(request, response, duration_ms):
    """Log request information for monitoring.

    Args:
        request: Flask request object
        response: Flask response object
        duration_ms: Request duration in milliseconds
    """
    from flask import current_app

    # Log slow requests (>2 seconds)
    if duration_ms > 2000:
        current_app.logger.warning(
            f"SLOW REQUEST ({duration_ms:.0f}ms): {request.method} {request.path} "
            f"from {request.remote_addr} -> {response.status_code}"
        )
    # Log errors
    elif response.status_code >= 500:
        current_app.logger.error(
            f"ERROR RESPONSE: {request.method} {request.path} "
            f"from {request.remote_addr} -> {response.status_code}"
        )
    # Log rate limit hits
    elif response.status_code == 429:
        current_app.logger.warning(
            f"RATE LIMIT HIT: {request.method} {request.path} "
            f"from {request.remote_addr}"
        )
    # Normal requests (only in debug mode)
    elif current_app.debug:
        current_app.logger.debug(
            f"{request.method} {request.path} -> {response.status_code} ({duration_ms:.0f}ms)"
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

    current_app.logger.error(error_msg)
