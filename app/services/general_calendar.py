"""Utility helpers for the internal collaborators calendar."""

from __future__ import annotations

from datetime import timedelta
from typing import Iterable

from flask import flash

from app import db
from app.models.tables import (
    GeneralCalendarEvent,
    GeneralCalendarEventParticipant,
    User,
)


DEFAULT_EVENT_COLOR = "#0d6efd"


def populate_event_participants(form) -> None:
    """Populate the selectable participant list with active users."""

    form.participants.choices = [
        (u.id, u.name)
        for u in User.query.filter_by(ativo=True).order_by(User.name).all()
    ]


def _selected_users(ids: Iterable[int]) -> list[User]:
    if not ids:
        return []
    return User.query.filter(User.id.in_(ids)).order_by(User.name).all()


def create_calendar_event_from_form(form, creator_id: int) -> GeneralCalendarEvent:
    """Persist a new calendar event using data from ``form``."""

    end_date = form.end_date.data or form.start_date.data
    event = GeneralCalendarEvent(
        title=form.title.data.strip(),
        description=(form.description.data or "").strip() or None,
        start_date=form.start_date.data,
        end_date=end_date,
        created_by_id=creator_id,
    )
    selected_users = _selected_users(form.participants.data)
    for user in selected_users:
        event.participants.append(
            GeneralCalendarEventParticipant(user_id=user.id, user_name=user.name)
        )
    db.session.add(event)
    db.session.commit()
    flash("Evento criado com sucesso!", "success")
    return event


def update_calendar_event_from_form(
    event: GeneralCalendarEvent, form
) -> GeneralCalendarEvent:
    """Update ``event`` with the latest data from ``form``."""

    event.title = form.title.data.strip()
    event.description = (form.description.data or "").strip() or None
    event.start_date = form.start_date.data
    event.end_date = form.end_date.data or form.start_date.data
    event.participants.clear()
    selected_users = _selected_users(form.participants.data)
    for user in selected_users:
        event.participants.append(
            GeneralCalendarEventParticipant(user_id=user.id, user_name=user.name)
        )
    db.session.commit()
    flash("Evento atualizado com sucesso!", "success")
    return event


def delete_calendar_event(event: GeneralCalendarEvent) -> None:
    """Remove an event from the database."""

    db.session.delete(event)
    db.session.commit()


def serialize_events_for_calendar(
    current_user_id: int,
    can_manage_all: bool,
    is_admin: bool,
) -> list[dict]:
    """Return events formatted for FullCalendar consumption."""

    events: list[dict] = []
    for event in GeneralCalendarEvent.query.order_by(GeneralCalendarEvent.start_date).all():
        can_edit = can_manage_all and (is_admin or event.created_by_id == current_user_id)
        can_delete = can_edit or is_admin
        events.append(
            {
                "id": event.id,
                "title": event.title,
                "start": event.start_date.isoformat(),
                "end": (event.end_date + timedelta(days=1)).isoformat(),
                "allDay": True,
                "start_date": event.start_date.isoformat(),
                "end_date": event.end_date.isoformat(),
                "description": event.description,
                "creator": event.created_by.name if event.created_by else None,
                "participants": [p.user_name for p in event.participants],
                "participant_ids": [p.user_id for p in event.participants],
                "color": DEFAULT_EVENT_COLOR,
                "can_edit": can_edit,
                "can_delete": can_delete,
            }
        )
    return events
