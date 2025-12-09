"""
Blueprint para central de acessos.

Este modulo contem rotas para a central de acessos (hub de links)
organizada por categorias (fiscal, contabil, pessoal).

Rotas:
    - GET /acessos: Hub principal com listagem e modais
    - POST /acessos/novo: Cria novo atalho (admin)
    - GET /acessos/<categoria>: Redirect para hub (compatibilidade)
    - POST /acessos/<categoria>/novo: Cria atalho em categoria (admin)
    - GET/POST /acessos/<id>/editar: Edita atalho (admin)
    - POST /acessos/<id>/excluir: Exclui atalho (admin)

Dependencias:
    - models: AccessLink
    - forms: AccessLinkForm

Autor: Refatoracao automatizada
Data: 2024
"""

from math import ceil

import sqlalchemy as sa
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import csrf, db
from app.controllers.routes._base import ACESSOS_CATEGORIES
from app.controllers.routes._decorators import meeting_only_access_check
from app.forms import AccessLinkForm
from app.models.tables import AccessLink


# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

acessos_bp = Blueprint('acessos', __name__)


# =============================================================================
# FUNCOES AUXILIARES
# =============================================================================

def _access_category_choices() -> list[tuple[str, str]]:
    """
    Retorna categorias disponiveis formatadas para WTForms.

    Returns:
        list: Lista de tuplas (slug, titulo) para SelectField.
    """
    return [
        (slug, data["title"])
        for slug, data in ACESSOS_CATEGORIES.items()
    ]


def _build_acessos_context(
    form: AccessLinkForm | None = None,
    *,
    open_modal: bool = False,
    page: int = 1,
) -> dict:
    """
    Constroi contexto para template da central de acessos.

    Implementa paginacao alfabetica com distribuicao em colunas.

    Args:
        form: Formulario de atalho (opcional)
        open_modal: Se True, modal deve abrir automaticamente
        page: Numero da pagina atual

    Returns:
        dict: Contexto para template com links, paginacao e formulario
    """
    per_page = 30
    columns_count = 3
    column_capacity = per_page // columns_count

    # Query ordenada alfabeticamente
    base_query = AccessLink.query.order_by(
        sa.func.lower(AccessLink.label), AccessLink.label
    )
    total_links = base_query.count()

    # Calcula paginacao
    if total_links:
        total_pages = ceil(total_links / per_page)
        page = max(1, min(page, total_pages))
        paginated_links = (
            base_query.offset((page - 1) * per_page).limit(per_page).all()
        )
    else:
        total_pages = 1
        page = 1
        paginated_links = []

    # Distribui links em colunas
    columns = [
        paginated_links[i * column_capacity : (i + 1) * column_capacity]
        for i in range(columns_count)
    ]
    while len(columns) < columns_count:
        columns.append([])

    # Dados de paginacao
    pagination = {
        "current_page": page,
        "total_pages": total_pages,
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "pages": list(range(1, total_pages + 1)),
        "per_page": per_page,
        "total_items": total_links,
    }

    return {
        "form": form,
        "open_modal": open_modal,
        "link_columns": columns,
        "pagination": pagination,
        "total_links": total_links,
        "per_page": per_page,
    }


def _handle_access_shortcut_submission(form: AccessLinkForm):
    """
    Persiste atalho se valido ou re-renderiza com erros.

    Args:
        form: Formulario preenchido

    Returns:
        Response: Redirect para listagem ou re-render com erros
    """
    if form.validate_on_submit():
        novo_link = AccessLink(
            category=form.category.data,
            label=form.label.data.strip(),
            url=form.url.data.strip(),
            description=(form.description.data or "").strip() or None,
            created_by=current_user,
        )
        db.session.add(novo_link)
        db.session.commit()
        flash("Novo atalho criado com sucesso!", "success")
        return redirect(url_for("acessos.acessos"))

    context = _build_acessos_context(form=form, open_modal=True)
    return render_template("acessos.html", **context)


# =============================================================================
# ROTAS
# =============================================================================

@acessos_bp.route("/acessos")
@login_required
@meeting_only_access_check
def acessos():
    """
    Exibe hub de acessos com categorias e atalhos salvos.

    Query params:
        modal: Tipo de modal a abrir (novo, editar)
        category: Categoria pre-selecionada para novo atalho
        link_id: ID do link para edicao
        page: Numero da pagina

    Returns:
        200: Pagina HTML com hub de acessos
    """
    modal_type = request.args.get("modal")
    open_modal = modal_type in ("novo", "editar")
    preselected_category = request.args.get("category")
    editing_link_id = request.args.get("link_id", type=int)

    # Paginacao
    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1

    form: AccessLinkForm | None = None
    editing_link = None

    # Configura formulario para admins
    if current_user.role == "admin":
        form = AccessLinkForm()
        form.category.choices = _access_category_choices()

        # Se esta editando, preenche formulario
        if modal_type == "editar" and editing_link_id:
            editing_link = AccessLink.query.get_or_404(editing_link_id)
            form.category.data = editing_link.category
            form.label.data = editing_link.label
            form.url.data = editing_link.url
            form.description.data = editing_link.description
        # Se esta criando novo com categoria pre-selecionada
        elif (
            preselected_category
            and preselected_category in ACESSOS_CATEGORIES
            and not form.category.data
        ):
            form.category.data = preselected_category

    context = _build_acessos_context(form=form, open_modal=open_modal, page=page)
    context["editing_link"] = editing_link
    context["modal_type"] = modal_type
    return render_template("acessos.html", **context)


