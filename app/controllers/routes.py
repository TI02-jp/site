"""Flask route handlers for the web application."""

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
)
from functools import wraps
from collections import Counter, deque
import unicodedata
from flask_login import current_user, login_required, login_user, logout_user
from app import app, db, csrf, limiter
from app.extensions.cache import cache, get_cache_timeout
from app.utils.security import sanitize_html
from app.utils.mailer import send_email, EmailDeliveryError
from app.utils.permissions import is_user_admin
from app.models.tables import (
    User,
    Empresa,
    Departamento,
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
    TaskStatusHistory,
    TaskNotification,
    NotificationType,
    TaskAttachment,
    TaskResponse,
    TaskResponseParticipant,
    AccessLink,
    Course,
    CourseTag,
    DiretoriaEvent,
    DiretoriaAgreement,
    DiretoriaFeedback,
    GeneralCalendarEvent,
    NotaDebito,
    CadastroNota,
    Announcement,
    AnnouncementAttachment,
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
    AnnouncementForm,
    DiretoriaAcordoForm,
    DiretoriaFeedbackForm,
    OperationalProcedureForm,
    NotaDebitoForm,
    CadastroNotaForm,
    PAGAMENTO_CHOICES,
)
import os, json, re, secrets, imghdr, time
import requests
from urllib.parse import urlparse
from werkzeug.utils import secure_filename
from uuid import uuid4
from sqlalchemy import or_, cast, String, text, inspect
from sqlalchemy.exc import IntegrityError, NoSuchTableError, SQLAlchemyError, OperationalError
import sqlalchemy as sa
from sqlalchemy.orm import joinedload, aliased
from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token
from google.auth.transport.requests import Request
from app.services.cnpj import consultar_cnpj
from app.services.courses import CourseStatus, get_courses_overview
from app.services.google_calendar import get_calendar_timezone
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


def ensure_diretoria_agreement_schema() -> None:
    """Ensure the Diretoria agreements table has the expected columns."""

    cache_key = "_diretoria_agreement_schema_checked"
    if app.config.get(cache_key):
        return

    bind = db.session.get_bind()
    if bind is None:
        return

    try:
        inspector = inspect(bind)
        columns = {column["name"] for column in inspector.get_columns("diretoria_agreements")}
        unique_constraints = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("diretoria_agreements")
        }
        indexes = {
            index["name"]: index for index in inspector.get_indexes("diretoria_agreements")
        }
        foreign_keys = inspector.get_foreign_keys("diretoria_agreements")
    except NoSuchTableError:
        # Table is missing entirely – nothing to fix at runtime.
        return
    except SQLAlchemyError as exc:  # pragma: no cover - defensive logging path
        db.session.rollback()
        current_app.logger.warning(
            "Falha ao inspecionar a tabela diretoria_agreements: %s", exc
        )
        return

    needs_column = "agreement_date" not in columns
    needs_unique_drop = "uq_diretoria_agreements_user_id" in unique_constraints

    if not needs_column and not needs_unique_drop:
        app.config[cache_key] = True
        return

    dialect = bind.dialect.name

    user_fk = next(
        (
            fk
            for fk in foreign_keys
            if fk.get("constrained_columns") == ["user_id"]
        ),
        None,
    )

    def quote_identifier(identifier: str) -> str:
        if dialect == "mysql":
            return f"`{identifier}`"
        if dialect == "postgresql":
            return f'"{identifier}"'
        return identifier

    has_nonunique_user_index = any(
        index.get("column_names") == ["user_id"]
        and not index.get("unique", False)
        for index in indexes.values()
        if index.get("name") != "uq_diretoria_agreements_user_id"
    )

    try:
        with bind.begin() as connection:
            if needs_unique_drop:
                if dialect == "mysql":
                    fk_name = (user_fk or {}).get("name")
                    referred_table = (user_fk or {}).get("referred_table")
                    referred_columns = (user_fk or {}).get("referred_columns") or []
                    fk_options = (user_fk or {}).get("options") or {}

                    if fk_name:
                        connection.execute(
                            text(
                                "ALTER TABLE diretoria_agreements "
                                f"DROP FOREIGN KEY {quote_identifier(fk_name)}"
                            )
                        )

                    connection.execute(
                        text(
                            "ALTER TABLE diretoria_agreements "
                            "DROP INDEX uq_diretoria_agreements_user_id"
                        )
                    )

                    if not has_nonunique_user_index:
                        try:
                            connection.execute(
                                text(
                                    "ALTER TABLE diretoria_agreements "
                                    "ADD INDEX ix_diretoria_agreements_user_id (user_id)"
                                )
                            )
                        except OperationalError as add_index_exc:
                            # Error code 1061 indicates the index already exists.
                            if getattr(getattr(add_index_exc, "orig", None), "args", [None])[0] != 1061:
                                raise

                    if fk_name and referred_table and referred_columns:
                        referenced_cols_sql = ", ".join(
                            quote_identifier(column) for column in referred_columns
                        )
                        constraint_sql = (
                            "ALTER TABLE diretoria_agreements "
                            f"ADD CONSTRAINT {quote_identifier(fk_name)} "
                            "FOREIGN KEY (user_id) "
                            f"REFERENCES {quote_identifier(referred_table)} "
                            f"({referenced_cols_sql})"
                        )
                        ondelete = fk_options.get("ondelete")
                        if ondelete:
                            constraint_sql += f" ON DELETE {ondelete.upper()}"
                        onupdate = fk_options.get("onupdate")
                        if onupdate:
                            constraint_sql += f" ON UPDATE {onupdate.upper()}"

                        connection.execute(text(constraint_sql))
                else:
                    connection.execute(
                        text(
                            "ALTER TABLE diretoria_agreements "
                            "DROP CONSTRAINT uq_diretoria_agreements_user_id"
                        )
                    )

            if needs_column:
                connection.execute(
                    text(
                        "ALTER TABLE diretoria_agreements "
                        "ADD COLUMN agreement_date DATE NULL"
                    )
                )
                connection.execute(
                    text(
                        "UPDATE diretoria_agreements "
                        "SET agreement_date = COALESCE(DATE(created_at), CURRENT_DATE)"
                    )
                )

                if dialect == "mysql":
                    connection.execute(
                        text(
                            "ALTER TABLE diretoria_agreements "
                            "MODIFY agreement_date DATE NOT NULL"
                        )
                    )
                else:
                    connection.execute(
                        text(
                            "ALTER TABLE diretoria_agreements "
                            "ALTER COLUMN agreement_date SET NOT NULL"
                        )
                    )
    except (SQLAlchemyError, OperationalError) as exc:  # pragma: no cover - defensive logging path
        db.session.rollback()
        current_app.logger.error(
            "Não foi possível ajustar a tabela diretoria_agreements automaticamente: %s",
            exc,
        )
        return

    db.session.expire_all()
    app.config[cache_key] = True


EXCLUDED_TASK_TAGS = ["Reunião"]
EXCLUDED_TASK_TAGS_LOWER = {t.lower() for t in EXCLUDED_TASK_TAGS}
PERSONAL_TAG_PREFIX = "__personal__"


ANNOUNCEMENTS_UPLOAD_SUBDIR = os.path.join("uploads", "announcements")
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


EVENT_TYPE_LABELS = {
    "treinamento": "Treinamento",
    "data_comemorativa": "Data comemorativa",
    "evento": "Evento",
}

EVENT_AUDIENCE_LABELS = {
    "interno": "Interno",
    "externo": "Externo",
    "ambos": "Ambos",
}

EVENT_CATEGORY_LABELS = {
    "cafe": "Café da manhã",
    "almoco": "Almoço",
    "lanche": "Lanche",
    "outros": "Outros serviços",
}


def _normalize_photo_entry(value: str) -> str | None:
    """Return a sanitized photo reference or ``None`` when invalid."""

    if not isinstance(value, str):
        return None

    trimmed = value.strip()
    if not trimmed:
        return None

    parsed = urlparse(trimmed)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc

    if scheme == "https" and netloc:
        return parsed.geturl()

    if scheme == "http" and netloc:
        static_path = parsed.path or ""
        if not static_path.startswith("/static/"):
            return None

        # Accept insecure scheme only when targeting this application host.
        allowed_hosts: set[str] = set()
        if has_request_context():
            host = (request.host or "").lower()
            if host:
                allowed_hosts.add(host)
        server_name = (current_app.config.get("SERVER_NAME") or "").lower()
        if server_name:
            allowed_hosts.add(server_name)

        normalized_netloc = netloc.lower()
        if not allowed_hosts:
            return static_path if static_path.startswith("/static/uploads/") else None

        netloc_base = normalized_netloc.split(":", 1)[0]
        for allowed in allowed_hosts:
            if not allowed:
                continue
            allowed_base = allowed.split(":", 1)[0]
            if normalized_netloc == allowed or netloc_base == allowed_base:
                return static_path

        return None

    if scheme and scheme not in {"http", "https"}:
        return None

    if not parsed.scheme and not parsed.netloc:
        if trimmed.startswith("/"):
            return "/" + trimmed.lstrip("/")
        if trimmed.lower().startswith("static/"):
            return "/" + trimmed.lstrip("/")

    return None


def _resolve_local_photo_path(normalized_photo_url: str) -> str | None:
    """Return the filesystem path for a stored upload inside ``/static``."""

    parsed = urlparse(normalized_photo_url)
    path = parsed.path if parsed.scheme else normalized_photo_url
    if not path:
        return None

    relative_path = path.lstrip("/")
    if not relative_path.startswith("static/uploads/"):
        return None

    safe_relative = os.path.normpath(relative_path)
    if not safe_relative.startswith("static/uploads/"):
        return None

    return os.path.join(current_app.root_path, safe_relative)


def _remove_announcement_attachment(attachment_path: str | None) -> None:
    """Delete an announcement attachment from disk if it exists."""

    if not attachment_path or not has_request_context():
        return

    static_root = os.path.join(current_app.root_path, "static")
    file_path = os.path.join(static_root, attachment_path)

    try:
        os.remove(file_path)
    except FileNotFoundError:
        return


def _remove_announcement_attachments(paths: Iterable[str | None]) -> None:
    """Remove multiple stored announcement attachments from disk."""

    seen: set[str] = set()
    for path in paths:
        if not path:
            continue
        normalized = path.replace("\\", "/")
        if normalized in seen:
            continue
        seen.add(normalized)
        _remove_announcement_attachment(normalized)


def _save_announcement_file(uploaded_file) -> dict[str, str | None]:
    """Persist an uploaded file and return its storage metadata."""

    original_name = secure_filename(uploaded_file.filename or "")
    extension = os.path.splitext(original_name)[1].lower()
    unique_name = f"{uuid4().hex}{extension}"

    upload_directory = os.path.join(
        current_app.root_path, "static", ANNOUNCEMENTS_UPLOAD_SUBDIR
    )
    os.makedirs(upload_directory, exist_ok=True)

    stored_path = os.path.join(upload_directory, unique_name)
    uploaded_file.save(stored_path)

    relative_path = os.path.join(ANNOUNCEMENTS_UPLOAD_SUBDIR, unique_name).replace(
        "\\", "/"
    )
    mime_type = uploaded_file.mimetype or guess_type(original_name)[0]

    return {
        "path": relative_path,
        "name": original_name or None,
        "mime_type": mime_type,
    }


def _normalize_announcement_content(raw_content: str | None) -> str:
    """Sanitize announcement bodies and preserve line breaks for plain text."""

    cleaned = sanitize_html(raw_content or "")
    if not cleaned:
        return ""

    if not re.search(r"<[a-zA-Z/][^>]*>", cleaned):
        normalized = cleaned.replace("\r\n", "\n").replace("\r", "\n")
        return normalized.replace("\n", "<br>")

    return cleaned


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


def _format_event_timestamp(raw_dt: datetime | None) -> str:
    """Return a São Paulo formatted timestamp for Diretoria JP views."""

    if raw_dt is None:
        return "—"

    timestamp = raw_dt
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    localized = timestamp.astimezone(SAO_PAULO_TZ)
    return localized.strftime("%d/%m/%Y %H:%M")


def _cleanup_diretoria_photo_uploads(
    photo_urls: Iterable[str], *, exclude_event_id: int | None = None
) -> None:
    """Delete unused Diretoria event photo files from the ``uploads`` folder."""

    normalized_to_path: dict[str, str] = {}
    for photo_url in photo_urls:
        normalized = _normalize_photo_entry(photo_url)
        if not normalized:
            continue

        file_path = _resolve_local_photo_path(normalized)
        if not file_path:
            continue

        normalized_to_path[normalized] = file_path

    if not normalized_to_path:
        return

    query = DiretoriaEvent.query
    if exclude_event_id is not None:
        query = query.filter(DiretoriaEvent.id != exclude_event_id)

    still_in_use: set[str] = set()
    for _, other_photos in query.with_entities(
        DiretoriaEvent.id, DiretoriaEvent.photos
    ):
        if not isinstance(other_photos, list):
            continue

        for other_photo in other_photos:
            normalized_other = _normalize_photo_entry(other_photo)
            if normalized_other in normalized_to_path:
                still_in_use.add(normalized_other)

        if len(still_in_use) == len(normalized_to_path):
            break

    for normalized, file_path in normalized_to_path.items():
        if normalized in still_in_use:
            continue

        if not os.path.exists(file_path):
            continue

        try:
            os.remove(file_path)
        except OSError:
            current_app.logger.warning(
                "Não foi possível remover o arquivo de foto não utilizado: %s",
                file_path,
                exc_info=True,
            )


