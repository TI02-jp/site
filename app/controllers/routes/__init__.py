"""
Handlers de rotas Flask para a aplicacao web JP Contabil Portal.

Este modulo contem as rotas principais da aplicacao. Durante a refatoracao,
as rotas estao sendo migradas para blueprints separados em:
    app/controllers/routes/blueprints/

ROTAS JA MIGRADAS (em blueprints/):
    - /ping, /offline, /sw.js -> blueprints/health.py
    - /upload_image, /upload_file -> blueprints/uploads.py
    - /tags/* -> blueprints/tags.py
    - /procedimentos/* -> blueprints/procedimentos.py
    - /acessos/* -> blueprints/acessos.py

ROTAS PENDENTES DE MIGRACAO:
    - Autenticacao: /login, /logout, OAuth
    - Cursos: /cursos
    - Consultorias: /consultorias/*, /setores/*
    - Calendario: /calendario-colaboradores
    - Diretoria: /diretoria/*
    - Notificacoes: /notifications/*
    - Notas: /notas-debito/*
    - Reunioes: /sala-reunioes/*
    - Relatorios: /relatorios/*
    - Usuarios: /users/*
    - Tarefas: /tarefas/*
    - Empresas: /empresas/*

ARQUIVOS AUXILIARES CRIADOS:
    - _base.py: Helpers e constantes compartilhados
    - _decorators.py: Decorators de autorizacao
    - _error_handlers.py: Tratamento centralizado de erros
    - _validators.py: Validacoes de upload

Autor: Refatoracao automatizada
Data: 2024
"""

from flask import (
    render_template,
    redirect,
    url_for,
    flash,
    request,
    abort,
    jsonify,
    current_app,
    session,
    has_request_context,
    g,
    Response,
    stream_with_context,
    Flask,
    send_from_directory,
)
from functools import wraps
from collections import Counter, deque, defaultdict
import unicodedata
from flask_login import current_user, login_required, login_user, logout_user
from app import app, db, csrf, limiter
from app.extensions.cache import cache, get_cache_timeout
from app.extensions.task_queue import submit_io_task
from app.utils.security import sanitize_html
from app.utils.mailer import send_email, EmailDeliveryError
from app.utils.permissions import is_user_admin
try:
    from app.controllers.routes.blueprints.notas import can_access_controle_notas
except Exception:
    # Fallback to keep template context processors working if the notas blueprint cannot be imported
    def can_access_controle_notas() -> bool:
        return False
from itsdangerous import URLSafeSerializer, BadSignature
from app.models.tables import (
    User,
    Empresa,
    Departamento,
    Consultoria,
    Setor,
    Tag,
    ClienteReuniao,
    Inclusao,
    Session,
    SAO_PAULO_TZ,
    Reuniao,
    ReuniaoStatus,
    default_meet_settings,
    Task,
    TaskStatus,
    TaskPriority,
    TaskFollower,
    TaskStatusHistory,
    TaskNotification,
    NotificationType,
    TaskAttachment,
    TaskResponse,
    TaskResponseParticipant,
    AccessLink,
    Course,
    CourseTag,
    ReportPermission,
    DiretoriaEvent,
    DiretoriaAgreement,
    DiretoriaFeedback,
    GeneralCalendarEvent,
    NotaDebito,
    CadastroNota,
    NotaRecorrente,
    OperationalProcedure,
)
from app.forms import (
    # Formulários de autenticação
    LoginForm,
    RegistrationForm,
    # Demais formulários da aplicação
    EmpresaForm,
    EditUserForm,
    DepartamentoFiscalForm,
    DepartamentoContabilForm,
    DepartamentoPessoalForm,
    DepartamentoAdministrativoForm,
    DepartamentoFinanceiroForm,
    ConsultoriaForm,
    SetorForm,
    TagForm,
    TagDeleteForm,
    MeetingForm,
    MeetConfigurationForm,
    GeneralCalendarEventForm,
    TaskForm,
    AccessLinkForm,
    CourseForm,
    CourseTagForm,
    DiretoriaAcordoForm,
    DiretoriaFeedbackForm,
    OperationalProcedureForm,
    NotaDebitoForm,
    CadastroNotaForm,
    NotaRecorrenteForm,
    PAGAMENTO_CHOICES,
    ClienteReuniaoForm,
)
import os, json, re, secrets, imghdr, time, calendar
import requests
from urllib.parse import urlparse
from werkzeug.utils import secure_filename
from uuid import uuid4
from sqlalchemy import or_, cast, String, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError, OperationalError
import sqlalchemy as sa
from sqlalchemy.orm import joinedload, aliased
from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token
from google.auth.transport.requests import Request
from app.services.cnpj import consultar_cnpj
from app.services.courses import CourseStatus, get_courses_overview
from app.services.google_calendar import get_calendar_timezone
from app.services.calendar_cache import calendar_cache
from app.services.meeting_room import (
    populate_participants_choices,
    fetch_raw_events,
    create_meeting_and_event,
    update_meeting,
    combine_events,
    delete_meeting,
    update_meeting_configuration,
    change_meeting_status,
    serialize_meeting_event,
    invalidate_calendar_cache,
    STATUS_SEQUENCE,
    get_status_label,
    MeetingStatusConflictError,
    RESCHEDULE_REQUIRED_STATUSES,
)
from app.services.general_calendar import (
    populate_event_participants as populate_general_event_participants,
    create_calendar_event_from_form,
    update_calendar_event_from_form,
    delete_calendar_event,
    serialize_events_for_calendar,
)
from werkzeug.exceptions import RequestEntityTooLarge
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from math import ceil
from typing import Any, Iterable, Optional
from pathlib import Path
from markupsafe import Markup, escape
from app.utils.performance_middleware import track_custom_span
from urllib.parse import urlunsplit
from mimetypes import guess_type

def utc3_now() -> datetime:
    """Return current datetime in São Paulo timezone."""

    return datetime.now(SAO_PAULO_TZ).replace(tzinfo=None)

GOOGLE_OAUTH_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/calendar",
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.addons.current.message.action",
    "https://www.googleapis.com/auth/gmail.addons.current.action.compose",
]


EXCLUDED_TASK_TAGS = ["Reunião"]
EXCLUDED_TASK_TAGS_LOWER = {t.lower() for t in EXCLUDED_TASK_TAGS}
PERSONAL_TAG_PREFIX = "__personal__"


TASKS_UPLOAD_SUBDIR = os.path.join("uploads", "tasks")


ACESSOS_CATEGORIES: dict[str, dict[str, Any]] = {
    "fiscal": {
        "title": "Fiscal",
        "description": "Sistemas utilizados pela equipe fiscal para gestão de obrigações e documentos.",
        "icon": "bi bi-clipboard-data",
    },
    "contabil": {
        "title": "Contábil",
        "description": "Ferramentas que apoiam a rotina contábil e o envio de documentos.",
        "icon": "bi bi-journal-check",
    },
    "pessoal": {
        "title": "Pessoal",
        "description": "Portais e ferramentas de apoio às rotinas de Departamento Pessoal e RH.",
        "icon": "bi bi-people",
    },
}


# =============================================================================
# CONSTANTES MIGRADAS PARA blueprints/diretoria.py (2024-12)
# =============================================================================
# EVENT_TYPE_LABELS = {...}
# EVENT_AUDIENCE_LABELS = {...}
# EVENT_CATEGORY_LABELS = {...}
# =============================================================================


def register_blueprints(flask_app: Flask) -> None:
    """
    Registra todos os blueprints da aplicacao.

    Esta funcao e chamada durante a inicializacao da aplicacao e registra:
    - Blueprints existentes (announcements, api)
    - Novos blueprints refatorados (health, uploads, tags, procedimentos, acessos)

    Os blueprints novos sao registrados sem url_prefix para manter
    compatibilidade com templates existentes.

    Args:
        flask_app: Instancia da aplicacao Flask.
    """
    # =========================================================================
    # BLUEPRINTS EXISTENTES
    # =========================================================================
    from app.controllers.routes.announcements import announcements_bp
    from app.controllers.routes.api import api_bp

    flask_app.register_blueprint(announcements_bp)
    flask_app.register_blueprint(api_bp)

    # =========================================================================
    # NOVOS BLUEPRINTS REFATORADOS
    # =========================================================================
    from app.controllers.routes.blueprints import register_all_blueprints
    register_all_blueprints(flask_app)

    # =========================================================================
    # ALIASES LEGADOS PARA COMPATIBILIDADE
    # =========================================================================
    # Adiciona endpoints sem prefixo de blueprint para manter templates funcionando
    for rule in list(flask_app.url_map.iter_rules()):
        if not rule.endpoint.startswith("announcements."):
            continue
        legacy_endpoint = rule.endpoint.split(".", 1)[1]
        if legacy_endpoint in flask_app.view_functions:
            continue
        view_func = flask_app.view_functions[rule.endpoint]
        flask_app.add_url_rule(
            rule.rule,
            endpoint=legacy_endpoint,
            view_func=view_func,
            defaults=rule.defaults,
            methods=rule.methods,
            provide_automatic_options=False,
        )




# =============================================================================
# FUNCOES AUXILIARES MIGRADAS PARA blueprints/diretoria.py (2024-12)
# =============================================================================
# def _normalize_photo_entry(value: str) -> str | None:
#     """Migrado para blueprints/diretoria.py"""
#
# def _resolve_local_photo_path(normalized_photo_url: str) -> str | None:
#     """Migrado para blueprints/diretoria.py"""


def _save_task_file(uploaded_file) -> dict[str, str | None]:
    """Persist an uploaded file for a task and return its storage metadata."""

    original_name = secure_filename(uploaded_file.filename or "")
    extension = os.path.splitext(original_name)[1].lower()
    unique_name = f"{uuid4().hex}{extension}"

    upload_directory = os.path.join(
        current_app.root_path, "static", TASKS_UPLOAD_SUBDIR
    )
    os.makedirs(upload_directory, exist_ok=True)

    stored_path = os.path.join(upload_directory, unique_name)
    uploaded_file.save(stored_path)

    relative_path = os.path.join(TASKS_UPLOAD_SUBDIR, unique_name).replace(
        "\\", "/"
    )
    mime_type = uploaded_file.mimetype or guess_type(original_name)[0]

    return {
        "path": relative_path,
        "name": original_name or None,
        "mime_type": mime_type,
    }


# =============================================================================
# FUNCOES AUXILIARES MIGRADAS PARA blueprints/diretoria.py (2024-12)
# =============================================================================
# def _format_event_timestamp(raw_dt: datetime | None) -> str:
#     """Migrado para blueprints/diretoria.py"""
#
# def _cleanup_diretoria_photo_uploads(...):
#     """Migrado para blueprints/diretoria.py"""
#
# def parse_diretoria_event_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
#     """Migrado para blueprints/diretoria.py"""
# =============================================================================


# =============================================================================
# FUNÇÕES MIGRADAS PARA blueprints/auth.py (2024-12)
# =============================================================================
# def build_google_flow(state: str | None = None) -> Flow:
#     """Return a configured Google OAuth ``Flow`` instance."""
#     client_id = current_app.config.get("GOOGLE_CLIENT_ID")
#     client_secret = current_app.config.get("GOOGLE_CLIENT_SECRET")
#     if not (client_id and client_secret):
#         abort(404)
#
#     redirect_uri = get_google_redirect_uri()
#
#     flow = Flow.from_client_config(
#         {
#             "web": {
#                 "client_id": client_id,
#                 "client_secret": client_secret,
#                 "auth_uri": "https://accounts.google.com/o/oauth2/auth",
#                 "token_uri": "https://oauth2.googleapis.com/token",
#                 "redirect_uris": [redirect_uri],
#             }
#         },
#         scopes=GOOGLE_OAUTH_SCOPES,
#         state=state,
#     )
#     flow.redirect_uri = redirect_uri
#     return flow
#
#
# def get_google_redirect_uri() -> str:
#     """Return the redirect URI registered with Google."""
#
#     configured_uri = current_app.config.get("GOOGLE_REDIRECT_URI")
#     if configured_uri:
#         return configured_uri
#
#     callback_path = url_for("google_callback", _external=False)
#
#     if has_request_context():
#         scheme = request.scheme or "http"
#         host = request.host
#
#         forwarded = request.headers.get("Forwarded")
#         if forwarded:
#             forwarded = forwarded.split(",", 1)[0]
#             forwarded_parts = {}
#             for part in forwarded.split(";"):
#                 if "=" not in part:
#                     continue
#                 key, value = part.split("=", 1)
#                 forwarded_parts[key.strip().lower()] = value.strip().strip('"')
#             scheme = forwarded_parts.get("proto", scheme) or scheme
#             host = forwarded_parts.get("host", host) or host
#
#         forwarded_proto = request.headers.get("X-Forwarded-Proto")
#         if forwarded_proto:
#             scheme = forwarded_proto.split(",", 1)[0].strip() or scheme
#
#         forwarded_host = request.headers.get("X-Forwarded-Host")
#         if forwarded_host:
#             host = forwarded_host.split(",", 1)[0].strip() or host
#
#         forwarded_port = request.headers.get("X-Forwarded-Port")
#         if forwarded_port:
#             port = forwarded_port.split(",", 1)[0].strip()
#             if port:
#                 default_port = "443" if scheme == "https" else "80"
#                 if ":" not in host and port != default_port:
#                     host = f"{host}:{port}"
#
#         scheme = scheme or current_app.config.get("PREFERRED_URL_SCHEME", "http")
#         host = host or request.host
#
#         return urlunsplit((scheme, host, callback_path, "", ""))
#
#     scheme = current_app.config.get("PREFERRED_URL_SCHEME", "http")
#     server_name = current_app.config.get("SERVER_NAME")
#     if server_name:
#         return urlunsplit((scheme, server_name, callback_path, "", ""))
#
#     return url_for("google_callback", _external=True, _scheme=scheme)
# =============================================================================


def credentials_to_dict(credentials):
    """Convert Google credentials object to a serializable dict."""
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }


_STATS_CACHE_KEY_PREFIX = "portal:stats:"
_NOTIFICATION_COUNT_KEY_PREFIX = "portal:notifications:unread:"
_NOTIFICATION_VERSION_KEY = "portal:notifications:version"

@cache.memoize(timeout=get_cache_timeout("SETORES_CACHE_TIMEOUT", 300))
def _get_setores_catalog() -> list[Setor]:
    """Cached catalog of setores ordered by name."""
    return Setor.query.order_by(Setor.nome).all()


def _invalidate_setores_cache() -> None:
    """Clear cached setores catalog."""
    cache.delete_memoized(_get_setores_catalog)



# =============================================================================
# FUNCOES MIGRADAS PARA blueprints/cursos.py (2024-12)
# =============================================================================
# _get_course_tags_catalog() - Migrado para blueprints/cursos.py
# _invalidate_course_tags_cache() - Migrado para blueprints/cursos.py
# =============================================================================


@cache.memoize(timeout=get_cache_timeout("CONSULTORIAS_CACHE_TIMEOUT", 300))
def _get_consultorias_catalog() -> list[Consultoria]:
    """Cached catalog of consultorias ordered by name."""
    return Consultoria.query.order_by(Consultoria.nome).all()


def _invalidate_consultorias_cache() -> None:
    """Clear cached consultorias catalog."""
    cache.delete_memoized(_get_consultorias_catalog)





def _get_stats_cache_timeout() -> int:
    return get_cache_timeout("PORTAL_STATS_CACHE_TIMEOUT", 300)


# =============================================================================
# FUNÇÕES MIGRADAS PARA blueprints/notifications.py (2024-12)
# =============================================================================
# def _get_notification_cache_timeout() -> int:
#     return get_cache_timeout("NOTIFICATION_COUNT_CACHE_TIMEOUT", 60)
#
#
# def _get_notification_version() -> int:
#     version = cache.get(_NOTIFICATION_VERSION_KEY)
#     if version is None:
#         version = int(time.time())
#         _set_notification_version(int(version))
#     return int(version)
#
#
# def _set_notification_version(version: int) -> None:
#     ttl = max(_get_notification_cache_timeout(), 300)
#     cache.set(_NOTIFICATION_VERSION_KEY, int(version), timeout=ttl)
#
#
# def _notification_cache_key(user_id: int) -> str:
#     return f"{_NOTIFICATION_COUNT_KEY_PREFIX}{_get_notification_version()}:{user_id}"
# =============================================================================


def _get_cached_stats(include_admin_metrics: bool) -> dict[str, int]:
    """Return lightweight portal stats with a short-lived cache."""
    cache_key = f"{_STATS_CACHE_KEY_PREFIX}{'admin' if include_admin_metrics else 'basic'}"
    cached = cache.get(cache_key)
    if cached is not None:
        return dict(cached)

    stats: dict[str, int] = {
        "total_empresas": Empresa.query.count(),
        "total_usuarios": 0,
        "online_users_count": 0,
    }
    if include_admin_metrics:
        stats["total_usuarios"] = User.query.count()

    cache.set(cache_key, dict(stats), timeout=_get_stats_cache_timeout())
    return stats


# =============================================================================
# FUNÇÕES MIGRADAS PARA blueprints/notifications.py (2024-12)
# =============================================================================
# def _get_unread_notifications_count(user_id: int, allow_cache: bool = True) -> int:
#     """Retrieve unread notification count with centralized cache support."""
#     if allow_cache:
#         return _memoized_unread_notifications(user_id)
#
#     unread = TaskNotification.query.filter(
#         TaskNotification.user_id == user_id,
#         TaskNotification.read_at.is_(None),
#     ).count()
#     return int(unread)
#
#
#
# @cache.memoize(timeout=get_cache_timeout("NOTIFICATION_COUNT_CACHE_TIMEOUT", 60))
# def _memoized_unread_notifications(user_id: int) -> int:
#     """Memoized unread notification counter (keyed per user)."""
#     return int(
#         TaskNotification.query.filter(
#             TaskNotification.user_id == user_id,
#             TaskNotification.read_at.is_(None),
#         ).count()
#     )
#
#
# def _invalidate_notification_cache(user_id: Optional[int] = None) -> None:
#     """Drop cached unread counts for a specific user or everyone."""
#     if user_id is None:
#         cache.delete_memoized(_memoized_unread_notifications)
#         return
#     cache.delete_memoized(_memoized_unread_notifications, user_id)
# =============================================================================


@app.context_processor
def inject_stats():
    """Inject global statistics into templates without hammering the DB."""
    if not current_user.is_authenticated:
        return {}
    stats = _get_cached_stats(include_admin_metrics=current_user.role == "admin")
    if current_user.role != "admin":
        stats = dict(stats)
        stats["total_usuarios"] = 0
        stats["online_users_count"] = 0
    return stats


# Allowed image file extensions for uploads
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}
# Allowed file extensions for uploads (images + PDFs)
ALLOWED_EXTENSIONS_WITH_PDF = IMAGE_EXTENSIONS | {"pdf"}

IMAGE_SIGNATURE_MAP = {
    "jpeg": {"jpg", "jpeg"},
    "png": {"png"},
    "gif": {"gif"},
}
IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/gif"}
PDF_MIME_TYPES = {"application/pdf"}


def _peek_stream(filestorage, size=512):
    """Return the first ``size`` bytes of the upload without consuming the stream."""

    stream = filestorage.stream
    position = stream.tell()
    chunk = stream.read(size)
    stream.seek(position)
    return chunk


def _get_file_size_bytes(filestorage) -> int:
    """Return the size of the uploaded file without consuming the stream."""

    stream = filestorage.stream
    position = stream.tell()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(position)
    return size


def allowed_file(filename):
    """Check if a filename has an allowed image extension."""

    return "." in filename and filename.rsplit(".", 1)[1].lower() in IMAGE_EXTENSIONS


def allowed_file_with_pdf(filename):
    """Check if a filename has an allowed extension (including PDF)."""

    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS_WITH_PDF


def is_safe_image_upload(file):
    """Validate image uploads against MIME type and file signature."""

    filename = file.filename or ""
    extension = filename.rsplit(".", 1)[1].lower() if "." in filename else ""
    if extension not in IMAGE_EXTENSIONS:
        return False

    if file.mimetype not in IMAGE_MIME_TYPES:
        return False

    header = _peek_stream(file)
    detected = imghdr.what(None, header)
    if not detected:
        return False

    return extension in IMAGE_SIGNATURE_MAP.get(detected, set())


def is_safe_pdf_upload(file):
    """Validate PDF uploads by MIME type and header signature."""

    if file.mimetype not in PDF_MIME_TYPES:
        return False
    header = _peek_stream(file, size=5)
    return header.startswith(b"%PDF")


## ============================================================================
## ERROR HANDLERS - Production-grade error handling
## ============================================================================

@app.errorhandler(404)
def handle_not_found(e):
    """Handle 404 Not Found errors with friendly page."""
    if request.path.startswith('/api/'):
        return jsonify({"error": "Resource not found", "status": 404}), 404
    return render_template('errors/404.html'), 404

@app.errorhandler(403)
def handle_forbidden(e):
    """Handle 403 Forbidden errors."""
    if request.path.startswith('/api/'):
        return jsonify({"error": "Access forbidden", "status": 403}), 403
    flash("Você não tem permissão para acessar este recurso.", "error")
    return redirect(url_for('home'))

@app.errorhandler(429)
def handle_rate_limit(e):
    """Handle 429 Too Many Requests (rate limit exceeded)."""
    from app.utils.logging_config import log_exception
    log_exception(e, request)

    if request.path.startswith('/api/'):
        return jsonify({
            "error": "Rate limit exceeded",
            "status": 429,
            "message": "Too many requests. Please wait before trying again."
        }), 429

    return render_template('errors/429.html', retry_after=e.description), 429

@app.errorhandler(500)
def handle_internal_error(e):
    """Handle 500 Internal Server Error with logging."""
    from app.utils.logging_config import log_exception
    log_exception(e, request)

    # Rollback any pending database transactions
    db.session.rollback()

    if request.path.startswith('/api/'):
        return jsonify({
            "error": "Internal server error",
            "status": 500,
            "message": "An unexpected error occurred. Please try again later."
        }), 500

    return render_template('errors/500.html'), 500

@app.errorhandler(SQLAlchemyError)
def handle_database_error(e):
    """Handle database errors specifically."""
    from app.utils.logging_config import log_exception
    log_exception(e, request)

    # Rollback failed transaction
    db.session.rollback()

    if request.path.startswith('/api/'):
        return jsonify({
            "error": "Database error",
            "status": 500,
            "message": "A database error occurred. Please try again."
        }), 500

    flash("Erro ao processar sua solicitação. Tente novamente.", "error")
    return redirect(request.referrer or url_for('home'))

@app.errorhandler(RequestEntityTooLarge)
def handle_large_file(e):
    """Return JSON error when uploaded file exceeds limit."""
    from app.utils.logging_config import log_exception
    log_exception(e, request)

    max_len = current_app.config.get("MAX_CONTENT_LENGTH")
    if max_len:
        limit_mb = max_len / (1024 * 1024)
        message = f"Arquivo excede o tamanho permitido ({limit_mb:.0f} MB)."
    else:
        message = "Arquivo excede o tamanho permitido."
    return jsonify({"error": message}), 413


def format_phone(digits: str) -> str:
    """Format raw digit strings into phone numbers."""
    if len(digits) >= 11:
        return f"({digits[:2]}) {digits[2:7]}-{digits[7:11]}"
    if len(digits) >= 10:
        return f"({digits[:2]}) {digits[2:6]}-{digits[6:10]}"
    return digits


def normalize_contatos(contatos):
    """Normalize contact entries into a consistent structure."""
    if not contatos:
        return []
    if all(isinstance(c, dict) and "meios" in c for c in contatos):
        for c in contatos:
            meios = c.get("meios") or []
            for m in meios:
                if "valor" in m and "endereco" not in m:
                    m["endereco"] = m.pop("valor")
                if m.get("tipo") in ("telefone", "whatsapp"):
                    digits = re.sub(r"\D", "", m.get("endereco", ""))
                    m["endereco"] = format_phone(digits)
        return contatos
    grouped = {}
    for c in contatos:
        if not isinstance(c, dict):
            continue
        nome = c.get("nome", "")
        tipo = c.get("tipo")
        endereco = c.get("endereco") or c.get("valor", "")
        if tipo in ("telefone", "whatsapp"):
            digits = re.sub(r"\D", "", endereco)
            endereco = format_phone(digits)
        contato = grouped.setdefault(nome, {"nome": nome, "meios": []})
        contato["meios"].append({"tipo": tipo, "endereco": endereco})
    return list(grouped.values())

# =============================================================================
# ROTAS MIGRADAS PARA blueprints/uploads.py
# =============================================================================
# @app.route("/upload_image", methods=["POST"])
# @login_required
# def upload_image():
#     """Migrado para blueprints/uploads.py"""
#     pass

# @app.route("/upload_file", methods=["POST"])
# @login_required
# def upload_file():
#     """Migrado para blueprints/uploads.py"""
#     pass


def admin_required(f):
    """Decorator that restricts access to admin users."""

    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role != "admin":
            abort(403)
        return f(*args, **kwargs)

    return decorated_function


REPORT_DEFINITIONS: dict[str, dict[str, str]] = {
    "empresas": {"title": "Relatório de Empresas", "description": "Dados consolidados das empresas"},
    "fiscal": {"title": "Relatório Fiscal", "description": "Indicadores e obrigações fiscais"},
    "contabil": {"title": "Relatório Contábil", "description": "Visão contábil e controle de relatórios"},
    "usuarios": {"title": "Relatório de Usuários", "description": "Gestão e estatísticas de usuários"},
    "cursos": {"title": "Relatório de Cursos", "description": "Métricas do catálogo de treinamentos"},
    "tarefas": {"title": "Relatório de Tarefas", "description": "Painel de tarefas e indicadores"},
}


def _get_report_permissions_for_code(report_code: str) -> list[ReportPermission]:
    """Return stored permissions for a report code."""

    return (
        ReportPermission.query.filter(
            ReportPermission.report_code == report_code
        ).all()
    )


def has_report_access(report_code: str | None = None) -> bool:
    """
    Return True if current user can access the given report.

    Rules (in order):
    - admin or master users always allowed
    - if there are saved permissions for the report, only listed tags/users can acessar
    - otherwise falls back to legacy tags (Administrativo, Relatórios, Relatórios:<code>)
    """
    if current_user.role == "admin" or getattr(current_user, "is_master", False):
        return True
    if not current_user.is_authenticated:
        return False
    user_tag_ids = set(_get_accessible_tag_ids(current_user))

    if report_code is None:
        # Menu-level check: allow if user matches any stored permission across reports.
        any_permissions = ReportPermission.query.all()
        if any_permissions:
            for permission in any_permissions:
                if permission.user_id == current_user.id:
                    return True
                if permission.tag_id and permission.tag_id in user_tag_ids:
                    return True
            return False
        # If no stored permissions exist anywhere, fall back to legacy tags for the menu.
        return any(
            (tag.nome or "").lower() in {"relatorios", "relatórios"}
            for tag in current_user.tags
        )

    code = report_code or "index"
    stored_permissions = _get_report_permissions_for_code(code)

    if stored_permissions:
        for permission in stored_permissions:
            if permission.user_id == current_user.id:
                return True
            if permission.tag_id and permission.tag_id in user_tag_ids:
                return True
        return False

    # Fallback legacy tags (removing o acesso global da tag Administrativo)
    allowed_tags = {"relatorios", "relatórios"}
    if code:
        allowed_tags.add(f"relatórios:{code}".lower())
        allowed_tags.add(f"relatorios:{code}".lower())
    return any((tag.nome or "").lower() in allowed_tags for tag in current_user.tags)


def report_access_required(report_code: str | None = None):
    """Decorator that allows admin or tagged users to access reports."""

    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if has_report_access(report_code):
                return f(*args, **kwargs)
            abort(403)

        return decorated_function

    return decorator


_id_serializers: dict[str, URLSafeSerializer] = {}


def _require_master_admin() -> None:
    """Abort with 403 if current user is not admin or master."""

    if current_user.role != "admin" and not getattr(current_user, "is_master", False):
        abort(403)


@app.route("/relatorios/permissoes", methods=["GET", "POST"])
@login_required
def report_permissions():
    """Manage report access per tag for master/admin users."""

    _require_master_admin()

    tags = Tag.query.order_by(sa.func.lower(Tag.nome)).all()
    existing_permissions = ReportPermission.query.filter(
        ReportPermission.user_id.is_(None)
    ).all()

    permitted_by_report: dict[str, set[int]] = {
        code: set() for code in REPORT_DEFINITIONS
    }
    for permission in existing_permissions:
        if permission.tag_id is None:
            continue
        permitted_by_report.setdefault(permission.report_code, set()).add(
            permission.tag_id
        )

    if request.method == "POST":
        for code in REPORT_DEFINITIONS:
            submitted_tag_ids: set[int] = set()
            for raw in request.form.getlist(f"tags_{code}"):
                try:
                    submitted_tag_ids.add(int(raw))
                except (TypeError, ValueError):
                    continue

            db.session.query(ReportPermission).filter(
                ReportPermission.report_code == code,
                ReportPermission.user_id.is_(None),
            ).delete(synchronize_session=False)

            for tag_id in submitted_tag_ids:
                db.session.add(
                    ReportPermission(report_code=code, tag_id=tag_id)
                )

            permitted_by_report[code] = submitted_tag_ids

        db.session.commit()
        flash("Permissões de relatórios atualizadas com sucesso.", "success")
        return redirect(url_for("report_permissions"))

    return render_template(
        "admin/report_permissions.html",
        tags=tags,
        reports=REPORT_DEFINITIONS,
        permitted_by_report=permitted_by_report,
    )


def _get_id_serializer(namespace: str = "default") -> URLSafeSerializer:
    """Return a cached serializer for signed IDs."""

    secret = current_app.config.get("SECRET_KEY")
    if not secret:
        raise RuntimeError("SECRET_KEY is required to sign route identifiers.")

    if namespace not in _id_serializers:
        _id_serializers[namespace] = URLSafeSerializer(
            secret,
            salt=f"id-signer:{namespace}",
        )
    return _id_serializers[namespace]


def encode_id(value: int, namespace: str = "default") -> str:
    """Create a signed token for a numeric ID."""

    return _get_id_serializer(namespace).dumps(int(value))


def decode_id(token: str, namespace: str = "default", *, allow_plain_int: bool = True) -> int:
    """Decode a signed token back to its numeric ID."""

    cleaned = (token or "").strip()
    if not cleaned:
        abort(404)

    if allow_plain_int and cleaned.isdigit():
        return int(cleaned)

    try:
        value = _get_id_serializer(namespace).loads(cleaned)
    except BadSignature:
        abort(404)

    if not isinstance(value, int):
        abort(404)
    return value


def user_has_tag(tag_name: str) -> bool:
    """Return True if current user has a tag with the given name."""
    return any(tag.nome.lower() == tag_name.lower() for tag in current_user.tags)


# =============================================================================
# FUNÇÕES MIGRADAS PARA blueprints/notas.py
# =============================================================================
# def can_access_controle_notas() -> bool:
#     """Return True if current user can access Controle de Notas module."""
#     if not current_user.is_authenticated:
#         return False
#
#     # Admin always has access
#     if is_user_admin(current_user):
#         return True
#
#     # Check if user has Gestão, Financeiro or Emissor NFe tags
#     if user_has_tag('Gestão') or user_has_tag('Financeiro') or user_has_tag('Emissor NFe'):
#         return True
#
#     return False
#
#
# def can_access_notas_totalizador() -> bool:
#     """Return True if current user can access the Notas Totalizador view."""
#     if not current_user.is_authenticated:
#         return False
#
#     if is_user_admin(current_user):
#         return True
#
#     return user_has_tag('Financeiro')


def is_meeting_only_user() -> bool:
    """Return True if current user has ONLY the 'reunião' tag (meeting-only access)."""
    if not current_user.is_authenticated:
        return False

    # Admins are not meeting-only users
    if is_user_admin(current_user):
        return False

    user_tags = getattr(current_user, 'tags', []) or []
    if not user_tags:
        return False

    # Check if user has exactly one tag and it's 'reunião'
    # OR has multiple tags but only 'reunião' is the functional one
    has_reunion_tag = any(tag.nome.lower() == 'reunião' for tag in user_tags)

    if not has_reunion_tag:
        return False

    # If user has only reunião tag, they are meeting-only
    if len(user_tags) == 1:
        return True

    # If user has other tags besides reunião, they are NOT meeting-only
    return False


def meeting_only_access_check(f):
    """Decorator that blocks access for meeting-only users to routes other than meeting room."""

    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if is_meeting_only_user():
            flash('Você só tem acesso à Sala de Reuniões.', 'warning')
            return redirect(url_for('sala_reunioes'))
        return f(*args, **kwargs)

    return decorated_function


def _get_ti_tag() -> Tag | None:
    """Return the TI tag if it exists (cached per request)."""

    if not has_request_context():
        return Tag.query.filter(sa.func.lower(Tag.nome) == "ti").first()

    if not hasattr(g, "_ti_tag"):
        g._ti_tag = Tag.query.filter(sa.func.lower(Tag.nome) == "ti").first()
    return g._ti_tag


def _can_user_access_tag(tag: Tag | None, user: User | None = None) -> bool:
    """Return True if ``user`` (or the current user) may access ``tag``."""

    if tag is None:
        return False
    if user is None:
        user = current_user if current_user.is_authenticated else None
    if user is None:
        return False
    if getattr(user, "role", None) == "admin":
        return True
    # Check if it's a personal tag for this user
    if tag.nome.startswith(PERSONAL_TAG_PREFIX):
        expected_personal_tag = f"{PERSONAL_TAG_PREFIX}{user.id}"
        if tag.nome == expected_personal_tag:
            return True
    user_tags = getattr(user, "tags", []) or []
    if tag in user_tags:
        return True
    ti_tag = _get_ti_tag()
    return ti_tag is not None and tag.id == ti_tag.id


