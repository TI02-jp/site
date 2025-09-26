"""Utilities for serving curated course information in the portal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum

import sqlalchemy as sa

from app import db


class CourseStatus(str, Enum):
    """Enumeration of supported course statuses."""

    COMPLETED = "concluído"
    PLANNED = "planejado"
    DELAYED = "atrasado"


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
    def sectors_list(self) -> list[str]:
        """Return the sectors as a mutable list for template iteration."""

        return list(self.sectors)

    @property
    def participants_list(self) -> list[str]:
        """Return the participants as a mutable list for template iteration."""

        return list(self.participants)

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

        return self.start_date.isoformat()

    @property
    def completion_date_value(self) -> str:
        """Return the ISO completion date suitable for date inputs."""

        return self.completion_date.isoformat() if self.completion_date else ""


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


def get_courses_overview() -> list[CourseRecord]:
    """Return all registered courses prioritizing upcoming plans and fresh completions."""

    from app.models.tables import Course

    status_priority = sa.case(
        (Course.status == CourseStatus.COMPLETED.value, 1),
        else_=0,
    )

    most_recent_date = sa.case(
        (Course.status == CourseStatus.COMPLETED.value, sa.func.coalesce(Course.completion_date, Course.start_date)),
        else_=Course.start_date,
    )

    stmt = (
        sa.select(
            Course.id,
            Course.name,
            Course.instructor,
            Course.sectors,
            Course.participants,
            sa.cast(Course.workload, sa.String).label("workload"),
            Course.start_date,
            sa.cast(Course.schedule_start, sa.String).label("schedule_start"),
            sa.cast(Course.schedule_end, sa.String).label("schedule_end"),
            Course.completion_date,
            Course.status,
        )
        .order_by(status_priority.asc(), most_recent_date.desc(), Course.id.desc())
    )

    records: list[CourseRecord] = []
    for row in db.session.execute(stmt).mappings():
        try:
            status = CourseStatus(row["status"])
        except ValueError:
            status = CourseStatus.PLANNED
        records.append(
            CourseRecord(
                id=row["id"],
                name=row["name"],
                instructor=row["instructor"],
                sectors=_split_values(row["sectors"]),
                participants=_split_values(row["participants"]),
                workload=_parse_time(row["workload"]),
                start_date=row["start_date"],
                schedule_start=_parse_time(row["schedule_start"]),
                schedule_end=_parse_time(row["schedule_end"]),
                completion_date=row["completion_date"],
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
