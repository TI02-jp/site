"""User action auditing module for compliance and security."""

import logging
from typing import Optional, Dict, Any

from flask import request, g
from flask_login import current_user


# Get dedicated user actions logger
user_actions_logger = logging.getLogger('user_actions')


def _extract_client_ip() -> str | None:
    """Resolve the best client IP, honoring reverse-proxy headers."""

    if not request:
        return None

    # Standard reverse proxy chain: client, proxy1, proxy2...
    forwarded_for = (request.headers.get('X-Forwarded-For') or '').strip()
    if forwarded_for:
        first_hop = forwarded_for.split(',')[0].strip()
        if first_hop:
            return first_hop

    real_ip = (request.headers.get('X-Real-IP') or '').strip()
    if real_ip:
        return real_ip

    # Flask may fill access_route with forwarded addresses depending on server stack.
    if getattr(request, 'access_route', None):
        for candidate in request.access_route:
            if candidate:
                return candidate

    return request.remote_addr


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
    CLIENT_COMPANY = 'client_company'
    CLIENT_DEPARTMENT = 'client_department'
    CLIENT_ANNOUNCEMENT = 'client_announcement'
    CLIENT_MEETING = 'client_meeting'


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
    ip_address = _extract_client_ip()
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
