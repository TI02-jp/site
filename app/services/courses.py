"""Utilities for serving curated course information in the portal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum


class CourseStatus(str, Enum):
    """Enumeration of supported course statuses."""

    COMPLETED = "concluído"
    PLANNED = "planejado"
    DELAYED = "atrasado"


@dataclass(frozen=True)
class CourseRecord:
    """Structured information describing a single course."""

    name: str
    instructor: str
    sectors: tuple[str, ...]
    participants: tuple[str, ...]
    workload: time | None
    start_date: date
    schedule: time | None
    completion_date: date | None
    status: CourseStatus
    completion_note: str | None = None

    @property
    def start_date_label(self) -> str:
        """Return the formatted start date for display in tables."""

        return self.start_date.strftime("%d/%m/%Y")

    @property
    def completion_label(self) -> str:
        """Return the formatted completion date or fallback note."""

        if self.completion_date:
            return self.completion_date.strftime("%d/%m/%Y")
        if self.completion_note:
            return self.completion_note
        return "-"

    @property
    def workload_label(self) -> str:
        """Return the formatted workload time or a fallback placeholder."""

        return _format_time_label(self.workload)

    @property
    def schedule_label(self) -> str:
        """Return the formatted schedule time or a fallback placeholder."""

        return _format_time_label(self.schedule)

    @property
    def sectors_list(self) -> list[str]:
        """Return the sectors as a mutable list for template iteration."""

        return list(self.sectors)

    @property
    def participants_list(self) -> list[str]:
        """Return the participants as a mutable list for template iteration."""

        return list(self.participants)


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


def get_courses_overview() -> list[CourseRecord]:
    """Return all registered courses ordered by the most recent start date."""

    from app.models.tables import Course

    records: list[CourseRecord] = []
    for course in Course.query.order_by(Course.start_date.desc()).all():
        try:
            status = CourseStatus(course.status)
        except ValueError:
            status = CourseStatus.PLANNED
        records.append(
            CourseRecord(
                name=course.name,
                instructor=course.instructor,
                sectors=_split_values(course.sectors),
                participants=_split_values(course.participants),
                workload=_parse_time(course.workload),
                start_date=course.start_date,
                schedule=_parse_time(course.schedule),
                completion_date=course.completion_date,
                completion_note=None,
                status=status,
            )
        )
    return records


__all__ = [
    "CourseRecord",
    "CourseStatus",
    "get_courses_overview",
]