def parse_diretoria_event_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate and normalize Diretoria JP event data sent from the form."""

    name = (payload.get("name") or "").strip()
    event_type = payload.get("type")
    date_raw = payload.get("date")
    description = (payload.get("description") or "").strip()
    audience = payload.get("audience")
    participants_raw = payload.get("participants")
    categories_payload = payload.get("categories") or {}
    photos_payload = payload.get("photos")

    errors: list[str] = []

    if not name:
        errors.append("Informe o nome do evento.")

    if event_type not in EVENT_TYPE_LABELS:
        errors.append("Selecione um tipo de evento válido.")

    try:
        event_date = datetime.strptime(str(date_raw), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        errors.append("Informe uma data válida para o evento.")
        event_date = None

    if audience not in EVENT_AUDIENCE_LABELS:
        errors.append("Selecione o público participante do evento.")

    try:
        participants = int(participants_raw)
        if participants < 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("Informe o número de participantes do evento.")
        participants = 0

    services_payload: dict[str, dict[str, object]] = {}
    total_event = Decimal("0.00")
    photos: list[str] = []

    if photos_payload is None:
        photos = []
    elif isinstance(photos_payload, list):
        seen_photos: set[str] = set()
        for entry in photos_payload:
            normalized_url = _normalize_photo_entry(entry)
            if not normalized_url or normalized_url in seen_photos:
                continue

            seen_photos.add(normalized_url)
            photos.append(normalized_url)
    else:
        errors.append("Formato inválido ao enviar as fotos do evento.")

    for key in EVENT_CATEGORY_LABELS:
        category_data = (
            categories_payload.get(key, {})
            if isinstance(categories_payload, dict)
            else {}
        )
        items_data = []
        if isinstance(category_data, dict):
            items_data = category_data.get("items", []) or []

        processed_items: list[dict[str, object]] = []
        category_total = Decimal("0.00")

        if isinstance(items_data, list):
            for item in items_data:
                if not isinstance(item, dict):
                    continue

                item_name = (item.get("name") or "").strip()
                if not item_name:
                    continue

                try:
                    quantity = Decimal(str(item.get("quantity", "0")))
                    unit_value = Decimal(str(item.get("unit_value", "0")))
                except (InvalidOperation, TypeError):
                    continue

                if quantity < 0 or unit_value < 0:
                    continue

                line_total = (quantity * unit_value).quantize(Decimal("0.01"))

                processed_items.append(
                    {
                        "name": item_name,
                        "quantity": float(quantity),
                        "unit_value": float(unit_value),
                        "total": float(line_total),
                    }
                )

                category_total += line_total

        category_total = category_total.quantize(Decimal("0.01"))
        services_payload[key] = {
            "items": processed_items,
            "total": float(category_total),
        }
        total_event += category_total

    total_event = total_event.quantize(Decimal("0.01"))

    normalized = {
        "name": name,
        "event_type": event_type,
        "event_date": event_date,
        "description": description or None,
        "audience": audience,
        "participants": participants,
        "services": services_payload,
        "total_cost": total_event,
        "photos": photos,
    }

    return normalized, errors


def build_google_flow(state: str | None = None) -> Flow:
    """Return a configured Google OAuth ``Flow`` instance."""
    client_id = current_app.config.get("GOOGLE_CLIENT_ID")
    client_secret = current_app.config.get("GOOGLE_CLIENT_SECRET")
    if not (client_id and client_secret):
        abort(404)

    redirect_uri = get_google_redirect_uri()

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        },
        scopes=GOOGLE_OAUTH_SCOPES,
        state=state,
    )
    flow.redirect_uri = redirect_uri
    return flow


def get_google_redirect_uri() -> str:
    """Return the redirect URI registered with Google."""

    configured_uri = current_app.config.get("GOOGLE_REDIRECT_URI")
    if configured_uri:
        return configured_uri

    callback_path = url_for("google_callback", _external=False)

    if has_request_context():
        scheme = request.scheme or "http"
        host = request.host

        forwarded = request.headers.get("Forwarded")
        if forwarded:
            forwarded = forwarded.split(",", 1)[0]
            forwarded_parts = {}
            for part in forwarded.split(";"):
                if "=" not in part:
                    continue
                key, value = part.split("=", 1)
                forwarded_parts[key.strip().lower()] = value.strip().strip('"')
            scheme = forwarded_parts.get("proto", scheme) or scheme
            host = forwarded_parts.get("host", host) or host

        forwarded_proto = request.headers.get("X-Forwarded-Proto")
        if forwarded_proto:
            scheme = forwarded_proto.split(",", 1)[0].strip() or scheme

        forwarded_host = request.headers.get("X-Forwarded-Host")
        if forwarded_host:
            host = forwarded_host.split(",", 1)[0].strip() or host

        forwarded_port = request.headers.get("X-Forwarded-Port")
        if forwarded_port:
            port = forwarded_port.split(",", 1)[0].strip()
            if port:
                default_port = "443" if scheme == "https" else "80"
                if ":" not in host and port != default_port:
                    host = f"{host}:{port}"

        scheme = scheme or current_app.config.get("PREFERRED_URL_SCHEME", "http")
        host = host or request.host

        return urlunsplit((scheme, host, callback_path, "", ""))

    scheme = current_app.config.get("PREFERRED_URL_SCHEME", "http")
    server_name = current_app.config.get("SERVER_NAME")
    if server_name:
        return urlunsplit((scheme, server_name, callback_path, "", ""))

    return url_for("google_callback", _external=True, _scheme=scheme)


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



def _get_stats_cache_timeout() -> int:
    return get_cache_timeout("PORTAL_STATS_CACHE_TIMEOUT", 300)


def _get_notification_cache_timeout() -> int:
    return get_cache_timeout("NOTIFICATION_COUNT_CACHE_TIMEOUT", 60)


def _get_notification_version() -> int:
    version = cache.get(_NOTIFICATION_VERSION_KEY)
    if version is None:
        version = int(time.time())
        _set_notification_version(int(version))
    return int(version)


def _set_notification_version(version: int) -> None:
    ttl = max(_get_notification_cache_timeout(), 300)
    cache.set(_NOTIFICATION_VERSION_KEY, int(version), timeout=ttl)


def _notification_cache_key(user_id: int) -> str:
    return f"{_NOTIFICATION_COUNT_KEY_PREFIX}{_get_notification_version()}:{user_id}"


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


def _get_unread_notifications_count(user_id: int, allow_cache: bool = True) -> int:
    """Retrieve unread notification count with centralized cache support."""
    cache_key = _notification_cache_key(user_id)
    if allow_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return int(cached)

    unread = TaskNotification.query.filter(
        TaskNotification.user_id == user_id,
        TaskNotification.read_at.is_(None),
    ).count()

    cache.set(cache_key, int(unread), timeout=_get_notification_cache_timeout())
    return unread


def _invalidate_notification_cache(user_id: Optional[int] = None) -> None:
    """Drop cached unread counts for a specific user or everyone."""
    if user_id is None:
        _set_notification_version(_get_notification_version() + 1)
        return
    cache.delete(_notification_cache_key(user_id))


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

@app.route("/upload_image", methods=["POST"])
@login_required
def upload_image():
    """Handle image uploads from the WYSIWYG editor."""
    from app.utils.audit import log_user_action, ActionType, ResourceType

    if "image" not in request.files:
        return jsonify({"error": "Nenhuma imagem enviada"}), 400

    file = request.files["image"]
    if not file.filename:
        return jsonify({"error": "Nome de arquivo vazio"}), 400

    if not allowed_file(file.filename) or not is_safe_image_upload(file):
        return jsonify({"error": "Imagem invalida ou nao permitida"}), 400

    filename = secure_filename(file.filename)
    file_size = _get_file_size_bytes(file)
    soft_limit_mb = current_app.config.get("WYSIWYG_UPLOAD_SOFT_LIMIT_MB")
    if soft_limit_mb and file_size > soft_limit_mb * 1024 * 1024:
        current_app.logger.warning(
            "Upload de imagem excedeu limite orientativo: %s (%.2f MB)",
            filename,
            file_size / (1024 * 1024),
        )
    unique_name = f"{uuid4().hex}_{filename}"
    upload_folder = os.path.join(current_app.root_path, "static", "uploads")
    file_path = os.path.join(upload_folder, unique_name)

    try:
        os.makedirs(upload_folder, exist_ok=True)
        file.save(file_path)
        file_url = url_for("static", filename=f"uploads/{unique_name}", _external=True)

        # Log file upload
        log_user_action(
            action_type=ActionType.UPLOAD,
            resource_type=ResourceType.FILE,
            action_description=f'Fez upload de imagem {filename}',
            new_values={
                'original_filename': filename,
                'saved_filename': unique_name,
                'file_size_bytes': file_size,
                'file_type': 'image',
                'file_url': file_url,
            }
        )

        return jsonify({"image_url": file_url})
    except Exception as exc:
        current_app.logger.exception("Falha ao salvar upload de imagem", exc_info=exc)
        return jsonify({"error": "Erro no servidor ao salvar arquivo"}), 500

@app.route("/upload_file", methods=["POST"])
@login_required
def upload_file():
    """Handle file uploads (images + PDFs) from the WYSIWYG editor."""
    from app.utils.audit import log_user_action, ActionType, ResourceType

    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Nome de arquivo vazio"}), 400

    if not allowed_file_with_pdf(file.filename):
        return jsonify({"error": "Extensao de arquivo nao permitida"}), 400

    is_pdf = file.filename.lower().endswith(".pdf")
    if is_pdf:
        if not is_safe_pdf_upload(file):
            return jsonify({"error": "PDF invalido ou corrompido"}), 400
    else:
        if not is_safe_image_upload(file):
            return jsonify({"error": "Imagem invalida ou nao permitida"}), 400

    filename = secure_filename(file.filename)
    file_size = _get_file_size_bytes(file)
    soft_limit_mb = current_app.config.get("WYSIWYG_UPLOAD_SOFT_LIMIT_MB")
    if soft_limit_mb and file_size > soft_limit_mb * 1024 * 1024:
        current_app.logger.warning(
            "Upload de arquivo excedeu limite orientativo: %s (%.2f MB)",
            filename,
            file_size / (1024 * 1024),
        )
    unique_name = f"{uuid4().hex}_{filename}"
    upload_folder = os.path.join(current_app.root_path, "static", "uploads")
    file_path = os.path.join(upload_folder, unique_name)

    try:
        os.makedirs(upload_folder, exist_ok=True)
        file.save(file_path)
        file_url = url_for("static", filename=f"uploads/{unique_name}", _external=True)

        # Log file upload
        log_user_action(
            action_type=ActionType.UPLOAD,
            resource_type=ResourceType.FILE,
            action_description=f'Fez upload de arquivo {filename}',
            new_values={
                'original_filename': filename,
                'saved_filename': unique_name,
                'file_size_bytes': file_size,
                'file_type': 'pdf' if is_pdf else 'image',
                'file_url': file_url,
            }
        )

        return jsonify({
            "file_url": file_url,
            "is_pdf": is_pdf,
            "filename": filename,
        })
    except Exception as exc:
        current_app.logger.exception("Falha ao salvar upload de arquivo", exc_info=exc)
        return jsonify({"error": "Erro no servidor ao salvar arquivo"}), 500


def admin_required(f):
    """Decorator that restricts access to admin users."""

    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role != "admin":
            abort(403)
        return f(*args, **kwargs)

    return decorated_function


def user_has_tag(tag_name: str) -> bool:
    """Return True if current user has a tag with the given name."""
    return any(tag.nome.lower() == tag_name.lower() for tag in current_user.tags)


def can_access_controle_notas() -> bool:
    """Return True if current user can access Controle de Notas module."""
    if not current_user.is_authenticated:
        return False

    # Admin always has access
    if is_user_admin(current_user):
        return True

    # Check if user has Gestão, Financeiro or Emissor NFe tags
    if user_has_tag('Gestão') or user_has_tag('Financeiro') or user_has_tag('Emissor NFe'):
        return True

    return False


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
    return dict(user_has_tag=user_has_tag, can_access_controle_notas=can_access_controle_notas, is_meeting_only_user=is_meeting_only_user)


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


@app.context_processor
def inject_notification_counts():
    """Expose the number of unread task notifications to templates."""

    if not current_user.is_authenticated:
        return {"unread_notifications_count": 0}
    cached = getattr(g, "_cached_unread_notifications", None)
    if cached is not None:
        return {"unread_notifications_count": cached}
    with track_custom_span("sidebar", "load_unread_notifications"):
        unread = _get_unread_notifications_count(current_user.id)
    g._cached_unread_notifications = unread
    return {"unread_notifications_count": unread}

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

@app.route("/announcements", methods=["GET", "POST"])
@login_required
@meeting_only_access_check
def announcements():
    """List internal announcements and allow admins to create new ones."""

    form = AnnouncementForm()

    search_term = (request.args.get("q") or "").strip()

    base_query = Announcement.query

    if search_term:
        ilike_pattern = f"%{search_term}%"
        base_query = base_query.filter(
            or_(
                Announcement.subject.ilike(ilike_pattern),
                Announcement.content.ilike(ilike_pattern),
            )
        )

    total_announcements = base_query.count()

    announcements_query = (
        base_query.options(
            joinedload(Announcement.created_by),
            joinedload(Announcement.attachments),
        )
        .order_by(Announcement.date.desc(), Announcement.created_at.desc())
    )

    if search_term:
        announcement_items = announcements_query.all()
    else:
        announcement_items = announcements_query.limit(6).all()

    display_count = len(announcement_items)
    history_count = max(total_announcements - 6, 0)
    has_history = not search_term and history_count > 0

    if request.method == "POST":
        if current_user.role != "admin":
            abort(403)

        if form.validate_on_submit():
            cleaned_content = _normalize_announcement_content(form.content.data)
            announcement = Announcement(
                date=form.date.data,
                subject=form.subject.data,
                content=cleaned_content,
                created_by=current_user,
            )

            db.session.add(announcement)
            db.session.flush()

            uploaded_files = [
                storage
                for storage in (form.attachments.data or [])
                if storage and storage.filename
            ]

            for uploaded_file in uploaded_files:
                saved = _save_announcement_file(uploaded_file)
                db.session.add(
                    AnnouncementAttachment(
                        announcement=announcement,
                        file_path=saved["path"],
                        original_name=saved["name"],
                        mime_type=saved["mime_type"],
                    )
                )

            db.session.flush()
            announcement.sync_legacy_attachment_fields()

            _broadcast_announcement_notification(announcement)
            db.session.commit()

            flash("Comunicado criado com sucesso.", "success")
            return redirect(url_for("announcements"))

        flash(
            "Não foi possível criar o comunicado. Verifique os dados informados.",
            "danger",
        )

    announcement_reads: dict[int, bool] = {}
    read_rows = (
        TaskNotification.query.with_entities(
            TaskNotification.announcement_id, TaskNotification.read_at
        )
        .filter(
            TaskNotification.user_id == current_user.id,
            TaskNotification.announcement_id.isnot(None),
        )
        .all()
    )

    for announcement_id, read_at in read_rows:
        if announcement_id is None:
            continue
        if read_at:
            announcement_reads[announcement_id] = True
        elif announcement_id not in announcement_reads:
            announcement_reads[announcement_id] = False

    edit_forms: dict[int, AnnouncementForm] = {}
    if current_user.role == "admin":
        for item in announcement_items:
            edit_form = AnnouncementForm(prefix=f"edit-{item.id}")
            edit_form.date.data = item.date
            edit_form.subject.data = item.subject
            edit_form.content.data = item.content
            edit_forms[item.id] = edit_form

    return render_template(
        "announcements.html",
        form=form,
        announcements=announcement_items,
        edit_forms=edit_forms,
        announcement_reads=announcement_reads,
        search_term=search_term,
        total_announcements=total_announcements,
        display_count=display_count,
        history_mode=False,
        history_count=history_count,
        has_history=has_history,
        search_action_url=url_for("announcements"),
        history_link_url=url_for("announcement_history"),
        history_back_url=None,
    )

@app.route("/announcements/history", methods=["GET"])
@login_required
def announcement_history():
    """Display the backlog of announcements that fall outside the main mural."""

    search_term = (request.args.get("q") or "").strip()

    recent_id_rows = (
        Announcement.query.with_entities(Announcement.id)
        .order_by(Announcement.date.desc(), Announcement.created_at.desc())
        .limit(6)
        .all()
    )
    recent_ids = [row[0] for row in recent_id_rows]

    base_query = Announcement.query

    if recent_ids:
        base_query = base_query.filter(Announcement.id.notin_(recent_ids))

    if search_term:
        ilike_pattern = f"%{search_term}%"
        base_query = base_query.filter(
            or_(
                Announcement.subject.ilike(ilike_pattern),
                Announcement.content.ilike(ilike_pattern),
            )
        )

    total_history = base_query.count()

    # Add pagination to limit memory usage with concurrent users
    page = request.args.get("page", 1, type=int)
    per_page = 50  # Show 50 announcements per page

    announcements_query = (
        base_query.options(
            joinedload(Announcement.created_by),
            joinedload(Announcement.attachments),
        )
        .order_by(Announcement.date.desc(), Announcement.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )

    announcement_items = announcements_query.all()
    display_count = len(announcement_items)
    total_pages = (total_history + per_page - 1) // per_page  # ceiling division

    announcement_reads: dict[int, bool] = {}
    read_rows = (
        TaskNotification.query.with_entities(
            TaskNotification.announcement_id, TaskNotification.read_at
        )
        .filter(
            TaskNotification.user_id == current_user.id,
            TaskNotification.announcement_id.isnot(None),
        )
        .all()
    )

    for announcement_id, read_at in read_rows:
        if announcement_id is None:
            continue
        if read_at:
            announcement_reads[announcement_id] = True
        elif announcement_id not in announcement_reads:
            announcement_reads[announcement_id] = False

    edit_forms: dict[int, AnnouncementForm] = {}
    if current_user.role == "admin":
        for item in announcement_items:
            edit_form = AnnouncementForm(prefix=f"edit-{item.id}")
            edit_form.date.data = item.date
            edit_form.subject.data = item.subject
            edit_form.content.data = item.content
            edit_forms[item.id] = edit_form

    return render_template(
        "announcements.html",
        form=None,
        announcements=announcement_items,
        edit_forms=edit_forms,
        announcement_reads=announcement_reads,
        search_term=search_term,
        total_announcements=total_history,
        display_count=display_count,
        history_mode=True,
        history_count=total_history,
        has_history=False,
        search_action_url=url_for("announcement_history"),
        history_link_url=None,
        history_back_url=url_for("announcements"),
        # Pagination variables
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )

@app.route("/announcements/<int:announcement_id>/update", methods=["POST"])
@login_required
def update_announcement(announcement_id: int):
    """Update an announcement's content and manage its attachments."""

    if current_user.role != "admin":
        abort(403)

    announcement = (
        Announcement.query.options(joinedload(Announcement.attachments))
        .get_or_404(announcement_id)
    )

    form = AnnouncementForm(prefix=f"edit-{announcement_id}")

    if form.validate_on_submit():
        announcement.date = form.date.data
        announcement.subject = form.subject.data
        announcement.content = _normalize_announcement_content(form.content.data)

        attachments_modified = False
        remove_ids = {
            int(attachment_id)
            for attachment_id in request.form.getlist("remove_attachment_ids")
            if attachment_id.isdigit()
        }

        if remove_ids:
            attachments_to_remove = [
                attachment
                for attachment in announcement.attachments
                if attachment.id in remove_ids
            ]
            for attachment in attachments_to_remove:
                _remove_announcement_attachment(attachment.file_path)
                db.session.delete(attachment)
            attachments_modified = True

        new_files = [
            storage
            for storage in (form.attachments.data or [])
            if storage and storage.filename
        ]

        for uploaded_file in new_files:
            saved = _save_announcement_file(uploaded_file)
            db.session.add(
                AnnouncementAttachment(
                    announcement=announcement,
                    file_path=saved["path"],
                    original_name=saved["name"],
                    mime_type=saved["mime_type"],
                )
            )
        if new_files:
            attachments_modified = True

        if attachments_modified:
            db.session.flush()
            announcement.sync_legacy_attachment_fields()

        db.session.commit()
        flash("Comunicado atualizado com sucesso.", "success")
        return redirect(url_for("announcements"))

    flash(
        "Não foi possível atualizar o comunicado. Verifique os dados informados.",
        "danger",
    )
    return redirect(url_for("announcements"))

@app.route("/announcements/<int:announcement_id>/delete", methods=["POST"])
@login_required
def delete_announcement(announcement_id: int):
    """Remove an existing announcement and its attachments."""

    if current_user.role != "admin":
        abort(403)

    announcement = (
        Announcement.query.options(joinedload(Announcement.attachments))
        .get_or_404(announcement_id)
    )

    attachment_paths = [
        attachment.file_path for attachment in announcement.attachments if attachment.file_path
    ]
    if announcement.attachment_path:
        attachment_paths.append(announcement.attachment_path)

    TaskNotification.query.filter_by(
        announcement_id=announcement.id
    ).delete(synchronize_session=False)
    db.session.delete(announcement)
    db.session.commit()

    _remove_announcement_attachments(attachment_paths)

    flash("Comunicado removido com sucesso.", "success")
    return redirect(url_for("announcements"))

@app.route("/announcements/<int:announcement_id>/read", methods=["POST"])
@login_required
def mark_announcement_read(announcement_id: int):
    """Mark the current user's notification for an announcement as read."""

    announcement = Announcement.query.get_or_404(announcement_id)

    notifications = TaskNotification.query.filter(
        TaskNotification.announcement_id == announcement.id,
        TaskNotification.user_id == current_user.id,
    ).all()

    now = datetime.utcnow()
    updated = 0
    already_read = False

    for notification in notifications:
        if notification.read_at:
            already_read = True
            continue
        notification.read_at = now
        updated += 1

    db.session.commit()

    read = bool(updated or already_read or not notifications)

    return jsonify({"status": "ok", "read": read})

