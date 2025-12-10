"""
Blueprint para gestao da diretoria.

Este modulo contem rotas para acordos, feedbacks e eventos da diretoria.

Rotas:
    - GET/POST /diretoria/acordos: Lista e cria acordos
    - POST /diretoria/acordos/<id>/excluir: Exclui acordo
    - GET/POST /diretoria/feedbacks: Lista e cria feedbacks
    - POST /diretoria/feedbacks/<id>/excluir: Exclui feedback
    - GET/POST /diretoria/eventos: Lista e cria eventos
    - GET/POST /diretoria/eventos/<id>/editar: Edita evento
    - GET /diretoria/eventos/<id>/visualizar: Visualiza evento
    - GET /diretoria/eventos/lista: Lista eventos
    - POST /diretoria/eventos/<id>/excluir: Exclui evento

Dependencias:
    - models: DiretoriaEvent, DiretoriaAgreement, DiretoriaFeedback, User
    - forms: DiretoriaAcordoForm, DiretoriaFeedbackForm
    - services: email

Autor: Refatoracao automatizada
Data: 2024
"""

import os
from collections.abc import Iterable
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse
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
    session,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload
import sqlalchemy as sa

from app import db
from app.forms import DiretoriaAcordoForm, DiretoriaFeedbackForm
from app.controllers.routes._base import SAO_PAULO_TZ
from app.controllers.routes._decorators import meeting_only_access_check
from app.models.tables import DiretoriaEvent, DiretoriaAgreement, DiretoriaFeedback, User
from app.utils.mailer import send_email, EmailDeliveryError
from app.extensions.task_queue import submit_io_task
from app.utils.security import sanitize_html


# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

diretoria_bp = Blueprint('diretoria', __name__)


# =============================================================================
# CONSTANTES
# =============================================================================

EVENT_TYPE_LABELS = {
    "treinamento": "Treinamento",
    "data_comemorativa": "Data comemorativa",
    "evento": "Evento",
}

EVENT_AUDIENCE_LABELS = {
    "interno": "Interno",
    "externo": "Externo",
    "ambos": "Ambos",
}

EVENT_CATEGORY_LABELS = {
    "cafe": "Café da manhã",
    "almoco": "Almoço",
    "lanche": "Lanche",
    "outros": "Outros serviços",
}


# =============================================================================
# FUNCOES AUXILIARES
# =============================================================================

def user_has_tag(tag_name: str) -> bool:
    """
    Verifica se o usuario atual possui uma tag especifica.

    Args:
        tag_name: Nome da tag a verificar

    Returns:
        bool: True se o usuario possui a tag
    """
    if not current_user.is_authenticated:
        return False
    return any(tag.nome.lower() == tag_name.lower() for tag in current_user.tags)


def _normalize_photo_entry(value: str) -> str | None:
    """
    Retorna uma referencia de foto sanitizada ou None se invalida.

    Args:
        value: URL ou caminho da foto

    Returns:
        str | None: URL normalizada ou None
    """
    if not isinstance(value, str):
        return None

    trimmed = value.strip()
    if not trimmed:
        return None

    parsed = urlparse(trimmed)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc

    if scheme == "https" and netloc:
        return parsed.geturl()

    if scheme == "http" and netloc:
        static_path = parsed.path or ""
        if not static_path.startswith("/static/"):
            return None

        # Aceita esquema inseguro apenas quando direcionado ao host da aplicacao
        allowed_hosts: set[str] = set()
        if has_request_context():
            host = (request.host or "").lower()
            if host:
                allowed_hosts.add(host)
        server_name = (current_app.config.get("SERVER_NAME") or "").lower()
        if server_name:
            allowed_hosts.add(server_name)

        normalized_netloc = netloc.lower()
        if not allowed_hosts:
            return static_path if static_path.startswith("/static/uploads/") else None

        netloc_base = normalized_netloc.split(":", 1)[0]
        for allowed in allowed_hosts:
            if not allowed:
                continue
            allowed_base = allowed.split(":", 1)[0]
            if normalized_netloc == allowed or netloc_base == allowed_base:
                return static_path

        return None

    if scheme and scheme not in {"http", "https"}:
        return None

    if not parsed.scheme and not parsed.netloc:
        if trimmed.startswith("/"):
            return "/" + trimmed.lstrip("/")
        if trimmed.lower().startswith("static/"):
            return "/" + trimmed.lstrip("/")

    return None


