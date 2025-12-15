"""
Blueprint para gestao de consultorias e setores.

Este modulo contem rotas para CRUD de consultorias e setores
utilizados na categorizacao de empresas, alem de inclusoes.

Rotas:
    - GET/POST /consultorias: Lista e cria consultorias
    - GET/POST /consultorias/cadastro: Cadastro legacy
    - GET/POST /consultorias/editar/<id>: Edita consultoria
    - GET/POST /consultorias/setores: Lista e cria setores
    - GET/POST /consultorias/setores/cadastro: Cadastro setor legacy
    - GET/POST /consultorias/setores/editar/<id>: Edita setor
    - GET /consultorias/relatorios: Relatorios de inclusoes
    - GET /consultorias/inclusoes: Lista inclusoes
    - GET/POST /consultorias/inclusoes/nova: Nova inclusao
    - GET /consultorias/inclusoes/<codigo>: Visualiza inclusao
    - GET/POST /consultorias/inclusoes/<codigo>/editar: Edita inclusao

Dependencias:
    - models: Consultoria, Setor, Inclusao, User
    - forms: ConsultoriaForm, SetorForm

Autor: Refatoracao automatizada
Data: 2024
"""

from datetime import datetime

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import String, cast, or_

from app import cache, db
from app.forms import ConsultoriaForm, SetorForm
from app.controllers.routes._base import decode_id
from app.utils.security import sanitize_html
from app.controllers.routes._decorators import admin_required, meeting_only_access_check
from app.models.tables import Consultoria, Setor, Inclusao, User


# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

consultorias_bp = Blueprint('consultorias', __name__)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_cache_timeout(config_key: str, default: int) -> int:
    """Obtem timeout de cache da configuracao ou usa valor padrao."""
    from flask import current_app
    return current_app.config.get(config_key, default)


@cache.memoize(timeout=300)
def _get_consultorias_catalog() -> list[Consultoria]:
    """Catalogo cacheado de consultorias ordenadas por nome."""
    return Consultoria.query.order_by(Consultoria.nome).all()


def _invalidate_consultorias_cache() -> None:
    """Limpa cache do catalogo de consultorias."""
    cache.delete_memoized(_get_consultorias_catalog)


@cache.memoize(timeout=300)
def _get_setores_catalog() -> list[Setor]:
    """Catalogo cacheado de setores ordenados por nome."""
    return Setor.query.order_by(Setor.nome).all()


def _invalidate_setores_cache() -> None:
    """Limpa cache do catalogo de setores."""
    cache.delete_memoized(_get_setores_catalog)


def _configure_consultoria_form(form: ConsultoriaForm) -> ConsultoriaForm:
    """Configura o formulario de consultoria com placeholder de senha."""
    render_kw = form.senha.render_kw or {}
    render_kw["placeholder"] = "••••••••"
    form.senha.render_kw = render_kw
    return form


# =============================================================================
# ROTAS DE CONSULTORIAS
# =============================================================================

