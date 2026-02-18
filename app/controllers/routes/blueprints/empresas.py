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
from datetime import datetime, date
from io import BytesIO
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode
from uuid import uuid4

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
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import defer, joinedload, load_only
from sqlalchemy.orm.attributes import flag_modified
from werkzeug.exceptions import NotFound

from app import csrf, db, limiter
from app.constants import EMPRESA_TAG_CHOICES, INVENTARIO_UPLOAD_SUBDIR
from app.controllers.routes import decode_id, encode_id, user_has_tag
from app.controllers.routes._decorators import meeting_only_access_check
from app.extensions.task_queue import submit_io_task
from app.extensions.cache import cache, get_cache_timeout
from app.services.optimized_queries import (
    get_active_users_with_tags,
    get_inventario_file_counts_by_empresa_ids,
    get_inventarios_by_empresa_ids,
)
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
from app.services.reuniao_export import export_reuniao_decisoes_pdf
from app.services.general_calendar import serialize_events_for_calendar
from app.services.google_calendar import get_calendar_timezone
from app.services.meeting_room import (
    combine_events,
    fetch_raw_events,
    try_get_cached_combined_events,
)
from app.utils.performance_middleware import track_commit_end, track_commit_start, track_custom_span
from app.utils.permissions import is_user_admin
from app.utils.mailer import send_email, EmailDeliveryError
from app.utils.security import sanitize_html

empresas_bp = Blueprint("empresas", __name__)

INVENTARIO_STATUS_CHOICES = [
    "FALTA ARQUIVO",
    "AGUARDANDO FECHAMENTO FISCAL",
    "AGUARDANDO TADEU",
    "LIBERADO PARA IMPORTAÇÃO",
    "IMPORTADO",
    "LIBERADO PARA BALANÇO",
    "ENCERRADO",
    "ECD-ECF ENCERRADO",
    "JULIANA IRÁ IMPORTAR",
    "AGUARDANDO HELENA",
]


def _get_inventario_cache_version() -> int:
    version = cache.get("inventario:cache_version")
    if version is None:
        version = 1
        cache.set("inventario:cache_version", version, timeout=60 * 60 * 24 * 30)
    return int(version)


def _bump_inventario_cache_version() -> int:
    next_version = _get_inventario_cache_version() + 1
    cache.set("inventario:cache_version", next_version, timeout=60 * 60 * 24 * 30)
    return next_version


def _build_inventario_dashboard_cache_key(cache_key_payload: dict) -> str:
    return f"inventario:dashboard:{json.dumps(cache_key_payload, sort_keys=True)}"


def _compute_inventario_dashboard_cards(
    *,
    base_query,
    inventario_joined: bool,
    status_filters: list[str],
    allowed_tributacoes: list[str],
) -> list[dict]:
    dashboard_stats = {
        trib: {
            "tributacao": trib,
            "total": 0,
            "concluida": 0,
            "aguardando_arquivo": 0,
            "fechamento_fiscal": 0,
        }
        for trib in allowed_tributacoes
    }

    stats_query = base_query
    if status_filters:
        if "FALTA ARQUIVO" in status_filters:
            if not inventario_joined:
                stats_query = stats_query.outerjoin(Inventario)
            stats_query = stats_query.filter(
                sa.or_(
                    Inventario.status.in_(status_filters),
                    Inventario.id.is_(None),
                )
            )
        else:
            if not inventario_joined:
                stats_query = stats_query.join(Inventario)
            stats_query = stats_query.filter(Inventario.status.in_(status_filters))
    else:
        if not inventario_joined:
            stats_query = stats_query.outerjoin(Inventario)

    stats_rows = (
        stats_query
        .with_entities(
            Empresa.tributacao.label("tributacao"),
            sa.func.count(Empresa.id).label("total"),
            sa.func.coalesce(
                sa.func.sum(sa.case((Inventario.status == "ENCERRADO", 1), else_=0)),
                0,
            ).label("concluida"),
            sa.func.coalesce(
                sa.func.sum(sa.case((Inventario.status == "FALTA ARQUIVO", 1), else_=0)),
                0,
            ).label("aguardando_arquivo"),
            sa.func.coalesce(
                sa.func.sum(sa.case((Inventario.encerramento_fiscal.is_(True), 1), else_=0)),
                0,
            ).label("fechamento_fiscal"),
        )
        .group_by(Empresa.tributacao)
        .all()
    )
    for row in stats_rows:
        tributacao = row.tributacao
        if tributacao not in dashboard_stats:
            continue
        stats = dashboard_stats[tributacao]
        stats["total"] = int(row.total or 0)
        stats["concluida"] = int(row.concluida or 0)
        stats["aguardando_arquivo"] = int(row.aguardando_arquivo or 0)
        stats["fechamento_fiscal"] = int(row.fechamento_fiscal or 0)
    for stats in dashboard_stats.values():
        stats["faltantes"] = max(stats["total"] - stats["concluida"], 0)
    return [dashboard_stats[trib] for trib in allowed_tributacoes]


def _get_or_set_inventario_dashboard_cards(
    *,
    base_query,
    inventario_joined: bool,
    status_filters: list[str],
    allowed_tributacoes: list[str],
    cache_key_payload: dict,
    cache_timeout: int,
) -> list[dict]:
    cache_key = _build_inventario_dashboard_cache_key(cache_key_payload)
    dashboard_cards = cache.get(cache_key)
    if dashboard_cards is not None:
        return dashboard_cards

    dashboard_cards = _compute_inventario_dashboard_cards(
        base_query=base_query,
        inventario_joined=inventario_joined,
        status_filters=status_filters,
        allowed_tributacoes=allowed_tributacoes,
    )
    cache.set(cache_key, dashboard_cards, timeout=cache_timeout)
    return dashboard_cards


def _prewarm_default_inventario_dashboard_cache(app_obj) -> None:
    try:
        with app_obj.app_context():
            cache_version = _get_inventario_cache_version()
            allowed_tributacoes = ["MEI", "Simples Nacional", "Lucro Presumido", "Lucro Real"]
            default_tag_filters = ["Matriz", "Filial"]
            base_query = Empresa.query.filter_by(ativo=True).filter(
                sa.or_(Empresa.tipo_empresa.in_(default_tag_filters), Empresa.tipo_empresa.is_(None))
            )
            cache_key_payload = {
                "tributacao": [],
                "encerramento": [],
                "status": [],
                "tag": default_tag_filters,
                "search": "",
                "v": cache_version,
            }
            cache_timeout = get_cache_timeout("INVENTARIO_DASHBOARD_CACHE_SECONDS", 60)
            dashboard_cards = _compute_inventario_dashboard_cards(
                base_query=base_query,
                inventario_joined=False,
                status_filters=[],
                allowed_tributacoes=allowed_tributacoes,
            )
            cache_key = _build_inventario_dashboard_cache_key(cache_key_payload)
            cache.set(cache_key, dashboard_cards, timeout=cache_timeout)
    except Exception:
        app_obj.logger.exception("Inventario dashboard prewarm failed")


def _invalidate_and_prewarm_inventario_caches() -> None:
    cooldown = int(current_app.config.get("INVENTARIO_CACHE_INVALIDATION_COOLDOWN_SECONDS", 15))
    bump_lock_key = "inventario:cache_version:bump_lock"
    should_bump = cache.add(bump_lock_key, "1", timeout=max(1, cooldown))
    if not should_bump:
        return

    _bump_inventario_cache_version()
    prewarm_lock_key = "inventario:dashboard:prewarm:lock"
    should_prewarm = cache.add(prewarm_lock_key, "1", timeout=15)
    if not should_prewarm:
        return
    app_obj = current_app._get_current_object()
    submit_io_task(_prewarm_default_inventario_dashboard_cache, app_obj)


def _build_inventario_items_for_empresas(
    empresas,
    status_filters: list[str],
    *,
    include_file_columns: bool = False,
) -> tuple[list[dict], dict]:
    empresa_ids = [empresa.id for empresa in empresas]
    inventarios_by_empresa = (
        get_inventarios_by_empresa_ids(empresa_ids, include_file_columns=include_file_columns)
        if empresa_ids
        else {}
    )
    file_counts_by_empresa = get_inventario_file_counts_by_empresa_ids(empresa_ids) if empresa_ids else {}

    items: list[dict] = []
    for empresa in empresas:
        inventario = inventarios_by_empresa.get(empresa.id)

        if inventario is None and (not status_filters or "FALTA ARQUIVO" in status_filters):
            inventario = Inventario(
                empresa_id=empresa.id,
                status='FALTA ARQUIVO',
                encerramento_fiscal=False,
            )

        if status_filters:
            if inventario is None:
                continue
            if inventario.status not in status_filters:
                continue

        items.append({'empresa': empresa, 'inventario': inventario})

    return items, file_counts_by_empresa


