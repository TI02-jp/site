import os
import re
from datetime import datetime
from typing import Any, Dict, Optional

import requests

TOKEN_ACESSORIAS = os.getenv("TOKEN_ACESSORIAS", "")
BASE_ACESSORIAS = "https://api.acessorias.com"
ENVIAR_PARA_ACESSORIAS = os.getenv("ENVIAR_PARA_ACESSORIAS") == "True"


def somente_numeros(s: str) -> str:
    """Remove todos os caracteres não numéricos."""
    return re.sub(r"\D", "", s or "")

def ymd(d: str) -> Optional[str]:
    """Converte datas diversas para o formato YYYY-MM-DD."""
    if not d:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(d, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def get_acessorias_company(cnpj: str) -> Optional[Dict[str, Any]]:
    """Consulta no Acessórias dados já cadastrados."""
    if not TOKEN_ACESSORIAS:
        return None
    url = f"{BASE_ACESSORIAS}/companies/{cnpj}"
    headers = {"Authorization": f"Bearer {TOKEN_ACESSORIAS}", "Accept": "application/json"}
    r = requests.get(url, headers=headers, timeout=20)
    if r.status_code == 200:
        try:
            return r.json()
        except Exception:
            return {"raw": r.text}
    if r.status_code == 404:
        return None
    return None


def get_brasilapi_cnpj(cnpj: str) -> Optional[Dict[str, Any]]:
    url = f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
    r = requests.get(url, timeout=20)
    if r.status_code == 200:
        return r.json()
    return None


def get_receitaws_cnpj(cnpj: str) -> Optional[Dict[str, Any]]:
    url = f"https://www.receitaws.com.br/v1/cnpj/{cnpj}"
    r = requests.get(url, timeout=20)
    if r.status_code == 200:
        try:
            data = r.json()
            if data.get("status") == "ERROR":
                return None
            return data
        except Exception:
            return None
    return None


def mapear_para_acessorias(d: Dict[str, Any]) -> Dict[str, Any]:
    """Mapeia campos externos para o payload do Acessórias."""
    payload = {
        "cnpj": somente_numeros(d.get("cnpj") or d.get("identificador") or ""),
        "nome": d.get("razao_social") or d.get("nome") or d.get("razao") or "",
        "fantasia": d.get("nome_fantasia") or d.get("fantasia") or "",
        "dtabertura": ymd(d.get("data_inicio_atividade") or d.get("abertura")),
        "endlogradouro": (d.get("logradouro") or d.get("descricao_tipo_de_logradouro") or "")
                         + ((" " + d.get("titulo_do_logradouro")) if d.get("titulo_do_logradouro") else ""),
        "endnumero": d.get("numero") or "",
        "endcomplemento": d.get("complemento") or "",
        "bairro": d.get("bairro") or "",
        "cep": somente_numeros(d.get("cep") or ""),
        "cidade": d.get("municipio") or d.get("cidade") or "",
        "uf": d.get("uf") or d.get("estado") or "",
        "fone": d.get("telefone") or "",
        "ativa": "S" if (d.get("situacao_cadastral") in (2, "ATIVA", "Ativa")) else "N",
    }
    payload = {k: v for k, v in payload.items() if v not in ("", None)}
    return payload


def upsert_acessorias_company(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not TOKEN_ACESSORIAS:
        return None
    url = f"{BASE_ACESSORIAS}/companies"
    headers = {"Authorization": f"Bearer {TOKEN_ACESSORIAS}", "Accept": "application/json"}
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    try:
        data = r.json()
    except Exception:
        data = {"status_code": r.status_code, "raw": r.text}
    return data


def consultar_cnpj(cnpj_input: str) -> Optional[Dict[str, Any]]:
    """Orquestra a consulta no Acessórias e fontes externas."""
    cnpj = somente_numeros(cnpj_input)
    base = get_acessorias_company(cnpj)
    externos = get_brasilapi_cnpj(cnpj) or get_receitaws_cnpj(cnpj)
    if not externos:
        return {"acessorias": base}
    payload = mapear_para_acessorias(externos)
    if ENVIAR_PARA_ACESSORIAS:
        upsert_acessorias_company(payload)
    return {"acessorias": base, "externo": externos, "payload": payload}
