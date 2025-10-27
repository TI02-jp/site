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

    # Production-tuned Waitress settings coordenado com Apache
    # Apache: max=64 conexões, timeout=30s normal / 90s SSE
    host = os.getenv("WAITRESS_HOST", "127.0.0.1")
    port = _get_int_env("WAITRESS_PORT", 5000)

    # Threads reduzidas de 64 -> 32 (alinhado com pool Apache: max=64/2)
    # Evita thread starvation e context switching excessivo
    threads = _get_int_env("WAITRESS_THREADS", 32)

    # Connection limit alinhado com Apache max pool
    connection_limit = _get_int_env("WAITRESS_CONNECTION_LIMIT", 256)

    # Backlog adequado para picos de tráfego
    backlog = _get_int_env("WAITRESS_BACKLOG", 256)

    # channel_timeout coordenado com Apache SSE timeout (90s)
    # Apache fecha conexões SSE após 90s, então 100s dá margem
    channel_timeout = _get_int_env("WAITRESS_CHANNEL_TIMEOUT", 100)

    # Buffers TCP otimizados para throughput
    # 32KB oferece bom balance entre memória e performance
    recv_bytes = _get_int_env("WAITRESS_RECV_BYTES", 32768)
    send_bytes = _get_int_env("WAITRESS_SEND_BYTES", 32768)

    # Buffer overflow para uploads grandes (32MB)
    inbuf_overflow = _get_int_env("WAITRESS_INBUF_OVERFLOW", 32 * 1024 * 1024)

    expose_tracebacks = os.getenv("WAITRESS_EXPOSE_TRACEBACKS", "0") == "1"

    logging.getLogger(__name__).info(
        "Starting Waitress: host=%s port=%s threads=%s channel_timeout=%s",
        host,
        port,
        threads,
        channel_timeout,
    )

    serve(
        app,
        host=host,
        port=port,
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
        expose_tracebacks=app.debug or expose_tracebacks,
    )
