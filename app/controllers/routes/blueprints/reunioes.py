"""
Blueprint para sala de reunioes.

Este modulo contem rotas para gestao de reunioes internas com
integracao ao Google Calendar e Meet.

Rotas:
    - GET/POST /sala-reunioes: Lista e cria reunioes
    - POST /reuniao/<id>/meet-config: Configura Google Meet
    - POST /reuniao/<id>/status: Atualiza status da reuniao
    - POST /reuniao/<id>/pautas: Registra pautas da reuniao
    - POST /reuniao/<id>/delete: Exclui reuniao

Dependencias:
    - models: Reuniao, ReuniaoStatus, User
    - forms: MeetingForm, MeetConfigurationForm
    - services: meeting_room, google_calendar, calendar_cache

Autor: Refatoracao automatizada
Data: 2024-12
"""

from datetime import datetime
from typing import Any

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required

from app import db, limiter
from app.forms import MeetConfigurationForm, MeetingForm
from app.models.tables import Reuniao, ReuniaoStatus, default_meet_settings
from app.services.google_calendar import get_calendar_timezone
from app.services.meeting_room import (
    MeetingStatusConflictError,
    RESCHEDULE_REQUIRED_STATUSES,
    STATUS_SEQUENCE,
    change_meeting_status,
    create_meeting_and_event,
    delete_meeting,
    fetch_raw_events,
    get_status_label,
    populate_participants_choices,
    serialize_meeting_event,
    update_meeting,
    update_meeting_configuration,
    invalidate_calendar_cache,
)
from app.utils.permissions import is_user_admin


# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

reunioes_bp = Blueprint('reunioes', __name__)


# =============================================================================
# FUNÇÕES AUXILIARES
# =============================================================================

def _meeting_host_candidates(meeting: Reuniao) -> tuple[list[dict[str, Any]], str]:
    """Return possible Meet host options alongside the creator's name."""

    candidates: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for participant in meeting.participantes:
        participant_id = participant.id_usuario
        if participant_id in seen_ids:
            continue
        seen_ids.add(participant_id)
        name = (
            participant.usuario.name
            if participant.usuario and participant.usuario.name
            else participant.username_usuario
        )
        candidates.append(
            {
                "id": participant_id,
                "name": name,
                "username": participant.username_usuario,
                "email": participant.usuario.email if participant.usuario else None,
            }
        )
    creator_obj = meeting.criador
    creator_name = (
        creator_obj.name
        if creator_obj and creator_obj.name
        else (creator_obj.username if creator_obj else "")
    )
    candidates.sort(key=lambda entry: (entry["name"] or "").lower())
    return candidates, creator_name


# =============================================================================
# ROTAS
# =============================================================================