def _build_zero_inventario_dashboard_cards(allowed_tributacoes: list[str]) -> list[dict]:
    return [
        {
            "tributacao": trib,
            "total": 0,
            "concluida": 0,
            "aguardando_arquivo": 0,
            "faltantes": 0,
            "fechamento_fiscal": 0,
        }
        for trib in allowed_tributacoes
    ]


def _build_inventario_base_query(
    *,
    search_term: str,
    tributacao_filters: list[str],
    encerramento_filters: list[str],
    tag_filters: list[str],
    allowed_tributacoes: list[str],
):
    """Build base query for inventario list/dashboard with common filters."""
    base_query = Empresa.query.filter_by(ativo=True)

    if tag_filters:
        if "Matriz" in tag_filters:
            base_query = base_query.filter(
                sa.or_(Empresa.tipo_empresa.in_(tag_filters), Empresa.tipo_empresa.is_(None))
            )
        else:
            base_query = base_query.filter(Empresa.tipo_empresa.in_(tag_filters))

    if search_term:
        like_pattern = f"%{search_term}%"
        base_query = base_query.filter(
            sa.or_(
                Empresa.codigo_empresa.ilike(like_pattern),
                Empresa.nome_empresa.ilike(like_pattern),
            )
        )

    if tributacao_filters:
        valid_filters = [t for t in tributacao_filters if t in allowed_tributacoes]
        if valid_filters:
            base_query = base_query.filter(Empresa.tributacao.in_(valid_filters))

    inventario_joined = False
    if encerramento_filters:
        bool_filters = []
        for encerramento_value in encerramento_filters:
            if encerramento_value == "true":
                bool_filters.append(True)
            elif encerramento_value == "false":
                bool_filters.append(False)

        if bool_filters:
            base_query = base_query.outerjoin(Inventario).filter(
                Inventario.encerramento_fiscal.in_(bool_filters)
            )
            inventario_joined = True

    return base_query, inventario_joined


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

    can_manage = is_user_admin(current_user) or user_has_tag("Gestáo") or user_has_tag("Coord.")
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
        form.tipo_empresa.data = form.tipo_empresa.data or "Matriz"
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
                tipo_empresa=form.tipo_empresa.data or "Matriz",
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

    page_arg = request.args.get("page", type=int)
    if page_arg is None:
        page = saved_filters.get("page", 1)
    else:
        page = page_arg
    per_page = 20
    show_inactive = request.args.get("show_inactive") in ("1", "on", "true", "True")
    allowed_tributacoes = ["Simples Nacional", "Lucro Presumido", "Lucro Real"]
    allowed_tag_filters = [value for value, _ in EMPRESA_TAG_CHOICES]
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

    clear_tag = request.args.get("clear_tag") == "1"
    raw_tags = request.args.getlist("tag")
    if clear_tag:
        tag_filters = ["Matriz"]
    elif raw_tags:
        tag_filters = [t for t in raw_tags if t in allowed_tag_filters]
    else:
        tag_filters = saved_filters.get("tag_filters") or ["Matriz"]
    if not tag_filters:
        tag_filters = ["Matriz"]

    sort = sort_arg or saved_filters.get("sort") or "nome"
    if sort not in ("nome", "codigo"):
        sort = "nome"

    order = order_arg or saved_filters.get("order") or "asc"
    if order not in ("asc", "desc"):
        order = "asc"

    session["listar_empresas_filters"] = {
        "sort": sort,
        "order": order,
        "search": search,
        "tag_filters": tag_filters,
        "page": page,
    }

    query = Empresa.query

    if show_inactive:
        query = query.filter_by(ativo=False)
    else:
        query = query.filter_by(ativo=True)

    if tributacao_filters:
        query = query.filter(Empresa.tributacao.in_(tributacao_filters))

    if tag_filters:
        if "Matriz" in tag_filters:
            query = query.filter(sa.or_(Empresa.tipo_empresa.in_(tag_filters), Empresa.tipo_empresa.is_(None)))
        else:
            query = query.filter(Empresa.tipo_empresa.in_(tag_filters))

    if search:
        like_pattern = f"%{search}%"
        cnpj_digits = re.sub(r"\D", "", search)
        search_filters = [
            Empresa.nome_empresa.ilike(like_pattern),
            Empresa.codigo_empresa.ilike(like_pattern),
        ]
        if cnpj_digits:
            cnpj_like = f"%{cnpj_digits}%"
            search_filters.append(Empresa.cnpj.ilike(cnpj_like))
        query = query.filter(sa.or_(*search_filters))

    order_column = Empresa.codigo_empresa if sort == "codigo" else Empresa.nome_empresa
    if order == "desc":
        query = query.order_by(Empresa.ativo.desc(), order_column.desc())
    else:
        query = query.order_by(Empresa.ativo.desc(), order_column.asc())

    session["listar_empresas_filters"] = {
        "sort": sort,
        "order": order,
        "tributacao_filters": tributacao_filters,
        "tag_filters": tag_filters,
        "search": search,
        "page": page,
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
        tag_filters=tag_filters,
        allowed_tag_filters=allowed_tag_filters,
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
        empresa_form.tipo_empresa.data = empresa.tipo_empresa or "Matriz"

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
    try:
        resolved_empresa_id = decode_id(str(raw_empresa), namespace="empresa")
    except NotFound:
        flash("Empresa não encontrada.", "warning")
        return redirect(url_for("empresas.listar_empresas"))

    empresa = Empresa.query.get(resolved_empresa_id)
    if empresa is None:
        flash("Empresa não encontrada.", "warning")
        return redirect(url_for("empresas.listar_empresas"))

    empresa_token = encode_id(resolved_empresa_id, namespace="empresa")

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
        user_ids, _ = _separate_cliente_reuniao_participantes(reuniao.participantes or [])
        participante_ids.update(user_ids)
    reunioes_participantes_map: dict[int, User] = {}
    if participante_ids:
        usuarios = User.query.filter(User.id.in_(participante_ids)).all()
        reunioes_participantes_map = {usuario.id: usuario for usuario in usuarios}
    for reuniao in cliente_reunioes:
        reuniao.participantes_resolvidos = _resolve_reuniao_participantes(
            reuniao.participantes or [],
            reunioes_participantes_map,
        )

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

    # Otimizado: usa cache e eager loading de tags
    usuarios_responsaveis = get_active_users_with_tags()
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


@empresas_bp.route("/empresa/visualizar_por_codigo")
@login_required
def visualizar_empresa_por_codigo():
    """Redirect to company details page based on company code."""
    def _codigo_base(valor: str | None) -> str:
        texto = (valor or "").strip()
        if not texto:
            return ""
        # Quando houver DV separado (ex.: 123-4), considera apenas o codigo base.
        match = re.match(r"^(\d+)\s*[-/]\s*[\dxX]+$", texto)
        if match:
            return match.group(1)
        return re.sub(r"\D", "", texto)

    codigo = (request.args.get("codigo") or "").strip()
    fallback_url = url_for("empresas.listar_empresas")
    if request.referrer and request.referrer.startswith(request.host_url):
        fallback_url = request.referrer

    if not codigo:
        flash("Informe o codigo da empresa.", "warning")
        return redirect(fallback_url)

    empresa = (
        Empresa.query.filter(sa.func.lower(sa.func.trim(Empresa.codigo_empresa)) == codigo.lower())
        .order_by(Empresa.ativo.desc(), Empresa.id.asc())
        .first()
    )
    if empresa is None:
        codigo_base = _codigo_base(codigo)
        if codigo_base:
            candidatas = (
                Empresa.query.options(load_only(Empresa.id, Empresa.codigo_empresa, Empresa.ativo))
                .all()
            )
            compativeis = [
                e for e in candidatas
                if _codigo_base(e.codigo_empresa) == codigo_base
            ]
            if compativeis:
                compativeis.sort(key=lambda e: (not bool(e.ativo), e.id))
                empresa = compativeis[0]

    if empresa is None:
        flash(f"Empresa com codigo {codigo} nao encontrada.", "warning")
        return redirect(fallback_url)

    empresa_token = encode_id(empresa.id, namespace="empresa")
    return redirect(url_for("empresas.visualizar_empresa", empresa_id=empresa_token))


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
    # Otimizado: usa cache e eager loading de tags
    usuarios_responsaveis = [
        {"id": str(usuario.id), "label": usuario.name or usuario.username or f"Usuário {usuario.id}"}
        for usuario in get_active_users_with_tags()
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
        user_ids, _ = _separate_cliente_reuniao_participantes(reuniao.participantes or [])
        participante_ids.update(user_ids)
    reunioes_participantes_map: dict[int, User] = {}
    if participante_ids:
        usuarios = User.query.filter(User.id.in_(participante_ids)).all()
        reunioes_participantes_map = {usuario.id: usuario for usuario in usuarios}
    for reuniao in reunioes_cliente:
        reuniao.participantes_resolvidos = _resolve_reuniao_participantes(
            reuniao.participantes or [],
            reunioes_participantes_map,
        )

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

    # Otimizado: usa cache e eager loading de tags
    usuarios = get_active_users_with_tags()
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


def _parse_cliente_reuniao_participantes_extras(payload: str | None) -> list[str]:
    """Return sanitized list of external participant names."""

    if not payload:
        return []
    try:
        data = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return []
    nomes: list[str] = []
    for item in data:
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                nomes.append(cleaned[:255])
    return nomes


def _separate_cliente_reuniao_participantes(participantes: list | None) -> tuple[list[int], list[str]]:
    """Split stored participants into portal user IDs and external names."""

    user_ids: list[int] = []
    guest_names: list[str] = []
    if not participantes:
        return user_ids, guest_names

    for participante in participantes:
        if isinstance(participante, dict):
            p_type = (participante.get("type") or participante.get("tipo") or participante.get("kind") or "").lower()
            if p_type == "guest":
                nome = (participante.get("name") or participante.get("nome") or participante.get("label") or "").strip()
                if nome:
                    guest_names.append(nome[:255])
                continue
            pid = participante.get("id") or participante.get("user_id")
            if isinstance(pid, int):
                user_ids.append(pid)
                continue
            alt_nome = (participante.get("name") or participante.get("label") or "").strip()
            if alt_nome:
                guest_names.append(alt_nome[:255])
        elif isinstance(participante, int):
            user_ids.append(participante)
        elif isinstance(participante, str):
            nome = participante.strip()
            if nome:
                guest_names.append(nome[:255])
    return user_ids, guest_names


def _build_cliente_reuniao_participantes(user_ids: list[int], extras: list[str]) -> list[dict]:
    """Return normalized payload mixing portal users and external guests."""

    participantes: list[dict] = []
    seen_users: set[int] = set()
    for pid in user_ids or []:
        if isinstance(pid, int) and pid not in seen_users:
            participantes.append({"type": "user", "id": pid})
            seen_users.add(pid)
    for nome in extras or []:
        cleaned = (nome or "").strip()
        if cleaned:
            participantes.append({"type": "guest", "name": cleaned[:255]})
    return participantes


def _resolve_reuniao_participantes(participantes_raw: list | None, user_lookup: dict[int, User] | None = None) -> list[dict]:
    """Return participant dicts ready for display."""

    user_ids, _ = _separate_cliente_reuniao_participantes(participantes_raw)
    lookup = user_lookup or {}
    if not lookup and user_ids:
        usuarios = User.query.filter(User.id.in_(user_ids)).all()
        lookup = {usuario.id: usuario for usuario in usuarios}

    resolved: list[dict] = []
    for participante in participantes_raw or []:
        if isinstance(participante, dict):
            p_type = (participante.get("type") or participante.get("tipo") or participante.get("kind") or "").lower()
            if p_type == "guest":
                nome = (participante.get("name") or participante.get("nome") or participante.get("label") or "").strip()
                if nome:
                    resolved.append({"label": nome, "is_user": False})
                continue
            pid = participante.get("id") or participante.get("user_id")
            if isinstance(pid, int):
                usuario = lookup.get(pid) if isinstance(lookup, dict) else None
                nome = None
                if usuario is not None:
                    nome = getattr(usuario, "name", None) or getattr(usuario, "username", None)
                resolved.append({"label": nome or f"Usuário #{pid}", "is_user": True, "id": pid})
                continue
            alt_nome = (participante.get("name") or participante.get("label") or "").strip()
            if alt_nome:
                resolved.append({"label": alt_nome, "is_user": False})
        elif isinstance(participante, int):
            usuario = lookup.get(participante) if isinstance(lookup, dict) else None
            nome = None
            if usuario is not None:
                nome = getattr(usuario, "name", None) or getattr(usuario, "username", None)
            resolved.append({"label": nome or f"Usuário #{participante}", "is_user": True, "id": participante})
        elif isinstance(participante, str):
            nome = participante.strip()
            if nome:
                resolved.append({"label": nome, "is_user": False})
    return resolved


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
    if not form.participantes_extras.data:
        form.participantes_extras.data = "[]"

    if form.validate_on_submit():
        topicos = _parse_cliente_reuniao_topicos(form.topicos_json.data)
        participantes_extras = _parse_cliente_reuniao_participantes_extras(form.participantes_extras.data)
        participantes_payload = _build_cliente_reuniao_participantes(form.participantes.data or [], participantes_extras)
        reuniao = ClienteReuniao(
            empresa_id=empresa.id,
            data=form.data.data,
            setor_id=form.setor_id.data or None,
            participantes=participantes_payload,
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
    if not form.participantes_extras.data:
        form.participantes_extras.data = "[]"

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
    if not form.participantes_extras.data:
        form.participantes_extras.data = "[]"

    if request.method == "GET":
        user_ids, guest_names = _separate_cliente_reuniao_participantes(reuniao.participantes or [])
        form.participantes.data = user_ids
        form.participantes_extras.data = json.dumps(guest_names)
        form.setor_id.data = reuniao.setor_id or 0
        form.topicos_json.data = json.dumps(reuniao.topicos or [])
        form.decisoes.data = reuniao.decisoes or ""
    if form.validate_on_submit():
        topicos = _parse_cliente_reuniao_topicos(form.topicos_json.data)
        participantes_extras = _parse_cliente_reuniao_participantes_extras(form.participantes_extras.data)
        reuniao.data = form.data.data
        reuniao.setor_id = form.setor_id.data or None
        reuniao.participantes = _build_cliente_reuniao_participantes(
            form.participantes.data or [],
            participantes_extras,
        )
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
    if not form.participantes_extras.data:
        form.participantes_extras.data = "[]"

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


@empresas_bp.route("/empresa/<empresa_id>/reunioes-cliente/<reuniao_id>/pdf")
@empresas_bp.route("/empresa/<int:id>/reunioes-cliente/<reuniao_id>/pdf")
@empresas_bp.route("/empresa/<empresa_id>/reunioes-cliente/<int:rid>/pdf")
@empresas_bp.route("/empresa/<int:id>/reunioes-cliente/<int:rid>/pdf")
@login_required
def reuniao_cliente_pdf(empresa_id: str | None = None, reuniao_id: str | None = None, id: int | None = None, rid: int | None = None):
    """Export meeting decisions to PDF using the timbrado template."""

    raw_empresa = empresa_id if empresa_id is not None else id
    raw_reuniao = reuniao_id if reuniao_id is not None else rid
    empresa_id_int = decode_id(str(raw_empresa), namespace="empresa")
    reuniao_id_int = decode_id(str(raw_reuniao), namespace="empresa-reuniao")
    reuniao = (
        ClienteReuniao.query.filter_by(id=reuniao_id_int, empresa_id=empresa_id_int)
        .options(joinedload(ClienteReuniao.setor), joinedload(ClienteReuniao.autor))
        .first_or_404()
    )

    try:
        pdf_bytes, filename = export_reuniao_decisoes_pdf(reuniao)
    except FileNotFoundError as exc:
        current_app.logger.error("Modelo de timbrado não encontrado: %s", exc)
        abort(404, description="Modelo de timbrado não encontrado.")
    except Exception as exc:  # pragma: no cover - caminho de erro
        current_app.logger.exception("Falha ao gerar PDF da reunião", exc_info=exc)
        abort(500, description="Falha ao gerar PDF da reunião.")
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
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
    saved_filters = session.get("inventario_filters", {})

    search_arg = request.args.get("q")
    if search_arg is None:
        search_term = (saved_filters.get("search") or "").strip()
    else:
        search_term = search_arg.strip()

    page_arg = request.args.get("page", type=int)
    if page_arg is None:
        saved_page = saved_filters.get("page", 1)
    else:
        saved_page = page_arg

    # Parâmetros de ordenação
    sort_arg = request.args.get('sort')
    order_arg = request.args.get('order')

    # Filtros
    allowed_tributacoes = ["MEI", "Simples Nacional", "Lucro Presumido", "Lucro Real"]
    allowed_tag_filters = [value for value, _ in EMPRESA_TAG_CHOICES]
    clear_tributacao = request.args.get("clear_tributacao") == "1"
    raw_tributacoes = request.args.getlist('tributacao')
    if clear_tributacao:
        tributacao_filters = []
    elif raw_tributacoes:
        tributacao_filters = [t for t in raw_tributacoes if t in allowed_tributacoes]
    else:
        tributacao_filters = saved_filters.get("tributacao_filters", [])

    # Filtro de Encerramento Fiscal
    clear_encerramento = request.args.get("clear_encerramento") == "1"
    raw_encerramento = request.args.getlist('encerramento')
    if clear_encerramento:
        encerramento_filters = []
    elif raw_encerramento:
        encerramento_filters = [e for e in raw_encerramento if e in ["true", "false"]]
    else:
        encerramento_filters = saved_filters.get("encerramento_filters", [])

    clear_tag = request.args.get("clear_tag") == "1"
    raw_tags = request.args.getlist("tag")
    if clear_tag:
        tag_filters = ["Matriz", "Filial"]
    elif raw_tags:
        tag_filters = [t for t in raw_tags if t in allowed_tag_filters]
    else:
        tag_filters = saved_filters.get("tag_filters") or ["Matriz", "Filial"]
    if not tag_filters:
        tag_filters = ["Matriz", "Filial"]

    # Filtro de status
    clear_status = request.args.get("clear_status") == "1"
    raw_status = request.args.getlist('status')
    if clear_status:
        status_filters = []
    elif raw_status:
        status_filters = [s for s in raw_status if s in INVENTARIO_STATUS_CHOICES]
    else:
        status_filters = saved_filters.get("status_filters", [])

    sort = sort_arg or saved_filters.get("sort") or 'nome'
    if sort not in ('codigo', 'nome', 'tributacao'):
        sort = 'nome'

    order = order_arg or saved_filters.get("order") or 'asc'
    if order not in ('asc', 'desc'):
        order = 'asc'

    # Paginação vs listagem completa
    username_normalized = (current_user.username or "").strip().lower()
    default_show_all = username_normalized == "tadeu"
    all_arg = request.args.get("all")
    if all_arg is None:
        show_all = saved_filters.get("show_all", default_show_all)
    else:
        show_all = all_arg in ("1", "on", "true", "True")

    session["inventario_filters"] = {
        "sort": sort,
        "order": order,
        "tributacao_filters": tributacao_filters,
        "encerramento_filters": encerramento_filters,
        "status_filters": status_filters,
        "tag_filters": tag_filters,
        "search": search_term,
        "page": saved_page,
        "show_all": show_all,
    }

    page = saved_page if saved_page > 0 else 1

    base_query, inventario_joined = _build_inventario_base_query(
        search_term=search_term,
        tributacao_filters=tributacao_filters,
        encerramento_filters=encerramento_filters,
        tag_filters=tag_filters,
        allowed_tributacoes=allowed_tributacoes,
    )

    cache_timeout = get_cache_timeout("INVENTARIO_DASHBOARD_CACHE_SECONDS", 60)
    cache_version = _get_inventario_cache_version()
    cache_key_payload = {
        "tributacao": tributacao_filters,
        "encerramento": encerramento_filters,
        "status": status_filters,
        "tag": tag_filters,
        "search": search_term,
        "v": cache_version,
    }
    dashboard_async = bool(current_app.config.get("INVENTARIO_DASHBOARD_ASYNC", True))
    if dashboard_async:
        dashboard_cards = _build_zero_inventario_dashboard_cards(allowed_tributacoes)
    else:
        dashboard_cards = _get_or_set_inventario_dashboard_cards(
            base_query=base_query,
            inventario_joined=inventario_joined,
            status_filters=status_filters,
            allowed_tributacoes=allowed_tributacoes,
            cache_key_payload=cache_key_payload,
            cache_timeout=cache_timeout,
        )

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
            if not inventario_joined:
                query = base_query.outerjoin(Inventario)
            else:
                query = base_query
            query = query.filter(
                sa.or_(
                    Inventario.status.in_(status_filters),
                    Inventario.id.is_(None),
                )
            )
        else:
            # Usa EXISTS para evitar join+sort pesado em ordenação por campos de Empresa.
            status_exists = sa.exists(
                sa.select(1)
                .select_from(Inventario)
                .where(
                    Inventario.empresa_id == Empresa.id,
                    Inventario.status.in_(status_filters),
                )
            )
            query = base_query.filter(status_exists)
        query = query.order_by(order_by_clause)
    else:
        query = base_query.order_by(order_by_clause)

    query = query.options(
        load_only(
            Empresa.id,
            Empresa.codigo_empresa,
            Empresa.nome_empresa,
            Empresa.tributacao,
            Empresa.tipo_empresa,
        )
    )

    # Buscar usuarios uma unica vez por request e reutilizar no template/chunks.
    usuarios = get_active_users_with_tags()
    usuarios_select_options = [{"id": int(u.id), "name": (u.name or "")} for u in usuarios]
    usuarios_name_by_id = {int(u.id): (u.name or "") for u in usuarios}

    list_cache_timeout = get_cache_timeout("INVENTARIO_LISTALL_CACHE_SECONDS", 30)
    listall_max_rows = int(current_app.config.get("INVENTARIO_LISTALL_MAX_ROWS", 300))
    list_cache_key_payload = {
        "sort": sort,
        "order": order,
        "tributacao": tributacao_filters,
        "encerramento": encerramento_filters,
        "status": status_filters,
        "tag": tag_filters,
        "search": search_term,
        "max_rows": listall_max_rows,
        "v": cache_version,
    }
    list_cache_key = (
        f"inventario:listall:{current_user.id}:"
        f"{json.dumps(list_cache_key_payload, sort_keys=True)}"
    )
    if show_all:
        cached_listall = cache.get(list_cache_key)
        if cached_listall is not None:
            return cached_listall.get("html", "")

    listall_token = None
    listall_total_rows = 0
    initial_batch_size = int(current_app.config.get("INVENTARIO_LISTALL_INITIAL_BATCH", 100))
    if show_all:
        # Guardrail para evitar resposta HTML gigante quando ha muitos registros.
        limited_ids_rows = query.with_entities(Empresa.id).limit(listall_max_rows + 1).all()
        ordered_empresa_ids = [int(row.id) for row in limited_ids_rows]
        if len(ordered_empresa_ids) > listall_max_rows:
            ordered_empresa_ids = ordered_empresa_ids[:listall_max_rows]
            flash(
                f"Listagem completa limitada aos primeiros {listall_max_rows} registros. "
                "Aplique filtros para reduzir o volume e carregar menos empresas por vez.",
                "warning",
            )

        listall_total_rows = len(ordered_empresa_ids)
        listall_token = uuid4().hex
        cache.set(
            f"inventario:listall:token:{listall_token}",
            {
                "empresa_ids": ordered_empresa_ids,
                "status_filters": status_filters,
                "user_id": current_user.id,
                "usuarios_name_by_id": usuarios_name_by_id,
            },
            timeout=300,
        )
        first_empresa_ids = ordered_empresa_ids[:initial_batch_size]
        empresas_map = (
            Empresa.query.filter(Empresa.id.in_(first_empresa_ids))
            .options(
                load_only(
                    Empresa.id,
                    Empresa.codigo_empresa,
                    Empresa.nome_empresa,
                    Empresa.tributacao,
                    Empresa.tipo_empresa,
                )
            )
            .all()
        )
        empresa_by_id = {empresa.id: empresa for empresa in empresas_map}
        empresas = [empresa_by_id[eid] for eid in first_empresa_ids if eid in empresa_by_id]
        total = listall_total_rows
        per_page = total if total > 0 else 1
        page = 1

        def _iter_pages(**_kwargs):
            return []

        pagination = type(
            "ListAllPagination",
            (),
            {
                "total": total,
                "page": page,
                "per_page": per_page,
                "pages": 1 if total > 0 else 0,
                "has_prev": False,
                "has_next": False,
                "prev_num": None,
                "next_num": None,
                "iter_pages": staticmethod(_iter_pages),
                "items": empresas,
            },
        )()
    else:
        per_page = 20
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        empresas = pagination.items
    # Otimização: Nunca carregar colunas pesadas de arquivos no carregamento inicial da lista.
    # O front-end carrega os metadados sob demanda quando o usuário clica no botão.
    items, file_counts_by_empresa = _build_inventario_items_for_empresas(
        empresas,
        status_filters,
        include_file_columns=False,
    )
    is_tadeu = (current_user.username or "").strip().lower().startswith("tadeu")

    # Buscar todos os usuários para o select de encerramento (otimizado com cache)
    dashboard_query_params = urlencode(
        [
            *[("tributacao", value) for value in tributacao_filters],
            *[("encerramento", value) for value in encerramento_filters],
            *[("status", value) for value in status_filters],
            *[("tag", value) for value in tag_filters],
            *([("q", search_term)] if search_term else []),
        ],
        doseq=True,
    )

    response = render_template(
        "empresas/inventario.html",
        items=items,
        file_counts_by_empresa=file_counts_by_empresa,
        pagination=pagination,
        status_choices=INVENTARIO_STATUS_CHOICES,
        sort=sort,
        order=order,
        tributacao_filters=tributacao_filters,
        allowed_tributacoes=allowed_tributacoes,
        encerramento_filters=encerramento_filters,
        tag_filters=tag_filters,
        allowed_tag_filters=allowed_tag_filters,
        status_filters=status_filters,
        search_term=search_term,
        show_all=show_all,
        all_param="1" if show_all else "0",
        usuarios=usuarios,
        usuarios_select_options=usuarios_select_options,
        usuarios_name_by_id=usuarios_name_by_id,
        dashboard_cards=dashboard_cards,
        is_admin=is_user_admin(current_user),
        is_tadeu=is_tadeu,
        dashboard_async=dashboard_async,
        dashboard_api_url=url_for("empresas.api_inventario_dashboard"),
        dashboard_query_params=dashboard_query_params,
        listall_token=listall_token,
        listall_initial_loaded=len(items) if show_all else 0,
        listall_total_rows=listall_total_rows if show_all else 0,
        listall_initial_batch=initial_batch_size if show_all else 0,
    )

    if show_all:
        cache.set(
            list_cache_key,
            {"html": response},
            timeout=list_cache_timeout,
        )

    return response


@empresas_bp.route("/api/inventario/dashboard", methods=["GET"])
@login_required
def api_inventario_dashboard():
    """Retorna cards de dashboard do inventario de forma assíncrona."""
    try:
        allowed_tributacoes = ["MEI", "Simples Nacional", "Lucro Presumido", "Lucro Real"]
        allowed_tag_filters = [value for value, _ in EMPRESA_TAG_CHOICES]

        search_term = (request.args.get("q") or "").strip()
        tributacao_filters = [
            value for value in request.args.getlist("tributacao") if value in allowed_tributacoes
        ]
        encerramento_filters = [
            value for value in request.args.getlist("encerramento") if value in ("true", "false")
        ]
        status_filters = [
            value for value in request.args.getlist("status") if value in INVENTARIO_STATUS_CHOICES
        ]
        tag_filters = [value for value in request.args.getlist("tag") if value in allowed_tag_filters]
        if not tag_filters:
            tag_filters = ["Matriz", "Filial"]

        base_query, inventario_joined = _build_inventario_base_query(
            search_term=search_term,
            tributacao_filters=tributacao_filters,
            encerramento_filters=encerramento_filters,
            tag_filters=tag_filters,
            allowed_tributacoes=allowed_tributacoes,
        )

        cache_timeout = get_cache_timeout("INVENTARIO_DASHBOARD_CACHE_SECONDS", 60)
        cache_version = _get_inventario_cache_version()
        cache_key_payload = {
            "tributacao": tributacao_filters,
            "encerramento": encerramento_filters,
            "status": status_filters,
            "tag": tag_filters,
            "search": search_term,
            "v": cache_version,
        }

        dashboard_cards = _get_or_set_inventario_dashboard_cards(
            base_query=base_query,
            inventario_joined=inventario_joined,
            status_filters=status_filters,
            allowed_tributacoes=allowed_tributacoes,
            cache_key_payload=cache_key_payload,
            cache_timeout=cache_timeout,
        )
        return jsonify({"success": True, "cards": dashboard_cards})
    except Exception as exc:
        current_app.logger.exception("Erro ao calcular dashboard do inventario: %s", exc)
        return jsonify({"success": False, "error": "Falha ao carregar dashboard"}), 500


@empresas_bp.route("/api/inventario/chunk", methods=["GET"])
@login_required
def api_inventario_chunk():
    token = (request.args.get("token") or "").strip()
    offset = request.args.get("offset", type=int) or 0
    limit = request.args.get("limit", type=int) or 100
    if not token:
        return jsonify({"success": False, "error": "Token ausente"}), 400
    if offset < 0:
        offset = 0
    limit = max(1, min(limit, 300))

    payload = cache.get(f"inventario:listall:token:{token}")
    if not payload:
        return jsonify({"success": False, "error": "Sessão expirada. Recarregue a página."}), 410
    if int(payload.get("user_id") or 0) != int(current_user.id):
        return jsonify({"success": False, "error": "Token inválido para este usuário."}), 403

    empresa_ids: list[int] = payload.get("empresa_ids") or []
    status_filters: list[str] = payload.get("status_filters") or []
    usuarios_name_by_id: dict[int, str] = payload.get("usuarios_name_by_id") or {}
    total = len(empresa_ids)
    chunk_cache_key = f"inventario:listall:chunk:{token}:{offset}:{limit}"
    cached_chunk = cache.get(chunk_cache_key)
    if cached_chunk is not None:
        return jsonify(cached_chunk)
    chunk_ids = empresa_ids[offset: offset + limit]

    if not chunk_ids:
        result = {"success": True, "html": "", "loaded": offset, "total": total, "has_more": False}
        cache.set(chunk_cache_key, result, timeout=120)
        return jsonify(result)

    empresas_map = (
        Empresa.query.filter(Empresa.id.in_(chunk_ids))
        .options(
            load_only(
                Empresa.id,
                Empresa.codigo_empresa,
                Empresa.nome_empresa,
                Empresa.tributacao,
                Empresa.tipo_empresa,
            )
        )
        .all()
    )
    empresa_by_id = {empresa.id: empresa for empresa in empresas_map}
    empresas = [empresa_by_id[eid] for eid in chunk_ids if eid in empresa_by_id]
    # Otimização: Nunca carregar colunas pesadas em chunks de listagem.
    items, file_counts_by_empresa = _build_inventario_items_for_empresas(
        empresas,
        status_filters,
        include_file_columns=False,
    )
    is_tadeu = (current_user.username or "").strip().lower().startswith("tadeu")

    if not usuarios_name_by_id:
        usuarios = get_active_users_with_tags()
        usuarios_name_by_id = {int(u.id): (u.name or "") for u in usuarios}
    status_choices = INVENTARIO_STATUS_CHOICES
    rows_html = render_template(
        "empresas/_inventario_rows.html",
        items=items,
        file_counts_by_empresa=file_counts_by_empresa,
        is_tadeu=is_tadeu,
        status_choices=status_choices,
        usuarios_name_by_id=usuarios_name_by_id,
    )
    new_loaded = min(offset + len(items), total)
    result = {
        "success": True,
        "html": rows_html,
        "loaded": new_loaded,
        "total": total,
        "has_more": new_loaded < total,
    }
    cache.set(chunk_cache_key, result, timeout=120)
    return jsonify(result)


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
            # Otimização: Adiar o carregamento de colunas JSON pesadas durante atualização de campos simples.
            inventario = (
                Inventario.query.filter_by(empresa_id=empresa_id)
                .options(
                    defer(Inventario.cfop_files),
                    defer(Inventario.cfop_consolidado_files),
                    defer(Inventario.cliente_files)
                )
                .first()
            )
            if not inventario:
                inventario = Inventario(empresa_id=empresa_id, encerramento_fiscal=False)
                db.session.add(inventario)
        previous_status = inventario.status if field == "status" else None

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

        status_changed_to_tadeu = (
            field == "status"
            and processed_value == "AGUARDANDO TADEU"
            and previous_status != "AGUARDANDO TADEU"
        )
        notify_cristiano = (
            field == "status"
            and processed_value == "LIBERADO PARA IMPORTAÇÃO"
            and previous_status != "LIBERADO PARA IMPORTAÇÃO"
        )
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

        if field == "status" and processed_value == "AGUARDANDO TADEU":
            _maybe_set_status_aguardando_tadeu(inventario)
            processed_value = inventario.status
            if inventario.status != "AGUARDANDO TADEU":
                status_changed_to_tadeu = False

        commit_started = track_commit_start()
        try:
            db.session.commit()
        finally:
            track_commit_end(commit_started)
        _invalidate_and_prewarm_inventario_caches()
        if status_changed_to_tadeu:
            current_app.logger.info(
                "Inventario: status alterado para AGUARDANDO TADEU; email sai pelo job diario das 17h"
            )
        if notify_cristiano:
            _notify_cristiano_liberado_importacao(empresa)

        # Retornar valor formatado se for campo monetário
        response_value = value
        if field in campos_monetarios and processed_value is not None:
            response_value = f"R$ {processed_value:,.2f}".replace(',', '_').replace('.', ',').replace('_', '.')

        return jsonify({'success': True, 'value': response_value, 'status': inventario.status})

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
    """Verifica se há arquivos VÁLIDOS na lista (suporta database e disk storage)."""
    entries = _coerce_file_entries(value)
    if not entries:
        return False

    # Verificar se há pelo menos um arquivo válido
    # Um arquivo válido deve ter 'filename' E ('file_data' codificado ou 'path' no disco) não vazios
    for entry in entries:
        if isinstance(entry, dict):
            filename = entry.get('filename', '').strip()
            file_data = entry.get('file_data', '').strip()
            path = entry.get('path', '').strip()
            if filename and (file_data or path):
                return True

    return False


def _get_inventario_file_flags(inventario):
    has_cfop = _has_file_entries(inventario.cfop_files) or bool(inventario.pdf_path)
    has_cfop_consolidado = _has_file_entries(inventario.cfop_consolidado_files)
    has_cliente = _has_file_entries(inventario.cliente_files) or bool(inventario.cliente_pdf_path)
    return has_cfop, has_cfop_consolidado, has_cliente


def _maybe_set_status_aguardando_tadeu(inventario):
    # Regra: deve ter CFOP CONSOLIDADO + ARQUIVO CLIENTE para mudar para AGUARDANDO TADEU
    has_cfop_consolidado = _has_file_entries(inventario.cfop_consolidado_files)
    has_cliente = _has_file_entries(inventario.cliente_files) or bool(inventario.cliente_pdf_path)

    if not (has_cfop_consolidado and has_cliente):
        # Só reverter para FALTA ARQUIVO se o status nunca foi definido
        # Se já está em AGUARDANDO TADEU, preservar (foi definido manualmente ou pelos uploads)
        if inventario.status in (None, "", "Selecione..."):
            inventario.status = "FALTA ARQUIVO"
            return True
        return False
    if inventario.status == "AGUARDANDO TADEU":
        return False
    inventario.status = "AGUARDANDO TADEU"
    return True


def _find_active_user_by_name(name: str) -> User | None:
    normalized = (name or "").strip()
    if not normalized:
        return None
    normalized_lower = normalized.lower()
    user = (
        User.query.filter(User.ativo.is_(True))
        .filter(sa.func.lower(User.name) == normalized_lower)
        .first()
    )
    if user:
        return user
    return (
        User.query.filter(User.ativo.is_(True), User.name.ilike(f"%{normalized}%"))
        .order_by(sa.func.length(User.name))
        .first()
    )


def _queue_inventario_email(
    *,
    recipient_name: str,
    subject: str,
    template_name: str,
    context: dict,
    use_async: bool = True,
    strict: bool = False,
) -> None:
    user = _find_active_user_by_name(recipient_name)
    if not user or not user.email:
        msg = "Inventario: usuario %s nao encontrado ou sem email para notificacao." % recipient_name
        current_app.logger.warning(msg)
        if strict:
            raise EmailDeliveryError(msg)
        return
    try:
        html_body = render_template(template_name, destinatario=user, **context)
    except Exception as exc:
        current_app.logger.exception(
            "Inventario: falha ao renderizar email para %s: %s",
            recipient_name,
            exc,
        )
        if strict:
            raise
        return
    try:
        if use_async:
            submit_io_task(
                send_email,
                subject=subject,
                html_body=html_body,
                recipients=[user.email],
            )
        else:
            send_email(
                subject=subject,
                html_body=html_body,
                recipients=[user.email],
            )
    except EmailDeliveryError as exc:
        current_app.logger.error(
            "Inventario: falha ao enviar email para %s: %s",
            recipient_name,
            exc,
        )
        if strict:
            raise


def _build_aguardando_tadeu_groups() -> list[dict]:
    allowed_tributacoes = ["MEI", "Simples Nacional", "Lucro Presumido", "Lucro Real"]
    grouped = {trib: [] for trib in allowed_tributacoes}
    outros: list[Empresa] = []
    empresas = (
        Empresa.query.join(Inventario, Inventario.empresa_id == Empresa.id)
        .options(joinedload(Empresa.inventario))
        .filter(Empresa.ativo.is_(True), Inventario.status == "AGUARDANDO TADEU")
        .order_by(Empresa.tributacao, Empresa.codigo_empresa)
        .all()
    )
    for empresa in empresas:
        trib = (empresa.tributacao or "").strip()
        if trib in grouped:
            grouped[trib].append(empresa)
        else:
            outros.append(empresa)
    groups: list[dict] = []
    for trib in allowed_tributacoes:
        if grouped[trib]:
            groups.append({"tributacao": trib, "empresas": grouped[trib]})
    if outros:
        groups.append({"tributacao": "Outros", "empresas": outros})
    return groups


def _tadeu_daily_state_file() -> Path:
    """Return path to state file used to avoid duplicate daily emails."""

    instance_dir = Path(current_app.instance_path)
    instance_dir.mkdir(parents=True, exist_ok=True)
    return instance_dir / "daily_tadeu_notification.json"


def _was_tadeu_notified_today(today: date) -> bool:
    state_path = _tadeu_daily_state_file()
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return data.get("date") == today.isoformat()


def _mark_tadeu_notified(today: date, total: int, groups_count: int) -> None:
    state_path = _tadeu_daily_state_file()
    payload = {
        "date": today.isoformat(),
        "total_empresas": total,
        "groups": groups_count,
        "marked_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        state_path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as exc:
        current_app.logger.warning("Falha ao gravar estado de notificacao Tadeu: %s", exc)


def _notify_tadeu_aguardando_inventario() -> None:
    groups = _build_aguardando_tadeu_groups()
    if not groups:
        return
    inventario_url = url_for("empresas.inventario", _external=True)
    for recipient_name in ("Tadeu", "Cristiano"):
        _queue_inventario_email(
            recipient_name=recipient_name,
            subject="[Inventario] Empresas aguardando Tadeu",
            template_name="emails/inventario_tadeu.html",
            context={
                "grupos": groups,
                "inventario_url": inventario_url,
            },
        )


def _notify_cassio_sem_cliente(empresa: Empresa) -> None:
    """Cria notificação no portal para Cassio quando CFOP é adicionado sem arquivo do cliente."""
    from app.models.tables import TaskNotification, NotificationType

    # Buscar usuário Cassio
    cassio = _find_active_user_by_name("Cassio")
    if not cassio:
        current_app.logger.warning("Inventario: usuário Cassio não encontrado para notificação")
        return

    # Criar mensagem da notificação
    message = f"Fechamento Fiscal finalizado sem arquivo do inventário: {empresa.codigo_empresa} - {empresa.nome_empresa}"
    if len(message) > 255:
        message = message[:252] + "..."

    # Criar notificação no portal
    notification = TaskNotification(
        user_id=cassio.id,
        task_id=None,
        announcement_id=None,
        type=NotificationType.INVENTARIO.value,
        message=message,
    )

    try:
        db.session.add(notification)
        db.session.commit()
        current_app.logger.info(
            "Notificação de inventário criada para Cassio: empresa %s",
            empresa.codigo_empresa,
        )
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(
            "Erro ao criar notificação para Cassio: %s",
            exc,
        )


def _notify_cristiano_liberado_importacao(empresa: Empresa) -> None:
    """Cria notificação no portal para Cristiano quando o inventário é liberado para importação."""
    from app.models.tables import TaskNotification, NotificationType

    cristiano = _find_active_user_by_name("Cristiano")
    if not cristiano:
        current_app.logger.warning("Inventario: usuário Cristiano não encontrado para notificação")
        return

    message = f"Inventário liberado para importação: {empresa.codigo_empresa} - {empresa.nome_empresa}"
    if len(message) > 255:
        message = message[:252] + "..."

    notification = TaskNotification(
        user_id=cristiano.id,
        task_id=None,
        announcement_id=None,
        type=NotificationType.INVENTARIO.value,
        message=message,
    )

    try:
        db.session.add(notification)
        db.session.commit()
        current_app.logger.info(
            "Notificação de inventário criada para Cristiano: empresa %s",
            empresa.codigo_empresa,
        )
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(
            "Erro ao criar notificação para Cristiano: %s",
            exc,
        )


def send_daily_tadeu_notification(
    recipients: Iterable[str] | None = None,
    force: bool = False,
    use_async: bool = True,
) -> None:
    """
    Job agendado: envia notificação diária para Tadeu com todas as empresas aguardando.
    Executado diariamente às 17h pelo scheduler.
    """
    current_app.logger.info("=" * 50)
    current_app.logger.info("🚀 EXECUTANDO JOB DIÁRIO DE NOTIFICAÇÃO PARA TADEU")
    current_app.logger.info("=" * 50)

    hoje = datetime.now(get_calendar_timezone()).date()
    if not force and _was_tadeu_notified_today(hoje):
        current_app.logger.info("Notificação de hoje já foi enviada; ignorando novo envio.")
        return

    # Buscar TODAS as empresas com status AGUARDANDO TADEU (sem filtro de data)
    groups = _build_aguardando_tadeu_groups()

    if not groups:
        current_app.logger.info("Nenhuma empresa aguardando Tadeu")
        return

    # Contar total de empresas
    total_empresas = sum(len(group["empresas"]) for group in groups)

    # Enviar notificação
    try:
        inventario_url = url_for("empresas.inventario", _external=True)
    except RuntimeError:
        # Fallback quando executado fora de requisição (ex: script standalone)
        inventario_url = "http://localhost:5000/inventario"
    if recipients:
        recipient_list = tuple(recipient.strip() for recipient in recipients if recipient and recipient.strip())
    else:
        recipient_list = ("Tadeu", "Cristiano")
    for recipient_name in recipient_list:
        _queue_inventario_email(
            recipient_name=recipient_name,
            subject="[Inventario] Empresas aguardando Tadeu",
            template_name="emails/inventario_tadeu.html",
            context={
                "grupos": groups,
                "inventario_url": inventario_url,
            },
            use_async=use_async,
            strict=not use_async,
        )

    current_app.logger.info("=" * 50)
    current_app.logger.info(
        "✅ NOTIFICAÇÃO ENVIADA: %d empresas em %d grupos",
        total_empresas,
        len(groups),
    )
    current_app.logger.info("=" * 50)
    _mark_tadeu_notified(hoje, total_empresas, len(groups))


# ============================================
# INVENTARIO COLUMN PREFERENCES API
# ============================================

@empresas_bp.route("/api/inventario/preferences", methods=["GET"])
@login_required
def api_inventario_get_preferences():
    """Retorna as preferências de colunas do usuário."""
    from app.models.tables import INVENTARIO_DEFAULT_COLUMNS

    user_prefs = current_user.preferences or {}
    table_prefs = user_prefs.get('inventario_table', {})
    columns = table_prefs.get('columns', {})

    # Criar defaults simplificados
    defaults = {
        col_id: {
            'visible': col_config['visible'],
            'order': col_config['order'],
            'width': col_config['width']
        }
        for col_id, col_config in INVENTARIO_DEFAULT_COLUMNS.items()
    }

    # Merge user preferences com defaults
    merged = defaults.copy()
    for col_id, prefs in columns.items():
        if col_id in merged:
            merged[col_id].update(prefs)

    return jsonify({
        'success': True,
        'preferences': merged,
        'defaults': defaults
    })


@empresas_bp.route("/api/inventario/preferences", methods=["POST"])
@login_required
def api_inventario_save_preferences():
    """Salva as preferências de colunas do usuário."""
    try:
        data = request.get_json()
        preferences = data.get('preferences', {})

        if not isinstance(preferences, dict):
            return jsonify({'success': False, 'error': 'Formato inválido'}), 400

        # Get or create user preferences
        user_prefs = current_user.preferences or {}

        if 'inventario_table' not in user_prefs:
            user_prefs['inventario_table'] = {}

        user_prefs['inventario_table']['columns'] = preferences
        user_prefs['inventario_table']['version'] = 1

        # Usar flag para forçar atualização JSON
        flag_modified(current_user, 'preferences')
        current_user.preferences = user_prefs
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Preferências salvas com sucesso'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@empresas_bp.route("/api/inventario/preferences/reset", methods=["POST"])
@login_required
def api_inventario_reset_preferences():
    """Reseta preferências para o padrão."""
    try:
        from app.models.tables import INVENTARIO_DEFAULT_COLUMNS

        # Limpar preferências do inventario
        user_prefs = current_user.preferences or {}
        if 'inventario_table' in user_prefs:
            del user_prefs['inventario_table']

        flag_modified(current_user, 'preferences')
        current_user.preferences = user_prefs if user_prefs else None
        db.session.commit()

        # Retornar defaults
        defaults = {
            col_id: {
                'visible': col_config['visible'],
                'order': col_config['order'],
                'width': col_config['width']
            }
            for col_id, col_config in INVENTARIO_DEFAULT_COLUMNS.items()
        }

        return jsonify({
            'success': True,
            'message': 'Preferências resetadas',
            'defaults': defaults
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@empresas_bp.route("/inventario/test-email")
@login_required
def inventario_test_email_page():
    """Página para testar envio de emails do inventário."""
    return render_template("empresas/inventario_test_email.html")


@empresas_bp.route("/api/inventario/test-email-cristiano", methods=["POST"])
@login_required
@csrf.exempt
def api_test_email_cristiano():
    """Dispara email de teste imediato para Tadeu e Cristiano."""
    from app.utils.mailer import send_email

    try:
        current_app.logger.info("🔥 Disparando email de teste para Tadeu e Cristiano")

        # Verificar se há empresas aguardando
        groups = _build_aguardando_tadeu_groups()
        if not groups:
            current_app.logger.warning("Nenhuma empresa aguardando Tadeu")
            return jsonify({'success': False, 'error': 'Nenhuma empresa aguardando'}), 400

        total_empresas = sum(len(group["empresas"]) for group in groups)
        current_app.logger.info("Encontradas %d empresas aguardando em %d grupos", total_empresas, len(groups))

        inventario_url = url_for("empresas.inventario", _external=True)
        recipients_info = []

        # Enviar para Tadeu e Cristiano
        for recipient_name in ("Tadeu", "Cristiano"):
            user = _find_active_user_by_name(recipient_name)
            if not user:
                current_app.logger.warning("Usuário %s não encontrado", recipient_name)
                continue

            if not user.email:
                current_app.logger.warning("Usuário %s sem email cadastrado", recipient_name)
                continue

            current_app.logger.info("Enviando email para %s (%s)", recipient_name, user.email)

            # Renderizar o template do email
            html_body = render_template(
                "emails/inventario_tadeu.html",
                destinatario=user,
                grupos=groups,
                inventario_url=inventario_url
            )

            # Enviar email SÍNCRONO (direto, sem fila) para ver erros
            send_email(
                subject="[Inventario] Teste - Empresas aguardando Tadeu",
                html_body=html_body,
                recipients=[user.email]
            )

            recipients_info.append(f"{recipient_name} ({user.email})")
            current_app.logger.info("Email enviado com sucesso para %s!", recipient_name)

        if not recipients_info:
            return jsonify({'success': False, 'error': 'Nenhum destinatário encontrado'}), 404

        return jsonify({
            'success': True,
            'message': f'Emails enviados para: {", ".join(recipients_info)}',
            'empresas': total_empresas,
            'grupos': len(groups)
        }), 200
    except Exception as e:
        current_app.logger.exception("Erro ao enviar email de teste: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


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
        had_cfop = _has_file_entries(inventario.cfop_files) or bool(inventario.pdf_path)
        has_cliente = _has_file_entries(inventario.cliente_files) or bool(inventario.cliente_pdf_path)

        # Salvar no disco ao invés de base64 no banco
        file_info = _save_inventario_disk_file(file, empresa_id, "cfop")

        # Atualizar array de arquivos
        cfop_files = inventario.cfop_files or []
        cfop_files.append(file_info)
        inventario.cfop_files = cfop_files
        status_changed = _maybe_set_status_aguardando_tadeu(inventario)
        notify_cassio = (not had_cfop) and (not has_cliente)
        # Marcar explicitamente que o JSON foi modificado
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(inventario, 'cfop_files')

        db.session.commit()
        if status_changed:
            current_app.logger.info(
                "Inventario: status AGUARDANDO TADEU atualizado por upload de CFOP; email aguardara job diario das 17h"
            )
        elif notify_cassio:
            _notify_cassio_sem_cliente(empresa)

        return jsonify({
            'success': True,
            'filename': filename,
            'uploaded_at': file_info['uploaded_at'],
            'storage': 'database',
            'status': inventario.status,
            'file_index': len(cfop_files) - 1  # índice do arquivo no array
        })

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erro ao fazer upload do PDF: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


@empresas_bp.route("/api/inventario/upload-cfop-consolidado/<int:empresa_id>", methods=["POST"])
@login_required
def api_inventario_upload_cfop_consolidado(empresa_id):
    """Upload de arquivo PDF para o CFOP consolidado (armazenado no banco)."""
    import base64
    from werkzeug.utils import secure_filename

    try:
        empresa = Empresa.query.get_or_404(empresa_id)

        if 'pdf' not in request.files:
            return jsonify({'success': False, 'error': 'Nenhum arquivo enviado'}), 400

        file = request.files['pdf']

        if file.filename == '':
            return jsonify({'success': False, 'error': 'Nenhum arquivo selecionado'}), 400

        if not file.filename.lower().endswith('.pdf'):
            return jsonify({'success': False, 'error': 'Apenas arquivos PDF são permitidos'}), 400

        inventario = Inventario.query.filter_by(empresa_id=empresa_id).first()
        if not inventario:
            inventario = Inventario(empresa_id=empresa_id, encerramento_fiscal=False)
            db.session.add(inventario)

        had_cfop = _has_file_entries(inventario.cfop_consolidado_files)
        has_cliente = _has_file_entries(inventario.cliente_files) or bool(inventario.cliente_pdf_path)

        # Salvar no disco ao invés de base64 no banco
        file_info = _save_inventario_disk_file(file, empresa_id, "cfop-consolidado")

        cfop_consolidado_files = inventario.cfop_consolidado_files or []
        cfop_consolidado_files.append(file_info)
        inventario.cfop_consolidado_files = cfop_consolidado_files
        status_changed = _maybe_set_status_aguardando_tadeu(inventario)
        notify_cassio = (not had_cfop) and (not has_cliente)

        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(inventario, 'cfop_consolidado_files')

        db.session.commit()
        if status_changed:
            current_app.logger.info(
                "Inventario: status AGUARDANDO TADEU atualizado por upload de CFOP consolidado; "
                "email aguardara job diario das 17h"
            )
        elif notify_cassio:
            _notify_cassio_sem_cliente(empresa)

        return jsonify({
            'success': True,
            'filename': filename,
            'uploaded_at': file_info['uploaded_at'],
            'storage': 'database',
            'status': inventario.status,
            'file_index': len(cfop_consolidado_files) - 1
        })

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erro ao fazer upload do CFOP consolidado: %s", e)
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
        _maybe_set_status_aguardando_tadeu(inventario)

        db.session.commit()

        return jsonify({'success': True, 'status': inventario.status})

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

        # Salvar no disco ao invés de base64 no banco
        file_info = _save_inventario_disk_file(file, empresa_id, "cliente")

        # Atualizar array de arquivos
        cliente_files = inventario.cliente_files or []
        cliente_files.append(file_info)
        inventario.cliente_files = cliente_files
        status_changed = _maybe_set_status_aguardando_tadeu(inventario)
        # Marcar explicitamente que o JSON foi modificado
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(inventario, 'cliente_files')

        db.session.commit()
        if status_changed:
            current_app.logger.info(
                "Inventario: status AGUARDANDO TADEU atualizado por arquivo do cliente; email aguardara job diario das 17h"
            )

        return jsonify({
            'success': True,
            'filename': filename,
            'uploaded_at': file_info['uploaded_at'],
            'storage': 'database',
            'status': inventario.status,
            'file_index': len(cliente_files) - 1  # índice do arquivo no array
        })

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erro ao fazer upload do arquivo do cliente: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


@empresas_bp.route("/api/inventario/file/<int:empresa_id>/<file_type>/<int:file_index>", methods=["GET"])
@login_required
def api_inventario_get_file(empresa_id, file_type, file_index):
    """Serve arquivo do inventário armazenado no banco de dados ou disco."""
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
        elif file_type == 'cfop-consolidado':
            files_array = inventario.cfop_consolidado_files or []
        elif file_type == 'cliente':
            files_array = inventario.cliente_files or []
        else:
            return jsonify({'success': False, 'error': 'Tipo de arquivo inválido'}), 400

        # Verificar se o índice é válido
        if file_index < 0 or file_index >= len(files_array):
            return jsonify({'success': False, 'error': 'Arquivo não encontrado'}), 404

        file_info = files_array[file_index]
        storage = file_info.get("storage", "database")
        filename = file_info.get('filename', 'arquivo')
        mime_type = file_info.get('mime_type', 'application/octet-stream')

        if storage == "disk":
            relative_path = file_info.get("path")
            if not relative_path:
                return jsonify({'success': False, 'error': 'Caminho não encontrado'}), 404
            absolute_path = os.path.join(current_app.root_path, "static", relative_path)
            if not os.path.exists(absolute_path):
                return jsonify({'success': False, 'error': 'Arquivo físico não encontrado'}), 404
            return send_file(absolute_path, mimetype=mime_type, as_attachment=False, download_name=filename)

        # Fallback: Database (Legacy)
        if 'file_data' not in file_info:
            return jsonify({'success': False, 'error': 'Dados do arquivo não encontrados'}), 404

        file_data = base64.b64decode(file_info['file_data'])
        return send_file(
            io.BytesIO(file_data),
            mimetype=mime_type,
            as_attachment=False,
            download_name=filename
        )

    except Exception as e:
        current_app.logger.exception("Erro ao servir arquivo do inventário: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


def _save_inventario_disk_file(uploaded_file, empresa_id: int, subdir_name: str) -> dict:
    """Salva arquivo de inventário no disco e retorna metadados."""
    from werkzeug.utils import secure_filename
    from mimetypes import guess_type

    original_name = secure_filename(uploaded_file.filename or "arquivo.pdf")
    extension = os.path.splitext(original_name)[1].lower()
    unique_name = f"{uuid4().hex}{extension}"

    # Caminho relativo: uploads/inventario/<empresa_id>/<subdir>/<uuid>.<ext>
    relative_dir = os.path.join(INVENTARIO_UPLOAD_SUBDIR, str(empresa_id), subdir_name).replace("\\", "/")
    absolute_dir = os.path.join(current_app.root_path, "static", relative_dir)
    os.makedirs(absolute_dir, exist_ok=True)

    absolute_path = os.path.join(absolute_dir, unique_name)
    uploaded_file.save(absolute_path)

    relative_path = os.path.join(relative_dir, unique_name).replace("\\", "/")
    mime_type = uploaded_file.mimetype or guess_type(original_name)[0] or "application/octet-stream"

    return {
        "filename": original_name,
        "path": relative_path,
        "uploaded_at": datetime.now().isoformat(),
        "mime_type": mime_type,
        "storage": "disk"
    }


def _serialize_inventario_file_metadata(files_array: list[dict]) -> list[dict]:
    """Retorna apenas metadados dos anexos, sem o campo pesado file_data."""
    items: list[dict] = []
    for index, file_info in enumerate(files_array or []):
        if not isinstance(file_info, dict):
            continue
        items.append(
            {
                "index": index,
                "filename": file_info.get("filename") or "arquivo",
                "uploaded_at": file_info.get("uploaded_at"),
                "mime_type": file_info.get("mime_type", "application/octet-stream"),
                "storage": file_info.get("storage", "database"),
            }
        )
    return items


@empresas_bp.route("/api/inventario/files/<int:empresa_id>", methods=["GET"])
@login_required
def api_inventario_list_files_metadata(empresa_id):
    """Retorna metadados de anexos por empresa para carregamento sob demanda."""
    try:
        inventario = Inventario.query.filter_by(empresa_id=empresa_id).first()
        if not inventario:
            return jsonify(
                {
                    "success": True,
                    "cfop": [],
                    "cfop_consolidado": [],
                    "cliente": [],
                }
            )

        return jsonify(
            {
                "success": True,
                "cfop": _serialize_inventario_file_metadata(inventario.cfop_files or []),
                "cfop_consolidado": _serialize_inventario_file_metadata(
                    inventario.cfop_consolidado_files or []
                ),
                "cliente": _serialize_inventario_file_metadata(inventario.cliente_files or []),
            }
        )
    except Exception as exc:
        current_app.logger.exception("Erro ao listar metadados dos arquivos do inventario: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


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
        _maybe_set_status_aguardando_tadeu(inventario)

        db.session.commit()

        return jsonify({'success': True, 'status': inventario.status})

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
            return jsonify({'success': False, 'error': 'índice do arquivo não fornecido'}), 400

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
        _maybe_set_status_aguardando_tadeu(inventario)
        db.session.commit()

        return jsonify({'success': True, 'status': inventario.status}), 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erro ao deletar arquivo CFOP: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


@empresas_bp.route("/api/inventario/delete-cfop-consolidado-file/<int:empresa_id>", methods=["POST"])
@login_required
def api_inventario_delete_cfop_consolidado_file(empresa_id):
    """Remove um arquivo específico do CFOP consolidado (armazenado no banco de dados)."""
    try:
        data = request.get_json()
        file_index = data.get('file_index')

        if file_index is None:
            return jsonify({'success': False, 'error': 'índice do arquivo não fornecido'}), 400

        inventario = Inventario.query.filter_by(empresa_id=empresa_id).first()

        if not inventario or not inventario.cfop_consolidado_files:
            return jsonify({'success': False, 'error': 'Arquivo não encontrado'}), 404

        cfop_consolidado_files = inventario.cfop_consolidado_files or []
        if file_index < 0 or file_index >= len(cfop_consolidado_files):
            return jsonify({'success': False, 'error': 'Arquivo não encontrado'}), 404

        cfop_consolidado_files.pop(file_index)
        inventario.cfop_consolidado_files = cfop_consolidado_files

        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(inventario, 'cfop_consolidado_files')
        _maybe_set_status_aguardando_tadeu(inventario)
        db.session.commit()

        return jsonify({'success': True, 'status': inventario.status}), 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erro ao deletar arquivo CFOP consolidado: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


@empresas_bp.route("/api/inventario/move-cfop-to-consolidado/<int:empresa_id>", methods=["POST"])
@login_required
def api_inventario_move_cfop_to_consolidado(empresa_id):
    """Move todos os arquivos de CFOP para CFOP consolidado."""
    try:
        inventario = Inventario.query.filter_by(empresa_id=empresa_id).first()
        if not inventario:
            return jsonify({'success': False, 'error': 'Inventário não encontrado'}), 404

        cfop_files = _coerce_file_entries(inventario.cfop_files)
        if not cfop_files:
            return jsonify({'success': False, 'error': 'Nenhum arquivo CFOP para mover'}), 400

        cfop_consolidado_files = _coerce_file_entries(inventario.cfop_consolidado_files)
        cfop_consolidado_files.extend(cfop_files)

        inventario.cfop_consolidado_files = cfop_consolidado_files
        inventario.cfop_files = []
        status_changed = _maybe_set_status_aguardando_tadeu(inventario)

        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(inventario, 'cfop_consolidado_files')
        flag_modified(inventario, 'cfop_files')

        db.session.commit()

        return jsonify({
            'success': True,
            'moved_count': len(cfop_files),
            'status': inventario.status,
            'cfop_consolidado_files': cfop_consolidado_files,
        }), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erro ao mover CFOP para consolidado: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


@empresas_bp.route("/api/inventario/delete-cliente-file-v2/<int:empresa_id>", methods=["POST"])
@login_required
def api_inventario_delete_cliente_file_v2(empresa_id):
    """Remove um arquivo específico do cliente (armazenado no banco de dados)."""
    try:
        data = request.get_json()
        file_index = data.get('file_index')

        if file_index is None:
            return jsonify({'success': False, 'error': 'índice do arquivo não fornecido'}), 400

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
        _maybe_set_status_aguardando_tadeu(inventario)
        db.session.commit()

        return jsonify({'success': True}), 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erro ao deletar arquivo do cliente: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500
