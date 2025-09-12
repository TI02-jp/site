from app import app, db
from app.models.tables import Setor
from app.models.mural import Mural


def seed_murais():
    base = [
        ("fiscal", "Fiscal"),
        ("contabil", "Contábil"),
        ("pessoal", "Pessoal"),
        ("simples-nacional", "Simples Nacional"),
    ]
    with app.app_context():
        for slug, nome in base:
            setor = Setor.query.filter_by(slug=slug).first()
            if not setor:
                setor = Setor(slug=slug, nome=nome, mural_habilitado=True)
                db.session.add(setor)
                db.session.flush()
            if not Mural.query.filter_by(slug=slug).first():
                db.session.add(Mural(slug=slug, nome=nome, setor_id=setor.id, habilitado=True))
        db.session.commit()


if __name__ == "__main__":
    seed_murais()
