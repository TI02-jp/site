"""
Blueprint para gestao de tarefas.

Este modulo contem rotas para o sistema de tarefas incluindo
visao geral, criacao, edicao, respostas e transferencias.

Rotas:
    - GET /tasks/overview: Visao geral Kanban
    - GET /tasks/overview/mine: Minhas tarefas
    - GET /tasks/overview/personal: Tarefas pessoais
    - GET/POST /tasks/new: Nova tarefa
    - GET/POST /tasks/<id>/edit: Editar tarefa
    - GET /tasks/users/<tag_id>: Tarefas por usuario
    - GET/POST /tasks/<id>/transfer: Transferir tarefa
    - GET /tasks/sector/<tag_id>: Tarefas por setor
    - GET /tasks/history: Historico
    - GET /tasks/<id>/responses: Listar respostas (JSON)
    - POST /tasks/<id>/responses: Criar resposta
    - POST /tasks/<id>/responses/read: Marcar como lido
    - GET /tasks/<id>: Visualizar tarefa
    - POST /tasks/<id>/status: Atualizar status
    - POST /tasks/<id>/delete: Excluir tarefa

Dependencias:
    - models: Task, TaskStatus, TaskPriority, TaskAttachment, TaskResponse, Tag, User
    - forms: TaskForm
    - decorators: login_required, meeting_only_access_check

Autor: Refatoracao automatizada
Data: 2024-12
"""

# ============================================================================
# IMPORTS
# ============================================================================

# Core Flask
from flask import (
    Blueprint,
    request,
    render_template,
    jsonify,
    redirect,
    url_for,
    flash,
    abort,
    current_app,
    g,
    has_request_context,
)
from flask_login import login_required, current_user

# Database & SQLAlchemy
from app import db
import sqlalchemy as sa
from sqlalchemy.orm import joinedload, aliased

# Models
from app.models.tables import (
    Task,
    TaskStatus,
    TaskPriority,
    TaskAttachment,
    TaskResponse,
    TaskResponseParticipant,
    TaskFollower,
    TaskStatusHistory,
    TaskNotification,
    NotificationType,
    Tag,
    User,
    SAO_PAULO_TZ,
    TaskHistory,
)

# Forms
from app.forms import TaskForm

# Decorators
from app.controllers.routes._decorators import (
    meeting_only_access_check,
    get_accessible_tag_ids as _get_accessible_tag_ids,
)

# Base Helpers
from app.controllers.routes._base import (
    EXCLUDED_TASK_TAGS,
    EXCLUDED_TASK_TAGS_LOWER,
    PERSONAL_TAG_PREFIX,
    utc3_now,
    save_task_file as _save_task_file,
)

# Utilities
from app.utils.performance_middleware import track_custom_span
from app.utils.security import sanitize_html

# Optimized queries with cache and eager loading
from app.services.optimized_queries import (
    get_active_users_with_tags,
)

# Other imports
from datetime import datetime, timezone
from typing import Iterable
import re
import unicodedata
from urllib.parse import urlparse


# ============================================================================
# BLUEPRINT DEFINITION
# ============================================================================

tasks_bp = Blueprint('tasks', __name__)


# ============================================================================
# HELPER FUNCTIONS - SHARED UTILITIES
# ============================================================================

def _get_ti_tag() -> Tag | None:
    """Return the TI tag if it exists (cached per request)."""

    if not has_request_context():
        return Tag.query.filter(sa.func.lower(Tag.nome) == "ti").first()

    if not hasattr(g, "_ti_tag"):
        g._ti_tag = Tag.query.filter(sa.func.lower(Tag.nome) == "ti").first()
    return g._ti_tag


def _can_user_access_tag(tag: Tag | None, user: User | None = None) -> bool:
    """Return True if ``user`` (or the current user) may access ``tag``."""

    if tag is None:
        return False
    if user is None:
        user = current_user if current_user.is_authenticated else None
    if user is None:
        return False
    if getattr(user, "role", None) == "admin":
        return True
    # Check if it's a personal tag for this user
    if tag.nome.startswith(PERSONAL_TAG_PREFIX):
        expected_personal_tag = f"{PERSONAL_TAG_PREFIX}{user.id}"
        if tag.nome == expected_personal_tag:
            return True
    user_tags = getattr(user, "tags", []) or []
    if tag in user_tags:
        return True
    return False


# ============================================================================
# HELPER FUNCTIONS - ACCESS CONTROL
# ============================================================================

def _user_can_access_task(task: Task, user: User | None) -> bool:
    """Return ``True`` when ``user`` is allowed to access ``task``."""

    if user is None:
        return False
    if getattr(user, "role", None) == "admin":
        return True
    if not getattr(task, "is_private", False):
        return True
    user_id = getattr(user, "id", None)
    if user_id is None:
        return False
    if (
        task.created_by == user_id
        or task.assigned_to == user_id
        or task.completed_by == user_id
    ):
        return True
    follow_up_entries = getattr(task, "follow_up_assignments", None) or []
    return any(entry.user_id == user_id for entry in follow_up_entries)


def _user_can_transfer_task(task: Task, user: User | None) -> bool:
    """Return ``True`` when ``user`` is allowed to transfer ``task`` to another assignee."""

    if user is None:
        return False
    if getattr(user, "role", None) == "admin":
        return True
    user_id = getattr(user, "id", None)
    if user_id is None:
        return False
    if task.created_by == user_id or task.assigned_to == user_id:
        return True
    follow_up_entries = getattr(task, "follow_up_assignments", None) or []
    return any(entry.user_id == user_id for entry in follow_up_entries)


def _task_visible_for_user(task: Task, user: User) -> bool:
    """Return True when ``task`` should be shown to ``user``."""
    return _user_can_access_task(task, user)


def _filter_tasks_for_user(tasks: list[Task], user: User) -> list[Task]:
    """Return a filtered list of tasks (and subtasks) visible to ``user``."""
    visible: list[Task] = []
    for task in tasks:
        if not _task_visible_for_user(task, user):
            continue
        children = list(getattr(task, "children", []) or [])
        filtered_children = _filter_tasks_for_user(children, user) if children else []
        task.filtered_children = filtered_children
        visible.append(task)
    return visible


def _user_has_task_privileges(task: Task, user: User | None) -> bool:
    """Return True when user should have the same powers as the responsible."""

    if user is None:
        return False
    if getattr(user, "role", None) == "admin":
        return True
    user_id = getattr(user, "id", None)
    if not user_id:
        return False
    if (
        task.created_by == user_id
        or task.assigned_to == user_id
        or task.completed_by == user_id
    ):
        return True
    return _is_task_follow_up(task, user_id)


def _user_task_access_filter(user: User):
    """Return SQLAlchemy filter for tasks accessible by user (including as follower)."""
    from app.models.tables import TaskFollower

    # Usuário pode acessar se:
    # 1. Task não é privada OU
    # 2. É o criador OU
    # 3. É o responsável OU
    # 4. É acompanhante
    follower_select = sa.select(TaskFollower.task_id).where(TaskFollower.user_id == user.id)
    return sa.or_(
        Task.is_private.is_(False),
        Task.created_by == user.id,
        Task.assigned_to == user.id,
        Task.id.in_(follower_select)
    )


# ============================================================================
# HELPER FUNCTIONS - TASK OPERATIONS
# ============================================================================

def _is_only_me_selected(values: list[str]) -> bool:
    """Return True when 'Somente para mim' checkbox is effectively selected."""

    truthy = {"1", "true", "on", "yes", "y"}
    for value in values:
        if isinstance(value, str) and value.lower() in truthy:
            return True
    return False


def _iter_tasks_with_children(tasks: Iterable[Task]) -> Iterable[Task]:
    """Yield tasks recursively, following ``filtered_children`` when available."""

    for task in tasks:
        yield task
        children = getattr(task, "filtered_children", None)
        if children is None:
            children = getattr(task, "children", None)
        if children:
            yield from _iter_tasks_with_children(children)


def _coerce_task_status(status_value) -> TaskStatus:
    """Return a valid TaskStatus, defaulting to PENDING on errors."""

    if isinstance(status_value, TaskStatus):
        return status_value
    try:
        return TaskStatus(status_value)
    except Exception:
        return TaskStatus.PENDING


def _group_root_tasks_by_status(
    tasks: Iterable[Task], visible_statuses: Iterable[TaskStatus] | None = None
) -> dict[TaskStatus, list[Task]]:
    """Return a mapping of status -> root tasks, ensuring children stay nested."""

    if visible_statuses:
        ordered_statuses = list(visible_statuses)
    else:
        ordered_statuses = list(TaskStatus)

    buckets: dict[TaskStatus, list[Task]] = {status: [] for status in ordered_statuses}
    tracked_statuses = set(buckets.keys())
    allow_extra_status = not visible_statuses

    for task in tasks:
        children = getattr(task, "children", None) or []
        task.filtered_children = sorted(
            children, key=lambda child: child.created_at or datetime.min
        )
        if getattr(task, "parent_id", None):
            continue

        status = _coerce_task_status(getattr(task, "status", None))
        if status not in tracked_statuses:
            if not allow_extra_status:
                continue
            buckets[status] = []
            tracked_statuses.add(status)

        buckets[status].append(task)

    # Guarantee all requested statuses exist even if empty
    for status in ordered_statuses:
        buckets.setdefault(status, [])

    return buckets


def _delete_task_recursive(task: Task) -> None:
    """Delete a task and all of its subtasks recursively."""

    for child in list(task.children or []):
        _delete_task_recursive(child)

    for history in TaskStatusHistory.query.filter_by(task_id=task.id).all():
        db.session.delete(history)
    for notification in TaskNotification.query.filter_by(task_id=task.id).all():
        db.session.delete(notification)

    db.session.delete(task)


# ============================================================================
# HELPER FUNCTIONS - NOTIFICATIONS
# ============================================================================

def _get_task_notification_recipients(task: Task, exclude_user_id: int | None = None) -> set[int]:
    """
    Retorna os IDs dos usuários que devem receber notificações sobre uma tarefa.
    Regra: Se tem responsável, notifica só ele; se não, notifica o setor.

    Args:
        task: A tarefa em questão
        exclude_user_id: ID do usuário a ser excluído (geralmente quem fez a ação)

    Returns:
        Set de IDs de usuários a serem notificados
    """
    recipients: set[int] = set()

    # Se tem responsável e não é privada, notifica apenas o responsável
    if task.assigned_to:
        if exclude_user_id is None or task.assigned_to != exclude_user_id:
            recipients.add(task.assigned_to)
    # Se não tem responsável e não é privada, notifica o setor
    elif not task.is_private and task.tag and getattr(task.tag, "users", None):
        for member in getattr(task.tag, "users", []) or []:
            if not getattr(member, "ativo", False):
                continue
            if not member.id:
                continue
            if exclude_user_id and member.id == exclude_user_id:
                continue
            recipients.add(member.id)

    return recipients


