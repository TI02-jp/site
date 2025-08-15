import os
import re
from datetime import datetime
from typing import Optional, Dict

import requests

TOKEN_ACESSORIAS = os.getenv("ACESSORIAS_API_TOKEN")
BASE_ACESSORIAS = os.getenv("ACESSORIAS_API_URL", "https://api.acessorias.com")


def somente_numeros(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def ymd(d: str) -> Optional[str]:
    if not d:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(d, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def get_acessorias_company(cnpj: str) -> Optional[Dict]:
    if not TOKEN_ACESSORIAS:
        return None
    url = f"{BASE_ACESSORIAS}/companies/{cnpj}"
    headers = {"Authorization": f"Bearer {TOKEN_ACESSORIAS}", "Accept": "application/json"}
    try:
        r = requests.get(url, headers=headers, timeout=20)
    except requests.RequestException:
        return None
    if r.status_code == 200:
        try:
            return r.json()
        except Exception:
            return {"raw": r.text}
    if r.status_code == 404:
        return None
    return None


def get_brasilapi_cnpj(cnpj: str) -> Optional[Dict]:
    url = f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
    try:
        r = requests.get(url, timeout=20)
    except requests.RequestException:
        return None
    if r.status_code == 200:
        try:
            return r.json()
        except Exception:
            return None
    return None


def get_receitaws_cnpj(cnpj: str) -> Optional[Dict]:
    url = f"https://www.receitaws.com.br/v1/cnpj/{cnpj}"
    try:
        r = requests.get(url, timeout=20)
    except requests.RequestException:
        return None
    if r.status_code == 200:
        try:
            data = r.json()
        except Exception:
            return None
        if data.get("status") == "ERROR":
            return None
        return data
    return None


def buscar_empresa_por_cnpj(cnpj: str) -> Optional[Dict]:
    cnpj = somente_numeros(cnpj)
    dados = get_acessorias_company(cnpj)
    if not dados:
        dados = get_brasilapi_cnpj(cnpj) or get_receitaws_cnpj(cnpj)
    if not dados:
        return None

    resultado: Dict[str, str] = {}
    resultado["nome_empresa"] = (
        dados.get("nome")
        or dados.get("fantasia")
        or dados.get("razao_social")
        or dados.get("nome_fantasia")
    )
    resultado["data_abertura"] = ymd(
        dados.get("dtabertura")
        or dados.get("data_inicio_atividade")
        or dados.get("abertura")
    )

    socio = None
    qsa = dados.get("qsa")
    if isinstance(qsa, list) and qsa:
        socio = qsa[0].get("nome")
    elif dados.get("socio_administrador"):
        socio = dados.get("socio_administrador")
    if socio:
        resultado["socio_administrador"] = socio

    atividade = None
    if isinstance(dados.get("atividade_principal"), list) and dados["atividade_principal"]:
        primeiro = dados["atividade_principal"][0]
        if isinstance(primeiro, dict):
            atividade = (
                primeiro.get("descricao")
                or primeiro.get("text")
                or primeiro.get("descricao_da_atividade")
            )
    atividade = (
        atividade
        or dados.get("descricao_atividade_economica_principal")
        or dados.get("cnae_fiscal_descricao")
    )
    if atividade:
        resultado["atividade_principal"] = atividade
    # remove empty fields so callers can map only available data
    resultado = {k: v for k, v in resultado.items() if v}
    return resultado or None
