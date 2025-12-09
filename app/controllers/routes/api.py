"""Minimal JSON API for mobile/third-party clients."""

from __future__ import annotations

import os
from datetime import datetime
from functools import wraps
from typing import Iterable
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa
from flask import Blueprint, jsonify, request, g, current_app, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.exc import SQLAlchemyError

from app import csrf, db, limiter
from app.models.tables import (
    Announcement,
    AnnouncementAttachment,
    NotificationType,
    Tag,
    Task,
    TaskAttachment,
    TaskPriority,
    TaskResponse,
    TaskFollower,
    TaskStatusHistory,
    TaskStatus,
    TaskNotification,
    User,
    Departamento,
    Empresa,
    OperationalProcedure,
    NotaDebito,
    CadastroNota,
    NotaRecorrente,
    Course,
    CourseTag,
    AccessLink,
    Inclusao,
    DiretoriaEvent,
    DiretoriaAgreement,
    DiretoriaFeedback,
    ReportPermission,
)
from app.utils.permissions import is_user_admin
from app.services.meeting_room import fetch_raw_events, combine_events
from app.services.google_calendar import get_calendar_timezone
from app.services.general_calendar import serialize_events_for_calendar
from app.services.calendar_cache import calendar_cache

api_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")
csrf.exempt(api_bp)

TOKEN_TTL_SECONDS = int(os.getenv("API_TOKEN_TTL_SECONDS", "86400"))


def _token_serializer() -> URLSafeTimedSerializer:
    """Return a serializer bound to the current app secret."""

    secret = current_app.config["SECRET_KEY"]
    return URLSafeTimedSerializer(secret_key=secret, salt="mobile-api-token")


def _issue_token(user: User) -> str:
    """Create a signed bearer token for the given user."""

    payload = {"user_id": user.id, "ts": int(datetime.utcnow().timestamp())}
    return _token_serializer().dumps(payload)


def _get_token_from_header() -> str | None:
    """Extract the bearer token from Authorization header."""

    header = request.headers.get("Authorization", "")
    if not header:
        return None
    if header.lower().startswith("bearer "):
        return header.split(None, 1)[1].strip() or None
    return None


def _load_user_from_token(token: str) -> User | None:
    """Validate token and return the corresponding user."""

    data = _token_serializer().loads(token, max_age=TOKEN_TTL_SECONDS)
    user_id = data.get("user_id")
    if not user_id:
        return None
    return User.query.get(user_id)