@app.route("/diretoria/acordos", methods=["GET", "POST"])
@login_required
@meeting_only_access_check
def diretoria_acordos():
    """Render and manage Diretoria JP agreements linked to portal users."""

    if current_user.role != "admin" and not user_has_tag("Diretoria"):
        abort(403)

    ensure_diretoria_agreement_schema()

    users = (
        User.query.filter(User.ativo.is_(True))
        .order_by(sa.func.lower(User.name))
        .all()
    )

    form = DiretoriaAcordoForm()

    search_query = (request.args.get("q", default="", type=str) or "").strip()

    if request.method == "POST":
        selected_user_id = request.form.get("user_id", type=int)
    else:
        selected_user_id = request.args.get("user_id", type=int)

    selected_user = None
    agreements: list[DiretoriaAgreement] = []
    active_agreement: DiretoriaAgreement | None = None
    search_results: list[DiretoriaAgreement] = []

    if selected_user_id:
        selected_user = (
            User.query.filter(
                User.id == selected_user_id,
                User.ativo.is_(True),
            ).first()
        )
        if not selected_user:
            message = "Usuário selecionado não foi encontrado ou está inativo."
            if request.method == "GET":
                flash(message, "warning")
                return redirect(url_for("diretoria_acordos"))
            flash(message, "danger")
        else:
            agreements = (
                DiretoriaAgreement.query.filter_by(user_id=selected_user.id)
                .order_by(
                    DiretoriaAgreement.agreement_date.desc(),
                    DiretoriaAgreement.created_at.desc(),
                )
                .all()
            )
    agreement_entries: list[dict[str, Any]] = [
        {"record": agreement_item}
        for agreement_item in agreements
    ]

    search_entries: list[dict[str, Any]] = []
    if search_query:
        search_term = f"%{search_query}%"
        search_results = (
            DiretoriaAgreement.query.options(joinedload(DiretoriaAgreement.user))
            .join(User)
            .filter(
                User.ativo.is_(True),
                sa.or_(
                    DiretoriaAgreement.title.ilike(search_term),
                    DiretoriaAgreement.description.ilike(search_term),
                ),
            )
            .order_by(
                DiretoriaAgreement.agreement_date.desc(),
                DiretoriaAgreement.created_at.desc(),
            )
            .all()
        )

    for agreement_item in search_results:
        search_entries.append(
            {
                "record": agreement_item,
                "user": agreement_item.user,
            }
        )

    if request.method == "POST":
        form_mode = request.form.get("form_mode") or "new"
        if form_mode not in {"new", "edit"}:
            form_mode = "new"
        agreement_id = request.form.get("agreement_id", type=int)
    else:
        form_mode = request.args.get("action")
        if form_mode not in {"new", "edit"}:
            form_mode = None
        agreement_id = request.args.get("agreement_id", type=int)

    if request.method == "GET" and selected_user:
        if form_mode == "edit" and agreement_id:
            active_agreement = next(
                (item for item in agreements if item.id == agreement_id), None
            )
            if not active_agreement:
                active_agreement = DiretoriaAgreement.query.filter_by(
                    id=agreement_id,
                    user_id=selected_user.id,
                ).first()
            if not active_agreement:
                flash("O acordo selecionado não foi encontrado.", "warning")
                return redirect(
                    url_for("diretoria_acordos", user_id=selected_user.id)
                )
            form.title.data = active_agreement.title
            form.agreement_date.data = active_agreement.agreement_date
            form.description.data = active_agreement.description
            form.notify_user.data = False
            form.notification_destination.data = "user"
            form.notification_email.data = ""
        elif form_mode == "new" and not form.agreement_date.data:
            form.agreement_date.data = date.today()
            form.notify_user.data = False
            form.notification_destination.data = "user"
            form.notification_email.data = ""

    if request.method == "POST":
        if not selected_user:
            if not selected_user_id:
                flash("Selecione um usuário para salvar o acordo.", "danger")
        else:
            if form_mode == "edit" and agreement_id:
                active_agreement = DiretoriaAgreement.query.filter_by(
                    id=agreement_id,
                    user_id=selected_user.id,
                ).first()
                if not active_agreement:
                    flash("O acordo selecionado não foi encontrado.", "warning")
                    return redirect(
                        url_for("diretoria_acordos", user_id=selected_user.id)
                    )

            if form.validate_on_submit():
                cleaned_description = sanitize_html(form.description.data or "")

                if form_mode == "edit" and active_agreement:
                    active_agreement.title = form.title.data
                    active_agreement.agreement_date = form.agreement_date.data
                    active_agreement.description = cleaned_description
                    feedback_message = "Acordo atualizado com sucesso."
                else:
                    active_agreement = DiretoriaAgreement(
                        user=selected_user,
                        title=form.title.data,
                        agreement_date=form.agreement_date.data,
                        description=cleaned_description,
                    )
                    db.session.add(active_agreement)
                    feedback_message = "Acordo criado com sucesso."

                db.session.commit()

                recipient_email = None
                destination_value = form.notification_destination.data or "user"
                if form.notify_user.data:
                    if destination_value == "custom":
                        recipient_email = form.notification_email.data
                    else:
                        recipient_email = selected_user.email

                if form.notify_user.data and recipient_email:
                    try:
                        action_label = (
                            "atualizado" if form_mode == "edit" else "registrado"
                        )
                        email_html = render_template(
                            "emails/diretoria_acordo.html",
                            agreement=active_agreement,
                            destinatario=selected_user,
                            editor=current_user,
                            action_label=action_label,
                        )
                        send_email(
                            subject=f"[Diretoria JP] Acordo {active_agreement.title}",
                            html_body=email_html,
                            recipients=[recipient_email],
                        )
                        flash(
                            f"{feedback_message} Notificação enviada por e-mail.",
                            "success",
                        )
                    except EmailDeliveryError as exc:
                        current_app.logger.error(
                            "Falha ao enviar e-mail do acordo %s para %s: %s",
                            active_agreement.id,
                            recipient_email,
                            exc,
                        )
                        flash(
                            f"{feedback_message} Porém, não foi possível enviar o e-mail de notificação.",
                            "warning",
                        )
                else:
                    flash(feedback_message, "success")

                return redirect(url_for("diretoria_acordos", user_id=selected_user.id))

            flash("Por favor, corrija os erros do formulário.", "danger")

    return render_template(
        "diretoria/acordos.html",
        users=users,
        form=form,
        selected_user=selected_user,
        agreements=agreements,
        form_mode=form_mode,
        active_agreement=active_agreement,
        agreement_entries=agreement_entries,
        search_query=search_query,
        search_entries=search_entries,
    )

@app.route("/diretoria/acordos/<int:agreement_id>/excluir", methods=["POST"])
@login_required
def diretoria_acordos_excluir(agreement_id: int):
    """Remove an agreement linked to a Diretoria JP user."""

    if current_user.role != "admin" and not (user_has_tag("Gestão") and user_has_tag("Diretoria")):
        abort(403)

    agreement = DiretoriaAgreement.query.get_or_404(agreement_id)

    redirect_user_id = request.form.get("user_id", type=int) or agreement.user_id
    if redirect_user_id != agreement.user_id:
        redirect_user_id = agreement.user_id

    db.session.delete(agreement)
    db.session.commit()

    flash("Acordo removido com sucesso.", "success")

    return redirect(url_for("diretoria_acordos", user_id=redirect_user_id))

@app.route("/diretoria/feedbacks", methods=["GET", "POST"])
@login_required
@meeting_only_access_check
def diretoria_feedbacks():
    """Render and manage Diretoria JP feedbacks linked to portal users."""

    if current_user.role != "admin" and not user_has_tag("Diretoria"):
        abort(403)

    users = (
        User.query.filter(User.ativo.is_(True))
        .order_by(sa.func.lower(User.name))
        .all()
    )

    form = DiretoriaFeedbackForm()

    search_query = (request.args.get("q", default="", type=str) or "").strip()

    if request.method == "POST":
        selected_user_id = request.form.get("user_id", type=int)
    else:
        selected_user_id = request.args.get("user_id", type=int)

    selected_user = None
    feedbacks: list[DiretoriaFeedback] = []
    active_feedback: DiretoriaFeedback | None = None
    search_results: list[DiretoriaFeedback] = []

    if selected_user_id:
        selected_user = (
            User.query.filter(
                User.id == selected_user_id,
                User.ativo.is_(True),
            ).first()
        )
        if not selected_user:
            message = "Usuário selecionado não foi encontrado ou está inativo."
            if request.method == "GET":
                flash(message, "warning")
                return redirect(url_for("diretoria_feedbacks"))
            flash(message, "danger")
        else:
            feedbacks = (
                DiretoriaFeedback.query.filter_by(user_id=selected_user.id)
                .order_by(
                    DiretoriaFeedback.feedback_date.desc(),
                    DiretoriaFeedback.created_at.desc(),
                )
                .all()
            )
    feedback_entries: list[dict[str, Any]] = [
        {"record": feedback_item}
        for feedback_item in feedbacks
    ]

    search_entries: list[dict[str, Any]] = []
    if search_query:
        search_term = f"%{search_query}%"
        search_results = (
            DiretoriaFeedback.query.options(joinedload(DiretoriaFeedback.user))
            .join(User)
            .filter(
                User.ativo.is_(True),
                sa.or_(
                    DiretoriaFeedback.title.ilike(search_term),
                    DiretoriaFeedback.description.ilike(search_term),
                ),
            )
            .order_by(
                DiretoriaFeedback.feedback_date.desc(),
                DiretoriaFeedback.created_at.desc(),
            )
            .all()
        )

    for feedback_item in search_results:
        search_entries.append(
            {
                "record": feedback_item,
                "user": feedback_item.user,
            }
        )

    if request.method == "POST":
        form_mode = request.form.get("form_mode") or "new"
        if form_mode not in {"new", "edit"}:
            form_mode = "new"
        feedback_id = request.form.get("feedback_id", type=int)
    else:
        form_mode = request.args.get("action")
        if form_mode not in {"new", "edit"}:
            form_mode = None
        feedback_id = request.args.get("feedback_id", type=int)

    if request.method == "GET" and selected_user:
        if form_mode == "edit" and feedback_id:
            active_feedback = next(
                (item for item in feedbacks if item.id == feedback_id), None
            )
            if not active_feedback:
                active_feedback = DiretoriaFeedback.query.filter_by(
                    id=feedback_id,
                    user_id=selected_user.id,
                ).first()
            if not active_feedback:
                flash("O feedback selecionado não foi encontrado.", "warning")
                return redirect(
                    url_for("diretoria_feedbacks", user_id=selected_user.id)
                )
            form.title.data = active_feedback.title
            form.feedback_date.data = active_feedback.feedback_date
            form.description.data = active_feedback.description
            form.notify_user.data = False
            form.notification_destination.data = "user"
            form.notification_email.data = ""
        elif form_mode == "new" and not form.feedback_date.data:
            form.feedback_date.data = date.today()
            form.notify_user.data = False
            form.notification_destination.data = "user"
            form.notification_email.data = ""

    if request.method == "POST":
        if not selected_user:
            if not selected_user_id:
                flash("Selecione um usuário para salvar o feedback.", "danger")
        else:
            if form_mode == "edit" and feedback_id:
                active_feedback = DiretoriaFeedback.query.filter_by(
                    id=feedback_id,
                    user_id=selected_user.id,
                ).first()
                if not active_feedback:
                    flash("O feedback selecionado não foi encontrado.", "warning")
                    return redirect(
                        url_for("diretoria_feedbacks", user_id=selected_user.id)
                    )

            if form.validate_on_submit():
                cleaned_description = sanitize_html(form.description.data or "")

                if form_mode == "edit" and active_feedback:
                    active_feedback.title = form.title.data
                    active_feedback.feedback_date = form.feedback_date.data
                    active_feedback.description = cleaned_description
                    feedback_message = "Feedback atualizado com sucesso."
                else:
                    active_feedback = DiretoriaFeedback(
                        user=selected_user,
                        title=form.title.data,
                        feedback_date=form.feedback_date.data,
                        description=cleaned_description,
                    )
                    db.session.add(active_feedback)
                    feedback_message = "Feedback criado com sucesso."

                db.session.commit()

                recipient_email = None
                destination_value = form.notification_destination.data or "user"
                if form.notify_user.data:
                    if destination_value == "custom":
                        recipient_email = form.notification_email.data
                    else:
                        recipient_email = selected_user.email

                if form.notify_user.data and recipient_email:
                    try:
                        action_label = (
                            "atualizado" if form_mode == "edit" else "registrado"
                        )
                        email_html = render_template(
                            "emails/diretoria_feedback.html",
                            feedback=active_feedback,
                            destinatario=selected_user,
                            editor=current_user,
                            action_label=action_label,
                        )
                        send_email(
                            subject=f"[Diretoria JP] Feedback {active_feedback.title}",
                            html_body=email_html,
                            recipients=[recipient_email],
                        )
                        flash(
                            f"{feedback_message} Notificação enviada por e-mail.",
                            "success",
                        )
                    except EmailDeliveryError as exc:
                        current_app.logger.error(
                            "Falha ao enviar e-mail do feedback %s para %s: %s",
                            active_feedback.id,
                            recipient_email,
                            exc,
                        )
                        flash(
                            f"{feedback_message} Porém, não foi possível enviar o e-mail de notificação.",
                            "warning",
                        )
                else:
                    flash(feedback_message, "success")

                return redirect(url_for("diretoria_feedbacks", user_id=selected_user.id))

            flash("Por favor, corrija os erros do formulário.", "danger")

    return render_template(
        "diretoria/feedbacks.html",
        users=users,
        form=form,
        selected_user=selected_user,
        feedbacks=feedbacks,
        form_mode=form_mode,
        active_feedback=active_feedback,
        feedback_entries=feedback_entries,
        search_query=search_query,
        search_entries=search_entries,
    )

@app.route("/diretoria/feedbacks/<int:feedback_id>/excluir", methods=["POST"])
@login_required
def diretoria_feedbacks_excluir(feedback_id: int):
    """Remove a feedback linked to a Diretoria JP user."""

    if current_user.role != "admin" and not user_has_tag("Diretoria"):
        abort(403)

    feedback = DiretoriaFeedback.query.get_or_404(feedback_id)

    redirect_user_id = request.form.get("user_id", type=int) or feedback.user_id
    if redirect_user_id != feedback.user_id:
        redirect_user_id = feedback.user_id

    db.session.delete(feedback)
    db.session.commit()

    flash("Feedback removido com sucesso.", "success")

    return redirect(url_for("diretoria_feedbacks", user_id=redirect_user_id))

@app.route("/diretoria/eventos", methods=["GET", "POST"])
@login_required
@meeting_only_access_check
def diretoria_eventos():
    """Render or persist Diretoria JP event planning data."""

    if current_user.role != "admin" and not (user_has_tag("Gestão") or user_has_tag("Diretoria")):
        abort(403)

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        normalized, errors = parse_diretoria_event_payload(payload)

        if errors:
            return jsonify({"errors": errors}), 400

        diretoria_event = DiretoriaEvent(
            **normalized,
            created_by=current_user,
        )

        db.session.add(diretoria_event)
        db.session.commit()

        session["diretoria_event_feedback"] = {
            "message": f'Evento "{diretoria_event.name}" salvo com sucesso.',
            "category": "success",
        }

        return (
            jsonify(
                {
                    "success": True,
                    "redirect_url": url_for("diretoria_eventos_lista"),
                }
            ),
            201,
        )

    return render_template("diretoria/eventos.html")

@app.route("/diretoria/eventos/<int:event_id>/editar", methods=["GET", "POST"])
@login_required
def diretoria_eventos_editar(event_id: int):
    """Edit an existing Diretoria JP event."""

    if current_user.role != "admin" and not (user_has_tag("Gestão") or user_has_tag("Diretoria")):
        abort(403)

    event = DiretoriaEvent.query.get_or_404(event_id)

    if request.method == "POST":
        previous_photos: list[str] = []
        if isinstance(event.photos, list):
            for photo in event.photos:
                normalized_prev = _normalize_photo_entry(photo)
                if normalized_prev:
                    previous_photos.append(normalized_prev)

        payload = request.get_json(silent=True) or {}
        normalized, errors = parse_diretoria_event_payload(payload)

        if errors:
            return jsonify({"errors": errors}), 400

        if not normalized.get("event_date"):
            return jsonify({"errors": ["Informe uma data válida para o evento."]}), 400

        event.name = normalized["name"]
        event.event_type = normalized["event_type"]
        event.event_date = normalized["event_date"]
        event.description = normalized["description"]
        event.audience = normalized["audience"]
        event.participants = normalized["participants"]
        event.services = normalized["services"]
        event.total_cost = normalized["total_cost"]
        event.photos = normalized["photos"]

        db.session.commit()

        removed_photos = [
            photo for photo in previous_photos if photo not in event.photos
        ]
        _cleanup_diretoria_photo_uploads(removed_photos, exclude_event_id=event.id)

        session["diretoria_event_feedback"] = {
            "message": f'Evento "{event.name}" atualizado com sucesso.',
            "category": "success",
        }

        return jsonify(
            {
                "success": True,
                "redirect_url": url_for("diretoria_eventos_lista"),
            }
        )

    sanitized_photos = []
    if isinstance(event.photos, list):
        for photo in event.photos:
            normalized = _normalize_photo_entry(photo)
            if normalized:
                sanitized_photos.append(normalized)

    event_payload = {
        "id": event.id,
        "name": event.name,
        "type": event.event_type,
        "date": event.event_date.strftime("%Y-%m-%d"),
        "description": event.description or "",
        "audience": event.audience,
        "participants": event.participants,
        "categories": event.services or {},
        "photos": sanitized_photos,
        "submit_url": url_for("diretoria_eventos_editar", event_id=event.id),
    }

    return render_template("diretoria/eventos.html", event_data=event_payload)

@app.route("/diretoria/eventos/<int:event_id>/visualizar")
@login_required
def diretoria_eventos_visualizar(event_id: int):
    """Display the details of a Diretoria JP event without editing it."""

    if current_user.role != "admin" and not (user_has_tag("Gestão") or user_has_tag("Diretoria")):
        abort(403)

    event = DiretoriaEvent.query.options(joinedload(DiretoriaEvent.created_by)).get_or_404(
        event_id
    )

    services = event.services if isinstance(event.services, dict) else {}

    def to_decimal(value: Any) -> Decimal | None:
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError):
            return None

    categories: list[dict[str, Any]] = []
    for key, label in EVENT_CATEGORY_LABELS.items():
        raw_category = services.get(key, {}) if isinstance(services, dict) else {}
        raw_items = raw_category.get("items", []) if isinstance(raw_category, dict) else []
        category_total = to_decimal(raw_category.get("total") if isinstance(raw_category, dict) else None)

        processed_items: list[dict[str, Any]] = []
        if isinstance(raw_items, list):
            for item in raw_items:
                if not isinstance(item, dict):
                    continue

                item_name = (item.get("name") or "").strip()
                if not item_name:
                    continue

                processed_items.append(
                    {
                        "name": item_name,
                        "quantity": to_decimal(item.get("quantity")),
                        "unit_value": to_decimal(item.get("unit_value")),
                        "total": to_decimal(item.get("total")),
                    }
                )

        categories.append(
            {
                "key": key,
                "label": label,
                "entries": processed_items,
                "total": category_total or Decimal("0.00"),
            }
        )

    photo_urls = []
    if isinstance(event.photos, list):
        for photo in event.photos:
            normalized = _normalize_photo_entry(photo)
            if normalized:
                photo_urls.append(normalized)

    return render_template(
        "diretoria/evento_visualizar.html",
        event=event,
        categories=categories,
        photos=photo_urls,
        event_type_labels=EVENT_TYPE_LABELS,
        audience_labels=EVENT_AUDIENCE_LABELS,
        updated_at_display=_format_event_timestamp(event.updated_at),
    )

@app.route("/diretoria/eventos/lista")
@login_required
def diretoria_eventos_lista():
    """Display saved Diretoria JP events with search support."""

    if current_user.role != "admin" and not (user_has_tag("Gestão") or user_has_tag("Diretoria")):
        abort(403)

    search_query = (request.args.get("q") or "").strip()

    events_query = DiretoriaEvent.query
    if search_query:
        events_query = events_query.filter(
            DiretoriaEvent.name.ilike(f"%{search_query}%")
        )

    events = (
        events_query.order_by(
            DiretoriaEvent.event_date.desc(), DiretoriaEvent.created_at.desc()
        ).all()
    )

    feedback = session.pop("diretoria_event_feedback", None)
    if isinstance(feedback, dict) and feedback.get("message"):
        flash(feedback["message"], feedback.get("category", "success"))

    return render_template(
        "diretoria/eventos_lista.html",
        events=events,
        search_query=search_query,
        event_type_labels=EVENT_TYPE_LABELS,
        audience_labels=EVENT_AUDIENCE_LABELS,
        category_labels=EVENT_CATEGORY_LABELS,
    )

