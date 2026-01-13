"""
Blueprint para acompanhar comunicados enviados aos clientes.

Rotas:
    - GET/POST /comunicados-clientes: painel administrativo de comunicados
"""

import os
from mimetypes import guess_type
from uuid import uuid4

from flask import Blueprint, flash, redirect, render_template, url_for, current_app, request
from flask_login import current_user
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from werkzeug.utils import secure_filename

from app import db
from app.controllers.routes._decorators import admin_required
from app.forms import ClientAnnouncementForm
from app.models.tables import ClientAnnouncement, ClientAnnouncementAttachment


comunicados_clientes_bp = Blueprint("comunicados_clientes", __name__)

CLIENT_ANNOUNCEMENTS_UPLOAD_SUBDIR = os.path.join("uploads", "client_announcements")


def _get_next_sequence_number() -> int:
    last_number = db.session.query(func.max(ClientAnnouncement.sequence_number)).scalar()
    return (last_number or 0) + 1


def _remove_client_announcement_attachment(attachment_path: str | None) -> None:
    if not attachment_path:
        return
    static_root = os.path.join(current_app.root_path, "static")
    file_path = os.path.join(static_root, attachment_path)
    try:
        os.remove(file_path)
    except FileNotFoundError:
        return


def _remove_client_announcement_attachments(paths: list[str]) -> None:
    seen: set[str] = set()
    for path in paths:
        if not path:
            continue
        normalized = path.replace("\\", "/")
        if normalized in seen:
            continue
        seen.add(normalized)
        _remove_client_announcement_attachment(normalized)


def _save_client_announcement_file(uploaded_file) -> dict[str, str | None]:
    original_name = secure_filename(uploaded_file.filename or "")
    extension = os.path.splitext(original_name)[1].lower()
    unique_name = f"{uuid4().hex}{extension}"

    upload_directory = os.path.join(
        current_app.root_path, "static", CLIENT_ANNOUNCEMENTS_UPLOAD_SUBDIR
    )
    os.makedirs(upload_directory, exist_ok=True)

    stored_path = os.path.join(upload_directory, unique_name)
    uploaded_file.save(stored_path)

    relative_path = os.path.join(CLIENT_ANNOUNCEMENTS_UPLOAD_SUBDIR, unique_name).replace(
        "\\", "/"
    )
    mime_type = uploaded_file.mimetype or guess_type(original_name)[0]

    return {
        "path": relative_path,
        "name": original_name or None,
        "mime_type": mime_type,
    }


def _collect_uploaded_files(form: ClientAnnouncementForm) -> list:
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


@comunicados_clientes_bp.route("/comunicados-clientes", methods=["GET", "POST"])
@admin_required
def comunicados_clientes():
    form = ClientAnnouncementForm()
    allowed_tributacoes = {value for value, _ in form.tax_regime.choices}
    if request.args.get("clear_tributacao"):
        tributacao_filters: list[str] = []
    else:
        tributacao_filters = [
            value
            for value in request.args.getlist("tributacao")
            if value in allowed_tributacoes
        ]

    if form.validate_on_submit():
        next_number = _get_next_sequence_number()
        code_value = (form.code.data or "").strip() or None
        status_value = (form.status.data or "Aguardando Envio").strip() or "Aguardando Envio"
        entry = ClientAnnouncement(
            sequence_number=next_number,
            code=code_value,
            status=status_value,
            subject=form.subject.data or "",
            tax_regime=form.tax_regime.data or "",
            summary=form.summary.data or "",
            created_by_id=current_user.id,
        )
        db.session.add(entry)
        db.session.flush()

        saved_paths: list[str] = []
        uploaded_files = _collect_uploaded_files(form)
        for uploaded_file in uploaded_files:
            saved = _save_client_announcement_file(uploaded_file)
            if saved["path"]:
                saved_paths.append(saved["path"])
            db.session.add(
                ClientAnnouncementAttachment(
                    client_announcement=entry,
                    file_path=saved["path"],
                    original_name=saved["name"],
                    mime_type=saved["mime_type"],
                )
            )
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            if saved_paths:
                _remove_client_announcement_attachments(saved_paths)
            flash(
                "Nao foi possivel registrar o comunicado. Atualize a pagina e tente novamente.",
                "warning",
            )
        else:
            flash("Comunicado registrado com sucesso.", "success")
            return redirect(url_for("comunicados_clientes"))

    entries_query = ClientAnnouncement.query.options(
        selectinload(ClientAnnouncement.attachments)
    )
    if tributacao_filters:
        entries_query = entries_query.filter(
            ClientAnnouncement.tax_regime.in_(tributacao_filters)
        )
    entries = entries_query.order_by(ClientAnnouncement.sequence_number.desc()).all()
    edit_forms: dict[int, ClientAnnouncementForm] = {}
    for item in entries:
        edit_form = ClientAnnouncementForm(prefix=f"edit-{item.id}")
        edit_form.code.data = item.code
        edit_form.status.data = item.status or "Aguardando Envio"
        edit_form.subject.data = item.subject
        edit_form.tax_regime.data = item.tax_regime
        edit_form.summary.data = item.summary
        edit_forms[item.id] = edit_form
    return render_template(
        "admin/comunicados_clientes.html",
        form=form,
        entries=entries,
        edit_forms=edit_forms,
        tributacao_filters=tributacao_filters,
    )