def _resolve_local_photo_path(normalized_photo_url: str) -> str | None:
    """
    Retorna o caminho do sistema de arquivos para um upload armazenado em /static.

    Args:
        normalized_photo_url: URL normalizada da foto

    Returns:
        str | None: Caminho do arquivo ou None
    """
    parsed = urlparse(normalized_photo_url)
    path = parsed.path if parsed.scheme else normalized_photo_url
    if not path:
        return None

    relative_path = path.lstrip("/")
    if not relative_path.startswith("static/uploads/"):
        return None

    safe_relative = os.path.normpath(relative_path)
    if not safe_relative.startswith("static/uploads/"):
        return None

    return os.path.join(current_app.root_path, safe_relative)


def _format_event_timestamp(raw_dt: datetime | None) -> str:
    """
    Retorna um timestamp formatado em fuso horario de Sao Paulo para views da Diretoria JP.

    Args:
        raw_dt: Datetime a formatar

    Returns:
        str: Timestamp formatado ou "—"
    """
    if raw_dt is None:
        return "—"

    timestamp = raw_dt
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    localized = timestamp.astimezone(SAO_PAULO_TZ)
    return localized.strftime("%d/%m/%Y %H:%M")


def _cleanup_diretoria_photo_uploads(
    photo_urls: Iterable[str], *, exclude_event_id: int | None = None
) -> None:
    """
    Remove arquivos de fotos de eventos da Diretoria nao utilizados da pasta uploads.

    Args:
        photo_urls: URLs das fotos a verificar
        exclude_event_id: ID do evento a excluir da verificacao
    """
    normalized_to_path: dict[str, str] = {}
    for photo_url in photo_urls:
        normalized = _normalize_photo_entry(photo_url)
        if not normalized:
            continue

        file_path = _resolve_local_photo_path(normalized)
        if not file_path:
            continue

        normalized_to_path[normalized] = file_path

    if not normalized_to_path:
        return

    query = DiretoriaEvent.query
    if exclude_event_id is not None:
        query = query.filter(DiretoriaEvent.id != exclude_event_id)

    still_in_use: set[str] = set()
    for _, other_photos in query.with_entities(
        DiretoriaEvent.id, DiretoriaEvent.photos
    ):
        if not isinstance(other_photos, list):
            continue

        for other_photo in other_photos:
            normalized_other = _normalize_photo_entry(other_photo)
            if normalized_other in normalized_to_path:
                still_in_use.add(normalized_other)

        if len(still_in_use) == len(normalized_to_path):
            break

    for normalized, file_path in normalized_to_path.items():
        if normalized in still_in_use:
            continue

        if not os.path.exists(file_path):
            continue

        try:
            os.remove(file_path)
        except OSError:
            current_app.logger.warning(
                "Não foi possível remover o arquivo de foto não utilizado: %s",
                file_path,
                exc_info=True,
            )


