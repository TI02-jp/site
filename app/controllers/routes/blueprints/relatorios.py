"""
Blueprint para relatorios administrativos.

Este modulo contem rotas para geracao de relatorios
com controle de acesso por permissoes.

Rotas:
    - GET /relatorios: Index de relatorios
    - GET /relatorios/empresas: Relatorio de empresas
    - GET /relatorios/fiscal: Relatorio fiscal
    - GET /relatorios/contabil: Relatorio contabil
    - GET /relatorios/usuarios: Relatorio de usuarios
    - GET /relatorios/cursos: Relatorio de cursos
    - GET /relatorios/tarefas: Relatorio de tarefas
    - GET/POST /relatorios/permissoes: Gestao de permissoes

Dependencias:
    - models: ReportPermission, Empresa, User, Course, Task
    - decorators: report_access_required

Autor: Refatoracao automatizada
Data: 2024

TODO: Migrar rotas do __init__.py para este blueprint
"""

from flask import Blueprint

# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

relatorios_bp = Blueprint('relatorios', __name__)

# =============================================================================
# NOTA: Rotas ainda no __init__.py
# =============================================================================