def _get_accessible_tag_ids(user: User | None = None) -> list[int]:
    """Return the tag IDs the given user can access."""

    if user is None:
        user = current_user if current_user.is_authenticated else None
    if user is None:
        return []
    ids = {t.id for t in getattr(user, "tags", []) or []}
    if getattr(user, "role", None) == "admin":
        return list(ids)
    # Include personal tag for this user
    personal_tag_name = f"{PERSONAL_TAG_PREFIX}{user.id}"
    personal_tag = Tag.query.filter_by(nome=personal_tag_name).first()
    if personal_tag:
        ids.add(personal_tag.id)
    ti_tag = _get_ti_tag()
    if ti_tag:
        ids.add(ti_tag.id)
    return list(ids)


@app.context_processor
def inject_user_tag_helpers():
    """Expose user tag helper utilities to templates."""
    return dict(
        user_has_tag=user_has_tag,
        can_access_controle_notas=can_access_controle_notas,
        is_meeting_only_user=is_meeting_only_user,
        has_report_access=has_report_access,
    )


@app.context_processor
def inject_task_tags():
    """Provide task-related tags for dynamic sidebar menus."""
    if not current_user.is_authenticated:
        return {"tasks_tags": []}
    cached = getattr(g, "_cached_tasks_tags", None)
    if cached is not None:
        return {"tasks_tags": cached}
    with track_custom_span("sidebar", "load_tasks_tags"):
        tags = sorted(
            [t for t in current_user.tags if t.nome.lower() not in EXCLUDED_TASK_TAGS_LOWER],
            key=lambda t: t.nome,
        )
    g._cached_tasks_tags = tags
    return {"tasks_tags": tags}


# =============================================================================
# CONTEXT PROCESSOR MIGRADO PARA blueprints/notifications.py (2024-12)
# =============================================================================
# @app.context_processor
# def inject_notification_counts():
#     """Expose the number of unread task notifications to templates."""
#
#     if not current_user.is_authenticated:
#         return {"unread_notifications_count": 0}
#     cached = getattr(g, "_cached_unread_notifications", None)
#     if cached is not None:
#         return {"unread_notifications_count": cached}
#     with track_custom_span("sidebar", "load_unread_notifications"):
#         unread = _get_unread_notifications_count(current_user.id)
#     g._cached_unread_notifications = unread
#     return {"unread_notifications_count": unread}
# =============================================================================

@app.route("/")
def index():
    """Redirect users to the appropriate first page."""
    if current_user.is_authenticated:
        if current_user.role == "admin":
            return redirect(url_for("tasks_overview"))
        first_tag = current_user.tags[0] if current_user.tags else None
        if first_tag:
            return redirect(url_for("tasks_sector", tag_id=first_tag.id))
        return redirect(url_for("home"))
    return redirect(url_for("login"))

@app.route("/home")
@login_required
@meeting_only_access_check
def home():
    """Render the authenticated home page."""
    with track_custom_span("template", "render_home"):
        return render_template("home.html")

# =============================================================================
# ROTAS MIGRADAS PARA blueprints/diretoria.py (2024-12)
# =============================================================================
# @app.route("/diretoria/acordos", methods=["GET", "POST"])
# @app.route("/diretoria/acordos/<int:agreement_id>/excluir", methods=["POST"])
# @app.route("/diretoria/feedbacks", methods=["GET", "POST"])
# @app.route("/diretoria/feedbacks/<int:feedback_id>/excluir", methods=["POST"])
# @app.route("/diretoria/eventos", methods=["GET", "POST"])
# @app.route("/diretoria/eventos/<int:event_id>/editar", methods=["GET", "POST"])
# @app.route("/diretoria/eventos/<int:event_id>/visualizar")
# @app.route("/diretoria/eventos/lista")
# @app.route("/diretoria/eventos/<int:event_id>/excluir", methods=["POST"])
# =============================================================================

# =============================================================================
# ROTA MIGRADA PARA blueprints/cursos.py (2024-12)
# =============================================================================
# @app.route("/cursos") - Migrado para blueprints/cursos.py
# =============================================================================


# =============================================================================
# ROTAS MIGRADAS PARA blueprints/acessos.py (2024-12)
# =============================================================================
# def _build_acessos_context(form, *, open_modal=False, page=1):
#     """Migrado para blueprints/acessos.py"""
#
# def _access_category_choices():
#     """Migrado para blueprints/acessos.py"""
#
# def _handle_access_shortcut_submission(form):
#     """Migrado para blueprints/acessos.py"""
#
# @app.route("/acessos") - Migrado para blueprints/acessos.py
# @app.route("/acessos/novo") - Migrado para blueprints/acessos.py
# @app.route("/acessos/<categoria_slug>") - Migrado para blueprints/acessos.py
# @app.route("/acessos/<categoria_slug>/novo") - Migrado para blueprints/acessos.py
# @app.route("/acessos/<int:link_id>/editar") - Migrado para blueprints/acessos.py
# @app.route("/acessos/<int:link_id>/excluir") - Migrado para blueprints/acessos.py
# =============================================================================

# =============================================================================
# ROTAS MIGRADAS PARA blueprints/procedimentos.py (2024-12)
# =============================================================================
# @app.route("/procedimentos") - Migrado para blueprints/procedimentos.py
# @app.route("/procedimentos/<int:proc_id>") - Migrado para blueprints/procedimentos.py
# @app.route("/procedimentos/<int:proc_id>/visualizar") - Migrado para blueprints/procedimentos.py
# @app.route("/procedimentos/<int:proc_id>/json") - Migrado para blueprints/procedimentos.py
# @app.route("/procedimentos/<int:proc_id>/editar") - Migrado para blueprints/procedimentos.py
# @app.route("/procedimentos/<int:proc_id>/excluir") - Migrado para blueprints/procedimentos.py
# @app.route("/procedimentos/search") - Migrado para blueprints/procedimentos.py
# =============================================================================

# =============================================================================
# ROTA MIGRADA PARA blueprints/health.py
# =============================================================================
# @app.route("/ping")
# @limiter.exempt
# def ping():
#     """Migrado para blueprints/health.py"""
#     pass


# =============================================================================
# FUNÇÕES MIGRADAS PARA blueprints/notifications.py (2024-12)
# =============================================================================
# def _serialize_notification(notification: TaskNotification) -> dict[str, Any]:
#     """Serialize a :class:`TaskNotification` into a JSON-friendly dict."""

#     raw_type = notification.type or NotificationType.TASK.value
#     try:
#         notification_type = NotificationType(raw_type)
#     except ValueError:
#         notification_type = NotificationType.TASK

#     message = (notification.message or "").strip() or None
#     action_label = None
#     target_url = None

#     if notification_type is NotificationType.ANNOUNCEMENT:
#         announcement = notification.announcement
#         if announcement:
#             if not message:
#                 subject = (announcement.subject or "").strip()
#                 if subject:
#                     message = f"Novo comunicado: {subject}"
#                 else:
#                     message = "Novo comunicado publicado."
#             target_url = url_for("announcements") + f"#announcement-{announcement.id}"
#         else:
#             if not message:
#                 message = "Comunicado removido."
#         action_label = "Abrir comunicado" if target_url else None
#     elif notification_type is NotificationType.RECURRING_INVOICE:
#         if not message:
#             message = "Emitir nota fiscal recorrente."
#         target_url = url_for("notas_recorrentes")
#         action_label = "Abrir notas recorrentes"
#     else:
#         task = notification.task
#         if task:
#             task_title = (task.title or "").strip()
#             query_params: dict[str, object] = {"highlight_task": task.id}
#             if notification_type is NotificationType.TASK_RESPONSE:
#                 query_params["open_responses"] = "1"
#             if task.is_private:
#                 if current_user.is_authenticated and _user_can_access_task(task, current_user):
#                     overview_endpoint = (
#                         "tasks_overview" if current_user.role == "admin" else "tasks_overview_mine"
#                     )
#                     target_url = url_for(overview_endpoint, **query_params) + f"#task-{task.id}"
#             else:
#                 target_url = url_for("tasks_sector", tag_id=task.tag_id, **query_params) + f"#task-{task.id}"
#             if not message:
#                 prefix = (
#                     "Tarefa atualizada"
#                     if notification_type is NotificationType.TASK
#                     else "Notificação"
#                 )
#                 if task_title:
#                     message = f"{prefix}: {task_title}"
#                 else:
#                     message = f"{prefix} atribuída a você."
#         else:
#             if not message:
#                 message = "Tarefa removida."
#         if not action_label:
#             if notification_type is NotificationType.TASK_RESPONSE:
#                 action_label = "Ver resposta" if target_url else None
#             else:
#                 action_label = "Abrir tarefa" if target_url else None

#     if not message:
#         message = "Atualização disponível."

#     created_at = notification.created_at or utc3_now()
#     if created_at.tzinfo is None:
#         # Notifications are stored in local (Sao Paulo) time as naive datetimes.
#         # Explicitly attach the timezone so the frontend receives the correct offset.
#         localized = created_at.replace(tzinfo=SAO_PAULO_TZ)
#     else:
#         localized = created_at.astimezone(SAO_PAULO_TZ)

#     created_at_iso = localized.isoformat()
#     display_dt = localized

#     return {
#         "id": notification.id,
#         "type": notification_type.value,
#         "message": message,
#         "created_at": created_at_iso,
#         "created_at_display": display_dt.strftime("%d/%m/%Y %H:%M"),
#         "is_read": notification.is_read,
#         "url": target_url,
#         "action_label": action_label,
#     }


# def _prune_old_notifications(retention_days: int = 60) -> int:
#     """Remove notifications older than the retention window."""

#     threshold = utc3_now() - timedelta(days=retention_days)
#     deleted = (
#         TaskNotification.query.filter(TaskNotification.created_at < threshold)
#         .delete(synchronize_session=False)
#     )
#     if deleted:
#         db.session.commit()
#     return deleted


# def _get_user_notification_items(limit: int | None = 20):
#     """Return serialized notifications and unread totals for the current user."""

#     _prune_old_notifications()
#     notifications_query = (
#         TaskNotification.query.filter(TaskNotification.user_id == current_user.id)
#         .options(
#             joinedload(TaskNotification.task).joinedload(Task.tag),
#             joinedload(TaskNotification.announcement),
#         )
#         .order_by(TaskNotification.created_at.desc())
#     )
#     if limit is not None:
#         notifications_query = notifications_query.limit(limit)
#     notifications = notifications_query.all()
#     unread_total = _get_unread_notifications_count(current_user.id)

#     items = []
#     for notification in notifications:
#         raw_type = notification.type or NotificationType.TASK.value
#         try:
#             notification_type = NotificationType(raw_type)
#         except ValueError:
#             notification_type = NotificationType.TASK

#         message = (notification.message or "").strip() or None
#         action_label = None
#         target_url = None

#         items.append(_serialize_notification(notification))

#     return items, unread_total

# =============================================================================



# =============================================================================
# ROTAS MIGRADAS PARA blueprints/notifications.py (2024-12)
# =============================================================================
# @app.route("/notifications", methods=["GET"])
# @login_required
# def list_notifications():
#     """Return the most recent notifications for the user."""

#     items, unread_total = _get_user_notification_items(limit=20)
#     return jsonify({"notifications": items, "unread": unread_total})

# @app.route("/notifications/stream")
# @login_required
# @limiter.exempt  # SSE connections remain open; exempt from standard rate limiting
# def notifications_stream():
#     """Server-Sent Events stream delivering real-time notifications."""
#     from app.services.realtime import get_broadcaster

#     since_id = request.args.get("since", type=int) or 0
#     batch_limit = current_app.config.get("NOTIFICATIONS_STREAM_BATCH", 50)
#     user_id = current_user.id

#     # Query DB once to get the initial last_sent_id, then release connection
#     if not since_id:
#         last_existing = (
#             TaskNotification.query.filter(TaskNotification.user_id == user_id)
#             .order_by(TaskNotification.id.desc())
#             .with_entities(TaskNotification.id)
#             .limit(1)
#             .scalar()
#         )
#         since_id = last_existing or 0

#     # CRITICAL: Release database connection before entering streaming loop
#     # This prevents connection pool exhaustion from long-running SSE connections
#     db.session.remove()

#     broadcaster = get_broadcaster()
#     client_id = broadcaster.register_client(user_id, subscribed_scopes={"notifications", "all"})
#     # Reduced heartbeat to 15s to prevent worker exhaustion (was 45s)
#     heartbeat_interval = current_app.config.get("NOTIFICATIONS_HEARTBEAT_INTERVAL", 15)

#     def event_stream() -> Any:
#         last_sent_id = since_id

#         try:
#             while True:
#                 # Check for new notifications in the database
#                 # We create a new session for each check to avoid holding connections
#                 new_notifications = (
#                     TaskNotification.query.filter(
#                         TaskNotification.user_id == user_id,
#                         TaskNotification.id > last_sent_id,
#                     )
#                     .options(
#                         joinedload(TaskNotification.task).joinedload(Task.tag),
#                         joinedload(TaskNotification.announcement),
#                     )
#                     .order_by(TaskNotification.id.asc())
#                     .limit(batch_limit)
#                     .all()
#                 )

#                 if new_notifications:
#                     serialized = [
#                         _serialize_notification(notification)
#                         for notification in new_notifications
#                     ]
#                     last_sent_id = max(notification.id for notification in new_notifications)
#                     # Use cache for unread count to reduce database queries
#                     unread_total = _get_unread_notifications_count(user_id, allow_cache=True)
#                     payload = json.dumps(
#                         {
#                             "notifications": serialized,
#                             "unread": unread_total,
#                             "last_id": last_sent_id,
#                         }
#                     )
#                     # Release DB connection immediately after query
#                     db.session.remove()
#                     yield f"data: {payload}\n\n"
#                 else:
#                     # No new notifications - release connection and send keep-alive
#                     db.session.remove()
#                     yield ": keep-alive\n\n"

#                 # Wait for broadcaster events or timeout
#                 # This doesn't hold a DB connection
#                 triggered = broadcaster.wait_for_events(
#                     user_id,
#                     client_id,
#                     timeout=heartbeat_interval,
#                 )

#                 # Small sleep to avoid busy-looping even after broadcast
#                 if triggered:
#                     time.sleep(0.5)  # Brief delay to batch notifications

#         except GeneratorExit:
#             broadcaster.unregister_client(user_id, client_id)
#             db.session.remove()
#             return
#         finally:
#             broadcaster.unregister_client(user_id, client_id)
#             db.session.remove()

#     response = Response(
#         stream_with_context(event_stream()),
#         mimetype="text/event-stream",
#     )
#     response.headers["Cache-Control"] = "no-cache"
#     response.headers["X-Accel-Buffering"] = "no"  # Disable nginx buffering
#     return response

# @app.route("/realtime/stream")
# @login_required
# @limiter.exempt  # SSE connections remain open; rate limiting causa reconexões agressivas
# def realtime_stream():
#     """Server-Sent Events stream for real-time system updates."""
#     from app.services.realtime import get_broadcaster

#     # Get subscribed scopes from query params (comma-separated)
#     scopes_param = request.args.get("scopes", "all")
#     subscribed_scopes = set(s.strip() for s in scopes_param.split(",") if s.strip())

#     user_id = current_user.id

#     # CRITICAL: Release database connection before entering streaming loop
#     # This prevents connection pool exhaustion from long-running SSE connections
#     db.session.remove()

#     broadcaster = get_broadcaster()
#     client_id = broadcaster.register_client(user_id, subscribed_scopes)
#     # Reduced heartbeat to 10s to prevent worker exhaustion (was 30s)
#     heartbeat_interval = current_app.config.get("REALTIME_HEARTBEAT_INTERVAL", 10)

#     def event_stream() -> Any:
#         try:
#             last_event_id = 0
#             while True:
#                 events = broadcaster.get_events(user_id, client_id, since_id=last_event_id)

#                 if events:
#                     for event in events:
#                         yield event.to_sse()
#                         last_event_id = max(last_event_id, event.id)
#                     continue

#                 triggered = broadcaster.wait_for_events(
#                     user_id,
#                     client_id,
#                     timeout=heartbeat_interval,
#                 )
#                 if not triggered:
#                     yield ": keep-alive\n\n"
#         except GeneratorExit:
#             broadcaster.unregister_client(user_id, client_id)
#             db.session.remove()
#             return
#         finally:
#             broadcaster.unregister_client(user_id, client_id)
#             db.session.remove()

#     response = Response(
#         stream_with_context(event_stream()),
#         mimetype="text/event-stream",
#     )
#     response.headers["Cache-Control"] = "no-cache"
#     response.headers["X-Accel-Buffering"] = "no"  # Disable nginx buffering
#     return response

# @app.route("/notificacoes")
# @login_required
# @meeting_only_access_check
# def notifications_center():
#     """Render the notification center page."""

#     items, unread_total = _get_user_notification_items(limit=50)
#     return render_template(
#         "notifications.html",
#         notifications=items,
#         unread_total=unread_total,
#     )

# @app.route("/notifications/<int:notification_id>/read", methods=["POST"])
# @login_required
# def mark_notification_read(notification_id):
#     """Mark a single notification as read."""

#     notification = TaskNotification.query.filter(
#         TaskNotification.id == notification_id,
#         TaskNotification.user_id == current_user.id,
#     ).first_or_404()
#     if not notification.read_at:
#         notification.read_at = utc3_now()
#         db.session.commit()
#         _invalidate_notification_cache(current_user.id)
#     return jsonify({"success": True})

# @app.route("/notifications/read-all", methods=["POST"])
# @login_required
# def mark_all_notifications_read():
#     """Mark all unread notifications for the current user as read."""

#     updated = (
#         TaskNotification.query.filter(
#             TaskNotification.user_id == current_user.id,
#             TaskNotification.read_at.is_(None),
#         ).update(
#             {TaskNotification.read_at: utc3_now()},
#             synchronize_session=False,
#         )
#     )
#     db.session.commit()
#     if updated:
#         _invalidate_notification_cache(current_user.id)
#     return jsonify({"success": True, "updated": updated or 0})


# @app.route("/notifications/subscribe", methods=["POST"])
# @login_required
# def subscribe_push_notifications():
#     """Subscribe to Web Push notifications."""
#     from app.models.tables import PushSubscription

#     data = request.get_json()
#     if not data:
#         return jsonify({"error": "Dados inválidos"}), 400

#     endpoint = data.get("endpoint")
#     keys = data.get("keys", {})
#     p256dh = keys.get("p256dh")
#     auth = keys.get("auth")

#     if not endpoint or not p256dh or not auth:
#         return jsonify({"error": "Dados de subscrição incompletos"}), 400

#     # Verificar se já existe uma subscrição para este endpoint
#     existing = PushSubscription.query.filter_by(endpoint=endpoint).first()

#     if existing:
#         # Atualizar usuário se mudou e timestamp
#         existing.user_id = current_user.id
#         existing.p256dh_key = p256dh
#         existing.auth_key = auth
#         existing.user_agent = request.headers.get("User-Agent", "")[:500]
#         existing.last_used_at = utc3_now()
#     else:
#         # Criar nova subscrição
#         subscription = PushSubscription(
#             user_id=current_user.id,
#             endpoint=endpoint,
#             p256dh_key=p256dh,
#             auth_key=auth,
#             user_agent=request.headers.get("User-Agent", "")[:500],
#         )
#         db.session.add(subscription)

#     try:
#         db.session.commit()
#         return jsonify({"success": True})
#     except Exception as e:
#         db.session.rollback()
#         return jsonify({"error": str(e)}), 500


# @app.route("/notifications/unsubscribe", methods=["POST"])
# @login_required
# def unsubscribe_push_notifications():
#     """Unsubscribe from Web Push notifications."""
#     from app.models.tables import PushSubscription

#     data = request.get_json()
#     if not data:
#         return jsonify({"error": "Dados inválidos"}), 400

#     endpoint = data.get("endpoint")
#     if not endpoint:
#         return jsonify({"error": "Endpoint não fornecido"}), 400

#     # Remover subscrição
#     PushSubscription.query.filter_by(
#         endpoint=endpoint,
#         user_id=current_user.id,
#     ).delete()

#     db.session.commit()
#     return jsonify({"success": True})


# @app.route("/notifications/vapid-public-key", methods=["GET"])
# def get_vapid_public_key():
#     """Return the VAPID public key for push subscription."""
#     public_key = os.getenv("VAPID_PUBLIC_KEY", "")
#     if not public_key:
#         return jsonify({"error": "VAPID não configurado"}), 500
#     return jsonify({"publicKey": public_key})


# @app.route("/notifications/test-push", methods=["POST"])
# @login_required
# def test_push_notification():
#     """Send a test push notification to the current user."""
    from app.services.push_notifications import test_push_notification as send_test

    result = send_test(current_user.id)
    return jsonify(result)


def _configure_consultoria_form(form: ConsultoriaForm) -> ConsultoriaForm:
    """Ensure consistent attributes for the consultoria form."""

    form.submit.label.text = "Salvar"
    render_kw = dict(form.senha.render_kw or {})
    render_kw["autocomplete"] = "off"
    form.senha.render_kw = render_kw
    return form

# =============================================================================
# ROTA MIGRADA PARA blueprints/consultorias.py (2024-12)
# =============================================================================
# @app.route("/consultorias") - Migrado para blueprints/consultorias.py
# =============================================================================

# =============================================================================
# ROTAS MIGRADAS PARA blueprints/calendario.py (2024-12)
# =============================================================================
# @app.route("/calendario-colaboradores") - Migrado para blueprints/calendario.py
# @app.route("/calendario-eventos/<int:event_id>/delete") - Migrado para blueprints/calendario.py
# =============================================================================


def _meeting_host_candidates(meeting: Reuniao) -> tuple[list[dict[str, Any]], str]:
    """Return possible Meet host options alongside the creator's name."""

    candidates: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for participant in meeting.participantes:
        participant_id = participant.id_usuario
        if participant_id in seen_ids:
            continue
        seen_ids.add(participant_id)
        name = (
            participant.usuario.name
            if participant.usuario and participant.usuario.name
            else participant.username_usuario
        )
        candidates.append(
            {
                "id": participant_id,
                "name": name,
                "username": participant.username_usuario,
                "email": participant.usuario.email if participant.usuario else None,
            }
        )
    creator_obj = meeting.criador
    creator_name = (
        creator_obj.name
        if creator_obj and creator_obj.name
        else (creator_obj.username if creator_obj else "")
    )
    candidates.sort(key=lambda entry: (entry["name"] or "").lower())
    return candidates, creator_name

@app.route("/sala-reunioes", methods=["GET", "POST"])
@login_required
@limiter.limit("10 per minute", methods=["GET"])  # Previne abuse de refresh/múltiplos cliques
def sala_reunioes():
    """List and create meetings using Google Calendar."""
    form = MeetingForm()
    meet_config_form = MeetConfigurationForm()
    populate_participants_choices(form)
    show_modal = False
    prefill_from_course = request.method == "GET" and request.args.get("course_calendar") == "1"
    if prefill_from_course:
        subject = (request.args.get("subject") or "").strip()
        if subject:
            form.subject.data = subject
        description = (request.args.get("description") or "").strip()
        if description:
            form.description.data = description
        date_raw = request.args.get("date")
        if date_raw:
            try:
                form.date.data = datetime.strptime(date_raw, "%Y-%m-%d").date()
            except ValueError:
                pass
        start_raw = request.args.get("start")
        if start_raw:
            try:
                form.start_time.data = datetime.strptime(start_raw, "%H:%M").time()
            except ValueError:
                pass
        end_raw = request.args.get("end")
        if end_raw:
            try:
                form.end_time.data = datetime.strptime(end_raw, "%H:%M").time()
            except ValueError:
                pass
        participant_ids: list[int] = []
        for raw_id in request.args.getlist("participants"):
            try:
                parsed_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if parsed_id in participant_ids:
                continue
            if any(choice_id == parsed_id for choice_id, _ in form.participants.choices):
                participant_ids.append(parsed_id)
        if participant_ids:
            form.participants.data = participant_ids
        if hasattr(form, "apply_more_days"):
            form.apply_more_days.data = False
        form.notify_attendees.data = True
        form.course_id.data = request.args.get("course_id", "")
        show_modal = True
        flash(
            "Revise os dados do curso, ajuste se necessário e confirme o agendamento da reunião.",
            "info",
        )
    raw_events = fetch_raw_events()
    calendar_tz = get_calendar_timezone()
    now = datetime.now(calendar_tz)
    is_creating_meeting_request = (
        request.method == "POST"
        and not form.meeting_id.data
        and bool(form.submit.data)
    )
    if is_creating_meeting_request:
        flash(
            "Estamos criando sua reunião. Aguarde alguns instantes enquanto finalizamos o agendamento.",
            "info",
        )

    if form.validate_on_submit():
        if form.meeting_id.data:
            meeting = Reuniao.query.get(int(form.meeting_id.data))
            # Admins (incluindo admin_master) podem editar qualquer reunião
            # Criadores só podem editar suas próprias reuniões
            can_edit_meeting = is_user_admin(current_user) or meeting.criador_id == current_user.id
            if meeting and can_edit_meeting:
                # Admins podem editar qualquer status; criadores têm restrições
                if not is_user_admin(current_user) and meeting.status in (
                    ReuniaoStatus.EM_ANDAMENTO,
                    ReuniaoStatus.REALIZADA,
                    ReuniaoStatus.CANCELADA,
                ):
                    flash(
                        "Reuniões em andamento, realizadas ou canceladas não podem ser editadas.",
                        "danger",
                    )
                    return redirect(url_for("sala_reunioes"))
                success, operation = update_meeting(form, raw_events, now, meeting)
                if success:
                    if operation and operation.meet_link:
                        session["meet_popup"] = {
                            "meeting_id": operation.meeting_id,
                            "meet_link": operation.meet_link,
                        }
                    return redirect(url_for("sala_reunioes"))
                show_modal = True
            else:
                flash(
                    "Você só pode editar reuniões que você criou.",
                    "danger",
                )
        else:
            success, operation = create_meeting_and_event(
                form, raw_events, now, current_user.id
            )
            if success:
                if operation and operation.meet_link:
                    session["meet_popup"] = {
                        "meeting_id": operation.meeting_id,
                        "meet_link": operation.meet_link,
                    }
                return redirect(url_for("sala_reunioes"))
            show_modal = True
    if request.method == "POST":
        show_modal = True
    meet_popup_payload = session.pop("meet_popup", None)
    meet_popup_data: dict[str, Any] | None = None
    if meet_popup_payload:
        meeting_id = meet_popup_payload.get("meeting_id")
        meet_link = meet_popup_payload.get("meet_link")
        if meeting_id and meet_link:
            meeting = Reuniao.query.get(meeting_id)
            if meeting and meeting.meet_link == meet_link:
                can_configure = (
                    is_user_admin(current_user)
                    or meeting.criador_id == current_user.id
                )
                participant_options, creator_name = _meeting_host_candidates(meeting)
                settings_dict = dict(meeting.meet_settings or default_meet_settings())
                meet_popup_data = {
                    "meeting_id": meeting.id,
                    "meet_link": meet_link,
                    "subject": meeting.assunto,
                    "host_candidates": participant_options,
                    "current_host_id": meeting.meet_host_id,
                    "meet_settings": settings_dict,
                    "creator_name": creator_name,
                    "can_configure": can_configure,
                }
    status_options = [
        {
            "value": status.value,
            "label": get_status_label(status),
        }
        for status in STATUS_SEQUENCE
    ]
    reschedule_statuses = [status.value for status in RESCHEDULE_REQUIRED_STATUSES]
    from datetime import date
    today_date = date.today().isoformat()
    return render_template(
        "sala_reunioes.html",
        form=form,
        meet_config_form=meet_config_form,
        show_modal=show_modal,
        calendar_timezone=calendar_tz.key,
        meet_popup_data=meet_popup_data,
        meeting_status_options=status_options,
        reschedule_statuses=reschedule_statuses,
        today_date=today_date,
    )

@app.route("/reuniao/<int:meeting_id>/meet-config", methods=["POST"])
@login_required
def configure_meet_call(meeting_id: int):
    """Persist configuration options for the Google Meet room."""

    meeting = Reuniao.query.get_or_404(meeting_id)
    # Admins (incluindo admin_master) ou criadores podem configurar o Meet
    if not is_user_admin(current_user) and meeting.criador_id != current_user.id:
        abort(403)
    form = MeetConfigurationForm()
    candidate_entries, creator_name = _meeting_host_candidates(meeting)
    host_choices = [(entry["id"], entry["name"]) for entry in candidate_entries]
    form.host_id.choices = host_choices
    form.meeting_id.data = str(meeting.id)
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if form.validate_on_submit():
        try:
            submitted_meeting_id = int(form.meeting_id.data)
        except (TypeError, ValueError):
            submitted_meeting_id = None
        if submitted_meeting_id != meeting.id:
            error_message = "Reunião inválida para configurar o Meet."
            form.meeting_id.errors.append(error_message)
        else:
            selected_host_raw = form.host_id.data
            allowed_host_ids = {choice for choice, _ in host_choices}
            if not allowed_host_ids:
                form.host_id.errors.append(
                    "Adicione participantes à reunião antes de definir o proprietário."
                )
            elif selected_host_raw not in allowed_host_ids:
                form.host_id.errors.append("Selecione um proprietário válido para o Meet.")
            else:
                host_id = selected_host_raw
                settings_payload = {
                    "quick_access_enabled": form.quick_access_enabled.data,
                    "mute_on_join": form.mute_on_join.data,
                    "allow_chat": form.allow_chat.data,
                    "allow_screen_share": form.allow_screen_share.data,
                }
                try:
                    (
                        normalized_settings,
                        host,
                        sync_result,
                    ) = update_meeting_configuration(
                        meeting, host_id, settings_payload
                    )
                except ValueError as exc:
                    form.host_id.errors.append(str(exc))
                else:
                    host_name = (
                        host.name or host.username
                        if host
                        else ""
                    )
                    warning_message = None
                    if sync_result is False:
                        warning_message = (
                            "Nao foi possivel aplicar as configuracoes do Meet automaticamente. "
                            "Verifique manualmente na sala do Google Meet."
                        )
                    elif sync_result is None and meeting.meet_link:
                        warning_message = (
                            "As configuracoes do Meet estao sendo aplicadas em segundo plano. "
                            "Verifique a sala do Google Meet em alguns instantes."
                        )
                    response_payload = {
                        "success": True,
                        "message": "Configuracoes do Meet atualizadas com sucesso!",
                        "meet_settings": normalized_settings,
                        "meet_host": {
                            "id": host.id if host else None,
                            "name": host_name,
                        },
                        "meet_settings_applied": bool(sync_result) or meeting.meet_link is None,
                        "meet_settings_pending": sync_result is None and meeting.meet_link is not None,
                    }
                    if warning_message:
                        response_payload["warning"] = warning_message
                    if is_ajax:
                        return jsonify(response_payload)
                    if warning_message:
                        flash(warning_message, "warning")
                    flash(response_payload["message"], "success")
                    return redirect(url_for("sala_reunioes"))

    if is_ajax:
        return jsonify({"success": False, "errors": form.errors}), 400
    for field_errors in form.errors.values():
        for error in field_errors:
            flash(error, "danger")
    return redirect(url_for("sala_reunioes"))

@app.route("/reuniao/<int:meeting_id>/status", methods=["POST"])
@login_required
def update_meeting_status(meeting_id: int):
    """Allow creators or admins to change the meeting status."""

    meeting = Reuniao.query.get_or_404(meeting_id)
    # Admins (incluindo admin_master) ou criadores podem mudar o status
    if not is_user_admin(current_user) and meeting.criador_id != current_user.id:
        abort(403)
    payload = request.get_json(silent=True) or {}
    status_raw = payload.get("status")
    notify_attendees = bool(payload.get("notify_attendees"))
    if not status_raw:
        return (
            jsonify({"success": False, "error": "Selecione um status para atualizar a reunião."}),
            400,
        )
    try:
        new_status = ReuniaoStatus(status_raw)
    except ValueError:
        return (
            jsonify({"success": False, "error": "Status inválido para a reunião."}),
            400,
        )
    calendar_tz = get_calendar_timezone()
    now = datetime.now(calendar_tz)
    new_start: datetime | None = None
    new_end: datetime | None = None
    if new_status == ReuniaoStatus.ADIADA:
        date_raw = payload.get("date")
        start_raw = payload.get("start_time")
        end_raw = payload.get("end_time")
        if not (date_raw and start_raw and end_raw):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Informe a nova data e horário para adiar a reunião.",
                    }
                ),
                400,
            )
        try:
            parsed_date = datetime.strptime(date_raw, "%Y-%m-%d").date()
            start_time = datetime.strptime(start_raw, "%H:%M").time()
            end_time = datetime.strptime(end_raw, "%H:%M").time()
        except (TypeError, ValueError):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Data ou horário inválido para adiar a reunião.",
                    }
                ),
                400,
            )
        new_start = datetime.combine(parsed_date, start_time, tzinfo=calendar_tz)
        new_end = datetime.combine(parsed_date, end_time, tzinfo=calendar_tz)
    try:
        event_payload = change_meeting_status(
            meeting,
            new_status,
            current_user.id,
            is_user_admin(current_user),
            new_start=new_start,
            new_end=new_end,
            notify_attendees=notify_attendees,
            now=now,
        )
    except MeetingStatusConflictError as exc:
        message = " ".join(exc.messages) if getattr(exc, "messages", None) else None
        return (
            jsonify(
                {
                    "success": False,
                    "error": message or "Horário indisponível para adiar a reunião.",
                }
            ),
            400,
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception:
        current_app.logger.exception(
            "Não foi possível atualizar o status da reunião %s", meeting_id
        )
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Não foi possível atualizar o status da reunião.",
                }
            ),
            500,
        )
    message = f"Status atualizado para {get_status_label(new_status)}."
    return jsonify({"success": True, "event": event_payload, "message": message})

