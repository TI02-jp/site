"""Helpers for meeting room scheduling logic."""

from datetime import date, datetime, timedelta
from flask import flash
from markupsafe import Markup
from dateutil.parser import isoparse
from typing import TypedDict

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


class _StatusConfig(TypedDict):
    label: str
    color: str
    text_color: str
    tooltip_text_color: str


STATUS_CONFIG: dict[ReuniaoStatus, _StatusConfig] = {
    ReuniaoStatus.AGENDADA: {
        "label": "Agendada",
        "color": "#ffc107",
        "text_color": "#212529",
        "tooltip_text_color": "#000000",
    },
    ReuniaoStatus.EM_ANDAMENTO: {
        "label": "Em Andamento",
        "color": "#198754",
        "text_color": "#ffffff",
        "tooltip_text_color": "#ffffff",
    },
    ReuniaoStatus.REALIZADA: {
        "label": "Realizada",
        "color": "#dc3545",
        "text_color": "#ffffff",
        "tooltip_text_color": "#ffffff",
    },
    ReuniaoStatus.ADIADA: {
        "label": "Adiada",
        "color": "#fd7e14",
        "text_color": "#ffffff",
        "tooltip_text_color": "#ffffff",
    },
    ReuniaoStatus.CANCELADA: {
        "label": "Cancelada",
        "color": "#6f42c1",
        "text_color": "#ffffff",
        "tooltip_text_color": "#ffffff",
    },
}


STATUS_ORDER: tuple[ReuniaoStatus, ...] = (
    ReuniaoStatus.AGENDADA,
    ReuniaoStatus.EM_ANDAMENTO,
    ReuniaoStatus.REALIZADA,
    ReuniaoStatus.ADIADA,
    ReuniaoStatus.CANCELADA,
)


MANUAL_STATUS_CHOICES = [
    (ReuniaoStatus.AGENDADA.value, STATUS_CONFIG[ReuniaoStatus.AGENDADA]["label"]),
    (ReuniaoStatus.ADIADA.value, STATUS_CONFIG[ReuniaoStatus.ADIADA]["label"]),
    (ReuniaoStatus.CANCELADA.value, STATUS_CONFIG[ReuniaoStatus.CANCELADA]["label"]),
]


def get_status_metadata_for_template() -> list[dict[str, str]]:
    """Return ordered status metadata for the meeting room template."""

    metadata: list[dict[str, str]] = []
    for status in STATUS_ORDER:
        config = STATUS_CONFIG[status]
        slug = status.value.replace(" ", "-")
        metadata.append(
            {
                "code": status.value,
                "label": config["label"],
                "color": config["color"],
                "text_color": config["text_color"],
                "tooltip_text_color": config["tooltip_text_color"],
                "slug": slug,
                "tooltip_class": f"tooltip-{slug}",
            }
        )
    return metadata


def _get_status_metadata(status: ReuniaoStatus) -> tuple[str, str]:
    """Return display label and color for the provided status."""

    config = STATUS_CONFIG.get(status, STATUS_CONFIG[ReuniaoStatus.AGENDADA])
    return config["label"], config["color"]


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
    """Fetch upcoming events from Google Calendar."""
    return list_upcoming_events(max_results=250)


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

    Returns a tuple ``(success, meet_link)`` where ``success`` indicates
    whether the meeting was created and ``meet_link`` contains the generated
    Google Meet URL when available.
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
    participant_emails = [u.email for u in selected_users]
    participant_usernames = [u.username for u in selected_users]
    description = form.description.data or ""
    if participant_usernames:
        description += "\nParticipantes: " + ", ".join(participant_usernames)
    status_label, _ = _get_status_metadata(ReuniaoStatus.AGENDADA)
    description += f"\nStatus: {status_label}"
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
                course_id_value,
            )
            if not meet_link and link and not additional_meet_link:
                additional_meet_link = link
    return True, meet_link or additional_meet_link


