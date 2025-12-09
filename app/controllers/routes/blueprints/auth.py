"""
Blueprint para autenticacao.

Este modulo contem rotas para login, logout e OAuth com Google.

Rotas:
    - GET/POST /login: Login com usuario/senha
    - GET /logout: Logout do usuario
    - GET /login/google: Inicia OAuth com Google
    - GET /oauth2callback: Callback do OAuth Google
    - GET /cookies: Politica de cookies
    - GET /cookies/revoke: Revoga consentimento de cookies

Dependencias:
    - models: User, Session
    - forms: LoginForm
    - services: Google OAuth

Autor: Refatoracao automatizada
Data: 2024
"""

from datetime import datetime, timedelta
from uuid import uuid4
import secrets
import logging

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
import requests
from requests import Request
from google.oauth2 import id_token

from app import db, limiter
from app.forms import LoginForm
from app.models.tables import User, Session as DbSession
from app.controllers.routes._base import SAO_PAULO_TZ

# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

auth_bp = Blueprint('auth', __name__)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def build_google_flow(state=None):
    """
    Constroi o fluxo OAuth do Google.

    Args:
        state: Estado opcional para validacao de callback

    Returns:
        Flow: Objeto de fluxo OAuth configurado
    """
    from google_auth_oauthlib.flow import Flow

    client_config = {
        "web": {
            "client_id": current_app.config["GOOGLE_CLIENT_ID"],
            "client_secret": current_app.config["GOOGLE_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

    scopes = [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ]

    flow = Flow.from_client_config(
        client_config,
        scopes=scopes,
        state=state,
    )
    flow.redirect_uri = url_for("auth.google_callback", _external=True)

    return flow


def credentials_to_dict(credentials):
    """
    Converte credenciais do Google para dicionario serializavel.

    Args:
        credentials: Objeto de credenciais do Google OAuth

    Returns:
        dict: Dicionario com dados das credenciais
    """
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }


def normalize_scopes(scopes):
    """
    Converte escopos curtos do Google para equivalentes longos.

    Args:
        scopes: Lista de escopos OAuth

    Returns:
        list: Lista de escopos normalizados
    """
    fixed = []
    for s in scopes:
        if s == "email":
            fixed.append("https://www.googleapis.com/auth/userinfo.email")
        elif s == "profile":
            fixed.append("https://www.googleapis.com/auth/userinfo.profile")
        else:
            fixed.append(s)
    return fixed


def _determine_post_login_redirect(user: User) -> str:
    """
    Determina a URL de redirecionamento apos autenticacao.

    Args:
        user: Usuario autenticado

    Returns:
        str: URL para redirecionamento
    """
    if user.role == "admin":
        return url_for("tasks_overview")

    # Verifica se o usuario tem APENAS a tag "reuniao" (acesso restrito a sala)
    tags = getattr(user, "tags", None) or []
    if len(tags) == 1 and tags[0].nome.lower() == 'reunião':
        return url_for("sala_reunioes")

    first_tag = tags[0] if tags else None
    if first_tag:
        return url_for("tasks_sector", tag_id=first_tag.id)

    return url_for("home")


# =============================================================================
# ROTAS DE COOKIES
# =============================================================================

@auth_bp.route("/cookies")
def cookies():
    """Exibe a pagina de politica de cookies."""
    return render_template("cookie_policy.html")


@auth_bp.route("/cookies/revoke")
def revoke_cookies():
    """Revoga o consentimento de cookies e redireciona para index."""
    resp = redirect(url_for("index"))
    resp.delete_cookie("cookie_consent")
    flash("Consentimento de cookies revogado.", "info")
    return resp


# =============================================================================
# ROTAS DE LOGIN/LOGOUT
# =============================================================================

@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])  # Protecao contra brute-force
def login():
    """
    Renderiza a pagina de login e processa autenticacao.

    GET: Exibe formulario de login
    POST: Valida credenciais e cria sessao
    """
    from app.utils.audit import log_user_action, ActionType, ResourceType

    form = LoginForm()
    google_enabled = bool(
        current_app.config.get("GOOGLE_CLIENT_ID")
        and current_app.config.get("GOOGLE_CLIENT_SECRET")
    )

    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()

        if user and user.check_password(form.password.data):
            # Verifica se usuario esta ativo
            if not user.ativo:
                flash("Seu usuário está inativo. Contate o administrador.", "danger")
                # Log de tentativa de login (usuario inativo)
                user_actions_logger = logging.getLogger('user_actions')
                user_actions_logger.warning(
                    f"[{form.username.data}] FAILED_LOGIN session - Usuario inativo - IP: {request.remote_addr}",
                    extra={
                        'username': form.username.data,
                        'action_type': 'failed_login',
                        'resource_type': 'session',
                        'ip_address': request.remote_addr,
                        'reason': 'inactive_user',
                    }
                )
                return redirect(url_for("auth.login"))

            # Login bem-sucedido
            login_user(
                user,
                remember=form.remember_me.data,
                duration=timedelta(days=30),
            )
            session.permanent = form.remember_me.data
            sid = uuid4().hex
            session["sid"] = sid

            # Cria registro de sessao no banco
            db.session.add(
                DbSession(
                    session_id=sid,
                    user_id=user.id,
                    session_data=dict(session),
                    ip_address=request.remote_addr,
                    user_agent=request.headers.get("User-Agent"),
                    last_activity=datetime.now(SAO_PAULO_TZ),
                )
            )
            db.session.commit()

            # Log de login bem-sucedido
            log_user_action(
                action_type=ActionType.LOGIN,
                resource_type=ResourceType.SESSION,
                action_description=f'Usuario {user.username} fez login com sucesso',
                resource_id=user.id,
                new_values={'remember_me': form.remember_me.data}
            )

            flash("Login bem-sucedido!", "success")
            return redirect(_determine_post_login_redirect(user))
        else:
            # Log de tentativa de login falha
            user_actions_logger = logging.getLogger('user_actions')
            user_actions_logger.warning(
                f"[{form.username.data}] FAILED_LOGIN session - Credenciais invalidas - IP: {request.remote_addr}",
                extra={
                    'username': form.username.data,
                    'action_type': 'failed_login',
                    'resource_type': 'session',
                    'ip_address': request.remote_addr,
                    'reason': 'invalid_credentials',
                }
            )
            flash("Credenciais inválidas", "danger")

    return render_template("login.html", form=form, google_enabled=google_enabled)