@comunicados_clientes_bp.route(
    "/comunicados-clientes/<int:announcement_id>/update",
    methods=["POST"],
    endpoint="comunicados_clientes_update",
)
@admin_required
def update_comunicado_cliente(announcement_id: int):
    entry = ClientAnnouncement.query.options(
        selectinload(ClientAnnouncement.attachments)
    ).get_or_404(announcement_id)

    form = ClientAnnouncementForm(prefix=f"edit-{announcement_id}")

    if form.validate_on_submit():
        entry.code = (form.code.data or "").strip() or None
        entry.status = (form.status.data or "Aguardando Envio").strip() or "Aguardando Envio"
        entry.subject = form.subject.data or ""
        entry.tax_regime = form.tax_regime.data or ""
        entry.summary = form.summary.data or ""

        remove_ids = {
            int(attachment_id)
            for attachment_id in request.form.getlist("remove_attachment_ids")
            if attachment_id.isdigit()
        }

        if remove_ids:
            attachments_to_remove = [
                attachment
                for attachment in entry.attachments
                if attachment.id in remove_ids
            ]
            for attachment in attachments_to_remove:
                _remove_client_announcement_attachment(attachment.file_path)
                db.session.delete(attachment)

        saved_paths: list[str] = []
        uploaded_files = _collect_uploaded_files(form)
        for uploaded_file in uploaded_files:
            saved = _save_client_announcement_file(uploaded_file)
            if saved["path"]:
                saved_paths.append(saved["path"])
            db.session.add(
                ClientAnnouncementAttachment(
                    client_announcement=entry,
                    file_path=saved["path"],
                    original_name=saved["name"],
                    mime_type=saved["mime_type"],
                )
            )

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            if saved_paths:
                _remove_client_announcement_attachments(saved_paths)
            flash(
                "Nao foi possivel atualizar o comunicado. Verifique os dados informados.",
                "warning",
            )
        else:
            flash("Comunicado atualizado com sucesso.", "success")
        return redirect(url_for("comunicados_clientes"))

    flash(
        "Nao foi possivel atualizar o comunicado. Verifique os dados informados.",
        "warning",
    )
    return redirect(url_for("comunicados_clientes"))


@comunicados_clientes_bp.route(
    "/comunicados-clientes/<int:announcement_id>/delete",
    methods=["POST"],
    endpoint="comunicados_clientes_delete",
)
@admin_required
def delete_comunicado_cliente(announcement_id: int):
    entry = ClientAnnouncement.query.options(
        selectinload(ClientAnnouncement.attachments)
    ).get_or_404(announcement_id)
    attachment_paths = [
        attachment.file_path
        for attachment in entry.attachments
        if attachment.file_path
    ]

    try:
        db.session.delete(entry)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash(
            "Nao foi possivel remover o comunicado. Tente novamente.",
            "warning",
        )
        return redirect(url_for("comunicados_clientes"))

    if attachment_paths:
        _remove_client_announcement_attachments(attachment_paths)

    flash("Comunicado removido com sucesso.", "success")
    return redirect(url_for("comunicados_clientes"))
