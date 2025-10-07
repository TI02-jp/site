"""Helpers for meeting room scheduling logic."""

from datetime import date, datetime, timedelta
from typing import Any

from flask import current_app, flash
from markupsafe import Markup
from dateutil.parser import isoparse

from app import db
from app.models.tables import (
    User,
    Reuniao,
    ReuniaoParticipante,
    ReuniaoStatus,
)
from app.services.google_calendar import (
    list_upcoming_events,
    create_meet_event,
    create_event,
    update_event,
    delete_event,
    get_calendar_timezone,
)

CALENDAR_TZ = get_calendar_timezone()

MIN_GAP = timedelta(minutes=2)

PORTAL_PRIMARY_HEX = "#0b288b"

CALENDAR_SYNC_WARNING = (
    "Não foi possível sincronizar com o Google Calendar. "
    "A reunião foi registrada apenas no portal."
)

STATUS_ORDER = [
    ReuniaoStatus.AGENDADA,
    ReuniaoStatus.EM_ANDAMENTO,
    ReuniaoStatus.REALIZADA,
    ReuniaoStatus.ADIADA,
    ReuniaoStatus.CANCELADA,
]

STATUS_METADATA: dict[ReuniaoStatus, dict[str, Any]] = {
    ReuniaoStatus.AGENDADA: {
        "label": "Agendada",
        "color": "#ffc107",
        "text_color": PORTAL_PRIMARY_HEX,
        "tooltip_class": "tooltip-warning",
        "badge_class": "bg-warning text-dark",
        "disable_meet_link": False,
    },
    ReuniaoStatus.EM_ANDAMENTO: {
        "label": "Em Andamento",
        "color": "#198754",
        "text_color": PORTAL_PRIMARY_HEX,
        "tooltip_class": "tooltip-success",
        "badge_class": "bg-success",
        "disable_meet_link": False,
    },
    ReuniaoStatus.REALIZADA: {
        "label": "Realizada",
        "color": "#dc3545",
        "text_color": PORTAL_PRIMARY_HEX,
        "tooltip_class": "tooltip-danger",
        "badge_class": "bg-danger",
        "disable_meet_link": True,
    },
    ReuniaoStatus.ADIADA: {
        "label": "Adiada",
        "color": "#0dcaf0",
        "text_color": PORTAL_PRIMARY_HEX,
        "tooltip_class": "tooltip-info",
        "badge_class": "bg-info text-dark",
        "disable_meet_link": False,
    },
    ReuniaoStatus.CANCELADA: {
        "label": "Cancelada",
        "color": "#6c757d",
        "text_color": PORTAL_PRIMARY_HEX,
        "tooltip_class": "tooltip-secondary",
        "badge_class": "bg-secondary",
        "disable_meet_link": True,
    },
}

MANUAL_STATUS_OVERRIDES = {
    ReuniaoStatus.ADIADA,
    ReuniaoStatus.CANCELADA,
    ReuniaoStatus.REALIZADA,
}


def _log_calendar_error(message: str, exc: Exception) -> None:
    """Log ``message`` with ``exc`` if an application context is available."""

    try:
        logger = current_app.logger
    except Exception:
        logger = None
    if logger:
        logger.warning(message, exc_info=exc)


def get_status_metadata(status: ReuniaoStatus) -> dict[str, Any]:
    """Return display metadata for ``status``."""

    meta = STATUS_METADATA[status].copy()
    meta["value"] = status.value
    return meta


def get_status_options() -> list[tuple[str, str]]:
    """Return ``(value, label)`` tuples for status selectors."""

    return [
        (status.value, STATUS_METADATA[status]["label"])
        for status in STATUS_ORDER
    ]


def get_status_badges() -> list[dict[str, str]]:
    """Return badge metadata for legend rendering."""

    return [
        {
            "value": status.value,
            "label": STATUS_METADATA[status]["label"],
            "badge_class": STATUS_METADATA[status]["badge_class"],
        }
        for status in STATUS_ORDER
    ]


