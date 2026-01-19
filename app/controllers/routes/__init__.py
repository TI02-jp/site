"""
Handlers de rotas Flask para a aplicacao web JP Contabil Portal.

Este modulo contem as rotas restantes da aplicacao. A maioria das rotas foi
migrada para blueprints separados em: app/controllers/routes/blueprints/

BLUEPRINTS DISPONIVEIS (rotas migradas):
    - health_bp: Health checks, PWA (/ping, /offline, /sw.js)
    - uploads_bp: Upload de arquivos (/upload_image, /upload_file)
    - tags_bp: Gestao de tags (/tags/*)
    - procedimentos_bp: Procedimentos operacionais (/procedimentos/*)
    - acessos_bp: Central de acessos (/acessos/*)
    - auth_bp: Autenticacao (/login, /logout, OAuth)
    - cursos_bp: Catalogo de cursos (/cursos)
    - consultorias_bp: Gestao de consultorias (/consultorias/*)
    - calendario_bp: Calendario de colaboradores (/calendario-colaboradores)
    - diretoria_bp: Gestao da diretoria (/diretoria/*)
    - notifications_bp: Notificacoes e SSE (/notifications/*)
    - notas_bp: Notas de debito (/notas-debito/*)
    - reunioes_bp: Sala de reunioes (/sala-reunioes/*)
    - relatorios_bp: Relatorios administrativos (/relatorios/*)
    - users_bp: Gestao de usuarios (/users/*)
    - tasks_bp: Gestao de tarefas (/tasks/*)
    - empresas_bp: Gestao de empresas (/empresas/*)
    - core_bp: Rotas principais (/, /home)

ROTAS RESTANTES NESTE ARQUIVO:
    - APIs: /api/cnpj, /api/reunioes, /api/calendario-eventos
    - Reunioes: /reuniao/<id>/* (controle de reunioes internas)
    - Relatorios: /relatorio_empresas, /relatorios/*

ARQUIVOS AUXILIARES:
    - _base.py: Helpers e constantes compartilhados
    - _decorators.py: Decorators de autorizacao
    - _error_handlers.py: Tratamento centralizado de erros
    - _validators.py: Validacoes de upload

Autor: Refatoracao automatizada
Data: 2024-12
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
from app.controllers.routes._decorators import is_meeting_only_user
from itsdangerous import URLSafeSerializer, BadSignature
from app.models.tables import (
    User,
    Empresa,
    Consultoria,
    Setor,
    Tag,
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
    GeneralCalendarEvent,
    OperationalProcedure,
)
from app.forms import (
    # Formulários de autenticação
    LoginForm,
    RegistrationForm,
    # Demais formulários da aplicação
    EditUserForm,
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
    OperationalProcedureForm,
)
import os, json, re, secrets, filetype, time, calendar
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


@app.context_processor
def inject_stats():
    """Inject global statistics into templates without hammering the DB."""
    if not has_request_context():
        return {}
    if not current_user or not current_user.is_authenticated:
        return {}
    stats = _get_cached_stats(include_admin_metrics=current_user.role == "admin")
    if current_user.role != "admin":
        stats = dict(stats)
        stats["total_usuarios"] = 0
        stats["online_users_count"] = 0
    return stats


@app.context_processor
def inject_user_tag_helpers():
    """Expose user tag helper utilities to templates."""
    return dict(
        user_has_tag=user_has_tag,
        can_access_controle_notas=can_access_controle_notas,
        is_meeting_only_user=is_meeting_only_user,
        has_report_access=has_report_access,
    )


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
    guess = filetype.guess(header)
    detected = guess.extension if guess else None
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

PORTAL_PERMISSION_DEFINITIONS: dict[str, dict[str, str]] = {
    "acessos_manage": {
        "title": "Central de Acessos - Atalhos",
        "description": "Criar, editar e excluir atalhos na central de acessos",
    },
}

ALL_PERMISSION_DEFINITIONS: dict[str, dict[str, str]] = {
    **REPORT_DEFINITIONS,
    **PORTAL_PERMISSION_DEFINITIONS,
}


def _get_ti_tag() -> Tag | None:
    """Return the TI tag if it exists (cached per request)."""

    if not has_request_context():
        return Tag.query.filter(sa.func.lower(Tag.nome) == "ti").first()

    if not hasattr(g, "_ti_tag"):
        g._ti_tag = Tag.query.filter(sa.func.lower(Tag.nome) == "ti").first()
    return g._ti_tag


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


def _require_master_admin() -> None:
    """Abort with 403 if current user is not admin or master."""

    if current_user.role != "admin" and not getattr(current_user, "is_master", False):
        abort(403)



# ============================================================================
# TODAS AS ROTAS FORAM MIGRADAS PARA BLUEPRINTS
# ============================================================================
#
# Este arquivo agora contém apenas:
#   - Imports necessários para os blueprints
#   - Decoradores customizados (@report_access_required, @meeting_only_access_check)
#   - Context processors
#   - Funções auxiliares compartilhadas
#
# Todas as rotas foram migradas para os seguintes blueprints:
#   - blueprints/core.py (/, /home)
#   - blueprints/auth.py (login, logout, OAuth)
#   - blueprints/empresas.py (empresas, APIs)
#   - blueprints/reunioes.py (reuniões)
#   - blueprints/relatorios.py (relatórios)
#   - blueprints/users.py (usuários)
#   - blueprints/tasks.py (tarefas)
#   - blueprints/notifications.py (notificações)
#   - blueprints/uploads.py (uploads)
#   - blueprints/tags.py (tags)
#   - blueprints/consultorias.py (consultorias)
#   - blueprints/notas.py (notas de débito)
#   - blueprints/diretoria.py (diretoria)
#   - blueprints/cursos.py (cursos)
#   - blueprints/calendario.py (calendário)
#   - blueprints/acessos.py (acessos)
#   - blueprints/procedimentos.py (procedimentos)
#   - blueprints/health.py (health checks, PWA)
#
# Veja: app/controllers/routes/blueprints/__init__.py para registro de blueprints
# ============================================================================
