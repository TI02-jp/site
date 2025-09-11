"""Helpers for meeting room scheduling logic."""

from datetime import datetime, timedelta
from flask import flash
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
from app.utils.event_stream import publish

CALENDAR_TZ = get_calendar_timezone()

MIN_GAP = timedelta(minutes=2)


def serialize_meeting(meeting: Reuniao) -> dict:
    """Return minimal meeting info for client notifications."""
    start_dt = meeting.inicio.astimezone(CALENDAR_TZ)
    return {
        "id": meeting.id,
        "title": meeting.assunto,
        "start": start_dt.isoformat(),
        "creator": meeting.criador.username,
        "participants": [p.username_usuario for p in meeting.participantes],
    }


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


def create_meeting_and_event(form, raw_events, now, user_id: int):
    """Create meeting adjusting times to avoid conflicts.

    Returns a tuple ``(success, meet_link)`` where ``success`` indicates
    whether the meeting was created and ``meet_link`` contains the generated
    Google Meet URL when available.
    """
    start_dt = datetime.combine(
        form.date.data, form.start_time.data, tzinfo=CALENDAR_TZ
    )
    end_dt = datetime.combine(
        form.date.data, form.end_time.data, tzinfo=CALENDAR_TZ
    )
    duration = end_dt - start_dt
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

    proposed_start = max(start_dt, now + MIN_GAP)
    messages: list[str] = []
    if proposed_start != start_dt:
        messages.append(
            "Reuniões devem ser agendadas com pelo menos 2 minutos de antecedência."
        )
    adjusted_start = _next_available(proposed_start, duration, intervals)
    if adjusted_start != proposed_start:
        messages.append("Horário conflita com outra reunião.")
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

    selected_users = User.query.filter(User.id.in_(form.participants.data)).all()
    participant_emails = [u.email for u in selected_users]
    participant_usernames = [u.username for u in selected_users]
    description = form.description.data or ""
    if participant_usernames:
        description += "\nParticipantes: " + ", ".join(participant_usernames)
    description += "\nStatus: Agendada"
    if form.create_meet.data:
        event = create_meet_event(
            form.subject.data,
            start_dt,
            end_dt,
            description,
            participant_emails,
        )
    else:
        event = create_event(
            form.subject.data,
            start_dt,
            end_dt,
            description,
            participant_emails,
        )
    meet_link = event.get("hangoutLink")
    meeting = Reuniao(
        inicio=start_dt,
        fim=end_dt,
        assunto=form.subject.data,
        descricao=form.description.data,
        status=ReuniaoStatus.AGENDADA,
        meet_link=meet_link,
        google_event_id=event["id"],
        criador_id=user_id,
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
    publish({"type": "created", "meeting": serialize_meeting(meeting)})
    if meet_link:
        flash(
            Markup(
                f'Reunião criada com sucesso! <a href="{meet_link}" target="_blank">Link do Meet</a>'
            ),
            "success",
        )
    else:
        flash("Reunião criada com sucesso!", "success")
    return True, meet_link


def update_meeting(form, raw_events, now, meeting: Reuniao):
    """Update existing meeting adjusting for conflicts and syncing with Google Calendar.

    Returns a tuple ``(success, meet_link)`` similar to
    :func:`create_meeting_and_event`.
    """
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
    meeting.participantes.clear()
    selected_users = User.query.filter(User.id.in_(form.participants.data)).all()
    for u in selected_users:
        meeting.participantes.append(ReuniaoParticipante(id_usuario=u.id, username_usuario=u.username))
    participant_emails = [u.email for u in selected_users]
    participant_usernames = [u.username for u in selected_users]
    description = form.description.data or ""
    if participant_usernames:
        description += "\nParticipantes: " + ", ".join(participant_usernames)
    description += "\nStatus: Agendada"
    if meeting.google_event_id:
        updated_event = update_event(
            meeting.google_event_id,
            form.subject.data,
            start_dt,
            end_dt,
            description,
            participant_emails,
            create_meet=form.create_meet.data,
        )
        meeting.meet_link = updated_event.get("hangoutLink") if form.create_meet.data else None
    else:
        if form.create_meet.data:
            updated_event = create_meet_event(
                form.subject.data,
                start_dt,
                end_dt,
                description,
                participant_emails,
            )
        else:
            updated_event = create_event(
                form.subject.data,
                start_dt,
                end_dt,
                description,
                participant_emails,
            )
        meeting.meet_link = updated_event.get("hangoutLink")
        meeting.google_event_id = updated_event.get("id")
    db.session.commit()
    publish({"type": "updated", "meeting": serialize_meeting(meeting)})
    flash("Reunião atualizada com sucesso!", "success")
    return True, meeting.meet_link


def delete_meeting(meeting: Reuniao) -> bool:
    """Remove meeting from DB and Google Calendar.

    Returns ``True`` when both the local record and the external calendar
    event are deleted successfully. If the Google Calendar removal fails,
    the meeting is left untouched in the database and ``False`` is
    returned so the caller can warn the user.
    """
    meeting_id = meeting.id
    if meeting.google_event_id:
        try:
            delete_event(meeting.google_event_id)
        except Exception:
            return False
    db.session.delete(meeting)
    db.session.commit()
    publish({"type": "deleted", "id": meeting_id})
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
        if now < start_dt:
            status = ReuniaoStatus.AGENDADA
            status_label = "Agendada"
            color = "#ffc107"
        elif start_dt <= now <= end_dt:
            status = ReuniaoStatus.EM_ANDAMENTO
            status_label = "Em Andamento"
            color = "#198754"
        else:
            status = ReuniaoStatus.REALIZADA
            status_label = "Realizada"
            color = "#dc3545"
        if r.status != status:
            r.status = status
            updated = True
        can_edit = r.criador_id == current_user_id and status == ReuniaoStatus.AGENDADA
        can_delete = can_edit or is_admin
        event_data = {
            "id": r.id,
            "title": r.assunto,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "color": color,
            "description": r.descricao,
            "status": status_label,
            "creator": r.criador.username,
            "participants": [p.username_usuario for p in r.participantes],
            "participant_ids": [p.id_usuario for p in r.participantes],
            "meeting_id": r.id,
            "can_edit": can_edit,
            "can_delete": can_delete,
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
                "participants": attendees,
                "creator": creator_name,
                "can_edit": False,
                "can_delete": False,
            }
        )
        seen_keys.add(key)

    return events


def monthly_meeting_stats(year: int) -> list[dict]:
    """Return meeting count and average duration per month for ``year``."""
    stats: list[dict] = []
    for month in range(1, 13):
        start = datetime(year, month, 1, tzinfo=CALENDAR_TZ)
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=CALENDAR_TZ)
        else:
            end = datetime(year, month + 1, 1, tzinfo=CALENDAR_TZ)
        meetings = Reuniao.query.filter(
            Reuniao.inicio >= start, Reuniao.inicio < end
        ).all()
        count = len(meetings)
        if count:
            total = sum((m.fim - m.inicio for m in meetings), timedelta())
            avg_minutes = (total / count).total_seconds() / 60
        else:
            avg_minutes = 0.0
        stats.append(
            {
                "month": month,
                "count": count,
                "avg_duration_minutes": round(avg_minutes, 2),
            }
        )
    return stats
