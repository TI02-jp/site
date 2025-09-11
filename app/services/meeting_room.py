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
    SAO_PAULO_TZ,
)
from app.services.google_calendar import (
    list_upcoming_events,
    create_meet_event,
    create_event,
    update_event,
    delete_event,
)

MIN_GAP = timedelta(minutes=2)


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
    """Create meeting adjusting times to avoid conflicts."""
    start_dt = datetime.combine(form.date.data, form.start_time.data).replace(
        tzinfo=SAO_PAULO_TZ
    )
    end_dt = datetime.combine(form.date.data, form.end_time.data).replace(
        tzinfo=SAO_PAULO_TZ
    )
    duration = end_dt - start_dt
    intervals: list[tuple[datetime, datetime]] = []
    for e in raw_events:
        existing_start = isoparse(e["start"].get("dateTime") or e["start"].get("date"))
        existing_end = isoparse(e["end"].get("dateTime") or e["end"].get("date"))
        intervals.append((existing_start, existing_end))
    for r in Reuniao.query.all():
        existing_start = datetime.combine(r.data_reuniao, r.hora_inicio).replace(
            tzinfo=SAO_PAULO_TZ
        )
        existing_end = datetime.combine(r.data_reuniao, r.hora_fim).replace(
            tzinfo=SAO_PAULO_TZ
        )
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
        return None

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
        data_reuniao=form.date.data,
        hora_inicio=form.start_time.data,
        hora_fim=form.end_time.data,
        assunto=form.subject.data,
        descricao=form.description.data,
        status="agendada",
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
    if meet_link:
        flash(
            Markup(
                f'Reunião criada com sucesso! <a href="{meet_link}" target="_blank">Link do Meet</a>'
            ),
            "success",
        )
    else:
        flash("Reunião criada com sucesso!", "success")
    return True


def update_meeting(form, raw_events, now, meeting: Reuniao):
    """Update existing meeting adjusting for conflicts and syncing with Google Calendar."""
    if meeting.status != "agendada":
        flash("A reunião só pode ser editada enquanto estiver agendada.", "warning")
        return None
    start_dt = datetime.combine(form.date.data, form.start_time.data).replace(tzinfo=SAO_PAULO_TZ)
    end_dt = datetime.combine(form.date.data, form.end_time.data).replace(tzinfo=SAO_PAULO_TZ)
    duration = end_dt - start_dt
    intervals: list[tuple[datetime, datetime]] = []
    for e in raw_events:
        existing_start = isoparse(e["start"].get("dateTime") or e["start"].get("date"))
        existing_end = isoparse(e["end"].get("dateTime") or e["end"].get("date"))
        intervals.append((existing_start, existing_end))
    for r in Reuniao.query.filter(Reuniao.id != meeting.id).all():
        existing_start = datetime.combine(r.data_reuniao, r.hora_inicio).replace(tzinfo=SAO_PAULO_TZ)
        existing_end = datetime.combine(r.data_reuniao, r.hora_fim).replace(tzinfo=SAO_PAULO_TZ)
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
        return None
    meeting.data_reuniao = form.date.data
    meeting.hora_inicio = form.start_time.data
    meeting.hora_fim = form.end_time.data
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
        )
        meeting.meet_link = updated_event.get("hangoutLink", meeting.meet_link)
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
    flash("Reunião atualizada com sucesso!", "success")
    return True


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


def combine_events(raw_events, now, current_user_id: int):
    """Combine Google and local events updating their status."""
    events: list[dict] = []
    seen_keys: set[tuple[str, str, str]] = set()

    google_ids = {e.get("id") for e in raw_events if e.get("id")}
    removed = False
    for r in Reuniao.query.filter(Reuniao.google_event_id.isnot(None)).all():
        if r.google_event_id not in google_ids:
            db.session.delete(r)
            removed = True
    if removed:
        db.session.commit()

    updated = False

    # Prioritize locally stored meetings so their metadata (including
    # edit permissions) is preserved. Google events are added later only
    # if they don't match an existing local meeting.
    for r in Reuniao.query.all():
        start_dt = datetime.combine(r.data_reuniao, r.hora_inicio).replace(
            tzinfo=SAO_PAULO_TZ
        )
        end_dt = datetime.combine(r.data_reuniao, r.hora_fim).replace(
            tzinfo=SAO_PAULO_TZ
        )
        key = (r.assunto, start_dt.isoformat(), end_dt.isoformat())
        if now < start_dt:
            status = "agendada"
            status_label = "Agendada"
            color = "#ffc107"
        elif start_dt <= now <= end_dt:
            status = "em andamento"
            status_label = "Em Andamento"
            color = "#198754"
        else:
            status = "realizada"
            status_label = "Realizada"
            color = "#dc3545"
        if r.status != status:
            r.status = status
            updated = True
        event_data = {
            "title": r.assunto,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "color": color,
            "description": r.descricao,
            "status": status_label,
            "participants": [p.username_usuario for p in r.participantes],
            "participant_ids": [p.id_usuario for p in r.participantes],
            "meeting_id": r.id,
            "can_edit": r.criador_id == current_user_id and r.status == "agendada",
            "can_delete": r.criador_id == current_user_id,
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
        start_dt = isoparse(start_str).astimezone(SAO_PAULO_TZ)
        end_dt = isoparse(end_str).astimezone(SAO_PAULO_TZ)
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
        events.append(
            {
                "title": e.get("summary", "Sem título"),
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "description": e.get("description"),
                "meet_link": e.get("hangoutLink"),
                "color": color,
                "status": status_label,
                "participants": attendees,
                "can_edit": False,
                "can_delete": False,
            }
        )
        seen_keys.add(key)

    return events
