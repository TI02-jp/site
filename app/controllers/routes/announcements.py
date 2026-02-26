"""Announcements blueprint and related helpers."""

from __future__ import annotations

import os
import re
from collections import defaultdict
from mimetypes import guess_type
from typing import Iterable
from uuid import uuid4

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    has_request_context,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload
from werkzeug.utils import secure_filename

from app import db
from app.controllers.routes import utc3_now
from app.controllers.routes._decorators import (
    get_accessible_tag_ids,
    has_report_access,
    meeting_only_access_check,
)
from app.controllers.routes.blueprints.notifications import _invalidate_notification_cache
from app.forms import AnnouncementForm
from app.models.tables import (
    Announcement,
    AnnouncementAttachment,
    AuditLog,
    NotificationType,
    TaskNotification,
    Tag,
    User,
)
from app.utils.permissions import is_user_admin
from app.utils.security import sanitize_html
from app.utils.audit import ActionType, ResourceType, log_user_action

announcements_bp = Blueprint("announcements", __name__)

ANNOUNCEMENTS_UPLOAD_SUBDIR = os.path.join("uploads", "announcements")


def _can_manage_announcements() -> bool:
    """Verifica se o usuário pode gerenciar comunicados.

    Returns:
        True se o usuário é admin OU tem a permissão announcements_manage
    """
    # Admin sempre pode
    if is_user_admin(current_user):
        return True

    # Verifica permissão específica
    return has_report_access("announcements_manage")


def _remove_announcement_attachment(attachment_path: str | None) -> None:
    """Delete an announcement attachment from disk if it exists."""

    if not attachment_path or not has_request_context():
        return

    static_root = os.path.join(current_app.root_path, "static")
    file_path = os.path.join(static_root, attachment_path)

    try:
        os.remove(file_path)
    except FileNotFoundError:
        return


def _remove_announcement_attachments(paths: Iterable[str | None]) -> None:
    """Remove multiple stored announcement attachments from disk."""

    seen: set[str] = set()
    for path in paths:
        if not path:
            continue
        normalized = path.replace("\\", "/")
        if normalized in seen:
            continue
        seen.add(normalized)
        _remove_announcement_attachment(normalized)


def _save_announcement_file(uploaded_file) -> dict[str, str | None]:
    """Persist an uploaded file and return its storage metadata."""

    original_name = secure_filename(uploaded_file.filename or "")
    extension = os.path.splitext(original_name)[1].lower()
    unique_name = f"{uuid4().hex}{extension}"

    upload_directory = os.path.join(
        current_app.root_path, "static", ANNOUNCEMENTS_UPLOAD_SUBDIR
    )
    os.makedirs(upload_directory, exist_ok=True)

    stored_path = os.path.join(upload_directory, unique_name)
    uploaded_file.save(stored_path)

    relative_path = os.path.join(ANNOUNCEMENTS_UPLOAD_SUBDIR, unique_name).replace(
        "\\", "/"
    )
    mime_type = uploaded_file.mimetype or guess_type(original_name)[0]

    return {
        "path": relative_path,
        "name": original_name or None,
        "mime_type": mime_type,
    }


def _normalize_announcement_content(raw_content: str | None) -> str:
    """Sanitize announcement bodies and preserve line breaks for plain text."""

    cleaned = sanitize_html(raw_content or "", allow_data_images=True)
    if not cleaned:
        return ""

    if not re.search(r"<[a-zA-Z/][^>]*>", cleaned):
        normalized = cleaned.replace("\r\n", "\n").replace("\r", "\n")
        return normalized.replace("\n", "<br>")

    return cleaned


def _collect_uploaded_files(form: AnnouncementForm) -> list:
    """Return uploaded files, falling back to raw request files when needed."""
    uploaded_files = [
        storage
        for storage in (form.attachments.data or [])
        if storage and storage.filename
    ]
    if uploaded_files:
        return uploaded_files

    field_name = getattr(form.attachments, "name", None) or "attachments"
    uploaded_files = [
        storage
        for storage in request.files.getlist(field_name)
        if storage and storage.filename
    ]
    if uploaded_files:
        return uploaded_files

    if field_name != "attachments":
        return [
            storage
            for storage in request.files.getlist("attachments")
            if storage and storage.filename
        ]

    return []


