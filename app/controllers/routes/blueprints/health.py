"""
Blueprint para health checks e endpoints de infraestrutura.

Este modulo contem rotas utilizadas para verificacao de saude da aplicacao,
manutencao de sessao e suporte a PWA (Progressive Web App).

Rotas:
    - GET /ping: Keep-alive para sessao ativa
    - GET /offline: Pagina de fallback offline para PWA
    - GET /sw.js: Service worker para PWA

Dependencias:
    - Nenhuma dependencia de models
    - Usa session do Flask para verificacao

Autor: Refatoracao automatizada
Data: 2024
"""

from flask import Blueprint, render_template, send_from_directory, session

from app import app, csrf, limiter


# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

health_bp = Blueprint('health', __name__)


# =============================================================================
# ROTAS
# =============================================================================

@health_bp.route("/ping")
@limiter.exempt  # Health check - nao aplica rate limit para evitar falsos positivos
def ping():
    """
    Endpoint leve para manter sessao ativa sem acessar ORM.

    Utilizado pelo frontend para evitar timeout de sessao em
    paginas com interacao prolongada (ex: formularios longos).

    Returns:
        204: Sessao valida e atualizada
        401: Sessao invalida ou expirada
    """
    if "_user_id" not in session:
        return ("", 401)
    session.modified = True
    return ("", 204)


@health_bp.route("/offline")
def offline_page():
    """
    Pagina de fallback offline para PWA.

    Exibida pelo service worker quando usuario esta offline
    e tenta acessar pagina nao cacheada.

    Returns:
        200: Pagina HTML offline com Cache-Control no-store
    """
    response = render_template("offline.html")
    return response, 200, {"Cache-Control": "no-store"}


@health_bp.route("/sw.js")
@csrf.exempt
def service_worker():
    """
    Serve service worker com escopo raiz.

    O service worker precisa ser servido com escopo raiz (/)
    para controlar toda a aplicacao PWA.

    Headers especiais:
        - Content-Type: application/javascript
        - Cache-Control: no-cache (sempre busca versao atualizada)
        - Service-Worker-Allowed: / (permite controle de toda app)

    Returns:
        200: Arquivo JavaScript do service worker
    """
    response = send_from_directory(app.static_folder, "sw.js")
    response.headers["Content-Type"] = "application/javascript"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"
    return response
