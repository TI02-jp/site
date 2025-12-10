"""Announcements blueprint and related helpers."""

from __future__ import annotations

import os
import re
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
from sqlalchemy import or_
from sqlalchemy.orm import joinedload
from werkzeug.utils import secure_filename

from app import db
from app.controllers.routes import (
    meeting_only_access_check,
    utc3_now,
)
from app.controllers.routes.blueprints.notifications import _invalidate_notification_cache
from app.forms import AnnouncementForm
from app.models.tables import (
    Announcement,
    AnnouncementAttachment,
    NotificationType,
    TaskNotification,
    User,
)
from app.utils.security import sanitize_html

announcements_bp = Blueprint("announcements", __name__)

ANNOUNCEMENTS_UPLOAD_SUBDIR = os.path.join("uploads", "announcements")


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

    cleaned = sanitize_html(raw_content or "")
    if not cleaned:
        return ""

    if not re.search(r"<[a-zA-Z/][^>]*>", cleaned):
        normalized = cleaned.replace("\r\n", "\n").replace("\r", "\n")
        return normalized.replace("\n", "<br>")

    return cleaned


def _broadcast_announcement_notification(announcement: Announcement) -> None:
    """Emit a notification about ``announcement`` for every active user."""

    active_user_rows = (
        User.query.with_entities(User.id)
        .filter(User.ativo.is_(True))
        .all()
    )
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


@announcements_bp.route(
    "/announcements", methods=["GET", "POST"], endpoint="announcements"
)
@login_required
@meeting_only_access_check
def announcements():
    """List internal announcements and allow admins to create new ones."""

    form = AnnouncementForm()

    search_term = (request.args.get("q") or "").strip()

    base_query = Announcement.query

    if search_term:
        ilike_pattern = f"%{search_term}%"
        base_query = base_query.filter(
            or_(
                Announcement.subject.ilike(ilike_pattern),
                Announcement.content.ilike(ilike_pattern),
            )
        )

    total_announcements = base_query.count()

    announcements_query = (
        base_query.options(
            joinedload(Announcement.created_by),
            joinedload(Announcement.attachments),
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
        if current_user.role != "admin":
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

            uploaded_files = [
                storage
                for storage in (form.attachments.data or [])
                if storage and storage.filename
            ]

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
    if current_user.role == "admin":
        for item in announcement_items:
            edit_form = AnnouncementForm(prefix=f"edit-{item.id}")
            edit_form.date.data = item.date
            edit_form.subject.data = item.subject
            edit_form.content.data = item.content
            edit_forms[item.id] = edit_form

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
    )


@announcements_bp.route(
    "/announcements/history", methods=["GET"], endpoint="announcement_history"
)
@login_required
def announcement_history():
    """Display the backlog of announcements that fall outside the main mural."""

    search_term = (request.args.get("q") or "").strip()

    recent_id_rows = (
        Announcement.query.with_entities(Announcement.id)
        .order_by(Announcement.date.desc(), Announcement.created_at.desc())
        .limit(6)
        .all()
    )
    recent_ids = [row[0] for row in recent_id_rows]

    base_query = Announcement.query

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

    total_history = base_query.count()

    # Add pagination to limit memory usage with concurrent users
    page = request.args.get("page", 1, type=int)
    per_page = 50  # Show 50 announcements per page

    announcements_query = (
        base_query.options(
            joinedload(Announcement.created_by),
            joinedload(Announcement.attachments),
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
    if current_user.role == "admin":
        for item in announcement_items:
            edit_form = AnnouncementForm(prefix=f"edit-{item.id}")
            edit_form.date.data = item.date
            edit_form.subject.data = item.subject
            edit_form.content.data = item.content
            edit_forms[item.id] = edit_form

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
    )


@announcements_bp.route(
    "/announcements/<int:announcement_id>/update",
    methods=["POST"],
    endpoint="update_announcement",
)
@login_required
def update_announcement(announcement_id: int):
    """Update an announcement's content and manage its attachments."""

    if current_user.role != "admin":
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

        new_files = [
            storage
            for storage in (form.attachments.data or [])
            if storage and storage.filename
        ]

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

    if current_user.role != "admin":
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