# ============================================================================
# HELPER FUNCTIONS - FOLLOWERS/FOLLOW-UP
# ============================================================================

def _sync_task_followers(task: Task, user_ids: list[int]) -> None:
    """Persist acompanhamento participants for ``task`` in bulk."""

    desired = [uid for uid in dict.fromkeys(user_ids) if isinstance(uid, int) and uid > 0]
    desired_set = set(desired)

    existing_rows = TaskFollower.query.filter_by(task_id=task.id).all()
    existing = {row.user_id: row for row in existing_rows}

    for user_id, follower in list(existing.items()):
        if user_id not in desired_set:
            db.session.delete(follower)

    for user_id in desired:
        if user_id in existing:
            continue
        db.session.add(TaskFollower(task_id=task.id, user_id=user_id))

    # Garantir que as alterações fiquem disponíveis para o restante do fluxo
    db.session.flush()


def _task_follow_up_user_ids(task: Task) -> set[int]:
    """Return a set with all acompanhamento participant IDs for ``task``."""

    entries = getattr(task, "follow_up_assignments", None) or []
    return {
        entry.user_id
        for entry in entries
        if getattr(entry, "user_id", None)
    }


def _is_task_follow_up(task: Task, user: User | int | None) -> bool:
    """Return True when ``user`` (or ``user_id``) is marked as acompanhamento."""

    if user is None:
        return False
    user_id = user if isinstance(user, int) else getattr(user, "id", None)
    if not user_id:
        return False
    return user_id in _task_follow_up_user_ids(task)


def _extract_follow_up_user_ids(form: TaskForm) -> list[int]:
    """Return sanitized user IDs selected for acompanhamento."""

    try:
        selected_ids: list[int] = list(form.follow_up_users.data or [])
    except (TypeError, ValueError):
        selected_ids = []
    normalized: list[int] = []
    seen: set[int] = set()
    for user_id in selected_ids:
        if not isinstance(user_id, int):
            try:
                user_id = int(user_id)
            except (TypeError, ValueError):
                continue
        if user_id <= 0 or user_id in seen:
            continue
        seen.add(user_id)
        normalized.append(user_id)
    # Retorna a lista normalizada se houver usuários selecionados
    return normalized


def _build_follow_up_user_choices() -> list[tuple[int, str]]:
    """Return active portal users for the acompanhamento multi-select."""
    entries: dict[int, str] = {}
    # Otimizado: usa cache e eager loading de tags
    users = get_active_users_with_tags()
    for user in users:
        label = (user.name or user.username or "").strip()
        if not label:
            label = user.username or f"Usuário {user.id}"
        entries[user.id] = label
    return _sort_choice_pairs(list(entries.items()))


# ============================================================================
# HELPER FUNCTIONS - FORM CHOICES
# ============================================================================

def _sortable_text(value: str | None) -> str:
    """Return a lowercased, accent-free representation suitable for sorting."""
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return without_accents.casefold()


def _sort_choice_pairs(
    pairs: list[tuple[int, str]], keep_first: bool = False
) -> list[tuple[int, str]]:
    """Sort a list of ``(value, label)`` pairs alphabetically by label.

    When ``keep_first`` is ``True`` the first element (commonly a sentinel like
    ``0`` → "Sem responsável") is preserved at the front and only the remaining
    items are sorted.
    """
    if not pairs:
        return []
    if keep_first:
        head, *tail = pairs
        return [head, *sorted(tail, key=lambda item: _sortable_text(item[1]))]
    return sorted(pairs, key=lambda item: _sortable_text(item[1]))


def _build_task_user_choices(tag_obj: Tag | None) -> list[tuple[int, str]]:
    """Build select choices for task assignee field based on tag membership."""
    entries: dict[int, str] = {}
    if tag_obj:
        users = [
            u
            for u in (getattr(tag_obj, "users", []) or [])
            if getattr(u, "ativo", False)
        ]
        for user in users:
            label = (user.name or user.username or "").strip()
            if label:
                entries[user.id] = label
            else:
                entries[user.id] = user.username or ""
    display_name = (current_user.name or current_user.username or "").strip()
    if current_user.id and current_user.id not in entries:
        entries[current_user.id] = display_name

    sorted_entries = sorted(entries.items(), key=lambda item: _sortable_text(item[1]))
    return [(0, "Sem responsável"), *sorted_entries]


# ============================================================================
# HELPER FUNCTIONS - CONVERSATION/RESPONSES
# ============================================================================

def _task_conversation_participant_ids(task: Task) -> set[int]:
    """Return user IDs that participate in the task conversation."""

    participant_ids = {task.created_by}
    if task.assigned_to:
        participant_ids.add(task.assigned_to)
    if task.completed_by:
        participant_ids.add(task.completed_by)
    participant_ids.update(_task_follow_up_user_ids(task))
    return {uid for uid in participant_ids if uid}


def _user_can_access_task_conversation(task: Task, user: User) -> bool:
    """Return True when ``user`` is allowed to view/post task responses."""

    return _user_has_task_privileges(task, user)


def _ensure_response_participant(task_id: int, user_id: int) -> TaskResponseParticipant:
    """Return or create a conversation participant row for the given task/user."""

    participant = TaskResponseParticipant.query.filter_by(
        task_id=task_id, user_id=user_id
    ).one_or_none()
    if participant is None:
        participant = TaskResponseParticipant(task_id=task_id, user_id=user_id)
        db.session.add(participant)
        db.session.flush()
    return participant


def _serialize_task(task: Task) -> dict[str, object]:
    """Return a JSON-serializable representation of ``task``."""

    tag = getattr(task, "tag", None)
    assignee = getattr(task, "assignee", None)
    finisher = getattr(task, "finisher", None)
    local_completed_at = (
        task.completed_at.replace(tzinfo=timezone.utc).astimezone(SAO_PAULO_TZ)
        if task.completed_at
        else None
    )
    assignee_name = None
    if assignee:
        assignee_name = assignee.name or assignee.username
    finisher_name = None
    if finisher:
        finisher_name = finisher.name or finisher.username
    follow_up_payload: list[dict[str, object]] = []
    for entry in getattr(task, "follow_up_assignments", []) or []:
        user = entry.user
        if not user:
            continue
        display_name = (user.name or user.username or "").strip() or None
        follow_up_payload.append(
            {
                "id": user.id,
                "name": display_name,
            }
        )

    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status.value if task.status else None,
        "priority": task.priority.value if task.priority else None,
        "tag_id": task.tag_id,
        "tag_name": tag.nome if tag else None,
        "assigned_to": task.assigned_to,
        "assignee_name": assignee_name,
        "created_by": task.created_by,
        "completed_by": task.completed_by,
        "completed_by_name": finisher_name,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "completed_at_display": (
            local_completed_at.strftime("%d/%m/%Y %H:%M") if local_completed_at else None
        ),
        "is_private": task.is_private,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "parent_id": task.parent_id,
        "updated_at": task.updated_at.isoformat() if getattr(task, "updated_at", None) else None,
        "follow_up_users": follow_up_payload,
    }


def _serialize_task_response(response: TaskResponse, viewer_id: int) -> dict[str, object]:
    """Serialize a ``TaskResponse`` into a JSON-friendly payload."""

    author = response.author
    local_created_at = (
        response.created_at.replace(tzinfo=timezone.utc).astimezone(SAO_PAULO_TZ)
        if response.created_at
        else None
    )
    created_at_display = (
        local_created_at.strftime("%d/%m/%Y %H:%M") if local_created_at else None
    )
    body = response.body or ""
    return {
        "id": response.id,
        "task_id": response.task_id,
        "body": body,
        "body_html": body.replace("\n", "<br>"),
        "created_at": response.created_at.isoformat() if response.created_at else None,
        "created_at_display": created_at_display,
        "author": {
            "id": author.id if author else None,
            "name": author.name if author and author.name else author.username if author else None,
        },
        "is_mine": author.id == viewer_id if author else False,
    }


def _build_task_conversation_meta(task: Task, viewer: User) -> dict[str, object]:
    """Return metadata required by the conversation sidebar/drawer."""

    participant_ids = _task_conversation_participant_ids(task)
    participants = User.query.filter(User.id.in_(participant_ids)).all() if participant_ids else []
    participants_info = []
    for person in participants:
        participants_info.append(
            {
                "id": person.id,
                "name": person.name or person.username,
                "is_creator": person.id == task.created_by,
                "is_assignee": person.id == task.assigned_to,
                "is_finisher": person.id == task.completed_by,
            }
        )
    participant_row = TaskResponseParticipant.query.filter_by(
        task_id=task.id, user_id=viewer.id
    ).one_or_none()
    last_read_at = participant_row.last_read_at if participant_row else None
    responses_query = (
        TaskResponse.query.filter_by(task_id=task.id)
        .order_by(TaskResponse.created_at.asc())
        .options(joinedload(TaskResponse.author))
    )
    responses = responses_query.all()
    serialized_responses = [_serialize_task_response(response, viewer.id) for response in responses]
    unread_count = 0
    for response in responses:
        if response.author_id == viewer.id:
            continue
        if not last_read_at or (response.created_at and response.created_at > last_read_at):
            unread_count += 1
    last_response_payload = serialized_responses[-1] if serialized_responses else None
    return {
        "participants": participants_info,
        "unread_count": unread_count,
        "last_response": last_response_payload,
        "total_responses": len(responses),
        "responses": serialized_responses,
        "last_read_at": last_read_at.isoformat() if last_read_at else None,
    }


