"""Seed helpers for the Mural feature."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - help type checkers without runtime import
    from flask_sqlalchemy import SQLAlchemy


def seed_murais(db: "SQLAlchemy") -> None:
    """Populate default boards and sectors if they do not exist."""

    from app.models.tables import Setor
    from app.models.mural import Mural

    base = [
        ("fiscal", "Fiscal"),
        ("contabil", "Contábil"),
        ("pessoal", "Pessoal"),
        ("simples-nacional", "Simples Nacional"),
    ]

    for slug, nome in base:
        setor = Setor.query.filter_by(slug=slug).first()
        if not setor:
            setor = Setor(slug=slug, nome=nome, mural_habilitado=True)
            db.session.add(setor)
            db.session.flush()
        if not Mural.query.filter_by(slug=slug).first():
            db.session.add(
                Mural(slug=slug, nome=nome, setor_id=setor.id, habilitado=True)
            )
    db.session.commit()


if __name__ == "__main__":  # pragma: no cover - manual invocation only
    from app import app, db

    with app.app_context():
        seed_murais(db)
