"""
Blueprint para calendario de colaboradores.

Este modulo contem rotas para gestao de eventos no calendario geral.

Rotas:
    - GET/POST /calendario-colaboradores: Calendario com eventos
    - POST /calendario-eventos/<id>/delete: Exclui evento

Dependencias:
    - models: GeneralCalendarEvent
    - forms: GeneralCalendarEventForm
    - services: general_calendar

Autor: Refatoracao automatizada
Data: 2024

TODO: Migrar rotas do __init__.py para este blueprint
"""

from flask import Blueprint

# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

calendario_bp = Blueprint('calendario', __name__)

# =============================================================================
# NOTA: Rotas ainda no __init__.py
# =============================================================================
