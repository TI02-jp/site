"""Centralized cache extension."""

from __future__ import annotations

import os
from typing import Any, Dict

from flask import current_app
from flask_caching import Cache

cache = Cache()


def init_cache(app) -> None:
    """Initialize the cache backing store based on environment configuration."""
    default_timeout = int(os.getenv("CACHE_DEFAULT_TIMEOUT", "120"))
    redis_url = os.getenv("REDIS_URL")
    config: Dict[str, Any] = {
        "CACHE_DEFAULT_TIMEOUT": default_timeout,
        "CACHE_KEY_PREFIX": os.getenv("CACHE_KEY_PREFIX", "portal"),
    }

    if redis_url:
        config.update(
            {
                "CACHE_TYPE": "RedisCache",
                "CACHE_REDIS_URL": redis_url,
                "CACHE_IGNORE_ERRORS": True,
            }
        )
    else:
        # SimpleCache keeps everything in-process; suitable as a development fallback.
        config.update({"CACHE_TYPE": "SimpleCache"})

    app.config.setdefault("CACHE_TYPE", config["CACHE_TYPE"])
    app.config.setdefault("CACHE_DEFAULT_TIMEOUT", config["CACHE_DEFAULT_TIMEOUT"])
    app.config.setdefault("CACHE_KEY_PREFIX", config["CACHE_KEY_PREFIX"])

    if redis_url:
        app.config.setdefault("CACHE_REDIS_URL", config["CACHE_REDIS_URL"])
        app.config.setdefault("CACHE_IGNORE_ERRORS", config["CACHE_IGNORE_ERRORS"])

    cache.init_app(app)


def get_cache_timeout(config_key: str, default: int) -> int:
    """Helper to read cache TTLs from app config when inside an app context."""
    if current_app:
        return int(current_app.config.get(config_key, default))
    return default