def _load_task_response_summaries(
    task_ids: Iterable[int], viewer_id: int
) -> dict[int, dict[str, object]]:
    """Return unread counts and last-response info for the given tasks."""

    normalized_ids = {int(task_id) for task_id in task_ids if task_id}
    if not normalized_ids:
        return {}

    response_counts = dict(
        db.session.query(TaskResponse.task_id, sa.func.count(TaskResponse.id))
        .filter(TaskResponse.task_id.in_(normalized_ids))
        .group_by(TaskResponse.task_id)
        .all()
    )

    participant_alias = aliased(TaskResponseParticipant)
    unread_rows = (
        db.session.query(
            TaskResponse.task_id,
            sa.func.count(TaskResponse.id),
        )
        .outerjoin(
            participant_alias,
            sa.and_(
                participant_alias.task_id == TaskResponse.task_id,
                participant_alias.user_id == viewer_id,
            ),
        )
        .filter(TaskResponse.task_id.in_(normalized_ids))
        .filter(
            sa.or_(
                participant_alias.last_read_at.is_(None),
                TaskResponse.created_at > participant_alias.last_read_at,
            )
        )
        .filter(TaskResponse.author_id != viewer_id)
        .group_by(TaskResponse.task_id)
        .all()
    )
    unread_counts = {task_id: count for task_id, count in unread_rows}

    latest_subquery = (
        db.session.query(
            TaskResponse.task_id.label("task_id"),
            sa.func.max(TaskResponse.id).label("latest_id"),
        )
        .filter(TaskResponse.task_id.in_(normalized_ids))
        .group_by(TaskResponse.task_id)
        .subquery()
    )

    latest_rows = (
        db.session.query(TaskResponse, User)
        .join(latest_subquery, TaskResponse.id == latest_subquery.c.latest_id)
        .outerjoin(User, User.id == TaskResponse.author_id)
        .all()
    )

    last_responses: dict[int, dict[str, object]] = {}
    for response, author in latest_rows:
        local_created_at = (
            response.created_at.replace(tzinfo=timezone.utc).astimezone(SAO_PAULO_TZ)
            if response.created_at
            else None
        )
        last_responses[response.task_id] = {
            "body": response.body or "",
            "body_html": (response.body or "").replace("\n", "<br>"),
            "created_at": response.created_at.isoformat() if response.created_at else None,
            "created_at_display": local_created_at.strftime("%d/%m/%Y %H:%M")
            if local_created_at
            else None,
            "author": {
                "id": author.id if author else None,
                "name": author.name if author and author.name else (author.username if author else None),
            },
        }

    summaries: dict[int, dict[str, object]] = {}
    for task_id in normalized_ids:
        summaries[task_id] = {
            "unread_count": unread_counts.get(task_id, 0),
            "total_responses": response_counts.get(task_id, 0),
            "last_response": last_responses.get(task_id),
        }

    return summaries


def _is_safe_referrer(referrer_url: str) -> bool:
    """Return True when referrer belongs to the current host."""

    parsed = urlparse(referrer_url)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return False

    ref_host = (parsed.netloc or "").split(":", 1)[0].lower()
    current_host = (request.host or "").split(":", 1)[0].lower()

    if ref_host and ref_host != current_host:
        return False

    return True


# ============================================================================
# ROUTES - OVERVIEW & LISTS
# ============================================================================

@tasks_bp.route("/tasks/overview")
@login_required
@meeting_only_access_check
def tasks_overview():
    """Kanban view of all tasks grouped by status."""

    tag_param = (request.args.get("tag_id") or "").strip()
    priority_param = (request.args.get("priority") or "").strip().lower()
    keyword = (request.args.get("q") or "").strip()
    user_params = request.args.getlist("user_id")
    # compat: aceita antigo user_id_2
    user_param_second = (request.args.get("user_id_2") or "").strip()
    if user_param_second:
        user_params.append(user_param_second)
    if not user_params:
        fallback_single = (request.args.get("user_id") or "").strip()
        if fallback_single:
            user_params = [fallback_single]
    due_from_raw = (request.args.get("due_from") or "").strip()
    due_to_raw = (request.args.get("due_to") or "").strip()
    selected_priority = None
    selected_user_ids: list[int] = []
    selected_user_id = None
    selected_user_id_2 = None
    selected_tag_id = None

    def _parse_date_param(raw_value):
        if not raw_value:
            return None
        try:
            return datetime.strptime(raw_value, "%Y-%m-%d").date()
        except ValueError:
            return None

    query = (
        Task.query.join(Tag)
        .filter(Task.parent_id.is_(None))
        .filter(Task.is_private.is_(False))
        .filter(~Tag.nome.in_(EXCLUDED_TASK_TAGS))
        .filter(_user_task_access_filter(current_user))
    )

    if current_user.role != "admin":
        accessible_ids = _get_accessible_tag_ids(current_user)
        allowed_filters = []
        if accessible_ids:
            allowed_filters.append(Task.tag_id.in_(accessible_ids))
        allowed_filters.append(Task.created_by == current_user.id)
        query = query.filter(sa.or_(*allowed_filters))
    else:
        accessible_ids = []

    def _parse_user_param(raw_value):
        if not raw_value:
            return None
        try:
            return int(raw_value)
        except ValueError:
            return None

    selected_user_ids = []
    for raw_user in user_params[:2]:  # limite de 2
        parsed = _parse_user_param(raw_user)
        if parsed is not None and parsed not in selected_user_ids:
            selected_user_ids.append(parsed)
    if selected_user_ids:
        selected_user_id = selected_user_ids[0]
        if len(selected_user_ids) > 1:
            selected_user_id_2 = selected_user_ids[1]

    if selected_user_ids:
        from app.models.tables import TaskFollower

        def _participant_filter(target_user_id: int):
            follower_select = (
                sa.select(TaskFollower.task_id)
                .where(TaskFollower.user_id == target_user_id)
            )
            return sa.or_(
                Task.assigned_to == target_user_id,
                Task.created_by == target_user_id,
                Task.id.in_(follower_select),
            )

        for uid in selected_user_ids:
            query = query.filter(_participant_filter(uid))

    if priority_param:
        try:
            selected_priority = TaskPriority(priority_param)
            query = query.filter(Task.priority == selected_priority)
        except ValueError:
            selected_priority = None

    if keyword:
        pattern = f"%{keyword}%"
        query = query.filter(
            sa.or_(Task.title.ilike(pattern), Task.description.ilike(pattern))
        )

    if tag_param:
        try:
            candidate_tag_id = int(tag_param)
        except ValueError:
            candidate_tag_id = None
        if candidate_tag_id:
            if current_user.role == "admin":
                selected_tag_id = candidate_tag_id
            elif candidate_tag_id in accessible_ids:
                selected_tag_id = candidate_tag_id
        if selected_tag_id:
            query = query.filter(Task.tag_id == selected_tag_id)

    due_from = _parse_date_param(due_from_raw)
    due_to = _parse_date_param(due_to_raw)
    if due_from:
        query = query.filter(Task.due_date.isnot(None)).filter(Task.due_date >= due_from)
    if due_to:
        query = query.filter(Task.due_date.isnot(None)).filter(Task.due_date <= due_to)

    # Otimizado: usa cache e eager loading de tags
    active_users = get_active_users_with_tags()
    if current_user.role == "admin":
        available_tags = (
            Tag.query.filter(~Tag.nome.in_(EXCLUDED_TASK_TAGS))
            .order_by(Tag.nome.asc())
            .all()
        )
    else:
        available_tags = (
            Tag.query.filter(Tag.id.in_(accessible_ids))
            .order_by(Tag.nome.asc())
            .all()
            if accessible_ids
            else []
        )

    tasks = (
        query.options(
            joinedload(Task.tag),
            joinedload(Task.assignee),
            joinedload(Task.finisher),
            joinedload(Task.creator),
            joinedload(Task.children).joinedload(Task.assignee),
            joinedload(Task.children).joinedload(Task.finisher),
            joinedload(Task.children).joinedload(Task.tag),
            joinedload(Task.children).joinedload(Task.creator),
        )
        .order_by(Task.due_date)
        .limit(200)
        .all()
    )
    tasks = _filter_tasks_for_user(tasks, current_user)
    summaries = _load_task_response_summaries(
        (task.id for task in _iter_tasks_with_children(tasks)),
        current_user.id,
    )
    default_summary = {"unread_count": 0, "total_responses": 0, "last_response": None}
    for task in _iter_tasks_with_children(tasks):
        task.conversation_summary = summaries.get(task.id) or default_summary.copy()

    tasks_by_status = _group_root_tasks_by_status(tasks)

    done_sorted = sorted(
        tasks_by_status[TaskStatus.DONE],
        key=lambda x: x.completed_at or datetime.min,
        reverse=True,
    )
    history_count = max(0, len(done_sorted) - 5)
    tasks_by_status[TaskStatus.DONE] = done_sorted[:5]

    return render_template(
        "tasks_overview.html",
        tasks_by_status=tasks_by_status,
        TaskStatus=TaskStatus,
        history_count=history_count,
        allow_delete=current_user.role == "admin",
        priorities=list(TaskPriority),
        selected_priority=selected_priority.value if selected_priority else "",
        keyword=keyword,
        selected_user_id=selected_user_id,
        selected_user_id_2=selected_user_id_2,
        available_tags=available_tags,
        selected_tag_id=selected_tag_id,
        due_from=due_from.strftime("%Y-%m-%d") if due_from else "",
        due_to=due_to.strftime("%Y-%m-%d") if due_to else "",
        users=active_users,
    )

