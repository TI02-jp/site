"""
Zera a coluna `encerramento_fiscal` do inventario para testar sincronizacao.

Uso:
    python scripts/reset_encerramento_fiscal.py --yes

Opcoes:
    --yes       Executa sem pedir confirmacao (cuidado!)
    --dry-run   Nao grava; apenas mostra quantos registros seriam atualizados.
"""

import argparse
import sys

from dotenv import load_dotenv

# Carrega variaveis do .env
load_dotenv()

from app import app, db  # noqa: E402
from app.models.tables import Inventario  # noqa: E402


def reset_encerramento_fiscal(*, dry_run: bool = False) -> int:
    """
    Define encerramento_fiscal=False para todos os inventarios.

    Retorna a quantidade de linhas afetadas (ou que seriam afetadas no dry-run).
    """
    with app.app_context():
        query = Inventario.query.filter(
            Inventario.encerramento_fiscal.isnot(False)
        )
        to_update = query.count()
        if dry_run or to_update == 0:
            return to_update

        updated = query.update(
            {Inventario.encerramento_fiscal: False},
            synchronize_session=False,
        )
        db.session.commit()
        return updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Zera a coluna encerramento_fiscal para todos os inventarios."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Executa sem confirmar (use com cuidado).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nao grava; apenas mostra o total a atualizar.",
    )
    args = parser.parse_args(argv)

    if not args.yes and not args.dry_run:
        confirm = input(
            "Isto vai marcar encerramento_fiscal=False para TODOS os inventarios. "
            "Digite 'SIM' para continuar: "
        ).strip()
        if confirm.upper() != "SIM":
            print("Cancelado.")
            return 1

    would_update = reset_encerramento_fiscal(dry_run=True)
    print(f"Inventarios a atualizar: {would_update}")

    if args.dry_run or would_update == 0:
        print("Nenhuma alteracao gravada (dry-run ou nada a fazer).")
        return 0

    updated = reset_encerramento_fiscal(dry_run=False)
    print(f"Inventarios atualizados: {updated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
