"""Utility functions for standardized application logging."""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable

logger = logging.getLogger(__name__)


def log_acesso_dados(user_id: int, resource_type: str, resource_id: int) -> None:
    """Log sensitive data access events."""
    logger.info(
        "Acesso a dados sensiveis | user=%s | %s_id=%s",
        user_id,
        resource_type,
        resource_id,
    )


def log_autenticacao(ip: str, username: str, status: str) -> None:
    """Log authentication attempts and results."""
    level = logging.INFO if status.lower() in {"login", "logout", "sucesso"} else logging.WARNING
    logger.log(
        level,
        "Autenticacao | ip=%s | usuario=%s | status=%s",
        ip,
        username,
        status,
    )


def log_alteracao_dados(
    action: str,
    resource_type: str,
    resource_id: int,
    fields: Iterable[str],
    user_id: int,
) -> None:
    """Log creation, update or deletion of data."""
    logger.info(
        "Alteracao de dados | acao=%s | %s_id=%s | campos=%s | user=%s",
        action,
        resource_type,
        resource_id,
        list(fields),
        user_id,
    )


def log_erro_execucao(route: str, user_id: int | None, error: Exception) -> None:
    """Log runtime errors and exceptions."""
    logger.error(
        "Erro de execucao | rota=%s | user=%s",
        route,
        user_id,
        exc_info=error,
    )


def log_integracao_externa(
    service: str,
    payload: Dict[str, Any],
    status: str,
    company_id: int,
) -> None:
    """Log interactions with external APIs."""
    level = logging.INFO if status.lower() in {"sucesso", "ok", "200"} else logging.ERROR
    logger.log(
        level,
        "Integracao externa | servico=%s | empresa=%s | status=%s | payload=%s",
        service,
        company_id,
        status,
        payload,
    )


def log_acao_administrativa(admin_id: int, action: str) -> None:
    """Log administrative actions."""
    logger.info("Acao administrativa | admin=%s | acao=%s", admin_id, action)