def parse_diretoria_event_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """
    Valida e normaliza dados de evento da Diretoria JP enviados do formulario.

    Args:
        payload: Dados do formulario

    Returns:
        tuple: (dados_normalizados, lista_de_erros)
    """
    name = (payload.get("name") or "").strip()
    event_type = payload.get("type")
    date_raw = payload.get("date")
    description = (payload.get("description") or "").strip()
    audience = payload.get("audience")
    participants_raw = payload.get("participants")
    categories_payload = payload.get("categories") or {}
    photos_payload = payload.get("photos")

    errors: list[str] = []

    if not name:
        errors.append("Informe o nome do evento.")

    if event_type not in EVENT_TYPE_LABELS:
        errors.append("Selecione um tipo de evento válido.")

    try:
        event_date = datetime.strptime(str(date_raw), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        errors.append("Informe uma data válida para o evento.")
        event_date = None

    if audience not in EVENT_AUDIENCE_LABELS:
        errors.append("Selecione o público participante do evento.")

    try:
        participants = int(participants_raw)
        if participants < 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("Informe o número de participantes do evento.")
        participants = 0

    services_payload: dict[str, dict[str, object]] = {}
    total_event = Decimal("0.00")
    photos: list[str] = []

    if photos_payload is None:
        photos = []
    elif isinstance(photos_payload, list):
        seen_photos: set[str] = set()
        for entry in photos_payload:
            normalized_url = _normalize_photo_entry(entry)
            if not normalized_url or normalized_url in seen_photos:
                continue

            seen_photos.add(normalized_url)
            photos.append(normalized_url)
    else:
        errors.append("Formato inválido ao enviar as fotos do evento.")

    for key in EVENT_CATEGORY_LABELS:
        category_data = (
            categories_payload.get(key, {})
            if isinstance(categories_payload, dict)
            else {}
        )
        items_data = []
        if isinstance(category_data, dict):
            items_data = category_data.get("items", []) or []

        processed_items: list[dict[str, object]] = []
        category_total = Decimal("0.00")

        if isinstance(items_data, list):
            for item in items_data:
                if not isinstance(item, dict):
                    continue

                item_name = (item.get("name") or "").strip()
                if not item_name:
                    continue

                try:
                    quantity = Decimal(str(item.get("quantity", "0")))
                    unit_value = Decimal(str(item.get("unit_value", "0")))
                except (InvalidOperation, TypeError):
                    continue

                if quantity < 0 or unit_value < 0:
                    continue

                line_total = (quantity * unit_value).quantize(Decimal("0.01"))

                processed_items.append(
                    {
                        "name": item_name,
                        "quantity": float(quantity),
                        "unit_value": float(unit_value),
                        "total": float(line_total),
                    }
                )

                category_total += line_total

        category_total = category_total.quantize(Decimal("0.01"))
        services_payload[key] = {
            "items": processed_items,
            "total": float(category_total),
        }
        total_event += category_total

    total_event = total_event.quantize(Decimal("0.01"))

    normalized = {
        "name": name,
        "event_type": event_type,
        "event_date": event_date,
        "description": description or None,
        "audience": audience,
        "participants": participants,
        "services": services_payload,
        "total_cost": total_event,
        "photos": photos,
    }

    return normalized, errors


# =============================================================================
# ROTAS - ACORDOS
# =============================================================================

@diretoria_bp.route("/diretoria/acordos", methods=["GET", "POST"])
@login_required
@meeting_only_access_check
def diretoria_acordos():
    """
    Renderiza e gerencia acordos da Diretoria JP vinculados a usuarios do portal.

    GET: Exibe formulario e lista de acordos
    POST: Cria ou edita acordo
    """
    if current_user.role != "admin" and not user_has_tag("Diretoria"):
        abort(403)

    users = (
        User.query.filter(User.ativo.is_(True))
        .order_by(sa.func.lower(User.name))
        .all()
    )

    form = DiretoriaAcordoForm()

    search_query = (request.args.get("q", default="", type=str) or "").strip()

    if request.method == "POST":
        selected_user_id = request.form.get("user_id", type=int)
    else:
        selected_user_id = request.args.get("user_id", type=int)

    selected_user = None
    agreements: list[DiretoriaAgreement] = []
    active_agreement: DiretoriaAgreement | None = None
    search_results: list[DiretoriaAgreement] = []

    if selected_user_id:
        selected_user = (
            User.query.filter(
                User.id == selected_user_id,
                User.ativo.is_(True),
            ).first()
        )
        if not selected_user:
            message = "Usuário selecionado não foi encontrado ou está inativo."
            if request.method == "GET":
                flash(message, "warning")
                return redirect(url_for("diretoria.diretoria_acordos"))
            flash(message, "danger")
        else:
            agreements = (
                DiretoriaAgreement.query.filter_by(user_id=selected_user.id)
                .order_by(
                    DiretoriaAgreement.agreement_date.desc(),
                    DiretoriaAgreement.created_at.desc(),
                )
                .all()
            )
    agreement_entries: list[dict[str, Any]] = [
        {"record": agreement_item}
        for agreement_item in agreements
    ]

    search_entries: list[dict[str, Any]] = []
    if search_query:
        search_term = f"%{search_query}%"
        search_results = (
            DiretoriaAgreement.query.options(joinedload(DiretoriaAgreement.user))
            .join(User)
            .filter(
                User.ativo.is_(True),
                sa.or_(
                    DiretoriaAgreement.title.ilike(search_term),
                    DiretoriaAgreement.description.ilike(search_term),
                ),
            )
            .order_by(
                DiretoriaAgreement.agreement_date.desc(),
                DiretoriaAgreement.created_at.desc(),
            )
            .all()
        )

    for agreement_item in search_results:
        search_entries.append(
            {
                "record": agreement_item,
                "user": agreement_item.user,
            }
        )

    if request.method == "POST":
        form_mode = request.form.get("form_mode") or "new"
        if form_mode not in {"new", "edit"}:
            form_mode = "new"
        agreement_id = request.form.get("agreement_id", type=int)
    else:
        form_mode = request.args.get("action")
        if form_mode not in {"new", "edit"}:
            form_mode = None
        agreement_id = request.args.get("agreement_id", type=int)

    if request.method == "GET" and selected_user:
        if form_mode == "edit" and agreement_id:
            active_agreement = next(
                (item for item in agreements if item.id == agreement_id), None
            )
            if not active_agreement:
                active_agreement = DiretoriaAgreement.query.filter_by(
                    id=agreement_id,
                    user_id=selected_user.id,
                ).first()
            if not active_agreement:
                flash("O acordo selecionado não foi encontrado.", "warning")
                return redirect(
                    url_for("diretoria.diretoria_acordos", user_id=selected_user.id)
                )
            form.title.data = active_agreement.title
            form.agreement_date.data = active_agreement.agreement_date
            form.description.data = active_agreement.description
            form.notify_user.data = False
            form.notification_destination.data = "user"
            form.notification_email.data = ""
        elif form_mode == "new" and not form.agreement_date.data:
            form.agreement_date.data = date.today()
            form.notify_user.data = False
            form.notification_destination.data = "user"
            form.notification_email.data = ""

    if request.method == "POST":
        if not selected_user:
            if not selected_user_id:
                flash("Selecione um usuário para salvar o acordo.", "danger")
        else:
            if form_mode == "edit" and agreement_id:
                active_agreement = DiretoriaAgreement.query.filter_by(
                    id=agreement_id,
                    user_id=selected_user.id,
                ).first()
                if not active_agreement:
                    flash("O acordo selecionado não foi encontrado.", "warning")
                    return redirect(
                        url_for("diretoria.diretoria_acordos", user_id=selected_user.id)
                    )

            if form.validate_on_submit():
                cleaned_description = sanitize_html(form.description.data or "")

                if form_mode == "edit" and active_agreement:
                    active_agreement.title = form.title.data
                    active_agreement.agreement_date = form.agreement_date.data
                    active_agreement.description = cleaned_description
                    feedback_message = "Acordo atualizado com sucesso."
                else:
                    active_agreement = DiretoriaAgreement(
                        user=selected_user,
                        title=form.title.data,
                        agreement_date=form.agreement_date.data,
                        description=cleaned_description,
                    )
                    db.session.add(active_agreement)
                    feedback_message = "Acordo criado com sucesso."

                db.session.commit()

                recipient_email = None
                destination_value = form.notification_destination.data or "user"
                if form.notify_user.data:
                    if destination_value == "custom":
                        recipient_email = form.notification_email.data
                    else:
                        recipient_email = selected_user.email

                if form.notify_user.data and recipient_email:
                    try:
                        action_label = (
                            "atualizado" if form_mode == "edit" else "registrado"
                        )
                        email_html = render_template(
                            "emails/diretoria_acordo.html",
                            agreement=active_agreement,
                            destinatario=selected_user,
                            editor=current_user,
                            action_label=action_label,
                        )
                        submit_io_task(
                            send_email,
                            subject=f"[Diretoria JP] Acordo {active_agreement.title}",
                            html_body=email_html,
                            recipients=[recipient_email],
                        )
                        flash(
                            f"{feedback_message} Notificação enfileirada para envio por e-mail.",
                            "success",
                        )
                    except EmailDeliveryError as exc:
                        current_app.logger.error(
                            "Falha ao enviar e-mail do acordo %s para %s: %s",
                            active_agreement.id,
                            recipient_email,
                            exc,
                        )
                        flash(
                            f"{feedback_message} Porém, não foi possível enviar o e-mail de notificação.",
                            "warning",
                        )
                else:
                    flash(feedback_message, "success")

                return redirect(url_for("diretoria.diretoria_acordos", user_id=selected_user.id))

            flash("Por favor, corrija os erros do formulário.", "danger")

    return render_template(
        "diretoria/acordos.html",
        users=users,
        form=form,
        selected_user=selected_user,
        agreements=agreements,
        form_mode=form_mode,
        active_agreement=active_agreement,
        agreement_entries=agreement_entries,
        search_query=search_query,
        search_entries=search_entries,
    )


@diretoria_bp.route("/diretoria/acordos/<int:agreement_id>/excluir", methods=["POST"])
@login_required
def diretoria_acordos_excluir(agreement_id: int):
    """
    Remove um acordo vinculado a um usuario da Diretoria JP.

    Args:
        agreement_id: ID do acordo a remover
    """
    if current_user.role != "admin" and not (user_has_tag("Gestão") and user_has_tag("Diretoria")):
        abort(403)

    agreement = DiretoriaAgreement.query.get_or_404(agreement_id)

    redirect_user_id = request.form.get("user_id", type=int) or agreement.user_id
    if redirect_user_id != agreement.user_id:
        redirect_user_id = agreement.user_id

    db.session.delete(agreement)
    db.session.commit()

    flash("Acordo removido com sucesso.", "success")

    return redirect(url_for("diretoria.diretoria_acordos", user_id=redirect_user_id))


# =============================================================================
# ROTAS - FEEDBACKS
# =============================================================================

@diretoria_bp.route("/diretoria/feedbacks", methods=["GET", "POST"])
@login_required
@meeting_only_access_check
def diretoria_feedbacks():
    """
    Renderiza e gerencia feedbacks da Diretoria JP vinculados a usuarios do portal.

    GET: Exibe formulario e lista de feedbacks
    POST: Cria ou edita feedback
    """
    if current_user.role != "admin" and not user_has_tag("Diretoria"):
        abort(403)

    users = (
        User.query.filter(User.ativo.is_(True))
        .order_by(sa.func.lower(User.name))
        .all()
    )

    form = DiretoriaFeedbackForm()

    search_query = (request.args.get("q", default="", type=str) or "").strip()

    if request.method == "POST":
        selected_user_id = request.form.get("user_id", type=int)
    else:
        selected_user_id = request.args.get("user_id", type=int)

    selected_user = None
    feedbacks: list[DiretoriaFeedback] = []
    active_feedback: DiretoriaFeedback | None = None
    search_results: list[DiretoriaFeedback] = []

    if selected_user_id:
        selected_user = (
            User.query.filter(
                User.id == selected_user_id,
                User.ativo.is_(True),
            ).first()
        )
        if not selected_user:
            message = "Usuário selecionado não foi encontrado ou está inativo."
            if request.method == "GET":
                flash(message, "warning")
                return redirect(url_for("diretoria.diretoria_feedbacks"))
            flash(message, "danger")
        else:
            feedbacks = (
                DiretoriaFeedback.query.filter_by(user_id=selected_user.id)
                .order_by(
                    DiretoriaFeedback.feedback_date.desc(),
                    DiretoriaFeedback.created_at.desc(),
                )
                .all()
            )
    feedback_entries: list[dict[str, Any]] = [
        {"record": feedback_item}
        for feedback_item in feedbacks
    ]

    search_entries: list[dict[str, Any]] = []
    if search_query:
        search_term = f"%{search_query}%"
        search_results = (
            DiretoriaFeedback.query.options(joinedload(DiretoriaFeedback.user))
            .join(User)
            .filter(
                User.ativo.is_(True),
                sa.or_(
                    DiretoriaFeedback.title.ilike(search_term),
                    DiretoriaFeedback.description.ilike(search_term),
                ),
            )
            .order_by(
                DiretoriaFeedback.feedback_date.desc(),
                DiretoriaFeedback.created_at.desc(),
            )
            .all()
        )

    for feedback_item in search_results:
        search_entries.append(
            {
                "record": feedback_item,
                "user": feedback_item.user,
            }
        )

    if request.method == "POST":
        form_mode = request.form.get("form_mode") or "new"
        if form_mode not in {"new", "edit"}:
            form_mode = "new"
        feedback_id = request.form.get("feedback_id", type=int)
    else:
        form_mode = request.args.get("action")
        if form_mode not in {"new", "edit"}:
            form_mode = None
        feedback_id = request.args.get("feedback_id", type=int)

    if request.method == "GET" and selected_user:
        if form_mode == "edit" and feedback_id:
            active_feedback = next(
                (item for item in feedbacks if item.id == feedback_id), None
            )
            if not active_feedback:
                active_feedback = DiretoriaFeedback.query.filter_by(
                    id=feedback_id,
                    user_id=selected_user.id,
                ).first()
            if not active_feedback:
                flash("O feedback selecionado não foi encontrado.", "warning")
                return redirect(
                    url_for("diretoria.diretoria_feedbacks", user_id=selected_user.id)
                )
            form.title.data = active_feedback.title
            form.feedback_date.data = active_feedback.feedback_date
            form.description.data = active_feedback.description
            form.notify_user.data = False
            form.notification_destination.data = "user"
            form.notification_email.data = ""
        elif form_mode == "new" and not form.feedback_date.data:
            form.feedback_date.data = date.today()
            form.notify_user.data = False
            form.notification_destination.data = "user"
            form.notification_email.data = ""

    if request.method == "POST":
        if not selected_user:
            if not selected_user_id:
                flash("Selecione um usuário para salvar o feedback.", "danger")
        else:
            if form_mode == "edit" and feedback_id:
                active_feedback = DiretoriaFeedback.query.filter_by(
                    id=feedback_id,
                    user_id=selected_user.id,
                ).first()
                if not active_feedback:
                    flash("O feedback selecionado não foi encontrado.", "warning")
                    return redirect(
                        url_for("diretoria.diretoria_feedbacks", user_id=selected_user.id)
                    )

            if form.validate_on_submit():
                cleaned_description = sanitize_html(form.description.data or "")

                if form_mode == "edit" and active_feedback:
                    active_feedback.title = form.title.data
                    active_feedback.feedback_date = form.feedback_date.data
                    active_feedback.description = cleaned_description
                    feedback_message = "Feedback atualizado com sucesso."
                else:
                    active_feedback = DiretoriaFeedback(
                        user=selected_user,
                        title=form.title.data,
                        feedback_date=form.feedback_date.data,
                        description=cleaned_description,
                    )
                    db.session.add(active_feedback)
                    feedback_message = "Feedback criado com sucesso."

                db.session.commit()

                recipient_email = None
                destination_value = form.notification_destination.data or "user"
                if form.notify_user.data:
                    if destination_value == "custom":
                        recipient_email = form.notification_email.data
                    else:
                        recipient_email = selected_user.email

                if form.notify_user.data and recipient_email:
                    try:
                        action_label = (
                            "atualizado" if form_mode == "edit" else "registrado"
                        )
                        email_html = render_template(
                            "emails/diretoria_feedback.html",
                            feedback=active_feedback,
                            destinatario=selected_user,
                            editor=current_user,
                            action_label=action_label,
                        )
                        submit_io_task(
                            send_email,
                            subject=f"[Diretoria JP] Feedback {active_feedback.title}",
                            html_body=email_html,
                            recipients=[recipient_email],
                        )
                        flash(
                            f"{feedback_message} Notificação enfileirada para envio por e-mail.",
                            "success",
                        )
                    except EmailDeliveryError as exc:
                        current_app.logger.error(
                            "Falha ao enviar e-mail do feedback %s para %s: %s",
                            active_feedback.id,
                            recipient_email,
                            exc,
                        )
                        flash(
                            f"{feedback_message} Porém, não foi possível enviar o e-mail de notificação.",
                            "warning",
                        )
                else:
                    flash(feedback_message, "success")

                return redirect(url_for("diretoria.diretoria_feedbacks", user_id=selected_user.id))

            flash("Por favor, corrija os erros do formulário.", "danger")

    return render_template(
        "diretoria/feedbacks.html",
        users=users,
        form=form,
        selected_user=selected_user,
        feedbacks=feedbacks,
        form_mode=form_mode,
        active_feedback=active_feedback,
        feedback_entries=feedback_entries,
        search_query=search_query,
        search_entries=search_entries,
    )


@diretoria_bp.route("/diretoria/feedbacks/<int:feedback_id>/excluir", methods=["POST"])
@login_required
def diretoria_feedbacks_excluir(feedback_id: int):
    """
    Remove um feedback vinculado a um usuario da Diretoria JP.

    Args:
        feedback_id: ID do feedback a remover
    """
    if current_user.role != "admin" and not user_has_tag("Diretoria"):
        abort(403)

    feedback = DiretoriaFeedback.query.get_or_404(feedback_id)

    redirect_user_id = request.form.get("user_id", type=int) or feedback.user_id
    if redirect_user_id != feedback.user_id:
        redirect_user_id = feedback.user_id

    db.session.delete(feedback)
    db.session.commit()

    flash("Feedback removido com sucesso.", "success")

    return redirect(url_for("diretoria.diretoria_feedbacks", user_id=redirect_user_id))


# =============================================================================
# ROTAS - EVENTOS
# =============================================================================

@diretoria_bp.route("/diretoria/eventos", methods=["GET", "POST"])
@login_required
@meeting_only_access_check
def diretoria_eventos():
    """
    Renderiza ou persiste dados de planejamento de eventos da Diretoria JP.

    GET: Exibe formulario de criacao de evento
    POST: Cria novo evento
    """
    if current_user.role != "admin" and not (user_has_tag("Gestão") or user_has_tag("Diretoria")):
        abort(403)

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        normalized, errors = parse_diretoria_event_payload(payload)

        if errors:
            return jsonify({"errors": errors}), 400

        diretoria_event = DiretoriaEvent(
            **normalized,
            created_by=current_user,
        )

        db.session.add(diretoria_event)
        db.session.commit()

        session["diretoria_event_feedback"] = {
            "message": f'Evento "{diretoria_event.name}" salvo com sucesso.',
            "category": "success",
        }

        return (
            jsonify(
                {
                    "success": True,
                    "redirect_url": url_for("diretoria.diretoria_eventos_lista"),
                }
            ),
            201,
        )

    return render_template("diretoria/eventos.html")


@diretoria_bp.route("/diretoria/eventos/<int:event_id>/editar", methods=["GET", "POST"])
@login_required
def diretoria_eventos_editar(event_id: int):
    """
    Edita um evento existente da Diretoria JP.

    GET: Exibe formulario de edicao com dados do evento
    POST: Atualiza dados do evento

    Args:
        event_id: ID do evento a editar
    """
    if current_user.role != "admin" and not (user_has_tag("Gestão") or user_has_tag("Diretoria")):
        abort(403)

    event = DiretoriaEvent.query.get_or_404(event_id)

    if request.method == "POST":
        previous_photos: list[str] = []
        if isinstance(event.photos, list):
            for photo in event.photos:
                normalized_prev = _normalize_photo_entry(photo)
                if normalized_prev:
                    previous_photos.append(normalized_prev)

        payload = request.get_json(silent=True) or {}
        normalized, errors = parse_diretoria_event_payload(payload)

        if errors:
            return jsonify({"errors": errors}), 400

        if not normalized.get("event_date"):
            return jsonify({"errors": ["Informe uma data válida para o evento."]}), 400

        event.name = normalized["name"]
        event.event_type = normalized["event_type"]
        event.event_date = normalized["event_date"]
        event.description = normalized["description"]
        event.audience = normalized["audience"]
        event.participants = normalized["participants"]
        event.services = normalized["services"]
        event.total_cost = normalized["total_cost"]
        event.photos = normalized["photos"]

        db.session.commit()

        removed_photos = [
            photo for photo in previous_photos if photo not in event.photos
        ]
        _cleanup_diretoria_photo_uploads(removed_photos, exclude_event_id=event.id)

        session["diretoria_event_feedback"] = {
            "message": f'Evento "{event.name}" atualizado com sucesso.',
            "category": "success",
        }

        return jsonify(
            {
                "success": True,
                "redirect_url": url_for("diretoria.diretoria_eventos_lista"),
            }
        )

    sanitized_photos = []
    if isinstance(event.photos, list):
        for photo in event.photos:
            normalized = _normalize_photo_entry(photo)
            if normalized:
                sanitized_photos.append(normalized)

    event_payload = {
        "id": event.id,
        "name": event.name,
        "type": event.event_type,
        "date": event.event_date.strftime("%Y-%m-%d"),
        "description": event.description or "",
        "audience": event.audience,
        "participants": event.participants,
        "categories": event.services or {},
        "photos": sanitized_photos,
        "submit_url": url_for("diretoria.diretoria_eventos_editar", event_id=event.id),
    }

    return render_template("diretoria/eventos.html", event_data=event_payload)


@diretoria_bp.route("/diretoria/eventos/<int:event_id>/visualizar")
@login_required
def diretoria_eventos_visualizar(event_id: int):
    """
    Exibe os detalhes de um evento da Diretoria JP sem edicao.

    Args:
        event_id: ID do evento a visualizar
    """
    if current_user.role != "admin" and not (user_has_tag("Gestão") or user_has_tag("Diretoria")):
        abort(403)

    event = DiretoriaEvent.query.options(joinedload(DiretoriaEvent.created_by)).get_or_404(
        event_id
    )

    services = event.services if isinstance(event.services, dict) else {}

    def to_decimal(value: Any) -> Decimal | None:
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError):
            return None

    categories: list[dict[str, Any]] = []
    for key, label in EVENT_CATEGORY_LABELS.items():
        raw_category = services.get(key, {}) if isinstance(services, dict) else {}
        raw_items = raw_category.get("items", []) if isinstance(raw_category, dict) else []
        category_total = to_decimal(raw_category.get("total") if isinstance(raw_category, dict) else None)

        processed_items: list[dict[str, Any]] = []
        if isinstance(raw_items, list):
            for item in raw_items:
                if not isinstance(item, dict):
                    continue

                item_name = (item.get("name") or "").strip()
                if not item_name:
                    continue

                processed_items.append(
                    {
                        "name": item_name,
                        "quantity": to_decimal(item.get("quantity")),
                        "unit_value": to_decimal(item.get("unit_value")),
                        "total": to_decimal(item.get("total")),
                    }
                )

        categories.append(
            {
                "key": key,
                "label": label,
                "entries": processed_items,
                "total": category_total or Decimal("0.00"),
            }
        )

    photo_urls = []
    if isinstance(event.photos, list):
        for photo in event.photos:
            normalized = _normalize_photo_entry(photo)
            if normalized:
                photo_urls.append(normalized)

    return render_template(
        "diretoria/evento_visualizar.html",
        event=event,
        categories=categories,
        photos=photo_urls,
        event_type_labels=EVENT_TYPE_LABELS,
        audience_labels=EVENT_AUDIENCE_LABELS,
        updated_at_display=_format_event_timestamp(event.updated_at),
    )


