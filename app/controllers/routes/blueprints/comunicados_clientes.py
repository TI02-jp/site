"""
Blueprint para acompanhar comunicados enviados aos clientes.

Rotas:
    - GET/POST /comunicados-clientes: painel administrativo de comunicados
"""

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app import db
from app.controllers.routes._decorators import admin_required
from app.forms import ClientAnnouncementForm
from app.models.tables import ClientAnnouncement


comunicados_clientes_bp = Blueprint("comunicados_clientes", __name__)


def _get_next_sequence_number() -> int:
    last_number = db.session.query(func.max(ClientAnnouncement.sequence_number)).scalar()
    return (last_number or 0) + 1


@comunicados_clientes_bp.route("/comunicados-clientes", methods=["GET", "POST"])
@admin_required
def comunicados_clientes():
    form = ClientAnnouncementForm()
    next_number = _get_next_sequence_number()

    if form.validate_on_submit():
        next_number = _get_next_sequence_number()
        entry = ClientAnnouncement(
            sequence_number=next_number,
            subject=form.subject.data or "",
            tax_regime=form.tax_regime.data or "",
            summary=form.summary.data or "",
            created_by_id=current_user.id,
        )
        db.session.add(entry)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash(
                "Nao foi possivel registrar o comunicado. Atualize a pagina e tente novamente.",
                "warning",
            )
        else:
            flash("Comunicado registrado com sucesso.", "success")
            return redirect(url_for("comunicados_clientes"))

    entries = (
        ClientAnnouncement.query.order_by(ClientAnnouncement.sequence_number.desc())
        .all()
    )
    return render_template(
        "admin/comunicados_clientes.html",
        form=form,
        entries=entries,
        next_number=next_number,
    )