@acessos_bp.route("/acessos/novo", methods=["GET", "POST"])
@login_required
def acessos_novo():
    """
    Exibe e processa formulario para criar novo atalho.

    GET: Redireciona para hub com modal aberto
    POST: Processa criacao do atalho

    Returns:
        302: Redirect para hub (GET ou sucesso)
        200: Formulario com erros (POST falha)
        403: Acesso negado se nao for admin
    """
    if current_user.role != "admin":
        abort(403)

    if request.method == "GET":
        return redirect(url_for("acessos.acessos", modal="novo"))

    form = AccessLinkForm()
    form.category.choices = _access_category_choices()
    return _handle_access_shortcut_submission(form)


@acessos_bp.route("/acessos/<categoria_slug>")
@login_required
def acessos_categoria(categoria_slug: str):
    """
    Endpoint legado para compatibilidade - redireciona para hub.

    Args:
        categoria_slug: Slug da categoria

    Returns:
        302: Redirect para hub principal
        404: Categoria nao encontrada
    """
    if categoria_slug.lower() not in ACESSOS_CATEGORIES:
        abort(404)

    return redirect(url_for("acessos.acessos"))


@acessos_bp.route("/acessos/<categoria_slug>/novo", methods=["GET", "POST"])
@login_required
def acessos_categoria_novo(categoria_slug: str):
    """
    Cria novo atalho dentro de uma categoria especifica.

    GET: Redireciona para hub com modal e categoria pre-selecionada
    POST: Processa criacao do atalho

    Args:
        categoria_slug: Slug da categoria

    Returns:
        302: Redirect para hub (GET ou sucesso)
        200: Formulario com erros (POST falha)
        403: Acesso negado se nao for admin
        404: Categoria nao encontrada
    """
    if current_user.role != "admin":
        abort(403)

    categoria_slug = categoria_slug.lower()
    categoria = ACESSOS_CATEGORIES.get(categoria_slug)
    if not categoria:
        abort(404)

    if request.method == "GET":
        return redirect(
            url_for("acessos.acessos", modal="novo", category=categoria_slug)
        )

    form = AccessLinkForm()
    form.category.choices = _access_category_choices()
    if not form.category.data:
        form.category.data = categoria_slug
    return _handle_access_shortcut_submission(form)


@acessos_bp.route("/acessos/<int:link_id>/editar", methods=["GET", "POST"])
@login_required
def acessos_editar(link_id: int):
    """
    Edita atalho existente.

    GET: Redireciona para hub com modal de edicao
    POST: Processa atualizacao do atalho

    Args:
        link_id: ID do atalho

    Returns:
        302: Redirect para hub (GET ou sucesso)
        200: Formulario com erros (POST falha)
        403: Acesso negado se nao for admin
        404: Atalho nao encontrado
    """
    if current_user.role != "admin":
        abort(403)

    link = AccessLink.query.get_or_404(link_id)

    if request.method == "GET":
        return redirect(url_for("acessos.acessos", modal="editar", link_id=link_id))

    form = AccessLinkForm()
    form.category.choices = _access_category_choices()

    if form.validate_on_submit():
        link.category = form.category.data
        link.label = form.label.data.strip()
        link.url = form.url.data.strip()
        link.description = (form.description.data or "").strip() or None
        db.session.commit()
        flash("Atalho atualizado com sucesso!", "success")
        return redirect(url_for("acessos.acessos"))

    context = _build_acessos_context(form=form, open_modal=True)
    context["editing_link"] = link
    return render_template("acessos.html", **context)


@acessos_bp.route("/acessos/<int:link_id>/excluir", methods=["POST"])
@login_required
@csrf.exempt
def acessos_excluir(link_id: int):
    """
    Exclui atalho existente.

    Args:
        link_id: ID do atalho

    Returns:
        302: Redirect para hub apos exclusao
        403: Acesso negado se nao for admin
        404: Atalho nao encontrado
    """
    if current_user.role != "admin":
        abort(403)

    link = AccessLink.query.get_or_404(link_id)
    label = link.label
    db.session.delete(link)
    db.session.commit()
    flash(f'Atalho "{label}" excluido com sucesso!', "success")
    return redirect(url_for("acessos.acessos"))