@tasks_bp.route("/tasks/overview/mine")
@login_required
def tasks_overview_mine():
    """Kanban view of tasks where the current user participates."""

    visible_statuses = [TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.DONE]

    keyword = (request.args.get("q") or "").strip()
    priority_param = (request.args.get("priority") or "").strip().lower()
    tag_param = (request.args.get("tag_id") or "").strip()
    due_from_raw = (request.args.get("due_from") or "").strip()
    due_to_raw = (request.args.get("due_to") or "").strip()
    user_params = request.args.getlist("user_id")
    user_param_second = (request.args.get("user_id_2") or "").strip()
    if user_param_second:
        user_params.append(user_param_second)
    if not user_params:
        fallback_single = (request.args.get("user_id") or "").strip()
        if fallback_single:
            user_params = [fallback_single]
    selected_user_ids: list[int] = []
    selected_user_id = None
    selected_user_id_2 = None
    selected_tag_id = None

    owned_sector_tags = [
        tag
        for tag in (current_user.tags or [])
        if not tag.nome.startswith(PERSONAL_TAG_PREFIX)
    ]
    owned_sector_tag_ids = [tag.id for tag in owned_sector_tags]
    available_tags = (
        Tag.query.filter(Tag.id.in_(owned_sector_tag_ids))
        .order_by(Tag.nome.asc())
        .all()
        if owned_sector_tag_ids
        else []
    )

    def _parse_date_param(raw_value: str | None):
        if not raw_value:
            return None
        try:
            return datetime.strptime(raw_value, "%Y-%m-%d").date()
        except ValueError:
            return None

    from app.models.tables import TaskFollower

    follower_select = (
        sa.select(TaskFollower.task_id)
        .where(TaskFollower.user_id == current_user.id)
    )

    participation_filters = [
        Task.created_by == current_user.id,
        Task.assigned_to == current_user.id,
        Task.id.in_(follower_select),
    ]
    if owned_sector_tag_ids:
        participation_filters.append(Task.tag_id.in_(owned_sector_tag_ids))

    query = (
        Task.query.join(Tag)
        .filter(Task.parent_id.is_(None))
        .filter(Task.is_private.is_(False))
        .filter(~Tag.nome.in_(EXCLUDED_TASK_TAGS))
        .filter(sa.or_(*participation_filters))
    )

    def _parse_user_param(raw_value: str | None):
        if not raw_value:
            return None
        try:
            return int(raw_value)
        except ValueError:
            return None

    for raw_user in user_params[:2]:
        parsed = _parse_user_param(raw_user)
        if parsed is not None and parsed not in selected_user_ids:
            selected_user_ids.append(parsed)
    if selected_user_ids:
        selected_user_id = selected_user_ids[0]
        if len(selected_user_ids) > 1:
            selected_user_id_2 = selected_user_ids[1]

    if selected_user_ids:
        def _participant_filter(target_user_id: int):
            follower_filter_select = (
                sa.select(TaskFollower.task_id)
                .where(TaskFollower.user_id == target_user_id)
            )
            return sa.or_(
                Task.assigned_to == target_user_id,
                Task.created_by == target_user_id,
                Task.id.in_(follower_filter_select),
            )

        for uid in selected_user_ids:
            query = query.filter(_participant_filter(uid))

    selected_priority = None
    if priority_param:
        try:
            selected_priority = TaskPriority(priority_param)
            query = query.filter(Task.priority == selected_priority)
        except ValueError:
            selected_priority = None

    if keyword:
        pattern = f"%{keyword}%"
        query = query.filter(
            sa.or_(Task.title.ilike(pattern), Task.description.ilike(pattern))
        )

    if tag_param:
        try:
            candidate_tag_id = int(tag_param)
        except ValueError:
            candidate_tag_id = None
        if candidate_tag_id and candidate_tag_id in owned_sector_tag_ids:
            selected_tag_id = candidate_tag_id
            query = query.filter(Task.tag_id == selected_tag_id)

    due_from = _parse_date_param(due_from_raw)
    due_to = _parse_date_param(due_to_raw)
    if due_from:
        query = query.filter(Task.due_date.isnot(None)).filter(Task.due_date >= due_from)
    if due_to:
        query = query.filter(Task.due_date.isnot(None)).filter(Task.due_date <= due_to)

    # selected_tag_id already set above if applicable

    tasks = (
        query.options(
            joinedload(Task.tag),
            joinedload(Task.assignee),
            joinedload(Task.finisher),
            joinedload(Task.creator),
            joinedload(Task.children).joinedload(Task.assignee),
            joinedload(Task.children).joinedload(Task.finisher),
            joinedload(Task.children).joinedload(Task.tag),
            joinedload(Task.children).joinedload(Task.creator),
        )
        .order_by(Task.due_date)
        .limit(200)
        .all()
    )
    # Otimizado: usa cache e eager loading de tags
    active_users = get_active_users_with_tags()
    available_tags = (
        Tag.query.filter(Tag.id.in_(owned_sector_tag_ids))
        .order_by(Tag.nome.asc())
        .all()
        if owned_sector_tag_ids
        else []
    )
    tasks = _filter_tasks_for_user(tasks, current_user)
    summaries = _load_task_response_summaries(
        (task.id for task in _iter_tasks_with_children(tasks)),
        current_user.id,
    )
    default_summary = {"unread_count": 0, "total_responses": 0, "last_response": None}
    for task in _iter_tasks_with_children(tasks):
        task.conversation_summary = summaries.get(task.id) or default_summary.copy()
    tasks_by_status = _group_root_tasks_by_status(tasks, visible_statuses)
    # Sort DONE tasks by completion date and show only last 5
    done_sorted = sorted(
        tasks_by_status[TaskStatus.DONE],
        key=lambda x: x.completed_at or datetime.min,
        reverse=True,
    )
    history_count = max(0, len(done_sorted) - 5)
    tasks_by_status[TaskStatus.DONE] = done_sorted[:5]

    history_url = url_for("tasks_history")

    return render_template(
        "tasks_overview_mine.html",
        keyword=keyword,
        selected_priority=selected_priority.value if selected_priority else "",
        available_tags=available_tags,
        selected_tag_id=selected_tag_id,
        due_from=due_from.strftime("%Y-%m-%d") if due_from else "",
        due_to=due_to.strftime("%Y-%m-%d") if due_to else "",
        selected_user_id=selected_user_id,
        selected_user_id_2=selected_user_id_2,
        users=active_users,
        tasks_by_status=tasks_by_status,
        TaskStatus=TaskStatus,
        visible_statuses=visible_statuses,
        history_count=history_count,
        allow_delete=current_user.role == "admin",
        history_url=history_url,
    )


@tasks_bp.route("/tasks/overview/personal")
@login_required
def tasks_overview_personal():
    """Display only private tasks that belong to the current user."""

    visible_statuses = [TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.DONE]
    query = (
        Task.query.filter(Task.parent_id.is_(None))
        .filter(Task.is_private.is_(True))
        # Tasks privadas são visíveis apenas para quem criou
        .filter(Task.created_by == current_user.id)
    )
    tasks = (
        query.options(
            joinedload(Task.tag),
            joinedload(Task.assignee),
            joinedload(Task.finisher),
            joinedload(Task.creator),
            joinedload(Task.children).joinedload(Task.assignee),
            joinedload(Task.children).joinedload(Task.finisher),
            joinedload(Task.children).joinedload(Task.tag),
            joinedload(Task.children).joinedload(Task.creator),
        )
        .order_by(Task.due_date)
        .limit(200)
        .all()
    )
    tasks = _filter_tasks_for_user(tasks, current_user)
    summaries = _load_task_response_summaries(
        (task.id for task in _iter_tasks_with_children(tasks)),
        current_user.id,
    )
    default_summary = {"unread_count": 0, "total_responses": 0, "last_response": None}
    for task in _iter_tasks_with_children(tasks):
        task.conversation_summary = summaries.get(task.id) or default_summary.copy()
    tasks_by_status = _group_root_tasks_by_status(tasks, visible_statuses)
    done_sorted = sorted(
        tasks_by_status[TaskStatus.DONE],
        key=lambda x: x.completed_at or datetime.min,
        reverse=True,
    )
    history_count = max(0, len(done_sorted) - 5)
    tasks_by_status[TaskStatus.DONE] = done_sorted[:5]

    return render_template(
        "tasks_overview_personal.html",
        tasks_by_status=tasks_by_status,
        TaskStatus=TaskStatus,
        visible_statuses=visible_statuses,
        history_count=history_count,
        allow_delete=current_user.role == "admin",
        history_url=url_for("tasks_history", only_me=1),
    )


@tasks_bp.route("/tasks/users/<int:tag_id>")
@login_required
def tasks_users(tag_id):
    """Return active users for the requested task tag."""
    tag = Tag.query.get_or_404(tag_id)
    if tag.nome.startswith(PERSONAL_TAG_PREFIX):
        display_name = current_user.name or current_user.username
        users = [{"id": current_user.id, "name": display_name}]
    else:
        users = [
            {"id": u.id, "name": u.name}
            for u in tag.users
            if u.ativo
        ]
        # Sort users alphabetically by name
        users.sort(key=lambda u: _sortable_text(u["name"]))
    return jsonify(users)


@tasks_bp.route("/tasks/sector/<int:tag_id>")
@login_required
@meeting_only_access_check
def tasks_sector(tag_id):
    """Kanban board of tasks for a specific sector/tag."""
    tag = Tag.query.get_or_404(tag_id)
    if tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    user_is_admin = current_user.role == "admin"
    if not _can_user_access_tag(tag, current_user):
        abort(403)
    ti_tag = _get_ti_tag()
    ti_tag_id = ti_tag.id if ti_tag else None
    assigned_param = (request.args.get("assigned_to_me", "") or "").lower()
    assigned_to_me = assigned_param in {"1", "true", "on", "yes"}
    query = Task.query.filter(
        Task.tag_id == tag_id,
        Task.parent_id.is_(None),
        sa.or_(Task.is_private.is_(False), Task.created_by == current_user.id),
    )
    if assigned_to_me:
        query = query.filter(Task.assigned_to == current_user.id)
    tasks = (
        query.options(
            joinedload(Task.tag),
            joinedload(Task.assignee),
            joinedload(Task.finisher),
            joinedload(Task.creator),  # NOVO: Eager load creator to prevent N+1
            # Removed status_history and attachments eager loading to reduce Cartesian product
            joinedload(Task.children).joinedload(Task.assignee),
            joinedload(Task.children).joinedload(Task.finisher),
            joinedload(Task.children).joinedload(Task.tag),
            joinedload(Task.children).joinedload(Task.creator),  # NOVO: Eager load creator for children
            # Removed children's status_history and attachments for same reason
        )
        .order_by(Task.due_date)
        .limit(200)  # Added limit to prevent loading too many tasks at once
        .all()
    )
    tasks = _filter_tasks_for_user(tasks, current_user)
    tasks_by_status = _group_root_tasks_by_status(tasks)
    done_sorted = sorted(
        tasks_by_status[TaskStatus.DONE],
        key=lambda x: x.completed_at or datetime.min,
        reverse=True,
    )
    history_count = max(0, len(done_sorted) - 5)
    tasks_by_status[TaskStatus.DONE] = done_sorted[:5]
    return render_template(
        "tasks_board.html",
        tag=tag,
        tasks_by_status=tasks_by_status,
        TaskStatus=TaskStatus,
        history_count=history_count,
        assigned_to_me=assigned_to_me,
        ti_tag_id=ti_tag_id,
    )

@tasks_bp.route("/tasks/history/<int:tag_id>")
@tasks_bp.route("/tasks/history", defaults={"tag_id": None})
@login_required
def tasks_history(tag_id=None):
    """Display archived tasks beyond the visible limit."""
    assigned_param = (request.args.get("assigned_to_me", "") or "").lower()
    assigned_to_me = assigned_param in {"1", "true", "on", "yes"}
    assigned_by_param = (request.args.get("assigned_by_me", "") or "").lower()
    assigned_by_me = assigned_by_param in {"1", "true", "on", "yes"}
    only_me_param = (request.args.get("only_me", "") or "").lower()
    only_me = only_me_param in {"1", "true", "on", "yes"}
    if tag_id:
        tag = Tag.query.get_or_404(tag_id)
        if tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
            abort(404)
        if not _can_user_access_tag(tag, current_user):
            abort(403)
        query = Task.query.filter(
            Task.tag_id == tag_id,
            Task.parent_id.is_(None),
            Task.status == TaskStatus.DONE,
        ).filter(_user_task_access_filter(current_user))
    else:
        tag = None
        if current_user.role == "admin":
            query = Task.query.filter(
                Task.parent_id.is_(None),
                Task.status == TaskStatus.DONE,
            ).filter(_user_task_access_filter(current_user))
        else:
            tag_ids = _get_accessible_tag_ids(current_user)
            filters = []
            if tag_ids:
                filters.append(Task.tag_id.in_(tag_ids))
            filters.append(Task.created_by == current_user.id)
            query = Task.query.filter(
                Task.parent_id.is_(None),
                Task.status == TaskStatus.DONE,
                sa.or_(*filters),
            )
            query = query.filter(_user_task_access_filter(current_user))
    if query is not None:
        if only_me:
            # Select para tasks onde o usuário é acompanhante
            from app.models.tables import TaskFollower
            follower_select = (
                sa.select(TaskFollower.task_id)
                .where(TaskFollower.user_id == current_user.id)
            )

            query = query.filter(Task.is_private.is_(True)).filter(
                sa.or_(
                    Task.created_by == current_user.id,
                    Task.assigned_to == current_user.id,
                    Task.id.in_(follower_select),
                )
            )
        if assigned_to_me:
            query = query.filter(Task.assigned_to == current_user.id)
        if assigned_by_me:
            query = query.filter(Task.created_by == current_user.id)
        tasks = (
            query.order_by(Task.completed_at.desc())
            .options(joinedload(Task.tag), joinedload(Task.finisher))
            .offset(5)
            .all()
        )
    else:
        tasks = []
    tasks = _filter_tasks_for_user(tasks, current_user)
    summaries = _load_task_response_summaries((task.id for task in _iter_tasks_with_children(tasks)), current_user.id)
    default_summary = {"unread_count": 0, "total_responses": 0, "last_response": None}
    for task in _iter_tasks_with_children(tasks):
        task.conversation_summary = summaries.get(task.id) or default_summary.copy()
    return render_template(
        "tasks_history.html",
        tag=tag,
        tasks=tasks,
        assigned_to_me=assigned_to_me,
        assigned_by_me=assigned_by_me,
        only_me=only_me,
    )