@consultorias_bp.route("/consultorias", methods=["GET", "POST"])
@login_required
@meeting_only_access_check
def consultorias():
    """Lista consultorias registradas e gerencia via modal."""
    consultoria_form = _configure_consultoria_form(
        ConsultoriaForm(prefix="consultoria")
    )
    consultorias_list = _get_consultorias_catalog()
    open_consultoria_modal = request.args.get("open_consultoria_modal") in (
        "1", "true", "True"
    )
    editing_consultoria: Consultoria | None = None

    # GET: verificar se esta editando
    if request.method == "GET":
        edit_id_raw = request.args.get("edit_consultoria_id")
        if edit_id_raw:
            try:
                edit_id = int(edit_id_raw)
            except (TypeError, ValueError):
                abort(404)
            editing_consultoria = Consultoria.query.get_or_404(edit_id)
            consultoria_form = _configure_consultoria_form(
                ConsultoriaForm(prefix="consultoria", obj=editing_consultoria)
            )
            open_consultoria_modal = True

    # POST: processar formulario
    if request.method == "POST":
        form_name = request.form.get("form_name")

        if form_name == "consultoria_create":
            open_consultoria_modal = True

            if consultoria_form.validate_on_submit():
                nome = (consultoria_form.nome.data or "").strip()
                usuario = (consultoria_form.usuario.data or "").strip()
                senha = (consultoria_form.senha.data or "").strip()
                consultoria_form.nome.data = nome
                consultoria_form.usuario.data = usuario
                consultoria_form.senha.data = senha

                duplicate = (
                    Consultoria.query.filter(
                        db.func.lower(Consultoria.nome) == nome.lower()
                    ).first()
                    if nome else None
                )

                if duplicate:
                    consultoria_form.nome.errors.append(
                        "Já existe uma consultoria com esse nome."
                    )
                    flash("Já existe uma consultoria com esse nome.", "warning")
                else:
                    consultoria = Consultoria(
                        nome=nome, usuario=usuario, senha=senha
                    )
                    db.session.add(consultoria)
                    db.session.commit()
                    _invalidate_consultorias_cache()
                    flash("Consultoria registrada com sucesso.", "success")
                    return redirect(url_for("consultorias.consultorias"))

        elif form_name == "consultoria_update":
            open_consultoria_modal = True

            consultoria_id_raw = request.form.get("consultoria_id")
            try:
                consultoria_id = int(consultoria_id_raw)
            except (TypeError, ValueError):
                abort(400)

            editing_consultoria = Consultoria.query.get_or_404(consultoria_id)

            if consultoria_form.validate_on_submit():
                nome = (consultoria_form.nome.data or "").strip()
                usuario = (consultoria_form.usuario.data or "").strip()
                senha = (consultoria_form.senha.data or "").strip()
                consultoria_form.nome.data = nome
                consultoria_form.usuario.data = usuario
                consultoria_form.senha.data = senha

                duplicate = (
                    Consultoria.query.filter(
                        db.func.lower(Consultoria.nome) == nome.lower(),
                        Consultoria.id != editing_consultoria.id,
                    ).first()
                    if nome else None
                )

                if duplicate:
                    consultoria_form.nome.errors.append(
                        "Já existe uma consultoria com esse nome."
                    )
                    flash("Já existe uma consultoria com esse nome.", "warning")
                else:
                    editing_consultoria.nome = nome
                    editing_consultoria.usuario = usuario
                    editing_consultoria.senha = senha
                    db.session.commit()
                    _invalidate_consultorias_cache()
                    flash("Consultoria atualizada com sucesso.", "success")
                    return redirect(url_for("consultorias.consultorias"))

    return render_template(
        "consultorias.html",
        consultorias=consultorias_list,
        consultoria_form=consultoria_form,
        open_consultoria_modal=open_consultoria_modal,
        editing_consultoria=editing_consultoria,
    )


@consultorias_bp.route("/consultorias/cadastro", methods=["GET", "POST"])
@login_required
def cadastro_consultoria():
    """Rota legacy preservada: redireciona para experiencia modal."""
    if request.method == "POST":
        form = ConsultoriaForm()
        if form.validate_on_submit():
            consultoria = Consultoria(
                nome=(form.nome.data or "").strip(),
                usuario=(form.usuario.data or "").strip(),
                senha=(form.senha.data or "").strip(),
            )
            db.session.add(consultoria)
            db.session.commit()
            _invalidate_consultorias_cache()
            flash("Consultoria registrada com sucesso.", "success")
            return redirect(url_for("consultorias.consultorias"))

        for errors in form.errors.values():
            for error in errors:
                flash(error, "warning")

    return redirect(url_for("consultorias.consultorias", open_consultoria_modal="1"))