@app.route("/diretoria/eventos/<int:event_id>/excluir", methods=["POST"])
@login_required
def diretoria_eventos_excluir(event_id: int):
    """Remove a Diretoria JP event."""

    if current_user.role != "admin" and not (user_has_tag("Gestão") or user_has_tag("Diretoria")):
        abort(403)

    event = DiretoriaEvent.query.get_or_404(event_id)
    event_name = event.name
    event_photos: list[str] = []
    if isinstance(event.photos, list):
        for photo in event.photos:
            normalized_photo = _normalize_photo_entry(photo)
            if normalized_photo:
                event_photos.append(normalized_photo)

    db.session.delete(event)
    db.session.commit()

    _cleanup_diretoria_photo_uploads(event_photos)

    flash(f'Evento "{event_name}" removido com sucesso.', "success")

    return redirect(url_for("diretoria_eventos_lista"))

@app.route("/cursos", methods=["GET", "POST"])
@login_required
@meeting_only_access_check
def cursos():
    """Display the curated catalog of internal courses."""

    form = CourseForm()
    tag_form = CourseTagForm(prefix="tag")
    can_manage_courses = current_user.role == "admin"

    # Usar Tags de usuários no campo "Setores Participantes"
    sector_choices = [
        (tag.id, tag.nome)
        for tag in Tag.query.order_by(Tag.nome.asc()).all()
    ]
    participant_choices = [
        (user.id, user.name)
        for user in User.query.filter_by(ativo=True).order_by(User.name.asc()).all()
    ]
    form.sectors.choices = sector_choices
    form.participants.choices = participant_choices
    course_tags = CourseTag.query.order_by(CourseTag.name.asc()).all()
    tag_choices = [(tag.id, tag.name) for tag in course_tags]
    form.tags.choices = tag_choices

    sector_lookup = {value: label for value, label in sector_choices}
    participant_lookup = {value: label for value, label in participant_choices}
    tag_lookup = {tag.id: tag for tag in course_tags}

    # Criar mapeamento de usuários para suas tags (IDs)
    users_with_tags = User.query.filter_by(ativo=True).options(db.joinedload(User.tags)).all()
    user_tags_map = {
        user.id: [tag.id for tag in user.tags]
        for user in users_with_tags
    }

    course_id_raw = (form.course_id.data or "").strip()
    is_tag_submission = request.method == "POST" and "tag-submit" in request.form

    if is_tag_submission:
        if not can_manage_courses:
            flash("Apenas administradores podem gerenciar as tags de cursos.", "danger")
            return redirect(url_for("cursos"))

        if tag_form.validate_on_submit():
            tag_name = tag_form.name.data or ""
            existing_tag = db.session.execute(
                sa.select(CourseTag).where(sa.func.lower(CourseTag.name) == sa.func.lower(tag_name))
            ).scalar_one_or_none()
            if existing_tag:
                tag_form.name.errors.append("Já existe uma tag de curso com esse nome.")
                flash("Já existe uma tag de curso com esse nome.", "warning")
            else:
                new_tag = CourseTag(name=tag_name)
                db.session.add(new_tag)
                db.session.commit()
                flash("Tag de curso criada com sucesso!", "success")
                return redirect(url_for("cursos"))
        else:
            flash(
                "Não foi possível adicionar a tag. Verifique o nome informado e tente novamente.",
                "danger",
            )

    if not is_tag_submission:
        if request.method == "POST" and not can_manage_courses:
            flash("Apenas administradores podem cadastrar ou editar cursos.", "danger")
            return redirect(url_for("cursos"))

        if request.method == "POST" and form.submit_delete.data:
            if not course_id_raw:
                flash("Selecione um curso para excluir.", "danger")
                return redirect(url_for("cursos"))

            try:
                course_id = int(course_id_raw)
            except ValueError:
                flash("O curso selecionado não foi encontrado. Tente novamente.", "danger")
                return redirect(url_for("cursos"))

            existing_course_id = db.session.execute(
                sa.select(Course.id).where(Course.id == course_id)
            ).scalar_one_or_none()

            if existing_course_id is None:
                flash("O curso selecionado não foi encontrado. Tente novamente.", "danger")
                return redirect(url_for("cursos"))

            linked_meetings = Reuniao.query.filter_by(course_id=course_id).all()
            for meeting in linked_meetings:
                if not delete_meeting(meeting):
                    flash(
                        "Não foi possível remover a reunião vinculada no calendário. Tente novamente em alguns instantes.",
                        "danger",
                    )
                    return redirect(url_for("cursos"))

            db.session.execute(sa.delete(Course).where(Course.id == course_id))
            db.session.commit()
            if linked_meetings:
                flash(
                    "Curso e reuniões associadas excluídos com sucesso!",
                    "success",
                )
            else:
                flash("Curso excluído com sucesso!", "success")
            return redirect(url_for("cursos"))

        if form.validate_on_submit():
            course_id: int | None = None
            if course_id_raw:
                try:
                    course_id = int(course_id_raw)
                except ValueError:
                    course_id = None

            selected_sector_names = [
                sector_lookup[sector_id]
                for sector_id in form.sectors.data
                if sector_id in sector_lookup
            ]
            selected_participant_names = [
                participant_lookup[user_id]
                for user_id in form.participants.data
                if user_id in participant_lookup
            ]
            selected_tags = [
                tag_lookup[tag_id]
                for tag_id in form.tags.data
                if tag_id in tag_lookup
            ]

            # Validação: verificar se setores e participantes não estão vazios
            if not selected_sector_names:
                flash("Selecione ao menos um setor válido para o curso.", "danger")
                return redirect(url_for("cursos"))

            if not selected_participant_names:
                flash("Selecione ao menos um participante válido para o curso.", "danger")
                return redirect(url_for("cursos"))

            should_add_to_calendar = bool(form.submit_add_to_calendar.data)
            meeting_query_params: dict[str, Any] = {}
            if should_add_to_calendar:
                meeting_query_params = {"course_calendar": "1"}
                name_value = (form.name.data or "").strip()
                if name_value:
                    meeting_query_params["subject"] = name_value
                observation_value = (form.observation.data or "").strip()
                if observation_value:
                    meeting_query_params["description"] = observation_value
                if form.start_date.data:
                    meeting_query_params["date"] = form.start_date.data.isoformat()
                if form.schedule_start.data:
                    meeting_query_params["start"] = form.schedule_start.data.strftime("%H:%M")
                if form.schedule_end.data:
                    meeting_query_params["end"] = form.schedule_end.data.strftime("%H:%M")
                participant_ids = [str(user_id) for user_id in form.participants.data]
                if participant_ids:
                    meeting_query_params["participants"] = participant_ids

            success_message = ""
            if course_id is not None:
                course_obj = db.session.get(Course, course_id)

                if course_obj is None:
                    flash("O curso selecionado não foi encontrado. Tente novamente.", "danger")
                    return redirect(url_for("cursos"))

                course_obj.name = form.name.data.strip()
                course_obj.instructor = form.instructor.data.strip()
                course_obj.sectors = ", ".join(selected_sector_names)
                course_obj.participants = ", ".join(selected_participant_names)
                course_obj.workload = form.workload.data
                course_obj.start_date = form.start_date.data
                course_obj.schedule_start = form.schedule_start.data
                course_obj.schedule_end = form.schedule_end.data
                course_obj.completion_date = form.completion_date.data
                course_obj.status = form.status.data
                course_obj.observation = (form.observation.data or "").strip() or None
                course_obj.tags = selected_tags
                db.session.commit()
                success_message = "Curso atualizado com sucesso!"
            else:
                course = Course(
                    name=form.name.data.strip(),
                    instructor=form.instructor.data.strip(),
                    sectors=", ".join(selected_sector_names),
                    participants=", ".join(selected_participant_names),
                    workload=form.workload.data,
                    start_date=form.start_date.data,
                    schedule_start=form.schedule_start.data,
                    schedule_end=form.schedule_end.data,
                    completion_date=form.completion_date.data,
                    status=form.status.data,
                    observation=(form.observation.data or "").strip() or None,
                    tags=selected_tags,
                )
                db.session.add(course)
                db.session.commit()
                course_id = course.id
                success_message = "Curso cadastrado com sucesso!"
            if success_message:
                flash(success_message, "success")
            if (
                should_add_to_calendar
                and meeting_query_params.get("subject")
                and meeting_query_params.get("date")
            ):
                if course_id is not None:
                    meeting_query_params["course_id"] = str(course_id)
                return redirect(url_for("sala_reunioes", **meeting_query_params))
            return redirect(url_for("cursos"))

        elif request.method == "POST":
            flash(
                "Não foi possível salvar o curso. Verifique os campos destacados e tente novamente.",
                "danger",
            )

    courses = get_courses_overview()
    status_counts = Counter(course.status for course in courses)
    status_classes = {
        CourseStatus.COMPLETED: "status-pill--completed",
        CourseStatus.PLANNED: "status-pill--planned",
        CourseStatus.DELAYED: "status-pill--delayed",
        CourseStatus.POSTPONED: "status-pill--postponed",
        CourseStatus.CANCELLED: "status-pill--cancelled",
    }
    return render_template(
        "cursos.html",
        courses=courses,
        status_counts=status_counts,
        status_classes=status_classes,
        CourseStatus=CourseStatus,
        form=form,
        tag_form=tag_form,
        course_tags=course_tags,
        editing_course_id=course_id_raw,
        can_manage_courses=can_manage_courses,
        user_tags_map=user_tags_map,
    )


def _build_acessos_context(
    form: "AccessLinkForm | None" = None,
    *,
    open_modal: bool = False,
    page: int = 1,
):
    """Return template data for the access hub with alphabetical pagination."""

    per_page = 30
    columns_count = 3
    column_capacity = per_page // columns_count

    base_query = AccessLink.query.order_by(
        sa.func.lower(AccessLink.label), AccessLink.label
    )
    total_links = base_query.count()

    if total_links:
        total_pages = ceil(total_links / per_page)
        page = max(1, min(page, total_pages))
        paginated_links = (
            base_query.offset((page - 1) * per_page).limit(per_page).all()
        )
    else:
        total_pages = 1
        page = 1
        paginated_links = []

    columns = [
        paginated_links[i * column_capacity : (i + 1) * column_capacity]
        for i in range(columns_count)
    ]
    while len(columns) < columns_count:
        columns.append([])

    pagination = {
        "current_page": page,
        "total_pages": total_pages,
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "pages": list(range(1, total_pages + 1)),
        "per_page": per_page,
        "total_items": total_links,
    }

    return {
        "form": form,
        "open_modal": open_modal,
        "link_columns": columns,
        "pagination": pagination,
        "total_links": total_links,
        "per_page": per_page,
    }

@app.route("/acessos")
@login_required
@meeting_only_access_check
def acessos():
    """Display the hub with the available access categories and saved shortcuts."""

    modal_type = request.args.get("modal")
    open_modal = modal_type in ("novo", "editar")
    preselected_category = request.args.get("category")
    editing_link_id = request.args.get("link_id", type=int)

    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1

    form: AccessLinkForm | None = None
    editing_link = None

    if current_user.role == "admin":
        form = AccessLinkForm()
        form.category.choices = _access_category_choices()

        # Se está editando, preenche o formulário com os dados existentes
        if modal_type == "editar" and editing_link_id:
            editing_link = AccessLink.query.get_or_404(editing_link_id)
            form.category.data = editing_link.category
            form.label.data = editing_link.label
            form.url.data = editing_link.url
            form.description.data = editing_link.description
        # Se está criando novo e tem categoria pré-selecionada
        elif (
            preselected_category
            and preselected_category in ACESSOS_CATEGORIES
            and not form.category.data
        ):
            form.category.data = preselected_category

    context = _build_acessos_context(form=form, open_modal=open_modal, page=page)
    context["editing_link"] = editing_link
    context["modal_type"] = modal_type
    return render_template("acessos.html", **context)


def _access_category_choices() -> list[tuple[str, str]]:
    """Return available categories formatted for WTForms choice fields."""

    return [
        (slug, data["title"])
        for slug, data in ACESSOS_CATEGORIES.items()
    ]


def _handle_access_shortcut_submission(form: "AccessLinkForm"):
    """Persist a shortcut when valid or re-render the listing with errors."""

    if form.validate_on_submit():
        novo_link = AccessLink(
            category=form.category.data,
            label=form.label.data.strip(),
            url=form.url.data.strip(),
            description=(form.description.data or "").strip() or None,
            created_by=current_user,
        )
        db.session.add(novo_link)
        db.session.commit()
        flash("Novo atalho criado com sucesso!", "success")
        return redirect(url_for("acessos"))

    context = _build_acessos_context(form=form, open_modal=True)
    return render_template("acessos.html", **context)

@app.route("/acessos/novo", methods=["GET", "POST"])
@login_required
def acessos_novo():
    """Display and process the form to create a new shortcut for any category."""

    if current_user.role != "admin":
        abort(403)

    if request.method == "GET":
        return redirect(url_for("acessos", modal="novo"))

    form = AccessLinkForm()
    form.category.choices = _access_category_choices()
    return _handle_access_shortcut_submission(form)

@app.route("/acessos/<categoria_slug>")
@login_required
def acessos_categoria(categoria_slug: str):
    """Legacy endpoint kept for compatibility; redirects to the main listing."""

    if categoria_slug.lower() not in ACESSOS_CATEGORIES:
        abort(404)

    return redirect(url_for("acessos"))

@app.route("/acessos/<categoria_slug>/novo", methods=["GET", "POST"])
@login_required
def acessos_categoria_novo(categoria_slug: str):
    """Display and process the form to create a new shortcut within a category."""

    if current_user.role != "admin":
        abort(403)

    categoria_slug = categoria_slug.lower()
    categoria = ACESSOS_CATEGORIES.get(categoria_slug)
    if not categoria:
        abort(404)

    if request.method == "GET":
        return redirect(
            url_for("acessos", modal="novo", category=categoria_slug)
        )

    form = AccessLinkForm()
    form.category.choices = _access_category_choices()
    if not form.category.data:
        form.category.data = categoria_slug
    return _handle_access_shortcut_submission(form)

@app.route("/acessos/<int:link_id>/editar", methods=["GET", "POST"])
@login_required
def acessos_editar(link_id: int):
    """Edit an existing access shortcut."""

    if current_user.role != "admin":
        abort(403)

    link = AccessLink.query.get_or_404(link_id)

    if request.method == "GET":
        return redirect(url_for("acessos", modal="editar", link_id=link_id))

    form = AccessLinkForm()
    form.category.choices = _access_category_choices()

    if form.validate_on_submit():
        link.category = form.category.data
        link.label = form.label.data.strip()
        link.url = form.url.data.strip()
        link.description = (form.description.data or "").strip() or None
        db.session.commit()
        flash("Atalho atualizado com sucesso!", "success")
        return redirect(url_for("acessos"))

    context = _build_acessos_context(form=form, open_modal=True)
    context["editing_link"] = link
    return render_template("acessos.html", **context)

@app.route("/acessos/<int:link_id>/excluir", methods=["POST"])
@login_required
@csrf.exempt
def acessos_excluir(link_id: int):
    """Delete an existing access shortcut."""

    if current_user.role != "admin":
        abort(403)

    link = AccessLink.query.get_or_404(link_id)
    label = link.label
    db.session.delete(link)
    db.session.commit()
    flash(f'Atalho "{label}" excluído com sucesso!', "success")
    return redirect(url_for("acessos"))

@app.route("/procedimentos", methods=["GET", "POST"])
@login_required
@meeting_only_access_check
def procedimentos_operacionais():
    """Lista e permite criação de procedimentos operacionais."""

    form = OperationalProcedureForm()
    search_term = (request.args.get("q") or "").strip()

    query = OperationalProcedure.query
    if search_term:
        pattern = f"%{search_term}%"
        query = query.filter(
            sa.or_(
                OperationalProcedure.title.ilike(pattern),
                OperationalProcedure.descricao.ilike(pattern),
            )
        )

    procedures = query.order_by(OperationalProcedure.updated_at.desc()).all()

    if request.method == "POST":
        if form.validate_on_submit():
            proc = OperationalProcedure(
                title=form.title.data,
                descricao=sanitize_html(form.descricao.data or "") or None,
                created_by_id=current_user.id,
            )
            db.session.add(proc)
            db.session.commit()
            flash("Procedimento criado com sucesso.", "success")
            return redirect(url_for("procedimentos_operacionais"))
        flash("Não foi possível criar o procedimento. Corrija os erros do formulário.", "danger")

    return render_template(
        "procedimentos.html",
        form=form,
        procedures=procedures,
        search_term=search_term,
    )

@app.route("/procedimentos/<int:proc_id>")
@login_required
def procedimentos_operacionais_redirect(proc_id: int):
    """Compatibilidade: redireciona para a visualização dedicada."""
    return redirect(url_for("procedimentos_operacionais_ver", proc_id=proc_id))

@app.route("/procedimentos/<int:proc_id>/visualizar")
@login_required
def procedimentos_operacionais_ver(proc_id: int):
    """Exibe somente a visualização do procedimento."""
    proc = OperationalProcedure.query.get_or_404(proc_id)
    return render_template("procedimento_view.html", procedure=proc)

@app.route("/procedimentos/<int:proc_id>/json")
@login_required
def procedimentos_operacionais_json(proc_id: int):
    """Retorna dados do procedimento em JSON para modal."""
    proc = OperationalProcedure.query.get_or_404(proc_id)
    return jsonify({
        "id": proc.id,
        "title": proc.title,
        "descricao": proc.descricao or "",
        "updated_at": proc.updated_at.strftime('%d/%m/%Y às %H:%M') if proc.updated_at else None
    })

@app.route("/procedimentos/<int:proc_id>/editar", methods=["GET", "POST"])
@login_required
def procedimentos_operacionais_editar(proc_id: int):
    """Página de edição do procedimento (apenas admin)."""
    if current_user.role != "admin":
        abort(403)

    proc = OperationalProcedure.query.get_or_404(proc_id)
    form = OperationalProcedureForm()
    if request.method == "GET":
        form.title.data = proc.title
        form.descricao.data = proc.descricao or ""
        return render_template("procedimento_edit.html", procedure=proc, form=form)

    if form.validate_on_submit():
        proc.title = form.title.data
        proc.descricao = sanitize_html(form.descricao.data or "") or None
        db.session.commit()
        flash("Procedimento atualizado com sucesso.", "success")
        return redirect(url_for("procedimentos_operacionais_ver", proc_id=proc.id))

    flash("Não foi possível atualizar. Verifique os campos.", "danger")
    return redirect(url_for("procedimentos_operacionais_editar", proc_id=proc.id))

