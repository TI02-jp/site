"""Rotinas de sincronizacao do inventario com dados externos."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable

from sqlalchemy.orm import joinedload

from app import db
from app.models.tables import Empresa, Inventario
from app.services.acessorias_deliveries import (
    AcessoriasDeliveriesClient,
    DeliveriesAuthError,
    DeliveriesClientError,
    EntregaMatch,
)

DEFAULT_PERIOD_START = date(2026, 1, 1)
DEFAULT_PERIOD_END = date(2026, 1, 31)


@dataclass
class SyncResult:
    checked: int = 0
    updated: int = 0
    set_true: int = 0
    set_false: int = 0
    skipped_no_cnpj: int = 0
    errors: list[dict[str, Any]] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "updated": self.updated,
            "set_true": self.set_true,
            "set_false": self.set_false,
            "skipped_no_cnpj": self.skipped_no_cnpj,
            "errors": self.errors or [],
        }


def _clean_cnpj(cnpj: str | None) -> str:
    return re.sub(r"\D", "", cnpj or "")


def sync_encerramento_fiscal(
    *,
    start_date: date = DEFAULT_PERIOD_START,
    end_date: date = DEFAULT_PERIOD_END,
    last_dh: str | None = None,
    logger: logging.Logger | None = None,
) -> SyncResult:
    """
    Atualiza ``encerramento_fiscal`` para empresas ativas no inventario.

    Criterio: existencia de entrega "Fechamento Fiscal" entregue no periodo informado.
    Apenas atualiza de False/None para True (nao desmarca quem ja esta como True).
    """
    log = logger or logging.getLogger(__name__)
    result = SyncResult(errors=[])

    client = AcessoriasDeliveriesClient(logger=log)

    inventarios: Iterable[Inventario] = (
        Inventario.query.join(Empresa)
        .filter(Empresa.ativo.is_(True))
        .options(joinedload(Inventario.empresa))
        .all()
    )

    for inventario in inventarios:
        empresa = inventario.empresa
        if not empresa:
            continue

        cnpj = _clean_cnpj(getattr(empresa, "cnpj", None))
        if len(cnpj) != 14:
            log.warning(
                "Empresa pulada: CNPJ invalido",
                extra={
                    "empresa_id": empresa.id,
                    "razao_social": getattr(empresa, "razao_social", "N/A"),
                    "cnpj_raw": getattr(empresa, "cnpj", None),
                    "cnpj_limpo": cnpj,
                }
            )
            result.skipped_no_cnpj += 1
            continue

        if inventario.encerramento_fiscal is True:
            log.debug(
                "Empresa ja marcada como encerrada; pulando reprocessamento",
                extra={"empresa_id": empresa.id, "cnpj": cnpj},
            )
            continue

        result.checked += 1

        log.info(
            "Buscando entregas para empresa",
            extra={
                "empresa_id": empresa.id,
                "cnpj": cnpj,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            }
        )

        try:
            entregas = client.fetch_deliveries(
                cnpj,
                start_date,
                end_date,
                last_dh=last_dh,
                include_config=False,
            )
        except DeliveriesAuthError:
            # Erro de token: aborta para nao mascarar credencial invalida
            raise
        except DeliveriesClientError as exc:
            result.errors.append(
                {
                    "empresa_id": empresa.id,
                    "razao_social": getattr(empresa, "razao_social", "N/A"),
                    "cnpj": cnpj,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "timestamp": datetime.now().isoformat(),
                }
            )
            log.error(
                "Erro ao buscar entregas para empresa",
                extra={
                    "empresa_id": empresa.id,
                    "cnpj": cnpj,
                    "error": str(exc),
                }
            )
            continue

        match: EntregaMatch | None = client.find_encerramento_fiscal(
            entregas,
            start_date=start_date,
            end_date=end_date,
        )
        new_value = bool(match)

        if match:
            log.info(
                "Encerramento Fiscal encontrado",
                extra={
                    "empresa_id": empresa.id,
                    "cnpj": cnpj,
                    "entrega_nome": match.raw.get("Nome"),
                    "entrega_status": match.raw.get("Status"),
                    "referencia": match.referencia.isoformat() if match.referencia else None,
                }
            )
        else:
            log.debug(
                "Nenhuma entrega de Fechamento Fiscal encontrada",
                extra={
                    "empresa_id": empresa.id,
                    "cnpj": cnpj,
                    "total_entregas": len(entregas),
                }
            )

        if not new_value:
            continue

        if inventario.encerramento_fiscal is True:
            continue

        inventario.encerramento_fiscal = True
        result.updated += 1
        result.set_true += 1

    if result.updated:
        db.session.commit()

    log.info(
        "Sincronizacao de encerramento fiscal concluida",
        extra={
            "checked": result.checked,
            "updated": result.updated,
            "set_true": result.set_true,
            "set_false": result.set_false,
            "skipped_no_cnpj": result.skipped_no_cnpj,
            "errors": len(result.errors or []),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
    )

    return result
