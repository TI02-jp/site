"""
Queries otimizadas com eager loading e cache.

Este módulo contém funções auxiliares para queries frequentes,
implementando eager loading para evitar N+1 queries e cache
para reduzir carga no banco de dados.

Uso:
    from app.services.optimized_queries import get_active_users_with_tags

    users = get_active_users_with_tags()
"""

from __future__ import annotations

import os
from typing import List, Optional

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload, load_only, selectinload, defer

from app import db
from app.extensions.cache import cached_query
from app.models.tables import (
    Empresa,
    Inventario,
    ProcessoSocietario,
    ProcessoSocietarioHistorico,
    Tag,
    Task,
    User,
)


# =============================================================================
# QUERIES DE USUÁRIOS
# =============================================================================

@cached_query(timeout=300, key_prefix='users')
def get_active_users_with_tags() -> List[User]:
    """
    Retorna lista de usuários ativos com tags carregadas (eager loading).

    Cache: 5 minutos
    Performance: Evita N+1 queries ao acessar user.tags

    Returns:
        Lista de usuários ativos ordenados por nome
    """
    enable_eager = os.getenv('ENABLE_EAGER_LOADING', 'true').lower() == 'true'

    query = User.query.filter_by(ativo=True)

    if enable_eager:
        query = query.options(selectinload(User.tags))

    return query.order_by(User.name.asc(), User.username.asc()).all()


@cached_query(timeout=300, key_prefix='users')
def get_user_by_id_with_tags(user_id: int) -> Optional[User]:
    """
    Retorna usuário por ID com tags carregadas.

    Cache: 5 minutos
    Performance: Evita N+1 query ao acessar user.tags

    Args:
        user_id: ID do usuário

    Returns:
        Usuário ou None se não encontrado
    """
    enable_eager = os.getenv('ENABLE_EAGER_LOADING', 'true').lower() == 'true'

    query = User.query.filter_by(id=user_id)

    if enable_eager:
        query = query.options(selectinload(User.tags))

    return query.first()


# =============================================================================
# QUERIES DE EMPRESAS
# =============================================================================

def get_empresas_with_inventario(ativo=True, apply_eager=True):
    """
    Retorna empresas com inventário carregado (eager loading).

    Performance: Evita N+1 queries ao acessar empresa.inventario

    Args:
        ativo: Filtrar apenas empresas ativas (default: True)
        apply_eager: Aplicar eager loading (default: True, pode ser desabilitado via ENABLE_EAGER_LOADING)

    Returns:
        Query de empresas (não executada - permite adicionar filtros)
    """
    enable_eager = os.getenv('ENABLE_EAGER_LOADING', 'true').lower() == 'true'

    query = Empresa.query.filter_by(ativo=ativo) if ativo is not None else Empresa.query

    if apply_eager and enable_eager:
        # Use joinedload para relacionamento one-to-one (inventario)
        query = query.options(joinedload(Empresa.inventario))

    return query


def get_empresas_with_departamentos(ativo=True, apply_eager=True):
    """
    Retorna empresas com departamentos carregados (eager loading).

    Performance: Evita N+1 queries ao acessar empresa.departamentos

    Args:
        ativo: Filtrar apenas empresas ativas (default: True)
        apply_eager: Aplicar eager loading (default: True)

    Returns:
        Query de empresas (não executada - permite adicionar filtros)
    """
    enable_eager = os.getenv('ENABLE_EAGER_LOADING', 'true').lower() == 'true'

    query = Empresa.query.filter_by(ativo=ativo) if ativo is not None else Empresa.query

    if apply_eager and enable_eager:
        # Use selectinload para relacionamento one-to-many (departamentos)
        query = query.options(selectinload(Empresa.departamentos))

    return query


# =============================================================================
# QUERIES DE TASKS
# =============================================================================

