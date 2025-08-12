import os
from datetime import date
from unittest.mock import patch

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['ACESSORIAS_API_TOKEN'] = 'tok'

from app import app, db
from app.models.tables import Empresa, CompanyObligation


def setup_module(module):
    with app.app_context():
        app.config['WTF_CSRF_ENABLED'] = False
        db.drop_all()
        db.create_all()


def test_link_and_unlink_routes():
    with app.app_context():
        empresa = Empresa(nome_empresa='Local', cnpj='123', data_abertura=date(2024, 1, 1), codigo_empresa='1', regime_lancamento='CAIXA')
        db.session.add(empresa)
        db.session.commit()
        company_id = empresa.id

    with patch('app.controllers.acessorias.sync_company_by_identifier') as sync_mock:
        client = app.test_client()
        resp = client.post(f'/companies/{company_id}/link-acessorias', json={'identifier': '123'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['identifier'] == '123'
        sync_mock.assert_called_once()

    with app.app_context():
        empresa = Empresa.query.get(company_id)
        assert empresa.acessorias_identifier == '123'

    client = app.test_client()
    resp = client.delete(f'/companies/{company_id}/link-acessorias')
    assert resp.status_code == 204
    with app.app_context():
        empresa = Empresa.query.get(company_id)
        assert empresa.acessorias_identifier is None


def test_view_route():
    with app.app_context():
        empresa = Empresa(nome_empresa='Local2', cnpj='456', data_abertura=date(2024, 1, 1), codigo_empresa='1', regime_lancamento='CAIXA')
        empresa.link_acessorias('456')
        db.session.add(empresa)
        db.session.commit()
        ob = CompanyObligation(company_id=empresa.id, nome='DCTF')
        db.session.add(ob)
        db.session.commit()
        company_id = empresa.id

    client = app.test_client()
    resp = client.get(f'/companies/{company_id}/acessorias')
    assert resp.status_code == 200
    assert b'DCTF' in resp.data
