from __future__ import annotations

from datetime import datetime
from datetime import datetime
from typing import Dict, Iterable, Optional

from config import Config
from integrations.acessorias.client import AcessoriasClient
from app import db
from app.models.tables import Empresa, CompanyObligation


def _client() -> AcessoriasClient:
    return AcessoriasClient(Config.ACESSORIAS_API_BASE, Config.ACESSORIAS_API_TOKEN)


def fetch_company_from_acessorias(identifier: str) -> Optional[Dict]:
    client = _client()
    return client.get_company(identifier)


def sync_company_by_identifier(identifier: str) -> Optional[Empresa]:
    company = Empresa.query.filter_by(acessorias_identifier=identifier).first()
    if not company:
        company = Empresa.query.filter_by(cnpj=identifier).first()
    if not company:
        return None

    payload = fetch_company_from_acessorias(identifier)
    if not payload:
        return None

    company.nome_empresa = payload.get("Razao") or company.nome_empresa
    company.acessorias_identifier = identifier
    company.acessorias_company_id = payload.get("Id")
    company.acessorias_synced_at = datetime.utcnow()
    db.session.add(company)
    obligations = payload.get("Obrigacoes") or []
    sync_obligations(company, obligations)
    db.session.commit()
    return company


def sync_obligations(company: Empresa, payload: Iterable[Dict]) -> None:
    for item in payload:
        nome = item.get("Nome")
        if not nome:
            continue
        ob = CompanyObligation.query.filter_by(company_id=company.id, nome=nome).first()
        if not ob:
            ob = CompanyObligation(company_id=company.id, nome=nome)
        ob.status = item.get("Status")
        ob.entregues = item.get("Entregues")
        ob.atrasadas = item.get("Atrasadas")
        ob.proximos_30d = item.get("Proximos30d")
        ob.futuras_30p = item.get("Futuras30p")
        db.session.add(ob)
