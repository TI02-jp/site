"""Helpers for meeting room scheduling logic."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Sequence
from flask import flash, current_app
from markupsafe import Markup
from dateutil.parser import isoparse

from app import db
from app.models.tables import (
    User,
    Reuniao,
    ReuniaoParticipante,
    ReuniaoStatus,
    ReuniaoRecorrenciaTipo,
    default_meet_settings,
)
from app.services.meeting_recurrence import (
    generate_recurrence_dates,
    generate_recurrence_group_id,
)
from app.services.google_calendar import (
    list_upcoming_events,
    create_meet_event,
    create_event,
    update_event,
    delete_event,
    get_calendar_timezone,
    update_meet_space_preferences,
)
from app.services.calendar_cache import calendar_cache

# Lazy-load calendar timezone to avoid API call at module import
_CALENDAR_TZ = None

def get_calendar_tz():
    """Get calendar timezone with lazy initialization."""
    global _CALENDAR_TZ
    if _CALENDAR_TZ is None:
        _CALENDAR_TZ = get_calendar_timezone()
    return _CALENDAR_TZ

CALENDAR_TZ = get_calendar_tz()

MIN_GAP = timedelta(minutes=2)

STATUS_SEQUENCE = [
    ReuniaoStatus.AGENDADA,
    ReuniaoStatus.EM_ANDAMENTO,
    ReuniaoStatus.REALIZADA,
    ReuniaoStatus.ADIADA,
    ReuniaoStatus.CANCELADA,
]

STATUS_DETAILS = {
    ReuniaoStatus.AGENDADA: {"label": "Agendada", "color": "#ffc107"},
    ReuniaoStatus.EM_ANDAMENTO: {"label": "Em Andamento", "color": "#198754"},
    ReuniaoStatus.REALIZADA: {"label": "Realizada", "color": "#dc3545"},
    ReuniaoStatus.ADIADA: {"label": "Adiada", "color": "#0d6efd"},
    ReuniaoStatus.CANCELADA: {"label": "Cancelada", "color": "#6c757d"},
}

EDITABLE_STATUSES = {ReuniaoStatus.AGENDADA, ReuniaoStatus.ADIADA}
CONFIGURABLE_STATUSES = {
    ReuniaoStatus.AGENDADA,
    ReuniaoStatus.EM_ANDAMENTO,
    ReuniaoStatus.ADIADA,
}
RESCHEDULE_REQUIRED_STATUSES = {ReuniaoStatus.ADIADA}


@dataclass(slots=True)
class MeetingOperationResult:
    """Lightweight data returned after creating or updating a meeting."""

    meeting_id: int
    meet_link: str | None


def get_status_label(status: ReuniaoStatus) -> str:
    """Return the human readable label for a meeting status."""

    return STATUS_DETAILS.get(status, {}).get("label", status.value.title())


def get_status_color(status: ReuniaoStatus) -> str:
    """Return the calendar color associated with a meeting status."""

    return STATUS_DETAILS.get(status, {}).get("color", "#6c757d")


def _compose_calendar_description(
    base_description: str | None,
    participant_usernames: Sequence[str],
    status_label: str,
) -> str:
    """Build the description sent to Google Calendar."""

    parts: list[str] = []
    base = (base_description or "").strip()
    if base:
        parts.append(base)
    if participant_usernames:
        parts.append("Participantes: " + ", ".join(participant_usernames))
    parts.append(f"Status: {status_label}")
    return "\n".join(parts)


class MeetingStatusConflictError(Exception):
    """Raised when a requested reschedule conflicts with existing meetings."""

    def __init__(self, messages: Sequence[str]):
        self.messages = list(messages)
        super().__init__(" ".join(self.messages))


def _normalize_meet_settings(raw: Any | None = None) -> dict[str, bool]:
    """Return a normalized dictionary with all Meet configuration flags."""

    defaults = default_meet_settings()
    normalized = defaults.copy()
    if isinstance(raw, dict):
        for key in defaults:
            if key in raw:
                normalized[key] = bool(raw[key])
    return normalized


def _apply_meet_preferences(meeting: Reuniao) -> bool | None:
    """Sync stored Meet preferences with the Google Meet space."""

    if not meeting.meet_link:
        return None
    settings = _normalize_meet_settings(meeting.meet_settings)
    try:
        update_meet_space_preferences(meeting.meet_link, settings)
    except Exception:
        current_app.logger.exception(
            "Failed to apply Meet settings for meeting %s", meeting.id
        )
        return False
    return True


def _parse_course_id(form) -> int | None:
    """Return the course identifier associated with the form submission."""

    field = getattr(form, "course_id", None)
    if not field or not getattr(field, "data", None):
        return None
    try:
        return int(field.data)
    except (TypeError, ValueError):
        return None


def populate_participants_choices(form):
    """Populate participant choices ordered by name."""
    form.participants.choices = [
        (u.id, u.name)
        for u in User.query.filter_by(ativo=True).order_by(User.name).all()
    ]


def fetch_raw_events():
    """Fetch upcoming events from Google Calendar with caching."""
    # Try to get from cache first
    cached_events = calendar_cache.get("raw_calendar_events")
    if cached_events is not None:
        return cached_events

    # Fetch from Google Calendar API
    events = list_upcoming_events(max_results=250)

    # Cache for 5 minutes (300 seconds) to reduce API calls
    # This significantly improves performance while keeping data fresh enough
    calendar_cache.set("raw_calendar_events", events, ttl=300)

    return events


def invalidate_calendar_cache():
    """Invalidate the calendar cache after meeting changes."""
    calendar_cache.delete("raw_calendar_events")


def _next_available(
    start: datetime, dur: timedelta, intervals: list[tuple[datetime, datetime]]
):
    intervals = sorted(intervals, key=lambda x: x[0])
    while True:
        for s, e in intervals:
            if start < e + MIN_GAP and start + dur > s - MIN_GAP:
                start = e + MIN_GAP
                break
        else:
            return start


def _collect_intervals(
    raw_events,
    *,
    exclude_meeting_id: int | None = None,
    exclude_event_id: str | None = None,
):
    intervals: list[tuple[datetime, datetime]] = []
    for e in raw_events:
        if exclude_event_id and e.get("id") == exclude_event_id:
            continue
        existing_start = isoparse(
            e["start"].get("dateTime") or e["start"].get("date")
        ).astimezone(CALENDAR_TZ)
        existing_end = isoparse(
            e["end"].get("dateTime") or e["end"].get("date")
        ).astimezone(CALENDAR_TZ)
        intervals.append((existing_start, existing_end))
    for r in Reuniao.query.all():
        if exclude_meeting_id and r.id == exclude_meeting_id:
            continue
        existing_start = r.inicio.astimezone(CALENDAR_TZ)
        existing_end = r.fim.astimezone(CALENDAR_TZ)
        intervals.append((existing_start, existing_end))
    return intervals


def _calculate_adjusted_start(
    start_dt: datetime, duration: timedelta, intervals: list[tuple[datetime, datetime]], now
) -> tuple[datetime, list[str]]:
    proposed_start = max(start_dt, now + MIN_GAP)
    messages: list[str] = []
    if proposed_start != start_dt:
        messages.append(
            "Reuniões devem ser agendadas com pelo menos 2 minutos de antecedência."
        )
    adjusted_start = _next_available(proposed_start, duration, intervals)
    if adjusted_start != proposed_start:
        messages.append("Horário conflita com outra reunião.")
    return adjusted_start, messages


def _auto_progress_status(
    meeting: Reuniao, start_dt: datetime, end_dt: datetime, now: datetime
) -> bool:
    """Update the meeting status according to the current time.

    Returns ``True`` if the status changed.
    """

    status = meeting.status
    new_status = status
    if status in {ReuniaoStatus.ADIADA, ReuniaoStatus.CANCELADA, ReuniaoStatus.REALIZADA}:
        return False
    if status == ReuniaoStatus.AGENDADA:
        if now >= end_dt:
            new_status = ReuniaoStatus.REALIZADA
        elif start_dt <= now < end_dt:
            new_status = ReuniaoStatus.EM_ANDAMENTO
    elif status == ReuniaoStatus.EM_ANDAMENTO:
        if now >= end_dt:
            new_status = ReuniaoStatus.REALIZADA
        elif now < start_dt:
            new_status = ReuniaoStatus.AGENDADA
    if new_status != status:
        meeting.status = new_status
        return True
    return False


def _meeting_participant_metadata(meeting: Reuniao):
    """Return participant information used in calendar serialization."""

    participant_usernames: list[str] = []
    participant_ids: list[int] = []
    participant_details: list[dict[str, Any]] = []
    participant_emails: list[str] = []
    host_candidates: list[dict[str, Any]] = []
    seen_host_ids: set[int] = set()
    for participant in meeting.participantes:
        participant_usernames.append(participant.username_usuario)
        participant_ids.append(participant.id_usuario)
        if participant.usuario and participant.usuario.name:
            display_name = participant.usuario.name
        else:
            display_name = participant.username_usuario
        participant_details.append(
            {
                "id": participant.id_usuario,
                "name": display_name,
                "username": participant.username_usuario,
                "email": participant.usuario.email if participant.usuario else None,
            }
        )
        if participant.usuario and participant.usuario.email:
            participant_emails.append(participant.usuario.email)
        if participant.id_usuario not in seen_host_ids:
            host_candidates.append({"id": participant.id_usuario, "name": display_name})
            seen_host_ids.add(participant.id_usuario)
    creator_name = ""
    creator_username = ""
    if meeting.criador:
        creator_name = meeting.criador.name or meeting.criador.username
        creator_username = meeting.criador.username
        if meeting.criador.email:
            participant_emails.append(meeting.criador.email)
        if meeting.criador.id not in seen_host_ids:
            host_candidates.append({"id": meeting.criador.id, "name": creator_name})
            seen_host_ids.add(meeting.criador.id)
    host_display: str | None = None
    if meeting.meet_host_id:
        if meeting.meet_host:
            host_display = meeting.meet_host.name or meeting.meet_host.username
        else:
            for detail in participant_details:
                if detail["id"] == meeting.meet_host_id:
                    host_display = detail["name"]
                    break
        if meeting.meet_host_id not in seen_host_ids and host_display:
            host_candidates.append({"id": meeting.meet_host_id, "name": host_display})
            seen_host_ids.add(meeting.meet_host_id)
    elif creator_name:
        host_display = creator_name
    return {
        "usernames": participant_usernames,
        "ids": participant_ids,
        "details": participant_details,
        "emails": [email for email in participant_emails if email],
        "host_candidates": host_candidates,
        "host_display": host_display,
        "creator_name": creator_name,
        "creator_username": creator_username,
    }


def serialize_meeting_event(
    meeting: Reuniao,
    now: datetime,
    current_user_id: int,
    is_admin: bool,
    *,
    auto_progress: bool = True,
):
    """Serialize a meeting instance to the structure used by the calendar."""

    start_dt = meeting.inicio.astimezone(CALENDAR_TZ)
    end_dt = meeting.fim.astimezone(CALENDAR_TZ)
    if auto_progress:
        status_changed = _auto_progress_status(meeting, start_dt, end_dt, now)
    else:
        status_changed = False
    status = meeting.status
    label = get_status_label(status)
    color = get_status_color(status)
    participant_meta = _meeting_participant_metadata(meeting)
    normalized_settings = _normalize_meet_settings(meeting.meet_settings)
    can_update_status = is_admin or meeting.criador_id == current_user_id
    can_edit = meeting.criador_id == current_user_id and status in EDITABLE_STATUSES
    can_configure = (
        bool(meeting.meet_link)
        and (is_admin or meeting.criador_id == current_user_id)
        and status in CONFIGURABLE_STATUSES
    )
    can_delete = is_admin or can_edit
    event_data = {
        "id": meeting.id,
        "title": meeting.assunto,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "color": color,
        "description": meeting.descricao,
        "status": label,
        "status_code": status.value,
        "creator": participant_meta["creator_name"] or participant_meta["creator_username"],
        "creator_username": participant_meta["creator_username"],
        "participants": participant_meta["usernames"],
        "participant_ids": participant_meta["ids"],
        "participant_details": participant_meta["details"],
        "meeting_id": meeting.id,
        "can_edit": can_edit,
        "can_delete": can_delete,
        "can_configure": can_configure,
        "can_update_status": can_update_status,
        "course_id": meeting.course_id,
        "meet_settings": normalized_settings,
        "host_candidates": participant_meta["host_candidates"],
        "meet_host_id": meeting.meet_host_id,
    }
    if meeting.meet_link:
        event_data["meet_link"] = meeting.meet_link
    if participant_meta["host_display"]:
        event_data["meet_host_name"] = participant_meta["host_display"]
    return event_data, status_changed


def _create_additional_meeting(
    additional_date,
    form,
    duration: timedelta,
    selected_users: list[User],
    description: str,
    participant_emails: list[str],
    should_notify: bool,
    user_id: int,
    course_id: int | None,
):
    start_dt = datetime.combine(
        additional_date, form.start_time.data, tzinfo=CALENDAR_TZ
    )
    end_dt = start_dt + duration
    if form.create_meet.data:
        event = create_meet_event(
            form.subject.data,
            start_dt,
            end_dt,
            description,
            participant_emails,
            notify_attendees=should_notify,
        )
        meet_link = event.get("hangoutLink")
    else:
        event = create_event(
            form.subject.data,
            start_dt,
            end_dt,
            description,
            participant_emails,
            notify_attendees=should_notify,
        )
        meet_link = None
    meeting = Reuniao(
        inicio=start_dt,
        fim=end_dt,
        assunto=form.subject.data,
        descricao=form.description.data,
        status=ReuniaoStatus.AGENDADA,
        meet_link=meet_link,
        google_event_id=event["id"],
        criador_id=user_id,
        course_id=course_id,
    )
    meeting.meet_settings = _normalize_meet_settings()
    db.session.add(meeting)
    db.session.flush()
    for u in selected_users:
        db.session.add(
            ReuniaoParticipante(
                reuniao_id=meeting.id, id_usuario=u.id, username_usuario=u.username
            )
        )
    db.session.commit()
    sync_result = _apply_meet_preferences(meeting)
    if sync_result is False:
        flash(
            "Não foi possível aplicar as configurações do Meet automaticamente.",
            "warning",
        )
    formatted_date = additional_date.strftime("%d/%m/%Y")
    if meet_link:
        flash(
            Markup(
                f"Reunião replicada para {formatted_date}! "
                f"<a href=\"{meet_link}\" target=\"_blank\">Link do Meet</a>"
            ),
            "success",
        )
    else:
        flash(f"Reunião replicada para {formatted_date}!", "success")
    return meet_link


def create_meeting_and_event(form, raw_events, now, user_id: int):
    """Create meeting adjusting times to avoid conflicts.

    Returns a tuple ``(success, result)`` where ``success`` indicates whether
    the meeting was created and ``result`` is an instance of
    :class:`MeetingOperationResult` containing the meeting identifier and the
    generated Google Meet URL when available.
    """
    course_id_value = _parse_course_id(form)
    start_dt = datetime.combine(
        form.date.data, form.start_time.data, tzinfo=CALENDAR_TZ
    )
    end_dt = datetime.combine(
        form.date.data, form.end_time.data, tzinfo=CALENDAR_TZ
    )
    duration = end_dt - start_dt
    intervals = _collect_intervals(raw_events)

    adjusted_start, messages = _calculate_adjusted_start(
        start_dt, duration, intervals, now
    )
    if adjusted_start != start_dt:
        adjusted_end = adjusted_start + duration
        form.date.data = adjusted_start.date()
        form.start_time.data = adjusted_start.time()
        form.end_time.data = adjusted_end.time()
        flash(
            f"{' '.join(messages)} Horário ajustado para o próximo horário livre.",
            "warning",
        )
        return False, None

    # Verificar se há recorrência configurada
    recorrencia_tipo_field = getattr(form, "recorrencia_tipo", None)
    recorrencia_fim_field = getattr(form, "recorrencia_fim", None)
    recorrencia_dias_semana_field = getattr(form, "recorrencia_dias_semana", None)

    recurrence_dates: list[date] = []
    group_id: str | None = None
    recorrencia_tipo_value = ReuniaoRecorrenciaTipo.NENHUMA
    recorrencia_fim_value: date | None = None
    recorrencia_dias_semana_value: str | None = None

    if (
        recorrencia_tipo_field
        and recorrencia_tipo_field.data
        and recorrencia_tipo_field.data != "NENHUMA"
        and recorrencia_fim_field
        and recorrencia_fim_field.data
    ):
        try:
            recorrencia_tipo_value = ReuniaoRecorrenciaTipo(recorrencia_tipo_field.data)
            recorrencia_fim_value = recorrencia_fim_field.data

            # Para recorrência semanal com dias específicos
            weekdays = None
            if recorrencia_tipo_value == ReuniaoRecorrenciaTipo.SEMANAL and recorrencia_dias_semana_field:
                if recorrencia_dias_semana_field.data:
                    weekdays = [int(d) for d in recorrencia_dias_semana_field.data]
                    recorrencia_dias_semana_value = ','.join(recorrencia_dias_semana_field.data)

            # Gerar todas as datas de recorrência
            all_dates = generate_recurrence_dates(
                start_date=form.date.data,
                end_date=recorrencia_fim_value,
                recurrence_type=recorrencia_tipo_value,
                weekdays=weekdays
            )

            # Remover a primeira data (será criada como reunião principal)
            recurrence_dates = [d for d in all_dates if d != form.date.data]

            # Gerar ID de grupo para a série
            if recurrence_dates:
                group_id = generate_recurrence_group_id()

        except (ValueError, AttributeError) as e:
            flash(f"Erro ao processar recorrência: {str(e)}", "danger")
            return False, None

    selected_users = User.query.filter(User.id.in_(form.participants.data)).all()
    participant_emails = [u.email for u in selected_users if u.email]
    participant_usernames = [u.username for u in selected_users]
    description = _compose_calendar_description(
        form.description.data,
        participant_usernames,
        get_status_label(ReuniaoStatus.AGENDADA),
    )
    notify_attendees = getattr(form, "notify_attendees", None)
    should_notify = bool(notify_attendees.data) if notify_attendees else False
    if form.create_meet.data:
        event = create_meet_event(
            form.subject.data,
            start_dt,
            end_dt,
            description,
            participant_emails,
            notify_attendees=should_notify,
        )
        meet_link = event.get("hangoutLink")
    else:
        event = create_event(
            form.subject.data,
            start_dt,
            end_dt,
            description,
            participant_emails,
            notify_attendees=should_notify,
        )
        meet_link = None
    meeting = Reuniao(
        inicio=start_dt,
        fim=end_dt,
        assunto=form.subject.data,
        descricao=form.description.data,
        status=ReuniaoStatus.AGENDADA,
        meet_link=meet_link,
        google_event_id=event["id"],
        criador_id=user_id,
        course_id=course_id_value,
        recorrencia_tipo=recorrencia_tipo_value,
        recorrencia_fim=recorrencia_fim_value,
        recorrencia_grupo_id=group_id,
        recorrencia_dias_semana=recorrencia_dias_semana_value,
    )
    meeting.meet_settings = _normalize_meet_settings()
    db.session.add(meeting)
    db.session.flush()
    for u in selected_users:
        db.session.add(
            ReuniaoParticipante(
                reuniao_id=meeting.id, id_usuario=u.id, username_usuario=u.username
            )
        )
    db.session.commit()
    sync_result = _apply_meet_preferences(meeting)
    if sync_result is False:
        flash(
            "Não foi possível aplicar as configurações do Meet automaticamente.",
            "warning",
        )

    additional_meet_link = None

    # Criar reuniões recorrentes
    if recurrence_dates:
        recurrence_count = 0
        for recurrence_date in recurrence_dates:
            start_dt_recurrent = datetime.combine(
                recurrence_date, form.start_time.data, tzinfo=CALENDAR_TZ
            )
            end_dt_recurrent = datetime.combine(
                recurrence_date, form.end_time.data, tzinfo=CALENDAR_TZ
            )

            # Criar evento no Google Calendar
            if form.create_meet.data:
                event_recurrent = create_meet_event(
                    form.subject.data,
                    start_dt_recurrent,
                    end_dt_recurrent,
                    description,
                    participant_emails,
                    notify_attendees=should_notify,
                )
                meet_link_recurrent = event_recurrent.get("hangoutLink")
            else:
                event_recurrent = create_event(
                    form.subject.data,
                    start_dt_recurrent,
                    end_dt_recurrent,
                    description,
                    participant_emails,
                    notify_attendees=should_notify,
                )
                meet_link_recurrent = None

            # Criar reunião recorrente
            recurrent_meeting = Reuniao(
                inicio=start_dt_recurrent,
                fim=end_dt_recurrent,
                assunto=form.subject.data,
                descricao=form.description.data,
                status=ReuniaoStatus.AGENDADA,
                meet_link=meet_link_recurrent,
                google_event_id=event_recurrent["id"],
                criador_id=user_id,
                course_id=course_id_value,
                recorrencia_tipo=recorrencia_tipo_value,
                recorrencia_fim=recorrencia_fim_value,
                recorrencia_grupo_id=group_id,
                recorrencia_dias_semana=recorrencia_dias_semana_value,
            )
            recurrent_meeting.meet_settings = _normalize_meet_settings()
            db.session.add(recurrent_meeting)
            db.session.flush()

            # Adicionar participantes
            for u in selected_users:
                db.session.add(
                    ReuniaoParticipante(
                        reuniao_id=recurrent_meeting.id,
                        id_usuario=u.id,
                        username_usuario=u.username
                    )
                )

            # Aplicar configurações do Meet
            sync_result_recurrent = _apply_meet_preferences(recurrent_meeting)
            if sync_result_recurrent is False:
                current_app.logger.warning(
                    f"Não foi possível aplicar configurações do Meet para reunião recorrente {recurrent_meeting.id}"
                )

            if not meet_link and meet_link_recurrent and not additional_meet_link:
                additional_meet_link = meet_link_recurrent

            recurrence_count += 1

        db.session.commit()

        # Mensagem de sucesso
        total_meetings = recurrence_count + 1
        if meet_link:
            flash(
                Markup(
                    f'Série de {total_meetings} reuniões recorrentes criada com sucesso! '
                    f'<a href="{meet_link}" target="_blank">Link do Meet da primeira reunião</a>'
                ),
                "success",
            )
        else:
            flash(f"Série de {total_meetings} reuniões recorrentes criada com sucesso!", "success")
    else:
        if meet_link:
            flash(
                Markup(
                    f'Reunião criada com sucesso! <a href="{meet_link}" target="_blank">Link do Meet</a>'
                ),
                "success",
            )
        else:
            flash("Reunião criada com sucesso!", "success")

    # Invalidar cache após criar reunião
    invalidate_calendar_cache()

    return True, MeetingOperationResult(meeting.id, meet_link or additional_meet_link)


def update_meeting(form, raw_events, now, meeting: Reuniao):
    """Update existing meeting adjusting for conflicts and syncing with Google Calendar.

    Returns a tuple ``(success, result)`` similar to
    :func:`create_meeting_and_event`, where ``result`` is a
    :class:`MeetingOperationResult`.
    """
    course_id_value = _parse_course_id(form)
    start_dt = datetime.combine(
        form.date.data, form.start_time.data, tzinfo=CALENDAR_TZ
    )
    end_dt = datetime.combine(
        form.date.data, form.end_time.data, tzinfo=CALENDAR_TZ
    )
    duration = end_dt - start_dt
    intervals: list[tuple[datetime, datetime]] = []
    for e in raw_events:
        if meeting.google_event_id and e.get("id") == meeting.google_event_id:
            continue
        existing_start = isoparse(
            e["start"].get("dateTime") or e["start"].get("date")
        ).astimezone(CALENDAR_TZ)
        existing_end = isoparse(
            e["end"].get("dateTime") or e["end"].get("date")
        ).astimezone(CALENDAR_TZ)
        intervals.append((existing_start, existing_end))
    for r in Reuniao.query.filter(Reuniao.id != meeting.id).all():
        existing_start = r.inicio.astimezone(CALENDAR_TZ)
        existing_end = r.fim.astimezone(CALENDAR_TZ)
        intervals.append((existing_start, existing_end))
    proposed_start = max(start_dt, now + MIN_GAP)
    messages: list[str] = []
    if proposed_start != start_dt:
        messages.append("Reuniões devem ser agendadas com pelo menos 2 minutos de antecedência.")
    adjusted_start = _next_available(proposed_start, duration, intervals)
    if adjusted_start != proposed_start:
        messages.append("Horário conflita com outra reunião.")
    if adjusted_start != start_dt:
        adjusted_end = adjusted_start + duration
        form.date.data = adjusted_start.date()
        form.start_time.data = adjusted_start.time()
        form.end_time.data = adjusted_end.time()
        flash(f"{' '.join(messages)} Horário ajustado para o próximo horário livre.", "warning")
        return False, None
    meeting.inicio = start_dt
    meeting.fim = end_dt
    meeting.assunto = form.subject.data
    meeting.descricao = form.description.data
    meeting.course_id = course_id_value
    meeting.meet_settings = _normalize_meet_settings(meeting.meet_settings)
    meeting.participantes.clear()
    selected_users = User.query.filter(User.id.in_(form.participants.data)).all()
    for u in selected_users:
        meeting.participantes.append(
            ReuniaoParticipante(id_usuario=u.id, username_usuario=u.username)
        )
    participant_emails = [u.email for u in selected_users if u.email]
    participant_usernames = [u.username for u in selected_users]
    description = _compose_calendar_description(
        form.description.data,
        participant_usernames,
        get_status_label(ReuniaoStatus.AGENDADA),
    )
    notify_field = getattr(form, "notify_attendees", None)
    should_notify = bool(notify_field.data) if notify_field else False
    if meeting.google_event_id:
        previous_meet_link = meeting.meet_link
        if form.create_meet.data:
            if previous_meet_link:
                create_meet_flag = None
            else:
                create_meet_flag = True
        else:
            create_meet_flag = False
        updated_event = update_event(
            meeting.google_event_id,
            form.subject.data,
            start_dt,
            end_dt,
            description,
            participant_emails,
            create_meet=create_meet_flag,
            notify_attendees=should_notify,
        )
        if form.create_meet.data:
            meeting.meet_link = updated_event.get("hangoutLink") or previous_meet_link
        else:
            meeting.meet_link = None
    else:
        if form.create_meet.data:
            updated_event = create_meet_event(
                form.subject.data,
                start_dt,
                end_dt,
                description,
                participant_emails,
                notify_attendees=should_notify,
            )
        else:
            updated_event = create_event(
                form.subject.data,
                start_dt,
                end_dt,
                description,
                participant_emails,
                notify_attendees=should_notify,
            )
        meeting.meet_link = updated_event.get("hangoutLink")
        meeting.google_event_id = updated_event.get("id")
    db.session.commit()
    sync_result = _apply_meet_preferences(meeting)
    if sync_result is False:
        flash(
            "Não foi possível aplicar as configurações do Meet automaticamente.",
            "warning",
        )
    flash("Reunião atualizada com sucesso!", "success")

    # Invalidar cache após atualizar reunião
    invalidate_calendar_cache()

    return True, MeetingOperationResult(meeting.id, meeting.meet_link)


def update_meeting_configuration(
    meeting: Reuniao,
    host_id: int | None,
    settings: dict[str, Any] | None,
) -> tuple[dict[str, bool], User | None, bool | None]:
    """Persist Meet configuration preferences for a meeting."""

    normalized_settings = _normalize_meet_settings(settings)
    meeting.meet_settings = normalized_settings
    host: User | None = None
    if host_id:
        host = User.query.get(host_id)
        if host is None:
            meeting.meet_host_id = None
        else:
            meeting.meet_host_id = host.id
    else:
        meeting.meet_host_id = None
    db.session.commit()
    sync_result = _apply_meet_preferences(meeting)
    return normalized_settings, host, sync_result


def change_meeting_status(
    meeting: Reuniao,
    new_status: ReuniaoStatus,
    current_user_id: int,
    is_admin: bool,
    *,
    new_start: datetime | None = None,
    new_end: datetime | None = None,
    raw_events: list[dict[str, Any]] | None = None,
    notify_attendees: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Update a meeting status handling optional rescheduling logic."""

    now = now or datetime.now(CALENDAR_TZ)
    if new_status == ReuniaoStatus.ADIADA:
        if new_start is None or new_end is None:
            raise ValueError(
                "Nova data e horários são obrigatórios para marcar a reunião como adiada."
            )
        if new_end <= new_start:
            raise ValueError("O horário de término deve ser posterior ao horário de início.")
        raw_events = raw_events or fetch_raw_events()
        intervals = _collect_intervals(
            raw_events,
            exclude_meeting_id=meeting.id,
            exclude_event_id=meeting.google_event_id,
        )
        adjusted_start, messages = _calculate_adjusted_start(
            new_start, new_end - new_start, intervals, now
        )
        if adjusted_start != new_start:
            raise MeetingStatusConflictError(messages)
        meeting.inicio = new_start
        meeting.fim = new_end
    elif new_status == ReuniaoStatus.EM_ANDAMENTO:
        if not (meeting.inicio <= now <= meeting.fim):
            raise ValueError("A reunião precisa estar dentro do horário previsto para ficar em andamento.")
    elif new_status == ReuniaoStatus.AGENDADA and now > meeting.fim:
        raise ValueError(
            "Não é possível marcar como agendada uma reunião cujo horário já foi concluído."
        )
    meeting.status = new_status
    participant_meta = _meeting_participant_metadata(meeting)
    participant_emails = list(dict.fromkeys(participant_meta["emails"]))
    description = _compose_calendar_description(
        meeting.descricao,
        participant_meta["usernames"],
        get_status_label(meeting.status),
    )
    try:
        if meeting.google_event_id:
            create_meet_flag = False if new_status == ReuniaoStatus.CANCELADA else None
            notify_flag = notify_attendees if new_status == ReuniaoStatus.ADIADA else False
            updated_event = update_event(
                meeting.google_event_id,
                meeting.assunto,
                meeting.inicio.astimezone(CALENDAR_TZ),
                meeting.fim.astimezone(CALENDAR_TZ),
                description,
                participant_emails,
                create_meet=create_meet_flag,
                notify_attendees=notify_flag,
            )
            if new_status == ReuniaoStatus.CANCELADA or create_meet_flag is False:
                meeting.meet_link = None
            elif updated_event.get("hangoutLink"):
                meeting.meet_link = updated_event.get("hangoutLink")
        elif new_status == ReuniaoStatus.CANCELADA:
            meeting.meet_link = None
        event_data, _ = serialize_meeting_event(
            meeting,
            now,
            current_user_id,
            is_admin,
            auto_progress=False,
        )
        db.session.commit()

        # Invalidar cache após mudar status da reunião
        invalidate_calendar_cache()

        return event_data
    except Exception:
        db.session.rollback()
        raise