def token_required(fn):
    """Decorator enforcing bearer-token authentication."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _get_token_from_header()
        if not token:
            return jsonify({"error": "missing_token"}), 401
        try:
            user = _load_user_from_token(token)
        except SignatureExpired:
            return jsonify({"error": "token_expired"}), 401
        except BadSignature:
            return jsonify({"error": "invalid_token"}), 401

        if not user or not user.ativo:
            return jsonify({"error": "user_not_found_or_inactive"}), 401

        g.api_user = user
        return fn(*args, **kwargs)

    return wrapper


def _serialize_user(user: User) -> dict:
    """Return a minimal user payload suitable for clients."""

    return {
        "id": user.id,
        "username": user.username,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "tags": [{"id": tag.id, "nome": tag.nome} for tag in user.tags],
    }


def _serialize_task(task: Task) -> dict:
    """Return a stable representation of a Task."""

    attachments = []
    for attachment in getattr(task, "attachments", []) or []:
        url = url_for("static", filename=attachment.file_path) if attachment.file_path else None
        attachments.append(
            {
                "id": attachment.id,
                "name": attachment.original_name or attachment.display_name,
                "mime_type": attachment.mime_type,
                "url": url,
            }
        )

    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status.value if task.status else None,
        "priority": task.priority.value if task.priority else None,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "tag": {"id": task.tag.id, "nome": task.tag.nome} if task.tag else None,
        "created_by": task.created_by,
        "assigned_to": task.assigned_to,
        "assignee_name": task.assignee.name if task.assignee else None,
        "attachments": attachments,
    }


def _serialize_announcement(announcement: Announcement) -> dict:
    """Return a lightweight announcement payload."""

    attachments = []
    for attachment in getattr(announcement, "attachments", []) or []:
        attachments.append(
            {
                "id": attachment.id,
                "name": attachment.attachment_name or attachment.file_path,
                "url": url_for("static", filename=attachment.attachment_path)
                if attachment.attachment_path
                else None,
                "mime_type": attachment.mime_type if hasattr(attachment, "mime_type") else None,
            }
        )

    return {
        "id": announcement.id,
        "date": announcement.date.isoformat() if announcement.date else None,
        "subject": announcement.subject,
        "content": announcement.content,
        "attachments": attachments,
        "created_at": announcement.created_at.isoformat() if announcement.created_at else None,
        "updated_at": announcement.updated_at.isoformat() if announcement.updated_at else None,
    }


def _parse_date(raw: str | None):
    """Return a date from YYYY-MM-DD string or raise ValueError."""

    if raw is None:
        return None
    return datetime.strptime(raw, "%Y-%m-%d").date()


def _parse_decimal(raw: object) -> Decimal:
    """Return Decimal from raw numeric or string."""

    if raw is None:
        raise InvalidOperation
    if isinstance(raw, (int, float, Decimal)):
        return Decimal(str(raw))
    if isinstance(raw, str):
        cleaned = raw.replace(",", ".").strip()
        return Decimal(cleaned)
    raise InvalidOperation


def _user_can_access_task(task: Task, user: User | None) -> bool:
    """Return True when ``user`` is allowed to access ``task``."""

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


def _user_has_tag(user: User, tag_name: str) -> bool:
    """Return True if the given user has a tag by name."""

    return any(tag.nome.lower() == tag_name.lower() for tag in user.tags)


def _serialize_notification(notification: TaskNotification) -> dict:
    """Return a minimal notification payload."""

    raw_type = notification.type or NotificationType.TASK.value
    try:
        notification_type = NotificationType(raw_type)
    except ValueError:
        notification_type = NotificationType.TASK

    created_at = notification.created_at or datetime.utcnow()
    created_iso = (
        created_at.isoformat()
        if created_at.tzinfo
        else created_at.replace(tzinfo=None).isoformat()
    )

    return {
        "id": notification.id,
        "type": notification_type.value,
        "message": notification.message,
        "task_id": notification.task_id,
        "announcement_id": notification.announcement_id,
        "created_at": created_iso,
        "is_read": bool(notification.read_at),
    }


def _serialize_status_history(entry: TaskStatusHistory) -> dict:
    """Return a representation of status transitions."""

    return {
        "id": entry.id,
        "task_id": entry.task_id,
        "from_status": entry.from_status.value if entry.from_status else None,
        "to_status": entry.to_status.value if entry.to_status else None,
        "changed_at": entry.changed_at.isoformat() if entry.changed_at else None,
        "changed_by": entry.changed_by,
    }


def _serialize_department(dept: Departamento) -> dict:
    """Return a lightweight department payload."""

    return {
        "id": dept.id,
        "empresa_id": dept.empresa_id,
        "tipo": dept.tipo,
        "responsavel": dept.responsavel,
        "descricao": dept.descricao,
        "formas_importacao": dept.formas_importacao,
        "forma_movimento": dept.forma_movimento,
        "envio_digital": dept.envio_digital,
        "envio_fisico": dept.envio_fisico,
        "malote_coleta": dept.malote_coleta,
        "observacao_movimento": dept.observacao_movimento,
        "observacao_importacao": dept.observacao_importacao,
        "observacao_contato": dept.observacao_contato,
        "updated_at": dept.updated_at.isoformat() if dept.updated_at else None,
    }


def _serialize_empresa(empresa: Empresa, include_departments: bool = False) -> dict:
    """Return a concise company payload."""

    payload = {
        "id": empresa.id,
        "nome": empresa.nome_empresa,
        "cnpj": empresa.cnpj,
        "atividade_principal": empresa.atividade_principal,
        "data_abertura": empresa.data_abertura.isoformat() if empresa.data_abertura else None,
        "socio_administrador": empresa.socio_administrador,
        "tributacao": empresa.tributacao,
        "regime_lancamento": empresa.regime_lancamento,
        "sistemas_consultorias": empresa.sistemas_consultorias,
        "sistema_utilizado": empresa.sistema_utilizado,
        "acessos": empresa.acessos,
        "observacao_acessos": empresa.observacao_acessos,
        "codigo_empresa": empresa.codigo_empresa,
        "contatos": empresa.contatos,
        "ativo": empresa.ativo,
    }
    if include_departments:
        payload["departamentos"] = [
            _serialize_department(dept) for dept in getattr(empresa, "departamentos", []) or []
        ]
    return payload


def _serialize_procedure(proc: OperationalProcedure) -> dict:
    """Return a minimal procedure payload."""

    return {
        "id": proc.id,
        "title": proc.title,
        "descricao": proc.descricao,
        "created_by": proc.created_by_id,
        "created_at": proc.created_at.isoformat() if proc.created_at else None,
        "updated_at": proc.updated_at.isoformat() if proc.updated_at else None,
    }


def _serialize_nota_debito(nota: NotaDebito) -> dict:
    """Return debit note payload."""

    return {
        "id": nota.id,
        "data_emissao": nota.data_emissao.isoformat() if nota.data_emissao else None,
        "empresa": nota.empresa,
        "notas": nota.notas,
        "qtde_itens": nota.qtde_itens,
        "valor_un": float(nota.valor_un) if nota.valor_un is not None else None,
        "total": float(nota.total) if nota.total is not None else None,
        "acordo": nota.acordo,
        "forma_pagamento": nota.forma_pagamento,
        "observacao": nota.observacao,
        "created_at": nota.created_at.isoformat() if nota.created_at else None,
    }


def _serialize_cadastro_nota(entry: CadastroNota) -> dict:
    """Return cadastro nota payload."""

    return {
        "id": entry.id,
        "pix": entry.pix,
        "cadastro": entry.cadastro,
        "valor": float(entry.valor) if entry.valor is not None else None,
        "acordo": entry.acordo,
        "forma_pagamento": entry.forma_pagamento,
        "usuario": entry.usuario,
        "senha": entry.senha,
        "ativo": entry.ativo,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


def _serialize_nota_recorrente(nota: NotaRecorrente) -> dict:
    """Return recurring note payload."""

    return {
        "id": nota.id,
        "empresa": nota.empresa,
        "descricao": nota.descricao,
        "valor": float(nota.valor) if nota.valor is not None else None,
        "periodo_inicio": nota.periodo_inicio,
        "periodo_fim": nota.periodo_fim,
        "dia_emissao": nota.dia_emissao,
        "forma_pagamento": getattr(nota, "forma_pagamento", None),
        "observacao": nota.observacao,
        "created_at": nota.created_at.isoformat() if nota.created_at else None,
    }


def _serialize_course(course: Course) -> dict:
    """Return course payload."""

    return {
        "id": course.id,
        "name": course.name,
        "instructor": course.instructor,
        "sectors": course.sectors,
        "participants": course.participants,
        "workload": course.workload.isoformat() if hasattr(course.workload, "isoformat") else str(course.workload),
        "start_date": course.start_date.isoformat() if course.start_date else None,
        "schedule_start": course.schedule_start.isoformat() if hasattr(course.schedule_start, "isoformat") else str(course.schedule_start),
        "schedule_end": course.schedule_end.isoformat() if hasattr(course.schedule_end, "isoformat") else str(course.schedule_end),
        "completion_date": course.completion_date.isoformat() if course.completion_date else None,
        "status": course.status,
        "observation": course.observation,
        "tags": [{"id": t.id, "name": t.name} for t in (course.tags or [])],
    }


def _serialize_access_link(link: AccessLink) -> dict:
    """Return access link payload."""

    return {
        "id": link.id,
        "category": link.category,
        "label": link.label,
        "url": link.url,
        "description": link.description,
        "created_by_id": link.created_by_id,
        "created_at": link.created_at.isoformat() if link.created_at else None,
        "updated_at": link.updated_at.isoformat() if link.updated_at else None,
    }


def _serialize_faq(entry: Inclusao) -> dict:
    """Return FAQ/inclusao payload."""

    return {
        "id": entry.id,
        "data": entry.data.isoformat() if entry.data else None,
        "usuario": entry.usuario,
        "setor": entry.setor,
        "consultoria": entry.consultoria,
        "assunto": entry.assunto,
        "pergunta": entry.pergunta,
        "resposta": entry.resposta,
    }


def _serialize_diretoria_event(event: DiretoriaEvent) -> dict:
    """Return Diretoria event payload."""

    return {
        "id": event.id,
        "name": event.name,
        "event_type": event.event_type,
        "event_date": event.event_date.isoformat() if event.event_date else None,
        "description": event.description,
        "audience": event.audience,
        "participants": event.participants,
        "services": event.services,
        "total_cost": float(event.total_cost) if event.total_cost is not None else None,
        "photos": event.photos,
        "created_by_id": event.created_by_id,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "updated_at": event.updated_at.isoformat() if event.updated_at else None,
    }


def _serialize_diretoria_agreement(agreement: DiretoriaAgreement) -> dict:
    """Return Diretoria agreement payload."""

    return {
        "id": agreement.id,
        "user_id": agreement.user_id,
        "title": agreement.title,
        "agreement_date": agreement.agreement_date.isoformat() if agreement.agreement_date else None,
        "notes": agreement.notes,
        "status": agreement.status,
        "created_at": agreement.created_at.isoformat() if agreement.created_at else None,
        "updated_at": agreement.updated_at.isoformat() if agreement.updated_at else None,
    }


def _serialize_diretoria_feedback(feedback: DiretoriaFeedback) -> dict:
    """Return Diretoria feedback payload."""

    return {
        "id": feedback.id,
        "user_id": feedback.user_id,
        "feedback_type": feedback.feedback_type,
        "content": feedback.content,
        "created_at": feedback.created_at.isoformat() if feedback.created_at else None,
    }


def _serialize_report_permission(entry: ReportPermission) -> dict:
    """Return report permission payload."""

    return {
        "id": entry.id,
        "report_code": entry.report_code,
        "tag_id": entry.tag_id,
        "user_id": entry.user_id,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
    }


def _serialize_comment(comment: TaskResponse) -> dict:
    """Return a lightweight representation of a task comment."""

    return {
        "id": comment.id,
        "task_id": comment.task_id,
        "author_id": comment.author_id,
        "author_name": comment.author.name if comment.author else None,
        "body": comment.body,
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
    }


def _save_upload(file_storage, upload_dir: str) -> dict[str, str | None]:
    """Persist upload and return metadata."""

    from uuid import uuid4
    from werkzeug.utils import secure_filename
    from mimetypes import guess_type

    original_name = secure_filename(file_storage.filename or "")
    extension = os.path.splitext(original_name)[1].lower()
    unique_name = f"{uuid4().hex}{extension}"
    os.makedirs(upload_dir, exist_ok=True)

    stored_path = os.path.join(upload_dir, unique_name)
    file_storage.save(stored_path)

    static_root = os.path.join(current_app.root_path, "static")
    relative_path = os.path.relpath(stored_path, static_root).replace("\\", "/")
    mime_type = file_storage.mimetype or guess_type(original_name)[0]

    return {
        "path": relative_path,
        "name": original_name or None,
        "mime_type": mime_type,
    }


def _save_attachment_from_request(file_field: str, subdir: str) -> dict[str, str | None] | None:
    """Save an uploaded file from multipart request into static/uploads/<subdir>."""

    storage = request.files.get(file_field)
    if not storage or not storage.filename:
        return None

    upload_dir = os.path.join(current_app.root_path, "static", "uploads", subdir)
    return _save_upload(storage, upload_dir)


@api_bp.route("/auth/login", methods=["POST"])
@csrf.exempt
@limiter.limit("10 per minute")
def api_login():
    """Authenticate user and return bearer token."""

    payload = request.get_json(silent=True) or {}
    identifier = (payload.get("username") or payload.get("email") or "").strip()
    password = payload.get("password") or ""

    if not identifier or not password:
        return jsonify({"error": "username_or_email_and_password_required"}), 400

    user = User.query.filter(
        sa.or_(User.username == identifier, User.email == identifier)
    ).first()

    if not user or not user.check_password(password):
        return jsonify({"error": "invalid_credentials"}), 401

    if not user.ativo:
        return jsonify({"error": "inactive_user"}), 403

    token = _issue_token(user)
    return jsonify(
        {
            "token": token,
            "token_type": "bearer",
            "expires_in": TOKEN_TTL_SECONDS,
            "user": _serialize_user(user),
        }
    )


@api_bp.route("/auth/refresh", methods=["POST"])
@token_required
def api_refresh():
    """Re-issue a token for the authenticated user."""

    token = _issue_token(g.api_user)
    return jsonify(
        {
            "token": token,
            "token_type": "bearer",
            "expires_in": TOKEN_TTL_SECONDS,
            "user": _serialize_user(g.api_user),
        }
    )


@api_bp.route("/me", methods=["GET"])
@token_required
def api_me():
    """Return authenticated user data."""

    return jsonify(_serialize_user(g.api_user))


@api_bp.route("/tags", methods=["GET"])
@token_required
def api_tags():
    """List tags available to the user."""

    query = Tag.query
    if not is_user_admin(g.api_user):
        user_tag_ids = [tag.id for tag in g.api_user.tags]
        query = query.filter(Tag.id.in_(user_tag_ids) if user_tag_ids else sa.text("0=1"))
    tags = query.order_by(Tag.nome.asc()).all()
    return jsonify([{"id": tag.id, "nome": tag.nome} for tag in tags])


@api_bp.route("/users", methods=["GET"])
@token_required
def api_users():
    """List users for assignment (restricted to shared tags for non-admins)."""

    query = User.query.filter(User.ativo.is_(True))
    if not is_user_admin(g.api_user):
        user_tag_ids = [tag.id for tag in g.api_user.tags]
        if not user_tag_ids:
            return jsonify([])
        query = query.join(User.tags).filter(Tag.id.in_(user_tag_ids))
    users = query.order_by(User.name.asc()).limit(200).all()
    return jsonify(
        [
            {
                "id": user.id,
                "name": user.name,
                "username": user.username,
                "email": user.email,
            }
            for user in users
        ]
    )


@api_bp.route("/tasks", methods=["GET"])
@token_required
def api_list_tasks():
    """List tasks visible to the authenticated user."""

    status_filter = request.args.get("status")
    query = Task.query

    if status_filter:
        try:
            status_enum = TaskStatus(status_filter)
        except ValueError:
            return jsonify({"error": "invalid_status"}), 400
        query = query.filter(Task.status == status_enum)

    if not is_user_admin(g.api_user):
        query = query.filter(
            sa.or_(Task.created_by == g.api_user.id, Task.assigned_to == g.api_user.id)
        )

    tasks = query.order_by(Task.created_at.desc()).limit(200).all()
    return jsonify([_serialize_task(task) for task in tasks])


@api_bp.route("/tasks/<int:task_id>", methods=["GET"])
@token_required
def api_get_task(task_id: int):
    """Return a single task if the user has access."""

    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404

    if not is_user_admin(g.api_user) and task.created_by != g.api_user.id and task.assigned_to != g.api_user.id:
        return jsonify({"error": "forbidden"}), 403

    return jsonify(_serialize_task(task))


@api_bp.route("/tasks", methods=["POST"])
@token_required
def api_create_task():
    """Create a new task assigned to a tag and optionally a user."""

    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()
    description = payload.get("description")
    tag_id = payload.get("tag_id")
    assignee_id = payload.get("assigned_to")
    priority_raw = payload.get("priority") or TaskPriority.MEDIUM.value
    due_date_raw = payload.get("due_date")

    if not title or not tag_id:
        return jsonify({"error": "title_and_tag_id_required"}), 400

    tag = Tag.query.get(tag_id)
    if not tag:
        return jsonify({"error": "tag_not_found"}), 404

    if not is_user_admin(g.api_user):
        allowed_tag_ids = {t.id for t in g.api_user.tags}
        if tag.id not in allowed_tag_ids:
            return jsonify({"error": "forbidden_for_tag"}), 403

    assignee = None
    if assignee_id:
        assignee = User.query.get(assignee_id)
        if not assignee:
            return jsonify({"error": "assignee_not_found"}), 404

    try:
        priority = TaskPriority(priority_raw)
    except ValueError:
        return jsonify({"error": "invalid_priority"}), 400

    try:
        due_date = _parse_date(due_date_raw) if due_date_raw else None
    except ValueError:
        return jsonify({"error": "invalid_due_date"}), 400

    try:
        task = Task(
            title=title,
            description=description,
            tag_id=tag.id,
            created_by=g.api_user.id,
            assigned_to=assignee.id if assignee else None,
            priority=priority,
            status=TaskStatus.PENDING,
            due_date=due_date,
        )
        db.session.add(task)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to create task via API")
        return jsonify({"error": "failed_to_create_task"}), 500

    return jsonify(_serialize_task(task)), 201


@api_bp.route("/tasks/<int:task_id>", methods=["PATCH"])
@token_required
def api_update_task(task_id: int):
    """Update mutable task fields (status, priority, title, description, due_date)."""

    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404

    if not is_user_admin(g.api_user) and task.created_by != g.api_user.id and task.assigned_to != g.api_user.id:
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}

    if "title" in payload:
        title = (payload.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title_cannot_be_empty"}), 400
        task.title = title

    if "description" in payload:
        task.description = payload.get("description")

    if "status" in payload:
        try:
            task.status = TaskStatus(payload.get("status"))
        except ValueError:
            return jsonify({"error": "invalid_status"}), 400

    if "priority" in payload:
        try:
            task.priority = TaskPriority(payload.get("priority"))
        except ValueError:
            return jsonify({"error": "invalid_priority"}), 400

    if "due_date" in payload:
        raw_date = payload.get("due_date")
        try:
            task.due_date = _parse_date(raw_date) if raw_date else None
        except ValueError:
            return jsonify({"error": "invalid_due_date"}), 400

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to update task via API")
        return jsonify({"error": "failed_to_update_task"}), 500

    return jsonify(_serialize_task(task))


@api_bp.route("/tasks/<int:task_id>", methods=["DELETE"])
@token_required
def api_delete_task(task_id: int):
    """Delete a task (admin or owner/assignee)."""

    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404

    if not is_user_admin(g.api_user) and task.created_by != g.api_user.id and task.assigned_to != g.api_user.id:
        return jsonify({"error": "forbidden"}), 403

    try:
        Task.query.filter(Task.id == task_id).delete(synchronize_session=False)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to delete task via API")
        return jsonify({"error": "failed_to_delete_task"}), 500

    return ("", 204)


@api_bp.route("/tasks/<int:task_id>/status", methods=["POST"])
@token_required
def api_update_task_status(task_id: int):
    """Update only the task status."""

    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404
    if not _user_can_access_task(task, g.api_user):
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    try:
        task.status = TaskStatus(payload.get("status"))
    except Exception:
        return jsonify({"error": "invalid_status"}), 400

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to update task status via API")
        return jsonify({"error": "failed_to_update_task"}), 500

    return jsonify(_serialize_task(task))


@api_bp.route("/tasks/<int:task_id>/followers", methods=["GET"])
@token_required
def api_list_task_followers(task_id: int):
    """Return followers of a task."""

    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404
    if not _user_can_access_task(task, g.api_user):
        return jsonify({"error": "forbidden"}), 403

    followers = (
        TaskFollower.query.filter_by(task_id=task.id)
        .join(User, TaskFollower.user_id == User.id)
        .with_entities(TaskFollower.user_id, User.name, User.username)
        .all()
    )
    return jsonify(
        [
            {"user_id": user_id, "name": name, "username": username}
            for user_id, name, username in followers
        ]
    )


@api_bp.route("/tasks/<int:task_id>/followers", methods=["POST"])
@token_required
def api_add_task_follower(task_id: int):
    """Add a follower to a task."""

    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404
    if not _user_can_access_task(task, g.api_user):
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    user_id = payload.get("user_id")
    if not user_id:
        return jsonify({"error": "user_id_required"}), 400

    user = User.query.get(user_id)
    if not user or not user.ativo:
        return jsonify({"error": "user_not_found"}), 404

    try:
        existing = TaskFollower.query.filter_by(task_id=task.id, user_id=user.id).first()
        if existing:
            return jsonify({"status": "already_following"}), 200
        follower = TaskFollower(task_id=task.id, user_id=user.id)
        db.session.add(follower)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to add follower via API")
        return jsonify({"error": "failed_to_add_follower"}), 500

    return jsonify({"status": "added", "user_id": user.id})


@api_bp.route("/tasks/<int:task_id>/followers/<int:user_id>", methods=["DELETE"])
@token_required
def api_remove_task_follower(task_id: int, user_id: int):
    """Remove a follower from a task."""

    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404
    if not _user_can_access_task(task, g.api_user):
        return jsonify({"error": "forbidden"}), 403

    try:
        deleted = (
            TaskFollower.query.filter_by(task_id=task.id, user_id=user_id)
            .delete(synchronize_session=False)
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to remove follower via API")
        return jsonify({"error": "failed_to_remove_follower"}), 500

    return jsonify({"deleted": deleted})


@api_bp.route("/tasks/<int:task_id>/history", methods=["GET"])
@token_required
def api_task_history(task_id: int):
    """Return status history for a task."""

    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404
    if not _user_can_access_task(task, g.api_user):
        return jsonify({"error": "forbidden"}), 403

    history = (
        TaskStatusHistory.query.filter_by(task_id=task.id)
        .order_by(TaskStatusHistory.changed_at.asc())
        .all()
    )
    return jsonify([_serialize_status_history(h) for h in history])


@api_bp.route("/tasks/<int:task_id>/comments", methods=["GET"])
@token_required
def api_list_task_comments(task_id: int):
    """Return comments for a task."""

    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404
    if not _user_can_access_task(task, g.api_user):
        return jsonify({"error": "forbidden"}), 403

    comments = (
        TaskResponse.query.filter_by(task_id=task.id)
        .order_by(TaskResponse.created_at.asc())
        .all()
    )
    return jsonify([_serialize_comment(comment) for comment in comments])


@api_bp.route("/tasks/<int:task_id>/comments", methods=["POST"])
@token_required
def api_create_task_comment(task_id: int):
    """Add a new comment to a task."""

    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404
    if not _user_can_access_task(task, g.api_user):
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    body = (payload.get("body") or "").strip()
    if not body:
        return jsonify({"error": "body_required"}), 400

    try:
        comment = TaskResponse(task_id=task.id, author_id=g.api_user.id, body=body)
        db.session.add(comment)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to add task comment via API")
        return jsonify({"error": "failed_to_create_comment"}), 500

    return jsonify(_serialize_comment(comment)), 201


@api_bp.route("/tasks/<int:task_id>/attachments", methods=["POST"])
@token_required
def api_upload_task_attachment(task_id: int):
    """Upload a file and attach to a task."""

    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "not_found"}), 404
    if not _user_can_access_task(task, g.api_user):
        return jsonify({"error": "forbidden"}), 403

    file_storage = request.files.get("file")
    if not file_storage or not file_storage.filename:
        return jsonify({"error": "file_required"}), 400

    upload_dir = os.path.join(current_app.root_path, "static", "uploads", "tasks")
    try:
        saved = _save_upload(file_storage, upload_dir)
        attachment = TaskAttachment(
            task_id=task.id,
            file_path=saved["path"],
            original_name=saved["name"],
            mime_type=saved["mime_type"],
        )
        db.session.add(attachment)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to upload attachment via API")
        return jsonify({"error": "failed_to_upload"}), 500

    return jsonify(
        {
            "id": attachment.id,
            "name": attachment.original_name or attachment.display_name,
            "mime_type": attachment.mime_type,
            "url": url_for("static", filename=attachment.file_path),
        }
    ), 201


@api_bp.route("/notifications", methods=["GET"])
@token_required
def api_notifications():
    """List recent notifications for the authenticated user."""

    limit = request.args.get("limit", default=50, type=int) or 50
    limit = min(max(limit, 1), 200)

    notifications = (
        TaskNotification.query.filter(TaskNotification.user_id == g.api_user.id)
        .order_by(TaskNotification.created_at.desc())
        .limit(limit)
        .all()
    )
    unread = (
        TaskNotification.query.filter(
            TaskNotification.user_id == g.api_user.id,
            TaskNotification.read_at.is_(None),
        ).count()
    )
    return jsonify(
        {
            "notifications": [_serialize_notification(n) for n in notifications],
            "unread": unread,
        }
    )


@api_bp.route("/notifications/read", methods=["POST"])
@token_required
def api_notifications_mark_read():
    """Mark a single notification or all as read."""

    payload = request.get_json(silent=True) or {}
    notification_id = payload.get("notification_id")
    now = datetime.utcnow()

    try:
        query = TaskNotification.query.filter(TaskNotification.user_id == g.api_user.id)
        if notification_id:
            query = query.filter(TaskNotification.id == notification_id)
        updated = query.update({TaskNotification.read_at: now}, synchronize_session=False)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to mark notifications as read via API")
        return jsonify({"error": "failed_to_mark_read"}), 500

    return jsonify({"updated": updated})


@api_bp.route("/announcements", methods=["GET"])
@token_required
def api_announcements():
    """List announcements."""

    limit = request.args.get("limit", default=20, type=int) or 20
    limit = min(max(limit, 1), 100)

    query = Announcement.query.order_by(Announcement.date.desc(), Announcement.id.desc())
    announcements = query.limit(limit).all()
    return jsonify([_serialize_announcement(a) for a in announcements])


@api_bp.route("/announcements", methods=["POST"])
@token_required
def api_announcement_create():
    """Create an announcement (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    subject = (payload.get("subject") or "").strip()
    content = (payload.get("content") or "").strip()
    date_raw = (payload.get("date") or "").strip()
    if not subject or not content or not date_raw:
        return jsonify({"error": "subject_content_date_required"}), 400
    try:
        parsed_date = _parse_date(date_raw)
    except ValueError:
        return jsonify({"error": "invalid_date"}), 400

    try:
        announcement = Announcement(
            subject=subject,
            content=content,
            date=parsed_date,
            created_by_id=g.api_user.id,
        )
        attachment_saved = _save_attachment_from_request("attachment", "announcements")
        if attachment_saved:
            db.session.flush()
            db.session.add(
                AnnouncementAttachment(
                    announcement=announcement,
                    file_path=attachment_saved["path"],
                    original_name=attachment_saved["name"],
                    mime_type=attachment_saved["mime_type"],
                )
            )
        db.session.add(announcement)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to create announcement via API")
        return jsonify({"error": "failed_to_create"}), 500

    return jsonify(_serialize_announcement(announcement)), 201


@api_bp.route("/announcements/<int:announcement_id>", methods=["GET"])
@token_required
def api_announcement_detail(announcement_id: int):
    """Return a single announcement."""

    announcement = Announcement.query.get(announcement_id)
    if not announcement:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_serialize_announcement(announcement))