@app.route("/reuniao/<int:meeting_id>/pautas", methods=["POST"])
@login_required
def update_meeting_pautas(meeting_id: int):
    """Persist meeting notes once the meeting has been completed."""

    meeting = Reuniao.query.get_or_404(meeting_id)
    # Admins (incluindo admin_master) ou criadores podem atualizar pautas
    user_is_admin = is_user_admin(current_user)
    if not user_is_admin and meeting.criador_id != current_user.id:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Voce nao tem permissao para atualizar as pautas desta reuniao.",
                }
            ),
            403,
        )
    if meeting.status != ReuniaoStatus.REALIZADA:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "As pautas so podem ser registradas apos a reuniao ser finalizada.",
                }
            ),
            400,
        )
    payload = request.get_json(silent=True) or {}
    pautas_raw = payload.get("pautas", "")
    if not isinstance(pautas_raw, str):
        return (
            jsonify(
                {"success": False, "error": "Informe um texto valido para as pautas."}
            ),
            400,
        )
    pautas_text = pautas_raw.strip()
    if len(pautas_text) > 5000:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "As pautas podem ter no maximo 5.000 caracteres.",
                }
            ),
            400,
        )
    meeting.pautas = pautas_text or None
    db.session.add(meeting)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Falha ao salvar pautas da reuniao", extra={"meeting_id": meeting_id}
        )
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Nao foi possivel salvar as pautas da reuniao.",
                }
            ),
            500,
        )
    invalidate_calendar_cache()
    calendar_tz = get_calendar_timezone()
    now = datetime.now(calendar_tz)
    event_data, _ = serialize_meeting_event(
        meeting,
        now,
        current_user.id,
        is_admin,
        auto_progress=False,
    )
    return jsonify(
        {
            "success": True,
            "pautas": meeting.pautas or "",
            "event": event_data,
        }
    )

@app.route("/reuniao/<int:meeting_id>/delete", methods=["POST"])
@login_required
def delete_reuniao(meeting_id):
    """Delete a meeting and its corresponding Google Calendar event."""
    meeting = Reuniao.query.get_or_404(meeting_id)
    # Admins (incluindo admin_master) podem excluir qualquer reunião
    if not is_user_admin(current_user):
        if meeting.criador_id != current_user.id:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "error": "Você só pode excluir reuniões que você criou."}), 403
            flash("Você só pode excluir reuniões que você criou.", "danger")
            return redirect(url_for("sala_reunioes"))
        if meeting.status in (
            ReuniaoStatus.EM_ANDAMENTO,
            ReuniaoStatus.REALIZADA,
        ):
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "error": "Reuniões em andamento ou realizadas não podem ser excluídas."}), 400
            flash(
                "Reuniões em andamento ou realizadas não podem ser excluídas.",
                "danger",
            )
            return redirect(url_for("sala_reunioes"))

    success = delete_meeting(meeting)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        if success:
            return jsonify({"success": True, "message": "Reunião excluída com sucesso!"}), 200
        else:
            return jsonify({"success": False, "error": "Não foi possível remover o evento do Google Calendar."}), 500

    if success:
        flash("Reunião excluída com sucesso!", "success")
    else:
        flash("Não foi possível remover o evento do Google Calendar.", "danger")
    return redirect(url_for("sala_reunioes"))

# =============================================================================
# ROTAS MIGRADAS PARA blueprints/consultorias.py (2024-12)
# =============================================================================
# @app.route("/consultorias/cadastro") - Migrado
# @app.route("/consultorias/editar/<int:id>") - Migrado
# @app.route("/consultorias/setores") - Migrado
# @app.route("/consultorias/setores/cadastro") - Migrado
# @app.route("/consultorias/setores/editar/<int:id>") - Migrado
# =============================================================================


# =============================================
# =============================================================================
# ROTAS MIGRADAS PARA blueprints/notas.py
# =============================================================================
# CONTROLE DE NOTAS ROUTES
# =============================================

# @app.route("/controle-notas/debito", methods=["GET", "POST"])
# @login_required
# @meeting_only_access_check
# def notas_debito():
#     """Legacy entrypoint; redirect to cadastro view."""
#
#     if not can_access_controle_notas():
#         abort(403)
#
#     flash("O controle de notas foi incorporado à tela de Cadastros.", "info")
#     return redirect(url_for("cadastro_notas"))


# @app.route("/controle-notas/debito/<int:nota_id>/forma-pagamento", methods=["POST"])
# @login_required
# @meeting_only_access_check
# def notas_debito_update_forma_pagamento(nota_id: int):
    # """Atualiza a forma de pagamento de uma nota via requisicao assincrona."""
    # if not can_access_controle_notas():
        # abort(403)

    # pode_ver_forma_pagamento = is_user_admin(current_user) or user_has_tag('Gestǜo') or user_has_tag('Financeiro')
    # if not pode_ver_forma_pagamento:
        # abort(403)

    # payload = request.get_json(silent=True) or {}
    # raw_value = payload.get("forma_pagamento", "")
    # if not isinstance(raw_value, str):
        # raw_value = ""

    # new_value = raw_value.strip().upper()
    # valid_values = {(choice or "").upper() for choice, _ in PAGAMENTO_CHOICES}
    # if new_value not in valid_values:
        # return jsonify({"success": False, "message": "Forma de pagamento invalida."}), 400

    # nota = NotaDebito.query.get_or_404(nota_id)
    # nota.forma_pagamento = new_value
    # db.session.commit()

    # label_map = {(choice or "").upper(): label for choice, label in PAGAMENTO_CHOICES}
    # return jsonify(
        # {
            # "success": True,
            # "forma_pagamento": nota.forma_pagamento,
            # "forma_pagamento_label": label_map.get(new_value, nota.forma_pagamento),
        # }
    # )


# def _parse_decimal_input(raw_value: str | None) -> Decimal | None:
    # """Convert a localized string into a Decimal value."""

    # if not raw_value:
        # return None
    # cleaned = (raw_value or "").strip()
    # if not cleaned:
        # return None
    # cleaned = cleaned.replace("R$", "").replace(" ", "")
    # if "," in cleaned:
        # cleaned = cleaned.replace(".", "").replace(",", ".")
    # else:
        # cleaned = cleaned.replace(",", "")
    # try:
        # return Decimal(cleaned)
    # except (InvalidOperation, ValueError):
        # return None


# def _format_decimal_input(value: Decimal | float | None) -> str:
    # """Format a Decimal into a string accepted by the recurring note modal."""

    # if value is None:
        # return ""
    # if not isinstance(value, Decimal):
        # value = Decimal(value)
    # return (
        # f"{value:,.2f}"
        # .replace(",", "_")
        # .replace(".", ",")
        # .replace("_", ".")
    # )


# def _get_month_day(year: int, month: int, desired_day: int) -> date:
    # """Return a valid date for ``desired_day`` inside ``year``/``month``."""

    # last_day = calendar.monthrange(year, month)[1]
    # clamped_day = max(1, min(desired_day, last_day))
    # return date(year, month, clamped_day)


# def _next_emission_date(recorrente: NotaRecorrente, reference: date | None = None) -> date:
    # """Return the next emission date for ``recorrente`` after ``reference``."""

    # if reference is None:
        # reference = date.today()

    # candidate = _get_month_day(reference.year, reference.month, recorrente.dia_emissao)
    # if candidate >= reference:
        # return candidate

    # next_month = reference.month + 1
    # next_year = reference.year
    # if next_month > 12:
        # next_month = 1
        # next_year += 1
    # return _get_month_day(next_year, next_month, recorrente.dia_emissao)


# def _normalize_tag_slug(name: str | None) -> str:
    # """Normalize tag labels for comparisons."""

    # if not name:
        # return ""
    # normalized = unicodedata.normalize("NFKD", name)
    # ascii_only = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    # return ascii_only.lower().replace(" ", "")


# _CONTROLE_NOTAS_ALLOWED_TAGS = {"gestao", "gestão", "financeiro", "emissornfe"}


# def _controle_notas_notification_user_ids() -> list[int]:
    # """Return IDs for users that must receive Controle de Notas reminders."""

    # users = (
        # User.query.options(joinedload(User.tags))
        # .filter(User.ativo.is_(True))
        # .all()
    # )
    # ids: list[int] = []
    # for user in users:
        # if not user.id:
            # continue
        # if is_user_admin(user):
            # ids.append(user.id)
            # continue
        # for tag in getattr(user, "tags", []) or []:
            # slug = _normalize_tag_slug(getattr(tag, "nome", ""))
            # if slug in _CONTROLE_NOTAS_ALLOWED_TAGS:
                # ids.append(user.id)
                # break
    # return ids


# def _trigger_recorrente_notifications(reference_date: date | None = None) -> int:
    # """Emit notifications for recurring invoices due on ``reference_date``."""

    # today = reference_date or date.today()
    # ativos = NotaRecorrente.query.filter(NotaRecorrente.ativo.is_(True)).all()
    # if not ativos:
        # return 0

    # user_ids = _controle_notas_notification_user_ids()
    # if not user_ids:
        # return 0

    # due_records: list[NotaRecorrente] = []
    # for registro in ativos:
        # due_date = _get_month_day(today.year, today.month, registro.dia_emissao)
        # if due_date != today:
            # continue
        # if registro.ultimo_aviso == today:
            # continue
        # due_records.append(registro)

    # if not due_records:
        # return 0

    # now = utc3_now()
    # created_notifications: list[tuple[int, TaskNotification]] = []
    # touched_users: set[int] = set()

    # for registro in due_records:
        # descricao = (registro.descricao or "").strip()
        # periodo = registro.periodo_formatado
        # base_message = f"Emitir NF {registro.empresa}"
        # if descricao:
            # base_message += f" - {descricao}"
        # message = f"{base_message} (período {periodo})." if periodo else base_message
        # truncated = message[:255]

        # for user_id in user_ids:
            # notification = TaskNotification(
                # user_id=user_id,
                # task_id=None,
                # announcement_id=None,
                # type=NotificationType.RECURRING_INVOICE.value,
                # message=truncated,
                # created_at=now,
            # )
            # db.session.add(notification)
            # created_notifications.append((user_id, notification))
            # touched_users.add(user_id)
        # registro.ultimo_aviso = today

    # if not created_notifications:
        # return 0

    # db.session.commit()

    # for user_id in touched_users:
        # _invalidate_notification_cache(user_id)

    # try:
        # from app.services.realtime import get_broadcaster

        # broadcaster = get_broadcaster()
        # for user_id, notification in created_notifications:
            # broadcaster.broadcast(
                # event_type="notification:created",
                # data={
                    # "id": notification.id,
                    # "type": notification.type,
                    # "message": notification.message,
                    # "created_at": notification.created_at.isoformat()
                    # if notification.created_at
                    # else None,
                # },
                # user_id=user_id,
                # scope="notifications",
            # )
    # except Exception:
        # pass

    # return len(created_notifications)


# @app.route("/controle-notas/cadastro", methods=["GET", "POST"])
# @login_required
# @meeting_only_access_check
# def cadastro_notas():
    # """List and manage Cadastro de Notas with modal-based CRUD integrado às notas."""

    # if not can_access_controle_notas():
        # abort(403)

    # _trigger_recorrente_notifications()

    # pode_ver_forma_pagamento = (
        # is_user_admin(current_user) or user_has_tag('Gestão') or user_has_tag('Financeiro')
    # )
    # cadastro_form = CadastroNotaForm(prefix="cadastro")
    # nota_form = NotaDebitoForm(prefix="nota")
    # search_term = (request.args.get("q") or "").strip()
    # cadastros_query = CadastroNota.query.filter(CadastroNota.ativo.is_(True))
    # if search_term:
        # pattern = f"%{search_term}%"
        # cadastros_query = cadastros_query.filter(sa.func.upper(CadastroNota.cadastro).ilike(pattern.upper()))
    # cadastros = cadastros_query.order_by(CadastroNota.cadastro).all()

    # data_inicial_raw = (request.args.get("data_inicial") or "").strip()
    # data_final_raw = (request.args.get("data_final") or "").strip()

    # def _parse_date(value: str) -> date | None:
        # if not value:
            # return None
        # try:
            # return datetime.strptime(value, "%Y-%m-%d").date()
        # except (ValueError, TypeError):
            # return None

    # data_inicial = _parse_date(data_inicial_raw)
    # data_final = _parse_date(data_final_raw)

    # notas_query = NotaDebito.query
    # if data_inicial and data_final:
        # if data_inicial > data_final:
            # data_inicial, data_final = data_final, data_inicial
        # notas_query = notas_query.filter(
            # NotaDebito.data_emissao >= data_inicial,
            # NotaDebito.data_emissao <= data_final,
        # )
    # notas_registradas = notas_query.order_by(NotaDebito.data_emissao.desc()).all()

    # open_cadastro_modal = request.args.get("open_cadastro_modal") in ("1", "true", "True")
    # open_nota_modal = request.args.get("open_nota_modal") in ("1", "true", "True")
    # editing_cadastro: CadastroNota | None = None
    # editing_nota: NotaDebito | None = None

    # notas_por_empresa: dict[str, list[NotaDebito]] = defaultdict(list)
    # for nota in notas_registradas:
        # empresa_key = (nota.empresa or "").strip().upper() or "SEM EMPRESA"
        # notas_por_empresa[empresa_key].append(nota)

    # def _format_currency_value(value: Decimal | float | int | None) -> str:
        # number = Decimal(value or 0)
        # return (
            # f"R$ {number:,.2f}"
            # .replace(",", "_")
            # .replace(".", ",")
            # .replace("_", ".")
        # )

    # cadastros_info: list[dict[str, object]] = []
    # cadastro_empresas: set[str] = set()

    # for cadastro in cadastros:
        # empresa_key = (cadastro.cadastro or "").strip().upper() or "SEM EMPRESA"
        # cadastro_empresas.add(empresa_key)
        # notas_relacionadas = notas_por_empresa.get(empresa_key, [])
        # total_valor = Decimal("0")
        # total_itens = 0
        # ultima_data = None
        # for nota in notas_relacionadas:
            # total_valor += Decimal(nota.total or 0)
            # total_itens += int(nota.qtde_itens or 0)
            # if nota.data_emissao:
                # if ultima_data is None or nota.data_emissao > ultima_data:
                    # ultima_data = nota.data_emissao
        # cadastros_info.append(
            # {
                # "cadastro": cadastro,
                # "empresa_key": empresa_key,
                # "notas": notas_relacionadas,
                # "total_notas": len(notas_relacionadas),
                # "total_itens": total_itens,
                # "total_valor": total_valor,
                # "total_valor_formatado": _format_currency_value(total_valor),
                # "ultima_data": ultima_data,
                # "ultima_data_formatada": ultima_data.strftime("%d/%m/%Y") if ultima_data else "-",
            # }
        # )

    # cadastros_info.sort(key=lambda item: item["empresa_key"])

    # notas_sem_cadastro: list[dict[str, object]] = []
    # for empresa_key, notas_lista in notas_por_empresa.items():
        # if empresa_key in cadastro_empresas:
            # continue
        # total_valor = Decimal("0")
        # total_itens = 0
        # for nota in notas_lista:
            # total_valor += Decimal(nota.total or 0)
            # total_itens += int(nota.qtde_itens or 0)
        # notas_sem_cadastro.append(
            # {
                # "empresa": empresa_key,
                # "notas": notas_lista,
                # "total_notas": len(notas_lista),
                # "total_itens": total_itens,
                # "total_valor_formatado": _format_currency_value(total_valor),
            # }
        # )

    # notas_sem_cadastro.sort(key=lambda item: item["empresa"])

    # if request.method == "GET":
        # edit_id_raw = request.args.get("edit_cadastro_id")
        # if edit_id_raw:
            # try:
                # edit_id = int(edit_id_raw)
            # except (TypeError, ValueError):
                # abort(404)
            # editing_cadastro = CadastroNota.query.get_or_404(edit_id)
            # cadastro_form = CadastroNotaForm(prefix="cadastro", obj=editing_cadastro)
            # open_cadastro_modal = True

        # edit_nota_id_raw = request.args.get("edit_nota_id")
        # if edit_nota_id_raw:
            # try:
                # edit_nota_id = int(edit_nota_id_raw)
            # except (TypeError, ValueError):
                # abort(404)
            # editing_nota = NotaDebito.query.get_or_404(edit_nota_id)
            # nota_form = NotaDebitoForm(prefix="nota", obj=editing_nota)
            # open_nota_modal = True

    # if request.method == "POST":
        # form_name = request.form.get("form_name")
        # if form_name == "cadastro_create":
            # open_cadastro_modal = True
            # if cadastro_form.validate_on_submit():
                # try:
                    # valor = float(cadastro_form.valor.data.replace(',', '.'))
                # except (ValueError, AttributeError):
                    # flash("Valor inválido.", "warning")
                # else:
                    # cadastro = CadastroNota(
                        # pix=None,
                        # cadastro=cadastro_form.cadastro.data.strip().upper() if cadastro_form.cadastro.data else '',
                        # valor=valor,
                        # acordo=cadastro_form.acordo.data.strip().upper() if cadastro_form.acordo.data else None,
                        # forma_pagamento='',
                        # usuario=cadastro_form.usuario.data.strip() if cadastro_form.usuario.data else None,
                        # senha=cadastro_form.senha.data.strip() if cadastro_form.senha.data else None,
                    # )
                    # db.session.add(cadastro)
                    # db.session.commit()
                    # flash("Cadastro registrado com sucesso.", "success")
                    # return redirect(url_for("cadastro_notas"))
        # elif form_name == "cadastro_update":
            # open_cadastro_modal = True
            # cadastro_id_raw = request.form.get("cadastro_id")
            # try:
                # cadastro_id = int(cadastro_id_raw)
            # except (TypeError, ValueError):
                # abort(400)
            # editing_cadastro = CadastroNota.query.get_or_404(cadastro_id)
            # if cadastro_form.validate_on_submit():
                # try:
                    # valor = float(cadastro_form.valor.data.replace(',', '.'))
                # except (ValueError, AttributeError):
                    # flash("Valor inválido.", "warning")
                # else:
                    # editing_cadastro.pix = None
                    # editing_cadastro.cadastro = cadastro_form.cadastro.data.strip().upper() if cadastro_form.cadastro.data else ''
                    # editing_cadastro.valor = valor
                    # editing_cadastro.acordo = cadastro_form.acordo.data.strip().upper() if cadastro_form.acordo.data else None
                    # editing_cadastro.usuario = cadastro_form.usuario.data.strip() if cadastro_form.usuario.data else None
                    # editing_cadastro.senha = cadastro_form.senha.data.strip() if cadastro_form.senha.data else None
                    # db.session.commit()
                    # flash("Cadastro atualizado com sucesso.", "success")
                    # return redirect(url_for("cadastro_notas"))
        # elif form_name == "cadastro_delete":
            # cadastro_id_raw = request.form.get("cadastro_id")
            # try:
                # cadastro_id = int(cadastro_id_raw)
            # except (TypeError, ValueError):
                # abort(400)
            # cadastro = CadastroNota.query.get_or_404(cadastro_id)
            # cadastro.ativo = False
            # db.session.commit()
            # flash("Cadastro desativado com sucesso.", "success")
            # return redirect(url_for("cadastro_notas"))
        # elif form_name == "nota_create":
            # open_nota_modal = True
            # if nota_form.validate_on_submit():
                # try:
                    # notas_int = int(nota_form.notas.data)
                    # qtde_int = int(nota_form.qtde_itens.data)
                # except (ValueError, TypeError):
                    # flash("Quantidade de notas/itens inválida.", "warning")
                # else:
                    # valor_un = _parse_decimal_input(nota_form.valor_un.data)
                    # total = _parse_decimal_input(nota_form.total.data)
                    # nota = NotaDebito(
                        # data_emissao=nota_form.data_emissao.data,
                        # empresa=nota_form.empresa.data.strip().upper() if nota_form.empresa.data else '',
                        # notas=notas_int,
                        # qtde_itens=qtde_int,
                        # valor_un=valor_un,
                        # total=total,
                        # acordo=nota_form.acordo.data.strip().upper() if nota_form.acordo.data else None,
                        # forma_pagamento=(nota_form.forma_pagamento.data or '').upper() if pode_ver_forma_pagamento else '',
                        # observacao=nota_form.observacao.data.strip() if nota_form.observacao.data else None
                    # )
                    # db.session.add(nota)
                    # db.session.commit()
                    # flash("Nota registrada com sucesso.", "success")
                    # return redirect(url_for("cadastro_notas"))
        # elif form_name == "nota_update":
            # open_nota_modal = True
            # nota_id_raw = request.form.get("nota_id")
            # try:
                # nota_id = int(nota_id_raw)
            # except (TypeError, ValueError):
                # abort(400)
            # editing_nota = NotaDebito.query.get_or_404(nota_id)
            # if nota_form.validate_on_submit():
                # try:
                    # notas_int = int(nota_form.notas.data)
                    # qtde_int = int(nota_form.qtde_itens.data)
                # except (ValueError, TypeError):
                    # flash("Quantidade de notas/itens inválida.", "warning")
                # else:
                    # valor_un = _parse_decimal_input(nota_form.valor_un.data)
                    # total = _parse_decimal_input(nota_form.total.data)
                    # editing_nota.data_emissao = nota_form.data_emissao.data
                    # editing_nota.empresa = nota_form.empresa.data.strip().upper() if nota_form.empresa.data else ''
                    # editing_nota.notas = notas_int
                    # editing_nota.qtde_itens = qtde_int
                    # editing_nota.valor_un = valor_un
                    # editing_nota.total = total
                    # editing_nota.acordo = nota_form.acordo.data.strip().upper() if nota_form.acordo.data else None
                    # if pode_ver_forma_pagamento:
                        # editing_nota.forma_pagamento = (nota_form.forma_pagamento.data or '').upper()
                    # editing_nota.observacao = nota_form.observacao.data.strip() if nota_form.observacao.data else None
                    # db.session.commit()
                    # flash("Nota atualizada com sucesso.", "success")
                    # return redirect(url_for("cadastro_notas"))
        # elif form_name == "nota_delete":
            # nota_id_raw = request.form.get("nota_id")
            # try:
                # nota_id = int(nota_id_raw)
            # except (TypeError, ValueError):
                # abort(400)
            # nota = NotaDebito.query.get_or_404(nota_id)
            # db.session.delete(nota)
            # db.session.commit()
            # flash("Nota excluída com sucesso.", "success")
            # return redirect(url_for("cadastro_notas"))

    # pode_acessar_totalizador = can_access_notas_totalizador()

    # return render_template(
        # "cadastro_notas.html",
        # cadastros=cadastros,
        # cadastros_info=cadastros_info,
        # notas_sem_cadastro=notas_sem_cadastro,
        # cadastro_form=cadastro_form,
        # nota_form=nota_form,
        # open_cadastro_modal=open_cadastro_modal,
        # open_nota_modal=open_nota_modal,
        # editing_cadastro=editing_cadastro,
        # editing_nota=editing_nota,
        # pode_ver_forma_pagamento=pode_ver_forma_pagamento,
        # pode_acessar_totalizador=pode_acessar_totalizador,
        # data_inicial=data_inicial,
        # data_final=data_final,
        # search_term=search_term,
    # )

# @app.route("/controle-notas/recorrentes", methods=["GET", "POST"])
# @login_required
# @meeting_only_access_check
# def notas_recorrentes():
    # """Manage recurring invoices that trigger monthly notifications."""

    # if not can_access_controle_notas():
        # abort(403)

    # _trigger_recorrente_notifications()

    # recorrente_form = NotaRecorrenteForm(prefix="recorrente")
    # open_recorrente_modal = request.args.get("open_recorrente_modal") in ("1", "true", "True")
    # editing_recorrente: NotaRecorrente | None = None

    # if request.method == "GET":
        # edit_id_raw = request.args.get("edit_recorrente_id")
        # if edit_id_raw:
            # try:
                # edit_id = int(edit_id_raw)
            # except (TypeError, ValueError):
                # abort(404)
            # editing_recorrente = NotaRecorrente.query.get_or_404(edit_id)
            # recorrente_form = NotaRecorrenteForm(prefix="recorrente", obj=editing_recorrente)
            # recorrente_form.valor.data = _format_decimal_input(editing_recorrente.valor)
            # open_recorrente_modal = True

    # if request.method == "POST":
        # form_name = request.form.get("form_name")
        # if form_name in {"recorrente_create", "recorrente_update"}:
            # open_recorrente_modal = True
            # target: NotaRecorrente | None = None
            # if form_name == "recorrente_update":
                # recorrente_id_raw = request.form.get("recorrente_id")
                # try:
                    # recorrente_id = int(recorrente_id_raw)
                # except (TypeError, ValueError):
                    # abort(400)
                # target = NotaRecorrente.query.get_or_404(recorrente_id)
                # editing_recorrente = target

            # if recorrente_form.validate_on_submit():
                # valor_decimal = _parse_decimal_input(recorrente_form.valor.data)
                # enterprise = (recorrente_form.empresa.data or "").strip().upper()
                # descricao = (recorrente_form.descricao.data or "").strip()
                # observacao = (recorrente_form.observacao.data or "").strip()
                # is_active = bool(recorrente_form.ativo.data)
                # periodo_inicio = recorrente_form.periodo_inicio.data or 1
                # periodo_fim = recorrente_form.periodo_fim.data or 1
                # dia_emissao = recorrente_form.dia_emissao.data or 1

                # if target is None:
                    # target = NotaRecorrente()
                    # db.session.add(target)

                # target.empresa = enterprise
                # target.descricao = descricao or None
                # target.valor = valor_decimal
                # target.observacao = observacao or None
                # target.ativo = is_active
                # target.periodo_inicio = periodo_inicio
                # target.periodo_fim = periodo_fim
                # target.dia_emissao = dia_emissao

                # db.session.commit()
                # flash("Nota recorrente salva com sucesso.", "success")
                # return redirect(url_for("notas_recorrentes"))
        # elif form_name == "recorrente_delete":
            # recorrente_id_raw = request.form.get("recorrente_id")
            # try:
                # recorrente_id = int(recorrente_id_raw)
            # except (TypeError, ValueError):
                # abort(400)
            # registro = NotaRecorrente.query.get_or_404(recorrente_id)
            # db.session.delete(registro)
            # db.session.commit()
            # flash("Nota recorrente removida.", "success")
            # return redirect(url_for("notas_recorrentes"))
        # elif form_name == "recorrente_toggle":
            # recorrente_id_raw = request.form.get("recorrente_id")
            # try:
                # recorrente_id = int(recorrente_id_raw)
            # except (TypeError, ValueError):
                # abort(400)
            # registro = NotaRecorrente.query.get_or_404(recorrente_id)
            # registro.ativo = not bool(registro.ativo)
            # db.session.commit()
            # status_label = "ativada" if registro.ativo else "pausada"
            # flash(f"Nota recorrente {status_label}.", "success")
            # return redirect(url_for("notas_recorrentes"))

    # hoje = date.today()
    # recorrentes = (
        # NotaRecorrente.query.order_by(
            # NotaRecorrente.ativo.desc(),
            # sa.func.upper(NotaRecorrente.empresa),
            # NotaRecorrente.dia_emissao,
        # ).all()
    # )
    # recorrentes_info = []
    # for registro in recorrentes:
        # proxima = _next_emission_date(registro, hoje)
        # recorrentes_info.append(
            # {
                # "registro": registro,
                # "proxima_data": proxima,
                # "dias_restantes": (proxima - hoje).days,
                # "emissao_hoje": proxima == hoje,
                # "valor_input": _format_decimal_input(registro.valor),
            # }
        # )

    # pode_acessar_totalizador = can_access_notas_totalizador()
    # cadastros = (
        # CadastroNota.query.filter(CadastroNota.ativo.is_(True))
        # .order_by(CadastroNota.cadastro)
        # .all()
    # )

    # return render_template(
        # "notas_recorrentes.html",
        # recorrentes_info=recorrentes_info,
        # recorrente_form=recorrente_form,
        # open_recorrente_modal=open_recorrente_modal,
        # editing_recorrente=editing_recorrente,
        # editing_recorrente_valor=_format_decimal_input(editing_recorrente.valor) if editing_recorrente else "",
        # pode_acessar_totalizador=pode_acessar_totalizador,
        # cadastros=cadastros,
    # )


# @app.route("/controle-notas/totalizador", methods=["GET"])
# @login_required
# @meeting_only_access_check
# def notas_totalizador():
    # """Display aggregated Nota Débito data with optional period filters."""

    # if not can_access_controle_notas():
        # abort(403)

    # if not can_access_notas_totalizador():
        # abort(403)

    # _trigger_recorrente_notifications()

    # today = date.today()
    # default_start = today.replace(day=1)
    # default_end = today

    # data_inicial_raw = (request.args.get("data_inicial") or "").strip()
    # data_final_raw = (request.args.get("data_final") or "").strip()

    # def _parse_date(value: str) -> date | None:
        # if not value:
            # return None
        # try:
            # return datetime.strptime(value, "%Y-%m-%d").date()
        # except (ValueError, TypeError):
            # return None

    # data_inicial = _parse_date(data_inicial_raw) or default_start
    # data_final = _parse_date(data_final_raw) or default_end

    # if data_inicial > data_final:
        # flash("A data inicial não pode ser maior que a data final.", "warning")
        # data_inicial, data_final = default_start, default_end

    # base_query = NotaDebito.query.filter(
        # NotaDebito.data_emissao >= data_inicial,
        # NotaDebito.data_emissao <= data_final,
    # )

    # nota_form = NotaDebitoForm()
    # pagamento_choices = nota_form.forma_pagamento.choices
    # pagamento_label_map = {
        # (choice or "").upper(): label for choice, label in pagamento_choices
    # }
    # pagamento_value_map = {
        # (choice or "").upper(): (choice or "") for choice, _ in pagamento_choices
    # }

    # def format_currency(value: Decimal | None) -> str:
        # number = value if isinstance(value, Decimal) else Decimal(value or 0)
        # return (
            # f"R$ {number:,.2f}"
            # .replace(",", "_")
            # .replace(".", ",")
            # .replace("_", ".")
        # )

    # dados_totalizador: list[dict[str, object]] = []
    # dados_totalizador_acordo: list[dict[str, object]] = []
    # dados_totalizador_pagamento: list[dict[str, object]] = []
    # notas_por_empresa: dict[str, dict[str, object]] = {}
    # notas_por_acordo: dict[str, dict[str, object]] = {}
    # notas_por_pagamento: dict[str, dict[str, object]] = {}

    # notas_list = (
        # base_query.order_by(
            # sa.func.lower(NotaDebito.empresa),
            # NotaDebito.data_emissao.desc(),
            # NotaDebito.id.desc(),
        # ).all()
    # )

    # total_registros = 0
    # total_notas = 0
    # total_itens = 0
    # total_valor = Decimal("0")

    # def _get_or_create_group(
        # storage: dict[str, dict[str, object]],
        # key: str,
        # titulo: str,
        # extra: dict[str, object] | None = None,
    # ) -> dict[str, object]:
        # grupo = storage.get(key)
        # if not grupo:
            # grupo = {
                # "titulo": titulo,
                # "qtd_registros": 0,
                # "total_notas": 0,
                # "total_itens": 0,
                # "valor_total": Decimal("0"),
                # "notas": [],
            # }
            # if extra:
                # grupo.update(extra)
            # storage[key] = grupo
        # return grupo

    # for nota in notas_list:
        # empresa_key = (nota.empresa or "").strip().upper()
        # if not empresa_key:
            # empresa_key = "SEM EMPRESA"

        # grupo_empresa = _get_or_create_group(
            # notas_por_empresa,
            # empresa_key,
            # empresa_key,
            # {"empresa": empresa_key},
        # )

        # valor_total = nota.total or Decimal("0")
        # valor_un = nota.valor_un or Decimal("0")

        # forma_pagamento_raw = (nota.forma_pagamento or "").strip()
        # forma_pagamento_value = forma_pagamento_raw.upper()
        # acordo_key = (nota.acordo or "").strip().upper() or "SEM ACORDO"
        # pagamento_key = forma_pagamento_value or "SEM PAGAMENTO"
        # pagamento_label = pagamento_label_map.get(
            # pagamento_key, pagamento_key
        # ) or "SEM PAGAMENTO"

        # grupo_acordo = _get_or_create_group(
            # notas_por_acordo,
            # acordo_key,
            # acordo_key,
            # {"acordo": acordo_key},
        # )
        # grupo_pagamento = _get_or_create_group(
            # notas_por_pagamento,
            # pagamento_key,
            # pagamento_label,
            # {
                # "forma_pagamento": pagamento_key,
                # "forma_pagamento_label": pagamento_label,
            # },
        # )

        # registro_nota = {
            # "id": nota.id,
            # "data_emissao": nota.data_emissao,
            # "data_emissao_formatada": nota.data_emissao_formatada,
            # "empresa": empresa_key,
            # "notas": nota.notas,
            # "qtde_itens": nota.qtde_itens,
            # "valor_un": valor_un,
            # "valor_un_formatado": nota.valor_un_formatado,
            # "valor_total": valor_total,
            # "valor_total_formatado": nota.total_formatado,
            # "acordo": (nota.acordo or "").upper() if nota.acordo else "#N/A",
            # "forma_pagamento": forma_pagamento_raw,
            # "forma_pagamento_upper": forma_pagamento_value,
            # "forma_pagamento_choice_value": pagamento_value_map.get(
                # forma_pagamento_value, forma_pagamento_raw
            # ),
            # "forma_pagamento_label": pagamento_label_map.get(
                # forma_pagamento_value, forma_pagamento_value
            # ),
            # "observacao": nota.observacao or "",
        # }

        # for grupo_destino in (grupo_empresa, grupo_acordo, grupo_pagamento):
            # grupo_destino["qtd_registros"] += 1
            # grupo_destino["total_notas"] += int(nota.notas or 0)
            # grupo_destino["total_itens"] += int(nota.qtde_itens or 0)
            # grupo_destino["valor_total"] += Decimal(valor_total)
            # grupo_destino["notas"].append(registro_nota)

        # total_registros += 1
        # total_notas += int(nota.notas or 0)
        # total_itens += int(nota.qtde_itens or 0)
        # total_valor += Decimal(valor_total)

    # def _sort_key_empresa(value: str) -> str:
        # normalized = unicodedata.normalize("NFKD", value or "")
        # return "".join(ch for ch in normalized if not unicodedata.combining(ch)).casefold()

    # def _finalizar_grupos(storage: dict[str, dict[str, object]]) -> list[dict[str, object]]:
        # grupos_finalizados: list[dict[str, object]] = []
        # for chave in sorted(storage.keys(), key=_sort_key_empresa):
            # grupo = storage[chave]
            # grupo["valor_total_formatado"] = format_currency(grupo["valor_total"])
            # grupos_finalizados.append(grupo)
        # return grupos_finalizados

    # dados_totalizador = _finalizar_grupos(notas_por_empresa)
    # dados_totalizador_acordo = _finalizar_grupos(notas_por_acordo)
    # dados_totalizador_pagamento = _finalizar_grupos(notas_por_pagamento)

    # tipos_empresa = [grupo["titulo"] for grupo in dados_totalizador]
    # tipos_acordo = [grupo["titulo"] for grupo in dados_totalizador_acordo]
    # tipos_pagamento: list[dict[str, str]] = [
        # {
            # "value": grupo.get("forma_pagamento", ""),
            # "label": grupo.get("titulo")
            # or grupo.get("forma_pagamento_label")
            # or grupo.get("forma_pagamento", ""),
        # }
        # for grupo in dados_totalizador_pagamento
    # ]

    # resumo_geral = {
        # "qtd_registros": total_registros,
        # "total_notas": total_notas,
        # "total_itens": total_itens,
        # "valor_total": total_valor,
        # "valor_total_formatado": format_currency(total_valor),
    # }

    # pode_ver_forma_pagamento = (
        # is_user_admin(current_user)
        # or user_has_tag("Gestão")
        # or user_has_tag("Financeiro")
    # )

    # return render_template(
        # "notas_totalizador.html",
        # dados_totalizador=dados_totalizador,
        # resumo_geral=resumo_geral,
        # data_inicial=data_inicial.isoformat(),
        # data_final=data_final.isoformat(),
        # pagamento_choices=pagamento_choices,
        # pode_ver_forma_pagamento=pode_ver_forma_pagamento,
        # dados_totalizador_acordo=dados_totalizador_acordo,
        # dados_totalizador_pagamento=dados_totalizador_pagamento,
        # tipos_empresa=tipos_empresa,
        # tipos_acordo=tipos_acordo,
        # tipos_pagamento=tipos_pagamento,
    # )


