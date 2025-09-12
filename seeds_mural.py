from app import app, db
from app.models.tables import Setor


def seed_setores():
    base = [
        ("fiscal", "Fiscal"),
        ("contabil", "Contábil"),
        ("pessoal", "Pessoal"),
        ("simples-nacional", "Simples Nacional"),
    ]
    with app.app_context():
        for slug, nome in base:
            if not Setor.query.filter_by(slug=slug).first():
                db.session.add(Setor(slug=slug, nome=nome, mural_habilitado=True))
        db.session.commit()


if __name__ == "__main__":
    seed_setores()
