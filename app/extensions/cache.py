"""Centralized cache extension."""

from __future__ import annotations

import os
from typing import Any, Dict

from flask import current_app
from flask_caching import Cache

cache = Cache()


def init_cache(app) -> None:
    """Initialize the cache backing store based on environment configuration."""
    default_timeout = int(os.getenv("CACHE_DEFAULT_TIMEOUT", "120"))
    redis_url = os.getenv("REDIS_URL")
    config: Dict[str, Any] = {
        "CACHE_DEFAULT_TIMEOUT": default_timeout,
        "CACHE_KEY_PREFIX": os.getenv("CACHE_KEY_PREFIX", "portal"),
    }

    if redis_url:
        config.update(
            {
                "CACHE_TYPE": "RedisCache",
                "CACHE_REDIS_URL": redis_url,
                "CACHE_IGNORE_ERRORS": True,
            }
        )
    else:
        # SimpleCache keeps everything in-process; suitable as a development fallback.
        config.update({"CACHE_TYPE": "SimpleCache"})

    app.config.setdefault("CACHE_TYPE", config["CACHE_TYPE"])
    app.config.setdefault("CACHE_DEFAULT_TIMEOUT", config["CACHE_DEFAULT_TIMEOUT"])
    app.config.setdefault("CACHE_KEY_PREFIX", config["CACHE_KEY_PREFIX"])

    if redis_url:
        app.config.setdefault("CACHE_REDIS_URL", config["CACHE_REDIS_URL"])
        app.config.setdefault("CACHE_IGNORE_ERRORS", config["CACHE_IGNORE_ERRORS"])

    cache.init_app(app)


def get_cache_timeout(config_key: str, default: int) -> int:
    """Helper to read cache TTLs from app config when inside an app context."""
    if current_app:
        return int(current_app.config.get(config_key, default))
    return default


# =============================================================================
# Query Caching Utilities (Performance Optimization)
# =============================================================================

from functools import wraps
from flask import request


def cached_query(timeout=300, key_prefix='query', unless=None):
    """
    Decorator para cachear resultado de queries.

    Args:
        timeout: Tempo de cache em segundos (padrão 5 min)
        key_prefix: Prefixo da chave de cache
        unless: Função que retorna True para não cachear

    Example:
        @cached_query(timeout=600, key_prefix='empresas')
        def get_active_empresas():
            return Empresa.query.filter_by(ativo=True).all()
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Não cachear em métodos de escrita
            if unless and unless():
                return f(*args, **kwargs)

            # Gerar chave de cache baseada em função e argumentos
            cache_key = f"{key_prefix}:{f.__name__}"

            # Adicionar argumentos à chave para cache único por parâmetro
            if args:
                cache_key += f":{str(args)}"
            if kwargs:
                cache_key += f":{str(sorted(kwargs.items()))}"

            # Tentar obter do cache
            result = cache.get(cache_key)
            if result is not None:
                return result

            # Executar query e cachear resultado
            result = f(*args, **kwargs)
            cache.set(cache_key, result, timeout=timeout)
            return result

        # Adicionar método para invalidar cache manualmente
        decorated_function.invalidate_cache = lambda: cache.delete_memoized(f)
        decorated_function.__cache_key_prefix__ = key_prefix

        return decorated_function
    return decorator


def invalidate_cache_on_mutation(cache_functions):
    """
    Decorator para invalidar caches em operações de escrita (POST, PUT, DELETE, PATCH).

    Args:
        cache_functions: Lista de funções com cache para invalidar

    Example:
        @invalidate_cache_on_mutation([get_active_empresas, get_empresas_count])
        @bp.route('/empresas/create', methods=['POST'])
        def create_empresa():
            # Criar empresa
            # Caches serão automaticamente invalidados após criação
            pass
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Executar função original
            result = f(*args, **kwargs)

            # Invalidar caches após mutação bem-sucedida
            if request.method in ['POST', 'PUT', 'DELETE', 'PATCH']:
                for cache_func in cache_functions:
                    if hasattr(cache_func, 'invalidate_cache'):
                        try:
                            cache_func.invalidate_cache()
                        except Exception:
                            # Não falhar se invalidação falhar
                            pass

            return result
        return decorated_function
    return decorator


def invalidate_cache_pattern(pattern: str):
    """
    Invalidar todas as chaves de cache que correspondem a um padrão.

    Args:
        pattern: Padrão glob para chaves (ex: 'empresas:*', 'query:get_*')

    Example:
        invalidate_cache_pattern('empresas:*')
    """
    try:
        if hasattr(cache.cache, '_client'):  # Redis
            # Redis backend
            keys = cache.cache._client.keys(f"{cache.config['CACHE_KEY_PREFIX']}:{pattern}")
            if keys:
                cache.cache._client.delete(*keys)
        else:
            # SimpleCache - não tem suporte a padrões, limpar tudo
            cache.clear()
    except Exception:
        # Não falhar se invalidação falhar
        pass
