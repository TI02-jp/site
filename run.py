"""Application entry point for running the Flask app with Waitress."""
import logging
import os

from waitress import serve

from app import app


def _get_int_env(var_name: str, default: int) -> int:
    """Safely parse integer environment variables with defaults."""
    value = os.getenv(var_name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


if __name__ == "__main__":
    waitress_log_level = os.getenv("WAITRESS_LOG_LEVEL", "info").upper()
    os.environ.setdefault("WAITRESS_LOG_LEVEL", waitress_log_level.lower())
    logging.getLogger("waitress").setLevel(waitress_log_level)

    # Production-tuned Waitress settings optimized for maior concorrencia
    threads = _get_int_env("WAITRESS_THREADS", 64)
    connection_limit = _get_int_env("WAITRESS_CONNECTION_LIMIT", 1000)
    backlog = _get_int_env("WAITRESS_BACKLOG", 512)
    channel_timeout = _get_int_env("WAITRESS_CHANNEL_TIMEOUT", 300)
    recv_bytes = _get_int_env("WAITRESS_RECV_BYTES", 16384)
    send_bytes = _get_int_env("WAITRESS_SEND_BYTES", 16384)
    inbuf_overflow = _get_int_env("WAITRESS_INBUF_OVERFLOW", 32 * 1024 * 1024)

    serve(
        app,
        host="0.0.0.0",
        port=5000,
        threads=threads,
        channel_timeout=channel_timeout,
        connection_limit=connection_limit,
        backlog=backlog,
        asyncore_use_poll=True,  # Use poll() instead of select() for speed
        clear_untrusted_proxy_headers=True,
        max_request_body_size=app.config.get("MAX_CONTENT_LENGTH"),  # Align upload cap with Flask config
        inbuf_overflow=inbuf_overflow,
        recv_bytes=recv_bytes,
        send_bytes=send_bytes,
        expose_tracebacks=app.debug,
    )