def get_tasks_with_relationships(tag_id: Optional[int] = None, parent_id_is_null: bool = True):
    """
    Retorna tasks com relacionamentos carregados (eager loading).

    Performance: Evita múltiplas N+1 queries ao acessar:
    - task.tag
    - task.creator
    - task.assignee
    - task.finisher
    - task.children (e seus relacionamentos)

    Args:
        tag_id: Filtrar por tag (opcional)
        parent_id_is_null: Apenas tasks raiz (default: True)

    Returns:
        Query de tasks (não executada - permite adicionar filtros)
    """
    enable_eager = os.getenv('ENABLE_EAGER_LOADING', 'true').lower() == 'true'

    query = Task.query

    if tag_id is not None:
        query = query.filter_by(tag_id=tag_id)

    if parent_id_is_null:
        query = query.filter(Task.parent_id.is_(None))

    if enable_eager:
        query = query.options(
            joinedload(Task.tag),
            joinedload(Task.creator),
            joinedload(Task.assignee),
            joinedload(Task.finisher),
            # Children já tem lazy='selectin' no modelo, mas podemos reforçar
            selectinload(Task.children).joinedload(Task.tag),
            selectinload(Task.children).joinedload(Task.creator),
            selectinload(Task.children).joinedload(Task.assignee),
            selectinload(Task.children).joinedload(Task.finisher),
        )

    return query


# =============================================================================
# QUERIES DE TAGS
# =============================================================================

@cached_query(timeout=600, key_prefix='tags')
def get_all_tags() -> List[Tag]:
    """
    Retorna todas as tags.

    Cache: 10 minutos
    Performance: Cache de resultado completo

    Returns:
        Lista de todas as tags ordenadas por nome
    """
    return Tag.query.order_by(Tag.nome).all()


@cached_query(timeout=600, key_prefix='tags')
def get_tag_by_id(tag_id: int) -> Optional[Tag]:
    """
    Retorna tag por ID.

    Cache: 10 minutos

    Args:
        tag_id: ID da tag

    Returns:
        Tag ou None se não encontrada
    """
    return Tag.query.get(tag_id)


# =============================================================================
# QUERIES DE INVENTÁRIO
# =============================================================================

def get_inventarios_by_empresa_ids(
    empresa_ids: List[int],
    status_filter: Optional[List[str]] = None,
    include_file_columns: bool = True,
):
    """
    Retorna inventários para lista de empresa IDs de forma otimizada.

    Performance: Usa IN clause ao invés de múltiplas queries individuais e defer de colunas pesadas.

    Args:
        empresa_ids: Lista de IDs de empresas
        status_filter: Filtro opcional de status (ex: ['ENCERRADO', 'FALTA ARQUIVO'])
        include_file_columns: Se False, adia o carregamento de colunas JSON com Base64.

    Returns:
        Dict mapeando empresa_id -> inventario
    """
    if not empresa_ids:
        return {}

    query = Inventario.query.filter(Inventario.empresa_id.in_(empresa_ids))

    if not include_file_columns:
        # Otimização: Adiar o carregamento das colunas JSON que podem conter Base64 pesado.
        # Isso evita consumo massivo de memória no servidor Flask.
        query = query.options(
            defer(Inventario.cfop_files),
            defer(Inventario.cfop_consolidado_files),
            defer(Inventario.cliente_files)
        )

    if status_filter:
        query = query.filter(Inventario.status.in_(status_filter))

    inventarios = query.all()

    # Retornar como dict para acesso O(1)
    return {inv.empresa_id: inv for inv in inventarios}


