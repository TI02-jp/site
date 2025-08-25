import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def log_acesso_dados(user_id: int, resource_id: int, resource_type: str = "") -> None:
    msg = f"ACESSO_DADOS user={user_id} resource={resource_type}:{resource_id}"
    logger.info(msg)


def log_autenticacao(ip: str, username: str | None, status: str) -> None:
    msg = f"AUTENTICACAO ip={ip} user={username} status={status}"
    if status.lower() in ("falha", "failure", "error"):
        logger.warning(msg)
    else:
        logger.info(msg)


def log_alteracao_dados(user_id: int, resource_id: int, changes: Any, action: str) -> None:
    msg = f"ALTERACAO_DADOS action={action} user={user_id} resource={resource_id} changes={changes}"
    logger.info(msg)


def log_erro_execucao(exception: Exception, route: str, user_id: int | None = None) -> None:
    msg = f"ERRO_EXECUCAO route={route} user={user_id}"
    logger.error(msg, exc_info=exception)


def log_integracao_externa(payload: Dict[str, Any], status: Any, company_id: Any) -> None:
    msg = f"INTEGRACAO_EXTERNA company={company_id} status={status} payload={payload}"
    if isinstance(status, int) and status >= 400:
        logger.error(msg)
    else:
        logger.info(msg)


def log_acao_administrativa(admin_id: int, action: str) -> None:
    msg = f"ACAO_ADMINISTRATIVA admin={admin_id} action={action}"
    logger.info(msg)