@consultorias_bp.route("/consultorias/editar/<int:id>", methods=["GET", "POST"])
@login_required
def editar_consultoria_cadastro(id):
    """Endpoint legacy de edicao: redireciona para fluxo modal."""
    consultoria = Consultoria.query.get_or_404(id)

    if request.method == "POST":
        form = ConsultoriaForm()
        if form.validate_on_submit():
            nome = (form.nome.data or "").strip()
            usuario = (form.usuario.data or "").strip()
            senha = (form.senha.data or "").strip()

            duplicate = (
                Consultoria.query.filter(
                    db.func.lower(Consultoria.nome) == nome.lower(),
                    Consultoria.id != consultoria.id,
                ).first()
                if nome else None
            )

            if duplicate:
                flash("Já existe uma consultoria com esse nome.", "warning")
            else:
                consultoria.nome = nome
                consultoria.usuario = usuario
                consultoria.senha = senha
                db.session.commit()
                _invalidate_consultorias_cache()
                flash("Consultoria atualizada com sucesso.", "success")
                return redirect(url_for("consultorias.consultorias"))

        for errors in form.errors.values():
            for error in errors:
                flash(error, "warning")

    return redirect(
        url_for(
            "consultorias.consultorias",
            open_consultoria_modal="1",
            edit_consultoria_id=str(consultoria.id),
        )
    )


# =============================================================================
# ROTAS DE SETORES
# =============================================================================

@consultorias_bp.route("/consultorias/setores", methods=["GET", "POST"])
@login_required
def setores():
    """Lista setores registrados e gerencia via modal."""
    setor_form = SetorForm(prefix="setor")
    setor_form.submit.label.text = "Salvar"
    setores_list = _get_setores_catalog()
    open_setor_modal = request.args.get("open_setor_modal") in ("1", "true", "True")
    editing_setor: Setor | None = None

    # GET: verificar se esta editando
    if request.method == "GET":
        edit_id_raw = request.args.get("edit_setor_id")
        if edit_id_raw:
            try:
                edit_id = int(edit_id_raw)
            except (TypeError, ValueError):
                abort(404)
            editing_setor = Setor.query.get_or_404(edit_id)
            setor_form = SetorForm(prefix="setor", obj=editing_setor)
            setor_form.submit.label.text = "Salvar"
            open_setor_modal = True

    # POST: processar formulario
    if request.method == "POST":
        form_name = request.form.get("form_name")

        if form_name == "setor_create":
            open_setor_modal = True

            if setor_form.validate_on_submit():
                nome = (setor_form.nome.data or "").strip()
                setor_form.nome.data = nome

                duplicate = (
                    Setor.query.filter(
                        db.func.lower(Setor.nome) == nome.lower()
                    ).first()
                    if nome else None
                )

                if duplicate:
                    setor_form.nome.errors.append("Já existe um setor com esse nome.")
                    flash("Já existe um setor com esse nome.", "warning")
                else:
                    setor = Setor(nome=nome)
                    db.session.add(setor)
                    db.session.commit()
                    _invalidate_setores_cache()
                    flash("Setor registrado com sucesso.", "success")
                    return redirect(url_for("consultorias.setores"))

        elif form_name == "setor_update":
            open_setor_modal = True

            setor_id_raw = request.form.get("setor_id")
            try:
                setor_id = int(setor_id_raw)
            except (TypeError, ValueError):
                abort(400)

            editing_setor = Setor.query.get_or_404(setor_id)

            if setor_form.validate_on_submit():
                nome = (setor_form.nome.data or "").strip()
                setor_form.nome.data = nome

                duplicate = (
                    Setor.query.filter(
                        db.func.lower(Setor.nome) == nome.lower(),
                        Setor.id != editing_setor.id,
                    ).first()
                    if nome else None
                )

                if duplicate:
                    setor_form.nome.errors.append("Já existe um setor com esse nome.")
                    flash("Já existe um setor com esse nome.", "warning")
                else:
                    editing_setor.nome = nome
                    db.session.commit()
                    _invalidate_setores_cache()
                    flash("Setor atualizado com sucesso.", "success")
                    return redirect(url_for("consultorias.setores"))

    return render_template(
        "setores.html",
        setores=setores_list,
        setor_form=setor_form,
        open_setor_modal=open_setor_modal,
        editing_setor=editing_setor,
    )


@consultorias_bp.route("/consultorias/setores/cadastro", methods=["GET", "POST"])
@login_required
def cadastro_setor():
    """Rota legacy de criacao de setor: redireciona para UI modal."""
    if request.method == "POST":
        form = SetorForm()
        if form.validate_on_submit():
            nome = (form.nome.data or "").strip()
            duplicate = (
                Setor.query.filter(
                    db.func.lower(Setor.nome) == nome.lower()
                ).first()
                if nome else None
            )

            if duplicate:
                flash("Já existe um setor com esse nome.", "warning")
            else:
                setor = Setor(nome=nome)
                db.session.add(setor)
                db.session.commit()
                flash("Setor registrado com sucesso.", "success")
                return redirect(url_for("consultorias.setores"))

        for errors in form.errors.values():
            for error in errors:
                flash(error, "warning")

    return redirect(url_for("consultorias.setores", open_setor_modal="1"))


