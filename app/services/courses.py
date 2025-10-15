"""Utilities for serving curated course information in the portal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum

import sqlalchemy as sa
from sqlalchemy.orm import selectinload

from app import db


class CourseStatus(str, Enum):
    """Enumeration of supported course statuses."""

    COMPLETED = "concluído"
    PLANNED = "planejado"
    DELAYED = "atrasado"
    POSTPONED = "adiada"
    CANCELLED = "cancelada"


@dataclass(frozen=True)
class CourseRecord:
    """Structured information describing a single course."""

    id: int
    name: str
    instructor: str
    sectors: tuple[str, ...]
    participants: tuple[str, ...]
    workload: time | None
    start_date: date
    schedule_start: time | None
    schedule_end: time | None
    completion_date: date | None
    status: CourseStatus
    completion_note: str | None = None
    observation: str | None = None
    tags: tuple[str, ...] = tuple()
    tag_ids: tuple[int, ...] = tuple()
    participant_sectors: tuple[str, ...] = tuple()  # Setores/tags incluídos por completo

    @property
    def start_date_label(self) -> str:
        """Return the formatted start date for display in tables."""

        normalized = _coerce_date(self.start_date)
        return normalized.strftime("%d/%m/%Y") if normalized else "-"

    @property
    def completion_label(self) -> str:
        """Return the formatted completion date or fallback note."""

        normalized = _coerce_date(self.completion_date)
        if normalized:
            return normalized.strftime("%d/%m/%Y")
        if self.completion_note:
            return self.completion_note
        return "-"

    @property
    def workload_label(self) -> str:
        """Return the formatted workload time or a fallback placeholder."""

        return _format_time_label(self.workload)

    @property
    def schedule_start_label(self) -> str:
        """Return the formatted start time or a fallback placeholder."""

        return _format_time_label(self.schedule_start)

    @property
    def schedule_end_label(self) -> str:
        """Return the formatted end time or a fallback placeholder."""

        return _format_time_label(self.schedule_end)

    @property
    def schedule_label(self) -> str:
        """Return a combined label with both start and end times."""

        return _format_time_range(self.schedule_start, self.schedule_end)

    @property
    def observation_label(self) -> str:
        """Return the stored observation or a fallback placeholder."""

        return (self.observation or "-").strip() or "-"

    @property
    def sectors_list(self) -> list[str]:
        """Return the sectors as a mutable list for template iteration."""

        return list(self.sectors)

    @property
    def participants_list(self) -> list[str]:
        """Return the participants as a mutable list for template iteration."""

        return list(self.participants)

    @property
    def tags_list(self) -> list[str]:
        """Return the tags as a mutable list for template iteration."""

        return list(self.tags)

    @property
    def tag_ids_list(self) -> list[int]:
        """Return the tag identifiers as a mutable list for serialization."""

        return list(self.tag_ids)

    @property
    def participant_sectors_list(self) -> list[str]:
        """Return the sectors with complete membership as a list."""

        return list(self.participant_sectors)

    @property
    def workload_value(self) -> str:
        """Return the raw workload value formatted for form inputs."""

        return self.workload.strftime("%H:%M") if self.workload else ""

    @property
    def schedule_start_value(self) -> str:
        """Return the raw schedule start formatted for form inputs."""

        return self.schedule_start.strftime("%H:%M") if self.schedule_start else ""

    @property
    def schedule_end_value(self) -> str:
        """Return the raw schedule end formatted for form inputs."""

        return self.schedule_end.strftime("%H:%M") if self.schedule_end else ""

    @property
    def start_date_value(self) -> str:
        """Return the ISO start date suitable for date inputs."""

        normalized = _coerce_date(self.start_date)
        return normalized.isoformat() if normalized else ""

    @property
    def completion_date_value(self) -> str:
        """Return the ISO completion date suitable for date inputs."""

        normalized = _coerce_date(self.completion_date)
        return normalized.isoformat() if normalized else ""


def _split_values(raw: str) -> tuple[str, ...]:
    """Normalize comma-separated strings into a tuple of values."""

    if not raw:
        return tuple()
    parts = [segment.strip() for segment in raw.replace(";", ",").split(",")]
    return tuple(part for part in parts if part)


def _parse_time(raw: str | time | None) -> time | None:
    """Parse persisted values into ``datetime.time`` objects when possible."""

    if raw is None:
        return None
    if isinstance(raw, time):
        return raw.replace(second=0, microsecond=0)
    if isinstance(raw, str):
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                parsed = datetime.strptime(raw, fmt).time()
                return parsed.replace(second=0, microsecond=0)
            except ValueError:
                continue
    return None


def _format_time_label(raw: time | None) -> str:
    """Return the formatted time value for display in the UI."""

    if not raw:
        return "-"
    return raw.strftime("%H:%M")


def _format_time_range(start: time | None, end: time | None) -> str:
    """Return a human-readable label representing the schedule window."""

    start_label = _format_time_label(start)
    end_label = _format_time_label(end)

    if start_label == "-" and end_label == "-":
        return "-"
    if end_label == "-":
        return start_label
    if start_label == "-":
        return end_label
    return f"{start_label} - {end_label}"


def _coerce_date(value: date | datetime | None) -> date | None:
    """Normalize database date or datetime payloads to ``date`` objects."""

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    return value


def get_courses_overview() -> list[CourseRecord]:
    """Return all registered courses prioritizing upcoming plans and fresh completions."""

    from app.models.tables import Course, Tag, User

    status_priority = sa.case(
        (Course.status == CourseStatus.COMPLETED.value, 1),
        else_=0,
    )

    most_recent_date = sa.case(
        (Course.status == CourseStatus.COMPLETED.value, sa.func.coalesce(Course.completion_date, Course.start_date)),
        else_=Course.start_date,
    )

    stmt = (
        sa.select(Course)
        .options(selectinload(Course.tags))
        .order_by(status_priority.asc(), most_recent_date.desc(), Course.id.desc())
    )

    # Carregar todos os usuários com suas tags para análise
    users_with_tags = db.session.execute(
        sa.select(User).where(User.ativo == True).options(selectinload(User.tags))
    ).scalars().all()

    # Criar mapa de tag -> usuários
    tag_users_map: dict[str, set[str]] = {}
    for user in users_with_tags:
        for tag in user.tags:
            tag_name = tag.nome.strip()
            if tag_name not in tag_users_map:
                tag_users_map[tag_name] = set()
            tag_users_map[tag_name].add(user.name.strip())

    records: list[CourseRecord] = []
    for course in db.session.execute(stmt).scalars():
        try:
            status = CourseStatus(course.status)
        except ValueError:
            status = CourseStatus.PLANNED
        tags_sorted = sorted(course.tags, key=lambda tag: tag.name.lower())

        # Analisar participantes do curso
        course_sectors = _split_values(course.sectors or "")
        course_participants = set(p.strip() for p in _split_values(course.participants or ""))

        # Detectar quais setores/tags estão completamente incluídos
        optimized_participants: list[str] = []
        complete_sectors: list[str] = []
        remaining_participants = set(course_participants)

        for sector in course_sectors:
            sector_users = tag_users_map.get(sector.strip(), set())
            if sector_users and sector_users.issubset(course_participants):
                # Todos os usuários desta tag estão incluídos
                sector_name = sector.strip()
                optimized_participants.append(sector_name)
                complete_sectors.append(sector_name)
                # Remover estes usuários da lista de participantes individuais
                remaining_participants -= sector_users

        # Adicionar participantes individuais restantes
        optimized_participants.extend(sorted(remaining_participants))

        records.append(
            CourseRecord(
                id=course.id,
                name=course.name,
                instructor=course.instructor,
                sectors=course_sectors,
                participants=tuple(optimized_participants),
                workload=_parse_time(course.workload),
                start_date=course.start_date,
                schedule_start=_parse_time(course.schedule_start),
                schedule_end=_parse_time(course.schedule_end),
                completion_date=course.completion_date,
                completion_note=None,
                status=status,
                observation=course.observation,
                tags=tuple(tag.name for tag in tags_sorted),
                tag_ids=tuple(tag.id for tag in tags_sorted),
                participant_sectors=tuple(complete_sectors),
            )
        )
    return records


__all__ = [
    "CourseRecord",
    "CourseStatus",
    "get_courses_overview",
]