# ============================================================================
# ROUTES - CREATE & EDIT
# ============================================================================

@tasks_bp.route("/tasks/new", methods=["GET", "POST"])
@login_required
def tasks_new():
    """Form to create a new task or subtask."""
    parent_id = request.args.get("parent_id", type=int)
    return_url = request.args.get("return_url")  # Não usar request.referrer - queremos ir para a Central de Tarefas
    form = TaskForm()

    if request.method == "POST" and not parent_id:
        posted_parent_id = form.parent_id.data
        if not posted_parent_id:
            try:
                posted_parent_id = int(request.form.get("parent_id", "") or 0)
            except (TypeError, ValueError):
                posted_parent_id = None
        parent_id = posted_parent_id or None

    parent_task = Task.query.get(parent_id) if parent_id else None
    if parent_task and parent_task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    if parent_task and parent_task.is_private and not _user_can_access_task(parent_task, current_user):
        abort(403)
    requested_tag_id = request.args.get("tag_id", type=int)
    choices: list[tuple[int, str]] = []
    preset_only_me_param = (request.args.get("only_me", "") or "").lower()
    if request.method == "GET" and preset_only_me_param in {"1", "true", "on", "yes"}:
        form.only_me.data = True
    tag = parent_task.tag if parent_task else None
    if parent_task:
        form.parent_id.data = parent_task.id
        form.tag_id.choices = [(parent_task.tag_id, parent_task.tag.nome)]
        form.tag_id.data = parent_task.tag_id
        # Não desabilitar o campo aqui - será tratado no template
        form.assigned_to.choices = _build_task_user_choices(parent_task.tag)
    else:
        tags_query = (
            Tag.query.filter(~Tag.nome.in_(EXCLUDED_TASK_TAGS))
            .filter(~Tag.nome.like(f"{PERSONAL_TAG_PREFIX}%"))
            .order_by(Tag.nome)
        )
        choices = [(t.id, t.nome) for t in tags_query.all()]
        choices = _sort_choice_pairs(choices)
        form.tag_id.choices = choices
        selected_tag_id = form.tag_id.data
        if not selected_tag_id and requested_tag_id:
            selected_tag_id = requested_tag_id
            if request.method == "GET":
                form.tag_id.data = selected_tag_id
        if not selected_tag_id and choices:
            selected_tag_id = choices[0][0]
            if request.method == "GET":
                form.tag_id.data = selected_tag_id
        if selected_tag_id:
            tag = Tag.query.get(selected_tag_id)
        form.assigned_to.choices = _build_task_user_choices(tag)

    form.follow_up_users.choices = _build_follow_up_user_choices()

    # Garantir que o valor do only_me seja preservado no POST
    if request.method == "POST":
        # Forçar o valor do checkbox com base em todos os valores enviados
        form.only_me.data = _is_only_me_selected(request.form.getlist("only_me"))
        current_app.logger.info(
            f"Task create POST - only_me raw value: {request.form.get('only_me')}, "
            f"form.only_me.data: {form.only_me.data}, "
            f"tag_id: {form.tag_id.data}, assigned_to: {form.assigned_to.data}"
        )

    if request.method == "POST" and form.only_me.data:
        form.assigned_to.data = current_user.id
        current_app.logger.info("Task create - only_me checked, forcing self-assignment")

    if form.validate_on_submit():
        follow_up_user_ids = _extract_follow_up_user_ids(form)
        current_app.logger.info("Formulário validado com sucesso. Criando task...")
        tag_id = parent_task.tag_id if parent_task else form.tag_id.data
        if not parent_task and (tag is None or tag.id != tag_id):
            tag = Tag.query.get(tag_id)
        if tag is None:
            abort(400)
        assignee_id = form.assigned_to.data or None
        is_private = bool(form.only_me.data)
        current_app.logger.info(
            f"Task create - is_private: {is_private}, tag_id: {tag_id}, "
            f"assignee_id: {assignee_id}, tag_name: {tag.nome if tag else 'None'}"
        )
        if is_private:
            assignee_id = current_user.id

        try:
            creation_notification_records: list[tuple[int, TaskNotification]] = []
            task = Task(
                is_private=is_private,
                title=form.title.data,
                description=form.description.data,
                tag_id=tag_id,
                priority=TaskPriority(form.priority.data),
                due_date=form.due_date.data,
                created_by=current_user.id,
                parent_id=parent_id,
                assigned_to=assignee_id,
            )
            db.session.add(task)
            db.session.flush()

            # Processar uploads de anexos
            uploaded_files = [
                storage
                for storage in (form.attachments.data or [])
                if storage and storage.filename
            ]

            for uploaded_file in uploaded_files:
                saved = _save_task_file(uploaded_file)
                db.session.add(
                    TaskAttachment(
                        task=task,
                        file_path=saved["path"],
                        original_name=saved["name"],
                        mime_type=saved["mime_type"],
                    )
                )

            _sync_task_followers(task, follow_up_user_ids)

            creator_name = current_user.name or current_user.username
            creation_now = utc3_now()
            notification_payloads: list[tuple[int, str]] = []

            if task.assigned_to:
                # Evitar notificação quando o criador é também o responsável
                if task.assigned_to == current_user.id:
                    task._skip_assignment_notification = True
            elif not task.is_private and tag and getattr(tag, "users", None):
                sector_label = (
                    "Para Mim" if tag.nome.startswith(PERSONAL_TAG_PREFIX) else tag.nome
                )
                sector_message = f'Tarefa "{task.title}" atribuída no setor {sector_label}.'
                for member in getattr(tag, "users", []) or []:
                    if not getattr(member, "ativo", False):
                        continue
                    if not member.id or member.id == current_user.id:
                        continue
                    notification_payloads.append((member.id, sector_message))

            notified_users: set[int] = set()
            for user_id, message in notification_payloads:
                if user_id in notified_users:
                    continue
                notified_users.add(user_id)
                notification = TaskNotification(
                    user_id=user_id,
                    task_id=task.id,
                    type=NotificationType.TASK.value,
                    message=message[:255] if message else None,
                    created_at=creation_now,
                )
                db.session.add(notification)
                creation_notification_records.append((user_id, notification))

            # Se for uma subtarefa, precisamos atualizar o parent_task também
            if parent_task:
                # Certifique-se de que o parent tem a lista de children atualizada
                parent_task.children.append(task)
                # Force a atualização do parent
                parent_task.updated_at = utc3_now()
                # Atualize explicitamente o status has_children
                parent_task.has_children = True

            db.session.commit()

            # Verificar e recarregar tarefa do banco para garantir persistencia
            db.session.refresh(task)
            if parent_task:
                db.session.refresh(parent_task)

            # Log detalhado do salvamento
            current_app.logger.info(
                f"Task {task.id} salva com sucesso no banco de dados. "
                f"is_private={task.is_private}, tag_id={task.tag_id}, "
                f"tag_nome={task.tag.nome if task.tag else 'None'}, "
                f"created_by={task.created_by}, assigned_to={task.assigned_to}"
            )

            # Verificacao de integridade: garantir que is_private foi salvo corretamente
            if task.is_private != is_private:
                error_msg = (
                    f"ERRO CRITICO: is_private nao foi salvo corretamente! "
                    f"Esperado: {is_private}, Obtido do banco: {task.is_private}"
                )
                current_app.logger.error(error_msg)
                flash("Erro ao salvar configuracao 'Somente para mim'. Por favor, tente novamente.", "danger")
                db.session.rollback()
                return redirect(url_for("tasks.tasks_new"))

            # Broadcast task creation
            from app.services.realtime import broadcast_task_created, get_broadcaster
            task_data = _serialize_task(task)
            if not task.is_private:
                broadcast_task_created(task_data, exclude_user=current_user.id)

            if creation_notification_records:
                broadcaster = get_broadcaster()
                for user_id, notification in creation_notification_records:
                    broadcaster.broadcast(
                        event_type="notification:created",
                        data={
                            "id": notification.id,
                            "task_id": task.id,
                            "type": notification.type,
                            "message": notification.message,
                            "created_at": notification.created_at.isoformat()
                            if notification.created_at
                            else None,
                        },
                        user_id=user_id,
                        scope="notifications",
                    )

            flash("Tarefa criada com sucesso!", "success")
            current_app.logger.info(
                "Task criada com sucesso (ID: %s). return_url: %s, current_user.role: %s",
                task.id, return_url, current_user.role
            )

            # Redirecionar de volta para a pagina original quando apropriado
            if return_url and not task.is_private and return_url != request.url:
                current_app.logger.info("Redirecionando para return_url: %s com highlight", return_url)
                # Adicionar parâmetro highlight_task para destacar a tarefa criada
                separator = '&' if '?' in return_url else '?'
                return redirect(f"{return_url}{separator}highlight_task={task.id}")

            destination = "tasks.tasks_overview" if current_user.role == "admin" else "tasks.tasks_overview_mine"
            current_app.logger.info("Redirecionando para %s com highlight", destination)
            return redirect(url_for(destination, highlight_task=task.id))
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception("Erro ao criar tarefa", exc_info=exc)
            flash(f"Erro ao criar tarefa: {str(exc)}", "danger")
    else:
        # Debug: mostrar erros de validação quando o formulário não validar
        if request.method == "POST":
            current_app.logger.warning(
                "Formulário de tarefa não validou. Erros: %s", form.errors
            )
            for field, errors in form.errors.items():
                for error in errors:
                    flash(f"Erro no campo '{field}': {error}", "danger")

    # Determinar URL de cancelamento - priorizar return_url se fornecido
    if return_url:
        cancel_url = return_url
    elif parent_task:
        cancel_url = url_for("tasks.tasks_sector", tag_id=parent_task.tag_id)
    elif tag:
        cancel_url = url_for("tasks.tasks_sector", tag_id=tag.id)
    else:
        # Fallback: usar a página de origem ou home
        cancel_url = request.referrer or url_for("home")

    return render_template(
        "tasks_new.html",
        form=form,
        parent_task=parent_task,
        cancel_url=cancel_url,
        is_editing=False,
        editing_task=None,
        return_url=return_url,
    )