def get_inventario_file_counts_by_empresa_ids(empresa_ids: List[int]) -> dict[int, dict[str, int | bool]]:
    """
    Retorna metadados de anexos do inventário sem carregar payload base64.

    Return:
        {
            empresa_id: {
                "cfop": int,
                "cfop_consolidado": int,
                "cliente": int,
                "cliente_legacy": bool,
            }
        }
    """
    if not empresa_ids:
        return {}

    try:
        rows = (
            db.session.query(
                Inventario.empresa_id.label("empresa_id"),
                sa.func.coalesce(sa.func.json_length(Inventario.cfop_files), 0).label("cfop"),
                sa.func.coalesce(
                    sa.func.json_length(Inventario.cfop_consolidado_files), 0
                ).label("cfop_consolidado"),
                sa.func.coalesce(sa.func.json_length(Inventario.cliente_files), 0).label("cliente"),
                Inventario.cliente_pdf_path.label("cliente_pdf_path"),
            )
            .filter(Inventario.empresa_id.in_(empresa_ids))
            .all()
        )
        return {
            int(row.empresa_id): {
                "cfop": int(row.cfop or 0),
                "cfop_consolidado": int(row.cfop_consolidado or 0),
                "cliente": int(row.cliente or 0),
                "cliente_legacy": bool(row.cliente_pdf_path),
            }
            for row in rows
        }
    except SQLAlchemyError:
        # Fallback para bancos sem json_length: calcula no Python.
        # AVISO: Isso pode ser lento se as colunas JSON contiverem dados Base64 pesados.
        # Por isso, limitamos o carregamento apenas ao estritamente necessário.
        inventarios = (
            Inventario.query.options(
                load_only(
                    "empresa_id",
                    "cfop_files",
                    "cfop_consolidado_files",
                    "cliente_files",
                    "cliente_pdf_path",
                )
            )
            .filter(Inventario.empresa_id.in_(empresa_ids))
            .all()
        )
        return {
            int(inv.empresa_id): {
                "cfop": len(inv.cfop_files or []) if isinstance(inv.cfop_files, list) else 0,
                "cfop_consolidado": len(inv.cfop_consolidado_files or []) if isinstance(inv.cfop_consolidado_files, list) else 0,
                "cliente": len(inv.cliente_files or []) if isinstance(inv.cliente_files, list) else 0,
                "cliente_legacy": bool(inv.cliente_pdf_path),
            }
            for inv in inventarios
        }


# =============================================================================
# FUNÇÕES DE INVALIDAÇÃO DE CACHE
# =============================================================================

def invalidate_user_caches():
    """Invalida todos os caches relacionados a usuários."""
    from app.extensions.cache import invalidate_cache_pattern
    invalidate_cache_pattern('users:*')


def invalidate_tag_caches():
    """Invalida todos os caches relacionados a tags."""
    from app.extensions.cache import invalidate_cache_pattern
    invalidate_cache_pattern('tags:*')


def invalidate_task_caches():
    """Invalida todos os caches relacionados a tasks."""
    from app.extensions.cache import invalidate_cache_pattern
    invalidate_cache_pattern('tasks:*')


# =============================================================================
# QUERIES DE SOCIETÁRIO
# =============================================================================

def get_processos_with_history_counts(
    status_filter=None,
    exclude_status=None,
):
    """
    Retorna processos societarios com contagem de historico em query unica.

    Performance: Usa LEFT JOIN com subquery ao inves de 2 queries separadas.

    Args:
        status_filter: Filtrar por status especifico (opcional)
        exclude_status: Excluir status especifico (opcional)

    Returns:
        Tuple (processos, history_counts_dict)
    """
    from sqlalchemy import func as sa_func

    history_subq = (
        db.session.query(
            ProcessoSocietarioHistorico.processo_id,
            sa_func.count(ProcessoSocietarioHistorico.id).label("hist_count"),
        )
        .group_by(ProcessoSocietarioHistorico.processo_id)
        .subquery()
    )

    query = ProcessoSocietario.query
    if status_filter is not None:
        query = query.filter(ProcessoSocietario.status == status_filter)
    elif exclude_status is not None:
        query = query.filter(ProcessoSocietario.status != exclude_status)

    rows = (
        query
        .outerjoin(history_subq, ProcessoSocietario.id == history_subq.c.processo_id)
        .add_columns(sa_func.coalesce(history_subq.c.hist_count, 0).label("history_count"))
        .order_by(ProcessoSocietario.data_inicio.asc(), ProcessoSocietario.id.asc())
        .all()
    )

    processos = []
    history_counts = {}
    for processo, hist_count in rows:
        processos.append(processo)
        history_counts[processo.id] = int(hist_count)

    return processos, history_counts


# =============================================================================
# FUNÇÕES DE UTILIDADE
# =============================================================================

def is_eager_loading_enabled() -> bool:
    """Verifica se eager loading está habilitado via variável de ambiente."""
    return os.getenv('ENABLE_EAGER_LOADING', 'true').lower() == 'true'


def is_query_cache_enabled() -> bool:
    """Verifica se cache de queries está habilitado via variável de ambiente."""
    return os.getenv('ENABLE_QUERY_CACHE', 'true').lower() == 'true'
