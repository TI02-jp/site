"""
Blueprint para gestao de procedimentos operacionais.

Este modulo contem rotas para listagem, criacao, visualizacao,
edicao e exclusao de procedimentos operacionais da empresa.

Rotas:
    - GET/POST /procedimentos: Lista e cria procedimentos
    - GET /procedimentos/<id>: Redirect para visualizacao
    - GET /procedimentos/<id>/visualizar: Visualiza procedimento
    - GET /procedimentos/<id>/json: Dados em JSON para modal
    - GET/POST /procedimentos/<id>/editar: Edita procedimento (admin)
    - POST /procedimentos/<id>/excluir: Exclui procedimento (admin)

Dependencias:
    - models: OperationalProcedure
    - forms: OperationalProcedureForm
    - utils: sanitize_html

Autor: Refatoracao automatizada
Data: 2024
"""

import sqlalchemy as sa
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.controllers.routes._decorators import meeting_only_access_check
from app.forms import OperationalProcedureForm
from app.models.tables import OperationalProcedure
from app.utils.security import sanitize_html


# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

procedimentos_bp = Blueprint('procedimentos', __name__)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _can_manage_procedures() -> bool:
    """Verifica se o usuário pode gerenciar procedimentos operacionais.

    Returns:
        True se o usuário é admin OU tem a permissão procedures_manage
    """
    from app.controllers.routes._decorators import has_report_access
    from app.utils.permissions import is_user_admin

    # Admin sempre pode
    if is_user_admin(current_user):
        return True

    # Verifica permissão específica
    return has_report_access("procedures_manage")


# =============================================================================
# ROTAS
# =============================================================================

@procedimentos_bp.route("/procedimentos", methods=["GET", "POST"])
@login_required
@meeting_only_access_check
def procedimentos_operacionais():
    """
    Lista e permite criacao de procedimentos operacionais.

    GET: Lista procedimentos com busca opcional
    POST: Cria novo procedimento

    Query params:
        q: Termo de busca (opcional)

    Returns:
        200: Pagina HTML com listagem de procedimentos
        302: Redirect apos criacao bem-sucedida
    """
    form = OperationalProcedureForm()
    search_term = (request.args.get("q") or "").strip()

    # Query base
    query = OperationalProcedure.query

    # Aplica filtro de busca se informado
    if search_term:
        pattern = f"%{search_term}%"
        query = query.filter(
            sa.or_(
                OperationalProcedure.title.ilike(pattern),
                OperationalProcedure.descricao.ilike(pattern),
            )
        )

    # Ordena por data de atualizacao (mais recentes primeiro)
    procedures = query.order_by(OperationalProcedure.updated_at.desc()).all()

    # Processa POST (criacao)
    if request.method == "POST":
        if not _can_manage_procedures():
            abort(403)

        if form.validate_on_submit():
            proc = OperationalProcedure(
                title=form.title.data,
                descricao=sanitize_html(form.descricao.data or "") or None,
                created_by_id=current_user.id,
            )
            db.session.add(proc)
            db.session.commit()
            flash("Procedimento criado com sucesso.", "success")
            return redirect(url_for("procedimentos.procedimentos_operacionais"))

        flash("Nao foi possivel criar o procedimento. Corrija os erros do formulario.", "danger")

    can_manage = _can_manage_procedures()
    return render_template(
        "procedimentos.html",
        form=form,
        procedures=procedures,
        search_term=search_term,
        can_manage=can_manage,
    )


@procedimentos_bp.route("/procedimentos/<int:proc_id>")
@login_required
def procedimentos_operacionais_redirect(proc_id: int):
    """
    Endpoint de compatibilidade que redireciona para visualizacao.

    Args:
        proc_id: ID do procedimento

    Returns:
        302: Redirect para pagina de visualizacao
    """
    return redirect(url_for("procedimentos.procedimentos_operacionais_ver", proc_id=proc_id))


@procedimentos_bp.route("/procedimentos/<int:proc_id>/visualizar")
@login_required
def procedimentos_operacionais_ver(proc_id: int):
    """
    Exibe pagina de visualizacao do procedimento.

    Args:
        proc_id: ID do procedimento

    Returns:
        200: Pagina HTML com detalhes do procedimento
        404: Procedimento nao encontrado
    """
    proc = OperationalProcedure.query.get_or_404(proc_id)
    can_manage = _can_manage_procedures()
    return render_template("procedimento_view.html", procedure=proc, can_manage=can_manage)


@procedimentos_bp.route("/procedimentos/<int:proc_id>/json")
@login_required
def procedimentos_operacionais_json(proc_id: int):
    """
    Retorna dados do procedimento em JSON para modal.

    Args:
        proc_id: ID do procedimento

    Returns:
        200: JSON com dados do procedimento
        404: Procedimento nao encontrado
    """
    proc = OperationalProcedure.query.get_or_404(proc_id)
    return jsonify({
        "id": proc.id,
        "title": proc.title,
        "descricao": proc.descricao or "",
        "updated_at": proc.updated_at.strftime('%d/%m/%Y as %H:%M') if proc.updated_at else None
    })


@procedimentos_bp.route("/procedimentos/<int:proc_id>/editar", methods=["GET", "POST"])
@login_required
def procedimentos_operacionais_editar(proc_id: int):
    """
    Pagina de edicao do procedimento.

    Args:
        proc_id: ID do procedimento

    Returns:
        200: Formulario de edicao (GET)
        302: Redirect apos atualizacao (POST)
        403: Acesso negado se nao tiver permissao
        404: Procedimento nao encontrado
    """
    if not _can_manage_procedures():
        abort(403)

    proc = OperationalProcedure.query.get_or_404(proc_id)
    form = OperationalProcedureForm()

    if request.method == "GET":
        # Preenche formulario com dados atuais
        form.title.data = proc.title
        form.descricao.data = proc.descricao or ""
        return render_template("procedimento_edit.html", procedure=proc, form=form)

    # Processa atualizacao
    if form.validate_on_submit():
        proc.title = form.title.data
        proc.descricao = sanitize_html(form.descricao.data or "") or None
        db.session.commit()
        flash("Procedimento atualizado com sucesso.", "success")
        return redirect(url_for("procedimentos.procedimentos_operacionais_ver", proc_id=proc.id))

    flash("Nao foi possivel atualizar. Verifique os campos.", "danger")
    return redirect(url_for("procedimentos.procedimentos_operacionais_editar", proc_id=proc.id))


@procedimentos_bp.route("/procedimentos/<int:proc_id>/excluir", methods=["POST"])
@login_required
def procedimentos_operacionais_excluir(proc_id: int):
    """
    Remove um procedimento.

    Args:
        proc_id: ID do procedimento

    Returns:
        302: Redirect para listagem apos exclusao
        403: Acesso negado se nao tiver permissao
        404: Procedimento nao encontrado
    """
    if not _can_manage_procedures():
        abort(403)

    proc = OperationalProcedure.query.get_or_404(proc_id)
    db.session.delete(proc)
    db.session.commit()
    flash("Procedimento excluido com sucesso.", "success")
    return redirect(url_for("procedimentos.procedimentos_operacionais"))