# =============================================================================
# ROTAS MIGRADAS PARA blueprints/tags.py
# =============================================================================
# @app.route("/tags")
# @login_required
# def tags():
#     """Migrado para blueprints/tags.py"""
#     pass

# @app.route("/tags/cadastro", methods=["GET", "POST"])
# @admin_required
# def cadastro_tag():
#     """Migrado para blueprints/tags.py"""
#     pass

# @app.route("/tags/editar/<int:id>", methods=["GET", "POST"])
# @admin_required
# def editar_tag(id):
#     """Migrado para blueprints/tags.py"""
#     pass

# =============================================================================
# ROTAS MIGRADAS PARA blueprints/consultorias.py (2024-12)
# =============================================================================
# @app.route("/consultorias/relatorios") - Migrado
# @app.route("/consultorias/inclusoes") - Migrado
# @app.route("/consultorias/inclusoes/nova") - Migrado
# @app.route("/consultorias/inclusoes/<codigo>") - Migrado
# @app.route("/consultorias/inclusoes/<codigo>/editar") - Migrado
# =============================================================================

# =============================================================================
# ROTAS MIGRADAS PARA blueprints/auth.py (2024-12)
# =============================================================================
# @app.route("/cookies") - Migrado para blueprints/auth.py
# @app.route("/cookies/revoke") - Migrado para blueprints/auth.py
# @app.route("/login/google") - Migrado para blueprints/auth.py
# @app.route("/google/callback") - Migrado para blueprints/auth.py (anteriormente /oauth2callback)
# @app.route("/login") - Migrado para blueprints/auth.py
# normalize_scopes() - Migrado para blueprints/auth.py
# _determine_post_login_redirect() - Migrado para blueprints/auth.py
# =============================================================================

@app.route("/api/cnpj/<cnpj>")
@login_required
def api_cnpj(cnpj):
    """Provide a JSON API for CNPJ lookups."""
    try:
        dados = consultar_cnpj(cnpj)
    except ValueError as e:
        msg = str(e)
        status = 400 if "inválido" in msg.lower() or "invalido" in msg.lower() else 404
        if status == 404:
            msg = "CNPJ não está cadastrado"
        return jsonify({"error": msg}), status
    except Exception:
        return jsonify({"error": "Erro ao consultar CNPJ"}), 500
    if not dados:
        return jsonify({"error": "CNPJ não está cadastrado"}), 404
    return jsonify(dados)

@app.route("/api/reunioes")
@login_required
@csrf.exempt
@limiter.limit("30 per minute")  # Limite de 30 req/min por IP (1 a cada 2s)
def api_reunioes():
    """Return meetings with up-to-date status as JSON."""
    raw_events = []
    fallback = None
    try:
        raw_events = fetch_raw_events()
    except Exception:
        current_app.logger.exception("Google Calendar fetch failed; using cached data fallback")
        raw_events = calendar_cache.get("raw_calendar_events")
        if raw_events is not None:
            fallback = "primary-cache"
        else:
            raw_events = calendar_cache.get("raw_calendar_events_stale")
            if raw_events is not None:
                fallback = "stale-cache"
            else:
                raw_events = []
    calendar_tz = get_calendar_timezone()
    now = datetime.now(calendar_tz)
    events = combine_events(
        raw_events, now, current_user.id, is_user_admin(current_user)
    )
    response = jsonify(events)
    if fallback:
        response.headers["X-Calendar-Fallback"] = fallback
    return response

@app.route("/api/calendario-eventos")
@login_required
def api_general_calendar_events():
    """Return collaborator calendar events as JSON."""

    can_manage = (
        is_user_admin(current_user) or user_has_tag("Gestão") or user_has_tag("Coord.")
    )
    events = serialize_events_for_calendar(
        current_user.id, can_manage, is_user_admin(current_user)
    )
    return jsonify(events)

@app.route("/cadastrar_empresa", methods=["GET", "POST"])
@login_required
def cadastrar_empresa():
    """Create a new company record."""
    form = EmpresaForm()
    if request.method == "GET":
        form.sistemas_consultorias.data = form.sistemas_consultorias.data or []
        form.regime_lancamento.data = form.regime_lancamento.data or []
    if form.validate_on_submit():
        try:
            cnpj_limpo = re.sub(r"\D", "", form.cnpj.data)
            acessos_json = form.acessos_json.data or "[]"
            try:
                acessos = json.loads(acessos_json) if acessos_json else []
            except Exception:
                acessos = []
            nova_empresa = Empresa(
                codigo_empresa=form.codigo_empresa.data,
                nome_empresa=form.nome_empresa.data,
                cnpj=cnpj_limpo,
                data_abertura=form.data_abertura.data,
                socio_administrador=form.socio_administrador.data,
                tributacao=form.tributacao.data,
                regime_lancamento=form.regime_lancamento.data,
                atividade_principal=form.atividade_principal.data,
                sistemas_consultorias=form.sistemas_consultorias.data,
                sistema_utilizado=form.sistema_utilizado.data,
                acessos=acessos,
            )
            db.session.add(nova_empresa)
            db.session.commit()
            flash("Empresa cadastrada com sucesso!", "success")
            return redirect(
                url_for("gerenciar_departamentos", empresa_id=nova_empresa.id)
            )
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao cadastrar empresa: {e}", "danger")
    else:
        print("Formulário não validado:")
        print(form.errors)

    return render_template("empresas/cadastrar.html", form=form)

@app.route("/listar_empresas")
@login_required
@meeting_only_access_check
def listar_empresas():
    """List companies with optional search and pagination."""
    search = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = 20
    show_inactive = request.args.get("show_inactive") in ("1", "on", "true", "True")
    allowed_tributacoes = ["Simples Nacional", "Lucro Presumido", "Lucro Real"]
    saved_filters = session.get("listar_empresas_filters", {})
    sort_arg = request.args.get("sort")
    order_arg = request.args.get("order")
    clear_tributacao = request.args.get("clear_tributacao") == "1"
    raw_tributacoes = request.args.getlist("tributacao")
    if clear_tributacao:
        tributacao_filters: list[str] = []
    elif raw_tributacoes:
        tributacao_filters = [t for t in raw_tributacoes if t in allowed_tributacoes]
    else:
        tributacao_filters = saved_filters.get("tributacao_filters", [])

    sort = sort_arg or saved_filters.get("sort") or "nome"
    if sort not in ("nome", "codigo"):
        sort = "nome"

    order = order_arg or saved_filters.get("order") or "asc"
    if order not in ("asc", "desc"):
        order = "asc"

    session["listar_empresas_filters"] = {"sort": sort, "order": order}

    query = Empresa.query

    # Filter active by default; if "show inactive" is marked, show only inactive
    if show_inactive:
        query = query.filter_by(ativo=False)
    else:
        query = query.filter_by(ativo=True)

    if tributacao_filters:
        query = query.filter(Empresa.tributacao.in_(tributacao_filters))

    if search:
        like_pattern = f"%{search}%"
        query = query.filter(
            or_(
                Empresa.nome_empresa.ilike(like_pattern),
                Empresa.codigo_empresa.ilike(like_pattern),
            )
        )

    if sort == "codigo":
        order_column = Empresa.codigo_empresa
    else:
        order_column = Empresa.nome_empresa

    # Show active companies first, then sort by the selected column
    if order == "desc":
        query = query.order_by(Empresa.ativo.desc(), order_column.desc())
    else:
        query = query.order_by(Empresa.ativo.desc(), order_column.asc())

    session["listar_empresas_filters"] = {
        "sort": sort,
        "order": order,
        "tributacao_filters": tributacao_filters,
    }

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    empresas = pagination.items

    return render_template(
        "empresas/listar.html",
        empresas=empresas,
        pagination=pagination,
        search=search,
        sort=sort,
        order=order,
        show_inactive=show_inactive,
        tributacao_filters=tributacao_filters,
        allowed_tributacoes=allowed_tributacoes,
    )

@app.route("/empresa/editar/<empresa_id>", methods=["GET", "POST"])
@app.route("/empresa/editar/<int:id>", methods=["GET", "POST"])
@login_required
def editar_empresa(empresa_id: str | None = None, id: int | None = None):
    """Edit an existing company and its details."""
    raw_empresa = empresa_id if empresa_id is not None else id
    empresa_id_int = decode_id(str(raw_empresa), namespace="empresa")
    empresa_token = encode_id(empresa_id_int, namespace="empresa")
    empresa = Empresa.query.get_or_404(empresa_id_int)
    empresa_form = EmpresaForm(request.form, obj=empresa)

    if request.method == "GET":
        empresa_form.sistemas_consultorias.data = empresa.sistemas_consultorias or []
        empresa_form.regime_lancamento.data = empresa.regime_lancamento or []
        empresa_form.acessos_json.data = json.dumps(empresa.acessos or [])
        empresa_form.contatos_json.data = json.dumps(empresa.contatos or [])
        empresa_form.ativo.data = empresa.ativo

    if request.method == "POST":
        if empresa_form.validate():
            empresa_form.populate_obj(empresa)
            empresa.cnpj = re.sub(r"\D", "", empresa_form.cnpj.data)
            empresa.sistemas_consultorias = empresa_form.sistemas_consultorias.data
            try:
                empresa.acessos = json.loads(empresa_form.acessos_json.data or "[]")
            except Exception:
                empresa.acessos = []
            try:
                empresa.contatos = json.loads(empresa_form.contatos_json.data or "[]")
            except Exception:
                empresa.contatos = []
            db.session.add(empresa)
            try:
                db.session.commit()
                flash("Dados do Cliente salvos com sucesso!", "success")
                return redirect(url_for("visualizar_empresa", empresa_id=empresa_token) + "#dados-cliente")
            except Exception as e:
                db.session.rollback()
                flash(f"Erro ao salvar: {str(e)}", "danger")
        else:
            for field, errors in empresa_form.errors.items():
                for error in errors:
                    flash(f"Erro: {error}", "danger")

    return render_template(
        "empresas/editar_empresa.html",
        empresa=empresa,
        empresa_form=empresa_form,
    )

@app.route("/empresa/visualizar/<empresa_id>")
@app.route("/empresa/visualizar/<int:id>")
@app.route("/empresa/visualizar_embed/<empresa_id>")
@app.route("/empresa/visualizar_embed/<int:id>")
@login_required
def visualizar_empresa(empresa_id: str | None = None, id: int | None = None):
    """Display a detailed view of a company."""
    from types import SimpleNamespace

    embed_mode = request.args.get("hide_actions") == "1"
    raw_empresa = empresa_id if empresa_id is not None else id
    resolved_empresa_id = decode_id(str(raw_empresa), namespace="empresa")
    empresa_token = encode_id(resolved_empresa_id, namespace="empresa")
    empresa = Empresa.query.get_or_404(resolved_empresa_id)

    if request.endpoint == "visualizar_empresa_embed" and not embed_mode:
        return redirect(url_for("visualizar_empresa", empresa_id=empresa_token))

    # display para regime de lançamento
    empresa.regime_lancamento_display = empresa.regime_lancamento or []

    can_access_financeiro = user_has_tag("financeiro")

    # Consolidated query: load all departments in one query instead of 6 separate queries
    dept_tipos = [
        "Departamento Fiscal",
        "Departamento Contábil",
        "Departamento Pessoal",
        "Departamento Administrativo",
        "Departamento Notas Fiscais"
    ]
    if can_access_financeiro:
        dept_tipos.append("Departamento Financeiro")

    departamentos = Departamento.query.filter(
        Departamento.empresa_id == id,
        Departamento.tipo.in_(dept_tipos)
    ).all()

    # Map departments by tipo for easy access
    dept_map = {dept.tipo: dept for dept in departamentos}
    fiscal = dept_map.get("Departamento Fiscal")
    contabil = dept_map.get("Departamento Contábil")
    pessoal = dept_map.get("Departamento Pessoal")
    administrativo = dept_map.get("Departamento Administrativo")
    financeiro = dept_map.get("Departamento Financeiro") if can_access_financeiro else None
    notas_fiscais = dept_map.get("Departamento Notas Fiscais")

    def _prepare_envio_fisico(departamento):
        if not departamento:
            return []
        try:
            lista = (
                json.loads(departamento.envio_fisico)
                if isinstance(departamento.envio_fisico, str)
                else (departamento.envio_fisico or [])
            )
        except Exception:
            lista = []
        if "malote" in lista and getattr(departamento, "malote_coleta", None):
            lista = [
                "Malote - " + departamento.malote_coleta if item == "malote" else item
                for item in lista
            ]
        return lista

    # monta contatos_list from empresa.contatos
    if getattr(empresa, "contatos", None):
        try:
            contatos_list = (
                json.loads(empresa.contatos)
                if isinstance(empresa.contatos, str)
                else empresa.contatos
            )
        except Exception:
            contatos_list = []
    else:
        contatos_list = []
    contatos_list = normalize_contatos(contatos_list)
    # injeta contatos_list na empresa para acesso no template
    empresa.contatos_list = contatos_list

    cliente_reunioes = (
        ClienteReuniao.query.options(
            joinedload(ClienteReuniao.setor),
        )
        .filter_by(empresa_id=id)
        .order_by(ClienteReuniao.data.desc(), ClienteReuniao.created_at.desc())
        .all()
    )
    participante_ids: set[int] = set()
    for reuniao in cliente_reunioes:
        for participante in reuniao.participantes or []:
            if isinstance(participante, int):
                participante_ids.add(participante)
    reunioes_participantes_map: dict[int, User] = {}
    if participante_ids:
        usuarios = User.query.filter(User.id.in_(participante_ids)).all()
        reunioes_participantes_map = {usuario.id: usuario for usuario in usuarios}

    # fiscal_view: garante objeto mesmo quando fiscal é None
    if fiscal is None:
        fiscal_view = SimpleNamespace(
            formas_importacao=[], envio_fisico=[]
        )
    else:
        fiscal_view = fiscal
        # normaliza formas_importacao
        formas = getattr(fiscal_view, "formas_importacao", None)
        if isinstance(formas, str):
            try:
                fiscal_view.formas_importacao = json.loads(formas)
            except Exception:
                fiscal_view.formas_importacao = []
        elif not formas:
            fiscal_view.formas_importacao = []
        # injeta listas sem risco
        setattr(fiscal_view, "envio_fisico", _prepare_envio_fisico(fiscal_view))

    if contabil:
        contabil.envio_fisico = _prepare_envio_fisico(contabil)
    if pessoal:
        pessoal.envio_fisico = _prepare_envio_fisico(pessoal)
    if administrativo:
        administrativo.envio_fisico = _prepare_envio_fisico(administrativo)
    if financeiro:
        financeiro.envio_fisico = _prepare_envio_fisico(financeiro)

    usuarios_responsaveis = (
        User.query.filter(User.ativo.is_(True))
        .order_by(User.name.asc(), User.username.asc())
        .all()
    )
    responsaveis_map = {
        str(usuario.id): (usuario.name or usuario.username or f"Usuário {usuario.id}")
        for usuario in usuarios_responsaveis
    }

    return render_template(
        "empresas/visualizar.html",
        empresa=empresa,
        fiscal=fiscal_view,
        contabil=contabil,
        pessoal=pessoal,
        administrativo=administrativo,
        financeiro=financeiro,
        notas_fiscais=notas_fiscais,
        reunioes_cliente=cliente_reunioes,
        reunioes_participantes_map=reunioes_participantes_map,
        can_access_financeiro=can_access_financeiro,
        responsaveis_map=responsaveis_map,
        empresa_token=empresa_token,
        embed_mode=embed_mode,
    )

    ## Rota para gerenciar departamentos de uma empresa

@app.route("/empresa/<empresa_id>/departamentos", methods=["GET", "POST"])
@app.route("/empresa/<int:id>/departamentos", methods=["GET", "POST"])
@login_required
def gerenciar_departamentos(empresa_id: str | None = None, id: int | None = None):
    """Create or update department data for a company."""
    raw_empresa = empresa_id if empresa_id is not None else id
    empresa_id_int = decode_id(str(raw_empresa), namespace="empresa")
    empresa_token = encode_id(empresa_id_int, namespace="empresa")
    empresa = Empresa.query.get_or_404(empresa_id_int)

    can_access_financeiro = user_has_tag("financeiro")
    responsavel_value = (request.form.get("responsavel") or "").strip() if request.method == "POST" else None

    # Consolidated query: load all departments in one query instead of 6 separate queries
    dept_tipos = [
        "Departamento Fiscal",
        "Departamento Contábil",
        "Departamento Pessoal",
        "Departamento Administrativo",
        "Departamento Notas Fiscais"
    ]
    if can_access_financeiro:
        dept_tipos.append("Departamento Financeiro")

    departamentos = Departamento.query.filter(
        Departamento.empresa_id == empresa_id_int,
        Departamento.tipo.in_(dept_tipos)
    ).all()

    # Map departments by tipo for easy access
    dept_map = {dept.tipo: dept for dept in departamentos}
    fiscal = dept_map.get("Departamento Fiscal")
    contabil = dept_map.get("Departamento Contábil")
    pessoal = dept_map.get("Departamento Pessoal")
    administrativo = dept_map.get("Departamento Administrativo")
    financeiro = dept_map.get("Departamento Financeiro") if can_access_financeiro else None
    notas_fiscais = dept_map.get("Departamento Notas Fiscais")

    fiscal_form = DepartamentoFiscalForm(request.form, obj=fiscal)
    contabil_form = DepartamentoContabilForm(request.form, obj=contabil)
    pessoal_form = DepartamentoPessoalForm(request.form, obj=pessoal)
    administrativo_form = DepartamentoAdministrativoForm(
        request.form, obj=administrativo
    )
    financeiro_form = (
        DepartamentoFinanceiroForm(request.form, obj=financeiro)
        if can_access_financeiro
        else None
    )
    usuarios_responsaveis = [
        {
            "id": str(usuario.id),
            "label": usuario.name or usuario.username or f"Usuário {usuario.id}",
        }
        for usuario in User.query.filter(User.ativo.is_(True))
        .order_by(User.name.asc(), User.username.asc())
        .all()
    ]
    usuarios_responsaveis_ids = [usuario["id"] for usuario in usuarios_responsaveis]

    if request.method == "GET":
        fiscal_form = DepartamentoFiscalForm(obj=fiscal)
        if fiscal:
            fiscal_form.envio_digital.data = (
                fiscal.envio_digital
                if isinstance(fiscal.envio_digital, list)
                else json.loads(fiscal.envio_digital) if fiscal.envio_digital else []
            )
            fiscal_form.envio_fisico.data = (
                fiscal.envio_fisico
                if isinstance(fiscal.envio_fisico, list)
                else json.loads(fiscal.envio_fisico) if fiscal.envio_fisico else []
            )

            if fiscal.contatos:
                try:
                    contatos_list = (
                        json.loads(fiscal.contatos)
                        if isinstance(fiscal.contatos, str)
                        else fiscal.contatos
                    )
                except Exception:
                    contatos_list = []
            else:
                contatos_list = []
            contatos_list = normalize_contatos(contatos_list)
            fiscal_form.contatos_json.data = json.dumps(contatos_list)

        contabil_form = DepartamentoContabilForm(obj=contabil)
        if contabil:
            contabil_form.metodo_importacao.data = (
                contabil.metodo_importacao
                if isinstance(contabil.metodo_importacao, list)
                else (
                    json.loads(contabil.metodo_importacao)
                    if contabil.metodo_importacao
                    else []
                )
            )
            contabil_form.envio_digital.data = (
                contabil.envio_digital
                if isinstance(contabil.envio_digital, list)
                else (
                    json.loads(contabil.envio_digital) if contabil.envio_digital else []
                )
            )
            contabil_form.envio_fisico.data = (
                contabil.envio_fisico
                if isinstance(contabil.envio_fisico, list)
                else json.loads(contabil.envio_fisico) if contabil.envio_fisico else []
            )
            contabil_form.controle_relatorios.data = (
                contabil.controle_relatorios
                if isinstance(contabil.controle_relatorios, list)
                else (
                    json.loads(contabil.controle_relatorios)
                    if contabil.controle_relatorios
                    else []
                )
            )

    form_type = request.form.get("form_type")

    if request.method == "POST":
        form_processed_successfully = False

        def _set_responsavel(departamento_obj):
            if departamento_obj is not None:
                departamento_obj.responsavel = responsavel_value or None

        if form_type == "fiscal" and fiscal_form.validate():
            if not fiscal:
                fiscal = Departamento(empresa_id=empresa_id_int, tipo="Departamento Fiscal")
                db.session.add(fiscal)

            fiscal_form.populate_obj(fiscal)
            _set_responsavel(fiscal)
            if "malote" not in (fiscal_form.envio_fisico.data or []):
                fiscal.malote_coleta = None
            else:
                fiscal.malote_coleta = fiscal_form.malote_coleta.data
            try:
                fiscal.contatos = json.loads(fiscal_form.contatos_json.data or "[]")
            except Exception:
                fiscal.contatos = []
            flash("Departamento Fiscal salvo com sucesso!", "success")
            form_processed_successfully = True

        elif form_type == "contabil" and contabil_form.validate():
            if not contabil:
                contabil = Departamento(
                    empresa_id=empresa_id_int, tipo="Departamento Contábil"
                )
                db.session.add(contabil)

            contabil_form.populate_obj(contabil)
            _set_responsavel(contabil)
            if "malote" not in (contabil_form.envio_fisico.data or []):
                contabil.malote_coleta = None
            else:
                contabil.malote_coleta = contabil_form.malote_coleta.data

            contabil.metodo_importacao = contabil_form.metodo_importacao.data or []
            contabil.envio_digital = contabil_form.envio_digital.data or []
            contabil.envio_fisico = contabil_form.envio_fisico.data or []
            contabil.controle_relatorios = contabil_form.controle_relatorios.data or []

            flash("Departamento Contábil salvo com sucesso!", "success")
            form_processed_successfully = True

        elif form_type == "pessoal" and pessoal_form.validate():
            if not pessoal:
                pessoal = Departamento(
                    empresa_id=empresa_id_int, tipo="Departamento Pessoal"
                )
                db.session.add(pessoal)

            pessoal_form.populate_obj(pessoal)
            _set_responsavel(pessoal)
            flash("Departamento Pessoal salvo com sucesso!", "success")
            form_processed_successfully = True

        elif form_type == "administrativo" and administrativo_form.validate():
            if not administrativo:
                administrativo = Departamento(
                    empresa_id=empresa_id_int, tipo="Departamento Administrativo"
                )
                db.session.add(administrativo)

            administrativo_form.populate_obj(administrativo)
            _set_responsavel(administrativo)
            flash("Departamento Administrativo salvo com sucesso!", "success")
            form_processed_successfully = True
        elif form_type == "financeiro":
            if not can_access_financeiro:
                abort(403)
            if financeiro_form and financeiro_form.validate():
                if not financeiro:
                    financeiro = Departamento(
                        empresa_id=empresa_id_int, tipo="Departamento Financeiro"
                    )
                    db.session.add(financeiro)

                financeiro_form.populate_obj(financeiro)
                _set_responsavel(financeiro)
                flash("Departamento Financeiro salvo com sucesso!", "success")
                form_processed_successfully = True

        elif form_type == "notas_fiscais":
            if not notas_fiscais:
                notas_fiscais = Departamento(
                    empresa_id=empresa_id_int, tipo="Departamento Notas Fiscais"
                )
                db.session.add(notas_fiscais)

            particularidades_texto = request.form.get("particularidades_texto", "")
            notas_fiscais.particularidades_texto = particularidades_texto
            _set_responsavel(notas_fiscais)
            flash("Departamento Notas Fiscais salvo com sucesso!", "success")
            form_processed_successfully = True

        if form_processed_successfully:
            try:
                db.session.commit()

                hash_ancoras = {
                    "fiscal": "fiscal",
                    "contabil": "contabil",
                    "pessoal": "pessoal",
                    "administrativo": "administrativo",
                    "financeiro": "financeiro",
                    "notas_fiscais": "notas-fiscais",
                }
                hash_ancora = hash_ancoras.get(form_type, "")

                return redirect(
                    url_for("visualizar_empresa", empresa_id=empresa_token) + f"#{hash_ancora}"
                )

            except Exception as e:
                db.session.rollback()
                flash(f"Ocorreu um erro ao salvar: {str(e)}", "danger")

        else:
            active_form = {
                "fiscal": fiscal_form,
                "contabil": contabil_form,
                "pessoal": pessoal_form,
                "administrativo": administrativo_form,
                "financeiro": financeiro_form,
            }.get(form_type)
            if active_form and active_form.errors:
                for field, errors in active_form.errors.items():
                    for error in errors:
                        flash(
                            f"Erro no formulário {form_type.capitalize()}: {error}",
                            "danger",
                        )

    reunioes_cliente = (
        ClienteReuniao.query.options(joinedload(ClienteReuniao.setor))
        .filter_by(empresa_id=empresa_id)
        .order_by(ClienteReuniao.data.desc(), ClienteReuniao.created_at.desc())
        .all()
    )
    participante_ids: set[int] = set()
    for reuniao in reunioes_cliente:
        for participante in reuniao.participantes or []:
            if isinstance(participante, int):
                participante_ids.add(participante)
    reunioes_participantes_map: dict[int, User] = {}
    if participante_ids:
        usuarios = User.query.filter(User.id.in_(participante_ids)).all()
        reunioes_participantes_map = {usuario.id: usuario for usuario in usuarios}

    return render_template(
        "empresas/departamentos.html",
        empresa=empresa,
        fiscal_form=fiscal_form,
        contabil_form=contabil_form,
        pessoal_form=pessoal_form,
        administrativo_form=administrativo_form,
        financeiro_form=financeiro_form,
        fiscal=fiscal,
        contabil=contabil,
        pessoal=pessoal,
        administrativo=administrativo,
        financeiro=financeiro,
        notas_fiscais=notas_fiscais,
        can_access_financeiro=can_access_financeiro,
        reunioes_cliente=reunioes_cliente,
        reunioes_participantes_map=reunioes_participantes_map,
        usuarios_responsaveis=usuarios_responsaveis,
        usuarios_responsaveis_ids=usuarios_responsaveis_ids,
    )


def _populate_cliente_reuniao_form(form: ClienteReuniaoForm) -> None:
    """Fill dynamic choices for the client meeting form."""

    usuarios = (
        User.query.filter_by(ativo=True)
        .order_by(User.name.asc(), User.username.asc())
        .all()
    )
    form.participantes.choices = [
        (
            usuario.id,
            (usuario.name or usuario.username or f"Usuário {usuario.id}"),
        )
        for usuario in usuarios
    ]

    setores = Setor.query.order_by(Setor.nome.asc()).all()
    setor_choices = [(0, "Selecione um setor")]
    setor_choices.extend([(setor.id, setor.nome) for setor in setores])
    form.setor_id.choices = setor_choices
    if form.setor_id.data is None:
        form.setor_id.data = 0


def _parse_cliente_reuniao_topicos(payload: str | None) -> list[str]:
    """Return a sanitized list of meeting topics."""

    if not payload:
        return []
    try:
        data = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return []
    topicos: list[str] = []
    for item in data:
        if isinstance(item, str):
            trimmed = item.strip()
            if trimmed:
                topicos.append(trimmed[:500])
    return topicos


def _resolve_reuniao_participantes(participante_ids: list[int]) -> list[tuple[int, str]]:
    """Return participant tuples preserving the original order."""

    ids = [pid for pid in participante_ids if isinstance(pid, int)]
    if not ids:
        return []
    usuarios = User.query.filter(User.id.in_(ids)).all()
    lookup = {
        usuario.id: (usuario.name or usuario.username or f"Usuário {usuario.id}")
        for usuario in usuarios
    }
    return [(pid, lookup.get(pid, f"Usuário {pid}")) for pid in ids]


@app.route("/empresa/<empresa_id>/reunioes-cliente/nova", methods=["GET", "POST"])
@app.route("/empresa/<int:id>/reunioes-cliente/nova", methods=["GET", "POST"])
@login_required
def nova_reuniao_cliente(empresa_id: str | None = None, id: int | None = None):
    """Render and process the creation form for client meetings."""

    raw_empresa = empresa_id if empresa_id is not None else id
    empresa_id_int = decode_id(str(raw_empresa), namespace="empresa")
    empresa_token = encode_id(empresa_id_int, namespace="empresa")
    empresa = Empresa.query.get_or_404(empresa_id_int)
    form = ClienteReuniaoForm()
    _populate_cliente_reuniao_form(form)

    if form.validate_on_submit():
        topicos = _parse_cliente_reuniao_topicos(form.topicos_json.data)
        reuniao = ClienteReuniao(
            empresa_id=empresa.id,
            data=form.data.data,
            setor_id=form.setor_id.data or None,
            participantes=form.participantes.data or [],
            topicos=topicos,
            decisoes=sanitize_html(form.decisoes.data or ""),
            acompanhar_ate=form.acompanhar_ate.data,
            created_by=current_user.id,
            updated_by=current_user.id,
        )
        db.session.add(reuniao)
        try:
            db.session.commit()
            flash("Reunião registrada com sucesso!", "success")
            return redirect(url_for("visualizar_empresa", empresa_id=empresa_token) + "#reunioes-cliente")
        except SQLAlchemyError as exc:
            current_app.logger.exception("Erro ao salvar reunião com cliente: %s", exc)
            db.session.rollback()
            flash("Não foi possível salvar a reunião. Tente novamente.", "danger")

    if not form.topicos_json.data:
        form.topicos_json.data = "[]"

    return render_template(
        "empresas/reuniao_cliente_form.html",
        empresa=empresa,
        form=form,
        is_edit=False,
        page_title="Adicionar reunião com cliente",
    )


