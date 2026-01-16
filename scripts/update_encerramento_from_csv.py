"""
Atualiza encerramento_fiscal=True para empresas listadas em um CSV.

Uso:
    # Usa scripts/fechamentofiscal.csv automaticamente:
    python scripts/update_encerramento_from_csv.py
    python scripts/update_encerramento_from_csv.py --dry-run

    # Ou especifica outro arquivo:
    python scripts/update_encerramento_from_csv.py C:\\caminho\\outro.csv

O CSV deve conter uma coluna com CNPJ (pode ser: CNPJ, cnpj, Cnpj, etc).
Empresas encontradas terão encerramento_fiscal marcado como True.

Opcoes:
    --dry-run   Nao grava; apenas mostra quantas seriam atualizadas.
"""

import argparse
import csv
import re
import sys
from pathlib import Path

# Adiciona o diretório raiz do projeto ao PYTHONPATH
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from dotenv import load_dotenv

# Carrega variaveis do .env
load_dotenv()

from app import app, db  # noqa: E402
from app.models.tables import Empresa, Inventario  # noqa: E402


def limpar_cnpj(cnpj_raw: str) -> str:
    """Remove caracteres não numéricos do CNPJ."""
    if not cnpj_raw:
        return ""
    return re.sub(r"\D", "", str(cnpj_raw).strip())


def ler_cnpjs_do_csv(csv_path: str) -> set[str]:
    """
    Lê o CSV e retorna um set com CNPJs limpos (apenas números).

    Procura automaticamente por colunas com nome parecido com CNPJ.
    Detecta automaticamente o delimitador (vírgula ou ponto e vírgula).
    """
    cnpjs = set()

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        # Ler primeira linha para detectar delimitador
        first_line = f.readline()
        f.seek(0)

        # Detectar delimitador: contar ; e , na primeira linha
        semicolon_count = first_line.count(";")
        comma_count = first_line.count(",")

        if semicolon_count > comma_count:
            delimiter = ";"
        else:
            delimiter = ","

        print(f"✓ Delimitador detectado: '{delimiter}' ({semicolon_count} ponto e vírgula, {comma_count} vírgulas)")

        reader = csv.DictReader(f, delimiter=delimiter)

        # Debug: mostrar colunas encontradas
        print(f"✓ Colunas encontradas: {reader.fieldnames}")

        # Procurar coluna de CNPJ (case-insensitive)
        cnpj_col = None
        for col in reader.fieldnames or []:
            if col.strip().upper() in ["CNPJ", "CPF/CNPJ", "CNPJ/CPF"]:
                cnpj_col = col
                break

        if not cnpj_col:
            raise ValueError(
                f"Coluna CNPJ não encontrada no CSV. "
                f"Colunas disponíveis: {reader.fieldnames}"
            )

        print(f"✓ Usando coluna: '{cnpj_col}'")

        for row in reader:
            cnpj_raw = row.get(cnpj_col, "")
            cnpj_limpo = limpar_cnpj(cnpj_raw)
            if cnpj_limpo and len(cnpj_limpo) == 14:
                cnpjs.add(cnpj_limpo)
            elif cnpj_limpo:
                print(f"⚠ CNPJ inválido ignorado: {cnpj_raw} (limpou para: {cnpj_limpo})")

    return cnpjs


def update_encerramento_fiscal(cnpjs: set[str], *, dry_run: bool = False) -> dict:
    """
    Atualiza encerramento_fiscal=True para empresas cujo CNPJ está no set.

    Retorna um dict com estatísticas:
    - total_csv: total de CNPJs no CSV
    - encontradas: empresas encontradas no banco
    - atualizadas: inventários atualizados
    - nao_encontradas: CNPJs que não existem no banco
    """
    with app.app_context():
        stats = {
            "total_csv": len(cnpjs),
            "encontradas": 0,
            "atualizadas": 0,
            "ja_marcadas": 0,
            "nao_encontradas": [],
        }

        for cnpj in cnpjs:
            # Buscar empresa pelo CNPJ
            empresa = Empresa.query.filter(
                Empresa.cnpj.like(f"%{cnpj}%")
            ).first()

            if not empresa:
                stats["nao_encontradas"].append(cnpj)
                continue

            stats["encontradas"] += 1

            # Buscar ou criar inventário
            inventario = Inventario.query.filter_by(empresa_id=empresa.id).first()

            if not inventario:
                if not dry_run:
                    inventario = Inventario(
                        empresa_id=empresa.id,
                        encerramento_fiscal=True,
                    )
                    db.session.add(inventario)
                stats["atualizadas"] += 1
            elif not inventario.encerramento_fiscal:
                if not dry_run:
                    inventario.encerramento_fiscal = True
                stats["atualizadas"] += 1
            else:
                # Já estava marcada como True
                stats["ja_marcadas"] += 1

        if not dry_run:
            db.session.commit()

        return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Atualiza encerramento_fiscal=True para empresas listadas em CSV."
    )
    parser.add_argument(
        "csv_path",
        nargs="?",
        default=None,
        help="Caminho para o arquivo CSV com CNPJs. Padrão: scripts/fechamentofiscal.csv",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nao grava; apenas mostra o que seria atualizado.",
    )
    args = parser.parse_args(argv)

    # Se não especificado, usa scripts/fechamentofiscal.csv
    if args.csv_path is None:
        csv_path = Path(__file__).parent / "fechamentofiscal.csv"
    else:
        csv_path = Path(args.csv_path)

    if not csv_path.exists():
        print(f"❌ Arquivo não encontrado: {csv_path}")
        if args.csv_path is None:
            print("💡 Dica: Coloque o arquivo 'fechamentofiscal.csv' na pasta 'scripts'")
            print("   Ou especifique o caminho: python scripts/update_encerramento_from_csv.py C:\\caminho\\arquivo.csv")
        return 1

    print(f"📄 Lendo CSV: {csv_path}")
    try:
        cnpjs = ler_cnpjs_do_csv(str(csv_path))
    except Exception as e:
        print(f"❌ Erro ao ler CSV: {e}")
        return 1

    print(f"✓ {len(cnpjs)} CNPJs válidos encontrados no CSV\n")

    if args.dry_run:
        print("🔍 Modo DRY-RUN: nenhuma alteração será gravada\n")

    print("🔄 Processando empresas...")
    stats = update_encerramento_fiscal(cnpjs, dry_run=args.dry_run)

    print("\n" + "=" * 60)
    print("📊 RESUMO")
    print("=" * 60)
    print(f"CNPJs no CSV: {stats['total_csv']}")
    print(f"Empresas encontradas no banco: {stats['encontradas']}")
    print(f"Inventários atualizados: {stats['atualizadas']}")
    print(f"Já estavam marcadas: {stats['ja_marcadas']}")
    print(f"CNPJs não encontrados: {len(stats['nao_encontradas'])}")

    if stats["nao_encontradas"]:
        print("\n⚠ CNPJs que não existem no banco:")
        for cnpj in stats["nao_encontradas"][:10]:  # Mostrar no máximo 10
            print(f"  - {cnpj}")
        if len(stats["nao_encontradas"]) > 10:
            print(f"  ... e mais {len(stats['nao_encontradas']) - 10}")

    if args.dry_run:
        print("\n✓ DRY-RUN concluído (nenhuma alteração gravada)")
    else:
        print("\n✓ Atualização concluída!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
