from flask import Blueprint

logs_bp = Blueprint('logs', __name__)

from . import routes  # noqa: E402,F401