@tasks_bp.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
@login_required
def tasks_edit(task_id: int):
    """Edit an existing task."""
    return_url = request.args.get("return_url")
    if request.method == "POST" and not return_url:
        return_url = request.form.get("return_url") or None

    task = Task.query.get_or_404(task_id)

    if task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER and current_user.role != "admin":
        abort(404)

    is_admin = current_user.role == "admin"
    is_creator = task.created_by == current_user.id
    is_assignee = task.assigned_to == current_user.id if task.assigned_to else False
    follow_up_ids = list(_task_follow_up_user_ids(task))
    is_follow_up = current_user.id in follow_up_ids

    if task.is_private:
        if not _user_can_access_task(task, current_user):
            abort(403)
    else:
        if not (
            _can_user_access_tag(task.tag, current_user)
            or is_admin
            or is_creator
            or is_assignee
            or is_follow_up
        ):
            abort(403)

    if task.status not in {TaskStatus.PENDING, TaskStatus.IN_PROGRESS} and not is_admin:
        flash("Apenas tarefas pendentes ou em andamento podem ser editadas.", "warning")
        if return_url:
            return redirect(return_url)
        return redirect(url_for("tasks.tasks_view", task_id=task.id))

    # Criar form (Flask binda automaticamente ao request.form no POST)
    form = TaskForm()
    parent_task = task.parent

    if parent_task:
        form.parent_id.data = parent_task.id
        form.tag_id.choices = [(parent_task.tag_id, parent_task.tag.nome)]
        form.tag_id.data = parent_task.tag_id
        tag = parent_task.tag
    else:
        tags_query = (
            Tag.query.filter(~Tag.nome.in_(EXCLUDED_TASK_TAGS))
            .filter(~Tag.nome.like(f"{PERSONAL_TAG_PREFIX}%"))
            .order_by(Tag.nome)
        )
        tag_choices = [(t.id, t.nome) for t in tags_query.all()]
        tag_choices = _sort_choice_pairs(tag_choices)
        form.tag_id.choices = tag_choices
        if task.is_private and all(choice[0] != task.tag_id for choice in form.tag_id.choices):
            updated_choices = list(form.tag_id.choices) + [(task.tag_id, "Para Mim")]
            form.tag_id.choices = _sort_choice_pairs(updated_choices)

        selected_tag_id = form.tag_id.data if request.method == "POST" else task.tag_id
        if selected_tag_id is None:
            selected_tag_id = task.tag_id
        if request.method != "POST":
            form.tag_id.data = selected_tag_id
        tag = Tag.query.get(selected_tag_id) if selected_tag_id else None

    assignee_choices = _build_task_user_choices(tag)
    if task.assigned_to and all(choice[0] != task.assigned_to for choice in assignee_choices):
        assignee = User.query.get(task.assigned_to)
        if assignee:
            assignee_label = (assignee.name or assignee.username or "").strip()
            assignee_choices.append((assignee.id, assignee_label))
    form.assigned_to.choices = _sort_choice_pairs(assignee_choices, keep_first=True)
    form.follow_up_users.choices = _build_follow_up_user_choices()

    # Popular campos no GET com dados da task existente
    if request.method == "GET":
        form.task_id.data = task.id
        form.title.data = task.title
        form.description.data = task.description
        form.priority.data = task.priority.value if task.priority else "medium"
        form.due_date.data = task.due_date
        form.only_me.data = task.is_private
        form.assigned_to.data = task.assigned_to or (current_user.id if task.is_private else 0)
        form.follow_up_users.data = follow_up_ids
        form.follow_up.data = bool(follow_up_ids)

    if request.method == "POST":
        form.only_me.data = _is_only_me_selected(request.form.getlist("only_me"))
        current_app.logger.info(
            f"Task edit POST (task {task_id}) - only_me raw value: {request.form.get('only_me')}, "
            f"form.only_me.data: {form.only_me.data}, "
            f"tag_id: {form.tag_id.data}, assigned_to: {form.assigned_to.data}"
        )
    if request.method == "POST" and form.only_me.data:
        form.assigned_to.data = current_user.id
        current_app.logger.info(
            "Task edit - only_me checked, forcing self-assignment"
        )

    if form.validate_on_submit():
        follow_up_user_ids = _extract_follow_up_user_ids(form)
        tag_id = parent_task.tag_id if parent_task else form.tag_id.data
        if not parent_task and (tag is None or tag.id != tag_id):
            tag = Tag.query.get(tag_id)
        if tag is None:
            abort(400)
        assignee_id = form.assigned_to.data or None
        is_private = bool(form.only_me.data)
        current_app.logger.info(
            f"Task edit - is_private: {is_private}, tag_id: {tag_id}, "
            f"assignee_id: {assignee_id}, tag_name: {tag.nome if tag else 'None'}"
        )
        if is_private:
            assignee_id = current_user.id

        try:
            task.title = form.title.data
            task.description = form.description.data
            task.priority = TaskPriority(form.priority.data)
            task.due_date = form.due_date.data
            task.is_private = is_private
            task.tag_id = tag_id
            task.assigned_to = assignee_id

            uploaded_files = [
                storage
                for storage in (form.attachments.data or [])
                if storage and storage.filename
            ]

            for uploaded_file in uploaded_files:
                saved = _save_task_file(uploaded_file)
                db.session.add(
                    TaskAttachment(
                        task=task,
                        file_path=saved["path"],
                        original_name=saved["name"],
                        mime_type=saved["mime_type"],
                    )
                )

            _sync_task_followers(task, follow_up_user_ids)

            # Notificar sobre a edição da tarefa
            editor_name = current_user.name or current_user.username
            edit_message = f'{editor_name} editou a tarefa "{task.title}".'
            edit_now = utc3_now()
            edit_recipients = _get_task_notification_recipients(task, exclude_user_id=current_user.id)

            edit_notification_records: list[tuple[int, TaskNotification]] = []
            for user_id in edit_recipients:
                notification = TaskNotification(
                    user_id=user_id,
                    task_id=task.id,
                    type=NotificationType.TASK.value,
                    message=edit_message[:255],
                    created_at=edit_now,
                )
                db.session.add(notification)
                edit_notification_records.append((user_id, notification))

            db.session.commit()

            # Verificar e recarregar tarefa do banco para garantir persistencia
            db.session.refresh(task)

            # Log detalhado da edicao
            current_app.logger.info(
                f"Task {task.id} editada com sucesso no banco de dados. "
                f"is_private={task.is_private}, tag_id={task.tag_id}, "
                f"tag_nome={task.tag.nome if task.tag else 'None'}, "
                f"created_by={task.created_by}, assigned_to={task.assigned_to}"
            )

            # Verificacao de integridade: garantir que is_private foi salvo corretamente
            if task.is_private != is_private:
                error_msg = (
                    f"ERRO CRITICO: is_private nao foi atualizado corretamente na edicao! "
                    f"Esperado: {is_private}, Obtido do banco: {task.is_private}"
                )
                current_app.logger.error(error_msg)
                flash("Erro ao salvar configuracao 'Somente para mim'. Por favor, tente novamente.", "danger")
                db.session.rollback()
                return redirect(url_for("tasks.tasks_edit", task_id=task.id))

            # Broadcast task update para atualizar interface em tempo real
            if not task.is_private:
                from app.services.realtime import broadcast_task_updated
                task_data = _serialize_task(task)
                broadcast_task_updated(task_data, exclude_user=current_user.id)

            # Broadcast notificações em tempo real
            if edit_notification_records:
                from app.services.realtime import get_broadcaster
                broadcaster = get_broadcaster()

                for user_id, notification in edit_notification_records:
                    broadcaster.broadcast(
                        event_type="notification:created",
                        data={
                            "id": notification.id,
                            "task_id": task.id,
                            "type": notification.type,
                            "message": notification.message,
                            "created_at": notification.created_at.isoformat(),
                        },
                        user_id=user_id,
                        scope="notifications",
                    )

            flash("Tarefa atualizada com sucesso!", "success")

            # Redirecionar de volta para a pagina original quando apropriado
            if return_url and not task.is_private and return_url != request.url:
                current_app.logger.info("Redirecionando para return_url: %s com highlight", return_url)
                # Adicionar parâmetro highlight_task para destacar a tarefa editada
                separator = '&' if '?' in return_url else '?'
                return redirect(f"{return_url}{separator}highlight_task={task.id}")

            destination = "tasks.tasks_overview" if current_user.role == "admin" else "tasks.tasks_overview_mine"
            current_app.logger.info("Redirecionando para %s com highlight", destination)
            return redirect(url_for(destination, highlight_task=task.id))
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception("Erro ao atualizar tarefa", exc_info=exc)
            flash(f"Erro ao atualizar tarefa: {str(exc)}", "danger")
    else:
        if request.method == "POST":
            current_app.logger.warning(
                "Formulário de edição de tarefa não validou. Erros: %s", form.errors
            )
            for field, errors in form.errors.items():
                for error in errors:
                    flash(f"Erro no campo '{field}': {error}", "danger")

    # Determinar URL de cancelamento - priorizar return_url se fornecido
    if return_url:
        cancel_url = return_url
    elif parent_task:
        cancel_url = url_for("tasks.tasks_sector", tag_id=parent_task.tag_id)
    elif task.is_private and current_user.role != "admin":
        cancel_url = url_for("tasks.tasks_overview_mine")
    elif not task.is_private:
        cancel_url = url_for("tasks.tasks_sector", tag_id=task.tag_id)
    else:
        # Fallback para tasks privadas de admin
        cancel_url = request.referrer or url_for("tasks.tasks_overview")

    return render_template(
        "tasks_new.html",
        form=form,
        parent_task=parent_task,
        cancel_url=cancel_url,
        is_editing=True,
        editing_task=task,
        return_url=return_url,
    )


# ============================================================================
# ROUTES - TRANSFER
# ============================================================================

