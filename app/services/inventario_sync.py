"""Rotinas de sincronizacao do inventario com dados externos."""

from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
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

# Configuração de paralelismo
MAX_WORKERS = 10
BATCH_COMMIT_SIZE = 50


@dataclass
class SyncResult:
    checked: int = 0
    updated: int = 0
    set_true: int = 0
    set_false: int = 0
    skipped_no_cnpj: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "updated": self.updated,
            "set_true": self.set_true,
            "set_false": self.set_false,
            "skipped_no_cnpj": self.skipped_no_cnpj,
            "errors": self.errors or [],
        }


@dataclass
class EmpresaProcessResult:
    """Resultado do processamento de uma empresa individual."""
    inventario_id: int
    should_update: bool = False
    skipped_no_cnpj: bool = False
    skipped_already_true: bool = False
    error: dict[str, Any] | None = None
    match_info: dict[str, Any] | None = None


def _clean_cnpj(cnpj: str | None) -> str:
    return re.sub(r"\D", "", cnpj or "")


def _process_single_empresa(
    inventario: Inventario,
    client: AcessoriasDeliveriesClient,
    start_date: date,
    end_date: date,
    last_dh: str | None,
    log: logging.Logger,
) -> EmpresaProcessResult:
    """
    Processa uma única empresa de forma isolada (thread-safe para a parte de API).
    Retorna o resultado sem modificar o banco.
    """
    result = EmpresaProcessResult(inventario_id=inventario.id)
    
    empresa = inventario.empresa
    if not empresa:
        return result

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
        result.skipped_no_cnpj = True
        return result

    if inventario.encerramento_fiscal is True:
        log.debug(
            "Empresa ja marcada como encerrada; pulando reprocessamento",
            extra={"empresa_id": empresa.id, "cnpj": cnpj},
        )
        result.skipped_already_true = True
        return result

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
        # Erro de token: propaga para abortar todo o processo
        raise
    except DeliveriesClientError as exc:
        result.error = {
            "empresa_id": empresa.id,
            "razao_social": getattr(empresa, "razao_social", "N/A"),
            "cnpj": cnpj,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "timestamp": datetime.now().isoformat(),
        }
        log.error(
            "Erro ao buscar entregas para empresa",
            extra={
                "empresa_id": empresa.id,
                "cnpj": cnpj,
                "error": str(exc),
            }
        )
        return result

    match: EntregaMatch | None = client.find_encerramento_fiscal(
        entregas,
        start_date=start_date,
        end_date=end_date,
    )

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
        result.should_update = True
        result.match_info = {
            "entrega_nome": match.raw.get("Nome"),
            "entrega_status": match.raw.get("Status"),
        }
    else:
        log.debug(
            "Nenhuma entrega de Fechamento Fiscal encontrada",
            extra={
                "empresa_id": empresa.id,
                "cnpj": cnpj,
                "total_entregas": len(entregas),
            }
        )

    return result


def sync_encerramento_fiscal(
    *,
    start_date: date = DEFAULT_PERIOD_START,
    end_date: date = DEFAULT_PERIOD_END,
    last_dh: str | None = None,
    logger: logging.Logger | None = None,
    max_workers: int = MAX_WORKERS,
) -> SyncResult:
    """
    Atualiza ``encerramento_fiscal`` para empresas ativas no inventario.

    Criterio: existencia de entrega "Fechamento Fiscal" entregue no periodo informado.
    Apenas atualiza de False/None para True (nao desmarca quem ja esta como True).
    
    Utiliza processamento paralelo para chamadas à API externa (5-10x mais rápido).
    """
    log = logger or logging.getLogger(__name__)
    result = SyncResult()

    client = AcessoriasDeliveriesClient(logger=log)

    inventarios: list[Inventario] = (
        Inventario.query.join(Empresa)
        .filter(Empresa.ativo.is_(True))
        .options(joinedload(Inventario.empresa))
        .all()
    )

    # Mapeamento de inventario_id -> inventario para atualização posterior
    inventario_map: dict[int, Inventario] = {inv.id: inv for inv in inventarios}
    
    log.info(
        "Iniciando sync paralelo de encerramento fiscal",
        extra={
            "total_empresas": len(inventarios),
            "max_workers": max_workers,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
    )

    # Lista para coletar resultados que precisam de update
    pending_updates: list[int] = []  # inventario_ids
    auth_error: DeliveriesAuthError | None = None
    lock = threading.Lock()

    def process_with_error_handling(inv: Inventario) -> EmpresaProcessResult | None:
        nonlocal auth_error
        try:
            return _process_single_empresa(
                inv, client, start_date, end_date, last_dh, log
            )
        except DeliveriesAuthError as e:
            with lock:
                auth_error = e
            return None

    # Processar em paralelo
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_with_error_handling, inv): inv 
            for inv in inventarios
        }
        
        for future in as_completed(futures):
            # Verificar se houve erro de autenticação
            if auth_error:
                break
                
            proc_result = future.result()
            if proc_result is None:
                continue
                
            if proc_result.skipped_no_cnpj:
                result.skipped_no_cnpj += 1
                continue
                
            if proc_result.skipped_already_true:
                continue
                
            if proc_result.error:
                result.errors.append(proc_result.error)
                continue
            
            # Contabilizar como verificado
            result.checked += 1
            
            if proc_result.should_update:
                pending_updates.append(proc_result.inventario_id)

    # Se houve erro de autenticação, propagar
    if auth_error:
        raise auth_error

    # Aplicar updates no banco de dados (thread principal, sequencial)
    # Com commits em batches para segurança
    batch_count = 0
    for inv_id in pending_updates:
        inventario = inventario_map.get(inv_id)
        if inventario and inventario.encerramento_fiscal is not True:
            inventario.encerramento_fiscal = True
            result.updated += 1
            result.set_true += 1
            batch_count += 1
            
            # Commit em batches
            if batch_count >= BATCH_COMMIT_SIZE:
                db.session.commit()
                log.debug(f"Commit intermediário: {batch_count} registros")
                batch_count = 0

    # Commit final dos registros restantes
    if batch_count > 0:
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