@auth_bp.route("/logout", methods=["GET"])
@login_required
def logout():
    """Encerra a sessao do usuario atual."""
    from app.utils.audit import log_user_action, ActionType, ResourceType

    # Log de logout antes de efetivamente deslogar
    log_user_action(
        action_type=ActionType.LOGOUT,
        resource_type=ResourceType.SESSION,
        action_description=f'Usuario {current_user.username} fez logout',
        resource_id=current_user.id,
    )

    sid = session.get("sid")
    if sid:
        DbSession.query.filter_by(session_id=sid).delete()
        db.session.commit()
        session.pop("sid", None)

    logout_user()
    return redirect(url_for("index"))


# =============================================================================
# ROTAS DE OAUTH (GOOGLE)
# =============================================================================

@auth_bp.route("/login/google")
def google_login():
    """Inicia o fluxo de login OAuth com Google."""
    flow = build_google_flow()
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent"
    )
    session["oauth_state"] = state

    # google-auth anexa o PKCE verifier apenas na instancia do flow,
    # entao persistimos explicitamente para reutilizar no callback
    code_verifier = getattr(flow, "code_verifier", None)
    if isinstance(code_verifier, bytes):
        code_verifier = code_verifier.decode()
    if code_verifier:
        session["oauth_code_verifier"] = code_verifier
    else:
        session.pop("oauth_code_verifier", None)

    return redirect(authorization_url)