@app.route("/empresa/<empresa_id>/reunioes-cliente/<reuniao_id>/editar", methods=["GET", "POST"])
@app.route("/empresa/<int:id>/reunioes-cliente/<reuniao_id>/editar", methods=["GET", "POST"])
@app.route("/empresa/<empresa_id>/reunioes-cliente/<int:rid>/editar", methods=["GET", "POST"])
@app.route("/empresa/<int:id>/reunioes-cliente/<int:rid>/editar", methods=["GET", "POST"])
@login_required
def editar_reuniao_cliente(empresa_id: str | None = None, reuniao_id: str | None = None, id: int | None = None, rid: int | None = None):
    """Allow updating an existing client meeting."""

    raw_empresa = empresa_id if empresa_id is not None else id
    raw_reuniao = reuniao_id if reuniao_id is not None else rid
    empresa_id_int = decode_id(str(raw_empresa), namespace="empresa")
    reuniao_id_int = decode_id(str(raw_reuniao), namespace="empresa-reuniao")
    empresa_token = encode_id(empresa_id_int, namespace="empresa")
    reuniao = (
        ClienteReuniao.query.filter_by(id=reuniao_id_int, empresa_id=empresa_id_int)
        .options(joinedload(ClienteReuniao.setor))
        .first_or_404()
    )
    form = ClienteReuniaoForm(obj=reuniao)
    _populate_cliente_reuniao_form(form)

    if request.method == "GET":
        form.participantes.data = reuniao.participantes or []
        form.setor_id.data = reuniao.setor_id or 0
        form.topicos_json.data = json.dumps(reuniao.topicos or [])
        form.decisoes.data = reuniao.decisoes or ""
    if form.validate_on_submit():
        topicos = _parse_cliente_reuniao_topicos(form.topicos_json.data)
        reuniao.data = form.data.data
        reuniao.setor_id = form.setor_id.data or None
        reuniao.participantes = form.participantes.data or []
        reuniao.topicos = topicos
        reuniao.decisoes = sanitize_html(form.decisoes.data or "")
        reuniao.acompanhar_ate = form.acompanhar_ate.data
        reuniao.updated_by = current_user.id
        try:
            db.session.commit()
            flash("Reunião atualizada com sucesso!", "success")
            return redirect(url_for("visualizar_empresa", empresa_id=empresa_token) + "#reunioes-cliente")
        except SQLAlchemyError as exc:
            current_app.logger.exception("Erro ao atualizar reunião com cliente: %s", exc)
            db.session.rollback()
            flash("Não foi possível atualizar a reunião. Tente novamente.", "danger")

    if not form.topicos_json.data:
        form.topicos_json.data = "[]"

    return render_template(
        "empresas/reuniao_cliente_form.html",
        empresa=reuniao.empresa,
        form=form,
        is_edit=True,
        reuniao=reuniao,
        page_title="Editar reunião com cliente",
    )


@app.route("/empresa/<empresa_id>/reunioes-cliente/<reuniao_id>")
@app.route("/empresa/<int:id>/reunioes-cliente/<reuniao_id>")
@app.route("/empresa/<empresa_id>/reunioes-cliente/<int:rid>")
@app.route("/empresa/<int:id>/reunioes-cliente/<int:rid>")
@login_required
def visualizar_reuniao_cliente(empresa_id: str | None = None, reuniao_id: str | None = None, id: int | None = None, rid: int | None = None):
    """Display a single client meeting with all recorded details."""

    raw_empresa = empresa_id if empresa_id is not None else id
    raw_reuniao = reuniao_id if reuniao_id is not None else rid
    empresa_id_int = decode_id(str(raw_empresa), namespace="empresa")
    reuniao_id_int = decode_id(str(raw_reuniao), namespace="empresa-reuniao")
    reuniao = (
        ClienteReuniao.query.filter_by(id=reuniao_id_int, empresa_id=empresa_id_int)
        .options(joinedload(ClienteReuniao.setor))
        .first_or_404()
    )
    participantes = _resolve_reuniao_participantes(reuniao.participantes or [])
    return render_template(
        "empresas/reuniao_cliente_visualizar.html",
        reuniao=reuniao,
        empresa=reuniao.empresa,
        participantes=participantes,
        topicos=reuniao.topicos or [],
    )


@app.route("/empresa/<empresa_id>/reunioes-cliente/<reuniao_id>/detalhes")
@app.route("/empresa/<int:id>/reunioes-cliente/<reuniao_id>/detalhes")
@app.route("/empresa/<empresa_id>/reunioes-cliente/<int:rid>/detalhes")
@app.route("/empresa/<int:id>/reunioes-cliente/<int:rid>/detalhes")
@login_required
def reuniao_cliente_detalhes_modal(empresa_id: str | None = None, reuniao_id: str | None = None, id: int | None = None, rid: int | None = None):
    """Return rendered HTML snippet for modal visualization."""

    raw_empresa = empresa_id if empresa_id is not None else id
    raw_reuniao = reuniao_id if reuniao_id is not None else rid
    empresa_id_int = decode_id(str(raw_empresa), namespace="empresa")
    reuniao_id_int = decode_id(str(raw_reuniao), namespace="empresa-reuniao")
    reuniao = (
        ClienteReuniao.query.filter_by(id=reuniao_id_int, empresa_id=empresa_id_int)
        .options(joinedload(ClienteReuniao.setor))
        .first_or_404()
    )
    participantes = _resolve_reuniao_participantes(reuniao.participantes or [])
    html = render_template(
        "empresas/partials/reuniao_cliente_detalhes_content.html",
        reuniao=reuniao,
        empresa=reuniao.empresa,
        participantes=participantes,
        topicos=reuniao.topicos or [],
    )
    return jsonify(
        {
            "title": f"Reunião com {reuniao.empresa.nome_empresa}",
            "html": html,
        }
    )


@app.route("/empresa/<empresa_id>/reunioes-cliente/<reuniao_id>/excluir", methods=["POST"])
@app.route("/empresa/<int:id>/reunioes-cliente/<reuniao_id>/excluir", methods=["POST"])
@app.route("/empresa/<empresa_id>/reunioes-cliente/<int:rid>/excluir", methods=["POST"])
@app.route("/empresa/<int:id>/reunioes-cliente/<int:rid>/excluir", methods=["POST"])
@login_required
def excluir_reuniao_cliente(empresa_id: str | None = None, reuniao_id: str | None = None, id: int | None = None, rid: int | None = None):
    """Delete a client meeting from the log."""

    raw_empresa = empresa_id if empresa_id is not None else id
    raw_reuniao = reuniao_id if reuniao_id is not None else rid
    empresa_id_int = decode_id(str(raw_empresa), namespace="empresa")
    reuniao_id_int = decode_id(str(raw_reuniao), namespace="empresa-reuniao")
    empresa_token = encode_id(empresa_id_int, namespace="empresa")
    reuniao = ClienteReuniao.query.filter_by(id=reuniao_id_int, empresa_id=empresa_id_int).first_or_404()
    db.session.delete(reuniao)
    try:
        db.session.commit()
        flash("Reunião excluída com sucesso.", "success")
    except SQLAlchemyError as exc:
        current_app.logger.exception("Erro ao excluir reunião com cliente: %s", exc)
        db.session.rollback()
        flash("Não foi possível excluir a reunião. Tente novamente.", "danger")
    return redirect(url_for("visualizar_empresa", empresa_id=empresa_token) + "#reunioes-cliente")

@app.route("/relatorios")
@report_access_required(report_code="index")
def relatorios():
    """Render the reports landing page."""
    return render_template("admin/relatorios.html")

@app.route("/relatorio_empresas")
@report_access_required(report_code="empresas")
def relatorio_empresas():
    """Display aggregated company statistics."""
    empresas = Empresa.query.with_entities(
        Empresa.id,
        Empresa.nome_empresa,
        Empresa.cnpj,
        Empresa.codigo_empresa,
        Empresa.tributacao,
        Empresa.sistema_utilizado,
    ).all()

    categorias = ["Simples Nacional", "Lucro Presumido", "Lucro Real"]
    grouped = {cat: [] for cat in categorias}
    grouped_sistemas = {}

    for eid, nome, cnpj, codigo, trib, sistema in empresas:
        label = trib if trib in categorias else "Outros"
        grouped.setdefault(label, []).append(
            {
                "id": eid,
                "token": encode_id(eid, namespace="empresa"),
                "nome": nome,
                "cnpj": cnpj,
                "codigo": codigo,
            }
        )

        sistema_label = sistema.strip() if sistema else "Não informado"
        grouped_sistemas.setdefault(sistema_label, []).append(
            {"nome": nome, "cnpj": cnpj, "codigo": codigo}
        )

    for empresas_list in grouped.values():
        empresas_list.sort(key=lambda item: (item.get("codigo") or "").strip())

    labels = list(grouped.keys())
    counts = [len(grouped[label]) for label in labels]
    tributacao_chart = {
        "type": "bar",
        "title": "Empresas por regime de tributação",
        "datasetLabel": "Quantidade de empresas",
        "labels": labels,
        "values": counts,
        "xTitle": "Regime",
        "yTitle": "Quantidade",
        "total": sum(counts),
    }

    sistema_labels = list(grouped_sistemas.keys())
    sistema_counts = [len(grouped_sistemas[label]) for label in sistema_labels]
    sistema_chart = {
        "type": "bar",
        "title": "Empresas por sistema utilizado",
        "datasetLabel": "Quantidade de empresas",
        "labels": sistema_labels,
        "values": sistema_counts,
        "xTitle": "Sistema",
        "yTitle": "Quantidade",
        "total": sum(sistema_counts),
    }

    return render_template(
        "admin/relatorio_empresas.html",
        tributacao_chart=tributacao_chart,
        sistema_chart=sistema_chart,
        tributacao_companies=grouped,
    )

@app.route("/relatorio_fiscal")
@report_access_required(report_code="fiscal")
def relatorio_fiscal():
    """Show summary charts for the fiscal department."""
    departamentos = (
        Departamento.query.filter_by(tipo="Departamento Fiscal")
        .join(Empresa)
        .with_entities(
            Empresa.nome_empresa,
            Empresa.codigo_empresa,
            Departamento.formas_importacao,
            Departamento.forma_movimento,
            Departamento.malote_coleta,
        )
        .all()
    )
    fiscal_form = DepartamentoFiscalForm()
    choice_map = dict(fiscal_form.formas_importacao.choices)
    import_grouped = {}
    envio_grouped = {}
    malote_grouped = {}
    for nome, codigo, formas, envio, malote in departamentos:
        formas_list = json.loads(formas) if isinstance(formas, str) else (formas or [])
        for f in formas_list:
            label = choice_map.get(f, f)
            import_grouped.setdefault(label, []).append(
                {"nome": nome, "codigo": codigo}
            )
        label_envio = envio if envio else "Não informado"
        envio_grouped.setdefault(label_envio, []).append(
            {"nome": nome, "codigo": codigo}
        )
        if envio in ("Fisico", "Digital e Físico"):
            label_malote = malote if malote else "Não informado"
            malote_grouped.setdefault(label_malote, []).append(
                {"nome": nome, "codigo": codigo}
            )
    labels_imp = list(import_grouped.keys())
    counts_imp = [len(import_grouped[label]) for label in labels_imp]
    importacao_chart = {
        "type": "bar",
        "title": "Formas de Importação (Fiscal)",
        "datasetLabel": "Quantidade de empresas",
        "labels": labels_imp,
        "values": counts_imp,
        "xTitle": "Forma",
        "yTitle": "Quantidade",
        "total": sum(counts_imp),
    }

    labels_env = list(envio_grouped.keys())
    counts_env = [len(envio_grouped[label]) for label in labels_env]
    envio_chart = {
        "type": "doughnut",
        "title": "Envio de Documentos (Fiscal)",
        "datasetLabel": "Distribuição",
        "labels": labels_env,
        "values": counts_env,
        "total": sum(counts_env),
    }

    labels_mal = list(malote_grouped.keys())
    counts_mal = [len(malote_grouped[label]) for label in labels_mal]
    malote_chart = {
        "type": "bar",
        "title": "Coleta de Malote (Envio Físico)",
        "datasetLabel": "Quantidade de empresas",
        "labels": labels_mal,
        "values": counts_mal,
        "xTitle": "Coleta",
        "yTitle": "Quantidade",
        "total": sum(counts_mal),
    }

    return render_template(
        "admin/relatorio_fiscal.html",
        importacao_chart=importacao_chart,
        envio_chart=envio_chart,
        malote_chart=malote_chart,
    )

@app.route("/relatorio_contabil")
@report_access_required(report_code="contabil")
def relatorio_contabil():
    """Show summary charts for the accounting department."""
    departamentos = (
        Departamento.query.filter_by(tipo="Departamento Contábil")
        .join(Empresa)
        .with_entities(
            Empresa.nome_empresa,
            Empresa.codigo_empresa,
            Departamento.metodo_importacao,
            Departamento.forma_movimento,
            Departamento.malote_coleta,
            Departamento.controle_relatorios,
        )
        .all()
    )
    contabil_form = DepartamentoContabilForm()
    metodo_map = dict(contabil_form.metodo_importacao.choices)
    relatorio_map = dict(contabil_form.controle_relatorios.choices)
    import_grouped = {}
    envio_grouped = {}
    malote_grouped = {}
    relatorios_grouped = {}
    for nome, codigo, metodo, envio, malote, relatorios in departamentos:
        metodo_list = json.loads(metodo) if isinstance(metodo, str) else (metodo or [])
        for m in metodo_list:
            label = metodo_map.get(m, m)
            import_grouped.setdefault(label, []).append(
                {"nome": nome, "codigo": codigo}
            )
        label_envio = envio if envio else "Não informado"
        envio_grouped.setdefault(label_envio, []).append(
            {"nome": nome, "codigo": codigo}
        )
        if envio in ("Fisico", "Digital e Físico"):
            label_malote = malote if malote else "Não informado"
            malote_grouped.setdefault(label_malote, []).append(
                {"nome": nome, "codigo": codigo}
            )
        rel_list = (
            json.loads(relatorios)
            if isinstance(relatorios, str)
            else (relatorios or [])
        )
        for r in rel_list:
            label = relatorio_map.get(r, r)
            relatorios_grouped.setdefault(label, []).append(
                {"nome": nome, "codigo": codigo}
            )
    labels_imp = list(import_grouped.keys())
    counts_imp = [len(import_grouped[label]) for label in labels_imp]
    importacao_chart = {
        "type": "bar",
        "title": "Métodos de Importação (Contábil)",
        "datasetLabel": "Quantidade de empresas",
        "labels": labels_imp,
        "values": counts_imp,
        "xTitle": "Método",
        "yTitle": "Quantidade",
        "total": sum(counts_imp),
    }

    labels_env = list(envio_grouped.keys())
    counts_env = [len(envio_grouped[label]) for label in labels_env]
    envio_chart = {
        "type": "doughnut",
        "title": "Envio de Documentos (Contábil)",
        "datasetLabel": "Distribuição",
        "labels": labels_env,
        "values": counts_env,
        "total": sum(counts_env),
    }

    labels_mal = list(malote_grouped.keys())
    counts_mal = [len(malote_grouped[label]) for label in labels_mal]
    malote_chart = {
        "type": "bar",
        "title": "Coleta de Malote (Envio Físico)",
        "datasetLabel": "Quantidade de empresas",
        "labels": labels_mal,
        "values": counts_mal,
        "xTitle": "Coleta",
        "yTitle": "Quantidade",
        "total": sum(counts_mal),
    }

    labels_rel = list(relatorios_grouped.keys())
    counts_rel = [len(relatorios_grouped[label]) for label in labels_rel]
    relatorios_chart = {
        "type": "bar",
        "title": "Controle de Relatórios (Contábil)",
        "datasetLabel": "Quantidade de empresas",
        "labels": labels_rel,
        "values": counts_rel,
        "xTitle": "Relatório",
        "yTitle": "Quantidade",
        "total": sum(counts_rel),
    }

    return render_template(
        "admin/relatorio_contabil.html",
        importacao_chart=importacao_chart,
        envio_chart=envio_chart,
        malote_chart=malote_chart,
        relatorios_chart=relatorios_chart,
    )

@app.route("/relatorio_usuarios")
@report_access_required(report_code="usuarios")
def relatorio_usuarios():
    """Visualize user counts by role and status."""
    users = User.query.with_entities(
        User.username, User.name, User.email, User.role, User.ativo
    ).all()
    grouped = {}
    labels = []
    counts = []
    for username, name, email, role, ativo in users:
        tipo = "Admin" if role == "admin" else "Usuário"
        status = "Ativo" if ativo else "Inativo"
        label = f"{tipo} {status}"
        grouped.setdefault(label, []).append(
            {"username": username, "name": name, "email": email}
        )
    for label, usuarios in grouped.items():
        labels.append(label)
        counts.append(len(usuarios))
    users_chart = {
        "type": "doughnut",
        "title": "Usuários por tipo e status",
        "datasetLabel": "Distribuição",
        "labels": labels,
        "values": counts,
        "total": sum(counts),
    }

    return render_template(
        "admin/relatorio_usuarios.html",
        users_chart=users_chart,
    )

@app.route("/relatorio_cursos")
@report_access_required(report_code="cursos")
def relatorio_cursos():
    """Show aggregated metrics for the internal course catalog."""
    records = get_courses_overview()
    total_courses = len(records)
    today = date.today()

    def _normalize_date(value: date | datetime | None) -> date | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        return value
    status_counts: Counter[CourseStatus] = Counter(record.status for record in records)
    status_labels_map = {
        CourseStatus.PLANNED: "Planejado",
        CourseStatus.DELAYED: "Atrasado",
        CourseStatus.POSTPONED: "Adiamento",
        CourseStatus.CANCELLED: "Cancelado",
        CourseStatus.COMPLETED: "Concluido",
    }
    status_labels = []
    status_values = []
    for status in CourseStatus:
        status_labels.append(status_labels_map[status])
        status_values.append(status_counts.get(status, 0))
    status_chart = {
        "type": "doughnut",
        "title": "Distribuicao por status",
        "datasetLabel": "Cursos",
        "labels": status_labels,
        "values": status_values,
        "total": total_courses,
    }

    def _month_key(anchor: date, offset: int) -> date:
        year = anchor.year
        month = anchor.month - offset
        while month <= 0:
            month += 12
            year -= 1
        return date(year, month, 1)

    months_window = 6
    current_month_start = date(today.year, today.month, 1)
    month_labels: list[str] = []
    month_index: dict[tuple[int, int], int] = {}
    for offset in range(months_window - 1, -1, -1):
        month_date = _month_key(current_month_start, offset)
        label = month_date.strftime("%b/%Y")
        month_labels.append(label)
        month_index[(month_date.year, month_date.month)] = len(month_labels) - 1
    earliest_month = _month_key(current_month_start, months_window - 1)
    planned_counts = [0] * len(month_labels)
    completed_counts = [0] * len(month_labels)
    for record in records:
        start_date = _normalize_date(record.start_date)
        if start_date and start_date >= earliest_month:
            idx = month_index.get((start_date.year, start_date.month))
            if idx is not None and record.status != CourseStatus.COMPLETED:
                planned_counts[idx] += 1
        completion_date = _normalize_date(record.completion_date)
        if completion_date and completion_date >= earliest_month:
            idx = month_index.get((completion_date.year, completion_date.month))
            if idx is not None:
                completed_counts[idx] += 1
    flow_chart = {
        "type": "bar",
        "title": "Cronograma de cursos",
        "labels": month_labels,
        "datasets": [
            {
                "label": "Planejados",
                "values": planned_counts,
                "backgroundColor": "#f97316",
            },
            {
                "label": "Concluidos",
                "values": completed_counts,
                "backgroundColor": "#22c55e",
            },
        ],
        "xTitle": "Mes",
        "yTitle": "Quantidade",
    }

    instructor_counts: Counter[str] = Counter()
    sector_counts: Counter[str] = Counter()
    participant_total = 0
    participant_counts: Counter[str] = Counter()
    workload_hours_sum = 0.0
    workload_count = 0
    for record in records:
        instructor = (record.instructor or "Sem instrutor").strip() or "Sem instrutor"
        instructor_counts[instructor] += 1
        for sector in record.sectors:
            label = sector.strip()
            if label:
                sector_counts[label] += 1
        participant_total += len(record.participants_raw)
        for participant in record.participants_raw:
            label = (participant or "").strip()
            if label:
                participant_counts[label] += 1
        if record.workload:
            workload_hours_sum += record.workload.hour + record.workload.minute / 60
            workload_count += 1
    top_instructors = instructor_counts.most_common(5)
    instructor_chart = {
        "type": "bar",
        "title": "Instrutores com mais cursos",
        "datasetLabel": "Cursos",
        "labels": [name for name, _ in top_instructors],
        "values": [count for _, count in top_instructors],
        "xTitle": "Instrutor",
        "yTitle": "Quantidade",
        "total": sum(count for _, count in top_instructors),
    }
    top_sectors = sector_counts.most_common(6)
    sector_chart = {
        "type": "bar",
        "title": "Setores atendidos",
        "datasetLabel": "Cursos",
        "labels": [name for name, _ in top_sectors],
        "values": [count for _, count in top_sectors],
        "xTitle": "Setor",
        "yTitle": "Participacoes",
        "total": sum(count for _, count in top_sectors),
    }
    top_participants = participant_counts.most_common(10)
    participants_chart = (
        {
            "type": "bar",
            "title": "Participantes mais presentes",
            "datasetLabel": "Participacoes",
            "labels": [name for name, _ in top_participants],
            "values": [count for _, count in top_participants],
            "xTitle": "Participante",
            "yTitle": "Participacoes",
            "total": sum(count for _, count in top_participants),
        }
        if top_participants
        else None
    )

    upcoming_30 = sum(
        1
        for record in records
        if _normalize_date(record.start_date)
        and today <= _normalize_date(record.start_date) <= today + timedelta(days=30)
    )
    completed_90 = sum(
        1
        for record in records
        if record.status == CourseStatus.COMPLETED
        and _normalize_date(record.completion_date)
        and _normalize_date(record.completion_date) >= today - timedelta(days=90)
    )
    avg_workload_hours = (
        workload_hours_sum / workload_count if workload_count else None
    )
    active_courses = sum(
        1
        for record in records
        if record.status in {CourseStatus.PLANNED, CourseStatus.DELAYED, CourseStatus.POSTPONED}
    )

    kpis = [
        {
            "label": "Cursos ativos",
            "value": active_courses,
            "description": "Planejados ou em ajuste",
        },
        {
            "label": "Concluidos (90 dias)",
            "value": completed_90,
            "description": "Encerrados no ultimo trimestre",
        },
        {
            "label": "Previstos (30 dias)",
            "value": upcoming_30,
            "description": "Inicios previstos ate 30 dias",
        },
        {
            "label": "Participantes estimados",
            "value": participant_total,
            "description": "Somatorio dos inscritos",
        },
    ]

    return render_template(
        "admin/relatorio_cursos.html",
        kpis=kpis,
        status_chart=status_chart,
        flow_chart=flow_chart,
        instructor_chart=instructor_chart,
        sector_chart=sector_chart,
        participants_chart=participants_chart,
        avg_workload_hours=avg_workload_hours,
        total_courses=total_courses,
    )


@app.route("/relatorio_tarefas")
@report_access_required(report_code="tarefas")
def relatorio_tarefas():
    """Expose tactical dashboards about the global task workload."""
    today = date.today()
    upcoming_limit = today + timedelta(days=7)
    now = utc3_now()
    trend_weeks = 6
    overview_query = (
        Task.query.join(Tag)
        .filter(Task.parent_id.is_(None))
        .filter(Task.is_private.is_(False))
        .filter(~Tag.nome.in_(EXCLUDED_TASK_TAGS))
    )
    open_tasks_query = overview_query.filter(Task.status != TaskStatus.DONE)

    total_tasks = overview_query.count()
    open_tasks = open_tasks_query.count()
    completed_last_30 = (
        overview_query.filter(
            Task.status == TaskStatus.DONE,
            Task.completed_at.isnot(None),
            Task.completed_at >= now - timedelta(days=30),
        ).count()
    )
    overdue_tasks = (
        open_tasks_query.filter(
            Task.due_date.isnot(None),
            Task.due_date < today,
        ).count()
    )
    due_soon_tasks = (
        open_tasks_query.filter(
            Task.due_date.isnot(None),
            Task.due_date >= today,
            Task.due_date <= upcoming_limit,
        ).count()
    )
    no_due_date_tasks = open_tasks_query.filter(Task.due_date.is_(None)).count()
    unassigned_tasks = open_tasks_query.filter(Task.assigned_to.is_(None)).count()
    on_track_tasks = max(open_tasks - overdue_tasks - due_soon_tasks - no_due_date_tasks, 0)

    status_rows = (
        overview_query.with_entities(Task.status, db.func.count(Task.id))
        .group_by(Task.status)
        .all()
    )
    status_labels_map = {
        TaskStatus.PENDING: "Pendentes",
        TaskStatus.IN_PROGRESS: "Em andamento",
        TaskStatus.DONE: "Concluidas",
    }
    status_labels = []
    status_values = []
    for status in TaskStatus:
        status_labels.append(status_labels_map[status])
        count = next((qty for st, qty in status_rows if st == status), 0)
        status_values.append(count)
    status_chart = {
        "type": "doughnut",
        "title": "Distribuicao por status",
        "datasetLabel": "Tarefas",
        "labels": status_labels,
        "values": status_values,
        "total": total_tasks,
    }

    priority_rows = (
        overview_query.with_entities(Task.priority, db.func.count(Task.id))
        .filter(Task.status != TaskStatus.DONE)
        .group_by(Task.priority)
        .all()
    )
    priority_labels_map = {
        TaskPriority.LOW: "Baixa",
        TaskPriority.MEDIUM: "Media",
        TaskPriority.HIGH: "Alta",
    }
    priority_labels = []
    priority_values = []
    for priority in TaskPriority:
        priority_labels.append(priority_labels_map[priority])
        count = next((qty for pr, qty in priority_rows if pr == priority), 0)
        priority_values.append(count)
    priority_chart = {
        "type": "bar",
        "title": "Prioridade das tarefas abertas",
        "datasetLabel": "Tarefas abertas",
        "labels": priority_labels,
        "values": priority_values,
        "xTitle": "Prioridade",
        "yTitle": "Quantidade",
        "total": sum(priority_values),
    }

    deadline_chart = {
        "type": "doughnut",
        "title": "Risco de prazo (tarefas abertas)",
        "datasetLabel": "Tarefas",
        "labels": [
            "Atrasadas",
            "Proximos 7 dias",
            "Sem prazo definido",
            "No prazo",
        ],
        "values": [
            overdue_tasks,
            due_soon_tasks,
            no_due_date_tasks,
            on_track_tasks,
        ],
        "total": open_tasks,
    }

    trend_anchor = today - timedelta(days=today.weekday())
    week_windows: list[tuple[date, date]] = []
    for index in range(trend_weeks):
        start = trend_anchor - timedelta(weeks=trend_weeks - 1 - index)
        end = start + timedelta(days=6)
        week_windows.append((start, end))
    first_window_start = week_windows[0][0]
    trend_start_dt = datetime.combine(first_window_start, datetime.min.time())
    completed_recent = (
        overview_query.with_entities(Task.completed_at)
        .filter(
            Task.status == TaskStatus.DONE,
            Task.completed_at.isnot(None),
            Task.completed_at >= trend_start_dt,
        )
        .all()
    )
    created_recent = (
        overview_query.with_entities(Task.created_at)
        .filter(
            Task.created_at.isnot(None),
            Task.created_at >= trend_start_dt,
        )
        .all()
    )
    completion_by_day: dict[date, int] = {}
    for (completed_at,) in completed_recent:
        completion_by_day.setdefault(completed_at.date(), 0)
        completion_by_day[completed_at.date()] += 1
    creation_by_day: dict[date, int] = {}
    for (created_at,) in created_recent:
        creation_by_day.setdefault(created_at.date(), 0)
        creation_by_day[created_at.date()] += 1
    trend_labels: list[str] = []
    completion_counts: list[int] = []
    creation_counts: list[int] = []
    for start, end in week_windows:
        label = f"{start.strftime('%d/%m')} - {end.strftime('%d/%m')}"
        trend_labels.append(label)
        completed_count = 0
        created_count = 0
        span = (end - start).days + 1
        for offset in range(span):
            day = start + timedelta(days=offset)
            completed_count += completion_by_day.get(day, 0)
            created_count += creation_by_day.get(day, 0)
        completion_counts.append(completed_count)
        creation_counts.append(created_count)
    flow_chart = {
        "type": "line",
        "title": "Fluxo semanal: criacoes x conclusoes",
        "labels": trend_labels,
        "datasets": [
            {
                "label": "Criadas",
                "values": creation_counts,
                "borderColor": "#0ea5e9",
                "backgroundColor": "rgba(14,165,233,0.2)",
                "fill": False,
                "tension": 0.35,
            },
            {
                "label": "Concluidas",
                "values": completion_counts,
                "borderColor": "#22c55e",
                "backgroundColor": "rgba(34,197,94,0.2)",
                "fill": False,
                "tension": 0.35,
            },
        ],
        "xTitle": "Semana",
        "yTitle": "Quantidade",
    }

    def _subtract_months(base: date, months: int) -> date:
        year = base.year
        month = base.month - months
        while month <= 0:
            month += 12
            year -= 1
        return date(year, month, 1)

    months_window = 4
    current_month_start = date(today.year, today.month, 1)
    month_labels: list[str] = []
    month_index: dict[tuple[int, int], int] = {}
    for offset in range(months_window - 1, -1, -1):
        month_date = _subtract_months(current_month_start, offset)
        label = month_date.strftime("%b/%Y")
        month_labels.append(label)
        month_index[(month_date.year, month_date.month)] = len(month_labels) - 1
    earliest_month = _subtract_months(current_month_start, months_window - 1)
    area_rows = (
        overview_query.with_entities(Task.created_at, Tag.nome)
        .filter(Task.created_at.isnot(None))
        .filter(Task.created_at >= datetime.combine(earliest_month, datetime.min.time()))
        .all()
    )
    counts_by_area: dict[str, list[int]] = {}
    for created_at, tag_name in area_rows:
        if not created_at:
            continue
        created_date = created_at.date()
        month_key = (created_date.year, created_date.month)
        idx = month_index.get(month_key)
        if idx is None:
            continue
        label = tag_name or "Sem setor"
        counts_by_area.setdefault(label, [0] * len(month_labels))
        counts_by_area[label][idx] += 1
    area_datasets: list[dict[str, object]] = []
    if counts_by_area:
        sorted_areas = sorted(
            counts_by_area.items(), key=lambda item: sum(item[1]), reverse=True
        )
        for area_label, values in sorted_areas[:4]:
            area_datasets.append(
                {
                    "label": area_label,
                    "values": values,
                    "type": "line",
                    "fill": False,
                }
            )
    service_area_chart = (
        {
            "type": "line",
            "title": "Chamados por area de atendimento (ultimos meses)",
            "labels": month_labels,
            "datasets": area_datasets,
            "xTitle": "Mes",
            "yTitle": "Chamados",
        }
        if area_datasets
        else None
    )

    sector_rows = (
        overview_query.with_entities(Tag.nome, db.func.count(Task.id))
        .filter(Task.status != TaskStatus.DONE)
        .group_by(Tag.nome)
        .order_by(db.func.count(Task.id).desc())
        .limit(8)
        .all()
    )
    sector_labels = []
    sector_values = []
    for nome, quantidade in sector_rows:
        sector_labels.append(nome or "Sem setor")
        sector_values.append(quantidade)
    sector_chart = {
        "type": "bar",
        "title": "Setores com mais tarefas abertas",
        "datasetLabel": "Tarefas abertas",
        "labels": sector_labels,
        "values": sector_values,
        "xTitle": "Setor",
        "yTitle": "Quantidade",
        "total": sum(sector_values),
    }

    user_rows = (
        overview_query.join(User, User.id == Task.assigned_to)
        .with_entities(User.id, User.name, User.username, db.func.count(Task.id))
        .filter(Task.status != TaskStatus.DONE)
        .group_by(User.id, User.name, User.username)
        .order_by(db.func.count(Task.id).desc())
        .limit(8)
        .all()
    )
    user_labels = []
    user_values = []
    for user_id, name, username, quantidade in user_rows:
        display_name = (name or username or "").strip()
        if not display_name:
            display_name = f"Usuario {user_id}"
        user_labels.append(display_name)
        user_values.append(quantidade)
    user_chart = {
        "type": "bar",
        "title": "Usuarios com mais tarefas abertas",
        "datasetLabel": "Tarefas abertas",
        "labels": user_labels,
        "values": user_values,
        "xTitle": "Usuario",
        "yTitle": "Quantidade",
        "total": sum(user_values),
    }

    subject_rows = (
        overview_query.with_entities(Task.title, db.func.count(Task.id))
        .group_by(Task.title)
        .order_by(db.func.count(Task.id).desc())
        .limit(10)
        .all()
    )
    subject_labels = []
    subject_values = []
    for title, quantidade in subject_rows:
        label = (title or "Sem assunto").strip() or "Sem assunto"
        subject_labels.append(label)
        subject_values.append(quantidade)

    open_task_dates = open_tasks_query.with_entities(Task.created_at).all()
    age_buckets = [
        ("0-7 dias", 0, 7),
        ("8-14 dias", 8, 14),
        ("15-30 dias", 15, 30),
        ("+30 dias", 31, None),
    ]
    bucket_counts = {label: 0 for label, _, _ in age_buckets}
    total_age_days = 0
    open_age_samples = 0
    for (created_at,) in open_task_dates:
        if not created_at:
            continue
        age_days = (today - created_at.date()).days
        if age_days < 0:
            continue
        total_age_days += age_days
        open_age_samples += 1
        for label, start, end in age_buckets:
            if end is None and age_days >= start:
                bucket_counts[label] += 1
                break
            if end is not None and start <= age_days <= end:
                bucket_counts[label] += 1
                break
    avg_open_age_days = (total_age_days / open_age_samples) if open_age_samples else None
    aging_chart = {
        "type": "bar",
        "title": "Idade das tarefas em aberto",
        "datasetLabel": "Quantidade de tarefas",
        "labels": [label for label, _, _ in age_buckets],
        "values": [bucket_counts[label] for label, _, _ in age_buckets],
        "xTitle": "Faixa de idade",
        "yTitle": "Quantidade",
        "total": sum(bucket_counts.values()),
    } if open_age_samples else None

    completed_speed_rows = (
        overview_query.with_entities(Task.created_at, Task.completed_at)
        .filter(
            Task.status == TaskStatus.DONE,
            Task.completed_at.isnot(None),
            Task.created_at.isnot(None),
            Task.completed_at >= now - timedelta(days=30),
        )
        .all()
    )
    avg_completion_days: float | None = None
    if completed_speed_rows:
        total_days = 0.0
        for created_at, completed_at in completed_speed_rows:
            delta = completed_at - created_at
            total_days += delta.total_seconds() / 86400
        avg_completion_days = total_days / len(completed_speed_rows)

    overview_task_rows = overview_query.with_entities(
        Task.id, Task.created_at, Task.completed_at
    ).all()
    task_meta = {
        task_id: {"created_at": created_at, "completed_at": completed_at}
        for task_id, created_at, completed_at in overview_task_rows
    }
    task_ids = list(task_meta.keys())

    time_to_completion_seconds: list[float] = []
    for meta in task_meta.values():
        created_at = meta["created_at"]
        completed_at = meta["completed_at"]
        if created_at and completed_at:
            delta = completed_at - created_at
            seconds = delta.total_seconds()
            if seconds >= 0:
                time_to_completion_seconds.append(seconds)

    pending_to_progress_seconds: list[float] = []
    progress_to_done_seconds: list[float] = []
    reopened_last_30 = 0
    reopened_current_month = 0
    month_start = date(today.year, today.month, 1)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    if task_ids:
        history_cutoff = now - timedelta(days=30)
        history_rows = (
            TaskStatusHistory.query.with_entities(
                TaskStatusHistory.task_id,
                TaskStatusHistory.from_status,
                TaskStatusHistory.to_status,
                TaskStatusHistory.changed_at,
            )
            .filter(TaskStatusHistory.task_id.in_(task_ids))
            .order_by(TaskStatusHistory.task_id, TaskStatusHistory.changed_at)
            .all()
        )
        history_map: dict[int, list[tuple[TaskStatus | None, TaskStatus, datetime]]] = defaultdict(list)
        for task_id, from_status, to_status, changed_at in history_rows:
            history_map[task_id].append((from_status, to_status, changed_at))

        for task_id, entries in history_map.items():
            meta = task_meta.get(task_id)
            if not meta:
                continue
            last_pending_time = meta["created_at"]
            last_in_progress_time = None
            for from_status, to_status, changed_at in entries:
                if not changed_at:
                    continue
                if (
                    from_status == TaskStatus.DONE
                    and to_status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)
                ):
                    if changed_at >= history_cutoff:
                        reopened_last_30 += 1
                    if month_start <= changed_at.date() < next_month:
                        reopened_current_month += 1
                if to_status == TaskStatus.PENDING:
                    last_pending_time = changed_at
                elif to_status == TaskStatus.IN_PROGRESS:
                    if last_pending_time:
                        delta = changed_at - last_pending_time
                        seconds = delta.total_seconds()
                        if seconds >= 0:
                            pending_to_progress_seconds.append(seconds)
                    last_in_progress_time = changed_at
                elif to_status == TaskStatus.DONE:
                    if last_in_progress_time:
                        delta = changed_at - last_in_progress_time
                        seconds = delta.total_seconds()
                        if seconds >= 0:
                            progress_to_done_seconds.append(seconds)

    def _avg_seconds(values: list[float]) -> float | None:
        if not values:
            return None
        return sum(values) / len(values)

    avg_creation_to_completion_seconds = _avg_seconds(time_to_completion_seconds)
    avg_pending_to_progress_seconds = _avg_seconds(pending_to_progress_seconds)
    avg_progress_to_done_seconds = _avg_seconds(progress_to_done_seconds)

    def _format_duration(seconds: float | None) -> str | None:
        if seconds is None:
            return None
        total_seconds = int(seconds)
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes = remainder // 60
        parts: list[str] = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if not parts and minutes:
            parts.append(f"{minutes}min")
        if not parts:
            parts.append("menos de 1 min")
        return " ".join(parts[:2])

    def _duration_source_label(count: int, noun: str) -> str:
        if count:
            return f"Media baseada em {count} {noun}"
        return "Sem dados suficientes"

    duration_metrics = [
        {
            "label": "Abertura -> Conclusao",
            "value": _format_duration(avg_creation_to_completion_seconds),
            "description": _duration_source_label(
                len(time_to_completion_seconds), "tarefas concluidas"
            ),
        },
        {
            "label": "Pendente -> Em andamento",
            "value": _format_duration(avg_pending_to_progress_seconds),
            "description": _duration_source_label(
                len(pending_to_progress_seconds), "transicoes registradas"
            ),
        },
        {
            "label": "Em andamento -> Concluida",
            "value": _format_duration(avg_progress_to_done_seconds),
            "description": _duration_source_label(
                len(progress_to_done_seconds), "transicoes registradas"
            ),
        },
    ]

    def _percent(part: int, whole: int) -> float:
        if not whole:
            return 0.0
        return (part / whole) * 100

    created_this_month = (
        overview_query.filter(
            Task.created_at.isnot(None),
            Task.created_at >= datetime.combine(month_start, datetime.min.time()),
            Task.created_at < datetime.combine(next_month, datetime.min.time()),
        ).count()
    )
    completed_this_month = (
        overview_query.filter(
            Task.completed_at.isnot(None),
            Task.completed_at >= datetime.combine(month_start, datetime.min.time()),
            Task.completed_at < datetime.combine(next_month, datetime.min.time()),
        ).count()
    )
    general_overview_month = {
        "created": created_this_month,
        "completed": completed_this_month,
        "reopened": reopened_current_month,
        "net": created_this_month - completed_this_month,
        "backlog": open_tasks,
    }
    general_overview_chart = {
        "type": "doughnut",
        "title": "Distribuicao do mes atual",
        "datasetLabel": "Tarefas",
        "labels": ["Criadas", "Concluidas", "Reabertas", "Backlog"],
        "values": [
            created_this_month,
            completed_this_month,
            reopened_current_month,
            open_tasks,
        ],
        "total": (
            created_this_month
            + completed_this_month
            + reopened_current_month
            + open_tasks
        ),
    }


    insights = [
        {
            "title": "Tarefas sem responsavel",
            "value": unassigned_tasks,
            "detail": f"{_percent(unassigned_tasks, open_tasks):.1f}% do backlog em aberto"
            if open_tasks
            else "Sem backlog em aberto",
        },
        {
            "title": "Sem prazo definido",
            "value": no_due_date_tasks,
            "detail": f"{_percent(no_due_date_tasks, open_tasks):.1f}% das tarefas abertas"
            if open_tasks
            else "Sem backlog em aberto",
        },
        {
            "title": "Idade media do backlog",
            "value": f"{avg_open_age_days:.1f} dias" if avg_open_age_days is not None else "Sem dados",
            "detail": "Tempo medio desde a criacao ate agora das tarefas abertas",
        },
        {
            "title": "Tarefas reabertas (30 dias)",
            "value": reopened_last_30,
            "detail": "Transicoes de concluida para pendente/em andamento nos ultimos 30 dias",
        },
    ]

    done_tasks = next((qty for st, qty in status_rows if st == TaskStatus.DONE), 0)
    completion_rate = (done_tasks / total_tasks * 100) if total_tasks else 0
    kpis = [
        {
            "label": "Tarefas totais",
            "value": total_tasks,
            "icon": "bi-stack",
            "description": "Acumulado em todo o sistema",
        },
        {
            "label": "Em aberto",
            "value": open_tasks,
            "icon": "bi-kanban",
            "description": "Pendentes + em andamento",
        },
        {
            "label": "Atrasadas",
            "value": overdue_tasks,
            "icon": "bi-exclamation-octagon",
            "description": "Necessitam atencao imediata",
        },
        {
            "label": "Concluidas (30 dias)",
            "value": completed_last_30,
            "icon": "bi-check2-circle",
            "description": "Fluxo recente de entregas",
        },
    ]

    return render_template(
        "admin/relatorio_tarefas.html",
        kpis=kpis,
        status_chart=status_chart,
        priority_chart=priority_chart,
        deadline_chart=deadline_chart,
        flow_chart=flow_chart,
        sector_chart=sector_chart,
        user_chart=user_chart,
        aging_chart=aging_chart,
        service_area_chart=service_area_chart,
        general_overview_month=general_overview_month,
        general_overview_chart=general_overview_chart,
        current_date=today,
        insights=insights,
        duration_metrics=duration_metrics,
        completion_rate=completion_rate,
        avg_completion_days=avg_completion_days,
        overdue_tasks=overdue_tasks,
        due_soon_tasks=due_soon_tasks,
        no_due_date_tasks=no_due_date_tasks,
        open_tasks=open_tasks,
        total_tasks=total_tasks,
    )


