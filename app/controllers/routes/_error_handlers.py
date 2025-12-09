"""
Handlers de erro centralizados para a aplicacao.

Este modulo centraliza o tratamento de erros HTTP e excecoes,
garantindo respostas consistentes para API e interface web.

Error Handlers:
    - 404: Recurso nao encontrado
    - 403: Acesso proibido
    - 429: Rate limit excedido
    - 500: Erro interno do servidor
    - SQLAlchemyError: Erros de banco de dados
    - RequestEntityTooLarge: Arquivo muito grande

Funcoes Auxiliares:
    - api_error_response: Resposta JSON padronizada para erros
    - web_error_redirect: Redirecionamento com flash message

Autor: Refatoracao automatizada
Data: 2024
"""

from flask import Flask, Response, current_app, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import RequestEntityTooLarge

from app import db


# =============================================================================
# FUNCOES AUXILIARES
# =============================================================================

def is_api_request() -> bool:
    """
    Verifica se a requisicao atual e para a API.

    Returns:
        bool: True se path comeca com /api/.
    """
    return request.path.startswith('/api/')


def api_error_response(error: str, status_code: int, message: str | None = None) -> tuple[Response, int]:
    """
    Cria resposta JSON padronizada para erros de API.

    Args:
        error: Tipo do erro (ex: "not_found", "forbidden").
        status_code: Codigo HTTP do erro.
        message: Mensagem descritiva opcional.

    Returns:
        tuple[Response, int]: Resposta JSON e codigo de status.
    """
    response_data = {
        "error": error,
        "status": status_code,
    }
    if message:
        response_data["message"] = message

    return jsonify(response_data), status_code


def web_error_redirect(message: str, category: str = "error", fallback_url: str | None = None) -> Response:
    """
    Redireciona com flash message para erros de interface web.

    Args:
        message: Mensagem a exibir para o usuario.
        category: Categoria do flash (error, warning, info, success).
        fallback_url: URL de fallback se referrer nao disponivel.

    Returns:
        Response: Redirecionamento HTTP.
    """
    flash(message, category)
    redirect_url = request.referrer or fallback_url or url_for('home')
    return redirect(redirect_url)


# =============================================================================
# REGISTRO DE ERROR HANDLERS
# =============================================================================

def register_error_handlers(app: Flask) -> None:
    """
    Registra todos os error handlers na aplicacao Flask.

    Args:
        app: Instancia da aplicacao Flask.

    Uso:
        from app.controllers.routes._error_handlers import register_error_handlers
        register_error_handlers(app)
    """

    @app.errorhandler(404)
    def handle_not_found(e):
        """
        Trata erros 404 - Recurso nao encontrado.

        Para API: Retorna JSON com erro.
        Para Web: Renderiza pagina 404.html.
        """
        if is_api_request():
            return api_error_response("Resource not found", 404)
        return render_template('errors/404.html'), 404

    @app.errorhandler(403)
    def handle_forbidden(e):
        """
        Trata erros 403 - Acesso proibido.

        Para API: Retorna JSON com erro.
        Para Web: Redireciona para home com mensagem.
        """
        if is_api_request():
            return api_error_response("Access forbidden", 403)
        flash("Voce nao tem permissao para acessar este recurso.", "error")
        return redirect(url_for('home'))

    @app.errorhandler(429)
    def handle_rate_limit(e):
        """
        Trata erros 429 - Rate limit excedido.

        Registra excecao e retorna mensagem apropriada.
        """
        from app.utils.logging_config import log_exception
        log_exception(e, request)

        if is_api_request():
            return api_error_response(
                "Rate limit exceeded",
                429,
                "Too many requests. Please wait before trying again."
            )

        return render_template('errors/429.html', retry_after=e.description), 429

    @app.errorhandler(500)
    def handle_internal_error(e):
        """
        Trata erros 500 - Erro interno do servidor.

        Registra excecao, faz rollback de transacoes pendentes
        e retorna mensagem apropriada.
        """
        from app.utils.logging_config import log_exception
        log_exception(e, request)

        # Rollback de transacoes pendentes
        db.session.rollback()

        if is_api_request():
            return api_error_response(
                "Internal server error",
                500,
                "An unexpected error occurred. Please try again later."
            )

        return render_template('errors/500.html'), 500

    @app.errorhandler(SQLAlchemyError)
    def handle_database_error(e):
        """
        Trata erros de banco de dados.

        Registra excecao, faz rollback da transacao falha
        e retorna mensagem apropriada.
        """
        from app.utils.logging_config import log_exception
        log_exception(e, request)

        # Rollback da transacao falha
        db.session.rollback()

        if is_api_request():
            return api_error_response(
                "Database error",
                500,
                "A database error occurred. Please try again."
            )

        flash("Erro ao processar sua solicitacao. Tente novamente.", "error")
        return redirect(request.referrer or url_for('home'))

    @app.errorhandler(RequestEntityTooLarge)
    def handle_large_file(e):
        """
        Trata erros de arquivo muito grande.

        Retorna mensagem com limite de tamanho configurado.
        """
        from app.utils.logging_config import log_exception
        log_exception(e, request)

        max_len = current_app.config.get("MAX_CONTENT_LENGTH")
        if max_len:
            limit_mb = max_len / (1024 * 1024)
            message = f"Arquivo excede o tamanho permitido ({limit_mb:.0f} MB)."
        else:
            message = "Arquivo excede o tamanho permitido."

        return jsonify({"error": message}), 413
