"""
Blueprint para gestao de empresas.

Rotas:
    - GET/POST /cadastrar_empresa
    - GET /listar_empresas
    - GET/POST /empresa/<id>/editar
    - GET /empresa/<id> (visualizar + embed)
    - GET/POST /empresa/<id>/departamentos
    - CRUD de reunioes de cliente
    - APIs auxiliares (/api/cnpj, /api/reunioes, /api/calendario-eventos)
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Iterable

import sqlalchemy as sa
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload

from app import csrf, db, limiter
from app.controllers.routes import decode_id, encode_id, user_has_tag
from app.controllers.routes._decorators import meeting_only_access_check
from app.forms import (
    ClienteReuniaoForm,
    DepartamentoAdministrativoForm,
    DepartamentoContabilForm,
    DepartamentoFinanceiroForm,
    DepartamentoFiscalForm,
    DepartamentoPessoalForm,
    EmpresaForm,
)
from app.models.tables import ClienteReuniao, Departamento, Empresa, Inventario, Setor, User
from app.services.calendar_cache import calendar_cache
from app.services.cnpj import consultar_cnpj
from app.services.general_calendar import serialize_events_for_calendar
from app.services.google_calendar import get_calendar_timezone
from app.services.meeting_room import (
    combine_events,
    fetch_raw_events,
    try_get_cached_combined_events,
)
from app.utils.performance_middleware import track_commit_end, track_commit_start, track_custom_span
from app.utils.permissions import is_user_admin
from app.utils.security import sanitize_html

empresas_bp = Blueprint("empresas", __name__)


# =============================================================================
# APIs auxiliares
# =============================================================================

@empresas_bp.route("/api/cnpj/<cnpj>")
@login_required
def api_cnpj(cnpj):
    """Provide a JSON API for CNPJ lookups."""
    try:
        dados = consultar_cnpj(cnpj)
    except ValueError as e:
        msg = str(e)
        status = 400 if "inválido" in msg.lower() or "invalido" in msg.lower() else 404
        if status == 404:
            msg = "CNPJ não está cadastrado"
        return jsonify({"error": msg}), status
    except Exception:
        return jsonify({"error": "Erro ao consultar CNPJ"}), 500
    if not dados:
        return jsonify({"error": "CNPJ não está cadastrado"}), 404
    return jsonify(dados)


@empresas_bp.route("/api/reunioes")
@login_required
@csrf.exempt
@limiter.limit("30 per minute")  # Limite de 30 req/min por IP (1 a cada 2s)
def api_reunioes():
    """Return meetings with up-to-date status as JSON."""
    is_admin = is_user_admin(current_user)

    cached_events, _, _ = try_get_cached_combined_events(current_user.id, is_admin)
    if cached_events is not None:
        response = jsonify(cached_events)
        response.headers["X-Calendar-Cache"] = "hit"
        return response

    raw_events = []
    fallback = None
    try:
        raw_events = fetch_raw_events()
    except Exception:
        current_app.logger.exception("Google Calendar fetch failed; using cached data fallback")
        raw_events = calendar_cache.get("raw_calendar_events")
        if raw_events is not None:
            fallback = "primary-cache"
        else:
            raw_events = calendar_cache.get("raw_calendar_events_stale")
            if raw_events is not None:
                fallback = "stale-cache"
            else:
                raw_events = []
    calendar_tz = get_calendar_timezone()
    now = datetime.now(calendar_tz)
    events = combine_events(raw_events, now, current_user.id, is_admin)
    response = jsonify(events)
    response.headers["X-Calendar-Cache"] = "miss"
    if fallback:
        response.headers["X-Calendar-Fallback"] = fallback
    return response


@empresas_bp.route("/api/calendario-eventos")
@login_required
def api_general_calendar_events():
    """Return collaborator calendar events as JSON."""

    can_manage = is_user_admin(current_user) or user_has_tag("Gestão") or user_has_tag("Coord.")
    events = serialize_events_for_calendar(current_user.id, can_manage, is_user_admin(current_user))
    return jsonify(events)


# =============================================================================
# Rotas de empresas
# =============================================================================

@empresas_bp.route("/cadastrar_empresa", methods=["GET", "POST"])
@login_required
def cadastrar_empresa():
    """Create a new company record."""
    form = EmpresaForm()
    if request.method == "GET":
        form.sistemas_consultorias.data = form.sistemas_consultorias.data or []
        form.regime_lancamento.data = form.regime_lancamento.data or []
    if form.validate_on_submit():
        try:
            cnpj_limpo = re.sub(r"\D", "", form.cnpj.data)
            acessos_json = form.acessos_json.data or "[]"
            try:
                acessos = json.loads(acessos_json) if acessos_json else []
            except Exception:
                acessos = []
            nova_empresa = Empresa(
                codigo_empresa=form.codigo_empresa.data,
                nome_empresa=form.nome_empresa.data,
                cnpj=cnpj_limpo,
                data_abertura=form.data_abertura.data,
                socio_administrador=form.socio_administrador.data,
                tributacao=form.tributacao.data,
                regime_lancamento=form.regime_lancamento.data,
                atividade_principal=form.atividade_principal.data,
                sistemas_consultorias=form.sistemas_consultorias.data,
                sistema_utilizado=form.sistema_utilizado.data,
                acessos=acessos,
            )
            db.session.add(nova_empresa)
            db.session.commit()
            flash("Empresa cadastrada com sucesso!", "success")
            return redirect(url_for("empresas.gerenciar_departamentos", empresa_id=nova_empresa.id))
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao cadastrar empresa: {e}", "danger")
    else:
        current_app.logger.debug("Formulário não validado: %s", form.errors)

    return render_template("empresas/cadastrar.html", form=form)


@empresas_bp.route("/listar_empresas")
@login_required
@meeting_only_access_check
def listar_empresas():
    """List companies with optional search and pagination."""
    saved_filters = session.get("listar_empresas_filters", {})

    search_arg = request.args.get("q")
    if search_arg is None:
        search = (saved_filters.get("search") or "").strip()
    else:
        search = search_arg.strip()

    page = request.args.get("page", 1, type=int)
    per_page = 20
    show_inactive = request.args.get("show_inactive") in ("1", "on", "true", "True")
    allowed_tributacoes = ["Simples Nacional", "Lucro Presumido", "Lucro Real"]
    sort_arg = request.args.get("sort")
    order_arg = request.args.get("order")
    clear_tributacao = request.args.get("clear_tributacao") == "1"
    raw_tributacoes = request.args.getlist("tributacao")
    if clear_tributacao:
        tributacao_filters: list[str] = []
    elif raw_tributacoes:
        tributacao_filters = [t for t in raw_tributacoes if t in allowed_tributacoes]
    else:
        tributacao_filters = saved_filters.get("tributacao_filters", [])

    sort = sort_arg or saved_filters.get("sort") or "nome"
    if sort not in ("nome", "codigo"):
        sort = "nome"

    order = order_arg or saved_filters.get("order") or "asc"
    if order not in ("asc", "desc"):
        order = "asc"

    session["listar_empresas_filters"] = {"sort": sort, "order": order, "search": search}

    query = Empresa.query

    if show_inactive:
        query = query.filter_by(ativo=False)
    else:
        query = query.filter_by(ativo=True)

    if tributacao_filters:
        query = query.filter(Empresa.tributacao.in_(tributacao_filters))

    if search:
        like_pattern = f"%{search}%"
        query = query.filter(
            sa.or_(Empresa.nome_empresa.ilike(like_pattern), Empresa.codigo_empresa.ilike(like_pattern))
        )

    order_column = Empresa.codigo_empresa if sort == "codigo" else Empresa.nome_empresa
    if order == "desc":
        query = query.order_by(Empresa.ativo.desc(), order_column.desc())
    else:
        query = query.order_by(Empresa.ativo.desc(), order_column.asc())

    session["listar_empresas_filters"] = {
        "sort": sort,
        "order": order,
        "tributacao_filters": tributacao_filters,
        "search": search,
    }

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    empresas = pagination.items

    return render_template(
        "empresas/listar.html",
        empresas=empresas,
        pagination=pagination,
        search=search,
        sort=sort,
        order=order,
        show_inactive=show_inactive,
        tributacao_filters=tributacao_filters,
        allowed_tributacoes=allowed_tributacoes,
    )


@empresas_bp.route("/empresa/editar/<empresa_id>", methods=["GET", "POST"])
@empresas_bp.route("/empresa/editar/<int:id>", methods=["GET", "POST"])
@login_required
def editar_empresa(empresa_id: str | None = None, id: int | None = None):
    """Edit an existing company and its details."""
    raw_empresa = empresa_id if empresa_id is not None else id
    empresa_id_int = decode_id(str(raw_empresa), namespace="empresa")
    empresa_token = encode_id(empresa_id_int, namespace="empresa")
    empresa = Empresa.query.get_or_404(empresa_id_int)
    empresa_form = EmpresaForm(request.form, obj=empresa)

    if request.method == "GET":
        empresa_form.sistemas_consultorias.data = empresa.sistemas_consultorias or []
        empresa_form.regime_lancamento.data = empresa.regime_lancamento or []
        empresa_form.acessos_json.data = json.dumps(empresa.acessos or [])
        empresa_form.contatos_json.data = json.dumps(empresa.contatos or [])
        empresa_form.ativo.data = empresa.ativo

    if request.method == "POST":
        if empresa_form.validate():
            empresa_form.populate_obj(empresa)
            empresa.cnpj = re.sub(r"\D", "", empresa_form.cnpj.data)
            empresa.sistemas_consultorias = empresa_form.sistemas_consultorias.data
            try:
                empresa.acessos = json.loads(empresa_form.acessos_json.data or "[]")
            except Exception:
                empresa.acessos = []
            try:
                empresa.contatos = json.loads(empresa_form.contatos_json.data or "[]")
            except Exception:
                empresa.contatos = []
            db.session.add(empresa)
            try:
                db.session.commit()
                flash("Dados do Cliente salvos com sucesso!", "success")
                return redirect(url_for("empresas.visualizar_empresa", empresa_id=empresa_token) + "#dados-cliente")
            except Exception as e:
                db.session.rollback()
                flash(f"Erro ao salvar: {str(e)}", "danger")
        else:
            for field, errors in empresa_form.errors.items():
                for error in errors:
                    flash(f"Erro: {error}", "danger")

    return render_template(
        "empresas/editar_empresa.html",
        empresa=empresa,
        empresa_form=empresa_form,
    )


@empresas_bp.route("/empresa/visualizar/<empresa_id>")
@empresas_bp.route("/empresa/visualizar/<int:id>")
@empresas_bp.route("/empresa/visualizar_embed/<empresa_id>")
@empresas_bp.route("/empresa/visualizar_embed/<int:id>")
@login_required
def visualizar_empresa(empresa_id: str | None = None, id: int | None = None):
    """Display a detailed view of a company."""
    from types import SimpleNamespace

    embed_mode = request.args.get("hide_actions") == "1"
    raw_empresa = empresa_id if empresa_id is not None else id
    resolved_empresa_id = decode_id(str(raw_empresa), namespace="empresa")
    empresa_token = encode_id(resolved_empresa_id, namespace="empresa")
    empresa = Empresa.query.get_or_404(resolved_empresa_id)

    if request.endpoint == "empresas.visualizar_empresa_embed" and not embed_mode:
        return redirect(url_for("empresas.visualizar_empresa", empresa_id=empresa_token))

    empresa.regime_lancamento_display = empresa.regime_lancamento or []

    can_access_financeiro = user_has_tag("financeiro")

    dept_tipos = [
        "Departamento Fiscal",
        "Departamento Contábil",
        "Departamento Pessoal",
        "Departamento Administrativo",
        "Departamento Notas Fiscais",
    ]
    if can_access_financeiro:
        dept_tipos.append("Departamento Financeiro")

    departamentos = Departamento.query.filter(
        Departamento.empresa_id == resolved_empresa_id, Departamento.tipo.in_(dept_tipos)
    ).all()

    dept_map = {dept.tipo: dept for dept in departamentos}
    fiscal = dept_map.get("Departamento Fiscal")
    contabil = dept_map.get("Departamento Contábil")
    pessoal = dept_map.get("Departamento Pessoal")
    administrativo = dept_map.get("Departamento Administrativo")
    financeiro = dept_map.get("Departamento Financeiro") if can_access_financeiro else None
    notas_fiscais = dept_map.get("Departamento Notas Fiscais")

    def _prepare_envio_fisico(departamento):
        if not departamento:
            return []
        try:
            lista = (
                json.loads(departamento.envio_fisico)
                if isinstance(departamento.envio_fisico, str)
                else (departamento.envio_fisico or [])
            )
        except Exception:
            lista = []
        if "malote" in lista and getattr(departamento, "malote_coleta", None):
            lista = ["Malote - " + departamento.malote_coleta if item == "malote" else item for item in lista]
        return lista

    if getattr(empresa, "contatos", None):
        try:
            contatos_list = json.loads(empresa.contatos) if isinstance(empresa.contatos, str) else empresa.contatos
        except Exception:
            contatos_list = []
    else:
        contatos_list = []
    contatos_list = normalize_contatos(contatos_list)
    empresa.contatos_list = contatos_list

    cliente_reunioes = (
        ClienteReuniao.query.options(joinedload(ClienteReuniao.setor))
        .filter_by(empresa_id=resolved_empresa_id)
        .order_by(ClienteReuniao.data.desc(), ClienteReuniao.created_at.desc())
        .all()
    )
    participante_ids: set[int] = set()
    for reuniao in cliente_reunioes:
        for participante in reuniao.participantes or []:
            if isinstance(participante, int):
                participante_ids.add(participante)
    reunioes_participantes_map: dict[int, User] = {}
    if participante_ids:
        usuarios = User.query.filter(User.id.in_(participante_ids)).all()
        reunioes_participantes_map = {usuario.id: usuario for usuario in usuarios}

    if fiscal is None:
        fiscal_view = SimpleNamespace(formas_importacao=[], envio_fisico=[])
    else:
        fiscal_view = fiscal
        formas = getattr(fiscal_view, "formas_importacao", None)
        if isinstance(formas, str):
            try:
                fiscal_view.formas_importacao = json.loads(formas)
            except Exception:
                fiscal_view.formas_importacao = []
        elif not formas:
            fiscal_view.formas_importacao = []
        setattr(fiscal_view, "envio_fisico", _prepare_envio_fisico(fiscal_view))

    if contabil:
        contabil.envio_fisico = _prepare_envio_fisico(contabil)
    if pessoal:
        pessoal.envio_fisico = _prepare_envio_fisico(pessoal)
    if administrativo:
        administrativo.envio_fisico = _prepare_envio_fisico(administrativo)
    if financeiro:
        financeiro.envio_fisico = _prepare_envio_fisico(financeiro)

    usuarios_responsaveis = (
        User.query.filter(User.ativo.is_(True)).order_by(User.name.asc(), User.username.asc()).all()
    )
    responsaveis_map = {
        str(usuario.id): (usuario.name or usuario.username or f"Usuário {usuario.id}") for usuario in usuarios_responsaveis
    }

    return render_template(
        "empresas/visualizar.html",
        empresa=empresa,
        fiscal=fiscal_view,
        contabil=contabil,
        pessoal=pessoal,
        administrativo=administrativo,
        financeiro=financeiro,
        notas_fiscais=notas_fiscais,
        reunioes_cliente=cliente_reunioes,
        reunioes_participantes_map=reunioes_participantes_map,
        can_access_financeiro=can_access_financeiro,
        responsaveis_map=responsaveis_map,
        empresa_token=empresa_token,
        embed_mode=embed_mode,
    )


@empresas_bp.route("/empresa/<empresa_id>/departamentos", methods=["GET", "POST"])
@empresas_bp.route("/empresa/<int:id>/departamentos", methods=["GET", "POST"])
@login_required
def gerenciar_departamentos(empresa_id: str | None = None, id: int | None = None):
    """Create or update department data for a company."""
    raw_empresa = empresa_id if empresa_id is not None else id
    empresa_id_int = decode_id(str(raw_empresa), namespace="empresa")
    empresa_token = encode_id(empresa_id_int, namespace="empresa")
    empresa = Empresa.query.get_or_404(empresa_id_int)

    can_access_financeiro = user_has_tag("financeiro")
    responsavel_value = (request.form.get("responsavel") or "").strip() if request.method == "POST" else None

    dept_tipos = [
        "Departamento Fiscal",
        "Departamento Contábil",
        "Departamento Pessoal",
        "Departamento Administrativo",
        "Departamento Notas Fiscais",
    ]
    if can_access_financeiro:
        dept_tipos.append("Departamento Financeiro")

    departamentos = Departamento.query.filter(
        Departamento.empresa_id == empresa_id_int, Departamento.tipo.in_(dept_tipos)
    ).all()

    dept_map = {dept.tipo: dept for dept in departamentos}
    fiscal = dept_map.get("Departamento Fiscal")
    contabil = dept_map.get("Departamento Contábil")
    pessoal = dept_map.get("Departamento Pessoal")
    administrativo = dept_map.get("Departamento Administrativo")
    financeiro = dept_map.get("Departamento Financeiro") if can_access_financeiro else None
    notas_fiscais = dept_map.get("Departamento Notas Fiscais")

    fiscal_form = DepartamentoFiscalForm(request.form, obj=fiscal)
    contabil_form = DepartamentoContabilForm(request.form, obj=contabil)
    pessoal_form = DepartamentoPessoalForm(request.form, obj=pessoal)
    administrativo_form = DepartamentoAdministrativoForm(request.form, obj=administrativo)
    financeiro_form = DepartamentoFinanceiroForm(request.form, obj=financeiro) if can_access_financeiro else None
    usuarios_responsaveis = [
        {"id": str(usuario.id), "label": usuario.name or usuario.username or f"Usuário {usuario.id}"}
        for usuario in User.query.filter(User.ativo.is_(True)).order_by(User.name.asc(), User.username.asc()).all()
    ]
    usuarios_responsaveis_ids = [usuario["id"] for usuario in usuarios_responsaveis]

    if request.method == "GET":
        fiscal_form = DepartamentoFiscalForm(obj=fiscal)
        if fiscal:
            fiscal_form.envio_digital.data = (
                fiscal.envio_digital
                if isinstance(fiscal.envio_digital, list)
                else json.loads(fiscal.envio_digital) if fiscal.envio_digital else []
            )
            fiscal_form.envio_fisico.data = (
                fiscal.envio_fisico
                if isinstance(fiscal.envio_fisico, list)
                else json.loads(fiscal.envio_fisico) if fiscal.envio_fisico else []
            )

            if fiscal.contatos:
                try:
                    contatos_list = (
                        json.loads(fiscal.contatos) if isinstance(fiscal.contatos, str) else fiscal.contatos
                    )
                except Exception:
                    contatos_list = []
            else:
                contatos_list = []
            contatos_list = normalize_contatos(contatos_list)
            fiscal_form.contatos_json.data = json.dumps(contatos_list)

        contabil_form = DepartamentoContabilForm(obj=contabil)
        if contabil:
            contabil_form.metodo_importacao.data = (
                contabil.metodo_importacao
                if isinstance(contabil.metodo_importacao, list)
                else json.loads(contabil.metodo_importacao) if contabil.metodo_importacao else []
            )
            contabil_form.envio_digital.data = (
                contabil.envio_digital
                if isinstance(contabil.envio_digital, list)
                else json.loads(contabil.envio_digital) if contabil.envio_digital else []
            )
            contabil_form.envio_fisico.data = (
                contabil.envio_fisico
                if isinstance(contabil.envio_fisico, list)
                else json.loads(contabil.envio_fisico) if contabil.envio_fisico else []
            )
            contabil_form.controle_relatorios.data = (
                contabil.controle_relatorios
                if isinstance(contabil.controle_relatorios, list)
                else json.loads(contabil.controle_relatorios) if contabil.controle_relatorios else []
            )

    form_type = request.form.get("form_type")

    if request.method == "POST":
        form_processed_successfully = False

        def _set_responsavel(departamento_obj):
            if departamento_obj is not None:
                departamento_obj.responsavel = responsavel_value or None

        if form_type == "fiscal" and fiscal_form.validate():
            if not fiscal:
                fiscal = Departamento(empresa_id=empresa_id_int, tipo="Departamento Fiscal")
                db.session.add(fiscal)

            fiscal_form.populate_obj(fiscal)
            _set_responsavel(fiscal)
            if "malote" not in (fiscal_form.envio_fisico.data or []):
                fiscal.malote_coleta = None
            else:
                fiscal.malote_coleta = fiscal_form.malote_coleta.data
            try:
                fiscal.contatos = json.loads(fiscal_form.contatos_json.data or "[]")
            except Exception:
                fiscal.contatos = []
            flash("Departamento Fiscal salvo com sucesso!", "success")
            form_processed_successfully = True

        elif form_type == "contabil" and contabil_form.validate():
            if not contabil:
                contabil = Departamento(empresa_id=empresa_id_int, tipo="Departamento Contábil")
                db.session.add(contabil)

            contabil_form.populate_obj(contabil)
            _set_responsavel(contabil)
            if "malote" not in (contabil_form.envio_fisico.data or []):
                contabil.malote_coleta = None
            else:
                contabil.malote_coleta = contabil_form.malote_coleta.data

            contabil.metodo_importacao = contabil_form.metodo_importacao.data or []
            contabil.envio_digital = contabil_form.envio_digital.data or []
            contabil.envio_fisico = contabil_form.envio_fisico.data or []
            contabil.controle_relatorios = contabil_form.controle_relatorios.data or []

            flash("Departamento Contábil salvo com sucesso!", "success")
            form_processed_successfully = True

        elif form_type == "pessoal" and pessoal_form.validate():
            if not pessoal:
                pessoal = Departamento(empresa_id=empresa_id_int, tipo="Departamento Pessoal")
                db.session.add(pessoal)

            pessoal_form.populate_obj(pessoal)
            _set_responsavel(pessoal)
            flash("Departamento Pessoal salvo com sucesso!", "success")
            form_processed_successfully = True

        elif form_type == "administrativo" and administrativo_form.validate():
            if not administrativo:
                administrativo = Departamento(empresa_id=empresa_id_int, tipo="Departamento Administrativo")
                db.session.add(administrativo)

            administrativo_form.populate_obj(administrativo)
            _set_responsavel(administrativo)
            flash("Departamento Administrativo salvo com sucesso!", "success")
            form_processed_successfully = True
        elif form_type == "financeiro":
            if not can_access_financeiro:
                abort(403)
            if financeiro_form and financeiro_form.validate():
                if not financeiro:
                    financeiro = Departamento(empresa_id=empresa_id_int, tipo="Departamento Financeiro")
                    db.session.add(financeiro)

                financeiro_form.populate_obj(financeiro)
                _set_responsavel(financeiro)
                flash("Departamento Financeiro salvo com sucesso!", "success")
                form_processed_successfully = True

        elif form_type == "notas_fiscais":
            if not notas_fiscais:
                notas_fiscais = Departamento(empresa_id=empresa_id_int, tipo="Departamento Notas Fiscais")
                db.session.add(notas_fiscais)

            particularidades_texto = request.form.get("particularidades_texto", "")
            notas_fiscais.particularidades_texto = particularidades_texto
            _set_responsavel(notas_fiscais)
            flash("Departamento Notas Fiscais salvo com sucesso!", "success")
            form_processed_successfully = True

        if form_processed_successfully:
            try:
                db.session.commit()

                hash_ancoras = {
                    "fiscal": "fiscal",
                    "contabil": "contabil",
                    "pessoal": "pessoal",
                    "administrativo": "administrativo",
                    "financeiro": "financeiro",
                    "notas_fiscais": "notas-fiscais",
                }
                hash_ancora = hash_ancoras.get(form_type, "")

                return redirect(url_for("empresas.visualizar_empresa", empresa_id=empresa_token) + f"#{hash_ancora}")

            except Exception as e:
                db.session.rollback()
                flash(f"Ocorreu um erro ao salvar: {str(e)}", "danger")

        else:
            active_form = {
                "fiscal": fiscal_form,
                "contabil": contabil_form,
                "pessoal": pessoal_form,
                "administrativo": administrativo_form,
                "financeiro": financeiro_form,
            }.get(form_type)
            if active_form and active_form.errors:
                for field, errors in active_form.errors.items():
                    for error in errors:
                        flash(f"Erro no formulário {form_type.capitalize()}: {error}", "danger")

    reunioes_cliente = (
        ClienteReuniao.query.options(joinedload(ClienteReuniao.setor))
        .filter_by(empresa_id=empresa_id_int)
        .order_by(ClienteReuniao.data.desc(), ClienteReuniao.created_at.desc())
        .all()
    )
    participante_ids: set[int] = set()
    for reuniao in reunioes_cliente:
        for participante in reuniao.participantes or []:
            if isinstance(participante, int):
                participante_ids.add(participante)
    reunioes_participantes_map: dict[int, User] = {}
    if participante_ids:
        usuarios = User.query.filter(User.id.in_(participante_ids)).all()
        reunioes_participantes_map = {usuario.id: usuario for usuario in usuarios}

    return render_template(
        "empresas/departamentos.html",
        empresa=empresa,
        fiscal_form=fiscal_form,
        contabil_form=contabil_form,
        pessoal_form=pessoal_form,
        administrativo_form=administrativo_form,
        financeiro_form=financeiro_form,
        fiscal=fiscal,
        contabil=contabil,
        pessoal=pessoal,
        administrativo=administrativo,
        financeiro=financeiro,
        notas_fiscais=notas_fiscais,
        can_access_financeiro=can_access_financeiro,
        reunioes_cliente=reunioes_cliente,
        reunioes_participantes_map=reunioes_participantes_map,
        usuarios_responsaveis=usuarios_responsaveis,
        usuarios_responsaveis_ids=usuarios_responsaveis_ids,
    )


def _populate_cliente_reuniao_form(form: ClienteReuniaoForm) -> None:
    """Fill dynamic choices for the client meeting form."""

    usuarios = (
        User.query.filter_by(ativo=True).order_by(User.name.asc(), User.username.asc()).all()
    )
    form.participantes.choices = [
        (usuario.id, (usuario.name or usuario.username or f"Usuário {usuario.id}")) for usuario in usuarios
    ]

    setores = Setor.query.order_by(Setor.nome.asc()).all()
    setor_choices = [(0, "Selecione um setor")]
    setor_choices.extend([(setor.id, setor.nome) for setor in setores])
    form.setor_id.choices = setor_choices
    if form.setor_id.data is None:
        form.setor_id.data = 0


def _parse_cliente_reuniao_topicos(payload: str | None) -> list[str]:
    """Return a sanitized list of meeting topics."""

    if not payload:
        return []
    try:
        data = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return []
    topicos: list[str] = []
    for item in data:
        if isinstance(item, str):
            trimmed = item.strip()
            if trimmed:
                topicos.append(trimmed[:500])
    return topicos


def _resolve_reuniao_participantes(participante_ids: list[int]) -> list[tuple[int, str]]:
    """Return participant tuples preserving the original order."""

    ids = [pid for pid in participante_ids if isinstance(pid, int)]
    if not ids:
        return []
    usuarios = User.query.filter(User.id.in_(ids)).all()
    lookup = {usuario.id: (usuario.name or usuario.username or f"Usuário {usuario.id}") for usuario in usuarios}
    return [(pid, lookup.get(pid, f"Usuário {pid}")) for pid in ids]


@empresas_bp.route("/empresa/<empresa_id>/reunioes-cliente/nova", methods=["GET", "POST"])
@empresas_bp.route("/empresa/<int:id>/reunioes-cliente/nova", methods=["GET", "POST"])
@login_required
def nova_reuniao_cliente(empresa_id: str | None = None, id: int | None = None):
    """Render and process the creation form for client meetings."""

    raw_empresa = empresa_id if empresa_id is not None else id
    empresa_id_int = decode_id(str(raw_empresa), namespace="empresa")
    empresa_token = encode_id(empresa_id_int, namespace="empresa")
    empresa = Empresa.query.get_or_404(empresa_id_int)
    form = ClienteReuniaoForm()
    _populate_cliente_reuniao_form(form)

    if form.validate_on_submit():
        topicos = _parse_cliente_reuniao_topicos(form.topicos_json.data)
        reuniao = ClienteReuniao(
            empresa_id=empresa.id,
            data=form.data.data,
            setor_id=form.setor_id.data or None,
            participantes=form.participantes.data or [],
            topicos=topicos,
            decisoes=sanitize_html(form.decisoes.data or ""),
            acompanhar_ate=form.acompanhar_ate.data,
            created_by=current_user.id,
            updated_by=current_user.id,
        )
        db.session.add(reuniao)
        try:
            db.session.commit()
            flash("Reunião registrada com sucesso!", "success")
            return redirect(url_for("empresas.visualizar_empresa", empresa_id=empresa_token) + "#reunioes-cliente")
        except SQLAlchemyError as exc:
            current_app.logger.exception("Erro ao salvar reunião com cliente: %s", exc)
            db.session.rollback()
            flash("Não foi possível salvar a reunião. Tente novamente.", "danger")

    if not form.topicos_json.data:
        form.topicos_json.data = "[]"

    return render_template(
        "empresas/reuniao_cliente_form.html",
        empresa=empresa,
        form=form,
        is_edit=False,
        page_title="Adicionar reunião com cliente",
    )


@empresas_bp.route("/empresa/<empresa_id>/reunioes-cliente/<reuniao_id>/editar", methods=["GET", "POST"])
@empresas_bp.route("/empresa/<int:id>/reunioes-cliente/<reuniao_id>/editar", methods=["GET", "POST"])
@empresas_bp.route("/empresa/<empresa_id>/reunioes-cliente/<int:rid>/editar", methods=["GET", "POST"])
@empresas_bp.route("/empresa/<int:id>/reunioes-cliente/<int:rid>/editar", methods=["GET", "POST"])
@login_required
def editar_reuniao_cliente(empresa_id: str | None = None, reuniao_id: str | None = None, id: int | None = None, rid: int | None = None):
    """Allow updating an existing client meeting."""

    raw_empresa = empresa_id if empresa_id is not None else id
    raw_reuniao = reuniao_id if reuniao_id is not None else rid
    empresa_id_int = decode_id(str(raw_empresa), namespace="empresa")
    reuniao_id_int = decode_id(str(raw_reuniao), namespace="empresa-reuniao")
    empresa_token = encode_id(empresa_id_int, namespace="empresa")
    reuniao = (
        ClienteReuniao.query.filter_by(id=reuniao_id_int, empresa_id=empresa_id_int)
        .options(joinedload(ClienteReuniao.setor))
        .first_or_404()
    )
    form = ClienteReuniaoForm(obj=reuniao)
    _populate_cliente_reuniao_form(form)

    if request.method == "GET":
        form.participantes.data = reuniao.participantes or []
        form.setor_id.data = reuniao.setor_id or 0
        form.topicos_json.data = json.dumps(reuniao.topicos or [])
        form.decisoes.data = reuniao.decisoes or ""
    if form.validate_on_submit():
        topicos = _parse_cliente_reuniao_topicos(form.topicos_json.data)
        reuniao.data = form.data.data
        reuniao.setor_id = form.setor_id.data or None
        reuniao.participantes = form.participantes.data or []
        reuniao.topicos = topicos
        reuniao.decisoes = sanitize_html(form.decisoes.data or "")
        reuniao.acompanhar_ate = form.acompanhar_ate.data
        reuniao.updated_by = current_user.id
        try:
            db.session.commit()
            flash("Reunião atualizada com sucesso!", "success")
            return redirect(url_for("empresas.visualizar_empresa", empresa_id=empresa_token) + "#reunioes-cliente")
        except SQLAlchemyError as exc:
            current_app.logger.exception("Erro ao atualizar reunião com cliente: %s", exc)
            db.session.rollback()
            flash("Não foi possível atualizar a reunião. Tente novamente.", "danger")

    if not form.topicos_json.data:
        form.topicos_json.data = "[]"

    return render_template(
        "empresas/reuniao_cliente_form.html",
        empresa=reuniao.empresa,
        form=form,
        is_edit=True,
        reuniao=reuniao,
        page_title="Editar reunião com cliente",
    )


@empresas_bp.route("/empresa/<empresa_id>/reunioes-cliente/<reuniao_id>")
@empresas_bp.route("/empresa/<int:id>/reunioes-cliente/<reuniao_id>")
@empresas_bp.route("/empresa/<empresa_id>/reunioes-cliente/<int:rid>")
@empresas_bp.route("/empresa/<int:id>/reunioes-cliente/<int:rid>")
@login_required
def visualizar_reuniao_cliente(empresa_id: str | None = None, reuniao_id: str | None = None, id: int | None = None, rid: int | None = None):
    """Display a single client meeting with all recorded details."""

    raw_empresa = empresa_id if empresa_id is not None else id
    raw_reuniao = reuniao_id if reuniao_id is not None else rid
    empresa_id_int = decode_id(str(raw_empresa), namespace="empresa")
    reuniao_id_int = decode_id(str(raw_reuniao), namespace="empresa-reuniao")
    reuniao = (
        ClienteReuniao.query.filter_by(id=reuniao_id_int, empresa_id=empresa_id_int)
        .options(joinedload(ClienteReuniao.setor))
        .first_or_404()
    )
    participantes = _resolve_reuniao_participantes(reuniao.participantes or [])
    return render_template(
        "empresas/reuniao_cliente_visualizar.html",
        reuniao=reuniao,
        empresa=reuniao.empresa,
        participantes=participantes,
        topicos=reuniao.topicos or [],
    )


@empresas_bp.route("/empresa/<empresa_id>/reunioes-cliente/<reuniao_id>/detalhes")
@empresas_bp.route("/empresa/<int:id>/reunioes-cliente/<reuniao_id>/detalhes")
@empresas_bp.route("/empresa/<empresa_id>/reunioes-cliente/<int:rid>/detalhes")
@empresas_bp.route("/empresa/<int:id>/reunioes-cliente/<int:rid>/detalhes")
@login_required
def reuniao_cliente_detalhes_modal(empresa_id: str | None = None, reuniao_id: str | None = None, id: int | None = None, rid: int | None = None):
    """Return rendered HTML snippet for modal visualization."""

    raw_empresa = empresa_id if empresa_id is not None else id
    raw_reuniao = reuniao_id if reuniao_id is not None else rid
    empresa_id_int = decode_id(str(raw_empresa), namespace="empresa")
    reuniao_id_int = decode_id(str(raw_reuniao), namespace="empresa-reuniao")
    reuniao = (
        ClienteReuniao.query.filter_by(id=reuniao_id_int, empresa_id=empresa_id_int)
        .options(joinedload(ClienteReuniao.setor))
        .first_or_404()
    )
    participantes = _resolve_reuniao_participantes(reuniao.participantes or [])
    html = render_template(
        "empresas/partials/reuniao_cliente_detalhes_content.html",
        reuniao=reuniao,
        empresa=reuniao.empresa,
        participantes=participantes,
        topicos=reuniao.topicos or [],
    )
    return jsonify(
        {
            "title": f"Reunião com {reuniao.empresa.nome_empresa}",
            "html": html,
        }
    )


@empresas_bp.route("/empresa/<empresa_id>/reunioes-cliente/<reuniao_id>/excluir", methods=["POST"])
@empresas_bp.route("/empresa/<int:id>/reunioes-cliente/<reuniao_id>/excluir", methods=["POST"])
@empresas_bp.route("/empresa/<empresa_id>/reunioes-cliente/<int:rid>/excluir", methods=["POST"])
@empresas_bp.route("/empresa/<int:id>/reunioes-cliente/<int:rid>/excluir", methods=["POST"])
@login_required
def excluir_reuniao_cliente(empresa_id: str | None = None, reuniao_id: str | None = None, id: int | None = None, rid: int | None = None):
    """Delete a client meeting from the log."""

    raw_empresa = empresa_id if empresa_id is not None else id
    raw_reuniao = reuniao_id if reuniao_id is not None else rid
    empresa_id_int = decode_id(str(raw_empresa), namespace="empresa")
    reuniao_id_int = decode_id(str(raw_reuniao), namespace="empresa-reuniao")
    empresa_token = encode_id(empresa_id_int, namespace="empresa")
    reuniao = ClienteReuniao.query.filter_by(id=reuniao_id_int, empresa_id=empresa_id_int).first_or_404()
    db.session.delete(reuniao)
    try:
        db.session.commit()
        flash("Reunião excluída com sucesso.", "success")
    except SQLAlchemyError as exc:
        current_app.logger.exception("Erro ao excluir reunião com cliente: %s", exc)
        db.session.rollback()
        flash("Não foi possível excluir a reunião. Tente novamente.", "danger")
    return redirect(url_for("empresas.visualizar_empresa", empresa_id=empresa_token) + "#reunioes-cliente")


def normalize_contatos(contatos: Iterable[dict] | None) -> list[dict]:
    """Normalize contatos payload to avoid template errors."""
    if not contatos:
        return []
    normalized = []
    for contato in contatos:
        if not isinstance(contato, dict):
            continue
        normalized.append(
            {
                "nome": contato.get("nome", ""),
                "telefone": contato.get("telefone", ""),
                "email": contato.get("email", ""),
                "cargo": contato.get("cargo", ""),
            }
        )
    return normalized


# =============================================================================
# Rotas de Inventário
# =============================================================================

@empresas_bp.route("/inventario")
@login_required
def inventario():
    """Lista todas as empresas com seus dados de inventário."""
    STATUS_CHOICES = [
        'FALTA ARQUIVO',
        'AGUARDANDO FECHAMENTO FISCAL',
        'AGUARDANDO TADEU',
        'LIBERADO PARA IMPORTAÇÃO',
        'IMPORTADO',
        'LIBERADO PARA BALANÇO',
        'ENCERRADO',
        'ECD-ECF ENCERRADO',
        'JULIANA IRÁ IMPORTAR',
        'AGUARDANDO HELENA'
    ]

    saved_filters = session.get("inventario_filters", {})

    search_arg = request.args.get("q")
    if search_arg is None:
        search_term = (saved_filters.get("search") or "").strip()
    else:
        search_term = search_arg.strip()

    # Parâmetros de ordenação
    sort_arg = request.args.get('sort')
    order_arg = request.args.get('order')

    # Filtros
    allowed_tributacoes = ["Simples Nacional", "Lucro Presumido", "Lucro Real"]
    clear_tributacao = request.args.get("clear_tributacao") == "1"
    raw_tributacoes = request.args.getlist('tributacao')
    if clear_tributacao:
        tributacao_filters = []
    elif raw_tributacoes:
        tributacao_filters = [t for t in raw_tributacoes if t in allowed_tributacoes]
    else:
        tributacao_filters = saved_filters.get("tributacao_filters", [])

    # Filtro de status
    clear_status = request.args.get("clear_status") == "1"
    raw_status = request.args.getlist('status')
    if clear_status:
        status_filters = []
    elif raw_status:
        status_filters = [s for s in raw_status if s in STATUS_CHOICES]
    else:
        status_filters = saved_filters.get("status_filters", [])

    sort = sort_arg or saved_filters.get("sort") or 'codigo'
    if sort not in ('codigo', 'nome', 'tributacao'):
        sort = 'codigo'

    order = order_arg or saved_filters.get("order") or 'asc'
    if order not in ('asc', 'desc'):
        order = 'asc'

    session["inventario_filters"] = {
        "sort": sort,
        "order": order,
        "tributacao_filters": tributacao_filters,
        "status_filters": status_filters,
        "search": search_term,
    }

    all_arg = request.args.get("all")
    show_all = all_arg in ("1", "true", "on")
    page = request.args.get("page", 1, type=int)

    # Query base - empresas ativas
    base_query = Empresa.query.filter_by(ativo=True)

    # Aplicar filtro de pesquisa
    if search_term:
        like_pattern = f"%{search_term}%"
        base_query = base_query.filter(
            sa.or_(
                Empresa.codigo_empresa.ilike(like_pattern),
                Empresa.nome_empresa.ilike(like_pattern),
            )
        )

    # Aplicar filtro de tributação
    if tributacao_filters:
        valid_filters = [t for t in tributacao_filters if t in allowed_tributacoes]
        if valid_filters:
            base_query = base_query.filter(Empresa.tributacao.in_(valid_filters))

    # Aplicar ordenação
    if sort == 'codigo':
        order_column = Empresa.codigo_empresa
    elif sort == 'nome':
        order_column = Empresa.nome_empresa
    elif sort == 'tributacao':
        order_column = Empresa.tributacao
    else:
        order_column = Empresa.codigo_empresa

    if order == 'desc':
        order_by_clause = order_column.desc()
    else:
        order_by_clause = order_column.asc()

    if status_filters:
        if "FALTA ARQUIVO" in status_filters:
            query = base_query.outerjoin(Inventario).filter(
                sa.or_(
                    Inventario.status.in_(status_filters),
                    Inventario.id.is_(None),
                )
            )
        else:
            query = base_query.join(Inventario).filter(Inventario.status.in_(status_filters))
        query = query.order_by(order_by_clause)
    else:
        query = base_query.order_by(order_by_clause)

    if show_all:
        total = query.count()
        per_page = total if total > 0 else 1
        page = 1
    else:
        per_page = 50
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    empresas = pagination.items
    empresa_ids = [empresa.id for empresa in empresas]

    # Identificar empresas sem inventário (sem considerar filtro de status)
    existing_inventario_ids = set()
    if empresa_ids:
        existing_inventario_ids = {
            row[0]
            for row in db.session.query(Inventario.empresa_id)
            .filter(Inventario.empresa_id.in_(empresa_ids))
            .all()
        }

    empresas_sem_inventario = [e for e in empresas if e.id not in existing_inventario_ids]

    # Criar inventários faltantes
    created_inventarios = False
    if empresas_sem_inventario:
        # Se há filtro de status e 'FALTA ARQUIVO' não está nos filtros, não criar
        should_create = not status_filters or 'FALTA ARQUIVO' in status_filters

        if should_create:
            novos_inventarios = [
                Inventario(
                    empresa_id=e.id,
                    status='FALTA ARQUIVO',
                    encerramento_fiscal=False
                )
                for e in empresas_sem_inventario
            ]
            db.session.add_all(novos_inventarios)
            created_inventarios = True

    # Buscar inventários existentes com filtro de status aplicado no SQL
    inventarios = {}
    if empresa_ids:
        inventario_query = Inventario.query.filter(Inventario.empresa_id.in_(empresa_ids))

        # Aplicar filtro de status direto na query SQL (otimização crítica)
        if status_filters:
            inventario_query = inventario_query.filter(Inventario.status.in_(status_filters))

        inventarios = {
            inv.empresa_id: inv
            for inv in inventario_query.all()
        }

    # Criar lista combinada apenas com empresas que têm inventário
    items = []
    for empresa in empresas:
        inventario = inventarios.get(empresa.id)

        # Adicionar apenas se há inventário (respeitando filtros)
        if inventario:
            items.append({
                'empresa': empresa,
                'inventario': inventario
            })

    # Buscar todos os usuários para o select de encerramento
    from app.models.tables import User
    usuarios = User.query.filter(User.ativo.is_(True)).order_by(User.name).all()

    response = render_template(
        "empresas/inventario.html",
        items=items,
        pagination=pagination,
        status_choices=STATUS_CHOICES,
        sort=sort,
        order=order,
        tributacao_filters=tributacao_filters,
        allowed_tributacoes=allowed_tributacoes,
        status_filters=status_filters,
        search_term=search_term,
        show_all=show_all,
        all_param=1 if show_all else "",
        usuarios=usuarios
    )

    if created_inventarios:
        db.session.commit()

    return response


@empresas_bp.route("/api/inventario/update", methods=["POST"])
@login_required
@csrf.exempt
def api_inventario_update():
    """API para atualizar campos do inventário inline."""
    from decimal import Decimal, InvalidOperation

    try:
        with track_custom_span("inventario_update", "parse_payload"):
            data = request.get_json()
            empresa_id = data.get('empresa_id')
            field = data.get('field')
            value = data.get('value', '').strip()

        if not empresa_id or not field:
            return jsonify({'success': False, 'error': 'Dados inválidos'}), 400

        # Verificar se a empresa existe
        with track_custom_span("inventario_update", "load_empresa"):
            empresa = Empresa.query.get(empresa_id)
        if not empresa:
            return jsonify({'success': False, 'error': 'Empresa não encontrada'}), 404

        # Buscar ou criar inventário
        with track_custom_span("inventario_update", "load_inventario"):
            inventario = Inventario.query.filter_by(empresa_id=empresa_id).first()
            if not inventario:
                inventario = Inventario(empresa_id=empresa_id, encerramento_fiscal=False)
                db.session.add(inventario)

        # Campos monetários que precisam de conversão
        campos_monetarios = ['dief_2024', 'balanco_2025_cliente', 'fechamento_tadeu_2025', 'valor_enviado_sped']

        # Campos booleanos
        campos_booleanos = ['encerramento_fiscal']

        # Campos de data
        campos_data = ['encerramento_balanco_data']

        # Campos inteiros
        campos_inteiros = ['encerramento_balanco_usuario_id']

        # Atualizar campo
        field_map = {
            'encerramento_fiscal': 'encerramento_fiscal',
            'dief_2024': 'dief_2024',
            'balanco_2025_cliente': 'balanco_2025_cliente',
            'fechamento_tadeu_2025': 'fechamento_tadeu_2025',
            'observacoes_tadeu': 'observacoes_tadeu',
            'valor_enviado_sped': 'valor_enviado_sped',
            'status': 'status',
            'encerramento_balanco_data': 'encerramento_balanco_data',
            'encerramento_balanco_usuario_id': 'encerramento_balanco_usuario_id',
            'pdf_path': 'pdf_path',
            'cliente_pdf_path': 'cliente_pdf_path',
        }

        if field not in field_map:
            return jsonify({'success': False, 'error': 'Campo inválido'}), 400

        # Processar valor
        old_pdf_path = inventario.pdf_path
        processed_value = None
        with track_custom_span("inventario_update", "process_value"):
            if value:
                if field in campos_monetarios:
                    # Converter valor monetário (remover R$, pontos e trocar vírgula por ponto)
                    try:
                        value_clean = value.replace('R$', '').replace('.', '').replace(',', '.').strip()
                        processed_value = Decimal(value_clean) if value_clean else None

                        # Validar limite de R$ 1.000.000.000,00 (1 bilhão)
                        if processed_value is not None and processed_value > Decimal('1000000000.00'):
                            return jsonify({'success': False, 'error': 'Valor não pode exceder R$ 1.000.000.000,00'}), 400
                    except (InvalidOperation, ValueError):
                        return jsonify({'success': False, 'error': 'Valor monetário inválido'}), 400
                elif field in campos_booleanos:
                    # Converter para booleano
                    if value.lower() in ['true', '1', 'sim', 'yes']:
                        processed_value = True
                    elif value.lower() in ['false', '0', 'não', 'nao', 'no']:
                        processed_value = False
                    else:
                        processed_value = None
                elif field in campos_data:
                    # Converter para data (formato YYYY-MM-DD)
                    try:
                        processed_value = datetime.strptime(value, '%Y-%m-%d').date() if value else None
                    except ValueError:
                        return jsonify({'success': False, 'error': 'Data inválida. Use o formato AAAA-MM-DD'}), 400
                elif field in campos_inteiros:
                    # Converter para inteiro
                    try:
                        processed_value = int(value) if value else None
                    except ValueError:
                        return jsonify({'success': False, 'error': 'Valor inteiro inválido'}), 400
                else:
                    processed_value = value
            elif field in campos_booleanos:
                # Se valor vazio para booleano, setar como None
                processed_value = None
            elif field in campos_data:
                # Se valor vazio para data, setar como None
                processed_value = None
            elif field in campos_inteiros:
                # Se valor vazio para inteiro, setar como None
                processed_value = None
            setattr(inventario, field_map[field], processed_value)

        if field == "pdf_path":
            with track_custom_span("inventario_update", "cleanup_pdf"):
                is_url = bool(processed_value) and (
                    processed_value.startswith("http://")
                    or processed_value.startswith("https://")
                )
                if old_pdf_path and not (
                    old_pdf_path.startswith("http://") or old_pdf_path.startswith("https://")
                ):
                    if processed_value is None or is_url:
                        if old_pdf_path.startswith("uploads/"):
                            file_path = os.path.join(current_app.root_path, "static", old_pdf_path)
                            if os.path.exists(file_path):
                                try:
                                    os.remove(file_path)
                                except Exception:
                                    current_app.logger.exception(
                                        "Erro ao remover PDF antigo: %s", file_path
                                    )
                if is_url or processed_value is None:
                    inventario.pdf_original_name = None

        commit_started = track_commit_start()
        try:
            db.session.commit()
        finally:
            track_commit_end(commit_started)

        # Retornar valor formatado se for campo monetário
        response_value = value
        if field in campos_monetarios and processed_value is not None:
            response_value = f"R$ {processed_value:,.2f}".replace(',', '_').replace('.', ',').replace('_', '.')

        return jsonify({'success': True, 'value': response_value})

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erro ao atualizar inventário: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


def _coerce_file_entries(value):
    if not value:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _has_file_entries(value):
    return bool(_coerce_file_entries(value))


def _maybe_set_status_aguardando_tadeu(inventario):
    has_cfop = _has_file_entries(inventario.cfop_files) or bool(inventario.pdf_path)
    has_cliente = _has_file_entries(inventario.cliente_files) or bool(inventario.cliente_pdf_path)
    if not (has_cfop and has_cliente):
        return
    inventario.status = "AGUARDANDO TADEU"


@empresas_bp.route("/api/inventario/upload-pdf/<int:empresa_id>", methods=["POST"])
@login_required
def api_inventario_upload_pdf(empresa_id):
    """Upload de arquivo PDF para o inventário - suporta múltiplos arquivos (armazenado no banco)."""
    import base64
    from werkzeug.utils import secure_filename

    try:
        empresa = Empresa.query.get_or_404(empresa_id)

        if 'pdf' not in request.files:
            return jsonify({'success': False, 'error': 'Nenhum arquivo enviado'}), 400

        file = request.files['pdf']

        if file.filename == '':
            return jsonify({'success': False, 'error': 'Nenhum arquivo selecionado'}), 400

        # Verificar extensão
        if not file.filename.lower().endswith('.pdf'):
            return jsonify({'success': False, 'error': 'Apenas arquivos PDF são permitidos'}), 400

        # Buscar ou criar inventário
        inventario = Inventario.query.filter_by(empresa_id=empresa_id).first()
        if not inventario:
            inventario = Inventario(empresa_id=empresa_id, encerramento_fiscal=False)
            db.session.add(inventario)

        filename = secure_filename(file.filename)

        # Ler arquivo e converter para base64
        file_data = base64.b64encode(file.read()).decode('utf-8')

        # Atualizar array de arquivos
        cfop_files = inventario.cfop_files or []
        file_info = {
            'filename': filename,
            'file_data': file_data,
            'uploaded_at': datetime.now().isoformat(),
            'mime_type': 'application/pdf'
        }
        cfop_files.append(file_info)
        inventario.cfop_files = cfop_files
        _maybe_set_status_aguardando_tadeu(inventario)
        # Marcar explicitamente que o JSON foi modificado
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(inventario, 'cfop_files')

        db.session.commit()

        return jsonify({
            'success': True,
            'filename': filename,
            'uploaded_at': file_info['uploaded_at'],
            'storage': 'database',
            'status': inventario.status,
            'file_index': len(cfop_files) - 1  # Índice do arquivo no array
        })

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erro ao fazer upload do PDF: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


@empresas_bp.route("/api/inventario/delete-pdf/<int:empresa_id>", methods=["POST"])
@login_required
def api_inventario_delete_pdf(empresa_id):
    """Remove o PDF do inventário."""
    import os

    try:
        inventario = Inventario.query.filter_by(empresa_id=empresa_id).first()

        if not inventario or not inventario.pdf_path:
            return jsonify({'success': False, 'error': 'Arquivo não encontrado'}), 404

        # Remover arquivo local se não for URL
        if not inventario.pdf_path.startswith('http'):
            file_path = os.path.join(current_app.root_path, 'static', inventario.pdf_path)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as e:
                    current_app.logger.warning("Erro ao remover arquivo físico: %s", e)

        # Limpar campos
        inventario.pdf_path = None
        inventario.pdf_original_name = None

        db.session.commit()

        return jsonify({'success': True})

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erro ao deletar PDF: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


@empresas_bp.route("/api/inventario/upload-cliente-file/<int:empresa_id>", methods=["POST"])
@login_required
def api_inventario_upload_cliente_file(empresa_id):
    """Upload de arquivo do cliente para o inventário - suporta múltiplos arquivos (armazenado no banco)."""
    import base64
    import mimetypes
    from werkzeug.utils import secure_filename

    try:
        empresa = Empresa.query.get_or_404(empresa_id)

        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'Nenhum arquivo enviado'}), 400

        file = request.files['file']

        if file.filename == '':
            return jsonify({'success': False, 'error': 'Nenhum arquivo selecionado'}), 400

        # Buscar ou criar inventário
        inventario = Inventario.query.filter_by(empresa_id=empresa_id).first()
        if not inventario:
            inventario = Inventario(empresa_id=empresa_id, encerramento_fiscal=False)
            db.session.add(inventario)

        filename = secure_filename(file.filename)

        # Detectar tipo MIME
        mime_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'

        # Ler arquivo e converter para base64
        file_data = base64.b64encode(file.read()).decode('utf-8')

        # Atualizar array de arquivos
        cliente_files = inventario.cliente_files or []
        file_info = {
            'filename': filename,
            'file_data': file_data,
            'uploaded_at': datetime.now().isoformat(),
            'mime_type': mime_type
        }
        cliente_files.append(file_info)
        inventario.cliente_files = cliente_files
        _maybe_set_status_aguardando_tadeu(inventario)
        # Marcar explicitamente que o JSON foi modificado
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(inventario, 'cliente_files')

        db.session.commit()

        return jsonify({
            'success': True,
            'filename': filename,
            'uploaded_at': file_info['uploaded_at'],
            'storage': 'database',
            'status': inventario.status,
            'file_index': len(cliente_files) - 1  # Índice do arquivo no array
        })

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erro ao fazer upload do arquivo do cliente: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


@empresas_bp.route("/api/inventario/file/<int:empresa_id>/<file_type>/<int:file_index>", methods=["GET"])
@login_required
def api_inventario_get_file(empresa_id, file_type, file_index):
    """Serve arquivo do inventário armazenado no banco de dados."""
    import base64
    from flask import send_file
    import io

    try:
        inventario = Inventario.query.filter_by(empresa_id=empresa_id).first()

        if not inventario:
            return jsonify({'success': False, 'error': 'Inventário não encontrado'}), 404

        # Selecionar array de arquivos correto
        if file_type == 'cfop':
            files_array = inventario.cfop_files or []
        elif file_type == 'cliente':
            files_array = inventario.cliente_files or []
        else:
            return jsonify({'success': False, 'error': 'Tipo de arquivo inválido'}), 400

        # Verificar se o índice é válido
        if file_index < 0 or file_index >= len(files_array):
            return jsonify({'success': False, 'error': 'Arquivo não encontrado'}), 404

        file_info = files_array[file_index]

        # Verificar se tem dados do arquivo
        if 'file_data' not in file_info:
            return jsonify({'success': False, 'error': 'Dados do arquivo não encontrados'}), 404

        # Decodificar base64
        file_data = base64.b64decode(file_info['file_data'])

        # Criar buffer de memória
        file_buffer = io.BytesIO(file_data)
        file_buffer.seek(0)

        # Obter informações do arquivo
        filename = file_info.get('filename', 'arquivo')
        mime_type = file_info.get('mime_type', 'application/octet-stream')

        # Enviar arquivo
        return send_file(
            file_buffer,
            mimetype=mime_type,
            as_attachment=False,
            download_name=filename
        )

    except Exception as e:
        current_app.logger.exception("Erro ao servir arquivo do inventário: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


@empresas_bp.route("/api/inventario/delete-cliente-file/<int:empresa_id>", methods=["POST"])
@login_required
def api_inventario_delete_cliente_file(empresa_id):
    """Remove o arquivo do cliente do inventário."""
    import os

    try:
        inventario = Inventario.query.filter_by(empresa_id=empresa_id).first()

        if not inventario or not inventario.cliente_pdf_path:
            return jsonify({'success': False, 'error': 'Arquivo não encontrado'}), 404

        # Remover arquivo local se não for URL
        if not inventario.cliente_pdf_path.startswith('http'):
            file_path = os.path.join(current_app.root_path, 'static', inventario.cliente_pdf_path)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as e:
                    current_app.logger.warning("Erro ao remover arquivo físico: %s", e)

        # Limpar campos
        inventario.cliente_pdf_path = None
        inventario.cliente_original_name = None

        db.session.commit()

        return jsonify({'success': True})

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erro ao deletar arquivo do cliente: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


@empresas_bp.route("/api/inventario/delete-cfop-file/<int:empresa_id>", methods=["POST"])
@login_required
def api_inventario_delete_cfop_file(empresa_id):
    """Remove um arquivo específico do CFOP (armazenado no banco de dados)."""
    try:
        data = request.get_json()
        file_index = data.get('file_index')

        if file_index is None:
            return jsonify({'success': False, 'error': 'Índice do arquivo não fornecido'}), 400

        inventario = Inventario.query.filter_by(empresa_id=empresa_id).first()

        if not inventario or not inventario.cfop_files:
            return jsonify({'success': False, 'error': 'Arquivo não encontrado'}), 404

        # Verificar se o índice é válido
        cfop_files = inventario.cfop_files or []
        if file_index < 0 or file_index >= len(cfop_files):
            return jsonify({'success': False, 'error': 'Arquivo não encontrado'}), 404

        # Remover arquivo do array
        cfop_files.pop(file_index)
        inventario.cfop_files = cfop_files

        # Marcar explicitamente que o JSON foi modificado
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(inventario, 'cfop_files')
        db.session.commit()

        return jsonify({'success': True}), 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erro ao deletar arquivo CFOP: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


@empresas_bp.route("/api/inventario/delete-cliente-file-v2/<int:empresa_id>", methods=["POST"])
@login_required
def api_inventario_delete_cliente_file_v2(empresa_id):
    """Remove um arquivo específico do cliente (armazenado no banco de dados)."""
    try:
        data = request.get_json()
        file_index = data.get('file_index')

        if file_index is None:
            return jsonify({'success': False, 'error': 'Índice do arquivo não fornecido'}), 400

        inventario = Inventario.query.filter_by(empresa_id=empresa_id).first()

        if not inventario or not inventario.cliente_files:
            return jsonify({'success': False, 'error': 'Arquivo não encontrado'}), 404

        # Verificar se o índice é válido
        cliente_files = inventario.cliente_files or []
        if file_index < 0 or file_index >= len(cliente_files):
            return jsonify({'success': False, 'error': 'Arquivo não encontrado'}), 404

        # Remover arquivo do array
        cliente_files.pop(file_index)
        inventario.cliente_files = cliente_files

        # Marcar explicitamente que o JSON foi modificado
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(inventario, 'cliente_files')
        db.session.commit()

        return jsonify({'success': True}), 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erro ao deletar arquivo do cliente: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500
