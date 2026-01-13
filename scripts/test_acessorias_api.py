"""
Script de teste para a API da Acessorias - Rota /deliveries.

Este script testa a conexão com a API da Acessorias e verifica se as entregas
de "Fechamento Fiscal" estão sendo retornadas corretamente.

Uso:
    python scripts/test_acessorias_api.py <CNPJ>

Exemplo:
    python scripts/test_acessorias_api.py 12345678901234
"""

import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

# Adicionar o diretório raiz ao path para importar módulos
sys.path.insert(0, str(Path(__file__).parent.parent))

# Carregar .env
from dotenv import load_dotenv
load_dotenv()

import requests


def _clean_cnpj(cnpj: str) -> str:
    """Remove caracteres não numéricos do CNPJ."""
    return re.sub(r"\D", "", cnpj or "")


def _parse_date(value):
    """Parse de data no formato YYYY-MM-DD ou outros formatos comuns."""
    if not value:
        return None
    s = str(value).strip()
    if not s or s in {"0000-00-00"}:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def test_api(cnpj: str, start_date: date, end_date: date):
    """Testa a API da Acessorias para um CNPJ específico."""

    # Obter token do ambiente
    token = (
        os.getenv("ACESSORIAS_DELIVERIES_TOKEN")
        or os.getenv("ACESSORIAS_TOKEN")
        or os.getenv("ACESSORIAS_API_TOKEN")
    )

    base_url = os.getenv("ACESSORIAS_BASE", "https://api.acessorias.com")

    print("=" * 80)
    print("TESTE DA API DA ACESSORIAS - ROTA /DELIVERIES")
    print("=" * 80)
    print()

    # Validar token
    if not token:
        print("❌ ERRO: Token não encontrado!")
        print()
        print("Configure uma das seguintes variáveis no .env:")
        print("  - ACESSORIAS_DELIVERIES_TOKEN")
        print("  - ACESSORIAS_TOKEN")
        print("  - ACESSORIAS_API_TOKEN")
        return False

    print(f"✓ Token configurado: {token[:10]}...{token[-10:]}")
    print(f"✓ Base URL: {base_url}")
    print()

    # Limpar CNPJ
    cnpj_clean = _clean_cnpj(cnpj)
    if len(cnpj_clean) != 14:
        print(f"⚠ AVISO: CNPJ tem {len(cnpj_clean)} dígitos (esperado: 14)")
        print(f"  CNPJ fornecido: {cnpj}")
        print(f"  CNPJ limpo: {cnpj_clean}")
        print()

    # Construir URL
    url = f"{base_url}/deliveries/{cnpj_clean}/"
    params = {
        "DtInitial": start_date.isoformat(),
        "DtFinal": end_date.isoformat(),
    }

    print(f"📡 Fazendo requisição para:")
    print(f"  URL: {url}")
    print(f"  Parâmetros: {params}")
    print()

    # Fazer requisição
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=30,
            proxies={"http": None, "https": None},
        )

        print(f"📥 Resposta recebida:")
        print(f"  Status Code: {response.status_code}")
        print(f"  Headers: {dict(response.headers)}")
        print()

        # Verificar erros HTTP
        if response.status_code == 401:
            print("❌ ERRO 401: Token inválido ou expirado")
            print(f"  Resposta: {response.text}")
            return False

        if response.status_code == 404:
            print("⚠ ERRO 404: CNPJ não encontrado ou sem entregas no período")
            print(f"  Resposta: {response.text}")
            return False

        if response.status_code != 200:
            print(f"❌ ERRO {response.status_code}: {response.text}")
            return False

        # Parse JSON
        try:
            payload = response.json()
        except Exception as e:
            print(f"❌ ERRO: Resposta não é JSON válido")
            print(f"  Exceção: {e}")
            print(f"  Resposta: {response.text[:500]}")
            return False

        # Mostrar estrutura da resposta
        print("✓ Resposta JSON recebida")
        print()
        print("📋 ESTRUTURA DA RESPOSTA:")
        print("-" * 80)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        print("-" * 80)
        print()

        # Flatten entregas
        entregas = []
        if isinstance(payload, dict):
            entregas.extend(payload.get("Entregas") or [])
        elif isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    entregas.extend(item.get("Entregas") or [])

        print(f"📦 Total de entregas encontradas: {len(entregas)}")
        print()

        if not entregas:
            print("⚠ Nenhuma entrega encontrada no período")
            return True

        # Analisar entregas
        print("🔍 ANÁLISE DAS ENTREGAS:")
        print("-" * 80)

        fechamento_fiscal_found = False

        for i, entrega in enumerate(entregas, 1):
            nome = str(entrega.get("Nome") or "").strip()
            status = str(entrega.get("Status") or "").strip()

            # Datas
            ent_competencia = entrega.get("EntCompetencia")
            ent_dt_prazo = entrega.get("EntDtPrazo")
            ent_dt_entrega = entrega.get("EntDtEntrega")
            ent_dt_atraso = entrega.get("EntDtAtraso")

            print(f"\nEntrega #{i}:")
            print(f"  Nome: {nome}")
            print(f"  Status: {status}")
            print(f"  Datas:")
            print(f"    EntCompetencia: {ent_competencia}")
            print(f"    EntDtPrazo: {ent_dt_prazo}")
            print(f"    EntDtEntrega: {ent_dt_entrega}")
            print(f"    EntDtAtraso: {ent_dt_atraso}")

            # Verificar se match "Fechamento Fiscal"
            nome_lower = nome.lower()
            status_lower = status.lower()

            is_fechamento = "fechamento fiscal" in nome_lower
            is_entregue = status_lower.startswith("entregue")

            # Parse data de referência
            data_ref = (
                _parse_date(ent_competencia)
                or _parse_date(ent_dt_prazo)
                or _parse_date(ent_dt_entrega)
                or _parse_date(ent_dt_atraso)
            )

            is_date_match = False
            if data_ref:
                is_date_match = start_date <= data_ref <= end_date
                print(f"  Data de referência parseada: {data_ref.isoformat()}")
                print(f"  Data dentro do período? {is_date_match}")
            else:
                print(f"  Data de referência: Nenhuma data válida encontrada")

            print(f"  Critérios de match:")
            print(f"    ✓ Nome contém 'fechamento fiscal'? {is_fechamento}")
            print(f"    ✓ Status começa com 'entregue'? {is_entregue}")
            print(f"    ✓ Data no período? {is_date_match}")

            # Resultado final
            is_match = is_fechamento and is_entregue and is_date_match
            if is_match:
                print(f"  🎯 MATCH! Esta entrega atende aos critérios de Encerramento Fiscal")
                fechamento_fiscal_found = True
            else:
                reasons = []
                if not is_fechamento:
                    reasons.append("nome não contém 'fechamento fiscal'")
                if not is_entregue:
                    reasons.append("status não começa com 'entregue'")
                if not is_date_match:
                    reasons.append("data fora do período ou inválida")
                print(f"  ✗ Não match: {', '.join(reasons)}")

        print("-" * 80)
        print()

        # Resumo final
        print("📊 RESUMO:")
        print(f"  Total de entregas: {len(entregas)}")
        print(f"  Fechamento Fiscal encontrado? {'✓ SIM' if fechamento_fiscal_found else '✗ NÃO'}")
        print()

        if fechamento_fiscal_found:
            print("✓ SUCESSO: Encerramento Fiscal será marcado como SIM")
        else:
            print("⚠ Encerramento Fiscal será marcado como NÃO")

        return True

    except requests.RequestException as e:
        print(f"❌ ERRO DE REDE: {e}")
        return False
    except Exception as e:
        print(f"❌ ERRO INESPERADO: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Função principal."""
    if len(sys.argv) < 2:
        print("Uso: python scripts/test_acessorias_api.py <CNPJ>")
        print()
        print("Exemplo:")
        print("  python scripts/test_acessorias_api.py 12345678901234")
        sys.exit(1)

    cnpj = sys.argv[1]

    # Período fixo: janeiro 2026 (período de entrega do fechamento de dezembro/2025)
    start_date = date(2026, 1, 1)
    end_date = date(2026, 1, 31)

    success = test_api(cnpj, start_date, end_date)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
