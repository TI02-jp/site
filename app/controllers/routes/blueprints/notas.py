"""
Blueprint para notas de debito.

Este modulo contem rotas para gestao de notas de debito,
cadastros e notas recorrentes.

Rotas:
    - GET/POST /notas-debito: Lista e cria notas
    - GET/POST /notas-debito/<id>/editar: Edita nota
    - POST /notas-debito/<id>/excluir: Exclui nota
    - GET/POST /cadastro-nota: Cadastro de notas
    - GET/POST /notas-recorrentes: Notas recorrentes
    - GET /totalizador-notas: Totalizador

Dependencias:
    - models: NotaDebito, CadastroNota, NotaRecorrente
    - forms: NotaDebitoForm, CadastroNotaForm, NotaRecorrenteForm

Autor: Refatoracao automatizada
Data: 2024

TODO: Migrar rotas do __init__.py para este blueprint
"""

from flask import Blueprint

# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

notas_bp = Blueprint('notas', __name__)

# =============================================================================
# NOTA: Rotas ainda no __init__.py
# =============================================================================
