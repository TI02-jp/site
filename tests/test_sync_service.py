import os
from datetime import date
from unittest.mock import patch

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['ACESSORIAS_API_TOKEN'] = 'tok'

from app import app, db
from app.models.tables import Empresa, CompanyObligation
from services import acessorias_sync


def setup_module(module):
    with app.app_context():
        db.drop_all()
        db.create_all()


def test_sync_company_updates_and_idempotent():
    sample_payload = {
        'Razao': 'Test LTDA',
        'Id': 10,
        'Obrigacoes': [
            {'Nome': 'DCTF', 'Status': 'ok', 'Entregues': 1, 'Atrasadas': 0, 'Proximos30d': 0, 'Futuras30p': 0}
        ]
    }
    with app.app_context():
        empresa = Empresa(nome_empresa='Local', cnpj='789', data_abertura=date(2024, 1, 1), codigo_empresa='1', regime_lancamento='CAIXA')
        db.session.add(empresa)
        db.session.commit()

        with patch('services.acessorias_sync.fetch_company_from_acessorias', return_value=sample_payload):
            acessorias_sync.sync_company_by_identifier('789')
            acessorias_sync.sync_company_by_identifier('789')

        empresa = Empresa.query.first()
        assert empresa.acessorias_company_id == 10
        assert empresa.acessorias_identifier == '789'
        ob = CompanyObligation.query.filter_by(company_id=empresa.id).all()
        assert len(ob) == 1
        assert ob[0].nome == 'DCTF'
