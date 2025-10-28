"""Permission utilities for role-based access control."""


def is_user_admin(user) -> bool:
    """Check if a user has admin privileges.

    Returns True if the user's role is 'admin' or 'admin_master'.

    Args:
        user: User object with a 'role' attribute

    Returns:
        bool: True if user is an admin, False otherwise
    """
    if not user:
        return False
    return user.role in ("admin", "admin_master")


def requires_admin_role(user) -> bool:
    """Alias for is_user_admin for backward compatibility."""
    return is_user_admin(user)