# =============================================================================
# @app.route("/logout") - Migrado para blueprints/auth.py (2024-12)
# =============================================================================

# =============================================================================
# ROTAS DE USERS - MIGRADAS PARA blueprints/users.py (2024-12)
# =============================================================================
# As rotas abaixo foram migradas para o blueprint users_bp
# TODO: Remover estas rotas apos confirmar que o blueprint esta funcionando
# =============================================================================

@app.route("/users/active", methods=["GET"], endpoint="list_active_users")
@app.route("/users", methods=["GET", "POST"])
@admin_required
def list_users():
    """List and register users in the admin panel."""
    form = RegistrationForm()
    edit_form = EditUserForm(prefix="edit")
    tag_create_form = TagForm(prefix="tag_create")
    tag_create_form.submit.label.text = "Adicionar"
    tag_edit_form = TagForm(prefix="tag_edit")
    tag_edit_form.submit.label.text = "Salvar alterações"
    tag_delete_form = TagDeleteForm()
    # Use cached tags to reduce database load (5-minute cache)
    from app.services.cache_service import get_all_tags_cached
    tag_list = get_all_tags_cached()
    form.tags.choices = [(t.id, t.nome) for t in tag_list]
    edit_form.tags.choices = [(t.id, t.nome) for t in tag_list]
    show_inactive = request.args.get("show_inactive") in ("1", "on", "true", "True")
    raw_tag_ids = request.args.getlist("tag_id")
    selected_tag_ids = []
    for raw_id in raw_tag_ids:
        try:
            tag_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if tag_id not in selected_tag_ids:
            selected_tag_ids.append(tag_id)
    selected_tag_id = selected_tag_ids[0] if len(selected_tag_ids) == 1 else None
    open_tag_modal = request.args.get("open_tag_modal") in ("1", "true", "True")
    open_user_modal = request.args.get("open_user_modal") in ("1", "true", "True")
    open_edit_modal = request.args.get("open_edit_modal") in ("1", "true", "True")
    edit_tag = None
    edit_tag_id_arg = request.args.get("edit_tag_id", type=int)
    editing_user = None
    editing_user_id = request.args.get("edit_user_id", type=int)
    edit_password_error = None
    if edit_tag_id_arg:
        open_tag_modal = True
        edit_tag = Tag.query.get(edit_tag_id_arg)
        if not edit_tag:
            flash("Tag não encontrada.", "warning")
        elif request.method == "GET":
            tag_edit_form.nome.data = edit_tag.nome

    if editing_user_id:
        editing_user = User.query.get(editing_user_id)
        if not editing_user:
            flash("Usuário não encontrado.", "warning")
            editing_user_id = None
        else:
            if editing_user.is_master and current_user.id != editing_user.id:
                abort(403)
            if request.method == "GET":
                edit_form.process(obj=editing_user)
                edit_form.tags.data = [t.id for t in editing_user.tags]
            open_edit_modal = True

    if request.method == "POST":
        form_name = request.form.get("form_name")

        if form_name == "user":
            open_user_modal = True
            if form.validate_on_submit():
                existing_user = User.query.filter(
                    (User.username == form.username.data)
                    | (User.email == form.email.data)
                ).first()
                if existing_user:
                    if existing_user.username == form.username.data:
                        form.username.errors.append("Usuário já cadastrado.")
                    if existing_user.email == form.email.data:
                        form.email.errors.append("Email já cadastrado.")
                    flash("Usuário ou email já cadastrado.", "warning")
                else:
                    from app.utils.audit import log_user_action, ActionType, ResourceType

                    user = User(
                        username=form.username.data,
                        email=form.email.data,
                        name=form.name.data,
                        role=form.role.data,
                    )
                    user.set_password(form.password.data)
                    if form.tags.data:
                        user.tags = Tag.query.filter(Tag.id.in_(form.tags.data)).all()
                    db.session.add(user)
                    db.session.commit()

                    # Log user creation
                    log_user_action(
                        action_type=ActionType.CREATE,
                        resource_type=ResourceType.USER,
                        action_description=f'Criou usuario {user.username}',
                        resource_id=user.id,
                        new_values={
                            'username': user.username,
                            'email': user.email,
                            'name': user.name,
                            'role': user.role,
                            'tags': [tag.nome for tag in user.tags] if user.tags else [],
                        }
                    )

                    flash("Novo usuário cadastrado com sucesso!", "success")
                    return redirect(url_for("list_users"))

        if form_name == "user_edit":
            open_edit_modal = True
            edit_user_id_raw = request.form.get("user_id")
            try:
                editing_user_id = int(edit_user_id_raw) if edit_user_id_raw is not None else None
            except (TypeError, ValueError):
                editing_user_id = None
            editing_user = (
                User.query.options(joinedload(User.tags)).get(editing_user_id)
                if editing_user_id is not None
                else None
            )
            if not editing_user:
                flash("Usuário não encontrado.", "warning")
            else:
                if editing_user.is_master and current_user.id != editing_user.id:
                    abort(403)
                if edit_form.validate_on_submit():
                    from app.utils.audit import log_user_action, ActionType, ResourceType

                    # Capture old values before changes
                    old_values = {
                        'username': editing_user.username,
                        'email': editing_user.email,
                        'name': editing_user.name,
                        'role': editing_user.role,
                        'ativo': editing_user.ativo,
                        'tags': [tag.nome for tag in editing_user.tags] if editing_user.tags else [],
                    }

                    editing_user.username = edit_form.username.data
                    editing_user.email = edit_form.email.data
                    editing_user.name = edit_form.name.data
                    if not editing_user.is_master:
                        editing_user.role = edit_form.role.data
                        editing_user.ativo = edit_form.ativo.data
                    else:
                        editing_user.ativo = True
                    if edit_form.tags.data:
                        editing_user.tags = (
                            Tag.query.filter(Tag.id.in_(edit_form.tags.data)).all()
                        )
                    else:
                        editing_user.tags = []

                    new_password = request.form.get("new_password")
                    confirm_new_password = request.form.get("confirm_new_password")
                    password_changed = False
                    if new_password:
                        if new_password != confirm_new_password:
                            edit_password_error = "As senhas devem ser iguais."
                        else:
                            editing_user.set_password(new_password)
                            password_changed = True

                    if edit_password_error:
                        flash(edit_password_error, "danger")
                    else:
                        db.session.commit()

                        # Capture new values after changes
                        new_values = {
                            'username': editing_user.username,
                            'email': editing_user.email,
                            'name': editing_user.name,
                            'role': editing_user.role,
                            'ativo': editing_user.ativo,
                            'tags': [tag.nome for tag in editing_user.tags] if editing_user.tags else [],
                        }

                        # Log user update
                        log_user_action(
                            action_type=ActionType.UPDATE,
                            resource_type=ResourceType.USER,
                            action_description=f'Atualizou usuario {editing_user.username}',
                            resource_id=editing_user.id,
                            old_values=old_values,
                            new_values=new_values,
                        )

                        # Log password change separately if applicable
                        if password_changed:
                            log_user_action(
                                action_type=ActionType.CHANGE_PASSWORD,
                                resource_type=ResourceType.USER,
                                action_description=f'Trocou senha do usuario {editing_user.username}',
                                resource_id=editing_user.id,
                            )

                        flash("Usuário atualizado com sucesso!", "success")
                        return redirect(url_for("list_users"))

        if form_name == "tag_create":
            open_tag_modal = True
            if tag_create_form.validate_on_submit():
                tag_name = (tag_create_form.nome.data or "").strip()
                existing_tag = (
                    Tag.query.filter(db.func.lower(Tag.nome) == tag_name.lower()).first()
                    if tag_name
                    else None
                )
                if existing_tag:
                    tag_create_form.nome.errors.append("Já existe uma tag com esse nome.")
                    flash("Já existe uma tag com esse nome.", "warning")
                elif tag_name:
                    tag = Tag(nome=tag_name)
                    db.session.add(tag)
                    db.session.commit()
                    # Invalidate tag cache after creating new tag
                    from app.services.cache_service import invalidate_tag_cache
                    invalidate_tag_cache()
                    flash("Tag cadastrada com sucesso!", "success")
                    return redirect(url_for("list_users", open_tag_modal="1"))

        if form_name == "tag_edit":
            open_tag_modal = True
            tag_id_raw = request.form.get("tag_id")
            try:
                tag_id = int(tag_id_raw) if tag_id_raw is not None else None
            except (TypeError, ValueError):
                tag_id = None
            if tag_id is not None:
                edit_tag = Tag.query.get(tag_id)
            if not edit_tag:
                flash("Tag não encontrada.", "warning")
            elif tag_edit_form.validate_on_submit():
                new_name = (tag_edit_form.nome.data or "").strip()
                if not new_name:
                    tag_edit_form.nome.errors.append("Informe um nome para a tag.")
                else:
                    duplicate = (
                        Tag.query.filter(
                            db.func.lower(Tag.nome) == new_name.lower(), Tag.id != edit_tag.id
                        ).first()
                    )
                    if duplicate:
                        tag_edit_form.nome.errors.append("Já existe uma tag com esse nome.")
                        flash("Já existe uma tag com esse nome.", "warning")
                    else:
                        edit_tag.nome = new_name
                        db.session.commit()
                        # Invalidate tag cache after editing tag
                        from app.services.cache_service import invalidate_tag_cache
                        invalidate_tag_cache()
                        flash("Tag atualizada com sucesso!", "success")
                        return redirect(url_for("list_users", open_tag_modal="1"))

        if form_name == "tag_delete":
            open_tag_modal = True
            if tag_delete_form.validate_on_submit():
                tag_id_raw = tag_delete_form.tag_id.data
                try:
                    tag_id = int(str(tag_id_raw).strip())
                except (TypeError, ValueError):
                    tag_id = None
                if tag_id is None:
                    flash("Tag selecionada é inválida.", "danger")
                else:
                    tag_to_delete = Tag.query.get(tag_id)
                    if not tag_to_delete:
                        flash("Tag não encontrada.", "warning")
                    else:
                        try:
                            if tag_to_delete.nome.startswith(PERSONAL_TAG_PREFIX):
                                personal_tasks = Task.query.filter_by(tag_id=tag_to_delete.id).all()
                                for task in personal_tasks:
                                    _delete_task_recursive(task)
                                db.session.flush()
                            db.session.delete(tag_to_delete)
                            db.session.commit()
                            # Invalidate tag cache after deleting tag
                            from app.services.cache_service import invalidate_tag_cache
                            invalidate_tag_cache()
                        except IntegrityError:
                            db.session.rollback()
                            flash(
                                "Não foi possível excluir a tag porque há tarefas vinculadas a ela. "
                                "Remova ou atualize as tarefas antes de tentar novamente.",
                                "danger",
                            )
                        except SQLAlchemyError:
                            db.session.rollback()
                            flash(
                                "Não foi possível excluir a tag selecionada.",
                                "danger",
                            )
                        else:
                            flash("Tag removida com sucesso!", "success")
                return redirect(url_for("list_users", open_tag_modal="1"))
            else:
                flash("Não foi possível excluir a tag selecionada.", "danger")

    users_query = User.query.options(joinedload(User.tags))
    if not show_inactive:
        users_query = users_query.filter_by(ativo=True)
    if selected_tag_ids:
        users_query = (
            users_query.join(User.tags)
            .filter(Tag.id.in_(selected_tag_ids))
            .distinct()
        )
    users = users_query.order_by(User.ativo.desc(), User.name).all()
    return render_template(
        "list_users.html",
        users=users,
        form=form,
        edit_form=edit_form,
        tag_create_form=tag_create_form,
        tag_edit_form=tag_edit_form,
        tag_delete_form=tag_delete_form,
        edit_tag=edit_tag,
        tag_list=tag_list,
        show_inactive=show_inactive,
        selected_tag_id=selected_tag_id,
        selected_tag_ids=selected_tag_ids,
        open_tag_modal=open_tag_modal,
        open_user_modal=open_user_modal,
        open_edit_modal=open_edit_modal,
        editing_user=editing_user,
        editing_user_id=editing_user_id,
        edit_password_error=edit_password_error,
    )

@app.route("/novo_usuario", methods=["GET"])
@admin_required
def novo_usuario():
    """Redirect to the user list with the registration modal open."""
    return redirect(url_for("list_users", open_user_modal="1"))

@app.route("/user/edit/<int:user_id>", methods=["GET"])
@admin_required
def edit_user(user_id):
    """Redirect to the user list opening the edit modal for the selected user."""
    user = User.query.get_or_404(user_id)
    if user.is_master and current_user.id != user.id:
        abort(403)
    return redirect(
        url_for(
            "list_users",
            open_edit_modal="1",
            edit_user_id=user.id,
        )
    )


# ---------------------- Task Management Routes ----------------------

def _user_can_access_task(task: Task, user: User | None) -> bool:
    """Return ``True`` when ``user`` is allowed to access ``task``."""

    if user is None:
        return False
    if getattr(user, "role", None) == "admin":
        return True
    if not getattr(task, "is_private", False):
        return True
    user_id = getattr(user, "id", None)
    if user_id is None:
        return False
    if (
        task.created_by == user_id
        or task.assigned_to == user_id
        or task.completed_by == user_id
    ):
        return True
    follow_up_entries = getattr(task, "follow_up_assignments", None) or []
    return any(entry.user_id == user_id for entry in follow_up_entries)


def _user_can_transfer_task(task: Task, user: User | None) -> bool:
    """Return ``True`` when ``user`` is allowed to transfer ``task`` to another assignee."""

    if user is None:
        return False
    if getattr(user, "role", None) == "admin":
        return True
    user_id = getattr(user, "id", None)
    if user_id is None:
        return False
    if task.created_by == user_id or task.assigned_to == user_id:
        return True
    follow_up_entries = getattr(task, "follow_up_assignments", None) or []
    return any(entry.user_id == user_id for entry in follow_up_entries)


def _task_visible_for_user(task: Task, user: User) -> bool:
    """Return True when ``task`` should be shown to ``user``."""
    return _user_can_access_task(task, user)


def _filter_tasks_for_user(tasks: list[Task], user: User) -> list[Task]:
    """Return a filtered list of tasks (and subtasks) visible to ``user``."""
    visible: list[Task] = []
    for task in tasks:
        if not _task_visible_for_user(task, user):
            continue
        children = list(getattr(task, "children", []) or [])
        filtered_children = _filter_tasks_for_user(children, user) if children else []
        task.filtered_children = filtered_children
        visible.append(task)
    return visible


def _is_only_me_selected(values: list[str]) -> bool:
    """Return True when 'Somente para mim' checkbox is effectively selected."""

    truthy = {"1", "true", "on", "yes", "y"}
    for value in values:
        if isinstance(value, str) and value.lower() in truthy:
            return True
    return False


def _get_task_notification_recipients(task: Task, exclude_user_id: int | None = None) -> set[int]:
    """
    Retorna os IDs dos usuários que devem receber notificações sobre uma tarefa.
    Regra: Se tem responsável, notifica só ele; se não, notifica o setor.

    Args:
        task: A tarefa em questão
        exclude_user_id: ID do usuário a ser excluído (geralmente quem fez a ação)

    Returns:
        Set de IDs de usuários a serem notificados
    """
    recipients: set[int] = set()

    # Se tem responsável e não é privada, notifica apenas o responsável
    if task.assigned_to:
        if exclude_user_id is None or task.assigned_to != exclude_user_id:
            recipients.add(task.assigned_to)
    # Se não tem responsável e não é privada, notifica o setor
    elif not task.is_private and task.tag and getattr(task.tag, "users", None):
        for member in getattr(task.tag, "users", []) or []:
            if not getattr(member, "ativo", False):
                continue
            if not member.id:
                continue
            if exclude_user_id and member.id == exclude_user_id:
                continue
            recipients.add(member.id)

    return recipients


def _iter_tasks_with_children(tasks: Iterable[Task]) -> Iterable[Task]:
    """Yield tasks recursively, following ``filtered_children`` when available."""

    for task in tasks:
        yield task
        children = getattr(task, "filtered_children", None)
        if children is None:
            children = getattr(task, "children", None)
        if children:
            yield from _iter_tasks_with_children(children)


def _coerce_task_status(status_value) -> TaskStatus:
    """Return a valid TaskStatus, defaulting to PENDING on errors."""

    if isinstance(status_value, TaskStatus):
        return status_value
    try:
        return TaskStatus(status_value)
    except Exception:
        return TaskStatus.PENDING


def _group_root_tasks_by_status(
    tasks: Iterable[Task], visible_statuses: Iterable[TaskStatus] | None = None
) -> dict[TaskStatus, list[Task]]:
    """Return a mapping of status -> root tasks, ensuring children stay nested."""

    if visible_statuses:
        ordered_statuses = list(visible_statuses)
    else:
        ordered_statuses = list(TaskStatus)

    buckets: dict[TaskStatus, list[Task]] = {status: [] for status in ordered_statuses}
    tracked_statuses = set(buckets.keys())
    allow_extra_status = not visible_statuses

    for task in tasks:
        children = getattr(task, "children", None) or []
        task.filtered_children = sorted(
            children, key=lambda child: child.created_at or datetime.min
        )
        if getattr(task, "parent_id", None):
            continue

        status = _coerce_task_status(getattr(task, "status", None))
        if status not in tracked_statuses:
            if not allow_extra_status:
                continue
            buckets[status] = []
            tracked_statuses.add(status)

        buckets[status].append(task)

    # Guarantee all requested statuses exist even if empty
    for status in ordered_statuses:
        buckets.setdefault(status, [])

    return buckets



@app.route("/tasks/overview")
@login_required
@meeting_only_access_check
def tasks_overview():
    """Kanban view of all tasks grouped by status."""

    tag_param = (request.args.get("tag_id") or "").strip()
    priority_param = (request.args.get("priority") or "").strip().lower()
    keyword = (request.args.get("q") or "").strip()
    user_params = request.args.getlist("user_id")
    # compat: aceita antigo user_id_2
    user_param_second = (request.args.get("user_id_2") or "").strip()
    if user_param_second:
        user_params.append(user_param_second)
    if not user_params:
        fallback_single = (request.args.get("user_id") or "").strip()
        if fallback_single:
            user_params = [fallback_single]
    due_from_raw = (request.args.get("due_from") or "").strip()
    due_to_raw = (request.args.get("due_to") or "").strip()
    selected_priority = None
    selected_user_ids: list[int] = []
    selected_user_id = None
    selected_user_id_2 = None
    selected_tag_id = None

    def _parse_date_param(raw_value):
        if not raw_value:
            return None
        try:
            return datetime.strptime(raw_value, "%Y-%m-%d").date()
        except ValueError:
            return None

    query = (
        Task.query.join(Tag)
        .filter(Task.parent_id.is_(None))
        .filter(Task.is_private.is_(False))
        .filter(~Tag.nome.in_(EXCLUDED_TASK_TAGS))
        .filter(_user_task_access_filter(current_user))
    )

    if current_user.role != "admin":
        accessible_ids = _get_accessible_tag_ids(current_user)
        allowed_filters = []
        if accessible_ids:
            allowed_filters.append(Task.tag_id.in_(accessible_ids))
        allowed_filters.append(Task.created_by == current_user.id)
        query = query.filter(sa.or_(*allowed_filters))
    else:
        accessible_ids = []

    def _parse_user_param(raw_value):
        if not raw_value:
            return None
        try:
            return int(raw_value)
        except ValueError:
            return None

    selected_user_ids = []
    for raw_user in user_params[:2]:  # limite de 2
        parsed = _parse_user_param(raw_user)
        if parsed is not None and parsed not in selected_user_ids:
            selected_user_ids.append(parsed)
    if selected_user_ids:
        selected_user_id = selected_user_ids[0]
        if len(selected_user_ids) > 1:
            selected_user_id_2 = selected_user_ids[1]

    if selected_user_ids:
        from app.models.tables import TaskFollower

        def _participant_filter(target_user_id: int):
            follower_subquery = (
                db.session.query(TaskFollower.task_id)
                .filter(TaskFollower.user_id == target_user_id)
                .subquery()
            )
            return sa.or_(
                Task.assigned_to == target_user_id,
                Task.created_by == target_user_id,
                Task.id.in_(follower_subquery),
            )

        for uid in selected_user_ids:
            query = query.filter(_participant_filter(uid))

    if priority_param:
        try:
            selected_priority = TaskPriority(priority_param)
            query = query.filter(Task.priority == selected_priority)
        except ValueError:
            selected_priority = None

    if keyword:
        pattern = f"%{keyword}%"
        query = query.filter(
            sa.or_(Task.title.ilike(pattern), Task.description.ilike(pattern))
        )

    if tag_param:
        try:
            candidate_tag_id = int(tag_param)
        except ValueError:
            candidate_tag_id = None
        if candidate_tag_id:
            if current_user.role == "admin":
                selected_tag_id = candidate_tag_id
            elif candidate_tag_id in accessible_ids:
                selected_tag_id = candidate_tag_id
        if selected_tag_id:
            query = query.filter(Task.tag_id == selected_tag_id)

    due_from = _parse_date_param(due_from_raw)
    due_to = _parse_date_param(due_to_raw)
    if due_from:
        query = query.filter(Task.due_date.isnot(None)).filter(Task.due_date >= due_from)
    if due_to:
        query = query.filter(Task.due_date.isnot(None)).filter(Task.due_date <= due_to)

    active_users = (
        User.query.filter(User.ativo.is_(True))
        .order_by(User.name.asc())
        .all()
    )
    if current_user.role == "admin":
        available_tags = (
            Tag.query.filter(~Tag.nome.in_(EXCLUDED_TASK_TAGS))
            .order_by(Tag.nome.asc())
            .all()
        )
    else:
        available_tags = (
            Tag.query.filter(Tag.id.in_(accessible_ids))
            .order_by(Tag.nome.asc())
            .all()
            if accessible_ids
            else []
        )

    tasks = (
        query.options(
            joinedload(Task.tag),
            joinedload(Task.assignee),
            joinedload(Task.finisher),
            joinedload(Task.creator),
            joinedload(Task.children).joinedload(Task.assignee),
            joinedload(Task.children).joinedload(Task.finisher),
            joinedload(Task.children).joinedload(Task.tag),
            joinedload(Task.children).joinedload(Task.creator),
        )
        .order_by(Task.due_date)
        .limit(200)
        .all()
    )
    tasks = _filter_tasks_for_user(tasks, current_user)
    summaries = _load_task_response_summaries(
        (task.id for task in _iter_tasks_with_children(tasks)),
        current_user.id,
    )
    default_summary = {"unread_count": 0, "total_responses": 0, "last_response": None}
    for task in _iter_tasks_with_children(tasks):
        task.conversation_summary = summaries.get(task.id) or default_summary.copy()

    tasks_by_status = _group_root_tasks_by_status(tasks)

    done_sorted = sorted(
        tasks_by_status[TaskStatus.DONE],
        key=lambda x: x.completed_at or datetime.min,
        reverse=True,
    )
    history_count = max(0, len(done_sorted) - 5)
    tasks_by_status[TaskStatus.DONE] = done_sorted[:5]

    return render_template(
        "tasks_overview.html",
        tasks_by_status=tasks_by_status,
        TaskStatus=TaskStatus,
        history_count=history_count,
        allow_delete=current_user.role == "admin",
        priorities=list(TaskPriority),
        selected_priority=selected_priority.value if selected_priority else "",
        keyword=keyword,
        selected_user_id=selected_user_id,
        selected_user_id_2=selected_user_id_2,
        available_tags=available_tags,
        selected_tag_id=selected_tag_id,
        due_from=due_from.strftime("%Y-%m-%d") if due_from else "",
        due_to=due_to.strftime("%Y-%m-%d") if due_to else "",
        users=active_users,
    )

