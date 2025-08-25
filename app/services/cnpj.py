import os
import re
import requests
from datetime import datetime
from requests import RequestException

from app.utils.logging_utils import log_integracao_externa

ACESSORIAS_BASE = "https://api.acessorias.com"
ACESSORIAS_TOKEN = os.getenv("ACESSORIAS_TOKEN")


def get_acessorias_company(cnpj: str) -> dict | bool | None:
    if not ACESSORIAS_TOKEN:
        return None
    url = f"{ACESSORIAS_BASE}/companies/{cnpj}"
    headers = {"Authorization": f"Bearer {ACESSORIAS_TOKEN}", "Accept": "application/json"}
    try:
        r = requests.get(url, headers=headers, timeout=20, proxies={"http": None, "https": None})
        log_integracao_externa({"cnpj": cnpj}, r.status_code, cnpj)
    except RequestException as e:
        log_integracao_externa({"cnpj": cnpj, "erro": str(e)}, 'exception', cnpj)
        return None
    if r.status_code == 200:
        try:
            return r.json()
        except Exception:
            return None
    if r.status_code == 404:
        return False
    return None


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


def pick(d: dict, *keys):
    """Retorna o primeiro valor não vazio encontrado nas chaves fornecidas."""
    if not isinstance(d, dict):
        return ""
    for key in keys:
        v = d.get(key)
        if v not in (None, "", [], {}):
            return v
    return ""


