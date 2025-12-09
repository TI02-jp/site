"""
Blueprint para gestao de tags.

Este modulo contem rotas para listagem, criacao e edicao de tags
utilizadas para categorizar tarefas e usuarios.

Rotas:
    - GET /tags: Lista todas as tags
    - GET/POST /tags/cadastro: Cadastro de nova tag (admin)
    - GET/POST /tags/editar/<id>: Edicao de tag existente (admin)

Dependencias:
    - models: Tag
    - forms: TagForm
    - services: cache_service

Autor: Refatoracao automatizada
Data: 2024
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app import db
from app.controllers.routes._decorators import admin_required
from app.forms import TagForm
from app.models.tables import Tag


# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

tags_bp = Blueprint('tags', __name__)


# =============================================================================
# ROTAS
# =============================================================================

@tags_bp.route("/tags")
@login_required
def tags():
    """
    Lista todas as tags cadastradas.

    Utiliza cache para melhor performance.

    Returns:
        200: Pagina HTML com listagem de tags
    """
    from app.services.cache_service import get_all_tags_cached

    all_tags = get_all_tags_cached()
    return render_template("tags.html", tags=all_tags)


@tags_bp.route("/tags/cadastro", methods=["GET", "POST"])
@admin_required
def cadastro_tag():
    """
    Renderiza e processa formulario de cadastro de tag.

    GET: Exibe formulario vazio
    POST: Valida e cria nova tag

    Returns:
        200: Formulario de cadastro (GET)
        302: Redirect para listagem apos sucesso (POST)
    """
    form = TagForm()

    if form.validate_on_submit():
        tag = Tag(nome=form.nome.data)
        db.session.add(tag)
        db.session.commit()

        # Invalida cache apos criacao
        from app.services.cache_service import invalidate_tag_cache
        invalidate_tag_cache()

        flash("Tag registrada com sucesso.", "success")
        return redirect(url_for("tags.tags"))

    return render_template("cadastro_tag.html", form=form)


@tags_bp.route("/tags/editar/<int:id>", methods=["GET", "POST"])
@admin_required
def editar_tag(id: int):
    """
    Processa edicao de tag existente.

    Redireciona para modal na listagem de usuarios.

    Args:
        id: ID da tag a editar

    Returns:
        302: Redirect para listagem de usuarios com modal aberto
    """
    tag = Tag.query.get_or_404(id)

    if request.method == "POST":
        form = TagForm()
        if form.validate_on_submit():
            new_name = (form.nome.data or "").strip()
            if new_name:
                # Verifica duplicidade
                duplicate = (
                    Tag.query.filter(
                        db.func.lower(Tag.nome) == new_name.lower(),
                        Tag.id != tag.id
                    ).first()
                )
                if duplicate:
                    flash("Ja existe uma tag com esse nome.", "warning")
                else:
                    tag.nome = new_name
                    db.session.commit()

                    # Invalida cache apos edicao
                    from app.services.cache_service import invalidate_tag_cache
                    invalidate_tag_cache()

                    flash("Tag atualizada com sucesso!", "success")
            else:
                flash("Informe um nome valido para a tag.", "warning")

    return redirect(url_for("list_users", open_tag_modal="1", edit_tag_id=tag.id))
