"""
Blueprint para sala de reunioes.

Este modulo contem rotas para gestao de reunioes com
integracao ao Google Calendar e Meet.

Rotas:
    - GET/POST /sala-reunioes: Sala de reunioes
    - GET/POST /sala-reunioes/nova: Nova reuniao
    - GET/POST /sala-reunioes/<id>/editar: Edita reuniao
    - POST /sala-reunioes/<id>/excluir: Exclui reuniao
    - POST /sala-reunioes/<id>/status: Atualiza status
    - GET /sala-reunioes/<id>/config: Configuracao Meet

Dependencias:
    - models: Reuniao, ReuniaoStatus
    - forms: MeetingForm, MeetConfigurationForm
    - services: meeting_room, calendar_cache, google_calendar

Autor: Refatoracao automatizada
Data: 2024

TODO: Migrar rotas do __init__.py para este blueprint
"""

from flask import Blueprint

# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

reunioes_bp = Blueprint('reunioes', __name__)

# =============================================================================
# NOTA: Rotas ainda no __init__.py
# =============================================================================