@app.route("/tasks/overview/mine")
@login_required
def tasks_overview_mine():
    """Kanban view of tasks where the current user participates."""

    visible_statuses = [TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.DONE]

    keyword = (request.args.get("q") or "").strip()
    priority_param = (request.args.get("priority") or "").strip().lower()
    tag_param = (request.args.get("tag_id") or "").strip()
    due_from_raw = (request.args.get("due_from") or "").strip()
    due_to_raw = (request.args.get("due_to") or "").strip()
    user_params = request.args.getlist("user_id")
    user_param_second = (request.args.get("user_id_2") or "").strip()
    if user_param_second:
        user_params.append(user_param_second)
    if not user_params:
        fallback_single = (request.args.get("user_id") or "").strip()
        if fallback_single:
            user_params = [fallback_single]
    selected_user_ids: list[int] = []
    selected_user_id = None
    selected_user_id_2 = None
    selected_tag_id = None

    owned_sector_tags = [
        tag
        for tag in (current_user.tags or [])
        if not tag.nome.startswith(PERSONAL_TAG_PREFIX)
    ]
    owned_sector_tag_ids = [tag.id for tag in owned_sector_tags]
    available_tags = (
        Tag.query.filter(Tag.id.in_(owned_sector_tag_ids))
        .order_by(Tag.nome.asc())
        .all()
        if owned_sector_tag_ids
        else []
    )

    def _parse_date_param(raw_value: str | None):
        if not raw_value:
            return None
        try:
            return datetime.strptime(raw_value, "%Y-%m-%d").date()
        except ValueError:
            return None

    from app.models.tables import TaskFollower

    follower_subquery = (
        db.session.query(TaskFollower.task_id)
        .filter(TaskFollower.user_id == current_user.id)
        .subquery()
    )

    participation_filters = [
        Task.created_by == current_user.id,
        Task.assigned_to == current_user.id,
        Task.id.in_(follower_subquery),
    ]
    if owned_sector_tag_ids:
        participation_filters.append(Task.tag_id.in_(owned_sector_tag_ids))

    query = (
        Task.query.join(Tag)
        .filter(Task.parent_id.is_(None))
        .filter(Task.is_private.is_(False))
        .filter(~Tag.nome.in_(EXCLUDED_TASK_TAGS))
        .filter(sa.or_(*participation_filters))
    )

    def _parse_user_param(raw_value: str | None):
        if not raw_value:
            return None
        try:
            return int(raw_value)
        except ValueError:
            return None

    for raw_user in user_params[:2]:
        parsed = _parse_user_param(raw_user)
        if parsed is not None and parsed not in selected_user_ids:
            selected_user_ids.append(parsed)
    if selected_user_ids:
        selected_user_id = selected_user_ids[0]
        if len(selected_user_ids) > 1:
            selected_user_id_2 = selected_user_ids[1]

    if selected_user_ids:
        def _participant_filter(target_user_id: int):
            follower_filter_subquery = (
                db.session.query(TaskFollower.task_id)
                .filter(TaskFollower.user_id == target_user_id)
                .subquery()
            )
            return sa.or_(
                Task.assigned_to == target_user_id,
                Task.created_by == target_user_id,
                Task.id.in_(follower_filter_subquery),
            )

        for uid in selected_user_ids:
            query = query.filter(_participant_filter(uid))

    selected_priority = None
    if priority_param:
        try:
            selected_priority = TaskPriority(priority_param)
            query = query.filter(Task.priority == selected_priority)
        except ValueError:
            selected_priority = None

    if keyword:
        pattern = f"%{keyword}%"
        query = query.filter(
            sa.or_(Task.title.ilike(pattern), Task.description.ilike(pattern))
        )

    if tag_param:
        try:
            candidate_tag_id = int(tag_param)
        except ValueError:
            candidate_tag_id = None
        if candidate_tag_id and candidate_tag_id in owned_sector_tag_ids:
            selected_tag_id = candidate_tag_id
            query = query.filter(Task.tag_id == selected_tag_id)

    due_from = _parse_date_param(due_from_raw)
    due_to = _parse_date_param(due_to_raw)
    if due_from:
        query = query.filter(Task.due_date.isnot(None)).filter(Task.due_date >= due_from)
    if due_to:
        query = query.filter(Task.due_date.isnot(None)).filter(Task.due_date <= due_to)

    # selected_tag_id already set above if applicable

    tasks = (
        query.options(
            joinedload(Task.tag),
            joinedload(Task.assignee),
            joinedload(Task.finisher),
            joinedload(Task.creator),
            joinedload(Task.children).joinedload(Task.assignee),
            joinedload(Task.children).joinedload(Task.finisher),
            joinedload(Task.children).joinedload(Task.tag),
            joinedload(Task.children).joinedload(Task.creator),
        )
        .order_by(Task.due_date)
        .limit(200)
        .all()
    )
    active_users = (
        User.query.filter(User.ativo.is_(True))
        .order_by(User.name.asc())
        .all()
    )
    available_tags = (
        Tag.query.filter(Tag.id.in_(owned_sector_tag_ids))
        .order_by(Tag.nome.asc())
        .all()
        if owned_sector_tag_ids
        else []
    )
    tasks = _filter_tasks_for_user(tasks, current_user)
    summaries = _load_task_response_summaries(
        (task.id for task in _iter_tasks_with_children(tasks)),
        current_user.id,
    )
    default_summary = {"unread_count": 0, "total_responses": 0, "last_response": None}
    for task in _iter_tasks_with_children(tasks):
        task.conversation_summary = summaries.get(task.id) or default_summary.copy()
    tasks_by_status = _group_root_tasks_by_status(tasks, visible_statuses)
    # Sort DONE tasks by completion date and show only last 5
    done_sorted = sorted(
        tasks_by_status[TaskStatus.DONE],
        key=lambda x: x.completed_at or datetime.min,
        reverse=True,
    )
    history_count = max(0, len(done_sorted) - 5)
    tasks_by_status[TaskStatus.DONE] = done_sorted[:5]

    history_url = url_for("tasks_history")

    return render_template(
        "tasks_overview_mine.html",
        keyword=keyword,
        selected_priority=selected_priority.value if selected_priority else "",
        available_tags=available_tags,
        selected_tag_id=selected_tag_id,
        due_from=due_from.strftime("%Y-%m-%d") if due_from else "",
        due_to=due_to.strftime("%Y-%m-%d") if due_to else "",
        selected_user_id=selected_user_id,
        selected_user_id_2=selected_user_id_2,
        users=active_users,
        tasks_by_status=tasks_by_status,
        TaskStatus=TaskStatus,
        visible_statuses=visible_statuses,
        history_count=history_count,
        allow_delete=current_user.role == "admin",
        history_url=history_url,
    )


@app.route("/tasks/overview/personal")
@login_required
def tasks_overview_personal():
    """Display only private tasks that belong to the current user."""

    visible_statuses = [TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.DONE]
    query = (
        Task.query.filter(Task.parent_id.is_(None))
        .filter(Task.is_private.is_(True))
        # Tasks privadas são visíveis apenas para quem criou
        .filter(Task.created_by == current_user.id)
    )
    tasks = (
        query.options(
            joinedload(Task.tag),
            joinedload(Task.assignee),
            joinedload(Task.finisher),
            joinedload(Task.creator),
            joinedload(Task.children).joinedload(Task.assignee),
            joinedload(Task.children).joinedload(Task.finisher),
            joinedload(Task.children).joinedload(Task.tag),
            joinedload(Task.children).joinedload(Task.creator),
        )
        .order_by(Task.due_date)
        .limit(200)
        .all()
    )
    tasks = _filter_tasks_for_user(tasks, current_user)
    summaries = _load_task_response_summaries(
        (task.id for task in _iter_tasks_with_children(tasks)),
        current_user.id,
    )
    default_summary = {"unread_count": 0, "total_responses": 0, "last_response": None}
    for task in _iter_tasks_with_children(tasks):
        task.conversation_summary = summaries.get(task.id) or default_summary.copy()
    tasks_by_status = _group_root_tasks_by_status(tasks, visible_statuses)
    done_sorted = sorted(
        tasks_by_status[TaskStatus.DONE],
        key=lambda x: x.completed_at or datetime.min,
        reverse=True,
    )
    history_count = max(0, len(done_sorted) - 5)
    tasks_by_status[TaskStatus.DONE] = done_sorted[:5]

    return render_template(
        "tasks_overview_personal.html",
        tasks_by_status=tasks_by_status,
        TaskStatus=TaskStatus,
        visible_statuses=visible_statuses,
        history_count=history_count,
        allow_delete=current_user.role == "admin",
        history_url=url_for("tasks_history", only_me=1),
    )


def _sortable_text(value: str | None) -> str:
    """Return a lowercased, accent-free representation suitable for sorting."""
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return without_accents.casefold()


def _sort_choice_pairs(
    pairs: list[tuple[int, str]], keep_first: bool = False
) -> list[tuple[int, str]]:
    """Sort a list of ``(value, label)`` pairs alphabetically by label.

    When ``keep_first`` is ``True`` the first element (commonly a sentinel like
    ``0`` → "Sem responsável") is preserved at the front and only the remaining
    items are sorted.
    """
    if not pairs:
        return []
    if keep_first:
        head, *tail = pairs
        return [head, *sorted(tail, key=lambda item: _sortable_text(item[1]))]
    return sorted(pairs, key=lambda item: _sortable_text(item[1]))


def _build_task_user_choices(tag_obj: Tag | None) -> list[tuple[int, str]]:
    """Build select choices for task assignee field based on tag membership."""
    entries: dict[int, str] = {}
    if tag_obj:
        users = [
            u
            for u in (getattr(tag_obj, "users", []) or [])
            if getattr(u, "ativo", False)
        ]
        for user in users:
            label = (user.name or user.username or "").strip()
            if label:
                entries[user.id] = label
            else:
                entries[user.id] = user.username or ""
    display_name = (current_user.name or current_user.username or "").strip()
    if current_user.id and current_user.id not in entries:
        entries[current_user.id] = display_name

    sorted_entries = sorted(entries.items(), key=lambda item: _sortable_text(item[1]))
    return [(0, "Sem responsável"), *sorted_entries]


def _build_follow_up_user_choices() -> list[tuple[int, str]]:
    """Return active portal users for the acompanhamento multi-select."""
    entries: dict[int, str] = {}
    users = (
        User.query.filter_by(ativo=True)
        .order_by(User.name.asc(), User.username.asc())
        .all()
    )
    for user in users:
        label = (user.name or user.username or "").strip()
        if not label:
            label = user.username or f"Usuário {user.id}"
        entries[user.id] = label
    return _sort_choice_pairs(list(entries.items()))


def _extract_follow_up_user_ids(form: TaskForm) -> list[int]:
    """Return sanitized user IDs selected for acompanhamento."""

    try:
        selected_ids: list[int] = list(form.follow_up_users.data or [])
    except (TypeError, ValueError):
        selected_ids = []
    normalized: list[int] = []
    seen: set[int] = set()
    for user_id in selected_ids:
        if not isinstance(user_id, int):
            try:
                user_id = int(user_id)
            except (TypeError, ValueError):
                continue
        if user_id <= 0 or user_id in seen:
            continue
        seen.add(user_id)
        normalized.append(user_id)
    # Retorna a lista normalizada se houver usuários selecionados
    return normalized


def _sync_task_followers(task: Task, user_ids: list[int]) -> None:
    """Persist acompanhamento participants for ``task`` in bulk."""

    desired = [uid for uid in dict.fromkeys(user_ids) if isinstance(uid, int) and uid > 0]
    desired_set = set(desired)

    existing_rows = TaskFollower.query.filter_by(task_id=task.id).all()
    existing = {row.user_id: row for row in existing_rows}

    for user_id, follower in list(existing.items()):
        if user_id not in desired_set:
            db.session.delete(follower)

    for user_id in desired:
        if user_id in existing:
            continue
        db.session.add(TaskFollower(task_id=task.id, user_id=user_id))

    # Garantir que as alterações fiquem disponíveis para o restante do fluxo
    db.session.flush()


def _task_follow_up_user_ids(task: Task) -> set[int]:
    """Return a set with all acompanhamento participant IDs for ``task``."""

    entries = getattr(task, "follow_up_assignments", None) or []
    return {
        entry.user_id
        for entry in entries
        if getattr(entry, "user_id", None)
    }


def _is_task_follow_up(task: Task, user: User | int | None) -> bool:
    """Return True when ``user`` (or ``user_id``) is marked as acompanhamento."""

    if user is None:
        return False
    user_id = user if isinstance(user, int) else getattr(user, "id", None)
    if not user_id:
        return False
    return user_id in _task_follow_up_user_ids(task)


def _user_has_task_privileges(task: Task, user: User | None) -> bool:
    """Return True when user should have the same powers as the responsible."""

    if user is None:
        return False
    if getattr(user, "role", None) == "admin":
        return True
    user_id = getattr(user, "id", None)
    if not user_id:
        return False
    if (
        task.created_by == user_id
        or task.assigned_to == user_id
        or task.completed_by == user_id
    ):
        return True
    return _is_task_follow_up(task, user_id)


def _user_task_access_filter(user: User):
    """Return SQLAlchemy filter for tasks accessible by user (including as follower)."""
    from app.controllers.task_filters import get_follower_subquery

    # Usuário pode acessar se:
    # 1. Task não é privada OU
    # 2. É o criador OU
    # 3. É o responsável OU
    # 4. É acompanhante
    return sa.or_(
        Task.is_private.is_(False),
        Task.created_by == user.id,
        Task.assigned_to == user.id,
        Task.id.in_(get_follower_subquery(user.id))
    )


@app.route("/tasks/new", methods=["GET", "POST"])
@login_required
def tasks_new():
    """Form to create a new task or subtask."""
    parent_id = request.args.get("parent_id", type=int)
    return_url = request.args.get("return_url")  # Não usar request.referrer - queremos ir para a Central de Tarefas
    form = TaskForm()

    if request.method == "POST" and not parent_id:
        posted_parent_id = form.parent_id.data
        if not posted_parent_id:
            try:
                posted_parent_id = int(request.form.get("parent_id", "") or 0)
            except (TypeError, ValueError):
                posted_parent_id = None
        parent_id = posted_parent_id or None

    parent_task = Task.query.get(parent_id) if parent_id else None
    if parent_task and parent_task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    if parent_task and parent_task.is_private and not _user_can_access_task(parent_task, current_user):
        abort(403)
    requested_tag_id = request.args.get("tag_id", type=int)
    choices: list[tuple[int, str]] = []
    preset_only_me_param = (request.args.get("only_me", "") or "").lower()
    if request.method == "GET" and preset_only_me_param in {"1", "true", "on", "yes"}:
        form.only_me.data = True
    tag = parent_task.tag if parent_task else None
    if parent_task:
        form.parent_id.data = parent_task.id
        form.tag_id.choices = [(parent_task.tag_id, parent_task.tag.nome)]
        form.tag_id.data = parent_task.tag_id
        # Não desabilitar o campo aqui - será tratado no template
        form.assigned_to.choices = _build_task_user_choices(parent_task.tag)
    else:
        tags_query = (
            Tag.query.filter(~Tag.nome.in_(EXCLUDED_TASK_TAGS))
            .filter(~Tag.nome.like(f"{PERSONAL_TAG_PREFIX}%"))
            .order_by(Tag.nome)
        )
        choices = [(t.id, t.nome) for t in tags_query.all()]
        choices = _sort_choice_pairs(choices)
        form.tag_id.choices = choices
        selected_tag_id = form.tag_id.data
        if not selected_tag_id and requested_tag_id:
            selected_tag_id = requested_tag_id
            if request.method == "GET":
                form.tag_id.data = selected_tag_id
        if not selected_tag_id and choices:
            selected_tag_id = choices[0][0]
            if request.method == "GET":
                form.tag_id.data = selected_tag_id
        if selected_tag_id:
            tag = Tag.query.get(selected_tag_id)
        form.assigned_to.choices = _build_task_user_choices(tag)

    form.follow_up_users.choices = _build_follow_up_user_choices()

    # Garantir que o valor do only_me seja preservado no POST
    if request.method == "POST":
        # Forçar o valor do checkbox com base em todos os valores enviados
        form.only_me.data = _is_only_me_selected(request.form.getlist("only_me"))
        current_app.logger.info(
            f"Task create POST - only_me raw value: {request.form.get('only_me')}, "
            f"form.only_me.data: {form.only_me.data}, " 
            f"tag_id: {form.tag_id.data}, assigned_to: {form.assigned_to.data}"
        )

    if request.method == "POST" and form.only_me.data:
        form.assigned_to.data = current_user.id
        current_app.logger.info("Task create - only_me checked, forcing self-assignment")

    if form.validate_on_submit():
        follow_up_user_ids = _extract_follow_up_user_ids(form)
        current_app.logger.info("Formulário validado com sucesso. Criando task...")
        tag_id = parent_task.tag_id if parent_task else form.tag_id.data
        if not parent_task and (tag is None or tag.id != tag_id):
            tag = Tag.query.get(tag_id)
        if tag is None:
            abort(400)
        assignee_id = form.assigned_to.data or None
        is_private = bool(form.only_me.data)
        current_app.logger.info(
            f"Task create - is_private: {is_private}, tag_id: {tag_id}, "
            f"assignee_id: {assignee_id}, tag_name: {tag.nome if tag else 'None'}"
        )
        if is_private:
            assignee_id = current_user.id

        try:
            creation_notification_records: list[tuple[int, TaskNotification]] = []
            task = Task(
                is_private=is_private,
                title=form.title.data,
                description=form.description.data,
                tag_id=tag_id,
                priority=TaskPriority(form.priority.data),
                due_date=form.due_date.data,
                created_by=current_user.id,
                parent_id=parent_id,
                assigned_to=assignee_id,
            )
            db.session.add(task)
            db.session.flush()

            # Processar uploads de anexos
            uploaded_files = [
                storage
                for storage in (form.attachments.data or [])
                if storage and storage.filename
            ]

            for uploaded_file in uploaded_files:
                saved = _save_task_file(uploaded_file)
                db.session.add(
                    TaskAttachment(
                        task=task,
                        file_path=saved["path"],
                        original_name=saved["name"],
                        mime_type=saved["mime_type"],
                    )
                )

            _sync_task_followers(task, follow_up_user_ids)

            creator_name = current_user.name or current_user.username
            creation_now = utc3_now()
            notification_payloads: list[tuple[int, str]] = []

            if task.assigned_to:
                # Evitar notificação quando o criador é também o responsável
                if task.assigned_to == current_user.id:
                    task._skip_assignment_notification = True
            elif not task.is_private and tag and getattr(tag, "users", None):
                sector_label = (
                    "Para Mim" if tag.nome.startswith(PERSONAL_TAG_PREFIX) else tag.nome
                )
                sector_message = f'Tarefa "{task.title}" atribuída no setor {sector_label}.'
                for member in getattr(tag, "users", []) or []:
                    if not getattr(member, "ativo", False):
                        continue
                    if not member.id or member.id == current_user.id:
                        continue
                    notification_payloads.append((member.id, sector_message))

            notified_users: set[int] = set()
            for user_id, message in notification_payloads:
                if user_id in notified_users:
                    continue
                notified_users.add(user_id)
                notification = TaskNotification(
                    user_id=user_id,
                    task_id=task.id,
                    type=NotificationType.TASK.value,
                    message=message[:255] if message else None,
                    created_at=creation_now,
                )
                db.session.add(notification)
                creation_notification_records.append((user_id, notification))

            # Se for uma subtarefa, precisamos atualizar o parent_task também
            if parent_task:
                # Certifique-se de que o parent tem a lista de children atualizada
                parent_task.children.append(task)
                # Force a atualização do parent
                parent_task.updated_at = utc3_now()
                # Atualize explicitamente o status has_children
                parent_task.has_children = True
            
            db.session.commit()

            # Verificar e recarregar tarefa do banco para garantir persistencia
            db.session.refresh(task)
            if parent_task:
                db.session.refresh(parent_task)

            # Log detalhado do salvamento
            current_app.logger.info(
                f"Task {task.id} salva com sucesso no banco de dados. "
                f"is_private={task.is_private}, tag_id={task.tag_id}, "
                f"tag_nome={task.tag.nome if task.tag else 'None'}, "
                f"created_by={task.created_by}, assigned_to={task.assigned_to}"
            )

            # Verificacao de integridade: garantir que is_private foi salvo corretamente
            if task.is_private != is_private:
                error_msg = (
                    f"ERRO CRITICO: is_private nao foi salvo corretamente! "
                    f"Esperado: {is_private}, Obtido do banco: {task.is_private}"
                )
                current_app.logger.error(error_msg)
                flash("Erro ao salvar configuracao 'Somente para mim'. Por favor, tente novamente.", "danger")
                db.session.rollback()
                return redirect(url_for("tasks_new"))

            # Broadcast task creation
            from app.services.realtime import broadcast_task_created, get_broadcaster
            task_data = _serialize_task(task)
            if not task.is_private:
                broadcast_task_created(task_data, exclude_user=current_user.id)

            if creation_notification_records:
                broadcaster = get_broadcaster()
                for user_id, notification in creation_notification_records:
                    broadcaster.broadcast(
                        event_type="notification:created",
                        data={
                            "id": notification.id,
                            "task_id": task.id,
                            "type": notification.type,
                            "message": notification.message,
                            "created_at": notification.created_at.isoformat()
                            if notification.created_at
                            else None,
                        },
                        user_id=user_id,
                        scope="notifications",
                    )

            flash("Tarefa criada com sucesso!", "success")
            current_app.logger.info(
                "Task criada com sucesso (ID: %s). return_url: %s, current_user.role: %s",
                task.id, return_url, current_user.role
            )

            # Redirecionar de volta para a pagina original quando apropriado
            if return_url and not task.is_private and return_url != request.url:
                current_app.logger.info("Redirecionando para return_url: %s com highlight", return_url)
                # Adicionar parâmetro highlight_task para destacar a tarefa criada
                separator = '&' if '?' in return_url else '?'
                return redirect(f"{return_url}{separator}highlight_task={task.id}")

            destination = "tasks_overview" if current_user.role == "admin" else "tasks_overview_mine"
            current_app.logger.info("Redirecionando para %s com highlight", destination)
            return redirect(url_for(destination, highlight_task=task.id))
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception("Erro ao criar tarefa", exc_info=exc)
            flash(f"Erro ao criar tarefa: {str(exc)}", "danger")
    else:
        # Debug: mostrar erros de validação quando o formulário não validar
        if request.method == "POST":
            current_app.logger.warning(
                "Formulário de tarefa não validou. Erros: %s", form.errors
            )
            for field, errors in form.errors.items():
                for error in errors:
                    flash(f"Erro no campo '{field}': {error}", "danger")

    # Determinar URL de cancelamento - priorizar return_url se fornecido
    if return_url:
        cancel_url = return_url
    elif parent_task:
        cancel_url = url_for("tasks_sector", tag_id=parent_task.tag_id)
    elif tag:
        cancel_url = url_for("tasks_sector", tag_id=tag.id)
    else:
        # Fallback: usar a página de origem ou home
        cancel_url = request.referrer or url_for("home")

    return render_template(
        "tasks_new.html",
        form=form,
        parent_task=parent_task,
        cancel_url=cancel_url,
        is_editing=False,
        editing_task=None,
        return_url=return_url,
    )


@app.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
@login_required
def tasks_edit(task_id: int):
    """Edit an existing task."""
    return_url = request.args.get("return_url")
    if request.method == "POST" and not return_url:
        return_url = request.form.get("return_url") or None

    task = Task.query.get_or_404(task_id)

    if task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER and current_user.role != "admin":
        abort(404)

    is_admin = current_user.role == "admin"
    is_creator = task.created_by == current_user.id
    is_assignee = task.assigned_to == current_user.id if task.assigned_to else False
    follow_up_ids = list(_task_follow_up_user_ids(task))
    is_follow_up = current_user.id in follow_up_ids

    if task.is_private:
        if not _user_can_access_task(task, current_user):
            abort(403)
    else:
        if not (
            _can_user_access_tag(task.tag, current_user)
            or is_admin
            or is_creator
            or is_assignee
            or is_follow_up
        ):
            abort(403)

    if task.status not in {TaskStatus.PENDING, TaskStatus.IN_PROGRESS} and not is_admin:
        flash("Apenas tarefas pendentes ou em andamento podem ser editadas.", "warning")
        if return_url:
            return redirect(return_url)
        return redirect(url_for("tasks_view", task_id=task.id))

    # Criar form (Flask binda automaticamente ao request.form no POST)
    form = TaskForm()
    parent_task = task.parent

    if parent_task:
        form.parent_id.data = parent_task.id
        form.tag_id.choices = [(parent_task.tag_id, parent_task.tag.nome)]
        form.tag_id.data = parent_task.tag_id
        tag = parent_task.tag
    else:
        tags_query = (
            Tag.query.filter(~Tag.nome.in_(EXCLUDED_TASK_TAGS))
            .filter(~Tag.nome.like(f"{PERSONAL_TAG_PREFIX}%"))
            .order_by(Tag.nome)
        )
        tag_choices = [(t.id, t.nome) for t in tags_query.all()]
        tag_choices = _sort_choice_pairs(tag_choices)
        form.tag_id.choices = tag_choices
        if task.is_private and all(choice[0] != task.tag_id for choice in form.tag_id.choices):
            updated_choices = list(form.tag_id.choices) + [(task.tag_id, "Para Mim")]
            form.tag_id.choices = _sort_choice_pairs(updated_choices)

        selected_tag_id = form.tag_id.data if request.method == "POST" else task.tag_id
        if selected_tag_id is None:
            selected_tag_id = task.tag_id
        if request.method != "POST":
            form.tag_id.data = selected_tag_id
        tag = Tag.query.get(selected_tag_id) if selected_tag_id else None

    assignee_choices = _build_task_user_choices(tag)
    if task.assigned_to and all(choice[0] != task.assigned_to for choice in assignee_choices):
        assignee = User.query.get(task.assigned_to)
        if assignee:
            assignee_label = (assignee.name or assignee.username or "").strip()
            assignee_choices.append((assignee.id, assignee_label))
    form.assigned_to.choices = _sort_choice_pairs(assignee_choices, keep_first=True)
    form.follow_up_users.choices = _build_follow_up_user_choices()

    # Popular campos no GET com dados da task existente
    if request.method == "GET":
        form.task_id.data = task.id
        form.title.data = task.title
        form.description.data = task.description
        form.priority.data = task.priority.value if task.priority else "medium"
        form.due_date.data = task.due_date
        form.only_me.data = task.is_private
        form.assigned_to.data = task.assigned_to or (current_user.id if task.is_private else 0)
        form.follow_up_users.data = follow_up_ids
        form.follow_up.data = bool(follow_up_ids)

    if request.method == "POST":
        form.only_me.data = _is_only_me_selected(request.form.getlist("only_me"))
        current_app.logger.info(
            f"Task edit POST (task {task_id}) - only_me raw value: {request.form.get('only_me')}, "
            f"form.only_me.data: {form.only_me.data}, "
            f"tag_id: {form.tag_id.data}, assigned_to: {form.assigned_to.data}"
        )
    if request.method == "POST" and form.only_me.data:
        form.assigned_to.data = current_user.id
        current_app.logger.info(
            "Task edit - only_me checked, forcing self-assignment"
        )

    if form.validate_on_submit():
        follow_up_user_ids = _extract_follow_up_user_ids(form)
        tag_id = parent_task.tag_id if parent_task else form.tag_id.data
        if not parent_task and (tag is None or tag.id != tag_id):
            tag = Tag.query.get(tag_id)
        if tag is None:
            abort(400)
        assignee_id = form.assigned_to.data or None
        is_private = bool(form.only_me.data)
        current_app.logger.info(
            f"Task edit - is_private: {is_private}, tag_id: {tag_id}, "
            f"assignee_id: {assignee_id}, tag_name: {tag.nome if tag else 'None'}"
        )
        if is_private:
            assignee_id = current_user.id

        try:
            task.title = form.title.data
            task.description = form.description.data
            task.priority = TaskPriority(form.priority.data)
            task.due_date = form.due_date.data
            task.is_private = is_private
            task.tag_id = tag_id
            task.assigned_to = assignee_id

            uploaded_files = [
                storage
                for storage in (form.attachments.data or [])
                if storage and storage.filename
            ]

            for uploaded_file in uploaded_files:
                saved = _save_task_file(uploaded_file)
                db.session.add(
                    TaskAttachment(
                        task=task,
                        file_path=saved["path"],
                        original_name=saved["name"],
                        mime_type=saved["mime_type"],
                    )
                )

            _sync_task_followers(task, follow_up_user_ids)

            # Notificar sobre a edição da tarefa
            editor_name = current_user.name or current_user.username
            edit_message = f'{editor_name} editou a tarefa "{task.title}".'
            edit_now = utc3_now()
            edit_recipients = _get_task_notification_recipients(task, exclude_user_id=current_user.id)

            edit_notification_records: list[tuple[int, TaskNotification]] = []
            for user_id in edit_recipients:
                notification = TaskNotification(
                    user_id=user_id,
                    task_id=task.id,
                    type=NotificationType.TASK.value,
                    message=edit_message[:255],
                    created_at=edit_now,
                )
                db.session.add(notification)
                edit_notification_records.append((user_id, notification))

            db.session.commit()

            # Verificar e recarregar tarefa do banco para garantir persistencia
            db.session.refresh(task)

            # Log detalhado da edicao
            current_app.logger.info(
                f"Task {task.id} editada com sucesso no banco de dados. "
                f"is_private={task.is_private}, tag_id={task.tag_id}, "
                f"tag_nome={task.tag.nome if task.tag else 'None'}, "
                f"created_by={task.created_by}, assigned_to={task.assigned_to}"
            )

            # Verificacao de integridade: garantir que is_private foi salvo corretamente
            if task.is_private != is_private:
                error_msg = (
                    f"ERRO CRITICO: is_private nao foi atualizado corretamente na edicao! "
                    f"Esperado: {is_private}, Obtido do banco: {task.is_private}"
                )
                current_app.logger.error(error_msg)
                flash("Erro ao salvar configuracao 'Somente para mim'. Por favor, tente novamente.", "danger")
                db.session.rollback()
                return redirect(url_for("tasks_edit", task_id=task.id))

            # Broadcast task update para atualizar interface em tempo real
            if not task.is_private:
                from app.services.realtime import broadcast_task_updated
                task_data = _serialize_task(task)
                broadcast_task_updated(task_data, exclude_user=current_user.id)

            # Broadcast notificações em tempo real
            if edit_notification_records:
                from app.services.realtime import get_broadcaster
                broadcaster = get_broadcaster()

                for user_id, notification in edit_notification_records:
                    broadcaster.broadcast(
                        event_type="notification:created",
                        data={
                            "id": notification.id,
                            "task_id": task.id,
                            "type": notification.type,
                            "message": notification.message,
                            "created_at": notification.created_at.isoformat(),
                        },
                        user_id=user_id,
                        scope="notifications",
                    )

            flash("Tarefa atualizada com sucesso!", "success")

            # Redirecionar de volta para a pagina original quando apropriado
            if return_url and not task.is_private and return_url != request.url:
                current_app.logger.info("Redirecionando para return_url: %s com highlight", return_url)
                # Adicionar parâmetro highlight_task para destacar a tarefa editada
                separator = '&' if '?' in return_url else '?'
                return redirect(f"{return_url}{separator}highlight_task={task.id}")

            destination = "tasks_overview" if current_user.role == "admin" else "tasks_overview_mine"
            current_app.logger.info("Redirecionando para %s com highlight", destination)
            return redirect(url_for(destination, highlight_task=task.id))
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception("Erro ao atualizar tarefa", exc_info=exc)
            flash(f"Erro ao atualizar tarefa: {str(exc)}", "danger")
    else:
        if request.method == "POST":
            current_app.logger.warning(
                "Formulário de edição de tarefa não validou. Erros: %s", form.errors
            )
            for field, errors in form.errors.items():
                for error in errors:
                    flash(f"Erro no campo '{field}': {error}", "danger")

    # Determinar URL de cancelamento - priorizar return_url se fornecido
    if return_url:
        cancel_url = return_url
    elif parent_task:
        cancel_url = url_for("tasks_sector", tag_id=parent_task.tag_id)
    elif task.is_private and current_user.role != "admin":
        cancel_url = url_for("tasks_overview_mine")
    elif not task.is_private:
        cancel_url = url_for("tasks_sector", tag_id=task.tag_id)
    else:
        # Fallback para tasks privadas de admin
        cancel_url = request.referrer or url_for("tasks_overview")

    return render_template(
        "tasks_new.html",
        form=form,
        parent_task=parent_task,
        cancel_url=cancel_url,
        is_editing=True,
        editing_task=task,
        return_url=return_url,
    )

@app.route("/tasks/users/<int:tag_id>")
@login_required
def tasks_users(tag_id):
    """Return active users for the requested task tag."""
    tag = Tag.query.get_or_404(tag_id)
    if tag.nome.startswith(PERSONAL_TAG_PREFIX):
        display_name = current_user.name or current_user.username
        users = [{"id": current_user.id, "name": display_name}]
    else:
        users = [
            {"id": u.id, "name": u.name}
            for u in tag.users
            if u.ativo
        ]
        # Sort users alphabetically by name
        users.sort(key=lambda u: _sortable_text(u["name"]))
    return jsonify(users)


@app.route("/tasks/<int:task_id>/transfer/options", methods=["GET"])
@login_required
def tasks_transfer_options(task_id: int):
    """Return available assignees for transferring a task."""

    task = Task.query.get_or_404(task_id)

    if task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER and current_user.role != "admin":
        abort(404)
    if task.is_private and not _user_can_access_task(task, current_user):
        abort(403)
    if not _user_can_transfer_task(task, current_user):
        abort(403)

    requested_tag_id = request.args.get("tag_id", type=int)
    is_admin = current_user.role == "admin"

    tag_entries: dict[int, Tag] = {}
    if is_admin:
        available_tags = (
            Tag.query.filter(~Tag.nome.in_(EXCLUDED_TASK_TAGS))
            .filter(~Tag.nome.like(f"{PERSONAL_TAG_PREFIX}%"))
            .order_by(Tag.nome)
            .all()
        )
        for tag in available_tags:
            tag_entries[tag.id] = tag
    else:
        accessible_ids = {tag_id for tag_id in _get_accessible_tag_ids(current_user) if tag_id}
        if task.tag_id:
            accessible_ids.add(task.tag_id)
        if accessible_ids:
            available_tags = (
                Tag.query.filter(Tag.id.in_(accessible_ids))
                .order_by(Tag.nome)
                .all()
            )
            for tag in available_tags:
                if tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
                    continue
                tag_entries[tag.id] = tag
    if task.tag and task.tag.id not in tag_entries:
        tag_entries[task.tag.id] = task.tag

    target_tag = task.tag
    if requested_tag_id:
        candidate_tag = Tag.query.get(requested_tag_id)
        if candidate_tag is None:
            abort(404)
        if candidate_tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER and not is_admin:
            abort(403)
        if not is_admin and not _can_user_access_tag(candidate_tag, current_user) and requested_tag_id != task.tag_id:
            abort(403)
        target_tag = candidate_tag
        tag_entries[candidate_tag.id] = candidate_tag

    tag_choices = [
        (
            tag_id,
            "Para Mim" if tag.nome.startswith(PERSONAL_TAG_PREFIX) else tag.nome,
        )
        for tag_id, tag in tag_entries.items()
        if tag is not None and (is_admin or tag.nome.lower() not in EXCLUDED_TASK_TAGS_LOWER)
    ]
    tag_choices = _sort_choice_pairs(tag_choices)

    tags_payload = [
        {"id": value, "label": label, "is_current": value == task.tag_id}
        for value, label in tag_choices
    ]

    choices = _build_task_user_choices(target_tag)
    options = [
        {"id": user_id, "label": label, "is_current": user_id == task.assigned_to}
        for user_id, label in choices
        if user_id
    ]

    payload = {
        "success": True,
        "options": options,
        "current_assignee": task.assigned_to,
        "assignee_name": task.assignee.name if task.assignee else None,
        "task_title": task.title,
        "tags": tags_payload,
        "current_tag_id": task.tag_id,
        "current_tag_name": task.tag.nome if task.tag else None,
        "selected_tag_id": target_tag.id if target_tag else None,
        "selected_tag_name": target_tag.nome if target_tag else None,
    }

    if not options:
        payload["success"] = False
        payload["message"] = "Nenhum colaborador disponível para receber esta tarefa."

    return jsonify(payload)