def _broadcast_announcement_notification(announcement: Announcement) -> None:
    """Emit a notification about ``announcement`` for every active user."""

    target_tag_ids = {
        tag.id for tag in (announcement.target_tags or []) if tag.id
    }

    user_query = User.query.with_entities(User.id).filter(User.ativo.is_(True))
    if target_tag_ids:
        user_query = user_query.filter(
            or_(
                User.role == "admin",
                User.is_master.is_(True),
                User.tags.any(Tag.id.in_(target_tag_ids)),
            )
        )

    active_user_rows = user_query.all()
    if not active_user_rows:
        return

    now = utc3_now()
    subject = (announcement.subject or "").strip()
    if subject:
        base_message = f"Novo comunicado: {subject}"
    else:
        base_message = "Novo comunicado publicado."
    truncated_message = base_message[:255]

    notifications = [
        TaskNotification(
            user_id=user_id,
            announcement_id=announcement.id,
            task_id=None,
            type=NotificationType.ANNOUNCEMENT.value,
            message=truncated_message,
            created_at=now,
        )
        for (user_id,) in active_user_rows
    ]

    db.session.bulk_save_objects(notifications)
    _invalidate_notification_cache()

    # Broadcast notification to all affected users' SSE streams
    from app.services.realtime import get_broadcaster

    try:
        broadcaster = get_broadcaster()
        for user_id, in active_user_rows:
            broadcaster.broadcast(
                event_type="notification:created",
                data={"user_id": user_id, "announcement_id": announcement.id},
                user_id=user_id,
                scope="notifications",
            )
    except Exception:
        # Don't fail if broadcast fails
        pass


def _get_tag_choices() -> list[tuple[int, str]]:
    """Return sorted tag choices for the announcement forms."""

    return [
        (tag.id, tag.nome)
        for tag in Tag.query.order_by(Tag.nome.asc()).all()
    ]


def _apply_visibility_filter(query):
    """Filter announcements so users only see those targeting their setores."""

    if is_user_admin(current_user) or getattr(current_user, "is_master", False):
        return query

    accessible_tag_ids = {
        tag_id for tag_id in (get_accessible_tag_ids(current_user) or []) if tag_id
    }
    if accessible_tag_ids:
        return query.filter(
            or_(
                ~Announcement.target_tags.any(),
                Announcement.target_tags.any(Tag.id.in_(accessible_tag_ids)),
            )
        )

    return query.filter(~Announcement.target_tags.any())


