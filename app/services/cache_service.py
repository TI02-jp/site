"""Cache service for frequently accessed data.

This module provides caching utilities to reduce database load by caching
frequently accessed, relatively static data like tags, users, and settings.
"""

from typing import List, Optional
from app.extensions.cache import cache
from app import db


@cache.memoize(timeout=300)  # 5 minutes cache
def get_all_tags_cached() -> List:
    """Get all tags with 5-minute cache.

    Returns:
        List of Tag objects ordered by name
    """
    from app.models.tables import Tag
    return Tag.query.order_by(Tag.nome).all()


@cache.memoize(timeout=300)
def get_tag_by_id_cached(tag_id: int):
    """Get a single tag by ID with caching.

    Args:
        tag_id: The tag ID to fetch

    Returns:
        Tag object or None if not found
    """
    from app.models.tables import Tag
    return Tag.query.get(tag_id)


@cache.memoize(timeout=300)
def get_tag_by_name_cached(nome: str):
    """Get a tag by name (case-insensitive) with caching.

    Args:
        nome: Tag name to search for

    Returns:
        Tag object or None if not found
    """
    from app.models.tables import Tag
    import sqlalchemy as sa
    return Tag.query.filter(sa.func.lower(Tag.nome) == nome.lower()).first()


@cache.memoize(timeout=60)  # 1 minute cache
def get_user_by_email_cached(email: str):
    """Get user by email with short-term caching.

    Args:
        email: User email to search for

    Returns:
        User object or None if not found
    """
    from app.models.tables import User
    return User.query.filter_by(email=email).first()


@cache.memoize(timeout=60)
def get_user_by_id_cached(user_id: int):
    """Get user by ID with short-term caching.

    Args:
        user_id: User ID to fetch

    Returns:
        User object or None if not found
    """
    from app.models.tables import User
    return User.query.get(user_id)


def invalidate_tag_cache():
    """Invalidate all tag-related caches.

    Call this after creating, updating, or deleting tags.
    """
    cache.delete_memoized(get_all_tags_cached)
    cache.delete_memoized(get_tag_by_name_cached)
    # Note: get_tag_by_id_cached is harder to invalidate selectively
    # Consider using cache.clear() if needed, but that clears ALL cache


def invalidate_user_cache(user_id: Optional[int] = None, email: Optional[str] = None):
    """Invalidate user-related caches.

    Args:
        user_id: Optional user ID to invalidate specific cache
        email: Optional email to invalidate specific cache

    Call this after updating user information.
    """
    if user_id:
        cache.delete_memoized(get_user_by_id_cached, user_id)
    if email:
        cache.delete_memoized(get_user_by_email_cached, email)