@app.route("/tasks/<int:task_id>/transfer", methods=["POST"])
@login_required
def tasks_transfer(task_id: int):
    """Transfer a task to another collaborator."""

    task = Task.query.get_or_404(task_id)

    if task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER and current_user.role != "admin":
        abort(404)
    if task.is_private and not _user_can_access_task(task, current_user):
        abort(403)
    if not _user_can_transfer_task(task, current_user):
        abort(403)

    data = request.get_json(silent=True) or {}
    assignee_raw = data.get("assignee_id")
    tag_raw = data.get("tag_id")
    is_admin = current_user.role == "admin"

    try:
        assignee_id = int(assignee_raw)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Selecione um colaborador válido."}), 400

    if assignee_id <= 0:
        return jsonify({"success": False, "message": "Selecione um colaborador válido."}), 400

    target_tag = task.tag
    if tag_raw is not None:
        try:
            tag_id = int(tag_raw)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "Selecione um setor válido."}), 400
        if tag_id <= 0:
            return jsonify({"success": False, "message": "Selecione um setor válido."}), 400
        candidate_tag = Tag.query.get(tag_id)
        if candidate_tag is None:
            return jsonify({"success": False, "message": "Selecione um setor válido."}), 400
        if candidate_tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER and not is_admin:
            return jsonify({"success": False, "message": "Selecione um setor válido."}), 400
        if not is_admin and not _can_user_access_tag(candidate_tag, current_user) and candidate_tag.id != task.tag_id:
            return jsonify({"success": False, "message": "Você não tem permissão para transferir para este setor."}), 403
        target_tag = candidate_tag

    valid_assignees = {
        user_id for user_id, _ in _build_task_user_choices(target_tag) if user_id
    }
    if assignee_id not in valid_assignees:
        return (
            jsonify(
                {"success": False, "message": "Colaborador não disponível para este setor."}
            ),
            400,
        )

    tag_changed = target_tag and target_tag.id != task.tag_id
    if assignee_id == task.assigned_to and not tag_changed:
        return jsonify(
            {
                "success": True,
                "task": _serialize_task(task),
                "message": "A tarefa já estava atribuída a este colaborador.",
            }
        )

    new_assignee = User.query.get(assignee_id)
    if new_assignee is None or not getattr(new_assignee, "ativo", True):
        return jsonify({"success": False, "message": "Colaborador indisponível."}), 400

    if target_tag and target_tag.id != task.tag_id:
        task.tag_id = target_tag.id
        task.tag = target_tag
    task.assigned_to = assignee_id
    task.assignee = new_assignee

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Erro ao transferir tarefa", exc_info=exc)
        return (
            jsonify(
                {
                    "success": False,
                    "message": "Não foi possível transferir a tarefa. Tente novamente.",
                }
            ),
            500,
        )

    db.session.refresh(task)

    task_data = _serialize_task(task)

    if not task.is_private:
        from app.services.realtime import broadcast_task_updated

        broadcast_task_updated(task_data, exclude_user=current_user.id)

    current_app.logger.info(
        "Task %s transferida para o usuário %s por %s (setor %s)",
        task.id,
        assignee_id,
        current_user.id,
        target_tag.id if target_tag else task.tag_id,
    )

    return jsonify({"success": True, "task": task_data, "message": "Tarefa transferida com sucesso."})

@app.route("/tasks/sector/<int:tag_id>")
@login_required
@meeting_only_access_check
def tasks_sector(tag_id):
    """Kanban board of tasks for a specific sector/tag."""
    tag = Tag.query.get_or_404(tag_id)
    if tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    user_is_admin = current_user.role == "admin"
    if not _can_user_access_tag(tag, current_user):
        abort(403)
    ti_tag = _get_ti_tag()
    ti_tag_id = ti_tag.id if ti_tag else None
    assigned_param = (request.args.get("assigned_to_me", "") or "").lower()
    assigned_to_me = assigned_param in {"1", "true", "on", "yes"}
    query = Task.query.filter(
        Task.tag_id == tag_id,
        Task.parent_id.is_(None),
        sa.or_(Task.is_private.is_(False), Task.created_by == current_user.id),
    )
    if assigned_to_me:
        query = query.filter(Task.assigned_to == current_user.id)
    tasks = (
        query.options(
            joinedload(Task.tag),
            joinedload(Task.assignee),
            joinedload(Task.finisher),
            joinedload(Task.creator),  # NOVO: Eager load creator to prevent N+1
            # Removed status_history and attachments eager loading to reduce Cartesian product
            joinedload(Task.children).joinedload(Task.assignee),
            joinedload(Task.children).joinedload(Task.finisher),
            joinedload(Task.children).joinedload(Task.tag),
            joinedload(Task.children).joinedload(Task.creator),  # NOVO: Eager load creator for children
            # Removed children's status_history and attachments for same reason
        )
        .order_by(Task.due_date)
        .limit(200)  # Added limit to prevent loading too many tasks at once
        .all()
    )
    tasks = _filter_tasks_for_user(tasks, current_user)
    tasks_by_status = _group_root_tasks_by_status(tasks)
    done_sorted = sorted(
        tasks_by_status[TaskStatus.DONE],
        key=lambda x: x.completed_at or datetime.min,
        reverse=True,
    )
    history_count = max(0, len(done_sorted) - 5)
    tasks_by_status[TaskStatus.DONE] = done_sorted[:5]
    return render_template(
        "tasks_board.html",
        tag=tag,
        tasks_by_status=tasks_by_status,
        TaskStatus=TaskStatus,
        history_count=history_count,
        assigned_to_me=assigned_to_me,
        ti_tag_id=ti_tag_id,
    )

@app.route("/tasks/history")
@app.route("/tasks/history/<int:tag_id>")
@login_required
def tasks_history(tag_id=None):
    """Display archived tasks beyond the visible limit."""
    assigned_param = (request.args.get("assigned_to_me", "") or "").lower()
    assigned_to_me = assigned_param in {"1", "true", "on", "yes"}
    assigned_by_param = (request.args.get("assigned_by_me", "") or "").lower()
    assigned_by_me = assigned_by_param in {"1", "true", "on", "yes"}
    only_me_param = (request.args.get("only_me", "") or "").lower()
    only_me = only_me_param in {"1", "true", "on", "yes"}
    if tag_id:
        tag = Tag.query.get_or_404(tag_id)
        if tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
            abort(404)
        if not _can_user_access_tag(tag, current_user):
            abort(403)
        query = Task.query.filter(
            Task.tag_id == tag_id,
            Task.parent_id.is_(None),
            Task.status == TaskStatus.DONE,
        ).filter(_user_task_access_filter(current_user))
    else:
        tag = None
        if current_user.role == "admin":
            query = Task.query.filter(
                Task.parent_id.is_(None),
                Task.status == TaskStatus.DONE,
            ).filter(_user_task_access_filter(current_user))
        else:
            tag_ids = _get_accessible_tag_ids(current_user)
            filters = []
            if tag_ids:
                filters.append(Task.tag_id.in_(tag_ids))
            filters.append(Task.created_by == current_user.id)
            query = Task.query.filter(
                Task.parent_id.is_(None),
                Task.status == TaskStatus.DONE,
                sa.or_(*filters),
            )
            query = query.filter(_user_task_access_filter(current_user))
    if query is not None:
        if only_me:
            # Subquery para tasks onde o usuário é acompanhante
            from app.models.tables import TaskFollower
            follower_subquery = (
                db.session.query(TaskFollower.task_id)
                .filter(TaskFollower.user_id == current_user.id)
                .subquery()
            )

            query = query.filter(Task.is_private.is_(True)).filter(
                sa.or_(
                    Task.created_by == current_user.id,
                    Task.assigned_to == current_user.id,
                    Task.id.in_(follower_subquery),
                )
            )
        if assigned_to_me:
            query = query.filter(Task.assigned_to == current_user.id)
        if assigned_by_me:
            query = query.filter(Task.created_by == current_user.id)
        tasks = (
            query.order_by(Task.completed_at.desc())
            .options(joinedload(Task.tag), joinedload(Task.finisher))
            .offset(5)
            .all()
        )
    else:
        tasks = []
    tasks = _filter_tasks_for_user(tasks, current_user)
    summaries = _load_task_response_summaries((task.id for task in _iter_tasks_with_children(tasks)), current_user.id)
    default_summary = {"unread_count": 0, "total_responses": 0, "last_response": None}
    for task in _iter_tasks_with_children(tasks):
        task.conversation_summary = summaries.get(task.id) or default_summary.copy()
    return render_template(
        "tasks_history.html",
        tag=tag,
        tasks=tasks,
        assigned_to_me=assigned_to_me,
        assigned_by_me=assigned_by_me,
        only_me=only_me,
    )


def _task_conversation_participant_ids(task: Task) -> set[int]:
    """Return user IDs that participate in the task conversation."""

    participant_ids = {task.created_by}
    if task.assigned_to:
        participant_ids.add(task.assigned_to)
    if task.completed_by:
        participant_ids.add(task.completed_by)
    participant_ids.update(_task_follow_up_user_ids(task))
    return {uid for uid in participant_ids if uid}


def _user_can_access_task_conversation(task: Task, user: User) -> bool:
    """Return True when ``user`` is allowed to view/post task responses."""

    return _user_has_task_privileges(task, user)


def _ensure_response_participant(task_id: int, user_id: int) -> TaskResponseParticipant:
    """Return or create a conversation participant row for the given task/user."""

    participant = TaskResponseParticipant.query.filter_by(
        task_id=task_id, user_id=user_id
    ).one_or_none()
    if participant is None:
        participant = TaskResponseParticipant(task_id=task_id, user_id=user_id)
        db.session.add(participant)
        db.session.flush()
    return participant


def _serialize_task(task: Task) -> dict[str, object]:
    """Return a JSON-serializable representation of ``task``."""

    tag = getattr(task, "tag", None)
    assignee = getattr(task, "assignee", None)
    finisher = getattr(task, "finisher", None)
    local_completed_at = (
        task.completed_at.replace(tzinfo=timezone.utc).astimezone(SAO_PAULO_TZ)
        if task.completed_at
        else None
    )
    assignee_name = None
    if assignee:
        assignee_name = assignee.name or assignee.username
    finisher_name = None
    if finisher:
        finisher_name = finisher.name or finisher.username
    follow_up_payload: list[dict[str, object]] = []
    for entry in getattr(task, "follow_up_assignments", []) or []:
        user = entry.user
        if not user:
            continue
        display_name = (user.name or user.username or "").strip() or None
        follow_up_payload.append(
            {
                "id": user.id,
                "name": display_name,
            }
        )

    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status.value if task.status else None,
        "priority": task.priority.value if task.priority else None,
        "tag_id": task.tag_id,
        "tag_name": tag.nome if tag else None,
        "assigned_to": task.assigned_to,
        "assignee_name": assignee_name,
        "created_by": task.created_by,
        "completed_by": task.completed_by,
        "completed_by_name": finisher_name,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "completed_at_display": (
            local_completed_at.strftime("%d/%m/%Y %H:%M") if local_completed_at else None
        ),
        "is_private": task.is_private,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "parent_id": task.parent_id,
        "updated_at": task.updated_at.isoformat() if getattr(task, "updated_at", None) else None,
        "follow_up_users": follow_up_payload,
    }


def _serialize_task_response(response: TaskResponse, viewer_id: int) -> dict[str, object]:
    """Serialize a ``TaskResponse`` into a JSON-friendly payload."""

    author = response.author
    local_created_at = (
        response.created_at.replace(tzinfo=timezone.utc).astimezone(SAO_PAULO_TZ)
        if response.created_at
        else None
    )
    created_at_display = (
        local_created_at.strftime("%d/%m/%Y %H:%M") if local_created_at else None
    )
    body = response.body or ""
    return {
        "id": response.id,
        "task_id": response.task_id,
        "body": body,
        "body_html": body.replace("\n", "<br>"),
        "created_at": response.created_at.isoformat() if response.created_at else None,
        "created_at_display": created_at_display,
        "author": {
            "id": author.id if author else None,
            "name": author.name if author and author.name else author.username if author else None,
        },
        "is_mine": author.id == viewer_id if author else False,
    }


def _build_task_conversation_meta(task: Task, viewer: User) -> dict[str, object]:
    """Return metadata required by the conversation sidebar/drawer."""

    participant_ids = _task_conversation_participant_ids(task)
    participants = User.query.filter(User.id.in_(participant_ids)).all() if participant_ids else []
    participants_info = []
    for person in participants:
        participants_info.append(
            {
                "id": person.id,
                "name": person.name or person.username,
                "is_creator": person.id == task.created_by,
                "is_assignee": person.id == task.assigned_to,
                "is_finisher": person.id == task.completed_by,
            }
        )
    participant_row = TaskResponseParticipant.query.filter_by(
        task_id=task.id, user_id=viewer.id
    ).one_or_none()
    last_read_at = participant_row.last_read_at if participant_row else None
    responses_query = (
        TaskResponse.query.filter_by(task_id=task.id)
        .order_by(TaskResponse.created_at.asc())
        .options(joinedload(TaskResponse.author))
    )
    responses = responses_query.all()
    serialized_responses = [_serialize_task_response(response, viewer.id) for response in responses]
    unread_count = 0
    for response in responses:
        if response.author_id == viewer.id:
            continue
        if not last_read_at or (response.created_at and response.created_at > last_read_at):
            unread_count += 1
    last_response_payload = serialized_responses[-1] if serialized_responses else None
    return {
        "participants": participants_info,
        "unread_count": unread_count,
        "last_response": last_response_payload,
        "total_responses": len(responses),
        "responses": serialized_responses,
        "last_read_at": last_read_at.isoformat() if last_read_at else None,
    }


def _load_task_response_summaries(
    task_ids: Iterable[int], viewer_id: int
) -> dict[int, dict[str, object]]:
    """Return unread counts and last-response info for the given tasks."""

    normalized_ids = {int(task_id) for task_id in task_ids if task_id}
    if not normalized_ids:
        return {}

    response_counts = dict(
        db.session.query(TaskResponse.task_id, sa.func.count(TaskResponse.id))
        .filter(TaskResponse.task_id.in_(normalized_ids))
        .group_by(TaskResponse.task_id)
        .all()
    )

    participant_alias = aliased(TaskResponseParticipant)
    unread_rows = (
        db.session.query(
            TaskResponse.task_id,
            sa.func.count(TaskResponse.id),
        )
        .outerjoin(
            participant_alias,
            sa.and_(
                participant_alias.task_id == TaskResponse.task_id,
                participant_alias.user_id == viewer_id,
            ),
        )
        .filter(TaskResponse.task_id.in_(normalized_ids))
        .filter(
            sa.or_(
                participant_alias.last_read_at.is_(None),
                TaskResponse.created_at > participant_alias.last_read_at,
            )
        )
        .filter(TaskResponse.author_id != viewer_id)
        .group_by(TaskResponse.task_id)
        .all()
    )
    unread_counts = {task_id: count for task_id, count in unread_rows}

    latest_subquery = (
        db.session.query(
            TaskResponse.task_id.label("task_id"),
            sa.func.max(TaskResponse.id).label("latest_id"),
        )
        .filter(TaskResponse.task_id.in_(normalized_ids))
        .group_by(TaskResponse.task_id)
        .subquery()
    )

    latest_rows = (
        db.session.query(TaskResponse, User)
        .join(latest_subquery, TaskResponse.id == latest_subquery.c.latest_id)
        .outerjoin(User, User.id == TaskResponse.author_id)
        .all()
    )

    last_responses: dict[int, dict[str, object]] = {}
    for response, author in latest_rows:
        local_created_at = (
            response.created_at.replace(tzinfo=timezone.utc).astimezone(SAO_PAULO_TZ)
            if response.created_at
            else None
        )
        last_responses[response.task_id] = {
            "body": response.body or "",
            "body_html": (response.body or "").replace("\n", "<br>"),
            "created_at": response.created_at.isoformat() if response.created_at else None,
            "created_at_display": local_created_at.strftime("%d/%m/%Y %H:%M")
            if local_created_at
            else None,
            "author": {
                "id": author.id if author else None,
                "name": author.name if author and author.name else (author.username if author else None),
            },
        }

    summaries: dict[int, dict[str, object]] = {}
    for task_id in normalized_ids:
        summaries[task_id] = {
            "unread_count": unread_counts.get(task_id, 0),
            "total_responses": response_counts.get(task_id, 0),
            "last_response": last_responses.get(task_id),
        }

    return summaries


@app.route("/tasks/<int:task_id>/responses", methods=["GET"])
@login_required
def task_responses_list(task_id: int):
    """Return responses for the given task in JSON format."""

    task = (
        Task.query.options(joinedload(Task.tag), joinedload(Task.assignee), joinedload(Task.creator))
        .get_or_404(task_id)
    )
    if task.tag and task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    if task.is_private and not _user_can_access_task(task, current_user):
        abort(403)
    if (
        not task.is_private
        and not (
            _can_user_access_tag(task.tag, current_user)
            or _user_has_task_privileges(task, current_user)
        )
    ):
        abort(403)
    if not _user_can_access_task_conversation(task, current_user):
        abort(403)
    if task.status not in (TaskStatus.IN_PROGRESS, TaskStatus.DONE):
        return jsonify({"success": False, "error": "conversation_unavailable"}), 409

    meta = _build_task_conversation_meta(task, current_user)
    responses = meta.pop("responses")
    can_post = task.status in (TaskStatus.IN_PROGRESS, TaskStatus.DONE)
    return jsonify(
        {
            "success": True,
            "task": {
                "id": task.id,
                "title": task.title,
                "status": task.status.value,
                "tag": task.tag.nome if task.tag else None,
                "creator_id": task.created_by,
                "assignee_id": task.assigned_to,
            },
            "responses": responses,
            "meta": {
                **meta,
                "can_post": can_post and _user_can_access_task_conversation(task, current_user),
            },
        }
    )


@app.route("/tasks/<int:task_id>/responses", methods=["POST"])
@login_required
def task_responses_create(task_id: int):
    """Create a new task response and notify participants."""

    task = (
        Task.query.options(joinedload(Task.tag), joinedload(Task.assignee), joinedload(Task.creator))
        .get_or_404(task_id)
    )
    if task.tag and task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    if task.is_private and not _user_can_access_task(task, current_user):
        abort(403)
    if (
        not task.is_private
        and not (
            _can_user_access_tag(task.tag, current_user)
            or _user_has_task_privileges(task, current_user)
        )
    ):
        abort(403)
    if not _user_can_access_task_conversation(task, current_user):
        abort(403)
    if task.status not in (TaskStatus.IN_PROGRESS, TaskStatus.DONE):
        return jsonify({"success": False, "error": "conversation_unavailable"}), 409

    payload = request.get_json(silent=True) or {}
    raw_body = (payload.get("body") or "").strip()
    if not raw_body:
        return jsonify({"success": False, "error": "empty_response"}), 400

    cleaned_body = sanitize_html(raw_body)
    if not cleaned_body.strip():
        return jsonify({"success": False, "error": "empty_response"}), 400

    created_at = utc3_now()
    response = TaskResponse(
        task_id=task.id,
        author_id=current_user.id,
        body=cleaned_body,
        created_at=created_at,
    )
    db.session.add(response)

    author_participant = _ensure_response_participant(task.id, current_user.id)
    author_participant.last_read_at = created_at

    recipients: set[int] = set()
    notification_records: list[tuple[int, TaskNotification]] = []
    now = utc3_now()
    sender_name = current_user.name or current_user.username
    body_preview = re.sub(r'<[^>]+>', '', cleaned_body).replace('\n', ' ').strip()
    if len(body_preview) > 90:
        body_preview = f"{body_preview[:87]}..."

    for participant_id in _task_conversation_participant_ids(task):
        participant = _ensure_response_participant(task.id, participant_id)
        if participant_id == current_user.id:
            participant.last_notified_at = now
            continue
        participant.last_notified_at = now
        recipients.add(participant_id)
        message = f'{sender_name} respondeu a tarefa "{task.title}".'
        notification = TaskNotification(
            user_id=participant_id,
            task_id=task.id,
            type=NotificationType.TASK_RESPONSE.value,
            message=message[:255],
            created_at=now,
        )
        db.session.add(notification)
        notification_records.append((participant_id, notification))

    db.session.flush()

    response_payload = _serialize_task_response(response, current_user.id)

    db.session.commit()

    from app.services.realtime import (
        broadcast_task_response_created,
        get_broadcaster,
    )

    if recipients:
        broadcast_task_response_created(
            task.id,
            response_payload,
            recipients=list(recipients),
            exclude_user=current_user.id,
        )

    broadcaster = get_broadcaster()
    for user_id, notification in notification_records:
        broadcaster.broadcast(
            event_type="notification:created",
            data={
                "id": notification.id,
                "task_id": task.id,
                "type": notification.type,
                "message": notification.message,
                "created_at": notification.created_at.isoformat()
                if notification.created_at
                else None,
            },
            user_id=user_id,
            scope="notifications",
        )

    refreshed_meta = _build_task_conversation_meta(task, current_user)

    return jsonify(
        {
            "success": True,
            "response": response_payload,
            "meta": {**refreshed_meta, "can_post": True},
        }
    )


@app.route("/tasks/<int:task_id>/responses/read", methods=["POST"])
@login_required
def task_responses_mark_read(task_id: int):
    """Mark all responses as read for the current user."""

    task = (
        Task.query.options(joinedload(Task.tag), joinedload(Task.assignee), joinedload(Task.creator))
        .get_or_404(task_id)
    )
    if task.tag and task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    if task.is_private and not _user_can_access_task(task, current_user):
        abort(403)
    if (
        not task.is_private
        and not (
            _can_user_access_tag(task.tag, current_user)
            or _user_has_task_privileges(task, current_user)
        )
    ):
        abort(403)
    if not _user_can_access_task_conversation(task, current_user):
        abort(403)
    if task.status not in (TaskStatus.IN_PROGRESS, TaskStatus.DONE):
        return jsonify({"success": False, "error": "conversation_unavailable"}), 409

    participant = _ensure_response_participant(task.id, current_user.id)
    now = utc3_now()
    participant.last_read_at = now

    (
        TaskNotification.query.filter(
            TaskNotification.user_id == current_user.id,
            TaskNotification.task_id == task.id,
            TaskNotification.type == NotificationType.TASK_RESPONSE.value,
            TaskNotification.read_at.is_(None),
        ).update({"read_at": now}, synchronize_session=False)
    )

    db.session.commit()

    meta = _build_task_conversation_meta(task, current_user)
    return jsonify({"success": True, "meta": {**meta, "can_post": True}})


def _is_safe_referrer(referrer_url: str) -> bool:
    """Return True when referrer belongs to the current host."""

    parsed = urlparse(referrer_url)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return False

    ref_host = (parsed.netloc or "").split(":", 1)[0].lower()
    current_host = (request.host or "").split(":", 1)[0].lower()

    if ref_host and ref_host != current_host:
        return False

    return True


@app.route("/tasks/<int:task_id>")
@login_required
def tasks_view(task_id):
    """Display details of a completed task."""
    task = (
        Task.query.options(
            joinedload(Task.tag),
            joinedload(Task.assignee),
            joinedload(Task.finisher),
            joinedload(Task.parent),
            joinedload(Task.status_history),
            joinedload(Task.attachments),
        )
        .get_or_404(task_id)
    )
    if task.is_private:
        if not _user_can_access_task(task, current_user):
            abort(403)
        if task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER and current_user.role != "admin":
            abort(404)
    else:
        if task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
            abort(404)
        if not (
            _can_user_access_tag(task.tag, current_user)
            or _user_has_task_privileges(task, current_user)
        ):
            abort(403)
    priority_labels = {"low": "Baixa", "medium": "Média", "high": "Alta"}
    priority_order = ["low", "medium", "high"]

    # Determinar URL de retorno
    explicit_return_url = request.args.get("return_url")
    if explicit_return_url:
        cancel_url = explicit_return_url
    else:
        if task.is_private:
            cancel_url = (
                url_for("tasks_overview")
                if current_user.role == "admin"
                else url_for("tasks_overview_mine")
            )
        elif _can_user_access_tag(task.tag, current_user):
            cancel_url = url_for("tasks_history", tag_id=task.tag_id)
        else:
            cancel_url = url_for("tasks_history", assigned_by_me=1)

        # Usar referrer se disponível e seguro
        if (
            request.referrer
            and request.referrer != request.url
            and _is_safe_referrer(request.referrer)
        ):
            cancel_url = request.referrer

    # Buscar histórico de alterações da tarefa
    from app.models.tables import TaskHistory
    history_entries = (
        TaskHistory.query
        .filter_by(task_id=task_id)
        .order_by(TaskHistory.changed_at.desc())
        .all()
    )

    return render_template(
        "tasks_view.html",
        task=task,
        priority_labels=priority_labels,
        priority_order=priority_order,
        cancel_url=cancel_url,
        history_entries=history_entries,
    )

@app.route("/tasks/<int:task_id>/status", methods=["POST"])
@login_required
def update_task_status(task_id):
    """Update a task status and record its history."""
    task = Task.query.get_or_404(task_id)
    current_app.logger.info(
        f"Updating task status - task_id: {task_id}, is_private: {task.is_private}, "
        f"created_by: {task.created_by}, current_user: {current_user.id}, "
        f"tag: {task.tag.nome}"
    )
    if task.is_private and not _user_can_access_task(task, current_user):
        current_app.logger.warning(f"Access denied: User {current_user.id} cannot modify private task {task_id}")
        abort(403)
    if task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    # For private tasks, access is already validated above
    if (
        not task.is_private
        and not (
            _can_user_access_tag(task.tag, current_user)
            or _user_has_task_privileges(task, current_user)
        )
    ):
        current_app.logger.warning(f"Access denied: User {current_user.id} cannot access tag {task.tag.nome}")
        abort(403)
    data = request.get_json() or {}
    status_value = data.get("status")
    try:
        new_status = TaskStatus(status_value)
    except Exception:
        abort(400)
    # Avoid unnecessary writes and undefined variables when status is unchanged
    if task.status == new_status:
        return jsonify({"success": True, "task": _serialize_task(task)})
    if current_user.role != "admin":
        allowed = {
            TaskStatus.PENDING: {TaskStatus.IN_PROGRESS},
            TaskStatus.IN_PROGRESS: {TaskStatus.DONE, TaskStatus.PENDING},
            TaskStatus.DONE: {TaskStatus.IN_PROGRESS},
        }
        if new_status not in allowed.get(task.status, set()):
            abort(403)
        # Only creator can reopen a completed task
        if task.status == TaskStatus.DONE and new_status == TaskStatus.IN_PROGRESS:
            if task.created_by != current_user.id:
                abort(403)
    if task.status != new_status:
        history = TaskStatusHistory(
            task_id=task.id,
            from_status=task.status,
            to_status=new_status,
            changed_by=current_user.id,
        )
        old_status = task.status
        task.status = new_status
        if new_status == TaskStatus.IN_PROGRESS:
            if old_status != TaskStatus.DONE or current_user.role != "admin":
                if task.assigned_to != current_user.id:
                    task._skip_assignment_notification = True
                task.assigned_to = current_user.id
            task.completed_by = None
            task.completed_at = None
        elif new_status == TaskStatus.DONE:
            task.completed_by = current_user.id
            task.completed_at = utc3_now()
        elif new_status == TaskStatus.PENDING:
            task.assigned_to = None
            task.completed_by = None
            task.completed_at = None
        else:
            task.completed_by = None
            task.completed_at = None

        status_notification_records: list[tuple[int, TaskNotification]] = []
        actor_name = current_user.name or current_user.username
        now = utc3_now()
        local_display = (
            now.replace(tzinfo=timezone.utc).astimezone(SAO_PAULO_TZ).strftime("%d/%m/%Y às %H:%M")
        )
        recipients: set[int] = set()
        status_message: str | None = None

        if new_status == TaskStatus.IN_PROGRESS:
            if old_status == TaskStatus.DONE:
                status_message = f'{actor_name} reabriu a tarefa "{task.title}".'
            else:
                status_message = f'{actor_name} iniciou a tarefa "{task.title}" às {local_display}.'
            # Aplicar regra: responsável OU setor
            recipients = _get_task_notification_recipients(task, exclude_user_id=current_user.id)
        elif new_status == TaskStatus.DONE:
            status_message = f'{actor_name} concluiu a tarefa "{task.title}" às {local_display}.'
            # Aplicar regra: responsável OU setor (além do criador)
            recipients = _get_task_notification_recipients(task, exclude_user_id=current_user.id)
            # Sempre incluir o criador
            if task.created_by and task.created_by != current_user.id:
                recipients.add(task.created_by)
        elif new_status == TaskStatus.PENDING and old_status == TaskStatus.IN_PROGRESS:
            status_message = f'{actor_name} moveu a tarefa "{task.title}" para pendente.'
            # Como assigned_to foi removido na linha 7715, agora notifica TODO O SETOR
            recipients = _get_task_notification_recipients(task, exclude_user_id=current_user.id)

        if status_message and recipients:
            for user_id in recipients:
                notification = TaskNotification(
                    user_id=user_id,
                    task_id=task.id,
                    type=NotificationType.TASK_STATUS.value,
                    message=status_message[:255],
                    created_at=now,
                )
                db.session.add(notification)
                status_notification_records.append((user_id, notification))

    db.session.add(history)
    if status_notification_records:
        db.session.flush()
    db.session.commit()

    # Broadcast status change
    from app.services.realtime import get_broadcaster, broadcast_task_status_changed
    task_data = _serialize_task(task)
    broadcaster = get_broadcaster()

    if task.is_private:
        # For private tasks, broadcast only to users with access.
        recipients = {task.created_by}
        if task.assigned_to:
            recipients.add(task.assigned_to)
        
        # Add followers
        followers = TaskFollower.query.filter_by(task_id=task.id).all()
        for follower in followers:
            recipients.add(follower.user_id)

        for user_id in recipients:
            # Don't send to the user who made the change
            if user_id == current_user.id:
                continue
            
            broadcaster.broadcast(
                event_type="task:status_changed",
                data={
                    "id": task.id,
                    "old_status": old_status.value,
                    "new_status": new_status.value,
                    "task": task_data,
                },
                user_id=user_id,
                scope="tasks",
            )
    else:
        # Public tasks are broadcast to everyone in the 'tasks' scope
        broadcast_task_status_changed(
            task.id,
            old_status.value,
            new_status.value,
            task_data,
            exclude_user=current_user.id,
        )
    if status_notification_records:
        broadcaster = get_broadcaster()
        for user_id, notification in status_notification_records:
            broadcaster.broadcast(
                event_type="notification:created",
                data={
                    "id": notification.id,
                    "task_id": task.id,
                    "type": notification.type,
                    "message": notification.message,
                    "created_at": notification.created_at.isoformat()
                    if notification.created_at
                    else None,
                },
                user_id=user_id,
                scope="notifications",
            )

    return jsonify({"success": True, "task": task_data})


def _delete_task_recursive(task: Task) -> None:
    """Delete a task and all of its subtasks recursively."""

    for child in list(task.children or []):
        _delete_task_recursive(child)

    for history in TaskStatusHistory.query.filter_by(task_id=task.id).all():
        db.session.delete(history)
    for notification in TaskNotification.query.filter_by(task_id=task.id).all():
        db.session.delete(notification)

    db.session.delete(task)

@app.route("/tasks/<int:task_id>/delete", methods=["POST"])
@login_required
def delete_task(task_id):
    """Remove a task from the system, including its subtasks and history."""

    task = Task.query.get_or_404(task_id)
    if task.is_private and not _user_can_access_task(task, current_user):
        abort(403)
    if task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER and current_user.role != "admin":
        abort(404)
    if current_user.role != "admin" and not _user_has_task_privileges(task, current_user):
        abort(403)

    # Store task ID before deletion for broadcasting
    deleted_task_id = task.id

    _delete_task_recursive(task)
    db.session.commit()

    # Broadcast task deletion
    from app.services.realtime import broadcast_task_deleted
    if not task.is_private:
        broadcast_task_deleted(deleted_task_id, exclude_user=current_user.id)

    return jsonify({"success": True})


## ============================================================================
## PWA SUPPORT
## ============================================================================

# =============================================================================
# ROTAS MIGRADAS PARA blueprints/health.py
# =============================================================================
# @app.route("/offline")
# def offline_page():
#     """Migrado para blueprints/health.py"""
#     pass

# @app.route("/sw.js")
# @csrf.exempt
# def service_worker():
#     """Migrado para blueprints/health.py"""
#     pass



## ============================================================================
## HEALTH CHECK ENDPOINTS - For monitoring and load balancers
## ============================================================================