@diretoria_bp.route("/diretoria/eventos/lista")
@login_required
def diretoria_eventos_lista():
    """
    Exibe eventos salvos da Diretoria JP com suporte a busca.
    """
    if current_user.role != "admin" and not (user_has_tag("Gestão") or user_has_tag("Diretoria")):
        abort(403)

    search_query = (request.args.get("q") or "").strip()

    events_query = DiretoriaEvent.query
    if search_query:
        events_query = events_query.filter(
            DiretoriaEvent.name.ilike(f"%{search_query}%")
        )

    events = (
        events_query.order_by(
            DiretoriaEvent.event_date.desc(), DiretoriaEvent.created_at.desc()
        ).all()
    )

    feedback = session.pop("diretoria_event_feedback", None)
    if isinstance(feedback, dict) and feedback.get("message"):
        flash(feedback["message"], feedback.get("category", "success"))

    return render_template(
        "diretoria/eventos_lista.html",
        events=events,
        search_query=search_query,
        event_type_labels=EVENT_TYPE_LABELS,
        audience_labels=EVENT_AUDIENCE_LABELS,
        category_labels=EVENT_CATEGORY_LABELS,
    )


@diretoria_bp.route("/diretoria/eventos/<int:event_id>/excluir", methods=["POST"])
@login_required
def diretoria_eventos_excluir(event_id: int):
    """
    Remove um evento da Diretoria JP.

    Args:
        event_id: ID do evento a remover
    """
    if current_user.role != "admin" and not (user_has_tag("Gestão") or user_has_tag("Diretoria")):
        abort(403)

    event = DiretoriaEvent.query.get_or_404(event_id)
    event_name = event.name
    event_photos: list[str] = []
    if isinstance(event.photos, list):
        for photo in event.photos:
            normalized_photo = _normalize_photo_entry(photo)
            if normalized_photo:
                event_photos.append(normalized_photo)

    db.session.delete(event)
    db.session.commit()

    _cleanup_diretoria_photo_uploads(event_photos)

    flash(f'Evento "{event_name}" removido com sucesso.', "success")

    return redirect(url_for("diretoria.diretoria_eventos_lista"))


# =============================================================================
# ALIASES PARA COMPATIBILIDADE
# =============================================================================

# Nota: Endpoints registrados como diretoria.*
# Para compatibilidade, registrar aliases no __init__.py se necessario
