from functools import wraps
from flask import abort
from flask_login import current_user, login_required


def require_role(role: str):
    """Decorator to restrict access to users with a specific role."""
    def decorator(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.role != role:
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def user_pode_atuar_no_setor(setor_id: int) -> bool:
    """Return True if the current user can operate on the given sector."""
    if current_user.role == "admin":
        return True
    return any(s.id == setor_id for s in current_user.setores)
