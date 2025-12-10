"""
Blueprint para calendario de colaboradores.

Este modulo contem rotas para gerenciar eventos do calendario interno.

Rotas:
    - GET/POST /calendario-colaboradores: Lista e cria eventos
    - POST /calendario-eventos/<int:event_id>/delete: Exclui evento

Dependencias:
    - models: GeneralCalendarEvent
    - forms: GeneralCalendarEventForm
    - services: general_calendar, google_calendar

Autor: Refatoracao automatizada
Data: 2024
"""

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

from app.forms import GeneralCalendarEventForm
from app.controllers.routes._decorators import meeting_only_access_check
from app.models.tables import GeneralCalendarEvent
from app.services.general_calendar import (
    create_calendar_event_from_form,
    update_calendar_event_from_form,
    delete_calendar_event,
    populate_event_participants,
)
from app.services.google_calendar import get_calendar_timezone
from app.utils.permissions import is_user_admin


# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

calendario_bp = Blueprint('calendario', __name__)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def user_has_tag(tag_name: str) -> bool:
    """
    Verifica se o usuario atual possui uma tag especifica.

    Args:
        tag_name: Nome da tag a verificar

    Returns:
        bool: True se o usuario possui a tag
    """
    if not current_user.is_authenticated:
        return False
    tags = getattr(current_user, "tags", None) or []
    return any(tag.nome == tag_name for tag in tags)


# =============================================================================
# ROTAS
# =============================================================================

@calendario_bp.route("/calendario-colaboradores", methods=["GET", "POST"])
@login_required
@meeting_only_access_check
def calendario_colaboradores():
    """
    Exibe e gerencia o calendario interno de colaboradores.

    GET: Exibe calendario
    POST: Cria ou edita evento
    """
    form = GeneralCalendarEventForm()
    populate_event_participants(form)
    can_manage = (
        is_user_admin(current_user) or user_has_tag("Gestão") or user_has_tag("Coord.")
    )
    show_modal = False

    if form.validate_on_submit():
        if not can_manage:
            abort(403)

        event_id_raw = form.event_id.data
        if event_id_raw:
            # Editando evento existente
            try:
                event_id = int(event_id_raw)
            except (TypeError, ValueError):
                abort(400)

            event = GeneralCalendarEvent.query.get_or_404(event_id)
            if current_user.role != "admin" and event.created_by_id != current_user.id:
                flash("Você só pode editar eventos que você criou.", "danger")
                return redirect(url_for("calendario.calendario_colaboradores"))

            update_calendar_event_from_form(event, form)
        else:
            # Criando novo evento
            create_calendar_event_from_form(form, current_user.id)

        return redirect(url_for("calendario.calendario_colaboradores"))

    elif request.method == "POST":
        show_modal = True

    calendar_tz = get_calendar_timezone()
    return render_template(
        "calendario_colaboradores.html",
        form=form,
        can_manage=can_manage,
        show_modal=show_modal,
        calendar_timezone=calendar_tz.key,
    )


@calendario_bp.route("/calendario-eventos/<int:event_id>/delete", methods=["POST"])
@login_required
def delete_calendario_evento(event_id):
    """Exclui um evento do calendario de colaboradores."""
    event = GeneralCalendarEvent.query.get_or_404(event_id)
    can_manage = (
        is_user_admin(current_user) or user_has_tag("Gestão") or user_has_tag("Coord.")
    )

    if not can_manage:
        abort(403)

    if not is_user_admin(current_user) and event.created_by_id != current_user.id:
        flash("Você só pode excluir eventos que você criou.", "danger")
        return redirect(url_for("calendario.calendario_colaboradores"))

    delete_calendar_event(event)
    flash("Evento removido com sucesso!", "success")
    return redirect(url_for("calendario.calendario_colaboradores"))


# =============================================================================
# ALIASES PARA COMPATIBILIDADE
# =============================================================================

# Nota: Endpoints registrados como calendario.calendario_colaboradores
# Para compatibilidade, registrar aliases no __init__.py se necessario
