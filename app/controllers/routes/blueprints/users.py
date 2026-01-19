"""
Blueprint para gestao de usuarios.

Este modulo contem rotas para listagem, cadastro e edicao de usuarios.

Rotas:
    - GET/POST /users: Lista e cadastra usuarios
    - GET /users/active: Lista usuarios ativos
    - GET /novo_usuario: Redirect para modal de cadastro
    - GET /user/edit/<id>: Redirect para modal de edicao

Dependencias:
    - models: User, Tag, Task
    - forms: RegistrationForm, EditUserForm, TagForm, TagDeleteForm

Autor: Refatoracao automatizada
Data: 2024-12
"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import joinedload

from app import db
from app.controllers.routes._decorators import admin_required
from app.forms import EditUserForm, RegistrationForm, TagDeleteForm, TagForm
from app.models.tables import Tag, Task, User


# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

users_bp = Blueprint('users', __name__)


# =============================================================================
# CONSTANTES
# =============================================================================

PERSONAL_TAG_PREFIX = "[PESSOAL] "


# =============================================================================
# FUNÇÕES AUXILIARES
# =============================================================================

def _delete_task_recursive(task: Task) -> None:
    """
    Deleta uma tarefa e todas as suas subtarefas recursivamente.

    Args:
        task: Tarefa a ser deletada
    """
    children = Task.query.filter_by(parent_id=task.id).all()
    for child in children:
        _delete_task_recursive(child)

    # Delete task responses
    from app.models.tables import TaskResponse, TaskResponseParticipant
    TaskResponseParticipant.query.filter_by(task_id=task.id).delete()
    TaskResponse.query.filter_by(task_id=task.id).delete()

    # Delete task follow-ups
    from app.models.tables import TaskFollowUp
    TaskFollowUp.query.filter_by(task_id=task.id).delete()

    # Delete task attachments
    from app.models.tables import TaskAttachment
    TaskAttachment.query.filter_by(task_id=task.id).delete()

    # Delete task history
    from app.models.tables import TaskHistory
    TaskHistory.query.filter_by(task_id=task.id).delete()

    # Delete the task itself
    db.session.delete(task)


# =============================================================================
# ROTAS
# =============================================================================

@users_bp.route("/users/active", methods=["GET"], endpoint="list_active_users")
@users_bp.route("/users", methods=["GET", "POST"])
@admin_required
def list_users():
    """List and register users in the admin panel."""
    form = RegistrationForm()
    edit_form = EditUserForm(prefix="edit")
    tag_create_form = TagForm(prefix="tag_create")
    tag_create_form.submit.label.text = "Adicionar"
    tag_edit_form = TagForm(prefix="tag_edit")
    tag_edit_form.submit.label.text = "Salvar alterações"
    tag_delete_form = TagDeleteForm()
    # Use cached tags to reduce database load (5-minute cache)
    from app.services.cache_service import get_all_tags_cached
    tag_list = get_all_tags_cached()
    form.tags.choices = [(t.id, t.nome) for t in tag_list]
    edit_form.tags.choices = [(t.id, t.nome) for t in tag_list]
    show_inactive = request.args.get("show_inactive") in ("1", "on", "true", "True")
    raw_tag_ids = request.args.getlist("tag_id")
    selected_tag_ids = []
    for raw_id in raw_tag_ids:
        try:
            tag_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if tag_id not in selected_tag_ids:
            selected_tag_ids.append(tag_id)
    selected_tag_id = selected_tag_ids[0] if len(selected_tag_ids) == 1 else None
    open_tag_modal = request.args.get("open_tag_modal") in ("1", "true", "True")
    open_user_modal = request.args.get("open_user_modal") in ("1", "true", "True")
    open_edit_modal = request.args.get("open_edit_modal") in ("1", "true", "True")
    edit_tag = None
    edit_tag_id_arg = request.args.get("edit_tag_id", type=int)
    editing_user = None
    editing_user_id = request.args.get("edit_user_id", type=int)
    edit_password_error = None
    if edit_tag_id_arg:
        open_tag_modal = True
        edit_tag = Tag.query.get(edit_tag_id_arg)
        if not edit_tag:
            flash("Tag não encontrada.", "warning")
        elif request.method == "GET":
            tag_edit_form.nome.data = edit_tag.nome

    if editing_user_id:
        editing_user = User.query.get(editing_user_id)
        if not editing_user:
            flash("Usuário não encontrado.", "warning")
            editing_user_id = None
        else:
            if editing_user.is_master and current_user.id != editing_user.id:
                abort(403)
            if request.method == "GET":
                edit_form.process(obj=editing_user)
                edit_form.tags.data = [t.id for t in editing_user.tags]
            open_edit_modal = True

    if request.method == "POST":
        form_name = request.form.get("form_name")

        if form_name == "user":
            open_user_modal = True
            if form.validate_on_submit():
                existing_user = User.query.filter(
                    (User.username == form.username.data)
                    | (User.email == form.email.data)
                ).first()
                if existing_user:
                    if existing_user.username == form.username.data:
                        form.username.errors.append("Usuário já cadastrado.")
                    if existing_user.email == form.email.data:
                        form.email.errors.append("Email já cadastrado.")
                    flash("Usuário ou email já cadastrado.", "warning")
                else:
                    from app.utils.audit import log_user_action, ActionType, ResourceType

                    user = User(
                        username=form.username.data,
                        email=form.email.data,
                        name=form.name.data,
                        role=form.role.data,
                    )
                    user.set_password(form.password.data)
                    if form.tags.data:
                        user.tags = Tag.query.filter(Tag.id.in_(form.tags.data)).all()
                    db.session.add(user)
                    db.session.commit()

                    # Log user creation
                    log_user_action(
                        action_type=ActionType.CREATE,
                        resource_type=ResourceType.USER,
                        action_description=f'Criou usuario {user.username}',
                        resource_id=user.id,
                        new_values={
                            'username': user.username,
                            'email': user.email,
                            'name': user.name,
                            'role': user.role,
                            'tags': [tag.nome for tag in user.tags] if user.tags else [],
                        }
                    )

                    flash("Novo usuário cadastrado com sucesso!", "success")
                    return redirect(url_for("users.list_users"))

        if form_name == "user_edit":
            open_edit_modal = True
            edit_user_id_raw = request.form.get("user_id")
            try:
                editing_user_id = int(edit_user_id_raw) if edit_user_id_raw is not None else None
            except (TypeError, ValueError):
                editing_user_id = None
            editing_user = (
                User.query.options(joinedload(User.tags)).get(editing_user_id)
                if editing_user_id is not None
                else None
            )
            if not editing_user:
                flash("Usuário não encontrado.", "warning")
            else:
                if editing_user.is_master and current_user.id != editing_user.id:
                    abort(403)
                if edit_form.validate_on_submit():
                    from app.utils.audit import log_user_action, ActionType, ResourceType

                    # Capture old values before changes
                    old_values = {
                        'username': editing_user.username,
                        'email': editing_user.email,
                        'name': editing_user.name,
                        'role': editing_user.role,
                        'ativo': editing_user.ativo,
                        'tags': [tag.nome for tag in editing_user.tags] if editing_user.tags else [],
                    }

                    editing_user.username = edit_form.username.data
                    editing_user.email = edit_form.email.data
                    editing_user.name = edit_form.name.data
                    if not editing_user.is_master:
                        editing_user.role = edit_form.role.data
                        editing_user.ativo = edit_form.ativo.data
                    else:
                        editing_user.ativo = True
                    if edit_form.tags.data:
                        editing_user.tags = (
                            Tag.query.filter(Tag.id.in_(edit_form.tags.data)).all()
                        )
                    else:
                        editing_user.tags = []

                    new_password = request.form.get("new_password")
                    confirm_new_password = request.form.get("confirm_new_password")
                    password_changed = False
                    if new_password:
                        if new_password != confirm_new_password:
                            edit_password_error = "As senhas devem ser iguais."
                        else:
                            editing_user.set_password(new_password)
                            password_changed = True

                    if edit_password_error:
                        flash(edit_password_error, "danger")
                    else:
                        db.session.commit()

                        # Capture new values after changes
                        new_values = {
                            'username': editing_user.username,
                            'email': editing_user.email,
                            'name': editing_user.name,
                            'role': editing_user.role,
                            'ativo': editing_user.ativo,
                            'tags': [tag.nome for tag in editing_user.tags] if editing_user.tags else [],
                        }

                        # Log user update
                        log_user_action(
                            action_type=ActionType.UPDATE,
                            resource_type=ResourceType.USER,
                            action_description=f'Atualizou usuario {editing_user.username}',
                            resource_id=editing_user.id,
                            old_values=old_values,
                            new_values=new_values,
                        )

                        # Log password change separately if applicable
                        if password_changed:
                            log_user_action(
                                action_type=ActionType.CHANGE_PASSWORD,
                                resource_type=ResourceType.USER,
                                action_description=f'Trocou senha do usuario {editing_user.username}',
                                resource_id=editing_user.id,
                            )

                        flash("Usuário atualizado com sucesso!", "success")
                        return redirect(url_for("users.list_users"))

        if form_name == "tag_create":
            open_tag_modal = True
            if tag_create_form.validate_on_submit():
                tag_name = (tag_create_form.nome.data or "").strip()
                existing_tag = (
                    Tag.query.filter(db.func.lower(Tag.nome) == tag_name.lower()).first()
                    if tag_name
                    else None
                )
                if existing_tag:
                    tag_create_form.nome.errors.append("Já existe uma tag com esse nome.")
                    flash("Já existe uma tag com esse nome.", "warning")
                elif tag_name:
                    tag = Tag(nome=tag_name)
                    db.session.add(tag)
                    db.session.commit()
                    # Invalidate tag cache after creating new tag
                    from app.services.cache_service import invalidate_tag_cache
                    invalidate_tag_cache()
                    flash("Tag cadastrada com sucesso!", "success")
                    return redirect(url_for("users.list_users", open_tag_modal="1"))

        if form_name == "tag_edit":
            open_tag_modal = True
            tag_id_raw = request.form.get("tag_id")
            try:
                tag_id = int(tag_id_raw) if tag_id_raw is not None else None
            except (TypeError, ValueError):
                tag_id = None
            if tag_id is not None:
                edit_tag = Tag.query.get(tag_id)
            if not edit_tag:
                flash("Tag não encontrada.", "warning")
            elif tag_edit_form.validate_on_submit():
                new_name = (tag_edit_form.nome.data or "").strip()
                if not new_name:
                    tag_edit_form.nome.errors.append("Informe um nome para a tag.")
                else:
                    duplicate = (
                        Tag.query.filter(
                            db.func.lower(Tag.nome) == new_name.lower(), Tag.id != edit_tag.id
                        ).first()
                    )
                    if duplicate:
                        tag_edit_form.nome.errors.append("Já existe uma tag com esse nome.")
                        flash("Já existe uma tag com esse nome.", "warning")
                    else:
                        edit_tag.nome = new_name
                        db.session.commit()
                        # Invalidate tag cache after editing tag
                        from app.services.cache_service import invalidate_tag_cache
                        invalidate_tag_cache()
                        flash("Tag atualizada com sucesso!", "success")
                        return redirect(url_for("users.list_users", open_tag_modal="1"))

        if form_name == "tag_delete":
            open_tag_modal = True
            if tag_delete_form.validate_on_submit():
                tag_id_raw = tag_delete_form.tag_id.data
                try:
                    tag_id = int(str(tag_id_raw).strip())
                except (TypeError, ValueError):
                    tag_id = None
                if tag_id is None:
                    flash("Tag selecionada é inválida.", "danger")
                else:
                    tag_to_delete = Tag.query.get(tag_id)
                    if not tag_to_delete:
                        flash("Tag não encontrada.", "warning")
                    else:
                        try:
                            if tag_to_delete.nome.startswith(PERSONAL_TAG_PREFIX):
                                personal_tasks = Task.query.filter_by(tag_id=tag_to_delete.id).all()
                                for task in personal_tasks:
                                    _delete_task_recursive(task)
                                db.session.flush()
                            db.session.delete(tag_to_delete)
                            db.session.commit()
                            # Invalidate tag cache after deleting tag
                            from app.services.cache_service import invalidate_tag_cache
                            invalidate_tag_cache()
                        except IntegrityError:
                            db.session.rollback()
                            flash(
                                "Não foi possível excluir a tag porque há tarefas vinculadas a ela. "
                                "Remova ou atualize as tarefas antes de tentar novamente.",
                                "danger",
                            )
                        except SQLAlchemyError:
                            db.session.rollback()
                            flash(
                                "Não foi possível excluir a tag selecionada.",
                                "danger",
                            )
                        else:
                            flash("Tag removida com sucesso!", "success")
                return redirect(url_for("users.list_users", open_tag_modal="1"))
            else:
                flash("Não foi possível excluir a tag selecionada.", "danger")

    users_query = User.query.options(joinedload(User.tags))
    if not show_inactive:
        users_query = users_query.filter_by(ativo=True)
    if selected_tag_ids:
        users_query = (
            users_query.join(User.tags)
            .filter(Tag.id.in_(selected_tag_ids))
            .distinct()
        )

    # Paginação (otimizado para listas grandes)
    page = request.args.get('page', 1, type=int)
    per_page = 50  # Limite de usuários por página
    pagination = users_query.order_by(User.ativo.desc(), User.name).paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )
    users = pagination.items
    return render_template(
        "list_users.html",
        users=users,
        pagination=pagination,
        form=form,
        edit_form=edit_form,
        tag_create_form=tag_create_form,
        tag_edit_form=tag_edit_form,
        tag_delete_form=tag_delete_form,
        edit_tag=edit_tag,
        tag_list=tag_list,
        show_inactive=show_inactive,
        selected_tag_id=selected_tag_id,
        selected_tag_ids=selected_tag_ids,
        open_tag_modal=open_tag_modal,
        open_user_modal=open_user_modal,
        open_edit_modal=open_edit_modal,
        editing_user=editing_user,
        editing_user_id=editing_user_id,
        edit_password_error=edit_password_error,
    )


@users_bp.route("/novo_usuario", methods=["GET"])
@admin_required
def novo_usuario():
    """Redirect to the user list with the registration modal open."""
    return redirect(url_for("users.list_users", open_user_modal="1"))


@users_bp.route("/user/edit/<int:user_id>", methods=["GET"])
@admin_required
def edit_user(user_id):
    """Redirect to the user list opening the edit modal for the selected user."""
    user = User.query.get_or_404(user_id)
    if user.is_master and current_user.id != user.id:
        abort(403)
    return redirect(
        url_for(
            "users.list_users",
            open_edit_modal="1",
            edit_user_id=user.id,
        )
    )