@auth_bp.route("/oauth2callback")
def google_callback():
    """
    Processa o callback do OAuth do Google.

    Valida o token, cria/atualiza usuario e inicia sessao.
    """
    error = request.args.get("error")
    if error:
        session.pop("oauth_state", None)
        session.pop("oauth_code_verifier", None)
        flash("O Google não autorizou o login solicitado.", "danger")
        return redirect(url_for("auth.login"))

    state = session.get("oauth_state")
    code_verifier = session.get("oauth_code_verifier")

    if state is None or state != request.args.get("state"):
        session.pop("oauth_state", None)
        session.pop("oauth_code_verifier", None)
        flash("Falha ao validar resposta do Google. Tente novamente.", "danger")
        return redirect(url_for("auth.login"))

    flow = build_google_flow(state=state)

    try:
        authorization_response = flow.redirect_uri or request.url
        if request.query_string:
            query_string = request.query_string.decode()
            separator = "&" if "?" in authorization_response else "?"
            authorization_response = f"{authorization_response}{separator}{query_string}"

        callback_scope = request.args.get("scope")
        fetch_kwargs = {"authorization_response": authorization_response}

        if callback_scope:
            normalized = normalize_scopes(callback_scope.split())
            fetch_kwargs["scope"] = normalized

        if code_verifier:
            fetch_kwargs["code_verifier"] = code_verifier

        flow.fetch_token(**fetch_kwargs)

    except Exception as exc:
        current_app.logger.exception(f"Falha no fetch_token: {exc}")
        flash("Não foi possível completar a autenticação com o Google.", "danger")
        return redirect(url_for("auth.login"))
    finally:
        session.pop("oauth_state", None)
        session.pop("oauth_code_verifier", None)

    credentials = flow.credentials
    request_session = requests.Session()
    token_request = Request(session=request_session)

    try:
        id_info = id_token.verify_oauth2_token(
            credentials.id_token,
            token_request,
            current_app.config["GOOGLE_CLIENT_ID"]
        )
    except ValueError:
        current_app.logger.exception("ID token do Google inválido durante login")
        flash("Não foi possível validar a resposta do Google.", "danger")
        return redirect(url_for("auth.login"))

    google_id = id_info.get("sub")
    email = id_info.get("email")
    name = id_info.get("name", email)

    # Busca usuario existente por google_id ou email
    user = User.query.filter(
        (User.google_id == google_id) | (User.email == email)
    ).first()

    # Cria novo usuario se nao existir
    if not user:
        base_username = email.split("@")[0]
        username = base_username
        counter = 1
        while User.query.filter_by(username=username).first():
            username = f"{base_username}{counter}"
            counter += 1
        user = User(username=username, email=email, name=name, google_id=google_id)
        random_password = secrets.token_hex(16)
        user.set_password(random_password)
        db.session.add(user)
        db.session.commit()

    # Atualiza refresh token se disponivel
    if credentials.refresh_token:
        user.google_refresh_token = credentials.refresh_token
        db.session.commit()

    # Inicia sessao
    login_user(user, remember=True, duration=timedelta(days=30))
    session.permanent = True
    sid = uuid4().hex
    session["sid"] = sid
    session["credentials"] = credentials_to_dict(credentials)

    db.session.add(
        DbSession(
            session_id=sid,
            user_id=user.id,
            session_data=dict(session),
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
            last_activity=datetime.now(SAO_PAULO_TZ),
        )
    )
    db.session.commit()

    flash("Login com Google bem-sucedido!", "success")
    return redirect(_determine_post_login_redirect(user))


# =============================================================================
# ALIASES PARA COMPATIBILIDADE
# Mantidos para que url_for('login'), url_for('logout'), etc. funcionem
# =============================================================================

# Nota: Os endpoints sao registrados com nomes do blueprint (auth.login, etc.)
# Para compatibilidade com templates antigos, registrar aliases no __init__.py