@app.route("/procedimentos/<int:proc_id>/excluir", methods=["POST"])
@login_required
def procedimentos_operacionais_excluir(proc_id: int):
    """Remove um procedimento (apenas admin)."""

    if current_user.role != "admin":
        abort(403)

    proc = OperationalProcedure.query.get_or_404(proc_id)
    db.session.delete(proc)
    db.session.commit()
    flash("Procedimento excluído com sucesso.", "success")
    return redirect(url_for("procedimentos_operacionais"))

@app.route("/procedimentos/search")
@login_required
def procedimentos_search():
    """Retorna procedimentos por título para menções (JSON)."""
    q = (request.args.get("q") or "").strip()
    query = OperationalProcedure.query
    if q:
        pattern = f"%{q}%"
        query = query.filter(OperationalProcedure.title.ilike(pattern))
    results = query.order_by(OperationalProcedure.updated_at.desc()).limit(10).all()
    return jsonify([
        {"id": p.id, "title": p.title, "url": url_for("procedimentos_operacionais_ver", proc_id=p.id)}
        for p in results
    ])

@app.route("/ping")
@limiter.exempt  # Health check endpoint - don't rate limit to avoid false positive errors
def ping():
    """Lightweight endpoint to keep the session active without hitting the ORM."""
    if "_user_id" not in session:
        return ("", 401)
    session.modified = True
    return ("", 204)


def _serialize_notification(notification: TaskNotification) -> dict[str, Any]:
    """Serialize a :class:`TaskNotification` into a JSON-friendly dict."""

    raw_type = notification.type or NotificationType.TASK.value
    try:
        notification_type = NotificationType(raw_type)
    except ValueError:
        notification_type = NotificationType.TASK

    message = (notification.message or "").strip() or None
    action_label = None
    target_url = None

    if notification_type is NotificationType.ANNOUNCEMENT:
        announcement = notification.announcement
        if announcement:
            if not message:
                subject = (announcement.subject or "").strip()
                if subject:
                    message = f"Novo comunicado: {subject}"
                else:
                    message = "Novo comunicado publicado."
            target_url = url_for("announcements") + f"#announcement-{announcement.id}"
        else:
            if not message:
                message = "Comunicado removido."
        action_label = "Abrir comunicado" if target_url else None
    else:
        task = notification.task
        if task:
            task_title = (task.title or "").strip()
            query_params: dict[str, object] = {"highlight_task": task.id}
            if notification_type is NotificationType.TASK_RESPONSE:
                query_params["open_responses"] = "1"
            if task.is_private:
                if current_user.is_authenticated and _user_can_access_task(task, current_user):
                    overview_endpoint = (
                        "tasks_overview" if current_user.role == "admin" else "tasks_overview_mine"
                    )
                    target_url = url_for(overview_endpoint, **query_params) + f"#task-{task.id}"
            else:
                target_url = url_for("tasks_sector", tag_id=task.tag_id, **query_params) + f"#task-{task.id}"
            if not message:
                prefix = (
                    "Tarefa atualizada"
                    if notification_type is NotificationType.TASK
                    else "Notificação"
                )
                if task_title:
                    message = f"{prefix}: {task_title}"
                else:
                    message = f"{prefix} atribuída a você."
        else:
            if not message:
                message = "Tarefa removida."
        if not action_label:
            if notification_type is NotificationType.TASK_RESPONSE:
                action_label = "Ver resposta" if target_url else None
            else:
                action_label = "Abrir tarefa" if target_url else None

    if not message:
        message = "Atualização disponível."

    created_at = notification.created_at or datetime.utcnow()
    if created_at.tzinfo is None:
        created_at_iso = created_at.isoformat() + "Z"
        display_dt = created_at.replace(tzinfo=timezone.utc).astimezone(SAO_PAULO_TZ)
    else:
        created_at_iso = created_at.isoformat()
        display_dt = created_at.astimezone(SAO_PAULO_TZ)

    return {
        "id": notification.id,
        "type": notification_type.value,
        "message": message,
        "created_at": created_at_iso,
        "created_at_display": display_dt.strftime("%d/%m/%Y %H:%M"),
        "is_read": notification.is_read,
        "url": target_url,
        "action_label": action_label,
    }


def _get_user_notification_items(limit: int | None = 20):
    """Return serialized notifications and unread totals for the current user."""

    notifications_query = (
        TaskNotification.query.filter(TaskNotification.user_id == current_user.id)
        .options(
            joinedload(TaskNotification.task).joinedload(Task.tag),
            joinedload(TaskNotification.announcement),
        )
        .order_by(TaskNotification.created_at.desc())
    )
    if limit is not None:
        notifications_query = notifications_query.limit(limit)
    notifications = notifications_query.all()
    unread_total = _get_unread_notifications_count(current_user.id)

    items = []
    for notification in notifications:
        raw_type = notification.type or NotificationType.TASK.value
        try:
            notification_type = NotificationType(raw_type)
        except ValueError:
            notification_type = NotificationType.TASK

        message = (notification.message or "").strip() or None
        action_label = None
        target_url = None

        items.append(_serialize_notification(notification))

    return items, unread_total


def _broadcast_announcement_notification(announcement: Announcement) -> None:
    """Emit a notification about ``announcement`` for every active user."""

    active_user_rows = (
        User.query.with_entities(User.id)
        .filter(User.ativo.is_(True))
        .all()
    )
    if not active_user_rows:
        return

    now = datetime.utcnow()
    subject = (announcement.subject or "").strip()
    if subject:
        base_message = f"Novo comunicado: {subject}"
    else:
        base_message = "Novo comunicado publicado."
    truncated_message = base_message[:255]

    notifications = [
        TaskNotification(
            user_id=user_id,
            announcement_id=announcement.id,
            task_id=None,
            type=NotificationType.ANNOUNCEMENT.value,
            message=truncated_message,
            created_at=now,
        )
        for (user_id,) in active_user_rows
    ]

    db.session.bulk_save_objects(notifications)
    _invalidate_notification_cache()

    # Broadcast notification to all affected users' SSE streams
    from app.services.realtime import get_broadcaster
    try:
        broadcaster = get_broadcaster()
        for user_id, in active_user_rows:
            broadcaster.broadcast(
                event_type="notification:created",
                data={"user_id": user_id, "announcement_id": announcement.id},
                user_id=user_id,
                scope="notifications",
            )
    except Exception:
        # Don't fail if broadcast fails
        pass

@app.route("/notifications", methods=["GET"])
@login_required
def list_notifications():
    """Return the most recent notifications for the user."""

    items, unread_total = _get_user_notification_items(limit=20)
    return jsonify({"notifications": items, "unread": unread_total})

@app.route("/notifications/stream")
@login_required
@limiter.exempt  # SSE connections remain open; exempt from standard rate limiting
def notifications_stream():
    """Server-Sent Events stream delivering real-time notifications."""
    from app.services.realtime import get_broadcaster

    since_id = request.args.get("since", type=int) or 0
    batch_limit = current_app.config.get("NOTIFICATIONS_STREAM_BATCH", 50)
    user_id = current_user.id

    # Query DB once to get the initial last_sent_id, then release connection
    if not since_id:
        last_existing = (
            TaskNotification.query.filter(TaskNotification.user_id == user_id)
            .order_by(TaskNotification.id.desc())
            .with_entities(TaskNotification.id)
            .limit(1)
            .scalar()
        )
        since_id = last_existing or 0

    # CRITICAL: Release database connection before entering streaming loop
    # This prevents connection pool exhaustion from long-running SSE connections
    db.session.remove()

    broadcaster = get_broadcaster()
    client_id = broadcaster.register_client(user_id, subscribed_scopes={"notifications", "all"})
    # Reduced heartbeat to 15s to prevent worker exhaustion (was 45s)
    heartbeat_interval = current_app.config.get("NOTIFICATIONS_HEARTBEAT_INTERVAL", 15)

    def event_stream() -> Any:
        last_sent_id = since_id

        try:
            while True:
                # Check for new notifications in the database
                # We create a new session for each check to avoid holding connections
                new_notifications = (
                    TaskNotification.query.filter(
                        TaskNotification.user_id == user_id,
                        TaskNotification.id > last_sent_id,
                    )
                    .options(
                        joinedload(TaskNotification.task).joinedload(Task.tag),
                        joinedload(TaskNotification.announcement),
                    )
                    .order_by(TaskNotification.id.asc())
                    .limit(batch_limit)
                    .all()
                )

                if new_notifications:
                    serialized = [
                        _serialize_notification(notification)
                        for notification in new_notifications
                    ]
                    last_sent_id = max(notification.id for notification in new_notifications)
                    # Use cache for unread count to reduce database queries
                    unread_total = _get_unread_notifications_count(user_id, allow_cache=True)
                    payload = json.dumps(
                        {
                            "notifications": serialized,
                            "unread": unread_total,
                            "last_id": last_sent_id,
                        }
                    )
                    # Release DB connection immediately after query
                    db.session.remove()
                    yield f"data: {payload}\n\n"
                else:
                    # No new notifications - release connection and send keep-alive
                    db.session.remove()
                    yield ": keep-alive\n\n"

                # Wait for broadcaster events or timeout
                # This doesn't hold a DB connection
                triggered = broadcaster.wait_for_events(
                    user_id,
                    client_id,
                    timeout=heartbeat_interval,
                )

                # Small sleep to avoid busy-looping even after broadcast
                if triggered:
                    time.sleep(0.5)  # Brief delay to batch notifications

        except GeneratorExit:
            broadcaster.unregister_client(user_id, client_id)
            db.session.remove()
            return
        finally:
            broadcaster.unregister_client(user_id, client_id)
            db.session.remove()

    response = Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"  # Disable nginx buffering
    return response

@app.route("/realtime/stream")
@login_required
@limiter.exempt  # SSE connections remain open; rate limiting causa reconexões agressivas
def realtime_stream():
    """Server-Sent Events stream for real-time system updates."""
    from app.services.realtime import get_broadcaster

    # Get subscribed scopes from query params (comma-separated)
    scopes_param = request.args.get("scopes", "all")
    subscribed_scopes = set(s.strip() for s in scopes_param.split(",") if s.strip())

    user_id = current_user.id

    # CRITICAL: Release database connection before entering streaming loop
    # This prevents connection pool exhaustion from long-running SSE connections
    db.session.remove()

    broadcaster = get_broadcaster()
    client_id = broadcaster.register_client(user_id, subscribed_scopes)
    # Reduced heartbeat to 10s to prevent worker exhaustion (was 30s)
    heartbeat_interval = current_app.config.get("REALTIME_HEARTBEAT_INTERVAL", 10)

    def event_stream() -> Any:
        try:
            last_event_id = 0
            while True:
                events = broadcaster.get_events(user_id, client_id, since_id=last_event_id)

                if events:
                    for event in events:
                        yield event.to_sse()
                        last_event_id = max(last_event_id, event.id)
                    continue

                triggered = broadcaster.wait_for_events(
                    user_id,
                    client_id,
                    timeout=heartbeat_interval,
                )
                if not triggered:
                    yield ": keep-alive\n\n"
        except GeneratorExit:
            broadcaster.unregister_client(user_id, client_id)
            db.session.remove()
            return
        finally:
            broadcaster.unregister_client(user_id, client_id)
            db.session.remove()

    response = Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"  # Disable nginx buffering
    return response

@app.route("/notificacoes")
@login_required
@meeting_only_access_check
def notifications_center():
    """Render the notification center page."""

    items, unread_total = _get_user_notification_items(limit=50)
    return render_template(
        "notifications.html",
        notifications=items,
        unread_total=unread_total,
    )

