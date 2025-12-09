"""
Blueprint para gestao da diretoria.

Este modulo contem rotas para acordos, feedbacks e eventos da diretoria.

Rotas:
    - GET/POST /diretoria/acordos: Lista e cria acordos
    - GET/POST /diretoria/acordos/<id>/editar: Edita acordo
    - POST /diretoria/acordos/<id>/excluir: Exclui acordo
    - GET/POST /diretoria/feedbacks: Lista e cria feedbacks
    - GET/POST /diretoria/eventos: Lista e cria eventos
    - GET/POST /diretoria/eventos/<id>/editar: Edita evento
    - POST /diretoria/eventos/<id>/excluir: Exclui evento

Dependencias:
    - models: DiretoriaEvent, DiretoriaAgreement, DiretoriaFeedback
    - forms: DiretoriaAcordoForm, DiretoriaFeedbackForm

Autor: Refatoracao automatizada
Data: 2024

TODO: Migrar rotas do __init__.py para este blueprint
"""

from flask import Blueprint

# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

diretoria_bp = Blueprint('diretoria', __name__)

# =============================================================================
# NOTA: Rotas ainda no __init__.py
# =============================================================================
