"""
Blueprint para gestao de empresas.

Este modulo contem rotas para CRUD de empresas e departamentos,
incluindo integracao com CNPJ e reunioes de cliente.

Rotas:
    - GET /empresas: Lista empresas
    - GET/POST /empresas/cadastro: Cadastro de empresa
    - GET /empresas/<id>: Visualiza empresa
    - GET/POST /empresas/<id>/editar: Edita empresa
    - POST /empresas/<id>/excluir: Exclui empresa
    - GET/POST /empresas/<id>/departamento/<tipo>: Gestao de departamento
    - GET/POST /empresas/<id>/reunioes-cliente: Reunioes do cliente

Dependencias:
    - models: Empresa, Departamento, ClienteReuniao
    - forms: EmpresaForm, DepartamentoFiscalForm, etc.
    - services: cnpj

Autor: Refatoracao automatizada
Data: 2024

TODO: Migrar rotas do __init__.py para este blueprint
"""

from flask import Blueprint

# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

empresas_bp = Blueprint('empresas', __name__)

# =============================================================================
# NOTA: Rotas ainda no __init__.py
# =============================================================================