@tasks_bp.route("/tasks/<int:task_id>/transfer/options", methods=["GET"])
@login_required
def tasks_transfer_options(task_id: int):
    """Return available assignees for transferring a task."""

    task = Task.query.get_or_404(task_id)

    if task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER and current_user.role != "admin":
        abort(404)
    if task.is_private and not _user_can_access_task(task, current_user):
        abort(403)
    if not _user_can_transfer_task(task, current_user):
        abort(403)

    requested_tag_id = request.args.get("tag_id", type=int)
    is_admin = current_user.role == "admin"

    tag_entries: dict[int, Tag] = {}
    if is_admin:
        available_tags = (
            Tag.query.filter(~Tag.nome.in_(EXCLUDED_TASK_TAGS))
            .filter(~Tag.nome.like(f"{PERSONAL_TAG_PREFIX}%"))
            .order_by(Tag.nome)
            .all()
        )
        for tag in available_tags:
            tag_entries[tag.id] = tag
    else:
        accessible_ids = {tag_id for tag_id in _get_accessible_tag_ids(current_user) if tag_id}
        if task.tag_id:
            accessible_ids.add(task.tag_id)
        if accessible_ids:
            available_tags = (
                Tag.query.filter(Tag.id.in_(accessible_ids))
                .order_by(Tag.nome)
                .all()
            )
            for tag in available_tags:
                if tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
                    continue
                tag_entries[tag.id] = tag
    if task.tag and task.tag.id not in tag_entries:
        tag_entries[task.tag.id] = task.tag

    target_tag = task.tag
    if requested_tag_id:
        candidate_tag = Tag.query.get(requested_tag_id)
        if candidate_tag is None:
            abort(404)
        if candidate_tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER and not is_admin:
            abort(403)
        if not is_admin and not _can_user_access_tag(candidate_tag, current_user) and requested_tag_id != task.tag_id:
            abort(403)
        target_tag = candidate_tag
        tag_entries[candidate_tag.id] = candidate_tag

    tag_choices = [
        (
            tag_id,
            "Para Mim" if tag.nome.startswith(PERSONAL_TAG_PREFIX) else tag.nome,
        )
        for tag_id, tag in tag_entries.items()
        if tag is not None and (is_admin or tag.nome.lower() not in EXCLUDED_TASK_TAGS_LOWER)
    ]
    tag_choices = _sort_choice_pairs(tag_choices)

    tags_payload = [
        {"id": value, "label": label, "is_current": value == task.tag_id}
        for value, label in tag_choices
    ]

    choices = _build_task_user_choices(target_tag)
    options = [
        {"id": user_id, "label": label, "is_current": user_id == task.assigned_to}
        for user_id, label in choices
        if user_id
    ]

    payload = {
        "success": True,
        "options": options,
        "current_assignee": task.assigned_to,
        "assignee_name": task.assignee.name if task.assignee else None,
        "task_title": task.title,
        "tags": tags_payload,
        "current_tag_id": task.tag_id,
        "current_tag_name": task.tag.nome if task.tag else None,
        "selected_tag_id": target_tag.id if target_tag else None,
        "selected_tag_name": target_tag.nome if target_tag else None,
    }

    if not options:
        payload["success"] = False
        payload["message"] = "Nenhum colaborador disponível para receber esta tarefa."

    return jsonify(payload)


@tasks_bp.route("/tasks/<int:task_id>/transfer", methods=["POST"])
@login_required
def tasks_transfer(task_id: int):
    """Transfer a task to another collaborator."""

    task = Task.query.get_or_404(task_id)

    if task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER and current_user.role != "admin":
        abort(404)
    if task.is_private and not _user_can_access_task(task, current_user):
        abort(403)
    if not _user_can_transfer_task(task, current_user):
        abort(403)

    data = request.get_json(silent=True) or {}
    assignee_raw = data.get("assignee_id")
    tag_raw = data.get("tag_id")
    is_admin = current_user.role == "admin"

    try:
        assignee_id = int(assignee_raw)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Selecione um colaborador válido."}), 400

    if assignee_id <= 0:
        return jsonify({"success": False, "message": "Selecione um colaborador válido."}), 400

    target_tag = task.tag
    if tag_raw is not None:
        try:
            tag_id = int(tag_raw)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "Selecione um setor válido."}), 400
        if tag_id <= 0:
            return jsonify({"success": False, "message": "Selecione um setor válido."}), 400
        candidate_tag = Tag.query.get(tag_id)
        if candidate_tag is None:
            return jsonify({"success": False, "message": "Selecione um setor válido."}), 400
        if candidate_tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER and not is_admin:
            return jsonify({"success": False, "message": "Selecione um setor válido."}), 400
        if not is_admin and not _can_user_access_tag(candidate_tag, current_user) and candidate_tag.id != task.tag_id:
            return jsonify({"success": False, "message": "Você não tem permissão para transferir para este setor."}), 403
        target_tag = candidate_tag

    valid_assignees = {
        user_id for user_id, _ in _build_task_user_choices(target_tag) if user_id
    }
    if assignee_id not in valid_assignees:
        return (
            jsonify(
                {"success": False, "message": "Colaborador não disponível para este setor."}
            ),
            400,
        )

    tag_changed = target_tag and target_tag.id != task.tag_id
    if assignee_id == task.assigned_to and not tag_changed:
        return jsonify(
            {
                "success": True,
                "task": _serialize_task(task),
                "message": "A tarefa já estava atribuída a este colaborador.",
            }
        )

    new_assignee = User.query.get(assignee_id)
    if new_assignee is None or not getattr(new_assignee, "ativo", True):
        return jsonify({"success": False, "message": "Colaborador indisponível."}), 400

    if target_tag and target_tag.id != task.tag_id:
        task.tag_id = target_tag.id
        task.tag = target_tag
    task.assigned_to = assignee_id
    task.assignee = new_assignee

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Erro ao transferir tarefa", exc_info=exc)
        return (
            jsonify(
                {
                    "success": False,
                    "message": "Não foi possível transferir a tarefa. Tente novamente.",
                }
            ),
            500,
        )

    db.session.refresh(task)

    task_data = _serialize_task(task)

    if not task.is_private:
        from app.services.realtime import broadcast_task_updated

        broadcast_task_updated(task_data, exclude_user=current_user.id)

    current_app.logger.info(
        "Task %s transferida para o usuário %s por %s (setor %s)",
        task.id,
        assignee_id,
        current_user.id,
        target_tag.id if target_tag else task.tag_id,
    )

    return jsonify({"success": True, "task": task_data, "message": "Tarefa transferida com sucesso."})


# ============================================================================
# ROUTES - RESPONSES/CONVERSATION
# ============================================================================

@tasks_bp.route("/tasks/<int:task_id>/responses", methods=["GET"])
@login_required
def task_responses_list(task_id: int):
    """Return responses for the given task in JSON format."""

    task = (
        Task.query.options(joinedload(Task.tag), joinedload(Task.assignee), joinedload(Task.creator))
        .get_or_404(task_id)
    )
    if task.tag and task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    if task.is_private and not _user_can_access_task(task, current_user):
        abort(403)
    if (
        not task.is_private
        and not (
            _can_user_access_tag(task.tag, current_user)
            or _user_has_task_privileges(task, current_user)
        )
    ):
        abort(403)
    if not _user_can_access_task_conversation(task, current_user):
        abort(403)
    if task.status not in (TaskStatus.IN_PROGRESS, TaskStatus.DONE):
        return jsonify({"success": False, "error": "conversation_unavailable"}), 409

    meta = _build_task_conversation_meta(task, current_user)
    responses = meta.pop("responses")
    can_post = task.status in (TaskStatus.IN_PROGRESS, TaskStatus.DONE)
    return jsonify(
        {
            "success": True,
            "task": {
                "id": task.id,
                "title": task.title,
                "status": task.status.value,
                "tag": task.tag.nome if task.tag else None,
                "creator_id": task.created_by,
                "assignee_id": task.assigned_to,
            },
            "responses": responses,
            "meta": {
                **meta,
                "can_post": can_post and _user_can_access_task_conversation(task, current_user),
            },
        }
    )


@tasks_bp.route("/tasks/<int:task_id>/responses", methods=["POST"])
@login_required
def task_responses_create(task_id: int):
    """Create a new task response and notify participants."""

    task = (
        Task.query.options(joinedload(Task.tag), joinedload(Task.assignee), joinedload(Task.creator))
        .get_or_404(task_id)
    )
    if task.tag and task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    if task.is_private and not _user_can_access_task(task, current_user):
        abort(403)
    if (
        not task.is_private
        and not (
            _can_user_access_tag(task.tag, current_user)
            or _user_has_task_privileges(task, current_user)
        )
    ):
        abort(403)
    if not _user_can_access_task_conversation(task, current_user):
        abort(403)
    if task.status not in (TaskStatus.IN_PROGRESS, TaskStatus.DONE):
        return jsonify({"success": False, "error": "conversation_unavailable"}), 409

    payload = request.get_json(silent=True) or {}
    raw_body = (payload.get("body") or "").strip()
    if not raw_body:
        return jsonify({"success": False, "error": "empty_response"}), 400

    cleaned_body = sanitize_html(raw_body)
    if not cleaned_body.strip():
        return jsonify({"success": False, "error": "empty_response"}), 400

    created_at = utc3_now()
    response = TaskResponse(
        task_id=task.id,
        author_id=current_user.id,
        body=cleaned_body,
        created_at=created_at,
    )
    db.session.add(response)

    author_participant = _ensure_response_participant(task.id, current_user.id)
    author_participant.last_read_at = created_at

    recipients: set[int] = set()
    notification_records: list[tuple[int, TaskNotification]] = []
    now = utc3_now()
    sender_name = current_user.name or current_user.username
    body_preview = re.sub(r'<[^>]+>', '', cleaned_body).replace('\n', ' ').strip()
    if len(body_preview) > 90:
        body_preview = f"{body_preview[:87]}..."

    for participant_id in _task_conversation_participant_ids(task):
        participant = _ensure_response_participant(task.id, participant_id)
        if participant_id == current_user.id:
            participant.last_notified_at = now
            continue
        participant.last_notified_at = now
        recipients.add(participant_id)
        message = f'{sender_name} respondeu a tarefa "{task.title}".'
        notification = TaskNotification(
            user_id=participant_id,
            task_id=task.id,
            type=NotificationType.TASK_RESPONSE.value,
            message=message[:255],
            created_at=now,
        )
        db.session.add(notification)
        notification_records.append((participant_id, notification))

    db.session.flush()

    response_payload = _serialize_task_response(response, current_user.id)

    db.session.commit()

    from app.services.realtime import (
        broadcast_task_response_created,
        get_broadcaster,
    )

    if recipients:
        broadcast_task_response_created(
            task.id,
            response_payload,
            recipients=list(recipients),
            exclude_user=current_user.id,
        )

    broadcaster = get_broadcaster()
    for user_id, notification in notification_records:
        broadcaster.broadcast(
            event_type="notification:created",
            data={
                "id": notification.id,
                "task_id": task.id,
                "type": notification.type,
                "message": notification.message,
                "created_at": notification.created_at.isoformat()
                if notification.created_at
                else None,
            },
            user_id=user_id,
            scope="notifications",
        )

    refreshed_meta = _build_task_conversation_meta(task, current_user)

    return jsonify(
        {
            "success": True,
            "response": response_payload,
            "meta": {**refreshed_meta, "can_post": True},
        }
    )


