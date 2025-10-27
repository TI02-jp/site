"""Utility helpers for the internal collaborators calendar."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from flask import current_app, flash
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.models.tables import (
    GeneralCalendarEvent,
    GeneralCalendarEventParticipant,
    User,
)


_TIME_COLUMNS_VERIFIED = False


def _ensure_time_columns() -> None:
    """Make sure optional columns exist for general calendar events.

    Older databases might have been created before the migration that introduced
    the optional time fields.  Accessing the new code without running the
    migration would raise ``ProgrammingError`` when SQLAlchemy tries to persist
    values for the missing columns.  To provide a smoother upgrade path we
    lazily verify the schema and patch any missing columns in place.
    """

    global _TIME_COLUMNS_VERIFIED
    if _TIME_COLUMNS_VERIFIED:
        return

    engine = db.engine
    inspector = inspect(engine)

    if not inspector.has_table("general_calendar_events"):
        _TIME_COLUMNS_VERIFIED = True
        return

    existing_columns = {
        column["name"] for column in inspector.get_columns("general_calendar_events")
    }
    statements: list[str] = []

    if "start_time" not in existing_columns:
        statements.append(
            "ALTER TABLE general_calendar_events ADD COLUMN start_time TIME NULL"
        )
    if "end_time" not in existing_columns:
        statements.append(
            "ALTER TABLE general_calendar_events ADD COLUMN end_time TIME NULL"
        )
    if "is_birthday" not in existing_columns:
        statements.append(
            "ALTER TABLE general_calendar_events ADD COLUMN is_birthday BOOLEAN NOT NULL DEFAULT 0"
        )
    if "birthday_user_id" not in existing_columns:
        statements.append(
            "ALTER TABLE general_calendar_events ADD COLUMN birthday_user_id INTEGER NULL"
        )
    if "birthday_user_name" not in existing_columns:
        statements.append(
            "ALTER TABLE general_calendar_events ADD COLUMN birthday_user_name VARCHAR(255)"
        )

    if not statements:
        _TIME_COLUMNS_VERIFIED = True
        return

    try:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
    except SQLAlchemyError as exc:  # pragma: no cover - defensive guard
        current_app.logger.exception(
            "Não foi possível ajustar a tabela general_calendar_events", exc_info=exc
        )
        raise

    current_app.logger.info(
        "Colunas de horário adicionadas à tabela general_calendar_events"
    )
    _TIME_COLUMNS_VERIFIED = True


def populate_event_participants(form) -> None:
    """Populate the selectable participant list with active users."""

    choices = [
        (u.id, u.name)
        for u in User.query.filter_by(ativo=True).order_by(User.name).all()
    ]
    form.participants.choices = choices
    if hasattr(form, "birthday_user_id"):
        form.birthday_user_id.choices = [(0, "Selecione um colaborador")] + choices


def _selected_users(ids: Iterable[int]) -> list[User]:
    if not ids:
        return []
    return User.query.filter(User.id.in_(ids)).order_by(User.name).all()


def create_calendar_event_from_form(form, creator_id: int) -> GeneralCalendarEvent:
    """Persist a new calendar event using data from ``form``."""

    _ensure_time_columns()

    start_date = form.start_date.data
    end_date = form.end_date.data or start_date
    use_times = (
        start_date == end_date
        and form.start_time.data
        and form.end_time.data
    )
    is_birthday = bool(form.is_birthday.data)
    birthday_user_id = form.birthday_user_id.data or 0
    if is_birthday:
        end_date = start_date
    participant_ids = set(form.participants.data or [])
    if is_birthday and birthday_user_id:
        participant_ids.add(birthday_user_id)
    selected_users = _selected_users(participant_ids)
    birthday_user = None
    if is_birthday and birthday_user_id:
        birthday_user = next(
            (user for user in selected_users if user.id == birthday_user_id), None
        )
        if not birthday_user:
            birthday_user = User.query.get(birthday_user_id)
    event = GeneralCalendarEvent(
        title=form.title.data.strip(),
        description=(form.description.data or "").strip() or None,
        start_date=start_date,
        end_date=end_date,
        start_time=form.start_time.data if use_times else None,
        end_time=form.end_time.data if use_times else None,
        created_by_id=creator_id,
        is_birthday=is_birthday,
        birthday_user_id=birthday_user.id if birthday_user else None,
        birthday_user_name=(
            (birthday_user.name or birthday_user.username)
            if birthday_user
            else None
        ),
    )
    for user in selected_users:
        event.participants.append(
            GeneralCalendarEventParticipant(
                user_id=user.id,
                user_name=user.username,
            )
        )
    db.session.add(event)
    db.session.commit()
    flash("Evento criado com sucesso!", "success")
    return event


def update_calendar_event_from_form(
    event: GeneralCalendarEvent, form
) -> GeneralCalendarEvent:
    """Update ``event`` with the latest data from ``form``."""

    _ensure_time_columns()

    event.title = form.title.data.strip()
    event.description = (form.description.data or "").strip() or None
    event.start_date = form.start_date.data
    event.end_date = form.end_date.data or form.start_date.data
    use_times = (
        event.start_date == event.end_date
        and form.start_time.data
        and form.end_time.data
    )
    event.start_time = form.start_time.data if use_times else None
    event.end_time = form.end_time.data if use_times else None
    event.is_birthday = bool(form.is_birthday.data)
    birthday_user_id = form.birthday_user_id.data or 0
    if event.is_birthday:
        event.end_date = event.start_date
    participant_ids = set(form.participants.data or [])
    if event.is_birthday and birthday_user_id:
        participant_ids.add(birthday_user_id)
    event.participants.clear()
    selected_users = _selected_users(participant_ids)
    birthday_user = None
    if event.is_birthday and birthday_user_id:
        birthday_user = next(
            (user for user in selected_users if user.id == birthday_user_id), None
        )
        if not birthday_user:
            birthday_user = User.query.get(birthday_user_id)
    event.birthday_user_id = birthday_user.id if birthday_user else None
    event.birthday_user_name = (
        (birthday_user.name or birthday_user.username) if birthday_user else None
    )
    for user in selected_users:
        event.participants.append(
            GeneralCalendarEventParticipant(
                user_id=user.id,
                user_name=user.username,
            )
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
    for event in (
        GeneralCalendarEvent.query.order_by(
            GeneralCalendarEvent.start_date,
            GeneralCalendarEvent.start_time,
            GeneralCalendarEvent.title,
        ).all()
    ):
        can_edit = can_manage_all and (is_admin or event.created_by_id == current_user_id)
        can_delete = can_edit or is_admin
        start_iso = event.start_date.isoformat()
        end_iso = (event.end_date + timedelta(days=1)).isoformat()
        all_day = True
        if (
            event.start_date == event.end_date
            and event.start_time
            and event.end_time
        ):
            start_iso = datetime.combine(event.start_date, event.start_time).isoformat()
            end_iso = datetime.combine(event.end_date, event.end_time).isoformat()
            all_day = False
        sorted_participants = sorted(
            event.participants,
            key=lambda participant: (
                0 if participant.user_id == 33 else 1,
                (participant.user.username if participant.user else participant.user_name or "").lower(),
                participant.user_id if participant.user_id is not None else 10**9,
                participant.id,
            ),
        )
        participants = [
            participant.user.username if participant.user else participant.user_name
            for participant in sorted_participants
        ]
        creator_username = event.created_by.username if event.created_by else None
        is_tadeu_event = any(
            username and username.lower() == "tadeu"
            for username in participants + ([creator_username] if creator_username else [])
        )
        is_birthday = bool(event.is_birthday)

        events.append(
            {
                "id": event.id,
                "title": event.title,
                "start": start_iso,
                "end": end_iso,
                "allDay": all_day,
                "start_date": event.start_date.isoformat(),
                "end_date": event.end_date.isoformat(),
                "start_time": event.start_time.strftime("%H:%M") if event.start_time else None,
                "end_time": event.end_time.strftime("%H:%M") if event.end_time else None,
                "description": event.description,
                "creator": creator_username,
                "participants": participants,
                "participant_ids": [p.user_id for p in sorted_participants],
                "can_edit": can_edit,
                "can_delete": can_delete,
                "is_tadeu_event": is_tadeu_event,
                "is_birthday": is_birthday,
                "birthday_user_id": event.birthday_user_id,
                "birthday_user_name": event.birthday_user_name,
                "backgroundColor": "#f58220" if is_birthday else None,
                "borderColor": "#f58220" if is_birthday else None,
                "textColor": "#ffffff" if is_birthday else None,
                "classNames": ["birthday-event"] if is_birthday else [],
            }
        )

    events.sort(
        key=lambda data: (
            data.get("start_date") or "",
            data.get("start_time") or "",
            0 if data.get("is_tadeu_event") else 1,
            (data.get("title") or "").lower(),
        )
    )
    return events