@consultorias_bp.route("/consultorias/setores/editar/<int:id>", methods=["GET", "POST"])
@login_required
def editar_setor(id):
    """Endpoint legacy de edicao de setor: redireciona para fluxo modal."""
    setor = Setor.query.get_or_404(id)

    if request.method == "POST":
        form = SetorForm()
        if form.validate_on_submit():
            nome = (form.nome.data or "").strip()
            duplicate = (
                Setor.query.filter(
                    db.func.lower(Setor.nome) == nome.lower(),
                    Setor.id != setor.id,
                ).first()
                if nome else None
            )

            if duplicate:
                flash("Já existe um setor com esse nome.", "warning")
            else:
                setor.nome = nome
                db.session.commit()
                flash("Setor atualizado com sucesso.", "success")
                return redirect(url_for("consultorias.setores"))

        for errors in form.errors.values():
            for error in errors:
                flash(error, "warning")

    return redirect(
        url_for(
            "consultorias.setores",
            open_setor_modal="1",
            edit_setor_id=str(setor.id),
        )
    )


# =============================================================================
# ROTAS DE RELATORIOS
# =============================================================================

@consultorias_bp.route("/consultorias/relatorios")
@admin_required
def relatorios_consultorias():
    """Exibe relatorios de inclusoes agrupados por consultoria, usuario e data."""
    inicio_raw = request.args.get("inicio")
    fim_raw = request.args.get("fim")
    query = Inclusao.query

    inicio = None
    if inicio_raw:
        try:
            inicio = datetime.strptime(inicio_raw, "%Y-%m-%d").date()
            query = query.filter(Inclusao.data >= inicio)
        except ValueError:
            inicio = None

    fim = None
    if fim_raw:
        try:
            fim = datetime.strptime(fim_raw, "%Y-%m-%d").date()
            query = query.filter(Inclusao.data <= fim)
        except ValueError:
            fim = None

    # Agrupamento por consultoria
    por_consultoria = (
        query.with_entities(Inclusao.consultoria, db.func.count(Inclusao.id))
        .group_by(Inclusao.consultoria)
        .order_by(db.func.count(Inclusao.id).desc())
        .all()
    )

    # Agrupamento por usuario
    por_usuario = (
        query.with_entities(Inclusao.usuario, db.func.count(Inclusao.id))
        .group_by(Inclusao.usuario)
        .order_by(db.func.count(Inclusao.id).desc())
        .all()
    )

    labels_consultoria = [c or "N/D" for c, _ in por_consultoria]
    counts_consultoria = [total for _, total in por_consultoria]
    chart_consultoria = {
        "type": "bar",
        "title": "Consultas por consultoria",
        "datasetLabel": "Total de consultas",
        "labels": labels_consultoria,
        "values": counts_consultoria,
        "xTitle": "Consultoria",
        "yTitle": "Total",
        "total": sum(counts_consultoria),
    }

    labels_usuario = [u or "N/D" for u, _ in por_usuario]
    counts_usuario = [total for _, total in por_usuario]
    chart_usuario = {
        "type": "bar",
        "title": "Consultas por usuário",
        "datasetLabel": "Total de consultas",
        "labels": labels_usuario,
        "values": counts_usuario,
        "xTitle": "Usuário",
        "yTitle": "Total",
        "total": sum(counts_usuario),
    }

    por_data = []
    if inicio or fim:
        por_data = (
            query.filter(Inclusao.data.isnot(None))
            .with_entities(Inclusao.data, db.func.count(Inclusao.id))
            .group_by(Inclusao.data)
            .order_by(Inclusao.data)
            .all()
        )

    return render_template(
        "relatorios_consultorias.html",
        chart_consultoria=chart_consultoria,
        chart_usuario=chart_usuario,
        por_data=por_data,
        inicio=inicio.strftime("%Y-%m-%d") if inicio else "",
        fim=fim.strftime("%Y-%m-%d") if fim else "",
    )