@tasks_bp.route("/tasks/<int:task_id>/responses/read", methods=["POST"])
@login_required
def task_responses_mark_read(task_id: int):
    """Mark all responses as read for the current user."""

    task = (
        Task.query.options(joinedload(Task.tag), joinedload(Task.assignee), joinedload(Task.creator))
        .get_or_404(task_id)
    )
    if task.tag and task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    if task.is_private and not _user_can_access_task(task, current_user):
        abort(403)
    if (
        not task.is_private
        and not (
            _can_user_access_tag(task.tag, current_user)
            or _user_has_task_privileges(task, current_user)
        )
    ):
        abort(403)
    if not _user_can_access_task_conversation(task, current_user):
        abort(403)
    if task.status not in (TaskStatus.IN_PROGRESS, TaskStatus.DONE):
        return jsonify({"success": False, "error": "conversation_unavailable"}), 409

    participant = _ensure_response_participant(task.id, current_user.id)
    now = utc3_now()
    participant.last_read_at = now

    (
        TaskNotification.query.filter(
            TaskNotification.user_id == current_user.id,
            TaskNotification.task_id == task.id,
            TaskNotification.type == NotificationType.TASK_RESPONSE.value,
            TaskNotification.read_at.is_(None),
        ).update({"read_at": now}, synchronize_session=False)
    )

    db.session.commit()

    meta = _build_task_conversation_meta(task, current_user)
    return jsonify({"success": True, "meta": {**meta, "can_post": True}})


# ============================================================================
# ROUTES - VIEW & ACTIONS
# ============================================================================

@tasks_bp.route("/tasks/<int:task_id>")
@login_required
def tasks_view(task_id):
    """Display details of a completed task."""
    task = (
        Task.query.options(
            joinedload(Task.tag),
            joinedload(Task.assignee),
            joinedload(Task.finisher),
            joinedload(Task.parent),
            joinedload(Task.status_history),
            joinedload(Task.attachments),
        )
        .get_or_404(task_id)
    )
    if task.is_private:
        if not _user_can_access_task(task, current_user):
            abort(403)
        if task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER and current_user.role != "admin":
            abort(404)
    else:
        if task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
            abort(404)
        if not (
            _can_user_access_tag(task.tag, current_user)
            or _user_has_task_privileges(task, current_user)
        ):
            abort(403)
    priority_labels = {"low": "Baixa", "medium": "Média", "high": "Alta"}
    priority_order = ["low", "medium", "high"]

    # Determinar URL de retorno
    explicit_return_url = request.args.get("return_url")
    if explicit_return_url:
        cancel_url = explicit_return_url
    else:
        if task.is_private:
            cancel_url = (
                url_for("tasks.tasks_overview")
                if current_user.role == "admin"
                else url_for("tasks.tasks_overview_mine")
            )
        elif _can_user_access_tag(task.tag, current_user):
            cancel_url = url_for("tasks.tasks_history", tag_id=task.tag_id)
        else:
            cancel_url = url_for("tasks.tasks_history", assigned_by_me=1)

        # Usar referrer se disponível e seguro
        if (
            request.referrer
            and request.referrer != request.url
            and _is_safe_referrer(request.referrer)
        ):
            cancel_url = request.referrer

    # Buscar histórico de alterações da tarefa
    history_entries = (
        TaskHistory.query
        .filter_by(task_id=task_id)
        .order_by(TaskHistory.changed_at.desc())
        .all()
    )

    return render_template(
        "tasks_view.html",
        task=task,
        priority_labels=priority_labels,
        priority_order=priority_order,
        cancel_url=cancel_url,
        history_entries=history_entries,
    )

@tasks_bp.route("/tasks/<int:task_id>/status", methods=["POST"])
@login_required
def tasks_status(task_id):
    """Update a task status and record its history."""
    task = Task.query.get_or_404(task_id)
    current_app.logger.info(
        f"Updating task status - task_id: {task_id}, is_private: {task.is_private}, "
        f"created_by: {task.created_by}, current_user: {current_user.id}, "
        f"tag: {task.tag.nome}"
    )
    if task.is_private and not _user_can_access_task(task, current_user):
        current_app.logger.warning(f"Access denied: User {current_user.id} cannot modify private task {task_id}")
        abort(403)
    if task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    # For private tasks, access is already validated above
    if (
        not task.is_private
        and not (
            _can_user_access_tag(task.tag, current_user)
            or _user_has_task_privileges(task, current_user)
        )
    ):
        current_app.logger.warning(f"Access denied: User {current_user.id} cannot access tag {task.tag.nome}")
        abort(403)
    data = request.get_json() or {}
    status_value = data.get("status")
    try:
        new_status = TaskStatus(status_value)
    except Exception:
        abort(400)
    # Avoid unnecessary writes and undefined variables when status is unchanged
    if task.status == new_status:
        return jsonify({"success": True, "task": _serialize_task(task)})
    if current_user.role != "admin":
        allowed = {
            TaskStatus.PENDING: {TaskStatus.IN_PROGRESS},
            TaskStatus.IN_PROGRESS: {TaskStatus.DONE, TaskStatus.PENDING},
            TaskStatus.DONE: {TaskStatus.IN_PROGRESS},
        }
        if new_status not in allowed.get(task.status, set()):
            abort(403)
        # Only creator can reopen a completed task
        if task.status == TaskStatus.DONE and new_status == TaskStatus.IN_PROGRESS:
            if task.created_by != current_user.id:
                abort(403)
    if task.status != new_status:
        history = TaskStatusHistory(
            task_id=task.id,
            from_status=task.status,
            to_status=new_status,
            changed_by=current_user.id,
        )
        old_status = task.status
        task.status = new_status
        if new_status == TaskStatus.IN_PROGRESS:
            if old_status != TaskStatus.DONE or current_user.role != "admin":
                if task.assigned_to != current_user.id:
                    task._skip_assignment_notification = True
                task.assigned_to = current_user.id
            task.completed_by = None
            task.completed_at = None
        elif new_status == TaskStatus.DONE:
            task.completed_by = current_user.id
            task.completed_at = utc3_now()
        elif new_status == TaskStatus.PENDING:
            task.assigned_to = None
            task.completed_by = None
            task.completed_at = None
        else:
            task.completed_by = None
            task.completed_at = None

        status_notification_records: list[tuple[int, TaskNotification]] = []
        actor_name = current_user.name or current_user.username
        now = utc3_now()
        local_display = (
            now.replace(tzinfo=timezone.utc).astimezone(SAO_PAULO_TZ).strftime("%d/%m/%Y às %H:%M")
        )
        recipients: set[int] = set()
        status_message: str | None = None

        if new_status == TaskStatus.IN_PROGRESS:
            if old_status == TaskStatus.DONE:
                status_message = f'{actor_name} reabriu a tarefa "{task.title}".'
            else:
                status_message = f'{actor_name} iniciou a tarefa "{task.title}" às {local_display}.'
            # Aplicar regra: responsável OU setor
            recipients = _get_task_notification_recipients(task, exclude_user_id=current_user.id)
        elif new_status == TaskStatus.DONE:
            status_message = f'{actor_name} concluiu a tarefa "{task.title}" às {local_display}.'
            # Aplicar regra: responsável OU setor (além do criador)
            recipients = _get_task_notification_recipients(task, exclude_user_id=current_user.id)
            # Sempre incluir o criador
            if task.created_by and task.created_by != current_user.id:
                recipients.add(task.created_by)
        elif new_status == TaskStatus.PENDING and old_status == TaskStatus.IN_PROGRESS:
            status_message = f'{actor_name} moveu a tarefa "{task.title}" para pendente.'
            # Como assigned_to foi removido na linha 7715, agora notifica TODO O SETOR
            recipients = _get_task_notification_recipients(task, exclude_user_id=current_user.id)

        if status_message and recipients:
            for user_id in recipients:
                notification = TaskNotification(
                    user_id=user_id,
                    task_id=task.id,
                    type=NotificationType.TASK_STATUS.value,
                    message=status_message[:255],
                    created_at=now,
                )
                db.session.add(notification)
                status_notification_records.append((user_id, notification))

    db.session.add(history)
    if status_notification_records:
        db.session.flush()
    db.session.commit()

    # Broadcast status change
    from app.services.realtime import get_broadcaster, broadcast_task_status_changed
    task_data = _serialize_task(task)
    broadcaster = get_broadcaster()

    if task.is_private:
        # For private tasks, broadcast only to users with access.
        recipients = {task.created_by}
        if task.assigned_to:
            recipients.add(task.assigned_to)

        # Add followers
        followers = TaskFollower.query.filter_by(task_id=task.id).all()
        for follower in followers:
            recipients.add(follower.user_id)

        for user_id in recipients:
            # Don't send to the user who made the change
            if user_id == current_user.id:
                continue

            broadcaster.broadcast(
                event_type="task:status_changed",
                data={
                    "id": task.id,
                    "old_status": old_status.value,
                    "new_status": new_status.value,
                    "task": task_data,
                },
                user_id=user_id,
                scope="tasks",
            )
    else:
        # Public tasks are broadcast to everyone in the 'tasks' scope
        broadcast_task_status_changed(
            task.id,
            old_status.value,
            new_status.value,
            task_data,
            exclude_user=current_user.id,
        )
    if status_notification_records:
        broadcaster = get_broadcaster()
        for user_id, notification in status_notification_records:
            broadcaster.broadcast(
                event_type="notification:created",
                data={
                    "id": notification.id,
                    "task_id": task.id,
                    "type": notification.type,
                    "message": notification.message,
                    "created_at": notification.created_at.isoformat()
                    if notification.created_at
                    else None,
                },
                user_id=user_id,
                scope="notifications",
            )

    return jsonify({"success": True, "task": task_data})


@tasks_bp.route("/tasks/<int:task_id>/delete", methods=["POST"])
@login_required
def tasks_delete(task_id):
    """Remove a task from the system, including its subtasks and history."""

    task = Task.query.get_or_404(task_id)
    if task.is_private and not _user_can_access_task(task, current_user):
        abort(403)
    if task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER and current_user.role != "admin":
        abort(404)
    if current_user.role != "admin" and not _user_has_task_privileges(task, current_user):
        abort(403)

    # Store task ID before deletion for broadcasting
    deleted_task_id = task.id

    _delete_task_recursive(task)
    db.session.commit()

    # Broadcast task deletion
    from app.services.realtime import broadcast_task_deleted
    if not task.is_private:
        broadcast_task_deleted(deleted_task_id, exclude_user=current_user.id)

    return jsonify({"success": True})