def delete_meeting(meeting: Reuniao) -> bool:
    """Remove meeting from DB and Google Calendar.

    Returns ``True`` when both the local record and the external calendar
    event are deleted successfully. If the Google Calendar removal fails,
    the meeting is left untouched in the database and ``False`` is
    returned so the caller can warn the user.
    """
    if meeting.google_event_id:
        try:
            delete_event(meeting.google_event_id)
        except Exception:
            return False
    db.session.delete(meeting)
    db.session.commit()

    # Invalidar cache após deletar reunião
    invalidate_calendar_cache()

    return True


def combine_events(raw_events, now, current_user_id: int, is_admin: bool):
    """Combine Google and local events updating their status.

    ``is_admin`` indicates if the requester has admin privileges, allowing
    them to delete meetings regardless of status.
    """
    events: list[dict] = []
    seen_keys: set[tuple[str, str, str]] = set()

    updated = False

    # Otimizar: buscar apenas reuniões dos últimos 6 meses e próximos 6 meses
    # com eager loading de relacionamentos
    six_months_ago = now - timedelta(days=180)
    six_months_ahead = now + timedelta(days=180)

    # Use eager loading para reduzir queries N+1
    from sqlalchemy.orm import joinedload

    meetings = (
        Reuniao.query
        .options(
            joinedload(Reuniao.participantes).joinedload(ReuniaoParticipante.usuario),
            joinedload(Reuniao.criador),
            joinedload(Reuniao.meet_host)
        )
        .filter(
            Reuniao.inicio >= six_months_ago,
            Reuniao.inicio <= six_months_ahead
        )
        .all()
    )

    # Prioritize locally stored meetings so their metadata (including
    # edit permissions) is preserved. Google events are added later only
    # if they don't match an existing local meeting.
    for r in meetings:
        event_data, status_changed = serialize_meeting_event(
            r, now, current_user_id, is_admin
        )
        if status_changed:
            updated = True
        key = (event_data["title"], event_data["start"], event_data["end"])
        events.append(event_data)
        seen_keys.add(key)

    if updated:
        db.session.commit()

    # Cache user emails para evitar múltiplas queries
    all_emails = set()
    for e in raw_events:
        attendee_objs = e.get("attendees", [])
        for a in attendee_objs:
            email = a.get("email")
            if email:
                all_emails.add(email)
        creator_info = e.get("creator") or e.get("organizer", {})
        creator_email = creator_info.get("email")
        if creator_email:
            all_emails.add(creator_email)

    # Buscar todos os usuários de uma vez
    user_map = {}
    if all_emails:
        users = User.query.filter(User.email.in_(list(all_emails))).all()
        user_map = {u.email: u.username for u in users}

    # Processar eventos do Google
    for e in raw_events:
        start_str = e["start"].get("dateTime") or e["start"].get("date")
        end_str = e["end"].get("dateTime") or e["end"].get("date")
        start_dt = isoparse(start_str).astimezone(CALENDAR_TZ)
        end_dt = isoparse(end_str).astimezone(CALENDAR_TZ)
        key = (e.get("summary", "Sem título"), start_dt.isoformat(), end_dt.isoformat())
        if key in seen_keys:
            continue
        if now < start_dt:
            color = "#ffc107"
            status_label = "Agendada"
        elif start_dt <= now <= end_dt:
            color = "#198754"
            status_label = "Em Andamento"
        else:
            color = "#dc3545"
            status_label = "Realizada"
        attendee_objs = e.get("attendees", [])
        attendees: list[str] = []
        for a in attendee_objs:
            email = a.get("email")
            if email and email in user_map:
                attendees.append(user_map[email])
            elif a.get("displayName"):
                attendees.append(a.get("displayName"))
            elif email:
                attendees.append(email)
        creator_info = e.get("creator") or e.get("organizer", {})
        creator_email = creator_info.get("email")
        if creator_email and creator_email in user_map:
            creator_name = user_map[creator_email]
        elif creator_info.get("displayName"):
            creator_name = creator_info.get("displayName")
        elif creator_email:
            creator_name = creator_email
        else:
            creator_name = ""
        events.append(
            {
                "id": e.get("id"),
                "title": e.get("summary", "Sem título"),
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "description": e.get("description"),
                "meet_link": e.get("hangoutLink"),
                "color": color,
                "status": status_label,
                "status_code": None,
                "participants": attendees,
                "creator": creator_name,
                "can_edit": False,
                "can_delete": False,
                "can_update_status": False,
                "can_configure": False,
            }
        )
        seen_keys.add(key)

    return events
