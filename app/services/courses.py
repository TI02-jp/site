"""Utilities for serving curated course information in the portal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
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
    workload: str
    start_date: date
    schedule: str
    completion_date: date | None
    completion_note: str | None
    status: CourseStatus

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
    def sectors_list(self) -> list[str]:
        """Return the sectors as a mutable list for template iteration."""

        return list(self.sectors)

    @property
    def participants_list(self) -> list[str]:
        """Return the participants as a mutable list for template iteration."""

        return list(self.participants)


def get_courses_overview() -> list[CourseRecord]:
    """Return a curated list of highlighted courses."""

    return [
        CourseRecord(
            name="Impactos da Reforma Tributária nas empresas do Simples Nacional",
            instructor="Ivonizia Fonseca Cunha",
            sectors=("Simples Nacional", "Serviços"),
            participants=("Ryan", "Maria", "Pablo", "Cássio", "Izabel"),
            workload="03h15min",
            start_date=date(2025, 3, 10),
            schedule="13h30",
            completion_date=date(2025, 3, 10),
            completion_note=None,
            status=CourseStatus.COMPLETED,
        ),
        CourseRecord(
            name="Formação tributária de rendimentos financeiros (Pessoa Jurídica)",
            instructor="Édson Rodrigues",
            sectors=("Turma I", "Financeiro"),
            participants=("Ryan", "Maria", "Rafa", "Cássio", "Izabel"),
            workload="01 hora",
            start_date=date(2025, 3, 24),
            schedule="08h00",
            completion_date=None,
            completion_note="A confirmar finalização com instrutor",
            status=CourseStatus.PLANNED,
        ),
        CourseRecord(
            name="Treinamento intensivo de inteligência artificial",
            instructor="Édson Rodrigues",
            sectors=("Turma II", "Gestão"),
            participants=("Helena", "Fernando", "Isadora", "Michele", "Simone"),
            workload="02h30min",
            start_date=date(2025, 2, 18),
            schedule="10h00",
            completion_date=date(2025, 2, 18),
            completion_note=None,
            status=CourseStatus.COMPLETED,
        ),
        CourseRecord(
            name="Workshop de atendimento consultivo",
            instructor="Édson / Gustavo",
            sectors=("Consultivo", "Relacionamento"),
            participants=("Gustavo", "Letícia", "Vinícius", "Ana Paula"),
            workload="03h45min",
            start_date=date(2025, 1, 15),
            schedule="15h30",
            completion_date=date(2025, 1, 15),
            completion_note=None,
            status=CourseStatus.COMPLETED,
        ),
        CourseRecord(
            name="Capacitação em LGPD para escritórios contábeis",
            instructor="Ingrid Nogueira",
            sectors=("Turma III", "Jurídico", "TI"),
            participants=("Helena", "Fernando", "Rafa", "Murilo", "Ana Paula"),
            workload="04h00min",
            start_date=date(2024, 11, 12),
            schedule="09h00",
            completion_date=date(2025, 1, 19),
            completion_note=None,
            status=CourseStatus.DELAYED,
        ),
    ]


__all__ = [
    "CourseRecord",
    "CourseStatus",
    "get_courses_overview",
]