def deep_pick(obj, keys: set[str]):
    """Busca recursivamente o primeiro valor encontrado para qualquer uma das chaves informadas."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in keys and v not in (None, "", [], {}):
                return v
            res = deep_pick(v, keys)
            if res not in (None, "", [], {}):
                return res
    elif isinstance(obj, list):
        for item in obj:
            res = deep_pick(item, keys)
            if res not in (None, "", [], {}):
                return res
    return ""


def regime_to_tributacao(value) -> str:
    """Mapeia códigos ou descrições de regime tributário para rótulos usados no formulário."""
    if value in (None, "", [], {}):
        return ""
    s = str(value).strip()
    try:
        i = int(s)
    except ValueError:
        sl = s.lower()
        if "simples" in sl:
            return "Simples Nacional"
        if "presum" in sl:
            return "Lucro Presumido"
        if "real" in sl:
            return "Lucro Real"
        return ""
    if i in (1, 2):
        return "Simples Nacional"
    if i in (3, 4):
        return "Lucro Presumido"
    if i == 5:
        return "Lucro Real"
    return ""


def mapear_para_acessorias(d: dict) -> dict:
    """Converte dados de CNPJ para o payload do POST /companies."""
    payload = {
        "cnpj": somente_numeros(pick(d, "cnpj", "identificador")),
        "nome": pick(d, "razao_social", "nome", "razao", "nome_fantasia"),
        "fantasia": pick(d, "nome_fantasia", "fantasia"),
        "dtabertura": ymd(pick(d, "data_inicio_atividade", "abertura", "data_abertura")),
        "endlogradouro": pick(d, "logradouro", "descricao_tipo_de_logradouro"),
        "endnumero": pick(d, "numero"),
        "endcomplemento": pick(d, "complemento"),
        "bairro": pick(d, "bairro"),
        "cep": somente_numeros(pick(d, "cep")),
        "cidade": pick(d, "municipio", "cidade"),
        "uf": pick(d, "uf", "estado"),
        "fone": pick(d, "telefone"),
    }
    situacao = pick(d, "situacao_cadastral", "situacao")
    if situacao:
        payload["ativa"] = "S" if str(situacao).upper() in ("2", "ATIVA", "ATIVO") else "N"
    return {k: v for k, v in payload.items() if v not in ("", None)}


def upsert_acessorias_company(payload: dict) -> dict | None:
    if not ACESSORIAS_TOKEN:
        return None
    url = f"{ACESSORIAS_BASE}/companies"
    headers = {
        "Authorization": f"Bearer {ACESSORIAS_TOKEN}",
        "Accept": "application/json",
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30, proxies={"http": None, "https": None})
        log_integracao_externa(payload, r.status_code, payload.get('cnpj'))
    except RequestException as e:
        log_integracao_externa({**payload, 'erro': str(e)}, 'exception', payload.get('cnpj'))
        return None
    try:
        data = r.json()
    except Exception:
        return None
    return data if r.status_code in (200, 201) else None


def get_brasilapi_cnpj(cnpj: str) -> dict | None:
    url = f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
    r = requests.get(url, timeout=20, proxies={"http": None, "https": None})
    if r.status_code == 200:
        return r.json()
    return None


def get_receitaws_cnpj(cnpj: str) -> dict | None:
    url = f"https://www.receitaws.com.br/v1/cnpj/{cnpj}"
    r = requests.get(url, timeout=20, proxies={"http": None, "https": None})
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
    # Atividade principal
    atividade = ""
    ap = pick(d, "atividade_principal", "descricao_atividade_principal")
    if isinstance(ap, list) and ap:
        atividade = pick(ap[0], "text", "descricao") or ""
    elif isinstance(ap, dict):
        atividade = pick(ap, "text", "descricao") or ""
    elif isinstance(ap, str):
        atividade = ap

    # Sócios administradores
    socio = ""
    qsa = pick(d, "qsa", "quadro_societario")
    if isinstance(qsa, list):
        admins = []
        for s in qsa:
            nome = pick(s, "nome", "nome_socio", "nome_rep_legal")
            qual = (pick(s, "qualificacao", "qualificacao_socio", "qualificacao_rep_legal") or "").upper()
            if "ADMIN" in qual or "SÓCIO" in qual:
                if nome:
                    admins.append(nome)
        if admins:
            socio = ", ".join(admins)

    payload = {
        "nome_empresa": pick(d, "razao_social", "nome", "razao", "nome_fantasia"),
        "data_abertura": ymd(pick(d, "data_inicio_atividade", "abertura", "data_abertura")),
        "atividade_principal": atividade,
        "socio_administrador": socio,
    }

    # Tributação
    tributacao = regime_to_tributacao(pick(d, "regime", "regime_tributario", "tributacao"))
    if not tributacao:
        if pick(d, "opcao_pelo_simples") in (True, "SIM", "Sim", "S"):
            tributacao = "Simples Nacional"
        else:
            simples = pick(d, "simples") or {}
            optante = pick(simples, "optante", "optanteSimples", "optante_simples")
            if isinstance(optante, str):
                optante = optante.upper() in ("SIM", "S", "ATIVO", "ATIVA")
            if optante:
                tributacao = "Simples Nacional"
    if tributacao:
        payload["tributacao"] = tributacao

    # Campos adicionais para possível preenchimento automático
    payload.update({
        "telefone": pick(d, "telefone"),
        "cep": somente_numeros(pick(d, "cep")),
        "logradouro": pick(d, "logradouro", "descricao_tipo_de_logradouro"),
        "numero": pick(d, "numero"),
        "complemento": pick(d, "complemento"),
        "bairro": pick(d, "bairro"),
        "municipio": pick(d, "municipio", "cidade"),
        "uf": pick(d, "uf", "estado"),
    })

    return {k: v for k, v in payload.items() if v not in ("", None)}


def consultar_cnpj(cnpj_input: str) -> dict | None:
    cnpj = somente_numeros(cnpj_input)
    if len(cnpj) != 14:
        raise ValueError("CNPJ inválido")
    dados = get_brasilapi_cnpj(cnpj)
    if not dados:
        dados = get_receitaws_cnpj(cnpj)
    if not dados:
        return None

    payload = mapear_para_form(dados)
    base = get_acessorias_company(cnpj)
    if base is False and ACESSORIAS_TOKEN:
        base = upsert_acessorias_company(mapear_para_acessorias(dados))
        if not base:
            raise ValueError("CNPJ não está cadastrado")

    if base:
        keys = {
            "id",
            "codigo",
            "cod",
            "code",
            "empresa_id",
            "empresaId",
            "empresaID",
            "id_empresa",
            "company_id",
        }
        codigo = deep_pick(base, {k.lower() for k in keys})
        if codigo:
            payload["codigo_empresa"] = str(codigo)
        trib = regime_to_tributacao(deep_pick(base, {"regime", "regime_tributario", "tributacao"}))
        if trib:
            payload["tributacao"] = trib
    return payload
