"""
Blueprint para gestao de usuarios.

Este modulo contem rotas para listagem, cadastro e edicao de usuarios.

Rotas:
    - GET/POST /users: Lista e cadastra usuarios
    - GET/POST /users/<id>/editar: Edita usuario
    - POST /users/<id>/toggle-status: Ativa/desativa usuario
    - POST /users/<id>/excluir: Exclui usuario

Dependencias:
    - models: User, Tag
    - forms: RegistrationForm, EditUserForm

Autor: Refatoracao automatizada
Data: 2024

TODO: Migrar rotas do __init__.py para este blueprint
"""

from flask import Blueprint

# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

users_bp = Blueprint('users', __name__)

# =============================================================================
# NOTA: Rotas ainda no __init__.py
# =============================================================================