@api_bp.route("/announcements/<int:announcement_id>", methods=["PATCH"])
@token_required
def api_announcement_update(announcement_id: int):
    """Update an announcement (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    announcement = Announcement.query.get(announcement_id)
    if not announcement:
        return jsonify({"error": "not_found"}), 404

    payload = request.get_json(silent=True) or {}
    if "subject" in payload:
        announcement.subject = (payload.get("subject") or "").strip() or announcement.subject
    if "content" in payload:
        announcement.content = (payload.get("content") or "").strip() or announcement.content
    if "date" in payload:
        raw = (payload.get("date") or "").strip()
        if raw:
            try:
                announcement.date = _parse_date(raw)
            except ValueError:
                return jsonify({"error": "invalid_date"}), 400
    attachment_saved = _save_attachment_from_request("attachment", "announcements")
    if attachment_saved:
        db.session.add(
            AnnouncementAttachment(
                announcement_id=announcement.id,
                file_path=attachment_saved["path"],
                original_name=attachment_saved["name"],
                mime_type=attachment_saved["mime_type"],
            )
        )

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to update announcement via API")
        return jsonify({"error": "failed_to_update"}), 500

    return jsonify(_serialize_announcement(announcement))


@api_bp.route("/announcements/<int:announcement_id>", methods=["DELETE"])
@token_required
def api_announcement_delete(announcement_id: int):
    """Delete an announcement (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    try:
        deleted = Announcement.query.filter(Announcement.id == announcement_id).delete(
            synchronize_session=False
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to delete announcement via API")
        return jsonify({"error": "failed_to_delete"}), 500

    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return ("", 204)


@api_bp.route("/announcements/<int:announcement_id>/read", methods=["POST"])
@token_required
def api_announcement_mark_read(announcement_id: int):
    """Mark announcement as read for current user."""

    now = datetime.utcnow()
    try:
        updated = (
            TaskNotification.query.filter(
                TaskNotification.user_id == g.api_user.id,
                TaskNotification.announcement_id == announcement_id,
            ).update({TaskNotification.read_at: now}, synchronize_session=False)
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to mark announcement as read via API")
        return jsonify({"error": "failed_to_mark_read"}), 500

    return jsonify({"updated": updated})


@api_bp.route("/reunioes", methods=["GET"])
@token_required
def api_reunioes_json():
    """Return meetings with up-to-date status as JSON (token-based)."""

    raw_events = []
    fallback = None
    try:
        raw_events = fetch_raw_events()
    except Exception:
        current_app.logger.exception("Google Calendar fetch failed; using cached data fallback")
        raw_events = calendar_cache.get("raw_calendar_events")
        if raw_events is not None:
            fallback = "primary-cache"
        else:
            raw_events = calendar_cache.get("raw_calendar_events_stale")
            if raw_events is not None:
                fallback = "stale-cache"
            else:
                raw_events = []
    calendar_tz = get_calendar_timezone()
    now = datetime.now(calendar_tz)
    events = combine_events(raw_events, now, g.api_user.id, is_user_admin(g.api_user))
    response = jsonify(events)
    if fallback:
        response.headers["X-Calendar-Fallback"] = fallback
    return response


@api_bp.route("/calendario-eventos", methods=["GET"])
@token_required
def api_general_calendar_events():
    """Return collaborator calendar events as JSON."""

    can_manage = (
        is_user_admin(g.api_user) or _user_has_tag(g.api_user, "Gestão") or _user_has_tag(g.api_user, "Coord.")
    )
    events = serialize_events_for_calendar(
        g.api_user.id, can_manage, is_user_admin(g.api_user)
    )
    return jsonify(events)


# ---------------------- Empresas / Departamentos ----------------------


@api_bp.route("/empresas", methods=["GET"])
@token_required
def api_empresas():
    """List companies (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    q = (request.args.get("q") or "").strip()
    limit = request.args.get("limit", type=int, default=100) or 100
    limit = min(max(limit, 1), 300)

    query = Empresa.query
    if q:
        like = f"%{q}%"
        query = query.filter(sa.or_(Empresa.nome_empresa.ilike(like), Empresa.cnpj.ilike(like)))

    empresas = query.order_by(Empresa.nome_empresa.asc()).limit(limit).all()
    return jsonify([_serialize_empresa(e, include_departments=False) for e in empresas])


@api_bp.route("/empresas/<int:empresa_id>", methods=["GET"])
@token_required
def api_empresa_detail(empresa_id: int):
    """Return company detail with departments (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    empresa = Empresa.query.get(empresa_id)
    if not empresa:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_serialize_empresa(empresa, include_departments=True))


@api_bp.route("/empresas/<int:empresa_id>/departamentos", methods=["GET"])
@token_required
def api_empresa_departments(empresa_id: int):
    """List departments of a company (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    empresa = Empresa.query.get(empresa_id)
    if not empresa:
        return jsonify({"error": "not_found"}), 404
    return jsonify([_serialize_department(dept) for dept in empresa.departamentos])


@api_bp.route("/empresas", methods=["POST"])
@token_required
def api_create_empresa():
    """Create a company (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    nome = (payload.get("nome") or payload.get("nome_empresa") or "").strip()
    cnpj = (payload.get("cnpj") or "").strip()
    data_abertura_raw = (payload.get("data_abertura") or "").strip()
    codigo_empresa = (payload.get("codigo_empresa") or "").strip()
    if not nome or not cnpj or not data_abertura_raw or not codigo_empresa:
        return jsonify({"error": "nome_cnpj_data_abertura_codigo_required"}), 400
    try:
        data_abertura = _parse_date(data_abertura_raw)
    except ValueError:
        return jsonify({"error": "invalid_data_abertura"}), 400

    try:
        empresa = Empresa(
            nome_empresa=nome,
            cnpj=cnpj,
            data_abertura=data_abertura,
            codigo_empresa=codigo_empresa,
            atividade_principal=payload.get("atividade_principal"),
            socio_administrador=payload.get("socio_administrador"),
            tributacao=payload.get("tributacao"),
            regime_lancamento=payload.get("regime_lancamento"),
            sistemas_consultorias=payload.get("sistemas_consultorias"),
            sistema_utilizado=payload.get("sistema_utilizado"),
            acessos=payload.get("acessos"),
            observacao_acessos=payload.get("observacao_acessos"),
            contatos=payload.get("contatos"),
            ativo=bool(payload.get("ativo", True)),
        )
        db.session.add(empresa)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to create company via API")
        return jsonify({"error": "failed_to_create"}), 500

    return jsonify(_serialize_empresa(empresa, include_departments=True)), 201


@api_bp.route("/empresas/<int:empresa_id>", methods=["PATCH"])
@token_required
def api_update_empresa(empresa_id: int):
    """Update a company (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    empresa = Empresa.query.get(empresa_id)
    if not empresa:
        return jsonify({"error": "not_found"}), 404

    payload = request.get_json(silent=True) or {}
    for field in [
        "nome_empresa",
        "atividade_principal",
        "socio_administrador",
        "tributacao",
        "regime_lancamento",
        "sistemas_consultorias",
        "sistema_utilizado",
        "acessos",
        "observacao_acessos",
        "codigo_empresa",
        "contatos",
    ]:
        if field in payload:
            setattr(empresa, field, payload.get(field))
    if "cnpj" in payload:
        empresa.cnpj = (payload.get("cnpj") or "").strip() or empresa.cnpj
    if "data_abertura" in payload:
        raw = (payload.get("data_abertura") or "").strip()
        if raw:
            try:
                empresa.data_abertura = _parse_date(raw)
            except ValueError:
                return jsonify({"error": "invalid_data_abertura"}), 400
    if "ativo" in payload:
        empresa.ativo = bool(payload.get("ativo"))

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to update company via API")
        return jsonify({"error": "failed_to_update"}), 500

    return jsonify(_serialize_empresa(empresa, include_departments=True))


@api_bp.route("/empresas/<int:empresa_id>", methods=["DELETE"])
@token_required
def api_delete_empresa(empresa_id: int):
    """Delete a company (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    try:
        deleted = Empresa.query.filter(Empresa.id == empresa_id).delete(synchronize_session=False)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to delete company via API")
        return jsonify({"error": "failed_to_delete"}), 500

    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return ("", 204)


@api_bp.route("/departamentos", methods=["POST"])
@token_required
def api_create_department():
    """Create a department (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    empresa_id = payload.get("empresa_id")
    tipo = (payload.get("tipo") or "").strip()
    if not empresa_id or not tipo:
        return jsonify({"error": "empresa_id_and_tipo_required"}), 400

    try:
        dept = Departamento(
            empresa_id=empresa_id,
            tipo=tipo,
            responsavel=payload.get("responsavel"),
            descricao=payload.get("descricao"),
            formas_importacao=payload.get("formas_importacao"),
            forma_movimento=payload.get("forma_movimento"),
            envio_digital=payload.get("envio_digital"),
            envio_fisico=payload.get("envio_fisico"),
            malote_coleta=payload.get("malote_coleta"),
            observacao_movimento=payload.get("observacao_movimento"),
            observacao_importacao=payload.get("observacao_importacao"),
            observacao_contato=payload.get("observacao_contato"),
            metodo_importacao=payload.get("metodo_importacao"),
            controle_relatorios=payload.get("controle_relatorios"),
            observacao_controle_relatorios=payload.get("observacao_controle_relatorios"),
            contatos=payload.get("contatos"),
            data_envio=payload.get("data_envio"),
            registro_funcionarios=payload.get("registro_funcionarios"),
            ponto_eletronico=payload.get("ponto_eletronico"),
            pagamento_funcionario=payload.get("pagamento_funcionario"),
            particularidades_texto=payload.get("particularidades_texto"),
        )
        db.session.add(dept)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to create department via API")
        return jsonify({"error": "failed_to_create"}), 500

    return jsonify(_serialize_department(dept)), 201


@api_bp.route("/departamentos/<int:dept_id>", methods=["PATCH"])
@token_required
def api_update_department(dept_id: int):
    """Update a department (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    dept = Departamento.query.get(dept_id)
    if not dept:
        return jsonify({"error": "not_found"}), 404

    payload = request.get_json(silent=True) or {}
    for field in [
        "tipo",
        "responsavel",
        "descricao",
        "formas_importacao",
        "forma_movimento",
        "envio_digital",
        "envio_fisico",
        "malote_coleta",
        "observacao_movimento",
        "observacao_importacao",
        "observacao_contato",
        "metodo_importacao",
        "controle_relatorios",
        "observacao_controle_relatorios",
        "contatos",
        "data_envio",
        "registro_funcionarios",
        "ponto_eletronico",
        "pagamento_funcionario",
        "particularidades_texto",
    ]:
        if field in payload:
            setattr(dept, field, payload.get(field))
    if "empresa_id" in payload:
        dept.empresa_id = payload.get("empresa_id") or dept.empresa_id

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to update department via API")
        return jsonify({"error": "failed_to_update"}), 500

    return jsonify(_serialize_department(dept))


@api_bp.route("/departamentos/<int:dept_id>", methods=["DELETE"])
@token_required
def api_delete_department(dept_id: int):
    """Delete a department (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    try:
        deleted = Departamento.query.filter(Departamento.id == dept_id).delete(synchronize_session=False)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to delete department via API")
        return jsonify({"error": "failed_to_delete"}), 500

    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return ("", 204)


# ---------------------- Procedimentos Operacionais ----------------------


@api_bp.route("/procedimentos", methods=["GET"])
@token_required
def api_procedures():
    """List operational procedures."""

    q = (request.args.get("q") or "").strip()
    limit = request.args.get("limit", type=int, default=50) or 50
    limit = min(max(limit, 1), 200)

    query = OperationalProcedure.query.order_by(OperationalProcedure.updated_at.desc())
    if q:
        like = f"%{q}%"
        query = query.filter(
            sa.or_(
                OperationalProcedure.title.ilike(like),
                OperationalProcedure.descricao.ilike(like),
            )
        )
    items = query.limit(limit).all()
    return jsonify([_serialize_procedure(proc) for proc in items])


@api_bp.route("/procedimentos/<int:proc_id>", methods=["GET"])
@token_required
def api_procedure_detail(proc_id: int):
    """Return a single operational procedure."""

    proc = OperationalProcedure.query.get(proc_id)
    if not proc:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_serialize_procedure(proc))


@api_bp.route("/procedimentos", methods=["POST"])
@token_required
def api_procedure_create():
    """Create an operational procedure (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title_required"}), 400
    descricao = payload.get("descricao")

    try:
        proc = OperationalProcedure(
            title=title,
            descricao=descricao,
            created_by_id=g.api_user.id,
        )
        db.session.add(proc)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to create procedure via API")
        return jsonify({"error": "failed_to_create"}), 500

    return jsonify(_serialize_procedure(proc)), 201


@api_bp.route("/procedimentos/<int:proc_id>", methods=["PATCH"])
@token_required
def api_procedure_update(proc_id: int):
    """Update an operational procedure (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    proc = OperationalProcedure.query.get(proc_id)
    if not proc:
        return jsonify({"error": "not_found"}), 404

    payload = request.get_json(silent=True) or {}
    if "title" in payload:
        proc.title = (payload.get("title") or "").strip() or proc.title
    if "descricao" in payload:
        proc.descricao = payload.get("descricao")

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to update procedure via API")
        return jsonify({"error": "failed_to_update"}), 500

    return jsonify(_serialize_procedure(proc))


@api_bp.route("/procedimentos/<int:proc_id>", methods=["DELETE"])
@token_required
def api_procedure_delete(proc_id: int):
    """Delete an operational procedure (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    try:
        deleted = OperationalProcedure.query.filter(OperationalProcedure.id == proc_id).delete(
            synchronize_session=False
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to delete procedure via API")
        return jsonify({"error": "failed_to_delete"}), 500

    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return ("", 204)


# ---------------------- Notas (Débito / Cadastro / Recorrentes) ----------------------


@api_bp.route("/notas/debito", methods=["GET"])
@token_required
def api_notas_debito():
    """List debit notes (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    limit = request.args.get("limit", type=int, default=100) or 100
    limit = min(max(limit, 1), 300)
    q = (request.args.get("q") or "").strip()

    query = NotaDebito.query.order_by(NotaDebito.data_emissao.desc())
    if q:
        like = f"%{q}%"
        query = query.filter(NotaDebito.empresa.ilike(like))

    notas = query.limit(limit).all()
    return jsonify([_serialize_nota_debito(n) for n in notas])


@api_bp.route("/notas/debito/<int:nota_id>", methods=["GET"])
@token_required
def api_nota_debito_detail(nota_id: int):
    """Return a single debit note (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    nota = NotaDebito.query.get(nota_id)
    if not nota:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_serialize_nota_debito(nota))


@api_bp.route("/notas/debito", methods=["POST"])
@token_required
def api_nota_debito_create():
    """Create a debit note (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    data_raw = (payload.get("data_emissao") or "").strip()
    empresa = (payload.get("empresa") or "").strip()
    forma_pagamento = (payload.get("forma_pagamento") or "").strip()
    notas = payload.get("notas") or 1
    qtde_itens = payload.get("qtde_itens") or 1
    if not data_raw or not empresa or not forma_pagamento:
        return jsonify({"error": "data_emissao_empresa_forma_pagamento_required"}), 400
    try:
        data_emissao = _parse_date(data_raw)
    except ValueError:
        return jsonify({"error": "invalid_data_emissao"}), 400

    try:
        valor_un = _parse_decimal(payload.get("valor_un")) if payload.get("valor_un") is not None else None
        total = _parse_decimal(payload.get("total")) if payload.get("total") is not None else None
    except InvalidOperation:
        return jsonify({"error": "invalid_valor"}), 400

    try:
        nota = NotaDebito(
            data_emissao=data_emissao,
            empresa=empresa,
            notas=int(notas or 1),
            qtde_itens=int(qtde_itens or 1),
            valor_un=valor_un,
            total=total,
            acordo=payload.get("acordo"),
            forma_pagamento=forma_pagamento,
            observacao=payload.get("observacao"),
        )
        db.session.add(nota)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to create debit note via API")
        return jsonify({"error": "failed_to_create"}), 500

    return jsonify(_serialize_nota_debito(nota)), 201


@api_bp.route("/notas/debito/<int:nota_id>", methods=["PATCH"])
@token_required
def api_nota_debito_update(nota_id: int):
    """Update a debit note (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    nota = NotaDebito.query.get(nota_id)
    if not nota:
        return jsonify({"error": "not_found"}), 404

    payload = request.get_json(silent=True) or {}
    if "data_emissao" in payload:
        raw = (payload.get("data_emissao") or "").strip()
        if raw:
            try:
                nota.data_emissao = _parse_date(raw)
            except ValueError:
                return jsonify({"error": "invalid_data_emissao"}), 400
    for field in ["empresa", "acordo", "forma_pagamento", "observacao"]:
        if field in payload:
            setattr(nota, field, payload.get(field))
    if "notas" in payload:
        nota.notas = int(payload.get("notas") or nota.notas)
    if "qtde_itens" in payload:
        nota.qtde_itens = int(payload.get("qtde_itens") or nota.qtde_itens)
    for field, attr in [("valor_un", "valor_un"), ("total", "total")]:
        if field in payload:
            try:
                parsed = _parse_decimal(payload.get(field)) if payload.get(field) is not None else None
            except InvalidOperation:
                return jsonify({"error": f"invalid_{field}"}), 400
            setattr(nota, attr, parsed)

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to update debit note via API")
        return jsonify({"error": "failed_to_update"}), 500

    return jsonify(_serialize_nota_debito(nota))


@api_bp.route("/notas/debito/<int:nota_id>", methods=["DELETE"])
@token_required
def api_nota_debito_delete(nota_id: int):
    """Delete a debit note (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    try:
        deleted = NotaDebito.query.filter(NotaDebito.id == nota_id).delete(synchronize_session=False)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to delete debit note via API")
        return jsonify({"error": "failed_to_delete"}), 500

    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return ("", 204)


@api_bp.route("/notas/cadastro", methods=["GET"])
@token_required
def api_cadastro_notas():
    """List cadastro notas (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    limit = request.args.get("limit", type=int, default=100) or 100
    limit = min(max(limit, 1), 300)
    q = (request.args.get("q") or "").strip()

    query = CadastroNota.query.order_by(CadastroNota.created_at.desc())
    if q:
        like = f"%{q}%"
        query = query.filter(CadastroNota.cadastro.ilike(like))

    itens = query.limit(limit).all()
    return jsonify([_serialize_cadastro_nota(c) for c in itens])


@api_bp.route("/notas/cadastro/<int:cadastro_id>", methods=["GET"])
@token_required
def api_cadastro_nota_detail(cadastro_id: int):
    """Return a single cadastro nota (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    entry = CadastroNota.query.get(cadastro_id)
    if not entry:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_serialize_cadastro_nota(entry))


@api_bp.route("/notas/cadastro", methods=["POST"])
@token_required
def api_cadastro_nota_create():
    """Create a cadastro nota (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    cadastro = (payload.get("cadastro") or "").strip()
    forma_pagamento = (payload.get("forma_pagamento") or "").strip()
    valor_raw = payload.get("valor")
    if not cadastro or not forma_pagamento or valor_raw is None:
        return jsonify({"error": "cadastro_valor_forma_pagamento_required"}), 400
    try:
        valor = _parse_decimal(valor_raw)
    except InvalidOperation:
        return jsonify({"error": "invalid_valor"}), 400

    try:
        entry = CadastroNota(
            cadastro=cadastro,
            valor=valor,
            forma_pagamento=forma_pagamento,
            pix=payload.get("pix"),
            acordo=payload.get("acordo"),
            usuario=payload.get("usuario"),
            senha=payload.get("senha"),
            ativo=bool(payload.get("ativo", True)),
        )
        db.session.add(entry)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to create cadastro nota via API")
        return jsonify({"error": "failed_to_create"}), 500

    return jsonify(_serialize_cadastro_nota(entry)), 201


@api_bp.route("/notas/cadastro/<int:cadastro_id>", methods=["PATCH"])
@token_required
def api_cadastro_nota_update(cadastro_id: int):
    """Update a cadastro nota (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    entry = CadastroNota.query.get(cadastro_id)
    if not entry:
        return jsonify({"error": "not_found"}), 404

    payload = request.get_json(silent=True) or {}
    for field in ["cadastro", "pix", "acordo", "forma_pagamento", "usuario", "senha"]:
        if field in payload:
            setattr(entry, field, payload.get(field))
    if "valor" in payload:
        try:
            entry.valor = _parse_decimal(payload.get("valor"))
        except InvalidOperation:
            return jsonify({"error": "invalid_valor"}), 400
    if "ativo" in payload:
        entry.ativo = bool(payload.get("ativo"))

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to update cadastro nota via API")
        return jsonify({"error": "failed_to_update"}), 500

    return jsonify(_serialize_cadastro_nota(entry))


@api_bp.route("/notas/cadastro/<int:cadastro_id>", methods=["DELETE"])
@token_required
def api_cadastro_nota_delete(cadastro_id: int):
    """Delete a cadastro nota (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    try:
        deleted = CadastroNota.query.filter(CadastroNota.id == cadastro_id).delete(
            synchronize_session=False
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to delete cadastro nota via API")
        return jsonify({"error": "failed_to_delete"}), 500

    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return ("", 204)


@api_bp.route("/notas/recorrentes", methods=["GET"])
@token_required
def api_notas_recorrentes():
    """List recurring notes (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    limit = request.args.get("limit", type=int, default=100) or 100
    limit = min(max(limit, 1), 300)
    q = (request.args.get("q") or "").strip()

    query = NotaRecorrente.query.order_by(NotaRecorrente.created_at.desc())
    if q:
        like = f"%{q}%"
        query = query.filter(NotaRecorrente.empresa.ilike(like))

    itens = query.limit(limit).all()
    return jsonify([_serialize_nota_recorrente(n) for n in itens])


@api_bp.route("/notas/recorrentes/<int:nota_id>", methods=["GET"])
@token_required
def api_nota_recorrente_detail(nota_id: int):
    """Return a single recurring note (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    entry = NotaRecorrente.query.get(nota_id)
    if not entry:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_serialize_nota_recorrente(entry))


@api_bp.route("/notas/recorrentes", methods=["POST"])
@token_required
def api_nota_recorrente_create():
    """Create a recurring note (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    empresa = (payload.get("empresa") or "").strip()
    periodo_inicio = payload.get("periodo_inicio")
    periodo_fim = payload.get("periodo_fim")
    dia_emissao = payload.get("dia_emissao")
    if not empresa or periodo_inicio is None or periodo_fim is None or dia_emissao is None:
        return jsonify({"error": "empresa_periodo_inicio_periodo_fim_dia_emissao_required"}), 400

    try:
        valor = _parse_decimal(payload.get("valor")) if payload.get("valor") is not None else None
    except InvalidOperation:
        return jsonify({"error": "invalid_valor"}), 400

    try:
        entry = NotaRecorrente(
            empresa=empresa,
            descricao=payload.get("descricao"),
            periodo_inicio=int(periodo_inicio),
            periodo_fim=int(periodo_fim),
            dia_emissao=int(dia_emissao),
            valor=valor,
            observacao=payload.get("observacao"),
            ativo=bool(payload.get("ativo", True)),
            ultimo_aviso=_parse_date(payload.get("ultimo_aviso")) if payload.get("ultimo_aviso") else None,
        )
        db.session.add(entry)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to create recurring note via API")
        return jsonify({"error": "failed_to_create"}), 500

    return jsonify(_serialize_nota_recorrente(entry)), 201


@api_bp.route("/notas/recorrentes/<int:nota_id>", methods=["PATCH"])
@token_required
def api_nota_recorrente_update(nota_id: int):
    """Update a recurring note (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    entry = NotaRecorrente.query.get(nota_id)
    if not entry:
        return jsonify({"error": "not_found"}), 404

    payload = request.get_json(silent=True) or {}
    for field in ["empresa", "descricao", "observacao"]:
        if field in payload:
            setattr(entry, field, payload.get(field))
    for field in ["periodo_inicio", "periodo_fim", "dia_emissao"]:
        if field in payload and payload.get(field) is not None:
            setattr(entry, field, int(payload.get(field)))
    if "valor" in payload:
        try:
            entry.valor = _parse_decimal(payload.get("valor")) if payload.get("valor") is not None else None
        except InvalidOperation:
            return jsonify({"error": "invalid_valor"}), 400
    if "ativo" in payload:
        entry.ativo = bool(payload.get("ativo"))
    if "ultimo_aviso" in payload:
        raw = payload.get("ultimo_aviso")
        try:
            entry.ultimo_aviso = _parse_date(raw) if raw else None
        except ValueError:
            return jsonify({"error": "invalid_ultimo_aviso"}), 400

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to update recurring note via API")
        return jsonify({"error": "failed_to_update"}), 500

    return jsonify(_serialize_nota_recorrente(entry))


@api_bp.route("/notas/recorrentes/<int:nota_id>", methods=["DELETE"])
@token_required
def api_nota_recorrente_delete(nota_id: int):
    """Delete a recurring note (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    try:
        deleted = NotaRecorrente.query.filter(NotaRecorrente.id == nota_id).delete(
            synchronize_session=False
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to delete recurring note via API")
        return jsonify({"error": "failed_to_delete"}), 500

    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return ("", 204)


# ---------------------- Cursos ----------------------


@api_bp.route("/courses", methods=["GET"])
@token_required
def api_courses():
    """List courses."""

    limit = request.args.get("limit", type=int, default=50) or 50
    limit = min(max(limit, 1), 200)
    status = (request.args.get("status") or "").strip()
    q = (request.args.get("q") or "").strip()

    query = Course.query.order_by(Course.start_date.desc())
    if status:
        query = query.filter(Course.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(Course.name.ilike(like))

    courses = query.limit(limit).all()
    return jsonify([_serialize_course(c) for c in courses])


@api_bp.route("/course-tags", methods=["GET"])
@token_required
def api_course_tags():
    """List course tags."""

    tags = CourseTag.query.order_by(CourseTag.name.asc()).all()
    return jsonify([{"id": t.id, "name": t.name} for t in tags])


@api_bp.route("/courses", methods=["POST"])
@token_required
def api_course_create():
    """Create a course (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    instructor = (payload.get("instructor") or "").strip()
    sectors = payload.get("sectors")
    participants = payload.get("participants")
    workload = payload.get("workload")
    start_date_raw = (payload.get("start_date") or "").strip()
    schedule_start = payload.get("schedule_start")
    schedule_end = payload.get("schedule_end")
    status = (payload.get("status") or "planejado").strip()
    if not name or not instructor or not sectors or not participants or not workload or not start_date_raw:
        return jsonify({"error": "missing_required_fields"}), 400
    try:
        start_date = _parse_date(start_date_raw)
    except ValueError:
        return jsonify({"error": "invalid_start_date"}), 400

    try:
        course = Course(
            name=name,
            instructor=instructor,
            sectors=sectors,
            participants=participants,
            workload=workload,
            start_date=start_date,
            schedule_start=schedule_start,
            schedule_end=schedule_end,
            completion_date=_parse_date(payload.get("completion_date")) if payload.get("completion_date") else None,
            status=status,
            observation=payload.get("observation"),
        )
        db.session.add(course)
        db.session.flush()
        tag_ids = payload.get("tag_ids") or []
        if tag_ids:
            tags = CourseTag.query.filter(CourseTag.id.in_(tag_ids)).all()
            course.tags = tags
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to create course via API")
        return jsonify({"error": "failed_to_create"}), 500

    return jsonify(_serialize_course(course)), 201


@api_bp.route("/courses/<int:course_id>", methods=["PATCH"])
@token_required
def api_course_update(course_id: int):
    """Update a course (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    course = Course.query.get(course_id)
    if not course:
        return jsonify({"error": "not_found"}), 404

    payload = request.get_json(silent=True) or {}
    for field in ["name", "instructor", "sectors", "participants", "workload", "schedule_start", "schedule_end", "observation", "status"]:
        if field in payload:
            setattr(course, field, payload.get(field))
    if "start_date" in payload:
        raw = (payload.get("start_date") or "").strip()
        if raw:
            try:
                course.start_date = _parse_date(raw)
            except ValueError:
                return jsonify({"error": "invalid_start_date"}), 400
    if "completion_date" in payload:
        raw = payload.get("completion_date")
        try:
            course.completion_date = _parse_date(raw) if raw else None
        except ValueError:
            return jsonify({"error": "invalid_completion_date"}), 400
    if "tag_ids" in payload:
        tag_ids = payload.get("tag_ids") or []
        tags = CourseTag.query.filter(CourseTag.id.in_(tag_ids)).all() if tag_ids else []
        course.tags = tags

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to update course via API")
        return jsonify({"error": "failed_to_update"}), 500

    return jsonify(_serialize_course(course))


@api_bp.route("/courses/<int:course_id>", methods=["DELETE"])
@token_required
def api_course_delete(course_id: int):
    """Delete a course (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    try:
        deleted = Course.query.filter(Course.id == course_id).delete(synchronize_session=False)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to delete course via API")
        return jsonify({"error": "failed_to_delete"}), 500

    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return ("", 204)


@api_bp.route("/course-tags", methods=["POST"])
@token_required
def api_course_tag_create():
    """Create a course tag (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name_required"}), 400

    try:
        tag = CourseTag(name=name)
        db.session.add(tag)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to create course tag via API")
        return jsonify({"error": "failed_to_create"}), 500

    return jsonify({"id": tag.id, "name": tag.name}), 201


@api_bp.route("/course-tags/<int:tag_id>", methods=["DELETE"])
@token_required
def api_course_tag_delete(tag_id: int):
    """Delete a course tag (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    try:
        deleted = CourseTag.query.filter(CourseTag.id == tag_id).delete(synchronize_session=False)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to delete course tag via API")
        return jsonify({"error": "failed_to_delete"}), 500

    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return ("", 204)


# ---------------------- Acessos ----------------------


@api_bp.route("/acessos", methods=["GET"])
@token_required
def api_access_links():
    """List access links."""

    limit = request.args.get("limit", type=int, default=200) or 200
    limit = min(max(limit, 1), 500)
    category = (request.args.get("category") or "").strip()

    query = AccessLink.query.order_by(AccessLink.created_at.desc())
    if category:
        query = query.filter(AccessLink.category == category)

    links = query.limit(limit).all()
    return jsonify([_serialize_access_link(link) for link in links])


@api_bp.route("/acessos", methods=["POST"])
@token_required
def api_create_access_link():
    """Create an access link (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    category = (payload.get("category") or "").strip()
    label = (payload.get("label") or "").strip()
    url_value = (payload.get("url") or "").strip()
    description = (payload.get("description") or "").strip() or None

    if not category or not label or not url_value:
        return jsonify({"error": "category_label_url_required"}), 400

    try:
        link = AccessLink(
            category=category,
            label=label,
            url=url_value,
            description=description,
            created_by_id=g.api_user.id,
        )
        db.session.add(link)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to create access link via API")
        return jsonify({"error": "failed_to_create"}), 500

    return jsonify(_serialize_access_link(link)), 201


@api_bp.route("/acessos/<int:link_id>", methods=["PATCH"])
@token_required
def api_update_access_link(link_id: int):
    """Update an access link (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    link = AccessLink.query.get(link_id)
    if not link:
        return jsonify({"error": "not_found"}), 404

    payload = request.get_json(silent=True) or {}
    if "category" in payload:
        link.category = (payload.get("category") or "").strip() or link.category
    if "label" in payload:
        link.label = (payload.get("label") or "").strip() or link.label
    if "url" in payload:
        link.url = (payload.get("url") or "").strip() or link.url
    if "description" in payload:
        link.description = (payload.get("description") or "").strip() or None

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to update access link via API")
        return jsonify({"error": "failed_to_update"}), 500

    return jsonify(_serialize_access_link(link))


@api_bp.route("/acessos/<int:link_id>", methods=["DELETE"])
@token_required
def api_delete_access_link(link_id: int):
    """Delete an access link (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    try:
        deleted = (
            AccessLink.query.filter(AccessLink.id == link_id).delete(synchronize_session=False)
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to delete access link via API")
        return jsonify({"error": "failed_to_delete"}), 500

    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return ("", 204)


# ---------------------- FAQ (Inclusoes) ----------------------


@api_bp.route("/faq", methods=["GET"])
@token_required
def api_faq():
    """List FAQ entries."""

    limit = request.args.get("limit", type=int, default=50) or 50
    limit = min(max(limit, 1), 200)
    q = (request.args.get("q") or "").strip()

    query = Inclusao.query.order_by(Inclusao.data.desc().nullslast(), Inclusao.id.desc())
    if q:
        like = f"%{q}%"
        query = query.filter(
            sa.or_(
                Inclusao.assunto.ilike(like),
                Inclusao.pergunta.ilike(like),
                Inclusao.resposta.ilike(like),
            )
        )
    itens = query.limit(limit).all()
    return jsonify([_serialize_faq(item) for item in itens])


@api_bp.route("/faq/<int:faq_id>", methods=["GET"])
@token_required
def api_faq_detail(faq_id: int):
    """Return a single FAQ entry."""

    entry = Inclusao.query.get(faq_id)
    if not entry:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_serialize_faq(entry))


@api_bp.route("/faq", methods=["POST"])
@token_required
def api_faq_create():
    """Create a FAQ entry (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    assunto = (payload.get("assunto") or "").strip()
    pergunta = (payload.get("pergunta") or "").strip()
    resposta = (payload.get("resposta") or "").strip()
    setor = (payload.get("setor") or "").strip() or None
    consultoria = (payload.get("consultoria") or "").strip() or None

    if not assunto or not pergunta or not resposta:
        return jsonify({"error": "assunto_pergunta_resposta_required"}), 400

    try:
        entry = Inclusao(
            data=datetime.utcnow().date(),
            usuario=g.api_user.username,
            setor=setor,
            consultoria=consultoria,
            assunto=assunto,
            pergunta=pergunta,
            resposta=resposta,
        )
        db.session.add(entry)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to create FAQ via API")
        return jsonify({"error": "failed_to_create"}), 500

    return jsonify(_serialize_faq(entry)), 201


@api_bp.route("/faq/<int:faq_id>", methods=["PATCH"])
@token_required
def api_faq_update(faq_id: int):
    """Update a FAQ entry (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    entry = Inclusao.query.get(faq_id)
    if not entry:
        return jsonify({"error": "not_found"}), 404

    payload = request.get_json(silent=True) or {}
    if "assunto" in payload:
        entry.assunto = (payload.get("assunto") or "").strip() or entry.assunto
    if "pergunta" in payload:
        entry.pergunta = (payload.get("pergunta") or "").strip() or entry.pergunta
    if "resposta" in payload:
        entry.resposta = (payload.get("resposta") or "").strip() or entry.resposta
    if "setor" in payload:
        entry.setor = (payload.get("setor") or "").strip() or None
    if "consultoria" in payload:
        entry.consultoria = (payload.get("consultoria") or "").strip() or None

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to update FAQ via API")
        return jsonify({"error": "failed_to_update"}), 500

    return jsonify(_serialize_faq(entry))


@api_bp.route("/faq/<int:faq_id>", methods=["DELETE"])
@token_required
def api_faq_delete(faq_id: int):
    """Delete a FAQ entry (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    try:
        deleted = Inclusao.query.filter(Inclusao.id == faq_id).delete(synchronize_session=False)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to delete FAQ via API")
        return jsonify({"error": "failed_to_delete"}), 500

    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return ("", 204)


# ---------------------- Diretoria ----------------------


@api_bp.route("/diretoria/eventos", methods=["GET"])
@token_required
def api_diretoria_eventos():
    """List Diretoria events."""

    limit = request.args.get("limit", type=int, default=50) or 50
    limit = min(max(limit, 1), 200)
    q = (request.args.get("q") or "").strip()
    event_type = (request.args.get("type") or "").strip()

    query = DiretoriaEvent.query.order_by(DiretoriaEvent.event_date.desc())
    if q:
        like = f"%{q}%"
        query = query.filter(DiretoriaEvent.name.ilike(like))
    if event_type:
        query = query.filter(DiretoriaEvent.event_type == event_type)

    events = query.limit(limit).all()
    return jsonify([_serialize_diretoria_event(e) for e in events])


@api_bp.route("/diretoria/eventos/<int:event_id>", methods=["GET"])
@token_required
def api_diretoria_event_detail(event_id: int):
    """Return a single Diretoria event."""

    event = DiretoriaEvent.query.get(event_id)
    if not event:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_serialize_diretoria_event(event))


@api_bp.route("/diretoria/eventos", methods=["POST"])
@token_required
def api_diretoria_event_create():
    """Create a Diretoria event (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    event_type = (payload.get("event_type") or "").strip()
    audience = (payload.get("audience") or "").strip()
    event_date_raw = (payload.get("event_date") or "").strip()

    if not name or not event_type or not event_date_raw:
        return jsonify({"error": "name_event_type_event_date_required"}), 400
    try:
        event_date = datetime.strptime(event_date_raw, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "invalid_event_date"}), 400

    description = (payload.get("description") or "").strip() or None
    participants = payload.get("participants") or 0
    services = payload.get("services") or {}
    photos = payload.get("photos") or []
    total_cost = payload.get("total_cost")

    try:
        evt = DiretoriaEvent(
            name=name,
            event_type=event_type,
            event_date=event_date,
            description=description,
            audience=audience or "interno",
            participants=int(participants or 0),
            services=services,
            photos=photos,
            total_cost=total_cost or 0,
            created_by_id=g.api_user.id,
        )
        db.session.add(evt)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to create Diretoria event via API")
        return jsonify({"error": "failed_to_create"}), 500

    return jsonify(_serialize_diretoria_event(evt)), 201


@api_bp.route("/diretoria/eventos/<int:event_id>", methods=["PATCH"])
@token_required
def api_diretoria_event_update(event_id: int):
    """Update a Diretoria event (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    evt = DiretoriaEvent.query.get(event_id)
    if not evt:
        return jsonify({"error": "not_found"}), 404

    payload = request.get_json(silent=True) or {}
    if "name" in payload:
        evt.name = (payload.get("name") or "").strip() or evt.name
    if "event_type" in payload:
        evt.event_type = (payload.get("event_type") or "").strip() or evt.event_type
    if "audience" in payload:
        evt.audience = (payload.get("audience") or "").strip() or evt.audience
    if "description" in payload:
        evt.description = (payload.get("description") or "").strip() or None
    if "participants" in payload:
        try:
            evt.participants = int(payload.get("participants") or 0)
        except Exception:
            pass
    if "services" in payload:
        evt.services = payload.get("services") or {}
    if "photos" in payload:
        evt.photos = payload.get("photos") or []
    if "total_cost" in payload:
        total_cost = payload.get("total_cost")
        evt.total_cost = total_cost if total_cost is not None else evt.total_cost
    if "event_date" in payload:
        raw = (payload.get("event_date") or "").strip()
        if raw:
            try:
                evt.event_date = datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                return jsonify({"error": "invalid_event_date"}), 400

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to update Diretoria event via API")
        return jsonify({"error": "failed_to_update"}), 500

    return jsonify(_serialize_diretoria_event(evt))


@api_bp.route("/diretoria/eventos/<int:event_id>", methods=["DELETE"])
@token_required
def api_diretoria_event_delete(event_id: int):
    """Delete a Diretoria event (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    try:
        deleted = DiretoriaEvent.query.filter(DiretoriaEvent.id == event_id).delete(synchronize_session=False)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to delete Diretoria event via API")
        return jsonify({"error": "failed_to_delete"}), 500

    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return ("", 204)


@api_bp.route("/diretoria/acordos", methods=["GET"])
@token_required
def api_diretoria_acordos():
    """List Diretoria agreements."""

    limit = request.args.get("limit", type=int, default=50) or 50
    limit = min(max(limit, 1), 200)
    user_id = request.args.get("user_id", type=int)

    query = DiretoriaAgreement.query.order_by(DiretoriaAgreement.agreement_date.desc())
    if user_id:
        query = query.filter(DiretoriaAgreement.user_id == user_id)

    items = query.limit(limit).all()
    return jsonify([_serialize_diretoria_agreement(a) for a in items])


@api_bp.route("/diretoria/acordos/<int:agreement_id>", methods=["GET"])
@token_required
def api_diretoria_acordo_detail(agreement_id: int):
    """Return a single Diretoria agreement."""

    agreement = DiretoriaAgreement.query.get(agreement_id)
    if not agreement:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_serialize_diretoria_agreement(agreement))


@api_bp.route("/diretoria/acordos", methods=["POST"])
@token_required
def api_diretoria_acordo_create():
    """Create a Diretoria agreement (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    user_id = payload.get("user_id")
    title = (payload.get("title") or "").strip()
    agreement_date_raw = (payload.get("agreement_date") or "").strip()

    if not user_id or not title or not agreement_date_raw:
        return jsonify({"error": "user_id_title_agreement_date_required"}), 400

    try:
        agreement_date = datetime.strptime(agreement_date_raw, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "invalid_agreement_date"}), 400

    notes = (payload.get("notes") or "").strip() or None
    status = (payload.get("status") or "").strip() or None

    try:
        agreement = DiretoriaAgreement(
            user_id=user_id,
            title=title,
            agreement_date=agreement_date,
            notes=notes,
            status=status,
        )
        db.session.add(agreement)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to create Diretoria agreement via API")
        return jsonify({"error": "failed_to_create"}), 500

    return jsonify(_serialize_diretoria_agreement(agreement)), 201


@api_bp.route("/diretoria/acordos/<int:agreement_id>", methods=["PATCH"])
@token_required
def api_diretoria_acordo_update(agreement_id: int):
    """Update a Diretoria agreement (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    agreement = DiretoriaAgreement.query.get(agreement_id)
    if not agreement:
        return jsonify({"error": "not_found"}), 404

    payload = request.get_json(silent=True) or {}
    if "title" in payload:
        agreement.title = (payload.get("title") or "").strip() or agreement.title
    if "notes" in payload:
        agreement.notes = (payload.get("notes") or "").strip() or None
    if "status" in payload:
        agreement.status = (payload.get("status") or "").strip() or agreement.status
    if "agreement_date" in payload:
        raw = (payload.get("agreement_date") or "").strip()
        if raw:
            try:
                agreement.agreement_date = datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                return jsonify({"error": "invalid_agreement_date"}), 400

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to update Diretoria agreement via API")
        return jsonify({"error": "failed_to_update"}), 500

    return jsonify(_serialize_diretoria_agreement(agreement))


@api_bp.route("/diretoria/acordos/<int:agreement_id>", methods=["DELETE"])
@token_required
def api_diretoria_acordo_delete(agreement_id: int):
    """Delete a Diretoria agreement (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    try:
        deleted = (
            DiretoriaAgreement.query.filter(DiretoriaAgreement.id == agreement_id).delete(
                synchronize_session=False
            )
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to delete Diretoria agreement via API")
        return jsonify({"error": "failed_to_delete"}), 500

    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return ("", 204)


@api_bp.route("/diretoria/feedbacks", methods=["GET"])
@token_required
def api_diretoria_feedbacks():
    """List Diretoria feedbacks."""

    limit = request.args.get("limit", type=int, default=50) or 50
    limit = min(max(limit, 1), 200)
    user_id = request.args.get("user_id", type=int)

    query = DiretoriaFeedback.query.order_by(DiretoriaFeedback.created_at.desc())
    if user_id:
        query = query.filter(DiretoriaFeedback.user_id == user_id)

    items = query.limit(limit).all()
    return jsonify([_serialize_diretoria_feedback(fb) for fb in items])


@api_bp.route("/diretoria/feedbacks/<int:feedback_id>", methods=["GET"])
@token_required
def api_diretoria_feedback_detail(feedback_id: int):
    """Return a single Diretoria feedback."""

    feedback = DiretoriaFeedback.query.get(feedback_id)
    if not feedback:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_serialize_diretoria_feedback(feedback))


@api_bp.route("/diretoria/feedbacks", methods=["POST"])
@token_required
def api_diretoria_feedback_create():
    """Create a Diretoria feedback (authenticated users)."""

    payload = request.get_json(silent=True) or {}
    feedback_type = (payload.get("feedback_type") or "").strip()
    content = (payload.get("content") or "").strip()
    if not feedback_type or not content:
        return jsonify({"error": "feedback_type_and_content_required"}), 400

    try:
        fb = DiretoriaFeedback(
            user_id=g.api_user.id,
            feedback_type=feedback_type,
            content=content,
        )
        db.session.add(fb)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to create Diretoria feedback via API")
        return jsonify({"error": "failed_to_create"}), 500

    return jsonify(_serialize_diretoria_feedback(fb)), 201


# ---------------------- Report Permissions (admin) ----------------------


@api_bp.route("/reports/permissions", methods=["GET"])
@token_required
def api_report_permissions():
    """List report permissions (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    limit = request.args.get("limit", type=int, default=200) or 200
    limit = min(max(limit, 1), 500)
    report_code = (request.args.get("report_code") or "").strip()

    query = ReportPermission.query.order_by(ReportPermission.report_code.asc(), ReportPermission.id.asc())
    if report_code:
        query = query.filter(ReportPermission.report_code == report_code)

    entries = query.limit(limit).all()
    return jsonify([_serialize_report_permission(entry) for entry in entries])


@api_bp.route("/reports/permissions", methods=["POST"])
@token_required
def api_report_permission_create():
    """Create a report permission (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    report_code = (payload.get("report_code") or "").strip()
    tag_id = payload.get("tag_id")
    user_id = payload.get("user_id")

    if not report_code or (not tag_id and not user_id):
        return jsonify({"error": "report_code_and_tag_or_user_required"}), 400

    try:
        entry = ReportPermission(
            report_code=report_code,
            tag_id=tag_id,
            user_id=user_id,
        )
        db.session.add(entry)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to create report permission via API")
        return jsonify({"error": "failed_to_create"}), 500

    return jsonify(_serialize_report_permission(entry)), 201


@api_bp.route("/reports/permissions/<int:perm_id>", methods=["DELETE"])
@token_required
def api_report_permission_delete(perm_id: int):
    """Delete a report permission (admin only)."""

    if not is_user_admin(g.api_user):
        return jsonify({"error": "forbidden"}), 403

    try:
        deleted = (
            ReportPermission.query.filter(ReportPermission.id == perm_id).delete(
                synchronize_session=False
            )
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to delete report permission via API")
        return jsonify({"error": "failed_to_delete"}), 500

    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return ("", 204)
