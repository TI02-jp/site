"""Cliente simples para buscar entregas na API da Acessorias.

Usado para sincronizar o campo ``encerramento_fiscal`` no inventario.
"""

from __future__ import annotations

import logging
import os
import re
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, List, Sequence

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_BASE_URL = os.getenv("ACESSORIAS_BASE", "https://api.acessorias.com")
DEFAULT_TOKEN = (
    os.getenv("ACESSORIAS_DELIVERIES_TOKEN")
    or os.getenv("ACESSORIAS_TOKEN")
    or os.getenv("ACESSORIAS_API_TOKEN")
)


class DeliveriesAuthError(RuntimeError):
    """Token ausente ou invalido para acessar a API."""


class DeliveriesClientError(RuntimeError):
    """Erro generico ao consultar entregas."""


def _digits_only(identifier: str | None) -> str:
    return re.sub(r"\D", "", identifier or "")


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s or s in {"0000-00-00"}:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _flatten_entregas(payload: Any) -> list[dict]:
    """Normaliza o retorno da API em uma lista simples de entregas."""
    entregas: list[dict] = []
    if isinstance(payload, dict):
        entregas.extend(payload.get("Entregas") or [])
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                entregas.extend(item.get("Entregas") or [])
    return [e for e in entregas if isinstance(e, dict)]


@dataclass
class EntregaMatch:
    """Entrega que atende ao criterio de encerramento fiscal."""

    raw: dict
    referencia: date | None


class AcessoriasDeliveriesClient:
    """Client fino para a rota ``/deliveries``."""

    def __init__(
        self,
        token: str | None = None,
        base_url: str | None = None,
        timeout: int = 30,
        logger: logging.Logger | None = None,
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.token = token or DEFAULT_TOKEN
        self.timeout = timeout
        self.logger = logger
        self.session = requests.Session()
        # Configurar retry automático e pool de conexões
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504]
        )
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20)
        adapter.max_retries = retry
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)

    # -- Fetch helpers -------------------------------------------------
    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise DeliveriesAuthError(
                "Token da API Acessorias nao configurado (defina ACESSORIAS_DELIVERIES_TOKEN ou ACESSORIAS_TOKEN)."
            )
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    def fetch_deliveries(
        self,
        identificador: str,
        start_date: date,
        end_date: date,
        *,
        last_dh: str | None = None,
        include_config: bool = False,
    ) -> list[dict]:
        """
        Busca entregas paginadas para um identificador (CNPJ/CPF).

        A API limita 50 registros por pagina; a paginacao continua ate receber
        uma lista vazia.
        """
        ident = _digits_only(identificador)
        if not ident:
            raise DeliveriesClientError("Identificador vazio para consulta de entregas.")

        ident_encoded = urllib.parse.quote(ident, safe="")
        url = f"{self.base_url}/deliveries/{ident_encoded}/"
        params = {
            "DtInitial": start_date.isoformat(),
            "DtFinal": end_date.isoformat(),
        }
        if last_dh:
            params["DtLastDH"] = last_dh
        if include_config:
            params["config"] = 1

        all_entregas: list[dict] = []
        page = 1

        while True:
            params["Pagina"] = page
            if self.logger:
                self.logger.debug(
                    f"Buscando pagina {page} de entregas",
                    extra={"identificador": ident, "page": page, "url": url}
                )
            try:
                resp = self.session.get(
                    url,
                    headers=self._headers(),
                    params=params,
                    timeout=self.timeout,
                    proxies={"http": None, "https": None},
                )
            except requests.RequestException as exc:
                raise DeliveriesClientError(f"Erro de rede ao consultar entregas: {exc}") from exc

            if resp.status_code == 401:
                if self.logger:
                    self.logger.error(
                        "Token de entregas invalido ou expirado",
                        extra={"status_code": 401, "url": url}
                    )
                raise DeliveriesAuthError("Token de entregas invalido ou expirado.")
            if resp.status_code == 404:
                # CNPJ nao encontrado ou sem entregas no periodo
                if self.logger:
                    self.logger.info(
                        "CNPJ nao encontrado ou sem entregas no periodo",
                        extra={"identificador": ident, "status_code": 404}
                    )
                break
            try:
                resp.raise_for_status()
            except requests.HTTPError as exc:
                raise DeliveriesClientError(
                    f"Falha ao buscar entregas (status {resp.status_code}): {resp.text}"
                ) from exc

            try:
                payload = resp.json()
            except Exception as exc:  # pragma: no cover - protecao defensiva
                raise DeliveriesClientError("Resposta da API nao e JSON.") from exc

            page_entregas = _flatten_entregas(payload)
            if not page_entregas:
                break

            all_entregas.extend(page_entregas)
            if len(page_entregas) < 50:
                # A API indica final quando retorna menos que o limite por pagina
                break
            page += 1

        if self.logger:
            self.logger.info(
                "Entregas recebidas da Acessorias",
                extra={"identificador": ident, "total_entregas": len(all_entregas)},
            )
        return all_entregas

    # -- Business helpers ---------------------------------------------
    def find_encerramento_fiscal(
        self,
        entregas: Sequence[dict] | None,
        *,
        start_date: date,
        end_date: date,
    ) -> EntregaMatch | None:
        """
        Localiza a entrega com Nome exatamente "Fechamento Fiscal" (case-insensitive)
        e verifica se esta entregue conforme regras definidas.
        """
        if not entregas:
            return None

        for entrega in entregas:
            nome = (entrega.get("Nome") or "").strip()
            if nome.lower() != "fechamento fiscal":
                continue

            status_raw = (entrega.get("Status") or "").strip()
            status_lower = status_raw.lower()
            ent_dt_entrega_raw = (entrega.get("EntDtEntrega") or "").strip()
            ent_dt_entrega = _parse_date(ent_dt_entrega_raw)

            # Considera entregue apenas se:
            # - status indicar entrega (entregue/ent.) OU
            # - data de entrega preenchida E status nao comece com "pend"
            entregue_por_status = (
                status_lower.startswith("entreg")
                or status_lower.startswith("ent.")
                or status_lower.startswith("ent ")
                or "entreg" in status_lower
                or "ent." in status_lower
            )
            entregue_por_data = (
                ent_dt_entrega_raw not in ("", "0000-00-00")
                and not status_lower.startswith("pend")
            )

            if not (entregue_por_status or entregue_por_data):
                continue

            entrega_date = (
                ent_dt_entrega
                or _parse_date(entrega.get("EntDtPrazo"))
                or _parse_date(entrega.get("EntDtAtraso"))
                or _parse_date(entrega.get("EntCompetencia"))
            )
            if not entrega_date:
                continue

            if start_date <= entrega_date <= end_date:
                return EntregaMatch(raw=entrega, referencia=entrega_date)

        return None