@app.route("/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def mark_notification_read(notification_id):
    """Mark a single notification as read."""

    notification = TaskNotification.query.filter(
        TaskNotification.id == notification_id,
        TaskNotification.user_id == current_user.id,
    ).first_or_404()
    if not notification.read_at:
        notification.read_at = datetime.utcnow()
        db.session.commit()
        _invalidate_notification_cache(current_user.id)
    return jsonify({"success": True})

@app.route("/notifications/read-all", methods=["POST"])
@login_required
def mark_all_notifications_read():
    """Mark all unread notifications for the current user as read."""

    updated = (
        TaskNotification.query.filter(
            TaskNotification.user_id == current_user.id,
            TaskNotification.read_at.is_(None),
        ).update(
            {TaskNotification.read_at: datetime.utcnow()},
            synchronize_session=False,
        )
    )
    db.session.commit()
    if updated:
        _invalidate_notification_cache(current_user.id)
    return jsonify({"success": True, "updated": updated or 0})


@app.route("/notifications/subscribe", methods=["POST"])
@login_required
def subscribe_push_notifications():
    """Subscribe to Web Push notifications."""
    from app.models.tables import PushSubscription

    data = request.get_json()
    if not data:
        return jsonify({"error": "Dados inválidos"}), 400

    endpoint = data.get("endpoint")
    keys = data.get("keys", {})
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")

    if not endpoint or not p256dh or not auth:
        return jsonify({"error": "Dados de subscrição incompletos"}), 400

    # Verificar se já existe uma subscrição para este endpoint
    existing = PushSubscription.query.filter_by(endpoint=endpoint).first()

    if existing:
        # Atualizar usuário se mudou e timestamp
        existing.user_id = current_user.id
        existing.p256dh_key = p256dh
        existing.auth_key = auth
        existing.user_agent = request.headers.get("User-Agent", "")[:500]
        existing.last_used_at = datetime.utcnow()
    else:
        # Criar nova subscrição
        subscription = PushSubscription(
            user_id=current_user.id,
            endpoint=endpoint,
            p256dh_key=p256dh,
            auth_key=auth,
            user_agent=request.headers.get("User-Agent", "")[:500],
        )
        db.session.add(subscription)

    try:
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/notifications/unsubscribe", methods=["POST"])
@login_required
def unsubscribe_push_notifications():
    """Unsubscribe from Web Push notifications."""
    from app.models.tables import PushSubscription

    data = request.get_json()
    if not data:
        return jsonify({"error": "Dados inválidos"}), 400

    endpoint = data.get("endpoint")
    if not endpoint:
        return jsonify({"error": "Endpoint não fornecido"}), 400

    # Remover subscrição
    PushSubscription.query.filter_by(
        endpoint=endpoint,
        user_id=current_user.id,
    ).delete()

    db.session.commit()
    return jsonify({"success": True})


@app.route("/notifications/vapid-public-key", methods=["GET"])
def get_vapid_public_key():
    """Return the VAPID public key for push subscription."""
    public_key = os.getenv("VAPID_PUBLIC_KEY", "")
    if not public_key:
        return jsonify({"error": "VAPID não configurado"}), 500
    return jsonify({"publicKey": public_key})


@app.route("/notifications/test-push", methods=["POST"])
@login_required
def test_push_notification():
    """Send a test push notification to the current user."""
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

@app.route("/consultorias", methods=["GET", "POST"])
@login_required
@meeting_only_access_check
def consultorias():
    """List registered consultorias and handle modal-based creation and edition."""

    consultoria_form = _configure_consultoria_form(ConsultoriaForm(prefix="consultoria"))
    consultorias = Consultoria.query.order_by(Consultoria.nome).all()
    open_consultoria_modal = request.args.get("open_consultoria_modal") in ("1", "true", "True")
    editing_consultoria: Consultoria | None = None

    if request.method == "GET":
        edit_id_raw = request.args.get("edit_consultoria_id")
        if edit_id_raw:
            try:
                edit_id = int(edit_id_raw)
            except (TypeError, ValueError):
                abort(404)
            editing_consultoria = Consultoria.query.get_or_404(edit_id)
            if current_user.role != "admin":
                abort(403)
            consultoria_form = _configure_consultoria_form(
                ConsultoriaForm(prefix="consultoria", obj=editing_consultoria)
            )
            open_consultoria_modal = True

    if request.method == "POST":
        form_name = request.form.get("form_name")
        if form_name == "consultoria_create":
            open_consultoria_modal = True
            if current_user.role != "admin":
                abort(403)
            if consultoria_form.validate_on_submit():
                nome = (consultoria_form.nome.data or "").strip()
                usuario = (consultoria_form.usuario.data or "").strip()
                senha = (consultoria_form.senha.data or "").strip()
                consultoria_form.nome.data = nome
                consultoria_form.usuario.data = usuario
                consultoria_form.senha.data = senha
                duplicate = (
                    Consultoria.query.filter(
                        db.func.lower(Consultoria.nome) == nome.lower()
                    ).first()
                    if nome
                    else None
                )
                if duplicate:
                    consultoria_form.nome.errors.append(
                        "Já existe uma consultoria com esse nome."
                    )
                    flash("Já existe uma consultoria com esse nome.", "warning")
                else:
                    consultoria = Consultoria(nome=nome, usuario=usuario, senha=senha)
                    db.session.add(consultoria)
                    db.session.commit()
                    flash("Consultoria registrada com sucesso.", "success")
                    return redirect(url_for("consultorias"))
        elif form_name == "consultoria_update":
            open_consultoria_modal = True
            if current_user.role != "admin":
                abort(403)
            consultoria_id_raw = request.form.get("consultoria_id")
            try:
                consultoria_id = int(consultoria_id_raw)
            except (TypeError, ValueError):
                abort(400)
            editing_consultoria = Consultoria.query.get_or_404(consultoria_id)
            if consultoria_form.validate_on_submit():
                nome = (consultoria_form.nome.data or "").strip()
                usuario = (consultoria_form.usuario.data or "").strip()
                senha = (consultoria_form.senha.data or "").strip()
                consultoria_form.nome.data = nome
                consultoria_form.usuario.data = usuario
                consultoria_form.senha.data = senha
                duplicate = (
                    Consultoria.query.filter(
                        db.func.lower(Consultoria.nome) == nome.lower(),
                        Consultoria.id != editing_consultoria.id,
                    ).first()
                    if nome
                    else None
                )
                if duplicate:
                    consultoria_form.nome.errors.append(
                        "Já existe uma consultoria com esse nome."
                    )
                    flash("Já existe uma consultoria com esse nome.", "warning")
                else:
                    editing_consultoria.nome = nome
                    editing_consultoria.usuario = usuario
                    editing_consultoria.senha = senha
                    db.session.commit()
                    flash("Consultoria atualizada com sucesso.", "success")
                    return redirect(url_for("consultorias"))

    return render_template(
        "consultorias.html",
        consultorias=consultorias,
        consultoria_form=consultoria_form,
        open_consultoria_modal=open_consultoria_modal,
        editing_consultoria=editing_consultoria,
    )

@app.route("/calendario-colaboradores", methods=["GET", "POST"])
@login_required
@meeting_only_access_check
def calendario_colaboradores():
    """Display and manage the internal collaborators calendar."""

    form = GeneralCalendarEventForm()
    populate_general_event_participants(form)
    can_manage = is_user_admin(current_user) or user_has_tag("Gestão")
    show_modal = False
    if form.validate_on_submit():
        if not can_manage:
            abort(403)
        event_id_raw = form.event_id.data
        if event_id_raw:
            try:
                event_id = int(event_id_raw)
            except (TypeError, ValueError):
                abort(400)
            event = GeneralCalendarEvent.query.get_or_404(event_id)
            if current_user.role != "admin" and event.created_by_id != current_user.id:
                flash("Você só pode editar eventos que você criou.", "danger")
                return redirect(url_for("calendario_colaboradores"))
            update_calendar_event_from_form(event, form)
        else:
            create_calendar_event_from_form(form, current_user.id)
        return redirect(url_for("calendario_colaboradores"))
    elif request.method == "POST":
        show_modal = True

    calendar_tz = get_calendar_timezone()
    return render_template(
        "calendario_colaboradores.html",
        form=form,
        can_manage=can_manage,
        show_modal=show_modal,
        calendar_timezone=calendar_tz.key,
    )

@app.route("/calendario-eventos/<int:event_id>/delete", methods=["POST"])
@login_required
def delete_calendario_evento(event_id):
    """Delete an event from the collaborators calendar."""

    event = GeneralCalendarEvent.query.get_or_404(event_id)
    can_manage = is_user_admin(current_user) or user_has_tag("Gestão")
    if not can_manage:
        abort(403)
    if not is_user_admin(current_user) and event.created_by_id != current_user.id:
        flash("Você só pode excluir eventos que você criou.", "danger")
        return redirect(url_for("calendario_colaboradores"))
    delete_calendar_event(event)
    flash("Evento removido com sucesso!", "success")
    return redirect(url_for("calendario_colaboradores"))


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

@app.route("/consultorias/cadastro", methods=["GET", "POST"])
@admin_required
def cadastro_consultoria():
    """Preserve legacy route by redirecting to the modal experience."""

    if request.method == "POST":
        form = ConsultoriaForm()
        if form.validate_on_submit():
            consultoria = Consultoria(
                nome=(form.nome.data or "").strip(),
                usuario=(form.usuario.data or "").strip(),
                senha=(form.senha.data or "").strip(),
            )
            db.session.add(consultoria)
            db.session.commit()
            flash("Consultoria registrada com sucesso.", "success")
            return redirect(url_for("consultorias"))
        for errors in form.errors.values():
            for error in errors:
                flash(error, "warning")
    return redirect(url_for("consultorias", open_consultoria_modal="1"))

@app.route("/consultorias/editar/<int:id>", methods=["GET", "POST"])
@admin_required
def editar_consultoria_cadastro(id):
    """Maintain legacy edit endpoint by redirecting into the modal flow."""

    consultoria = Consultoria.query.get_or_404(id)
    if request.method == "POST":
        form = ConsultoriaForm()
        if form.validate_on_submit():
            nome = (form.nome.data or "").strip()
            usuario = (form.usuario.data or "").strip()
            senha = (form.senha.data or "").strip()
            duplicate = (
                Consultoria.query.filter(
                    db.func.lower(Consultoria.nome) == nome.lower(),
                    Consultoria.id != consultoria.id,
                ).first()
                if nome
                else None
            )
            if duplicate:
                flash("Já existe uma consultoria com esse nome.", "warning")
            else:
                consultoria.nome = nome
                consultoria.usuario = usuario
                consultoria.senha = senha
                db.session.commit()
                flash("Consultoria atualizada com sucesso.", "success")
                return redirect(url_for("consultorias"))
        for errors in form.errors.values():
            for error in errors:
                flash(error, "warning")
    return redirect(
        url_for(
            "consultorias",
            open_consultoria_modal="1",
            edit_consultoria_id=str(consultoria.id),
        )
    )

@app.route("/consultorias/setores", methods=["GET", "POST"])
@login_required
def setores():
    """List registered setores and handle modal-based creation and edition."""

    setor_form = SetorForm(prefix="setor")
    setor_form.submit.label.text = "Salvar"
    setores = Setor.query.order_by(Setor.nome).all()
    open_setor_modal = request.args.get("open_setor_modal") in ("1", "true", "True")
    editing_setor: Setor | None = None

    if request.method == "GET":
        edit_id_raw = request.args.get("edit_setor_id")
        if edit_id_raw:
            try:
                edit_id = int(edit_id_raw)
            except (TypeError, ValueError):
                abort(404)
            editing_setor = Setor.query.get_or_404(edit_id)
            if current_user.role != "admin":
                abort(403)
            setor_form = SetorForm(prefix="setor", obj=editing_setor)
            setor_form.submit.label.text = "Salvar"
            open_setor_modal = True

    if request.method == "POST":
        form_name = request.form.get("form_name")
        if form_name == "setor_create":
            open_setor_modal = True
            if current_user.role != "admin":
                abort(403)
            if setor_form.validate_on_submit():
                nome = (setor_form.nome.data or "").strip()
                setor_form.nome.data = nome
                duplicate = (
                    Setor.query.filter(db.func.lower(Setor.nome) == nome.lower()).first()
                    if nome
                    else None
                )
                if duplicate:
                    setor_form.nome.errors.append("Já existe um setor com esse nome.")
                    flash("Já existe um setor com esse nome.", "warning")
                else:
                    setor = Setor(nome=nome)
                    db.session.add(setor)
                    db.session.commit()
                    flash("Setor registrado com sucesso.", "success")
                    return redirect(url_for("setores"))
        elif form_name == "setor_update":
            open_setor_modal = True
            if current_user.role != "admin":
                abort(403)
            setor_id_raw = request.form.get("setor_id")
            try:
                setor_id = int(setor_id_raw)
            except (TypeError, ValueError):
                abort(400)
            editing_setor = Setor.query.get_or_404(setor_id)
            if setor_form.validate_on_submit():
                nome = (setor_form.nome.data or "").strip()
                setor_form.nome.data = nome
                duplicate = (
                    Setor.query.filter(
                        db.func.lower(Setor.nome) == nome.lower(),
                        Setor.id != editing_setor.id,
                    ).first()
                    if nome
                    else None
                )
                if duplicate:
                    setor_form.nome.errors.append("Já existe um setor com esse nome.")
                    flash("Já existe um setor com esse nome.", "warning")
                else:
                    editing_setor.nome = nome
                    db.session.commit()
                    flash("Setor atualizado com sucesso.", "success")
                    return redirect(url_for("setores"))

    return render_template(
        "setores.html",
        setores=setores,
        setor_form=setor_form,
        open_setor_modal=open_setor_modal,
        editing_setor=editing_setor,
    )

@app.route("/consultorias/setores/cadastro", methods=["GET", "POST"])
@admin_required
def cadastro_setor():
    """Maintain legacy setor creation route by redirecting to the modal UI."""

    if request.method == "POST":
        form = SetorForm()
        if form.validate_on_submit():
            nome = (form.nome.data or "").strip()
            duplicate = (
                Setor.query.filter(db.func.lower(Setor.nome) == nome.lower()).first()
                if nome
                else None
            )
            if duplicate:
                flash("Já existe um setor com esse nome.", "warning")
            else:
                setor = Setor(nome=nome)
                db.session.add(setor)
                db.session.commit()
                flash("Setor registrado com sucesso.", "success")
                return redirect(url_for("setores"))
        for errors in form.errors.values():
            for error in errors:
                flash(error, "warning")
    return redirect(url_for("setores", open_setor_modal="1"))

@app.route("/consultorias/setores/editar/<int:id>", methods=["GET", "POST"])
@admin_required
def editar_setor(id):
    """Keep legacy setor edit endpoint by redirecting into the modal flow."""

    setor = Setor.query.get_or_404(id)
    if request.method == "POST":
        form = SetorForm()
        if form.validate_on_submit():
            nome = (form.nome.data or "").strip()
            duplicate = (
                Setor.query.filter(
                    db.func.lower(Setor.nome) == nome.lower(),
                    Setor.id != setor.id,
                ).first()
                if nome
                else None
            )
            if duplicate:
                flash("Já existe um setor com esse nome.", "warning")
            else:
                setor.nome = nome
                db.session.commit()
                flash("Setor atualizado com sucesso.", "success")
                return redirect(url_for("setores"))
        for errors in form.errors.values():
            for error in errors:
                flash(error, "warning")
    return redirect(
        url_for(
            "setores",
            open_setor_modal="1",
            edit_setor_id=str(setor.id),
        )
    )


# =============================================
# CONTROLE DE NOTAS ROUTES
# =============================================

@app.route("/controle-notas/debito", methods=["GET", "POST"])
@login_required
@meeting_only_access_check
def notas_debito():
    """List and manage Notas para Débito with modal-based CRUD."""

    if not can_access_controle_notas():
        abort(403)

    nota_form = NotaDebitoForm(prefix="nota")
    notas = NotaDebito.query.order_by(NotaDebito.data_emissao.desc()).all()

    # Buscar cadastros para autocomplete
    cadastros = CadastroNota.query.order_by(CadastroNota.cadastro).all()
    cadastros_json = json.dumps([{
        'nome': (c.cadastro or '').upper(),
        'valor': float(c.valor) if c.valor else 0,
        'acordo': (c.acordo or '').upper()
    } for c in cadastros])

    open_nota_modal = request.args.get("open_nota_modal") in ("1", "true", "True")
    editing_nota: NotaDebito | None = None

    if request.method == "GET":
        edit_id_raw = request.args.get("edit_nota_id")
        if edit_id_raw:
            try:
                edit_id = int(edit_id_raw)
            except (TypeError, ValueError):
                abort(404)
            editing_nota = NotaDebito.query.get_or_404(edit_id)
            nota_form = NotaDebitoForm(prefix="nota", obj=editing_nota)
            open_nota_modal = True

    if request.method == "POST":
        form_name = request.form.get("form_name")
        if form_name == "nota_create":
            open_nota_modal = True
            if nota_form.validate_on_submit():
                try:
                    valor_un = float(nota_form.valor_un.data.replace(',', '.')) if nota_form.valor_un.data else None
                    total = float(nota_form.total.data.replace(',', '.')) if nota_form.total.data else None
                    notas_int = int(nota_form.notas.data)
                    qtde_int = int(nota_form.qtde_itens.data)
                except (ValueError, AttributeError):
                    flash("Valores numéricos inválidos.", "warning")
                else:
                    nota = NotaDebito(
                        data_emissao=nota_form.data_emissao.data,
                        empresa=nota_form.empresa.data.strip().upper() if nota_form.empresa.data else '',
                        notas=notas_int,
                        qtde_itens=qtde_int,
                        valor_un=valor_un,
                        total=total,
                        acordo=nota_form.acordo.data.strip().upper() if nota_form.acordo.data else None,
                        forma_pagamento=(nota_form.forma_pagamento.data or '').upper(),
                        observacao=nota_form.observacao.data.strip() if nota_form.observacao.data else None
                    )
                    db.session.add(nota)
                    db.session.commit()
                    flash("Nota registrada com sucesso.", "success")
                    return redirect(url_for("notas_debito"))
        elif form_name == "nota_update":
            open_nota_modal = True
            nota_id_raw = request.form.get("nota_id")
            try:
                nota_id = int(nota_id_raw)
            except (TypeError, ValueError):
                abort(400)
            editing_nota = NotaDebito.query.get_or_404(nota_id)
            if nota_form.validate_on_submit():
                try:
                    valor_un = float(nota_form.valor_un.data.replace(',', '.')) if nota_form.valor_un.data else None
                    total = float(nota_form.total.data.replace(',', '.')) if nota_form.total.data else None
                    notas_int = int(nota_form.notas.data)
                    qtde_int = int(nota_form.qtde_itens.data)
                except (ValueError, AttributeError):
                    flash("Valores numéricos inválidos.", "warning")
                else:
                    editing_nota.data_emissao = nota_form.data_emissao.data
                    editing_nota.empresa = nota_form.empresa.data.strip().upper() if nota_form.empresa.data else ''
                    editing_nota.notas = notas_int
                    editing_nota.qtde_itens = qtde_int
                    editing_nota.valor_un = valor_un
                    editing_nota.total = total
                    editing_nota.acordo = nota_form.acordo.data.strip().upper() if nota_form.acordo.data else None
                    editing_nota.forma_pagamento = (nota_form.forma_pagamento.data or '').upper()
                    editing_nota.observacao = nota_form.observacao.data.strip() if nota_form.observacao.data else None
                    db.session.commit()
                    flash("Nota atualizada com sucesso.", "success")
                    return redirect(url_for("notas_debito"))
        elif form_name == "nota_delete":
            nota_id_raw = request.form.get("nota_id")
            try:
                nota_id = int(nota_id_raw)
            except (TypeError, ValueError):
                abort(400)
            nota = NotaDebito.query.get_or_404(nota_id)
            db.session.delete(nota)
            db.session.commit()
            flash("Nota excluída com sucesso.", "success")
            return redirect(url_for("notas_debito"))

    # Verificar se usuário pode ver forma de pagamento (apenas Gestão e Financeiro)
    pode_ver_forma_pagamento = is_user_admin(current_user) or user_has_tag('Gestão') or user_has_tag('Financeiro')

    return render_template(
        "notas_debito.html",
        notas=notas,
        nota_form=nota_form,
        open_nota_modal=open_nota_modal,
        editing_nota=editing_nota,
        cadastros=cadastros,
        cadastros_json=cadastros_json,
        pode_ver_forma_pagamento=pode_ver_forma_pagamento,
    )


@app.route("/controle-notas/debito/<int:nota_id>/forma-pagamento", methods=["POST"])
@login_required
@meeting_only_access_check
def notas_debito_update_forma_pagamento(nota_id: int):
    """Atualiza a forma de pagamento de uma nota via requisicao assincrona."""
    if not can_access_controle_notas():
        abort(403)

    pode_ver_forma_pagamento = is_user_admin(current_user) or user_has_tag('Gestǜo') or user_has_tag('Financeiro')
    if not pode_ver_forma_pagamento:
        abort(403)

    payload = request.get_json(silent=True) or {}
    raw_value = payload.get("forma_pagamento", "")
    if not isinstance(raw_value, str):
        raw_value = ""

    new_value = raw_value.strip().upper()
    valid_values = {(choice or "").upper() for choice, _ in PAGAMENTO_CHOICES}
    if new_value not in valid_values:
        return jsonify({"success": False, "message": "Forma de pagamento invalida."}), 400

    nota = NotaDebito.query.get_or_404(nota_id)
    nota.forma_pagamento = new_value
    db.session.commit()

    label_map = {(choice or "").upper(): label for choice, label in PAGAMENTO_CHOICES}
    return jsonify(
        {
            "success": True,
            "forma_pagamento": nota.forma_pagamento,
            "forma_pagamento_label": label_map.get(new_value, nota.forma_pagamento),
        }
    )


@app.route("/controle-notas/cadastro", methods=["GET", "POST"])
@login_required
@meeting_only_access_check
def cadastro_notas():
    """List and manage Cadastro de Notas with modal-based CRUD."""

    if not can_access_controle_notas():
        abort(403)

    cadastro_form = CadastroNotaForm(prefix="cadastro")
    cadastros = CadastroNota.query.order_by(CadastroNota.cadastro).all()
    open_cadastro_modal = request.args.get("open_cadastro_modal") in ("1", "true", "True")
    editing_cadastro: CadastroNota | None = None

    if request.method == "GET":
        edit_id_raw = request.args.get("edit_cadastro_id")
        if edit_id_raw:
            try:
                edit_id = int(edit_id_raw)
            except (TypeError, ValueError):
                abort(404)
            editing_cadastro = CadastroNota.query.get_or_404(edit_id)
            cadastro_form = CadastroNotaForm(prefix="cadastro", obj=editing_cadastro)
            open_cadastro_modal = True

    if request.method == "POST":
        form_name = request.form.get("form_name")
        if form_name == "cadastro_create":
            open_cadastro_modal = True
            if cadastro_form.validate_on_submit():
                try:
                    valor = float(cadastro_form.valor.data.replace(',', '.'))
                except (ValueError, AttributeError):
                    flash("Valor inválido.", "warning")
                else:
                    cadastro = CadastroNota(
                        pix="49991352070",
                        cadastro=cadastro_form.cadastro.data.strip().upper() if cadastro_form.cadastro.data else '',
                        valor=valor,
                        acordo=cadastro_form.acordo.data.strip().upper() if cadastro_form.acordo.data else None,
                        forma_pagamento=''
                    )
                    db.session.add(cadastro)
                    db.session.commit()
                    flash("Cadastro registrado com sucesso.", "success")
                    return redirect(url_for("cadastro_notas"))
        elif form_name == "cadastro_update":
            open_cadastro_modal = True
            cadastro_id_raw = request.form.get("cadastro_id")
            try:
                cadastro_id = int(cadastro_id_raw)
            except (TypeError, ValueError):
                abort(400)
            editing_cadastro = CadastroNota.query.get_or_404(cadastro_id)
            if cadastro_form.validate_on_submit():
                try:
                    valor = float(cadastro_form.valor.data.replace(',', '.'))
                except (ValueError, AttributeError):
                    flash("Valor inválido.", "warning")
                else:
                    editing_cadastro.pix = "49991352070"
                    editing_cadastro.cadastro = cadastro_form.cadastro.data.strip().upper() if cadastro_form.cadastro.data else ''
                    editing_cadastro.valor = valor
                    editing_cadastro.acordo = cadastro_form.acordo.data.strip().upper() if cadastro_form.acordo.data else None
                    db.session.commit()
                    flash("Cadastro atualizado com sucesso.", "success")
                    return redirect(url_for("cadastro_notas"))
        elif form_name == "cadastro_delete":
            cadastro_id_raw = request.form.get("cadastro_id")
            try:
                cadastro_id = int(cadastro_id_raw)
            except (TypeError, ValueError):
                abort(400)
            cadastro = CadastroNota.query.get_or_404(cadastro_id)
            db.session.delete(cadastro)
            db.session.commit()
            flash("Cadastro excluído com sucesso.", "success")
            return redirect(url_for("cadastro_notas"))

    # Verificar se usuário pode ver forma de pagamento (apenas Gestão e Financeiro)
    pode_ver_forma_pagamento = is_user_admin(current_user) or user_has_tag('Gestão') or user_has_tag('Financeiro')

    return render_template(
        "cadastro_notas.html",
        cadastros=cadastros,
        cadastro_form=cadastro_form,
        open_cadastro_modal=open_cadastro_modal,
        editing_cadastro=editing_cadastro,
        pode_ver_forma_pagamento=pode_ver_forma_pagamento,
    )


@app.route("/controle-notas/totalizador", methods=["GET"])
@login_required
@meeting_only_access_check
def notas_totalizador():
    """Display aggregated Nota Débito data with optional period filters."""

    if not can_access_controle_notas():
        abort(403)

    today = date.today()
    default_start = today.replace(day=1)
    default_end = today

    data_inicial_raw = (request.args.get("data_inicial") or "").strip()
    data_final_raw = (request.args.get("data_final") or "").strip()

    def _parse_date(value: str) -> date | None:
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    data_inicial = _parse_date(data_inicial_raw) or default_start
    data_final = _parse_date(data_final_raw) or default_end

    if data_inicial > data_final:
        flash("A data inicial não pode ser maior que a data final.", "warning")
        data_inicial, data_final = default_start, default_end

    base_query = NotaDebito.query.filter(
        NotaDebito.data_emissao >= data_inicial,
        NotaDebito.data_emissao <= data_final,
    )

    nota_form = NotaDebitoForm()
    pagamento_choices = nota_form.forma_pagamento.choices
    pagamento_label_map = {
        (choice or "").upper(): label for choice, label in pagamento_choices
    }
    pagamento_value_map = {
        (choice or "").upper(): (choice or "") for choice, _ in pagamento_choices
    }

    def format_currency(value: Decimal | None) -> str:
        number = value if isinstance(value, Decimal) else Decimal(value or 0)
        return (
            f"R$ {number:,.2f}"
            .replace(",", "_")
            .replace(".", ",")
            .replace("_", ".")
        )

    dados_totalizador: list[dict[str, object]] = []
    notas_por_empresa: dict[str, dict[str, object]] = {}

    notas_list = (
        base_query.order_by(
            sa.func.lower(NotaDebito.empresa),
            NotaDebito.data_emissao.desc(),
            NotaDebito.id.desc(),
        ).all()
    )

    total_registros = 0
    total_notas = 0
    total_itens = 0
    total_valor = Decimal("0")

    for nota in notas_list:
        empresa_key = (nota.empresa or "").strip().upper()
        if not empresa_key:
            empresa_key = "SEM EMPRESA"

        grupo = notas_por_empresa.setdefault(
            empresa_key,
            {
                "empresa": empresa_key,
                "qtd_registros": 0,
                "total_notas": 0,
                "total_itens": 0,
                "valor_total": Decimal("0"),
                "notas": [],
            },
        )

        valor_total = nota.total or Decimal("0")
        valor_un = nota.valor_un or Decimal("0")

        forma_pagamento_raw = (nota.forma_pagamento or "").strip()
        forma_pagamento_value = forma_pagamento_raw.upper()

        grupo["qtd_registros"] += 1
        grupo["total_notas"] += int(nota.notas or 0)
        grupo["total_itens"] += int(nota.qtde_itens or 0)
        grupo["valor_total"] += Decimal(valor_total)
        grupo["notas"].append(
            {
                "id": nota.id,
                "data_emissao": nota.data_emissao,
                "data_emissao_formatada": nota.data_emissao_formatada,
                "empresa": empresa_key,
                "notas": nota.notas,
                "qtde_itens": nota.qtde_itens,
                "valor_un": valor_un,
                "valor_un_formatado": nota.valor_un_formatado,
                "valor_total": valor_total,
                "valor_total_formatado": nota.total_formatado,
                "acordo": (nota.acordo or "").upper() if nota.acordo else "#N/A",
                "forma_pagamento": forma_pagamento_raw,
                "forma_pagamento_upper": forma_pagamento_value,
                "forma_pagamento_choice_value": pagamento_value_map.get(
                    forma_pagamento_value, forma_pagamento_raw
                ),
                "forma_pagamento_label": pagamento_label_map.get(
                    forma_pagamento_value, forma_pagamento_value
                ),
                "observacao": nota.observacao or "",
            }
        )

        total_registros += 1
        total_notas += int(nota.notas or 0)
        total_itens += int(nota.qtde_itens or 0)
        total_valor += Decimal(valor_total)

    def _sort_key_empresa(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value or "")
        return "".join(ch for ch in normalized if not unicodedata.combining(ch)).casefold()

    for empresa_key in sorted(notas_por_empresa.keys(), key=_sort_key_empresa):
        grupo = notas_por_empresa[empresa_key]
        grupo["valor_total_formatado"] = format_currency(grupo["valor_total"])
        dados_totalizador.append(grupo)

    resumo_geral = {
        "qtd_registros": total_registros,
        "total_notas": total_notas,
        "total_itens": total_itens,
        "valor_total": total_valor,
        "valor_total_formatado": format_currency(total_valor),
    }

    pode_ver_forma_pagamento = (
        is_user_admin(current_user)
        or user_has_tag("Gestão")
        or user_has_tag("Financeiro")
    )

    return render_template(
        "notas_totalizador.html",
        dados_totalizador=dados_totalizador,
        resumo_geral=resumo_geral,
        data_inicial=data_inicial.isoformat(),
        data_final=data_final.isoformat(),
        pagamento_choices=pagamento_choices,
        pode_ver_forma_pagamento=pode_ver_forma_pagamento,
    )


@app.route("/tags")
@login_required
def tags():
    """List registered tags."""
    tags = Tag.query.all()
    return render_template("tags.html", tags=tags)

@app.route("/tags/cadastro", methods=["GET", "POST"])
@admin_required
def cadastro_tag():
    """Render and handle the Cadastro de Tag page."""
    form = TagForm()
    if form.validate_on_submit():
        tag = Tag(nome=form.nome.data)
        db.session.add(tag)
        db.session.commit()
        flash("Tag registrada com sucesso.", "success")
        return redirect(url_for("tags"))
    return render_template("cadastro_tag.html", form=form)

@app.route("/tags/editar/<int:id>", methods=["GET", "POST"])
@admin_required
def editar_tag(id):
    """Redirect tag editing to the suspended user list modal."""
    tag = Tag.query.get_or_404(id)
    if request.method == "POST":
        form = TagForm()
        if form.validate_on_submit():
            new_name = (form.nome.data or "").strip()
            if new_name:
                duplicate = (
                    Tag.query.filter(db.func.lower(Tag.nome) == new_name.lower(), Tag.id != tag.id).first()
                )
                if duplicate:
                    flash("Já existe uma tag com esse nome.", "warning")
                else:
                    tag.nome = new_name
                    db.session.commit()
                    flash("Tag atualizada com sucesso!", "success")
            else:
                flash("Informe um nome válido para a tag.", "warning")
    return redirect(url_for("list_users", open_tag_modal="1", edit_tag_id=tag.id))

@app.route("/consultorias/relatorios")
@admin_required
def relatorios_consultorias():
    """Display reports of inclusões grouped by consultoria, user, and date."""
    inicio_raw = request.args.get("inicio")
    fim_raw = request.args.get("fim")
    query = Inclusao.query

    inicio = None
    if inicio_raw:
        try:
            inicio = datetime.strptime(inicio_raw, "%Y-%m-%d").date()
            query = query.filter(Inclusao.data >= inicio)
        except ValueError:
            inicio = None

    fim = None
    if fim_raw:
        try:
            fim = datetime.strptime(fim_raw, "%Y-%m-%d").date()
            query = query.filter(Inclusao.data <= fim)
        except ValueError:
            fim = None

    por_consultoria = (
        query.with_entities(Inclusao.consultoria, db.func.count(Inclusao.id))
        .group_by(Inclusao.consultoria)
        .order_by(db.func.count(Inclusao.id).desc())
        .all()
    )

    por_usuario = (
        query.with_entities(Inclusao.usuario, db.func.count(Inclusao.id))
        .group_by(Inclusao.usuario)
        .order_by(db.func.count(Inclusao.id).desc())
        .all()
    )

    labels_consultoria = [c or "N/D" for c, _ in por_consultoria]
    counts_consultoria = [total for _, total in por_consultoria]
    chart_consultoria = {
        "type": "bar",
        "title": "Inclusões por consultoria",
        "datasetLabel": "Total de inclusões",
        "labels": labels_consultoria,
        "values": counts_consultoria,
        "xTitle": "Consultoria",
        "yTitle": "Total",
        "total": sum(counts_consultoria),
    }

    labels_usuario = [u or "N/D" for u, _ in por_usuario]
    counts_usuario = [total for _, total in por_usuario]
    chart_usuario = {
        "type": "bar",
        "title": "Inclusões por usuário",
        "datasetLabel": "Total de inclusões",
        "labels": labels_usuario,
        "values": counts_usuario,
        "xTitle": "Usuário",
        "yTitle": "Total",
        "total": sum(counts_usuario),
    }

    inclusoes = query.all()
    por_data = []
    if inicio or fim:
        por_data = (
            query.filter(Inclusao.data.isnot(None))
            .with_entities(Inclusao.data, db.func.count(Inclusao.id))
            .group_by(Inclusao.data)
            .order_by(Inclusao.data)
            .all()
        )

    return render_template(
        "relatorios_consultorias.html",
        chart_consultoria=chart_consultoria,
        chart_usuario=chart_usuario,
        por_data=por_data,
        inicio=inicio.strftime("%Y-%m-%d") if inicio else "",
        fim=fim.strftime("%Y-%m-%d") if fim else "",
    )

@app.route("/consultorias/inclusoes")
@login_required
def inclusoes():
    """List and search Consultorias."""
    search_raw = request.args.get("q", "")
    page = request.args.get("page", 1, type=int)
    query = Inclusao.query

    if search_raw:
        like = f"%{search_raw}%"
        query = query.filter(
            or_(
                cast(Inclusao.data, String).ilike(like),
                Inclusao.usuario.ilike(like),
                Inclusao.assunto.ilike(like),
            )
        )

    pagination = query.order_by(Inclusao.data.desc()).paginate(page=page, per_page=50)

    return render_template(
        "inclusoes.html",
        inclusoes=pagination.items,
        pagination=pagination,
        search=search_raw,
    )

@app.route("/consultorias/inclusoes/nova", methods=["GET", "POST"])
@login_required
def nova_inclusao():
    """Render and handle Consultoria form."""
    users = User.query.order_by(User.name).all()
    if request.method == "POST":
        user_id = request.form.get("usuario")
        user = User.query.get(int(user_id)) if user_id else None
        data_str = request.form.get("data")
        data = datetime.strptime(data_str, "%Y-%m-%d").date() if data_str else None
        inclusao = Inclusao(
            data=data,
            usuario=user.name if user else "",
            setor=request.form.get("setor"),
            consultoria=request.form.get("consultoria"),
            assunto=(request.form.get("assunto") or "").upper(),
            pergunta=sanitize_html(request.form.get("pergunta")),
            resposta=sanitize_html(request.form.get("resposta")),
        )
        db.session.add(inclusao)
        db.session.commit()
        flash("Consultoria registrada com sucesso.", "success")
        return redirect(url_for("inclusoes"))
    return render_template(
        "nova_inclusao.html",
        users=users,
        setores=Setor.query.order_by(Setor.nome).all(),
        consultorias=Consultoria.query.order_by(Consultoria.nome).all(),
    )

@app.route("/consultorias/inclusoes/<int:codigo>")
@login_required
def visualizar_consultoria(codigo):
    """Display details for a single consultoria."""
    inclusao = Inclusao.query.get_or_404(codigo)
    return render_template(
        "visualizar_consultoria.html",
        inclusao=inclusao,
        data_formatada=inclusao.data_formatada,
    )

@app.route("/consultorias/inclusoes/<int:codigo>/editar", methods=["GET", "POST"])
@login_required
def editar_consultoria(codigo):
    """Render and handle editing of a consultoria."""
    inclusao = Inclusao.query.get_or_404(codigo)
    users = User.query.order_by(User.name).all()
    if request.method == "POST":
        user_id = request.form.get("usuario")
        user = User.query.get(int(user_id)) if user_id else None
        data_str = request.form.get("data")
        inclusao.data = (
            datetime.strptime(data_str, "%Y-%m-%d").date() if data_str else None
        )
        inclusao.usuario = user.name if user else ""
        inclusao.setor = request.form.get("setor")
        inclusao.consultoria = request.form.get("consultoria")
        inclusao.assunto = (request.form.get("assunto") or "").upper()
        inclusao.pergunta = sanitize_html(request.form.get("pergunta"))
        inclusao.resposta = sanitize_html(request.form.get("resposta"))
        db.session.commit()
        flash("Consultoria atualizada com sucesso.", "success")
        next_url = request.form.get("next") or request.args.get("next")
        if next_url:
            return redirect(next_url)
        return redirect(url_for("inclusoes"))
    return render_template(
        "nova_inclusao.html",
        users=users,
        setores=Setor.query.order_by(Setor.nome).all(),
        consultorias=Consultoria.query.order_by(Consultoria.nome).all(),
        inclusao=inclusao,
    )

@app.route("/cookies")
def cookies():
    """Render the cookie policy page."""
    return render_template("cookie_policy.html")

@app.route("/cookies/revoke")
def revoke_cookies():
    """Revoke cookie consent and redirect to index."""
    resp = redirect(url_for("index"))
    resp.delete_cookie("cookie_consent")
    flash("Consentimento de cookies revogado.", "info")
    return resp

@app.route("/login/google")
def google_login():
    """Start OAuth login with Google."""
    flow = build_google_flow()
    authorization_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    session["oauth_state"] = state
    # ``google-auth`` only attaches the PKCE verifier to the flow instance, so we
    # persist it explicitly to reuse on the callback.
    code_verifier = getattr(flow, "code_verifier", None)
    if isinstance(code_verifier, bytes):
        code_verifier = code_verifier.decode()
    if code_verifier:
        session["oauth_code_verifier"] = code_verifier
    else:
        session.pop("oauth_code_verifier", None)
    return redirect(authorization_url)


def normalize_scopes(scopes):
    """Converte escopos curtos do Google para equivalentes longos."""
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
    """Return the appropriate dashboard URL after authentication."""

    if user.role == "admin":
        return url_for("tasks_overview")

    # Check if user has ONLY the "reunião" tag (meeting-only access)
    tags = getattr(user, "tags", None) or []
    if len(tags) == 1 and tags[0].nome.lower() == 'reunião':
        return url_for("sala_reunioes")

    first_tag = tags[0] if tags else None
    if first_tag:
        return url_for("tasks_sector", tag_id=first_tag.id)

    return url_for("home")

@app.route("/oauth2callback")
def google_callback():
    error = request.args.get("error")
    if error:
        session.pop("oauth_state", None)
        session.pop("oauth_code_verifier", None)
        flash("O Google não autorizou o login solicitado.", "danger")
        return redirect(url_for("login"))

    state = session.get("oauth_state")
    code_verifier = session.get("oauth_code_verifier")
    if state is None or state != request.args.get("state"):
        session.pop("oauth_state", None)
        session.pop("oauth_code_verifier", None)
        flash("Falha ao validar resposta do Google. Tente novamente.", "danger")
        return redirect(url_for("login"))

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
        return redirect(url_for("login"))
    finally:
        session.pop("oauth_state", None)
        session.pop("oauth_code_verifier", None)
    credentials = flow.credentials
    request_session = requests.Session()
    token_request = Request(session=request_session)
    try:
        id_info = id_token.verify_oauth2_token(
            credentials.id_token, token_request, current_app.config["GOOGLE_CLIENT_ID"]
        )
    except ValueError:
        current_app.logger.exception("ID token do Google inválido durante login")
        flash("Não foi possível validar a resposta do Google.", "danger")
        return redirect(url_for("login"))
    google_id = id_info.get("sub")
    email = id_info.get("email")
    name = id_info.get("name", email)
    user = User.query.filter(
        (User.google_id == google_id) | (User.email == email)
    ).first()
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
    if credentials.refresh_token:
        user.google_refresh_token = credentials.refresh_token
        db.session.commit()
    login_user(user, remember=True, duration=timedelta(days=30))
    session.permanent = True
    sid = uuid4().hex
    session["sid"] = sid
    session["credentials"] = credentials_to_dict(credentials)
    db.session.add(
        Session(
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

@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])  # Brute-force protection
def login():
    """Render the login page and handle authentication."""
    from app.utils.audit import log_user_action, ActionType, ResourceType

    form = LoginForm()
    google_enabled = bool(
        current_app.config.get("GOOGLE_CLIENT_ID")
        and current_app.config.get("GOOGLE_CLIENT_SECRET")
    )
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data):
            if not user.ativo:
                flash("Seu usuário está inativo. Contate o administrador.", "danger")
                # Log failed login attempt (inactive user)
                import logging
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
                return redirect(url_for("login"))
            login_user(
                user,
                remember=form.remember_me.data,
                duration=timedelta(days=30),
            )
            session.permanent = form.remember_me.data
            sid = uuid4().hex
            session["sid"] = sid
            db.session.add(
                Session(
                    session_id=sid,
                    user_id=user.id,
                    session_data=dict(session),
                    ip_address=request.remote_addr,
                    user_agent=request.headers.get("User-Agent"),
                    last_activity=datetime.now(SAO_PAULO_TZ),
                )
            )
            db.session.commit()

            # Log successful login
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
            # Log failed login attempt
            import logging
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
    raw_events = fetch_raw_events()
    calendar_tz = get_calendar_timezone()
    now = datetime.now(calendar_tz)
    events = combine_events(
        raw_events, now, current_user.id, is_user_admin(current_user)
    )
    return jsonify(events)