def _build_announcement_view_status(
    announcements: list[Announcement],
) -> tuple[dict[int, list[AuditLog]], dict[int, list[User]], dict[int, int]]:
    """Return per-announcement first viewers, non-viewers and totals."""

    announcement_ids = [announcement.id for announcement in announcements if announcement.id]
    if not announcement_ids:
        return {}, {}, {}

    all_users = (
        User.query
        .order_by(User.name.asc(), User.username.asc())
        .all()
    )

    first_log_ids_subquery = (
        db.session.query(func.min(AuditLog.id).label("first_log_id"))
        .filter(
            AuditLog.resource_type == ResourceType.ANNOUNCEMENT,
            AuditLog.action_type == ActionType.VIEW,
            AuditLog.action_description == "mural_card_click",
            AuditLog.resource_id.in_(announcement_ids),
        )
        .group_by(AuditLog.resource_id, AuditLog.user_id)
        .subquery()
    )

    first_view_logs = (
        AuditLog.query.options(joinedload(AuditLog.user))
        .filter(
            AuditLog.id.in_(
                db.session.query(first_log_ids_subquery.c.first_log_id)
            )
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .all()
    )

    seen_by_announcement: dict[int, dict[int, AuditLog]] = defaultdict(dict)
    for row in first_view_logs:
        announcement_id = row.resource_id
        user_id = row.user_id
        if not isinstance(announcement_id, int) or announcement_id not in announcement_ids:
            continue
        if not isinstance(user_id, int):
            continue
        seen_by_announcement[announcement_id][user_id] = row

    viewed_logs_by_announcement: dict[int, list[AuditLog]] = {}
    not_viewed_users_by_announcement: dict[int, list[User]] = {}
    viewed_totals: dict[int, int] = {}

    for announcement in announcements:
        announcement_id = announcement.id
        if not announcement_id:
            continue

        seen_map = seen_by_announcement.get(announcement_id, {})
        audience_user_ids = {user.id for user in all_users}
        viewed_logs = [
            log_item
            for user_id, log_item in seen_map.items()
            if user_id in audience_user_ids
        ]
        seen_audience_user_ids = {
            log_item.user_id
            for log_item in viewed_logs
            if isinstance(log_item.user_id, int)
        }
        not_viewed_users = [
            user for user in all_users if user.id not in seen_audience_user_ids
        ]

        viewed_logs_by_announcement[announcement_id] = viewed_logs
        not_viewed_users_by_announcement[announcement_id] = not_viewed_users
        viewed_totals[announcement_id] = len(viewed_logs)

    return (
        viewed_logs_by_announcement,
        not_viewed_users_by_announcement,
        viewed_totals,
    )


@announcements_bp.route(
    "/announcements", methods=["GET", "POST"], endpoint="announcements"
)
@login_required
@meeting_only_access_check
def announcements():
    """List internal announcements and allow admins to create new ones."""

    form = AnnouncementForm()
    tag_choices = _get_tag_choices()
    form.target_tag_ids.choices = tag_choices

    search_term = (request.args.get("q") or "").strip()
    selected_tag_id = request.args.get("tag_id", type=int)
    available_tags = Tag.query.order_by(Tag.nome.asc()).all()

    base_query = Announcement.query

    if search_term:
        ilike_pattern = f"%{search_term}%"
        base_query = base_query.filter(
            or_(
                Announcement.subject.ilike(ilike_pattern),
                Announcement.content.ilike(ilike_pattern),
            )
        )
    if selected_tag_id:
        base_query = base_query.filter(
            Announcement.target_tags.any(Tag.id == selected_tag_id)
        )
    base_query = _apply_visibility_filter(base_query)

    total_announcements = base_query.count()

    announcements_query = (
        base_query.options(
            joinedload(Announcement.created_by),
            joinedload(Announcement.attachments),
            joinedload(Announcement.target_tags),
        )
        .order_by(Announcement.date.desc(), Announcement.created_at.desc())
    )

    if search_term:
        announcement_items = announcements_query.all()
    else:
        announcement_items = announcements_query.limit(6).all()

    display_count = len(announcement_items)
    history_count = max(total_announcements - 6, 0)
    has_history = not search_term and history_count > 0

    if request.method == "POST":
        if not _can_manage_announcements():
            abort(403)

        if form.validate_on_submit():
            cleaned_content = _normalize_announcement_content(form.content.data)
            announcement = Announcement(
                date=form.date.data,
                subject=form.subject.data,
                content=cleaned_content,
                created_by=current_user,
            )

            db.session.add(announcement)
            db.session.flush()

            uploaded_files = _collect_uploaded_files(form)

            for uploaded_file in uploaded_files:
                saved = _save_announcement_file(uploaded_file)
                db.session.add(
                    AnnouncementAttachment(
                        announcement=announcement,
                        file_path=saved["path"],
                        original_name=saved["name"],
                        mime_type=saved["mime_type"],
                    )
                )

            selected_tag_ids = {
                tag_id
                for tag_id in (form.target_tag_ids.data or [])
                if isinstance(tag_id, int)
            }
            if selected_tag_ids:
                announcement.target_tags = Tag.query.filter(Tag.id.in_(selected_tag_ids)).all()
            else:
                announcement.target_tags = []

            db.session.flush()
            announcement.sync_legacy_attachment_fields()

            _broadcast_announcement_notification(announcement)
            db.session.commit()

            flash("Comunicado criado com sucesso.", "success")
            return redirect(url_for("announcements"))

        flash(
            "N釅 foi poss」el criar o comunicado. Verifique os dados informados.",
            "danger",
        )

    announcement_reads: dict[int, bool] = {}
    read_rows = (
        TaskNotification.query.with_entities(
            TaskNotification.announcement_id, TaskNotification.read_at
        )
        .filter(
            TaskNotification.user_id == current_user.id,
            TaskNotification.announcement_id.isnot(None),
        )
        .all()
    )

    for announcement_id, read_at in read_rows:
        if announcement_id is None:
            continue
        if read_at:
            announcement_reads[announcement_id] = True
        elif announcement_id not in announcement_reads:
            announcement_reads[announcement_id] = False

    edit_forms: dict[int, AnnouncementForm] = {}
    if _can_manage_announcements():
        for item in announcement_items:
            edit_form = AnnouncementForm(prefix=f"edit-{item.id}")
            edit_form.date.data = item.date
            edit_form.subject.data = item.subject
            edit_form.content.data = item.content
            edit_form.target_tag_ids.choices = tag_choices
            edit_form.target_tag_ids.data = [tag.id for tag in item.target_tags]
            edit_forms[item.id] = edit_form

    can_view_click_logs = is_user_admin(current_user) or getattr(current_user, "is_master", False)
    can_access_mural_logs = has_report_access("mural_logs")
    announcement_view_logs: dict[int, list[AuditLog]] = {}
    announcement_not_viewed_users: dict[int, list[User]] = {}
    announcement_view_totals: dict[int, int] = {}
    if can_view_click_logs:
        (
            announcement_view_logs,
            announcement_not_viewed_users,
            announcement_view_totals,
        ) = _build_announcement_view_status(
            announcement_items
        )

    return render_template(
        "announcements.html",
        form=form,
        announcements=announcement_items,
        edit_forms=edit_forms,
        announcement_reads=announcement_reads,
        search_term=search_term,
        total_announcements=total_announcements,
        display_count=display_count,
        history_mode=False,
        history_count=history_count,
        has_history=has_history,
        search_action_url=url_for("announcements"),
        history_link_url=url_for("announcement_history"),
        history_back_url=None,
        can_manage=_can_manage_announcements(),
        can_view_click_logs=can_view_click_logs,
        can_access_mural_logs=can_access_mural_logs,
        announcement_view_logs=announcement_view_logs,
        announcement_not_viewed_users=announcement_not_viewed_users,
        announcement_view_totals=announcement_view_totals,
        available_tags=available_tags,
        selected_tag_id=selected_tag_id,
    )


@announcements_bp.route(
    "/announcements/history", methods=["GET"], endpoint="announcement_history"
)
@login_required
def announcement_history():
    """Display the backlog of announcements that fall outside the main mural."""

    search_term = (request.args.get("q") or "").strip()
    selected_tag_id = request.args.get("tag_id", type=int)
    available_tags = Tag.query.order_by(Tag.nome.asc()).all()

    recent_id_rows = (
        Announcement.query.with_entities(Announcement.id)
        .order_by(Announcement.date.desc(), Announcement.created_at.desc())
        .limit(6)
        .all()
    )
    recent_ids = [row[0] for row in recent_id_rows]

    base_query = Announcement.query
    tag_choices = _get_tag_choices()

    if recent_ids:
        base_query = base_query.filter(Announcement.id.notin_(recent_ids))

    if search_term:
        ilike_pattern = f"%{search_term}%"
        base_query = base_query.filter(
            or_(
                Announcement.subject.ilike(ilike_pattern),
                Announcement.content.ilike(ilike_pattern),
            )
        )

    if selected_tag_id:
        base_query = base_query.filter(
            Announcement.target_tags.any(Tag.id == selected_tag_id)
        )

    base_query = _apply_visibility_filter(base_query)

    total_history = base_query.count()

    # Add pagination to limit memory usage with concurrent users
    page = request.args.get("page", 1, type=int)
    per_page = 50  # Show 50 announcements per page

    announcements_query = (
        base_query.options(
            joinedload(Announcement.created_by),
            joinedload(Announcement.attachments),
            joinedload(Announcement.target_tags),
        )
        .order_by(Announcement.date.desc(), Announcement.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )

    announcement_items = announcements_query.all()
    display_count = len(announcement_items)
    total_pages = (total_history + per_page - 1) // per_page  # ceiling division

    announcement_reads: dict[int, bool] = {}
    read_rows = (
        TaskNotification.query.with_entities(
            TaskNotification.announcement_id, TaskNotification.read_at
        )
        .filter(
            TaskNotification.user_id == current_user.id,
            TaskNotification.announcement_id.isnot(None),
        )
        .all()
    )

    for announcement_id, read_at in read_rows:
        if announcement_id is None:
            continue
        if read_at:
            announcement_reads[announcement_id] = True
        elif announcement_id not in announcement_reads:
            announcement_reads[announcement_id] = False

    edit_forms: dict[int, AnnouncementForm] = {}
    if _can_manage_announcements():
        for item in announcement_items:
            edit_form = AnnouncementForm(prefix=f"edit-{item.id}")
            edit_form.date.data = item.date
            edit_form.subject.data = item.subject
            edit_form.content.data = item.content
            edit_form.target_tag_ids.choices = tag_choices
            edit_form.target_tag_ids.data = [tag.id for tag in item.target_tags]
            edit_forms[item.id] = edit_form

    can_view_click_logs = is_user_admin(current_user) or getattr(current_user, "is_master", False)
    can_access_mural_logs = has_report_access("mural_logs")
    announcement_view_logs: dict[int, list[AuditLog]] = {}
    announcement_not_viewed_users: dict[int, list[User]] = {}
    announcement_view_totals: dict[int, int] = {}
    if can_view_click_logs:
        (
            announcement_view_logs,
            announcement_not_viewed_users,
            announcement_view_totals,
        ) = _build_announcement_view_status(
            announcement_items
        )

    return render_template(
        "announcements.html",
        form=None,
        announcements=announcement_items,
        edit_forms=edit_forms,
        announcement_reads=announcement_reads,
        search_term=search_term,
        total_announcements=total_history,
        display_count=display_count,
        history_mode=True,
        history_count=total_history,
        has_history=False,
        search_action_url=url_for("announcement_history"),
        history_link_url=None,
        history_back_url=url_for("announcements"),
        # Pagination variables
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        can_manage=_can_manage_announcements(),
        can_view_click_logs=can_view_click_logs,
        can_access_mural_logs=can_access_mural_logs,
        announcement_view_logs=announcement_view_logs,
        announcement_not_viewed_users=announcement_not_viewed_users,
        announcement_view_totals=announcement_view_totals,
        available_tags=available_tags,
        selected_tag_id=selected_tag_id,
    )


@announcements_bp.route(
    "/announcements/<int:announcement_id>/update",
    methods=["POST"],
    endpoint="update_announcement",
)
@login_required
def update_announcement(announcement_id: int):
    """Update an announcement's content and manage its attachments."""

    if not _can_manage_announcements():
        abort(403)

    announcement = (
        Announcement.query.options(joinedload(Announcement.attachments))
        .get_or_404(announcement_id)
    )

    form = AnnouncementForm(prefix=f"edit-{announcement_id}")

    if form.validate_on_submit():
        announcement.date = form.date.data
        announcement.subject = form.subject.data
        announcement.content = _normalize_announcement_content(form.content.data)

        attachments_modified = False
        remove_ids = {
            int(attachment_id)
            for attachment_id in request.form.getlist("remove_attachment_ids")
            if attachment_id.isdigit()
        }

        if remove_ids:
            attachments_to_remove = [
                attachment
                for attachment in announcement.attachments
                if attachment.id in remove_ids
            ]
            for attachment in attachments_to_remove:
                _remove_announcement_attachment(attachment.file_path)
                db.session.delete(attachment)
            attachments_modified = True

        new_files = _collect_uploaded_files(form)

        for uploaded_file in new_files:
            saved = _save_announcement_file(uploaded_file)
            db.session.add(
                AnnouncementAttachment(
                    announcement=announcement,
                    file_path=saved["path"],
                    original_name=saved["name"],
                    mime_type=saved["mime_type"],
                )
            )
        if new_files:
            attachments_modified = True

        if attachments_modified:
            db.session.flush()
            announcement.sync_legacy_attachment_fields()

        selected_tag_ids = {
            tag_id
            for tag_id in (form.target_tag_ids.data or [])
            if isinstance(tag_id, int)
        }
        if selected_tag_ids:
            announcement.target_tags = Tag.query.filter(Tag.id.in_(selected_tag_ids)).all()
        else:
            announcement.target_tags = []

        db.session.commit()
        flash("Comunicado atualizado com sucesso.", "success")
        return redirect(url_for("announcements"))

    flash(
        "N釅 foi poss」el atualizar o comunicado. Verifique os dados informados.",
        "danger",
    )
    return redirect(url_for("announcements"))


@announcements_bp.route(
    "/announcements/<int:announcement_id>/delete",
    methods=["POST"],
    endpoint="delete_announcement",
)
@login_required
def delete_announcement(announcement_id: int):
    """Remove an existing announcement and its attachments."""

    if not _can_manage_announcements():
        abort(403)

    announcement = (
        Announcement.query.options(joinedload(Announcement.attachments))
        .get_or_404(announcement_id)
    )

    attachment_paths = [
        attachment.file_path for attachment in announcement.attachments if attachment.file_path
    ]
    if announcement.attachment_path:
        attachment_paths.append(announcement.attachment_path)

    TaskNotification.query.filter_by(
        announcement_id=announcement.id
    ).delete(synchronize_session=False)
    db.session.delete(announcement)
    db.session.commit()

    _remove_announcement_attachments(attachment_paths)

    flash("Comunicado removido com sucesso.", "success")
    return redirect(url_for("announcements"))


@announcements_bp.route(
    "/announcements/<int:announcement_id>/read",
    methods=["POST"],
    endpoint="mark_announcement_read",
)
@login_required
def mark_announcement_read(announcement_id: int):
    """Mark the current user's notification for an announcement as read."""

    announcement = Announcement.query.get_or_404(announcement_id)

    notifications = TaskNotification.query.filter(
        TaskNotification.announcement_id == announcement.id,
        TaskNotification.user_id == current_user.id,
    ).all()

    now = utc3_now()
    updated = 0
    already_read = False

    for notification in notifications:
        if notification.read_at:
            already_read = True
            continue
        notification.read_at = now
        updated += 1

    db.session.commit()

    read = bool(updated or already_read or not notifications)

    return jsonify({"status": "ok", "read": read})


@announcements_bp.route(
    "/announcements/log-event",
    methods=["POST"],
    endpoint="log_mural_event",
)
@login_required
@meeting_only_access_check
def log_mural_event():
    """Register MURAL interaction events for auditing/reporting."""

    payload = request.get_json(silent=True) or {}
    event_type = (payload.get("event_type") or "").strip().lower()
    if event_type not in {"mural_view", "mural_card_click"}:
        return jsonify({"status": "error", "message": "Evento invalido."}), 400

    resource_id = None
    if event_type == "mural_card_click":
        announcement_id = payload.get("announcement_id")
        if not isinstance(announcement_id, int):
            return jsonify({"status": "error", "message": "Comunicado invalido."}), 400

        announcement = _apply_visibility_filter(
            Announcement.query.filter(Announcement.id == announcement_id)
        ).first()
        if not announcement:
            return jsonify({"status": "error", "message": "Comunicado nao encontrado."}), 404
        resource_id = announcement.id

        # Keep only the first view per user/card for view/not-view tracking.
        first_view = (
            AuditLog.query.with_entities(AuditLog.id)
            .filter(
                AuditLog.resource_type == ResourceType.ANNOUNCEMENT,
                AuditLog.action_type == ActionType.VIEW,
                AuditLog.action_description == "mural_card_click",
                AuditLog.resource_id == resource_id,
                AuditLog.user_id == current_user.id,
            )
            .first()
        )
        if first_view:
            return jsonify({"status": "ok", "already_logged": True})

    log_user_action(
        action_type=ActionType.VIEW,
        resource_type=ResourceType.ANNOUNCEMENT,
        resource_id=resource_id,
        action_description=event_type,
        new_values={
            "mural_event": event_type,
            "announcement_id": resource_id,
        },
    )
    return jsonify({"status": "ok"})


@announcements_bp.route(
    "/announcements/<int:announcement_id>/log-status",
    methods=["GET"],
    endpoint="announcement_log_status",
)
@login_required
def announcement_log_status(announcement_id: int):
    """Return live log status (viewed/not viewed) for one announcement."""

    if not (is_user_admin(current_user) or getattr(current_user, "is_master", False)):
        abort(403)

    announcement = _apply_visibility_filter(
        Announcement.query.options(joinedload(Announcement.target_tags)).filter(
            Announcement.id == announcement_id
        )
    ).first()
    if not announcement:
        abort(404)

    viewed_map, not_viewed_map, totals = _build_announcement_view_status([announcement])
    viewed_logs = viewed_map.get(announcement_id, [])
    not_viewed_users = not_viewed_map.get(announcement_id, [])

    return jsonify(
        {
            "announcement_id": announcement_id,
            "viewed_total": totals.get(announcement_id, 0),
            "not_viewed_total": len(not_viewed_users),
            "viewed_logs": [
                {
                    "created_at": (
                        row.created_at.strftime("%d/%m/%Y %H:%M:%S")
                        if row.created_at
                        else "-"
                    ),
                    "name": (
                        row.user.name
                        if row.user and row.user.name
                        else row.username
                    ),
                    "username": (
                        row.user.username
                        if row.user and row.user.username
                        else row.username
                    ),
                    "ip_address": row.ip_address or "-",
                }
                for row in viewed_logs
            ],
            "not_viewed_users": [
                {
                    "name": user.name or user.username,
                    "username": user.username or "",
                }
                for user in not_viewed_users
            ],
        }
    )
