"""Application entry point for local development."""
from waitress import serve
from app import app

if __name__ == "__main__":
    # Production-tuned Waitress settings
    serve(
        app,
        host="0.0.0.0",
        port=5000,
        threads=8,  # Run with one thread per CPU core
        channel_timeout=60,  # Wait up to 60s for client activity
        connection_limit=500,  # Allow up to 500 concurrent sockets
        backlog=256,  # Pending connection queue length
        asyncore_use_poll=True,  # Use poll() instead of select() for speed
        max_request_body_size=app.config.get("MAX_CONTENT_LENGTH"),  # Align upload cap with Flask config
        inbuf_overflow=32 * 1024 * 1024,  # Spill to disk only after 32 MB of request body
    )
