"""
Blueprint para rotas principais (home e redirecionamentos iniciais).

Rotas:
    - GET /: Redireciona para a primeira pÁgina apropriada
    - GET /home: Home autenticada
"""

from flask import Blueprint, redirect, render_template, url_for
from flask_login import current_user, login_required

from app.controllers.routes._decorators import meeting_only_access_check
from app.controllers.routes.blueprints.societario import can_access_societario
from app.utils.performance_middleware import track_custom_span

core_bp = Blueprint("core", __name__)


@core_bp.route("/")
def index():
    """Redirect users to the appropriate first page."""
    if current_user.is_authenticated:
        user_name = (current_user.name or current_user.username or "").strip().lower()
        if can_access_societario(current_user) and "tadeu" in user_name:
            return redirect(url_for("societario.societario"))
        if current_user.role == "admin":
            return redirect(url_for("tasks_overview"))
        first_tag = current_user.tags[0] if current_user.tags else None
        if first_tag:
            return redirect(url_for("tasks_sector", tag_id=first_tag.id))
        return redirect(url_for("home"))
    return redirect(url_for("login"))


@core_bp.route("/home")
@login_required
@meeting_only_access_check
def home():
    """Render the authenticated home page."""
    with track_custom_span("template", "render_home"):
        return render_template("home.html")