@reunioes_bp.route("/sala-reunioes", methods=["GET", "POST"])
@login_required
@limiter.limit("10 per minute", methods=["GET"])  # Previne abuse de refresh/múltiplos cliques
def sala_reunioes():
    """List and create meetings using Google Calendar."""
    form = MeetingForm()
    meet_config_form = MeetConfigurationForm()
    populate_participants_choices(form)
    show_modal = False
    prefill_from_course = request.method == "GET" and request.args.get("course_calendar") == "1"
    if prefill_from_course:
        subject = (request.args.get("subject") or "").strip()
        if subject:
            form.subject.data = subject
        description = (request.args.get("description") or "").strip()
        if description:
            form.description.data = description
        date_raw = request.args.get("date")
        if date_raw:
            try:
                form.date.data = datetime.strptime(date_raw, "%Y-%m-%d").date()
            except ValueError:
                pass
        start_raw = request.args.get("start")
        if start_raw:
            try:
                form.start_time.data = datetime.strptime(start_raw, "%H:%M").time()
            except ValueError:
                pass
        end_raw = request.args.get("end")
        if end_raw:
            try:
                form.end_time.data = datetime.strptime(end_raw, "%H:%M").time()
            except ValueError:
                pass
        participant_ids: list[int] = []
        for raw_id in request.args.getlist("participants"):
            try:
                parsed_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if parsed_id in participant_ids:
                continue
            if any(choice_id == parsed_id for choice_id, _ in form.participants.choices):
                participant_ids.append(parsed_id)
        if participant_ids:
            form.participants.data = participant_ids
        if hasattr(form, "apply_more_days"):
            form.apply_more_days.data = False
        form.notify_attendees.data = True
        form.course_id.data = request.args.get("course_id", "")
        show_modal = True
        flash(
            "Revise os dados do curso, ajuste se necessário e confirme o agendamento da reunião.",
            "info",
        )
    raw_events = fetch_raw_events()
    calendar_tz = get_calendar_timezone()
    now = datetime.now(calendar_tz)
    is_creating_meeting_request = (
        request.method == "POST"
        and not form.meeting_id.data
        and bool(form.submit.data)
    )
    if is_creating_meeting_request:
        flash(
            "Estamos criando sua reunião. Aguarde alguns instantes enquanto finalizamos o agendamento.",
            "info",
        )

    if form.validate_on_submit():
        if form.meeting_id.data:
            meeting = Reuniao.query.get(int(form.meeting_id.data))
            # Admins (incluindo admin_master) podem editar qualquer reunião
            # Criadores só podem editar suas próprias reuniões
            can_edit_meeting = is_user_admin(current_user) or meeting.criador_id == current_user.id
            if meeting and can_edit_meeting:
                # Admins podem editar qualquer status; criadores têm restrições
                if not is_user_admin(current_user) and meeting.status in (
                    ReuniaoStatus.EM_ANDAMENTO,
                    ReuniaoStatus.REALIZADA,
                    ReuniaoStatus.CANCELADA,
                ):
                    flash(
                        "Reuniões em andamento, realizadas ou canceladas não podem ser editadas.",
                        "danger",
                    )
                    return redirect(url_for("reunioes.sala_reunioes"))
                success, operation = update_meeting(form, raw_events, now, meeting)
                if success:
                    if operation and operation.meet_link:
                        session["meet_popup"] = {
                            "meeting_id": operation.meeting_id,
                            "meet_link": operation.meet_link,
                        }
                    return redirect(url_for("reunioes.sala_reunioes"))
                show_modal = True
            else:
                flash(
                    "Você só pode editar reuniões que você criou.",
                    "danger",
                )
        else:
            success, operation = create_meeting_and_event(
                form, raw_events, now, current_user.id
            )
            if success:
                if operation and operation.meet_link:
                    session["meet_popup"] = {
                        "meeting_id": operation.meeting_id,
                        "meet_link": operation.meet_link,
                    }
                return redirect(url_for("reunioes.sala_reunioes"))
            show_modal = True
    if request.method == "POST":
        show_modal = True
    meet_popup_payload = session.pop("meet_popup", None)
    meet_popup_data: dict[str, Any] | None = None
    if meet_popup_payload:
        meeting_id = meet_popup_payload.get("meeting_id")
        meet_link = meet_popup_payload.get("meet_link")
        if meeting_id and meet_link:
            meeting = Reuniao.query.get(meeting_id)
            if meeting and meeting.meet_link == meet_link:
                can_configure = (
                    is_user_admin(current_user)
                    or meeting.criador_id == current_user.id
                )
                participant_options, creator_name = _meeting_host_candidates(meeting)
                settings_dict = dict(meeting.meet_settings or default_meet_settings())
                meet_popup_data = {
                    "meeting_id": meeting.id,
                    "meet_link": meet_link,
                    "subject": meeting.assunto,
                    "host_candidates": participant_options,
                    "current_host_id": meeting.meet_host_id,
                    "meet_settings": settings_dict,
                    "creator_name": creator_name,
                    "can_configure": can_configure,
                }
    status_options = [
        {
            "value": status.value,
            "label": get_status_label(status),
        }
        for status in STATUS_SEQUENCE
    ]
    reschedule_statuses = [status.value for status in RESCHEDULE_REQUIRED_STATUSES]
    from datetime import date
    today_date = date.today().isoformat()
    return render_template(
        "sala_reunioes.html",
        form=form,
        meet_config_form=meet_config_form,
        show_modal=show_modal,
        calendar_timezone=calendar_tz.key,
        meet_popup_data=meet_popup_data,
        meeting_status_options=status_options,
        reschedule_statuses=reschedule_statuses,
        today_date=today_date,
    )