def update_meeting(form, raw_events, now, meeting: Reuniao):
    """Update existing meeting adjusting for conflicts and syncing with Google Calendar.

    Returns a tuple ``(success, meet_link)`` similar to
    :func:`create_meeting_and_event`.
    """
    course_id_value = _parse_course_id(form)
    status_field = getattr(form, "status", None)
    status_value = meeting.status
    previous_status = meeting.status
    previous_start = meeting.inicio
    previous_end = meeting.fim
    if status_field and status_field.data:
        try:
            status_value = ReuniaoStatus(status_field.data)
        except ValueError:
            status_value = meeting.status
    postponed_date_field = getattr(form, "postponed_date", None)
    postponed_start_field = getattr(form, "postponed_start_time", None)
    postponed_end_field = getattr(form, "postponed_end_time", None)
    effective_date = form.date.data
    effective_start_time = form.start_time.data
    effective_end_time = form.end_time.data
    if status_value == ReuniaoStatus.ADIADA:
        if postponed_date_field and postponed_date_field.data:
            effective_date = postponed_date_field.data
        if postponed_start_field and postponed_start_field.data:
            effective_start_time = postponed_start_field.data
        if postponed_end_field and postponed_end_field.data:
            effective_end_time = postponed_end_field.data
    start_dt = datetime.combine(
        effective_date, effective_start_time, tzinfo=CALENDAR_TZ
    )
    end_dt = datetime.combine(
        effective_date, effective_end_time, tzinfo=CALENDAR_TZ
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
        if status_value == ReuniaoStatus.ADIADA:
            if postponed_date_field:
                postponed_date_field.data = adjusted_start.date()
            if postponed_start_field:
                postponed_start_field.data = adjusted_start.time()
            if postponed_end_field:
                postponed_end_field.data = adjusted_end.time()
        flash(f"{' '.join(messages)} Horário ajustado para o próximo horário livre.", "warning")
        return False, None
    meeting.inicio = start_dt
    meeting.fim = end_dt
    meeting.assunto = form.subject.data
    meeting.descricao = form.description.data
    meeting.course_id = course_id_value
    meeting.status = status_value
    if status_value == ReuniaoStatus.ADIADA:
        if previous_status != ReuniaoStatus.ADIADA or meeting.postponed_from_start is None:
            meeting.postponed_from_start = previous_start
            meeting.postponed_from_end = previous_end
    else:
        meeting.postponed_from_start = None
        meeting.postponed_from_end = None
    meeting.participantes.clear()
    selected_users = User.query.filter(User.id.in_(form.participants.data)).all()
    for u in selected_users:
        meeting.participantes.append(ReuniaoParticipante(id_usuario=u.id, username_usuario=u.username))
    participant_emails = [u.email for u in selected_users]
    participant_usernames = [u.username for u in selected_users]
    description = form.description.data or ""
    if participant_usernames:
        description += "\nParticipantes: " + ", ".join(participant_usernames)
    status_label, _ = _get_status_metadata(status_value)
    description += f"\nStatus: {status_label}"
    notify_field = getattr(form, "notify_attendees", None)
    should_notify = bool(notify_field.data) if notify_field else False
    create_meet_field = getattr(form, "create_meet", None)
    should_create_meet = bool(create_meet_field.data) if create_meet_field else False
    if status_value == ReuniaoStatus.CANCELADA:
        should_create_meet = False
    if meeting.google_event_id:
        updated_event = update_event(
            meeting.google_event_id,
            form.subject.data,
            start_dt,
            end_dt,
            description,
            participant_emails,
            create_meet=should_create_meet,
            notify_attendees=should_notify,
        )
        meeting.meet_link = (
            updated_event.get("hangoutLink") if should_create_meet else None
        )
    else:
        if should_create_meet:
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
        meeting.meet_link = updated_event.get("hangoutLink") if should_create_meet else None
        meeting.google_event_id = updated_event.get("id")
    db.session.commit()
    flash("Reunião atualizada com sucesso!", "success")
    return True, meeting.meet_link


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
        status = r.status
        if status not in (ReuniaoStatus.ADIADA, ReuniaoStatus.CANCELADA):
            if now < start_dt:
                derived_status = ReuniaoStatus.AGENDADA
            elif start_dt <= now <= end_dt:
                derived_status = ReuniaoStatus.EM_ANDAMENTO
            else:
                derived_status = ReuniaoStatus.REALIZADA
            if status != derived_status:
                r.status = derived_status
                status = derived_status
                updated = True
        status_label, color = _get_status_metadata(status)
        can_edit = (
            r.criador_id == current_user_id
            and status not in (ReuniaoStatus.EM_ANDAMENTO, ReuniaoStatus.REALIZADA)
        )
        can_delete = can_edit or is_admin
        original_start = (
            r.postponed_from_start.astimezone(CALENDAR_TZ).isoformat()
            if r.postponed_from_start
            else None
        )
        original_end = (
            r.postponed_from_end.astimezone(CALENDAR_TZ).isoformat()
            if r.postponed_from_end
            else None
        )
        if status != ReuniaoStatus.ADIADA:
            original_start = None
            original_end = None
        event_data = {
            "id": r.id,
            "title": r.assunto,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "color": color,
            "description": r.descricao,
            "status": status_label,
            "status_code": status.value,
            "creator": r.criador.username,
            "participants": [p.username_usuario for p in r.participantes],
            "participant_ids": [p.id_usuario for p in r.participantes],
            "meeting_id": r.id,
            "can_edit": can_edit,
            "can_delete": can_delete,
            "course_id": r.course_id,
            "original_start": original_start,
            "original_end": original_end,
        }
        if status == ReuniaoStatus.CANCELADA:
            event_data["meet_link"] = None
        elif r.meet_link:
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
                "status": status_label,
                "status_code": "",
                "participants": attendees,
                "creator": creator_name,
                "can_edit": False,
                "can_delete": False,
                "course_id": None,
                "original_start": None,
                "original_end": None,
            }
        )
        seen_keys.add(key)

    return events