@app.route("/api/calendario-eventos")
@login_required
def api_general_calendar_events():
    """Return collaborator calendar events as JSON."""

    can_manage = is_user_admin(current_user) or user_has_tag("Gestão")
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

    query = Empresa.query

    # Filter inactive companies by default
    if not show_inactive:
        query = query.filter_by(ativo=True)

    if search:
        like_pattern = f"%{search}%"
        query = query.filter(
            or_(
                Empresa.nome_empresa.ilike(like_pattern),
                Empresa.codigo_empresa.ilike(like_pattern),
            )
        )

    sort = request.args.get("sort", "nome")
    order = request.args.get("order", "asc")

    if sort == "codigo":
        order_column = Empresa.codigo_empresa
    else:
        order_column = Empresa.nome_empresa

    # Show active companies first, then sort by the selected column
    if order == "desc":
        query = query.order_by(Empresa.ativo.desc(), order_column.desc())
    else:
        query = query.order_by(Empresa.ativo.desc(), order_column.asc())

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
    )

@app.route("/empresa/editar/<int:id>", methods=["GET", "POST"])
@login_required
def editar_empresa(id):
    """Edit an existing company and its details."""
    empresa = Empresa.query.get_or_404(id)
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
                return redirect(url_for("visualizar_empresa", id=id) + "#dados-cliente")
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

