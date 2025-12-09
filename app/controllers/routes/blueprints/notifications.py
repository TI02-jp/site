"""
Blueprint para notificacoes e SSE.

Este modulo contem rotas para centro de notificacoes e
Server-Sent Events (SSE) para atualizacoes em tempo real.

Rotas:
    - GET /notifications: Centro de notificacoes
    - GET /notifications/stream: SSE stream
    - POST /notifications/mark-read: Marca como lida
    - POST /notifications/mark-all-read: Marca todas como lidas
    - GET /realtime/stream: SSE para atualizacoes gerais

Dependencias:
    - models: TaskNotification
    - services: realtime, push_notifications

Autor: Refatoracao automatizada
Data: 2024

TODO: Migrar rotas do __init__.py para este blueprint
"""

from flask import Blueprint

# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

notifications_bp = Blueprint('notifications', __name__)

# =============================================================================
# NOTA: Rotas ainda no __init__.py
# =============================================================================
