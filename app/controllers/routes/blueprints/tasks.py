"""
Blueprint para gestao de tarefas.

Este modulo contem rotas para o sistema de tarefas incluindo
visao geral, criacao, edicao, respostas e transferencias.

Rotas:
    - GET /tarefas: Visao geral de tarefas
    - GET /tarefas/setor/<tag>: Tarefas por setor
    - GET /tarefas/historico: Historico de tarefas
    - GET/POST /tarefas/nova: Nova tarefa
    - GET/POST /tarefas/<id>: Visualiza/edita tarefa
    - POST /tarefas/<id>/responder: Adiciona resposta
    - POST /tarefas/<id>/transferir: Transfere tarefa
    - POST /tarefas/<id>/status: Atualiza status
    - POST /tarefas/<id>/excluir: Exclui tarefa

Dependencias:
    - models: Task, TaskStatus, TaskPriority, TaskAttachment, TaskResponse, Tag
    - forms: TaskForm

Autor: Refatoracao automatizada
Data: 2024

TODO: Migrar rotas do __init__.py para este blueprint
"""

from flask import Blueprint

# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

tasks_bp = Blueprint('tasks', __name__)

# =============================================================================
# NOTA: Rotas ainda no __init__.py
# =============================================================================