def _auto_status_enum(start_dt: datetime, end_dt: datetime, now: datetime) -> ReuniaoStatus:
    """Return the automatic status for a meeting interval."""

    if now < start_dt:
        return ReuniaoStatus.AGENDADA
    if start_dt <= now <= end_dt:
        return ReuniaoStatus.EM_ANDAMENTO
    return ReuniaoStatus.REALIZADA


def _resolve_meeting_status(
    meeting: Reuniao, start_dt: datetime, end_dt: datetime, now: datetime
) -> tuple[ReuniaoStatus, bool]:
    """Determine the meeting status respecting manual overrides."""

    if meeting.status_override:
        status_enum = meeting.status_override
        changed = meeting.status != status_enum
        if changed:
            meeting.status = status_enum
        return status_enum, changed

    status_enum = _auto_status_enum(start_dt, end_dt, now)
    changed = meeting.status != status_enum
    if changed:
        meeting.status = status_enum
    return status_enum, changed

def _status_palette(start_dt: datetime, end_dt: datetime, now: datetime):
    """Return status metadata (enum, label, colors) for a meeting interval."""

    if now < start_dt:
        return (
            ReuniaoStatus.AGENDADA,
            "Agendada",
            "#ffc107",
            PORTAL_PRIMARY_HEX,
        )
    if start_dt <= now <= end_dt:
        return (
            ReuniaoStatus.EM_ANDAMENTO,
            "Em Andamento",
            "#198754",
            PORTAL_PRIMARY_HEX,
        )
    return (
        ReuniaoStatus.REALIZADA,
        "Realizada",
        "#dc3545",
        PORTAL_PRIMARY_HEX,
    )


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
    choices = [
        (u.id, u.name)
        for u in User.query.filter_by(ativo=True).order_by(User.name).all()
    ]
    form.participants.choices = choices
    owner_field = getattr(form, "owner_id", None)
    if owner_field is not None:
        owner_field.choices = [("", "Selecione o proprietário")] + choices


def fetch_raw_events():
    """Fetch upcoming events from Google Calendar."""
    try:
        return list_upcoming_events(max_results=250)
    except Exception as exc:
        _log_calendar_error("Não foi possível carregar eventos externos.", exc)
        return []


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


def _collect_intervals(raw_events):
    intervals: list[tuple[datetime, datetime]] = []
    for e in raw_events:
        existing_start = isoparse(
            e["start"].get("dateTime") or e["start"].get("date")
        ).astimezone(CALENDAR_TZ)
        existing_end = isoparse(
            e["end"].get("dateTime") or e["end"].get("date")
        ).astimezone(CALENDAR_TZ)
        intervals.append((existing_start, existing_end))
    for r in Reuniao.query.all():
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


