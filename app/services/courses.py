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
            name="Onboarding Fiscal para novos analistas",
            instructor="Ivonizia Fonseca Cunha",
            sectors=("Fiscal", "Folha de Pagamento"),
            participants=("Ryan", "Maria Clara", "Pablo", "Cássio"),
            workload="02h30",
            start_date=date(2025, 1, 27),
            schedule="09h00",
            completion_date=date(2025, 1, 27),
            completion_note=None,
            status=CourseStatus.COMPLETED,
        ),
        CourseRecord(
            name="Oficina prática: Fechamento Contábil 2024",
            instructor="Gustavo Oliveira",
            sectors=("Contábil", "Consultivo"),
            participants=("Letícia", "Vinícius", "Ana Paula", "Izabel"),
            workload="03h15",
            start_date=date(2025, 2, 14),
            schedule="14h00",
            completion_date=date(2025, 2, 14),
            completion_note=None,
            status=CourseStatus.COMPLETED,
        ),
        CourseRecord(
            name="Capacitação em LGPD aplicada a escritórios contábeis",
            instructor="Ingrid Nogueira",
            sectors=("Jurídico", "TI", "Controladoria"),
            participants=("Helena", "Fernando", "Murilo", "Michele"),
            workload="04h00",
            start_date=date(2025, 3, 5),
            schedule="08h30",
            completion_date=None,
            completion_note="Aguardando entrega do case final",
            status=CourseStatus.DELAYED,
        ),
        CourseRecord(
            name="Treinamento em Inteligência Artificial aplicada à rotina contábil",
            instructor="Édson Rodrigues",
            sectors=("Gestão", "Inovação"),
            participants=("Helena", "Isadora", "Michele", "Simone"),
            workload="02h00",
            start_date=date(2025, 3, 18),
            schedule="10h30",
            completion_date=None,
            completion_note="Entrega prevista após tutoria 1:1",
            status=CourseStatus.PLANNED,
        ),
        CourseRecord(
            name="Workshop de atendimento consultivo e experiência do cliente",
            instructor="Édson / Gustavo",
            sectors=("Relacionamento", "Sucesso do Cliente"),
            participants=("Gustavo", "Letícia", "Vinícius", "Ana Paula"),
            workload="03h30",
            start_date=date(2025, 4, 7),
            schedule="15h30",
            completion_date=None,
            completion_note="Planejado para a Semana do Cliente",
            status=CourseStatus.PLANNED,
        ),
        CourseRecord(
            name="Atualização sobre impactos da Reforma Tributária no Simples Nacional",
            instructor="Ivonizia Fonseca Cunha",
            sectors=("Simples Nacional", "Parcerias"),
            participants=("Ryan", "Maria Clara", "Rafa", "Cássio", "Izabel"),
            workload="01h45",
            start_date=date(2025, 5, 9),
            schedule="13h30",
            completion_date=None,
            completion_note="Conteúdo em desenvolvimento pela consultoria",
            status=CourseStatus.PLANNED,
        ),
    ]


__all__ = [
    "CourseRecord",
    "CourseStatus",
    "get_courses_overview",
]