# =============================================================================
# ROTAS DE INCLUSOES
# =============================================================================

@consultorias_bp.route("/consultorias/inclusoes")
@login_required
def inclusoes():
    """Lista e pesquisa inclusoes de consultorias."""
    search_raw = request.args.get("q", "")
    page = request.args.get("page", 1, type=int)
    query = Inclusao.query

    if search_raw:
        like = f"%{search_raw}%"
        query = query.filter(
            or_(
                cast(Inclusao.data, String).ilike(like),
                Inclusao.usuario.ilike(like),
                Inclusao.assunto.ilike(like),
            )
        )

    pagination = query.order_by(Inclusao.data.desc()).paginate(page=page, per_page=50)

    return render_template(
        "inclusoes.html",
        inclusoes=pagination.items,
        pagination=pagination,
        search=search_raw,
    )


@consultorias_bp.route("/consultorias/inclusoes/nova", methods=["GET", "POST"])
@login_required
def nova_inclusao():
    """Renderiza e processa formulario de nova inclusao."""
    users = User.query.order_by(User.name).all()

    if request.method == "POST":
        user_id = request.form.get("usuario")
        user = User.query.get(int(user_id)) if user_id else None
        data_str = request.form.get("data")
        data = datetime.strptime(data_str, "%Y-%m-%d").date() if data_str else None

        inclusao = Inclusao(
            data=data,
            usuario=user.name if user else "",
            setor=request.form.get("setor"),
            consultoria=request.form.get("consultoria"),
            assunto=(request.form.get("assunto") or "").upper(),
            pergunta=sanitize_html(request.form.get("pergunta")),
            resposta=sanitize_html(request.form.get("resposta")),
        )
        db.session.add(inclusao)
        db.session.commit()
        flash("Consultoria registrada com sucesso.", "success")
        return redirect(url_for("consultorias.inclusoes"))

    return render_template(
        "nova_inclusao.html",
        users=users,
        setores=_get_setores_catalog(),
        consultorias=_get_consultorias_catalog(),
    )


@consultorias_bp.route("/consultorias/inclusoes/<codigo>")
@login_required
def visualizar_consultoria(codigo: str):
    """Exibe detalhes de uma inclusao especifica."""
    inclusao_id = decode_id(codigo, namespace="consultoria-inclusao")
    inclusao = Inclusao.query.get_or_404(inclusao_id)

    return render_template(
        "visualizar_consultoria.html",
        inclusao=inclusao,
        data_formatada=inclusao.data_formatada,
    )


@consultorias_bp.route("/consultorias/inclusoes/<codigo>/editar", methods=["GET", "POST"])
@login_required
def editar_consultoria(codigo: str):
    """Renderiza e processa edicao de uma inclusao."""
    inclusao_id = decode_id(codigo, namespace="consultoria-inclusao")
    inclusao = Inclusao.query.get_or_404(inclusao_id)
    users = User.query.order_by(User.name).all()

    if request.method == "POST":
        user_id = request.form.get("usuario")
        user = User.query.get(int(user_id)) if user_id else None
        data_str = request.form.get("data")

        inclusao.data = (
            datetime.strptime(data_str, "%Y-%m-%d").date() if data_str else None
        )
        inclusao.usuario = user.name if user else ""
        inclusao.setor = request.form.get("setor")
        inclusao.consultoria = request.form.get("consultoria")
        inclusao.assunto = (request.form.get("assunto") or "").upper()
        inclusao.pergunta = sanitize_html(request.form.get("pergunta"))
        inclusao.resposta = sanitize_html(request.form.get("resposta"))
        db.session.commit()
        flash("Consultoria atualizada com sucesso.", "success")

        next_url = request.form.get("next") or request.args.get("next")
        if next_url:
            return redirect(next_url)
        return redirect(url_for("consultorias.inclusoes"))

    return render_template(
        "nova_inclusao.html",
        users=users,
        setores=_get_setores_catalog(),
        consultorias=_get_consultorias_catalog(),
        inclusao=inclusao,
    )