@app.route("/empresa/visualizar/<int:id>")
@login_required
def visualizar_empresa(id):
    """Display a detailed view of a company."""
    from types import SimpleNamespace

    empresa = Empresa.query.get_or_404(id)

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

    return render_template(
        "empresas/visualizar.html",
        empresa=empresa,
        fiscal=fiscal_view,
        contabil=contabil,
        pessoal=pessoal,
        administrativo=administrativo,
        financeiro=financeiro,
        notas_fiscais=notas_fiscais,
        can_access_financeiro=can_access_financeiro,
    )

    ## Rota para gerenciar departamentos de uma empresa

@app.route("/empresa/<int:empresa_id>/departamentos", methods=["GET", "POST"])
@login_required
def gerenciar_departamentos(empresa_id):
    """Create or update department data for a company."""
    empresa = Empresa.query.get_or_404(empresa_id)

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
        Departamento.empresa_id == empresa_id,
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

        if form_type == "fiscal" and fiscal_form.validate():
            if not fiscal:
                fiscal = Departamento(empresa_id=empresa_id, tipo="Departamento Fiscal")
                db.session.add(fiscal)

            fiscal_form.populate_obj(fiscal)
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
                    empresa_id=empresa_id, tipo="Departamento Contábil"
                )
                db.session.add(contabil)

            contabil_form.populate_obj(contabil)
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
                    empresa_id=empresa_id, tipo="Departamento Pessoal"
                )
                db.session.add(pessoal)

            pessoal_form.populate_obj(pessoal)
            flash("Departamento Pessoal salvo com sucesso!", "success")
            form_processed_successfully = True

        elif form_type == "administrativo" and administrativo_form.validate():
            if not administrativo:
                administrativo = Departamento(
                    empresa_id=empresa_id, tipo="Departamento Administrativo"
                )
                db.session.add(administrativo)

            administrativo_form.populate_obj(administrativo)
            flash("Departamento Administrativo salvo com sucesso!", "success")
            form_processed_successfully = True
        elif form_type == "financeiro":
            if not can_access_financeiro:
                abort(403)
            if financeiro_form and financeiro_form.validate():
                if not financeiro:
                    financeiro = Departamento(
                        empresa_id=empresa_id, tipo="Departamento Financeiro"
                    )
                    db.session.add(financeiro)

                financeiro_form.populate_obj(financeiro)
                flash("Departamento Financeiro salvo com sucesso!", "success")
                form_processed_successfully = True

        elif form_type == "notas_fiscais":
            if not notas_fiscais:
                notas_fiscais = Departamento(
                    empresa_id=empresa_id, tipo="Departamento Notas Fiscais"
                )
                db.session.add(notas_fiscais)

            particularidades_texto = request.form.get("particularidades_texto", "")
            notas_fiscais.particularidades_texto = particularidades_texto
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
                    url_for("visualizar_empresa", id=empresa_id) + f"#{hash_ancora}"
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
    )

@app.route("/relatorios")
@admin_required
def relatorios():
    """Render the reports landing page."""
    return render_template("admin/relatorios.html")

@app.route("/relatorio_empresas")
@admin_required
def relatorio_empresas():
    """Display aggregated company statistics."""
    empresas = Empresa.query.with_entities(
        Empresa.nome_empresa,
        Empresa.cnpj,
        Empresa.codigo_empresa,
        Empresa.tributacao,
        Empresa.sistema_utilizado,
    ).all()

    categorias = ["Simples Nacional", "Lucro Presumido", "Lucro Real"]
    grouped = {cat: [] for cat in categorias}
    grouped_sistemas = {}

    for nome, cnpj, codigo, trib, sistema in empresas:
        label = trib if trib in categorias else "Outros"
        grouped.setdefault(label, []).append(
            {"nome": nome, "cnpj": cnpj, "codigo": codigo}
        )

        sistema_label = sistema.strip() if sistema else "Não informado"
        grouped_sistemas.setdefault(sistema_label, []).append(
            {"nome": nome, "cnpj": cnpj, "codigo": codigo}
        )

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
    )

@app.route("/relatorio_fiscal")
@admin_required
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
@admin_required
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
@admin_required
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

@app.route("/logout", methods=["GET"])
@login_required
def logout():
    """Log out the current user."""
    from app.utils.audit import log_user_action, ActionType, ResourceType

    # Log logout before actually logging out
    log_user_action(
        action_type=ActionType.LOGOUT,
        resource_type=ResourceType.SESSION,
        action_description=f'Usuario {current_user.username} fez logout',
        resource_id=current_user.id,
    )

    sid = session.get("sid")
    if sid:
        Session.query.filter_by(session_id=sid).delete()
        db.session.commit()
        session.pop("sid", None)
    logout_user()
    return redirect(url_for("index"))

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
    return user_id is not None and (
        task.created_by == user_id or task.assigned_to == user_id
    )


def _user_can_transfer_task(task: Task, user: User | None) -> bool:
    """Return ``True`` when ``user`` is allowed to transfer ``task`` to another assignee."""

    if user is None:
        return False
    if getattr(user, "role", None) == "admin":
        return True
    user_id = getattr(user, "id", None)
    if user_id is None:
        return False
    return task.created_by == user_id or task.assigned_to == user_id


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


def _ensure_personal_tag(user: User) -> Tag:
    """Return (and create if needed) the personal tag for ``user``."""

    tag_name = f"{PERSONAL_TAG_PREFIX}{user.id}"
    tag = Tag.query.filter_by(nome=tag_name).first()
    if not tag:
        tag = Tag(nome=tag_name)
        db.session.add(tag)
        db.session.flush()
    return tag


@app.route("/tasks/overview")
@login_required
@meeting_only_access_check
def tasks_overview():
    """Kanban view of all tasks grouped by status."""
    assigned_param = (request.args.get("assigned_by_me", "") or "").lower()
    assigned_by_me = assigned_param in {"1", "true", "on", "yes"}
    priority_param = (request.args.get("priority") or "").strip().lower()
    keyword = (request.args.get("q") or "").strip()
    assignee_param = (request.args.get("assignee_id") or "").strip()
    creator_param = (request.args.get("creator_id") or "").strip()
    due_from_raw = (request.args.get("due_from") or "").strip()
    due_to_raw = (request.args.get("due_to") or "").strip()
    selected_priority = None
    selected_assignee_id = None
    selected_creator_id = None

    def _parse_date_param(raw_value):
        if not raw_value:
            return None
        try:
            return datetime.strptime(raw_value, "%Y-%m-%d").date()
        except ValueError:
            return None

    query = (
        Task.query.join(Tag)
        .filter(Task.parent_id.is_(None), ~Tag.nome.in_(EXCLUDED_TASK_TAGS))
        .filter(sa.or_(Task.is_private.is_(False), Task.created_by == current_user.id))
    )
    if current_user.role != "admin":
        accessible_ids = _get_accessible_tag_ids(current_user)
        allowed_filters = []
        if accessible_ids:
            allowed_filters.append(Task.tag_id.in_(accessible_ids))
        allowed_filters.append(Task.created_by == current_user.id)
        query = query.filter(sa.or_(*allowed_filters))
    if assigned_by_me:
        selected_creator_id = current_user.id
        query = query.filter(Task.created_by == current_user.id)
    elif creator_param:
        try:
            selected_creator_id = int(creator_param)
        except ValueError:
            selected_creator_id = None
        if selected_creator_id:
            query = query.filter(Task.created_by == selected_creator_id)
    if assignee_param:
        try:
            selected_assignee_id = int(assignee_param)
        except ValueError:
            selected_assignee_id = None
        if selected_assignee_id:
            query = query.filter(Task.assigned_to == selected_assignee_id)
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

    tasks = (
        query.options(
            joinedload(Task.tag),
            joinedload(Task.assignee),
            joinedload(Task.finisher),
            joinedload(Task.creator),
            # Removed status_history and attachments eager loading to reduce Cartesian product
            # These will be loaded on-demand when needed (lazy loading)
            joinedload(Task.children).joinedload(Task.assignee),
            joinedload(Task.children).joinedload(Task.finisher),
            joinedload(Task.children).joinedload(Task.tag),
            joinedload(Task.children).joinedload(Task.creator),
            # Removed children's status_history and attachments for same reason
        )
        .order_by(Task.due_date)
        .limit(200)  # Added limit to prevent loading too many tasks at once
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
    tasks_by_status = {status: [] for status in TaskStatus}
    for t in tasks:
        status = t.status
        if isinstance(status, str):
            try:
                status = TaskStatus(status)
            except Exception:
                status = TaskStatus.PENDING
        if status not in tasks_by_status:
            status = TaskStatus.PENDING
        tasks_by_status[status].append(t)
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
        assigned_by_me=assigned_by_me,
        allow_delete=current_user.role == "admin",
        priorities=list(TaskPriority),
        selected_priority=selected_priority.value if selected_priority else "",
        keyword=keyword,
        assignee_id=selected_assignee_id,
        creator_id=selected_creator_id,
        due_from=due_from.strftime("%Y-%m-%d") if due_from else "",
        due_to=due_to.strftime("%Y-%m-%d") if due_to else "",
        users=active_users,
    )


@app.route("/tasks/overview/mine")
@login_required
def tasks_overview_mine():
    """Kanban view of tasks created by the current user."""

    visible_statuses = [TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.DONE]
    query = (
        Task.query.join(Tag)
        .filter(Task.parent_id.is_(None))
        .filter(
            sa.or_(
                Task.created_by == current_user.id,
                sa.and_(Task.is_private.is_(True), Task.assigned_to == current_user.id),
            )
        )
    )
    tasks = (
        query.options(
            joinedload(Task.tag),
            joinedload(Task.assignee),
            joinedload(Task.finisher),
            joinedload(Task.creator),
            # Removed status_history and attachments eager loading to reduce Cartesian product
            joinedload(Task.children).joinedload(Task.assignee),
            joinedload(Task.children).joinedload(Task.finisher),
            joinedload(Task.children).joinedload(Task.tag),
            joinedload(Task.children).joinedload(Task.creator),
            # Removed children's status_history and attachments for same reason
        )
        .order_by(Task.due_date)
        .limit(200)  # Added limit to prevent loading too many tasks at once
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
    tasks_by_status = {status: [] for status in visible_statuses}
    for t in tasks:
        status = t.status
        if isinstance(status, str):
            try:
                status = TaskStatus(status)
            except Exception:
                status = TaskStatus.PENDING
        if status in tasks_by_status:
            tasks_by_status[status].append(t)
    # Sort DONE tasks by completion date and show only last 5
    done_sorted = sorted(
        tasks_by_status[TaskStatus.DONE],
        key=lambda x: x.completed_at or datetime.min,
        reverse=True,
    )
    history_count = max(0, len(done_sorted) - 5)
    tasks_by_status[TaskStatus.DONE] = done_sorted[:5]

    return render_template(
        "tasks_overview_mine.html",
        tasks_by_status=tasks_by_status,
        TaskStatus=TaskStatus,
        visible_statuses=visible_statuses,
        history_count=history_count,
        allow_delete=current_user.role == "admin",
        history_url=url_for("tasks_history", assigned_by_me=1),
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
    tasks_by_status = {status: [] for status in visible_statuses}
    for t in tasks:
        status = t.status
        if isinstance(status, str):
            try:
                status = TaskStatus(status)
            except Exception:
                status = TaskStatus.PENDING
        if status in tasks_by_status:
            tasks_by_status[status].append(t)
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


@app.route("/tasks/new", methods=["GET", "POST"])
@login_required
def tasks_new():
    """Form to create a new task or subtask."""
    parent_id = request.args.get("parent_id", type=int)
    return_url = request.args.get("return_url")  # Não usar request.referrer - queremos ir para "Minhas Tarefas"
    parent_task = Task.query.get(parent_id) if parent_id else None
    if parent_task and parent_task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    if parent_task and parent_task.is_private and not _user_can_access_task(parent_task, current_user):
        abort(403)
    requested_tag_id = request.args.get("tag_id", type=int)
    choices: list[tuple[int, str]] = []

    form = TaskForm()
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
    personal_tag = None
    # Garantir que o valor do only_me seja preservado no POST
    if request.method == "POST":
        # Forçar o valor do checkbox baseado no request.form
        form.only_me.data = bool(request.form.get('only_me'))
        current_app.logger.info(
            f"Task create POST - only_me raw value: {request.form.get('only_me')}, "
            f"form.only_me.data: {form.only_me.data}, " 
            f"tag_id: {form.tag_id.data}, assigned_to: {form.assigned_to.data}"
        )
        
    if request.method == "POST" and form.only_me.data:
        personal_tag = _ensure_personal_tag(current_user)
        form.tag_id.data = personal_tag.id
        form.assigned_to.data = current_user.id
        if all(choice[0] != personal_tag.id for choice in form.tag_id.choices):
            form.tag_id.choices.append((personal_tag.id, "Para Mim"))
        current_app.logger.info(
            f"Task create - only_me checked, personal_tag: {personal_tag.id}, "
            f"tag_id updated to: {form.tag_id.data}"
        )

    if form.validate_on_submit():
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
            personal_tag = personal_tag or _ensure_personal_tag(current_user)
            tag = personal_tag
            tag_id = personal_tag.id
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

            creator_name = current_user.name or current_user.username
            creation_now = datetime.utcnow()
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

            db.session.commit()

            # Verificar e recarregar tarefa do banco para garantir persistencia
            db.session.refresh(task)

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

    if task.is_private:
        if not _user_can_access_task(task, current_user):
            abort(403)
    else:
        if not (_can_user_access_tag(task.tag, current_user) or is_admin or is_creator or is_assignee):
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

    # Popular campos no GET com dados da task existente
    if request.method == "GET":
        form.task_id.data = task.id
        form.title.data = task.title
        form.description.data = task.description
        form.priority.data = task.priority.value if task.priority else "medium"
        form.due_date.data = task.due_date
        form.only_me.data = task.is_private
        form.assigned_to.data = task.assigned_to or (current_user.id if task.is_private else 0)

    personal_tag = None
    if request.method == "POST":
        current_app.logger.info(
            f"Task edit POST (task {task_id}) - only_me raw value: {request.form.get('only_me')}, "
            f"form.only_me.data: {form.only_me.data}, "
            f"tag_id: {form.tag_id.data}, assigned_to: {form.assigned_to.data}"
        )
    if request.method == "POST" and form.only_me.data:
        personal_tag = _ensure_personal_tag(current_user)
        form.tag_id.data = personal_tag.id
        form.assigned_to.data = current_user.id
        if all(choice[0] != personal_tag.id for choice in form.tag_id.choices):
            updated_choices = list(form.tag_id.choices) + [(personal_tag.id, "Para Mim")]
            form.tag_id.choices = _sort_choice_pairs(updated_choices)
        tag = personal_tag
        current_app.logger.info(
            f"Task edit - only_me checked, personal_tag: {personal_tag.id}, "
            f"tag_id updated to: {form.tag_id.data}"
        )

    if form.validate_on_submit():
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
            personal_tag = personal_tag or _ensure_personal_tag(current_user)
            tag = personal_tag
            tag_id = personal_tag.id
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

            # Notificar sobre a edição da tarefa
            editor_name = current_user.name or current_user.username
            edit_message = f'{editor_name} editou a tarefa "{task.title}".'
            edit_now = datetime.utcnow()
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
    tasks_by_status = {status: [] for status in TaskStatus}
    for t in tasks:
        status = t.status
        if isinstance(status, str):
            try:
                status = TaskStatus(status)
            except Exception:
                status = TaskStatus.PENDING
        if status not in tasks_by_status:
            status = TaskStatus.PENDING
        tasks_by_status[status].append(t)
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
            sa.or_(Task.is_private.is_(False), Task.created_by == current_user.id),
        )
    else:
        tag = None
        if current_user.role == "admin":
            query = Task.query.filter(
                Task.parent_id.is_(None),
                Task.status == TaskStatus.DONE,
                sa.or_(Task.is_private.is_(False), Task.created_by == current_user.id),
            )
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
            query = query.filter(
                sa.or_(Task.is_private.is_(False), Task.created_by == current_user.id)
            )
    if query is not None:
        if only_me:
            query = query.filter(Task.is_private.is_(True)).filter(
                sa.or_(
                    Task.created_by == current_user.id,
                    Task.assigned_to == current_user.id,
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
    return {uid for uid in participant_ids if uid}


def _user_can_access_task_conversation(task: Task, user: User) -> bool:
    """Return True when ``user`` is allowed to view/post task responses."""

    if user.role == "admin":
        return True
    if task.created_by == user.id:
        return True
    if task.assigned_to == user.id:
        return True
    if task.completed_by == user.id:
        return True
    return False


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
    if not task.is_private and not _can_user_access_tag(task.tag, current_user) and task.created_by != current_user.id:
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
    if not task.is_private and not _can_user_access_tag(task.tag, current_user) and task.created_by != current_user.id:
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

    created_at = datetime.utcnow()
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
    now = datetime.utcnow()
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
    if not task.is_private and not _can_user_access_tag(task.tag, current_user) and task.created_by != current_user.id:
        abort(403)
    if not _user_can_access_task_conversation(task, current_user):
        abort(403)
    if task.status not in (TaskStatus.IN_PROGRESS, TaskStatus.DONE):
        return jsonify({"success": False, "error": "conversation_unavailable"}), 409

    participant = _ensure_response_participant(task.id, current_user.id)
    now = datetime.utcnow()
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
        if not _can_user_access_tag(task.tag, current_user) and task.created_by != current_user.id:
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
        if request.referrer and request.referrer != request.url:
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
    if not task.is_private and not _can_user_access_tag(task.tag, current_user):
        current_app.logger.warning(f"Access denied: User {current_user.id} cannot access tag {task.tag.nome}")
        abort(403)
    data = request.get_json() or {}
    status_value = data.get("status")
    try:
        new_status = TaskStatus(status_value)
    except Exception:
        abort(400)
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
            task.completed_at = datetime.utcnow()
        elif new_status == TaskStatus.PENDING:
            task.assigned_to = None
            task.completed_by = None
            task.completed_at = None
        else:
            task.completed_by = None
            task.completed_at = None

        status_notification_records: list[tuple[int, TaskNotification]] = []
        actor_name = current_user.name or current_user.username
        now = datetime.utcnow()
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
    from app.services.realtime import broadcast_task_status_changed, get_broadcaster
    task_data = _serialize_task(task)
    if not task.is_private:
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
    if current_user.role != "admin" and task.created_by != current_user.id and task.assigned_to != current_user.id:
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
## HEALTH CHECK ENDPOINTS - For monitoring and load balancers
## ============================================================================
