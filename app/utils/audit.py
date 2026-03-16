import ipaddress
import logging
from typing import Optional, Dict, Any

from flask import request, g
from flask_login import current_user


# Get dedicated user actions logger
user_actions_logger = logging.getLogger('user_actions')


def _normalize_ip_candidate(candidate: str | None) -> str | None:
    """Normalize forwarded IP candidates (e.g. '1.2.3.4:1234')."""

    if not candidate:
        return None

    value = candidate.strip().strip('"')
    if not value or value.lower() == 'unknown':
        return None

    if value.startswith('[') and ']' in value:
        value = value[1:value.index(']')]
    elif value.count(':') == 1 and '.' in value:
        host, port = value.rsplit(':', 1)
        if port.isdigit():
            value = host

    return value.strip() or None


def _parse_ip(candidate: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse a normalized candidate into an IP object."""

    normalized = _normalize_ip_candidate(candidate)
    if not normalized:
        return None

    try:
        return ipaddress.ip_address(normalized)
    except ValueError:
        return None


def _is_public_ip(parsed_ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True when the address is public-routable."""

    return not (
        parsed_ip.is_private
        or parsed_ip.is_loopback
        or parsed_ip.is_link_local
        or parsed_ip.is_multicast
        or parsed_ip.is_reserved
        or parsed_ip.is_unspecified
    )


def _extract_client_ip() -> str | None:
    """Resolve the best client IP, honoring reverse-proxy headers."""

    if not request:
        return None

    candidates: list[str] = []

    forwarded_for = (request.headers.get('X-Forwarded-For') or '').strip()
    if forwarded_for:
        candidates.extend(part.strip() for part in forwarded_for.split(',') if part.strip())

    for header in ('X-Real-IP', 'CF-Connecting-IP', 'True-Client-IP', 'X-Client-IP'):
        header_value = (request.headers.get(header) or '').strip()
        if header_value:
            candidates.append(header_value)

    if getattr(request, 'access_route', None):
        candidates.extend(str(value).strip() for value in request.access_route if value)

    remote_addr = request.remote_addr
    if remote_addr:
        candidates.append(str(remote_addr).strip())

    first_valid_ip: str | None = None
    for candidate in candidates:
        parsed = _parse_ip(candidate)
        if not parsed:
            continue

        normalized = str(parsed)
        if first_valid_ip is None:
            first_valid_ip = normalized

        if _is_public_ip(parsed):
            return normalized

    return first_valid_ip


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