def _create_additional_meeting(
    additional_date,
    form,
    duration: timedelta,
    selected_users: list[User],
    description: str,
    participant_emails: list[str],
    should_notify: bool,
    user_id: int,
    owner_id: int | None,
    course_id: int | None,
):
    start_dt = datetime.combine(
        additional_date, form.start_time.data, tzinfo=CALENDAR_TZ
    )
    end_dt = start_dt + duration
    event = None
    meet_link = None
    google_event_id = None
    try:
        if form.create_meet.data:
            event = create_meet_event(
                form.subject.data,
                start_dt,
                end_dt,
                description,
                participant_emails,
                notify_attendees=should_notify,
            )
        else:
            event = create_event(
                form.subject.data,
                start_dt,
                end_dt,
                description,
                participant_emails,
                notify_attendees=should_notify,
            )
        if event:
            meet_link = event.get("hangoutLink")
            google_event_id = event.get("id")
    except Exception as exc:
        _log_calendar_error(
            "Falha ao criar reunião adicional no Google Calendar.", exc
        )
        flash(CALENDAR_SYNC_WARNING, "warning")
    meeting = Reuniao(
        inicio=start_dt,
        fim=end_dt,
        assunto=form.subject.data,
        descricao=form.description.data,
        status=ReuniaoStatus.AGENDADA,
        meet_link=meet_link,
        google_event_id=google_event_id,
        criador_id=user_id,
        owner_id=owner_id,
        course_id=course_id,
    )
    db.session.add(meeting)
    db.session.flush()
    for u in selected_users:
        db.session.add(
            ReuniaoParticipante(
                reuniao_id=meeting.id, id_usuario=u.id, username_usuario=u.username
            )
        )
    db.session.commit()
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

    Returns a tuple ``(success, meet_info)`` where ``success`` indicates
    whether the meeting was created and ``meet_info`` is either ``None`` or a
    dictionary containing the generated Google Meet URL (under the
    ``"link"`` key) alongside the Google Calendar event identifier (under the
    ``"event_id"`` key).
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

    additional_dates_field = getattr(form, "additional_dates", None)
    apply_more_days_field = getattr(form, "apply_more_days", None)
    additional_dates: list[date] = []
    if (
        apply_more_days_field
        and additional_dates_field
        and apply_more_days_field.data
    ):
        for field in additional_dates_field:
            if not field.data:
                continue
            extra_start = datetime.combine(
                field.data, form.start_time.data, tzinfo=CALENDAR_TZ
            )
            extra_adjusted_start, extra_messages = _calculate_adjusted_start(
                extra_start, duration, intervals, now
            )
            if extra_adjusted_start != extra_start:
                formatted_date = field.data.strftime("%d/%m/%Y")
                flash(
                    "Não foi possível agendar a reunião adicional na data selecionada. "
                    + " ".join(extra_messages)
                    + f" Ajuste o horário do dia {formatted_date} e tente novamente.",
                    "warning",
                )
                return False, None
            additional_dates.append(field.data)

    selected_users = User.query.filter(User.id.in_(form.participants.data)).all()
    selected_user_ids = [u.id for u in selected_users]
    owner_field = getattr(form, "owner_id", None)
    owner_id_value: int | None = None
    if selected_user_ids:
        if len(selected_user_ids) == 1:
            owner_id_value = selected_user_ids[0]
        elif owner_field and owner_field.data in selected_user_ids:
            owner_id_value = owner_field.data
    participant_emails = [u.email for u in selected_users]
    participant_usernames = [u.username for u in selected_users]
    description = form.description.data or ""
    if participant_usernames:
        description += "\nParticipantes: " + ", ".join(participant_usernames)
    description += "\nStatus: " + STATUS_METADATA[ReuniaoStatus.AGENDADA]["label"]
    notify_attendees = getattr(form, "notify_attendees", None)
    should_notify = bool(notify_attendees.data) if notify_attendees else False
    event = None
    meet_link = None
    google_event_id = None
    try:
        if form.create_meet.data:
            event = create_meet_event(
                form.subject.data,
                start_dt,
                end_dt,
                description,
                participant_emails,
                notify_attendees=should_notify,
            )
        else:
            event = create_event(
                form.subject.data,
                start_dt,
                end_dt,
                description,
                participant_emails,
                notify_attendees=should_notify,
            )
        if event:
            meet_link = event.get("hangoutLink")
            google_event_id = event.get("id")
    except Exception as exc:
        _log_calendar_error("Falha ao criar evento no Google Calendar.", exc)
        flash(CALENDAR_SYNC_WARNING, "warning")
    meeting = Reuniao(
        inicio=start_dt,
        fim=end_dt,
        assunto=form.subject.data,
        descricao=form.description.data,
        status=ReuniaoStatus.AGENDADA,
        meet_link=meet_link,
        google_event_id=google_event_id,
        criador_id=user_id,
        owner_id=owner_id_value,
        course_id=course_id_value,
    )
    db.session.add(meeting)
    db.session.flush()
    for u in selected_users:
        db.session.add(
            ReuniaoParticipante(
                reuniao_id=meeting.id, id_usuario=u.id, username_usuario=u.username
            )
        )
    db.session.commit()
    additional_meet_link = None
    if meet_link:
        flash(
            Markup(
                f'Reunião criada com sucesso! <a href="{meet_link}" target="_blank">Link do Meet</a>'
            ),
            "success",
        )
    else:
        flash("Reunião criada com sucesso!", "success")
    if additional_dates:
        for extra_date in additional_dates:
            link = _create_additional_meeting(
                extra_date,
                form,
                duration,
                selected_users,
                description,
                participant_emails,
                should_notify,
                user_id,
                owner_id_value,
                course_id_value,
            )
            if not meet_link and link and not additional_meet_link:
                additional_meet_link = link
    meet_link_for_popup = meet_link or additional_meet_link
    meet_info = None
    if meet_link_for_popup:
        meet_info = {"link": meet_link_for_popup, "event_id": google_event_id}
    return True, meet_info


