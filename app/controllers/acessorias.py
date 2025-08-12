from flask import Blueprint, jsonify, request, render_template, abort

from flask import Blueprint, jsonify, request, render_template, abort

from app import db
from app.models.tables import Empresa, CompanyObligation
from services.acessorias_sync import fetch_company_from_acessorias, sync_company_by_identifier

bp_acessorias = Blueprint('acessorias', __name__)


@bp_acessorias.get('/integrations/acessorias/companies/<identifier>')
def api_company(identifier):
    data = fetch_company_from_acessorias(identifier)
    if data is None:
        abort(404)
    return jsonify(data)


@bp_acessorias.post('/companies/<int:company_id>/link-acessorias')
def link(company_id):
    empresa = Empresa.query.get_or_404(company_id)
    identifier = (request.json or {}).get('identifier')
    if not identifier:
        abort(400, 'identifier required')
    empresa.link_acessorias(identifier)
    db.session.add(empresa)
    db.session.commit()
    sync_company_by_identifier(identifier)
    return jsonify({'company_id': empresa.id, 'identifier': empresa.acessorias_identifier})


@bp_acessorias.delete('/companies/<int:company_id>/link-acessorias')
def unlink(company_id):
    empresa = Empresa.query.get_or_404(company_id)
    empresa.unlink_acessorias()
    db.session.commit()
    return ('', 204)


@bp_acessorias.get('/companies/<int:company_id>/acessorias')
def view(company_id):
    empresa = Empresa.query.get_or_404(company_id)
    obligations = CompanyObligation.query.filter_by(company_id=empresa.id).all()
    return render_template('acessorias/company.html', company=empresa, obligations=obligations)
