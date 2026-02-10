"""
Blueprint para catalogo de cursos.

Este modulo contem rotas para visualizacao e gestao de cursos de treinamento.

Rotas:
    - GET/POST /cursos: Catalogo de cursos

Dependencias:
    - models: Course, CourseTag, CourseStatus, User, Reuniao
    - forms: CourseForm, CourseTagForm
    - services: courses, meeting_room

Autor: Refatoracao automatizada
Data: 2024
"""

from collections import Counter
from typing import Any

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
import sqlalchemy as sa

from app import cache, db
from app.forms import CourseForm, CourseTagForm
from app.controllers.routes._decorators import meeting_only_access_check
from app.models.tables import Course, CourseTag, Reuniao, User
from app.services.courses import CourseStatus, get_courses_overview
from app.services.meeting_room import delete_meeting


# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

cursos_bp = Blueprint('cursos', __name__)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_cache_timeout(config_key: str, default: int) -> int:
    """
    Obtem timeout de cache da configuracao ou usa valor padrao.

    Args:
        config_key: Chave de configuracao
        default: Valor padrao em segundos

    Returns:
        int: Timeout em segundos
    """
    from flask import current_app
    return current_app.config.get(config_key, default)


@cache.memoize(timeout=600)
def _get_course_tags_catalog() -> list[CourseTag]:
    """
    Catalogo cacheado de tags de cursos ordenadas por nome.

    Returns:
        list[CourseTag]: Lista de tags de cursos
    """
    return CourseTag.query.order_by(CourseTag.name.asc()).all()


def _invalidate_course_tags_cache() -> None:
    """Limpa o cache do catalogo de tags de cursos."""
    cache.delete_memoized(_get_course_tags_catalog)


# =============================================================================
# ROTAS
# =============================================================================

