"""Cache service for frequently accessed data.

This module provides caching utilities to reduce database load by caching
frequently accessed, relatively static data like tags, users, and settings.
"""

from typing import List
from app.extensions.cache import cache


@cache.memoize(timeout=300)  # 5 minutes cache
def get_all_tags_cached() -> List:
    """Get all tags with 5-minute cache.

    Returns:
        List of Tag objects ordered by name
    """
    from app.models.tables import Tag
    return Tag.query.order_by(Tag.nome).all()


def invalidate_tag_cache():
    """Invalidate all tag-related caches.

    Call this after creating, updating, or deleting tags.
    """
    cache.delete_memoized(get_all_tags_cached)
