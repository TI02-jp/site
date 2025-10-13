"""Simple in-memory cache for Google Calendar API responses."""

from datetime import datetime, timedelta
from typing import Any
import threading


class SimpleCache:
    """Thread-safe in-memory cache with TTL support."""

    def __init__(self, default_ttl: int = 30):
        """Initialize cache with default TTL in seconds."""
        self.cache: dict[str, tuple[Any, datetime]] = {}
        self.default_ttl = default_ttl
        self.lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        """Get value from cache if not expired."""
        with self.lock:
            if key not in self.cache:
                return None

            value, expires_at = self.cache[key]
            if datetime.now() > expires_at:
                del self.cache[key]
                return None

            return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Set value in cache with optional TTL."""
        if ttl is None:
            ttl = self.default_ttl

        expires_at = datetime.now() + timedelta(seconds=ttl)
        with self.lock:
            self.cache[key] = (value, expires_at)

    def delete(self, key: str) -> None:
        """Remove key from cache."""
        with self.lock:
            self.cache.pop(key, None)

    def clear(self) -> None:
        """Clear all cache entries."""
        with self.lock:
            self.cache.clear()

    def cleanup_expired(self) -> int:
        """Remove all expired entries and return count of removed items."""
        now = datetime.now()
        with self.lock:
            expired_keys = [
                key for key, (_, expires_at) in self.cache.items()
                if now > expires_at
            ]
            for key in expired_keys:
                del self.cache[key]
            return len(expired_keys)


# Global cache instance for calendar events
# Cache for 30 seconds by default - balances freshness with performance
calendar_cache = SimpleCache(default_ttl=30)