@cursos_bp.route("/cursos", methods=["GET", "POST"])
@login_required
@meeting_only_access_check
def cursos():
    """
    Exibe o catalogo de cursos internos e permite gestao.

    GET: Exibe a lista de cursos e formularios
    POST: Cria/edita/exclui cursos e tags
    """
    form = CourseForm()
    tag_form = CourseTagForm(prefix="tag")
    can_manage_courses = current_user.role == "admin"

    # Usar Tags de usuarios no campo "Setores Participantes"
    from app.services.cache_service import get_all_tags_cached
    from app.services.optimized_queries import get_active_users_with_tags

    sector_choices = [
        (tag.id, tag.nome)
        for tag in get_all_tags_cached()
    ]
    participant_choices = [
        (user.id, user.name)
        for user in get_active_users_with_tags()
    ]
    form.sectors.choices = sector_choices
    form.participants.choices = participant_choices
    course_tags = _get_course_tags_catalog()
    tag_choices = [(tag.id, tag.name) for tag in course_tags]
    form.tags.choices = tag_choices

    sector_lookup = {value: label for value, label in sector_choices}
    participant_lookup = {value: label for value, label in participant_choices}
    tag_lookup = {tag.id: tag for tag in course_tags}

    # Criar mapeamento de usuarios para suas tags (IDs)
    users_with_tags = User.query.filter_by(ativo=True).options(
        db.joinedload(User.tags)
    ).all()
    user_tags_map = {
        user.id: [tag.id for tag in user.tags]
        for user in users_with_tags
    }

    course_id_raw = (form.course_id.data or "").strip()
    is_tag_submission = request.method == "POST" and "tag-submit" in request.form

    # Processamento de submissao de tag
    if is_tag_submission:
        if not can_manage_courses:
            flash("Apenas administradores podem gerenciar as tags de cursos.", "danger")
            return redirect(url_for("cursos.cursos"))

        if tag_form.validate_on_submit():
            tag_name = tag_form.name.data or ""
            existing_tag = db.session.execute(
                sa.select(CourseTag).where(
                    sa.func.lower(CourseTag.name) == sa.func.lower(tag_name)
                )
            ).scalar_one_or_none()

            if existing_tag:
                tag_form.name.errors.append("Já existe uma tag de curso com esse nome.")
                flash("Já existe uma tag de curso com esse nome.", "warning")
            else:
                new_tag = CourseTag(name=tag_name)
                db.session.add(new_tag)
                db.session.commit()
                _invalidate_course_tags_cache()
                flash("Tag de curso criada com sucesso!", "success")
                return redirect(url_for("cursos.cursos"))
        else:
            flash(
                "Não foi possível adicionar a tag. Verifique o nome informado.",
                "danger",
            )

    # Processamento de submissao de curso
    if not is_tag_submission:
        if request.method == "POST" and not can_manage_courses:
            flash("Apenas administradores podem cadastrar ou editar cursos.", "danger")
            return redirect(url_for("cursos.cursos"))

        # Exclusao de curso
        if request.method == "POST" and form.submit_delete.data:
            if not course_id_raw:
                flash("Selecione um curso para excluir.", "danger")
                return redirect(url_for("cursos.cursos"))

            try:
                course_id = int(course_id_raw)
            except ValueError:
                flash("O curso selecionado não foi encontrado.", "danger")
                return redirect(url_for("cursos.cursos"))

            existing_course_id = db.session.execute(
                sa.select(Course.id).where(Course.id == course_id)
            ).scalar_one_or_none()

            if existing_course_id is None:
                flash("O curso selecionado não foi encontrado.", "danger")
                return redirect(url_for("cursos.cursos"))

            # Remover reunioes vinculadas
            linked_meetings = Reuniao.query.filter_by(course_id=course_id).all()
            for meeting in linked_meetings:
                if not delete_meeting(meeting):
                    flash(
                        "Não foi possível remover a reunião vinculada.",
                        "danger",
                    )
                    return redirect(url_for("cursos.cursos"))

            db.session.execute(sa.delete(Course).where(Course.id == course_id))
            db.session.commit()

            if linked_meetings:
                flash("Curso e reuniões associadas excluídos!", "success")
            else:
                flash("Curso excluído com sucesso!", "success")
            return redirect(url_for("cursos.cursos"))

        # Criacao/edicao de curso
        if form.validate_on_submit():
            course_id: int | None = None
            if course_id_raw:
                try:
                    course_id = int(course_id_raw)
                except ValueError:
                    course_id = None

            selected_sector_names = [
                sector_lookup[sector_id]
                for sector_id in form.sectors.data
                if sector_id in sector_lookup
            ]
            selected_participant_names = [
                participant_lookup[user_id]
                for user_id in form.participants.data
                if user_id in participant_lookup
            ]
            selected_tags = [
                tag_lookup[tag_id]
                for tag_id in form.tags.data
                if tag_id in tag_lookup
            ]

            # Validacao de campos obrigatorios
            if not selected_sector_names:
                flash("Selecione ao menos um setor válido para o curso.", "danger")
                return redirect(url_for("cursos.cursos"))

            if not selected_participant_names:
                flash("Selecione ao menos um participante válido.", "danger")
                return redirect(url_for("cursos.cursos"))

            # Preparar dados para adicionar ao calendario
            should_add_to_calendar = bool(form.submit_add_to_calendar.data)
            meeting_query_params: dict[str, Any] = {}

            if should_add_to_calendar:
                meeting_query_params = {"course_calendar": "1"}
                name_value = (form.name.data or "").strip()
                if name_value:
                    meeting_query_params["subject"] = name_value
                observation_value = (form.observation.data or "").strip()
                if observation_value:
                    meeting_query_params["description"] = observation_value
                if form.start_date.data:
                    meeting_query_params["date"] = form.start_date.data.isoformat()
                if form.schedule_start.data:
                    meeting_query_params["start"] = form.schedule_start.data.strftime("%H:%M")
                if form.schedule_end.data:
                    meeting_query_params["end"] = form.schedule_end.data.strftime("%H:%M")
                participant_ids = [str(user_id) for user_id in form.participants.data]
                if participant_ids:
                    meeting_query_params["participants"] = participant_ids

            success_message = ""

            # Edicao de curso existente
            if course_id is not None:
                course_obj = db.session.get(Course, course_id)

                if course_obj is None:
                    flash("O curso selecionado não foi encontrado.", "danger")
                    return redirect(url_for("cursos.cursos"))

                course_obj.name = form.name.data.strip()
                course_obj.instructor = form.instructor.data.strip()
                course_obj.sectors = ", ".join(selected_sector_names)
                course_obj.participants = ", ".join(selected_participant_names)
                course_obj.workload = form.workload.data
                course_obj.start_date = form.start_date.data
                course_obj.schedule_start = form.schedule_start.data
                course_obj.schedule_end = form.schedule_end.data
                course_obj.completion_date = form.completion_date.data
                course_obj.status = form.status.data
                course_obj.observation = (form.observation.data or "").strip() or None
                course_obj.tags = selected_tags
                db.session.commit()
                success_message = "Curso atualizado com sucesso!"
            else:
                # Criacao de novo curso
                course = Course(
                    name=form.name.data.strip(),
                    instructor=form.instructor.data.strip(),
                    sectors=", ".join(selected_sector_names),
                    participants=", ".join(selected_participant_names),
                    workload=form.workload.data,
                    start_date=form.start_date.data,
                    schedule_start=form.schedule_start.data,
                    schedule_end=form.schedule_end.data,
                    completion_date=form.completion_date.data,
                    status=form.status.data,
                    observation=(form.observation.data or "").strip() or None,
                    tags=selected_tags,
                )
                db.session.add(course)
                db.session.commit()
                course_id = course.id
                success_message = "Curso cadastrado com sucesso!"

            if success_message:
                flash(success_message, "success")

            # Redirecionar para calendario se solicitado
            if (
                should_add_to_calendar
                and meeting_query_params.get("subject")
                and meeting_query_params.get("date")
            ):
                if course_id is not None:
                    meeting_query_params["course_id"] = str(course_id)
                return redirect(url_for("sala_reunioes", **meeting_query_params))

            return redirect(url_for("cursos.cursos"))

        elif request.method == "POST":
            flash(
                "Não foi possível salvar o curso. Verifique os campos.",
                "danger",
            )

    # Renderizar catalogo de cursos
    courses = get_courses_overview()
    status_counts = Counter(course.status for course in courses)
    status_classes = {
        CourseStatus.COMPLETED: "status-pill--completed",
        CourseStatus.PLANNED: "status-pill--planned",
        CourseStatus.DELAYED: "status-pill--delayed",
        CourseStatus.POSTPONED: "status-pill--postponed",
        CourseStatus.CANCELLED: "status-pill--cancelled",
    }

    return render_template(
        "cursos.html",
        courses=courses,
        status_counts=status_counts,
        status_classes=status_classes,
        CourseStatus=CourseStatus,
        form=form,
        tag_form=tag_form,
        course_tags=course_tags,
        editing_course_id=course_id_raw,
        can_manage_courses=can_manage_courses,
        user_tags_map=user_tags_map,
    )


# =============================================================================
# ALIASES PARA COMPATIBILIDADE
# =============================================================================

# Nota: Os endpoints sao registrados como cursos.cursos
# Para compatibilidade, registrar alias no __init__.py se necessario
