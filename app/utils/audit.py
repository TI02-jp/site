"""User action auditing module for compliance and security."""

import logging
from functools import wraps
from typing import Optional, Dict, Any

from flask import request, g
from flask_login import current_user


# Get dedicated user actions logger
user_actions_logger = logging.getLogger('user_actions')


class ActionType:
    """Constants for action types."""
    # Autenticacao
    LOGIN = 'login'
    LOGOUT = 'logout'
    FAILED_LOGIN = 'failed_login'

    # CRUD
    CREATE = 'create'
    UPDATE = 'update'
    DELETE = 'delete'
    VIEW = 'view'

    # Usuarios
    CHANGE_PASSWORD = 'change_password'
    RESET_PASSWORD = 'reset_password'
    CHANGE_ROLE = 'change_role'
    CHANGE_PERMISSIONS = 'change_permissions'
    ACTIVATE_USER = 'activate_user'
    DEACTIVATE_USER = 'deactivate_user'

    # Arquivos
    UPLOAD = 'upload'
    DOWNLOAD = 'download'
    DELETE_FILE = 'delete_file'

    # Tarefas
    ASSIGN_TASK = 'assign_task'
    COMPLETE_TASK = 'complete_task'
    REOPEN_TASK = 'reopen_task'

    # Configuracoes
    CHANGE_SETTINGS = 'change_settings'


class ResourceType:
    """Constants for resource types."""
    USER = 'user'
    TASK = 'task'
    ANNOUNCEMENT = 'announcement'
    COURSE = 'course'
    MEETING = 'meeting'
    EVENT = 'event'
    COMPANY = 'company'
    DEPARTMENT = 'department'
    FILE = 'file'
    SESSION = 'session'
    TAG = 'tag'
    PROCEDURE = 'procedure'
    CONSULTORIA = 'consultoria'


def log_user_action(
    action_type: str,
    resource_type: str,
    action_description: str,
    resource_id: Optional[int] = None,
    old_values: Optional[Dict[str, Any]] = None,
    new_values: Optional[Dict[str, Any]] = None,
):
    """Log a user action to both database and log files.

    Args:
        action_type: Type of action (use ActionType constants)
        resource_type: Type of resource affected (use ResourceType constants)
        action_description: Human-readable description of the action
        resource_id: Optional ID of the affected resource
        old_values: Optional dict of values before the change
        new_values: Optional dict of values after the change
    """
    # Import here to avoid circular imports
    from app import db
    from app.models.tables import AuditLog

    # Skip if user is not authenticated (system actions)
    if not current_user or not current_user.is_authenticated:
        return

    # Request context
    ip_address = request.remote_addr if request else None
    user_agent = request.headers.get('User-Agent') if request else None
    request_id = getattr(g, 'request_id', None)
    endpoint = request.endpoint if request else None

    # Database audit entry
    audit_entry = AuditLog(
        user_id=current_user.id,
        username=current_user.username,
        action_type=action_type,
        resource_type=resource_type,
        resource_id=resource_id,
        action_description=action_description,
        old_values=old_values,
        new_values=new_values,
        ip_address=ip_address,
        user_agent=user_agent,
        request_id=request_id,
        endpoint=endpoint,
    )

    try:
        db.session.add(audit_entry)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        user_actions_logger.error(f"Failed to save audit log to database: {e}")

    # File log with structured extra data for JSON formatter
    log_message = (
        f"[{current_user.username}] {action_type.upper()} {resource_type} "
        f"(ID: {resource_id}) - {action_description} - IP: {ip_address}"
    )

    user_actions_logger.info(
        log_message,
        extra={
            'request_id': request_id,
            'user_id': current_user.id,
            'username': current_user.username,
            'action_type': action_type,
            'resource_type': resource_type,
            'resource_id': resource_id,
            'ip_address': ip_address,
            'old_values': old_values,
            'new_values': new_values,
        }
    )


def audit_action(action_type: str, resource_type: str, description_template: Optional[str] = None):
    """Decorator to automatically log user actions.

    Args:
        action_type: Type of action (use ActionType constants)
        resource_type: Type of resource (use ResourceType constants)
        description_template: Optional template for description (e.g., "Created user {username}")

    Example:
        @audit_action(ActionType.CREATE, ResourceType.USER, "Created user {username}")
        def create_user():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Execute the function
            result = f(*args, **kwargs)

            # Log after success
            try:
                resource_id = kwargs.get('id') or kwargs.get('user_id') or kwargs.get('task_id')
                description = description_template or f"{action_type.title()} {resource_type}"

                # Try to format description with kwargs
                if description_template:
                    try:
                        description = description_template.format(**kwargs)
                    except (KeyError, ValueError):
                        pass  # Use template as-is if formatting fails

                log_user_action(
                    action_type=action_type,
                    resource_type=resource_type,
                    action_description=description,
                    resource_id=resource_id,
                )
            except Exception as e:
                user_actions_logger.error(f"Failed to log user action in decorator: {e}")

            return result
        return decorated_function
    return decorator