@reunioes_bp.route("/reuniao/<int:meeting_id>/meet-config", methods=["POST"])
@login_required
def configure_meet_call(meeting_id: int):
    """Persist configuration options for the Google Meet room."""

    meeting = Reuniao.query.get_or_404(meeting_id)
    # Admins (incluindo admin_master) ou criadores podem configurar o Meet
    if not is_user_admin(current_user) and meeting.criador_id != current_user.id:
        abort(403)
    form = MeetConfigurationForm()
    candidate_entries, creator_name = _meeting_host_candidates(meeting)
    host_choices = [(entry["id"], entry["name"]) for entry in candidate_entries]
    form.host_id.choices = host_choices
    form.meeting_id.data = str(meeting.id)
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if form.validate_on_submit():
        try:
            submitted_meeting_id = int(form.meeting_id.data)
        except (TypeError, ValueError):
            submitted_meeting_id = None
        if submitted_meeting_id != meeting.id:
            error_message = "Reunião inválida para configurar o Meet."
            form.meeting_id.errors.append(error_message)
        else:
            selected_host_raw = form.host_id.data
            allowed_host_ids = {choice for choice, _ in host_choices}
            if not allowed_host_ids:
                form.host_id.errors.append(
                    "Adicione participantes à reunião antes de definir o proprietário."
                )
            elif selected_host_raw not in allowed_host_ids:
                form.host_id.errors.append("Selecione um proprietário válido para o Meet.")
            else:
                host_id = selected_host_raw
                settings_payload = {
                    "quick_access_enabled": form.quick_access_enabled.data,
                    "mute_on_join": form.mute_on_join.data,
                    "allow_chat": form.allow_chat.data,
                    "allow_screen_share": form.allow_screen_share.data,
                }
                try:
                    (
                        normalized_settings,
                        host,
                        sync_result,
                    ) = update_meeting_configuration(
                        meeting, host_id, settings_payload
                    )
                except ValueError as exc:
                    form.host_id.errors.append(str(exc))
                else:
                    host_name = (
                        host.name or host.username
                        if host
                        else ""
                    )
                    warning_message = None
                    if sync_result is False:
                        warning_message = (
                            "Nao foi possivel aplicar as configuracoes do Meet automaticamente. "
                            "Verifique manualmente na sala do Google Meet."
                        )
                    elif sync_result is None and meeting.meet_link:
                        warning_message = (
                            "As configuracoes do Meet estao sendo aplicadas em segundo plano. "
                            "Verifique a sala do Google Meet em alguns instantes."
                        )
                    response_payload = {
                        "success": True,
                        "message": "Configuracoes do Meet atualizadas com sucesso!",
                        "meet_settings": normalized_settings,
                        "meet_host": {
                            "id": host.id if host else None,
                            "name": host_name,
                        },
                        "meet_settings_applied": bool(sync_result) or meeting.meet_link is None,
                        "meet_settings_pending": sync_result is None and meeting.meet_link is not None,
                    }
                    if warning_message:
                        response_payload["warning"] = warning_message
                    if is_ajax:
                        return jsonify(response_payload)
                    if warning_message:
                        flash(warning_message, "warning")
                    flash(response_payload["message"], "success")
                    return redirect(url_for("reunioes.sala_reunioes"))

    if is_ajax:
        return jsonify({"success": False, "errors": form.errors}), 400
    for field_errors in form.errors.values():
        for error in field_errors:
            flash(error, "danger")
    return redirect(url_for("reunioes.sala_reunioes"))

@reunioes_bp.route("/reuniao/<int:meeting_id>/status", methods=["POST"])
@login_required
def update_meeting_status_route(meeting_id: int):
    """Allow creators or admins to change the meeting status."""

    meeting = Reuniao.query.get_or_404(meeting_id)
    # Admins (incluindo admin_master) ou criadores podem mudar o status
    if not is_user_admin(current_user) and meeting.criador_id != current_user.id:
        abort(403)
    payload = request.get_json(silent=True) or {}
    status_raw = payload.get("status")
    notify_attendees = bool(payload.get("notify_attendees"))
    if not status_raw:
        return (
            jsonify({"success": False, "error": "Selecione um status para atualizar a reunião."}),
            400,
        )
    try:
        new_status = ReuniaoStatus(status_raw)
    except ValueError:
        return (
            jsonify({"success": False, "error": "Status inválido para a reunião."}),
            400,
        )
    calendar_tz = get_calendar_timezone()
    now = datetime.now(calendar_tz)
    new_start: datetime | None = None
    new_end: datetime | None = None
    if new_status == ReuniaoStatus.ADIADA:
        date_raw = payload.get("date")
        start_raw = payload.get("start_time")
        end_raw = payload.get("end_time")
        if not (date_raw and start_raw and end_raw):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Informe a nova data e horário para adiar a reunião.",
                    }
                ),
                400,
            )
        try:
            parsed_date = datetime.strptime(date_raw, "%Y-%m-%d").date()
            start_time = datetime.strptime(start_raw, "%H:%M").time()
            end_time = datetime.strptime(end_raw, "%H:%M").time()
        except (TypeError, ValueError):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Data ou horário inválido para adiar a reunião.",
                    }
                ),
                400,
            )
        new_start = datetime.combine(parsed_date, start_time, tzinfo=calendar_tz)
        new_end = datetime.combine(parsed_date, end_time, tzinfo=calendar_tz)
    try:
        event_payload = change_meeting_status(
            meeting,
            new_status,
            current_user.id,
            is_user_admin(current_user),
            new_start=new_start,
            new_end=new_end,
            notify_attendees=notify_attendees,
            now=now,
        )
    except MeetingStatusConflictError as exc:
        message = " ".join(exc.messages) if getattr(exc, "messages", None) else None
        return (
            jsonify(
                {
                    "success": False,
                    "error": message or "Horário indisponível para adiar a reunião.",
                }
            ),
            400,
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception:
        current_app.logger.exception(
            "Não foi possível atualizar o status da reunião %s", meeting_id
        )
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Não foi possível atualizar o status da reunião.",
                }
            ),
            500,
        )
    message = f"Status atualizado para {get_status_label(new_status)}."
    return jsonify({"success": True, "event": event_payload, "message": message})

