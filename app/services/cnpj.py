import re
import requests
from datetime import datetime


def somente_numeros(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def ymd(d: str) -> str | None:
    if not d:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(d, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def get_brasilapi_cnpj(cnpj: str) -> dict | None:
    url = f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
    r = requests.get(url, timeout=20)
    if r.status_code == 200:
        return r.json()
    return None


def get_receitaws_cnpj(cnpj: str) -> dict | None:
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


def mapear_para_form(d: dict) -> dict:
    atividade = ""
    if isinstance(d.get("atividade_principal"), list) and d["atividade_principal"]:
        atividade = d["atividade_principal"][0].get("text") or d["atividade_principal"][0].get("descricao") or ""
    else:
        atividade = d.get("descricao_atividade_principal") or d.get("atividade_principal") or ""

    socio = ""
    qsa = d.get("qsa") or d.get("quadro_societario")
    if isinstance(qsa, list):
        admins = []
        for s in qsa:
            nome = s.get("nome") or s.get("nome_socio") or s.get("nome_rep_legal")
            qual = (s.get("qualificacao") or s.get("qualificacao_socio") or "").upper()
            if "ADMIN" in qual or "SÓCIO" in qual:
                if nome:
                    admins.append(nome)
        if admins:
            socio = ", ".join(admins)

    payload = {
        "nome_empresa": d.get("razao_social") or d.get("nome_fantasia") or "",
        "data_abertura": ymd(d.get("data_inicio_atividade") or d.get("abertura")),
        "atividade_principal": atividade,
        "socio_administrador": socio,
    }

    # Tributação (Simples Nacional) quando possível
    tributacao = ""
    if d.get("opcao_pelo_simples") in (True, "SIM", "Sim", "S"):
        tributacao = "Simples Nacional"
    else:
        simples = d.get("simples") or {}
        optante = simples.get("optante") or simples.get("optanteSimples") or simples.get("optante_simples")
        if isinstance(optante, str):
            optante = optante.upper() in ("SIM", "S", "ATIVO", "ATIVA")
        if optante:
            tributacao = "Simples Nacional"
    if tributacao:
        payload["tributacao"] = tributacao

    # Usa o próprio CNPJ como código da empresa por padrão
    cnpj_limpo = somente_numeros(d.get("cnpj") or "")
    if cnpj_limpo:
        payload["codigo_empresa"] = cnpj_limpo

    return {k: v for k, v in payload.items() if v not in ("", None)}


def consultar_cnpj(cnpj_input: str) -> dict | None:
    cnpj = somente_numeros(cnpj_input)
    dados = get_brasilapi_cnpj(cnpj)
    if not dados:
        dados = get_receitaws_cnpj(cnpj)
    if not dados:
        return None
    return mapear_para_form(dados)