def update_meeting(form, raw_events, now, meeting: Reuniao):
    """Update existing meeting adjusting for conflicts and syncing with Google Calendar.

    Returns a tuple ``(success, meet_info)`` similar to
    :func:`create_meeting_and_event`.
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
    meeting.participantes.clear()
    selected_users = User.query.filter(User.id.in_(form.participants.data)).all()
    selected_user_ids = [u.id for u in selected_users]
    owner_field = getattr(form, "owner_id", None)
    owner_id_value: int | None = None
    if selected_user_ids:
        if len(selected_user_ids) == 1:
            owner_id_value = selected_user_ids[0]
        elif owner_field and owner_field.data in selected_user_ids:
            owner_id_value = owner_field.data
    meeting.owner_id = owner_id_value
    for u in selected_users:
        meeting.participantes.append(ReuniaoParticipante(id_usuario=u.id, username_usuario=u.username))
    participant_emails = [u.email for u in selected_users]
    participant_usernames = [u.username for u in selected_users]
    description = form.description.data or ""
    if participant_usernames:
        description += "\nParticipantes: " + ", ".join(participant_usernames)
    current_status = meeting.status_override or meeting.status or ReuniaoStatus.AGENDADA
    status_label = STATUS_METADATA[current_status]["label"]
    description += "\nStatus: " + status_label
    notify_field = getattr(form, "notify_attendees", None)
    should_notify = bool(notify_field.data) if notify_field else False
    if meeting.google_event_id:
        wants_meet = bool(form.create_meet.data)
        existing_link = meeting.meet_link
        create_meet_flag: bool | None = None
        if wants_meet and not existing_link:
            create_meet_flag = True
        elif not wants_meet and existing_link:
            create_meet_flag = False

        updated_event = None
        try:
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
        except Exception as exc:
            _log_calendar_error("Falha ao atualizar evento no Google Calendar.", exc)
            flash(CALENDAR_SYNC_WARNING, "warning")

        if wants_meet:
            if updated_event and updated_event.get("hangoutLink"):
                meeting.meet_link = updated_event.get("hangoutLink")
            else:
                meeting.meet_link = existing_link
        else:
            meeting.meet_link = None
    else:
        updated_event = None
        try:
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
        except Exception as exc:
            _log_calendar_error("Falha ao criar evento no Google Calendar.", exc)
            flash(CALENDAR_SYNC_WARNING, "warning")
        meeting.meet_link = updated_event.get("hangoutLink") if updated_event else None
        meeting.google_event_id = updated_event.get("id") if updated_event else None
    db.session.commit()
    flash("Reunião atualizada com sucesso!", "success")
    meet_info = None
    if meeting.meet_link:
        meet_info = {"link": meeting.meet_link, "event_id": meeting.google_event_id}
    return True, meet_info


def postpone_meeting(
    meeting: Reuniao,
    new_start: datetime,
    new_end: datetime,
    status: ReuniaoStatus,
    raw_events: list[dict[str, Any]] | None = None,
    notify_attendees: bool = False,
    check_external_conflicts: bool = True,
) -> tuple[bool, str | None]:
    """Reschedule ``meeting`` to the provided interval.

    Returns ``(success, error_message)``. When ``success`` is ``False`` the
    ``error_message`` contains a human readable reason. When
    ``check_external_conflicts`` is ``False`` the conflict detection only uses
    local meetings, skipping external calendar lookups for faster responses.
    """

    if new_end <= new_start:
        return False, "O horário de término deve ser posterior ao horário de início."

    intervals: list[tuple[datetime, datetime]] = []
    if check_external_conflicts and raw_events is None and meeting.google_event_id:
        try:
            raw_events = fetch_raw_events()
        except Exception:
            raw_events = []

    if raw_events:
        for event in raw_events:
            if meeting.google_event_id and event.get("id") == meeting.google_event_id:
                continue
            existing_start = isoparse(
                event["start"].get("dateTime") or event["start"].get("date")
            ).astimezone(CALENDAR_TZ)
            existing_end = isoparse(
                event["end"].get("dateTime") or event["end"].get("date")
            ).astimezone(CALENDAR_TZ)
            intervals.append((existing_start, existing_end))

    for other in Reuniao.query.filter(Reuniao.id != meeting.id).all():
        existing_start = other.inicio.astimezone(CALENDAR_TZ)
        existing_end = other.fim.astimezone(CALENDAR_TZ)
        intervals.append((existing_start, existing_end))

    for existing_start, existing_end in intervals:
        if new_start < existing_end + MIN_GAP and new_end > existing_start - MIN_GAP:
            return False, "O horário informado conflita com outra reunião."

    participant_ids = [p.id_usuario for p in meeting.participantes]
    users = (
        User.query.filter(User.id.in_(participant_ids)).all()
        if participant_ids
        else []
    )
    email_map = {user.id: user.email for user in users if user.email}
    username_map = {user.id: user.username for user in users if user.username}
    participant_emails = [
        email_map[pid] for pid in participant_ids if pid in email_map
    ]
    participant_usernames: list[str] = []
    for participant in meeting.participantes:
        username = username_map.get(participant.id_usuario) or participant.username_usuario
        if username:
            participant_usernames.append(username)

    description_lines: list[str] = []
    if meeting.descricao:
        description_lines.append(meeting.descricao)
    if participant_usernames:
        description_lines.append("Participantes: " + ", ".join(participant_usernames))
    description_lines.append("Status: " + STATUS_METADATA[status]["label"])
    description = "\n".join(description_lines).strip()

    original_start = meeting.inicio
    original_end = meeting.fim

    try:
        if meeting.google_event_id:
            updated_event = update_event(
                meeting.google_event_id,
                meeting.assunto,
                new_start,
                new_end,
                description,
                participant_emails,
                create_meet=None,
                notify_attendees=notify_attendees,
            )
            hangout_link = updated_event.get("hangoutLink") if updated_event else None
            if hangout_link:
                meeting.meet_link = hangout_link
        else:
            if meeting.meet_link:
                created_event = create_meet_event(
                    meeting.assunto,
                    new_start,
                    new_end,
                    description,
                    participant_emails,
                    notify_attendees=notify_attendees,
                )
            else:
                created_event = create_event(
                    meeting.assunto,
                    new_start,
                    new_end,
                    description,
                    participant_emails,
                    notify_attendees=notify_attendees,
                )
            meeting.google_event_id = created_event.get("id") if created_event else None
            hangout_link = created_event.get("hangoutLink") if created_event else None
            if hangout_link:
                meeting.meet_link = hangout_link
    except Exception:
        meeting.inicio = original_start
        meeting.fim = original_end
        return False, "Não foi possível atualizar o evento no Google Calendar."

    meeting.inicio = new_start
    meeting.fim = new_end
    return True, None


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
    return True


def combine_events(raw_events, now, current_user_id: int, is_admin: bool):
    """Combine Google and local events updating their status.

    ``is_admin`` indicates if the requester has admin privileges, allowing
    them to delete meetings regardless of status.
    """
    events: list[dict] = []
    seen_keys: set[tuple[str, str, str]] = set()

    updated = False

    # Prioritize locally stored meetings so their metadata (including
    # edit permissions) is preserved. Google events are added later only
    # if they don't match an existing local meeting.
    for r in Reuniao.query.all():
        start_dt = r.inicio.astimezone(CALENDAR_TZ)
        end_dt = r.fim.astimezone(CALENDAR_TZ)
        key = (r.assunto, start_dt.isoformat(), end_dt.isoformat())
        status_enum, changed = _resolve_meeting_status(r, start_dt, end_dt, now)
        if changed:
            updated = True
        meta = STATUS_METADATA[status_enum]
        status_label = meta["label"]
        color = meta["color"]
        text_color = meta["text_color"]
        tooltip_class = meta["tooltip_class"]
        disable_meet_link = meta["disable_meet_link"]
        can_edit = (
            r.criador_id == current_user_id
            and status_enum in (ReuniaoStatus.AGENDADA, ReuniaoStatus.ADIADA)
        )
        can_delete = can_edit or is_admin
        allowed_status_users = {r.criador_id}
        if r.owner_id:
            allowed_status_users.add(r.owner_id)
        can_update_status = is_admin or current_user_id in allowed_status_users
        event_data = {
            "id": r.id,
            "title": r.assunto,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "color": color,
            "description": r.descricao,
            "status": status_label,
            "status_value": status_enum.value,
            "creator": r.criador.username,
            "owner_id": r.owner_id,
            "owner_name": r.owner.username if r.owner else None,
            "participants": [p.username_usuario for p in r.participantes],
            "participant_ids": [p.id_usuario for p in r.participantes],
            "meeting_id": r.id,
            "can_edit": can_edit,
            "can_delete": can_delete,
            "can_update_status": can_update_status,
            "course_id": r.course_id,
            "textColor": text_color,
            "tooltip_class": tooltip_class,
            "disable_meet_link": disable_meet_link,
        }
        if r.meet_link:
            event_data["meet_link"] = r.meet_link
        events.append(event_data)
        seen_keys.add(key)

    if updated:
        db.session.commit()

    for e in raw_events:
        start_str = e["start"].get("dateTime") or e["start"].get("date")
        end_str = e["end"].get("dateTime") or e["end"].get("date")
        start_dt = isoparse(start_str).astimezone(CALENDAR_TZ)
        end_dt = isoparse(end_str).astimezone(CALENDAR_TZ)
        key = (e.get("summary", "Sem título"), start_dt.isoformat(), end_dt.isoformat())
        if key in seen_keys:
            continue
        status_enum = _auto_status_enum(start_dt, end_dt, now)
        meta = STATUS_METADATA[status_enum]
        status_label = meta["label"]
        color = meta["color"]
        text_color = meta["text_color"]
        tooltip_class = meta["tooltip_class"]
        disable_meet_link = meta["disable_meet_link"]
        _, status_label, color, text_color = _status_palette(start_dt, end_dt, now)
        attendee_objs = e.get("attendees", [])
        emails = [a.get("email") for a in attendee_objs if a.get("email")]
        user_map = {
            u.email: u.username
            for u in User.query.filter(User.email.in_(emails)).all()
        } if emails else {}
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
                "textColor": text_color,
                "status": status_label,
                "status_value": status_enum.value,
                "participants": attendees,
                "creator": creator_name,
                "can_edit": False,
                "can_delete": False,
                "can_update_status": False,
                "tooltip_class": tooltip_class,
                "disable_meet_link": disable_meet_link,
            }
        )
        seen_keys.add(key)

    return events