@reunioes_bp.route("/reuniao/<int:meeting_id>/pautas", methods=["POST"])
@login_required
def update_meeting_pautas(meeting_id: int):
    """Persist meeting notes once the meeting has been completed."""

    meeting = Reuniao.query.get_or_404(meeting_id)
    # Admins (incluindo admin_master) ou criadores podem atualizar pautas
    user_is_admin = is_user_admin(current_user)
    if not user_is_admin and meeting.criador_id != current_user.id:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Voce nao tem permissao para atualizar as pautas desta reuniao.",
                }
            ),
            403,
        )
    if meeting.status != ReuniaoStatus.REALIZADA:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "As pautas so podem ser registradas apos a reuniao ser finalizada.",
                }
            ),
            400,
        )
    payload = request.get_json(silent=True) or {}
    pautas_raw = payload.get("pautas", "")
    if not isinstance(pautas_raw, str):
        return (
            jsonify(
                {"success": False, "error": "Informe um texto valido para as pautas."}
            ),
            400,
        )
    pautas_text = pautas_raw.strip()
    if len(pautas_text) > 5000:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "As pautas podem ter no maximo 5.000 caracteres.",
                }
            ),
            400,
        )
    meeting.pautas = pautas_text or None
    db.session.add(meeting)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Falha ao salvar pautas da reuniao", extra={"meeting_id": meeting_id}
        )
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Nao foi possivel salvar as pautas da reuniao.",
                }
            ),
            500,
        )
    invalidate_calendar_cache()
    calendar_tz = get_calendar_timezone()
    now = datetime.now(calendar_tz)
    event_data, _ = serialize_meeting_event(
        meeting,
        now,
        current_user.id,
        user_is_admin,
        auto_progress=False,
    )
    return jsonify(
        {
            "success": True,
            "pautas": meeting.pautas or "",
            "event": event_data,
        }
    )

@reunioes_bp.route("/reuniao/<int:meeting_id>/delete", methods=["POST"])
@login_required
def delete_reuniao(meeting_id):
    """Delete a meeting and its corresponding Google Calendar event."""
    meeting = Reuniao.query.get_or_404(meeting_id)
    # Admins (incluindo admin_master) podem excluir qualquer reunião
    if not is_user_admin(current_user):
        if meeting.criador_id != current_user.id:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "error": "Você só pode excluir reuniões que você criou."}), 403
            flash("Você só pode excluir reuniões que você criou.", "danger")
            return redirect(url_for("reunioes.sala_reunioes"))
        if meeting.status in (
            ReuniaoStatus.EM_ANDAMENTO,
            ReuniaoStatus.REALIZADA,
        ):
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "error": "Reuniões em andamento ou realizadas não podem ser excluídas."}), 400
            flash(
                "Reuniões em andamento ou realizadas não podem ser excluídas.",
                "danger",
            )
            return redirect(url_for("reunioes.sala_reunioes"))

    success = delete_meeting(meeting)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        if success:
            return jsonify({"success": True, "message": "Reunião excluída com sucesso!"}), 200
        else:
            return jsonify({"success": False, "error": "Não foi possível remover o evento do Google Calendar."}), 500

    if success:
        flash("Reunião excluída com sucesso!", "success")
    else:
        flash("Não foi possível remover o evento do Google Calendar.", "danger")
    return redirect(url_for("reunioes.sala_reunioes"))
