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
)
from functools import wraps
from collections import Counter
from flask_login import current_user, login_required, login_user, logout_user
from app import app, db, csrf
from app.utils.security import sanitize_html
from app.utils.mailer import send_email, EmailDeliveryError
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
    AccessLink,
    Course,
    CourseTag,
    DiretoriaEvent,
    DiretoriaAgreement,
    GeneralCalendarEvent,
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
    MeetingForm,
    MeetConfigurationForm,
    GeneralCalendarEventForm,
    TaskForm,
    AccessLinkForm,
    CourseForm,
    CourseTagForm,
    AnnouncementForm,
    DiretoriaAcordoForm,
    OperationalProcedureForm,
)
import os, json, re, secrets, imghdr
import requests
from urllib.parse import urlparse
from werkzeug.utils import secure_filename
from uuid import uuid4
from sqlalchemy import or_, cast, String, text, inspect
from sqlalchemy.exc import NoSuchTableError, SQLAlchemyError, OperationalError
import sqlalchemy as sa
from sqlalchemy.orm import joinedload
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
import plotly.graph_objects as go
from plotly.colors import qualitative
from werkzeug.exceptions import RequestEntityTooLarge
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable
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


ANNOUNCEMENTS_UPLOAD_SUBDIR = os.path.join("uploads", "announcements")


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

ACESSOS_DISPLAY_GROUPS: list[dict[str, Any]] = [
    {
        "slug": "fiscal_contabil",
        "title": "Fiscal & Contábil",
        "icon": "bi bi-clipboard-check",
        "categories": ("fiscal", "contabil"),
    },
    {
        "slug": "pessoal",
        "title": "Pessoal",
        "icon": ACESSOS_CATEGORIES["pessoal"]["icon"],
        "categories": ("pessoal",),
    },
]


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

    if parsed.scheme == "https" and parsed.netloc:
        return parsed.geturl()
    if parsed.scheme and parsed.scheme != "https":
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


@app.context_processor
def inject_stats():
    """Inject global statistics into templates."""
    if current_user.is_authenticated:
        total_empresas = Empresa.query.count()
        total_usuarios = User.query.count() if current_user.role == "admin" else 0
        online_count = 0
        if current_user.role == "admin":
            cutoff = datetime.utcnow() - timedelta(minutes=5)
            online_count = User.query.filter(User.last_seen >= cutoff).count()
        return {
            "total_empresas": total_empresas,
            "total_usuarios": total_usuarios,
            "online_users_count": online_count,
        }
    return {}


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


@app.errorhandler(RequestEntityTooLarge)
def handle_large_file(e):
    """Return JSON error when uploaded file exceeds limit."""
    return jsonify({"error": "Arquivo excede o tamanho permitido"}), 413


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
    if "image" not in request.files:
        return jsonify({"error": "Nenhuma imagem enviada"}), 400

    file = request.files["image"]
    if not file.filename:
        return jsonify({"error": "Nome de arquivo vazio"}), 400

    if not allowed_file(file.filename) or not is_safe_image_upload(file):
        return jsonify({"error": "Imagem invalida ou nao permitida"}), 400

    filename = secure_filename(file.filename)
    unique_name = f"{uuid4().hex}_{filename}"
    upload_folder = os.path.join(current_app.root_path, "static", "uploads")
    file_path = os.path.join(upload_folder, unique_name)

    try:
        os.makedirs(upload_folder, exist_ok=True)
        file.save(file_path)
        file_url = url_for("static", filename=f"uploads/{unique_name}", _external=True)
        return jsonify({"image_url": file_url})
    except Exception as exc:
        current_app.logger.exception("Falha ao salvar upload de imagem", exc_info=exc)
        return jsonify({"error": "Erro no servidor ao salvar arquivo"}), 500


@app.route("/upload_file", methods=["POST"])
@login_required
def upload_file():
    """Handle file uploads (images + PDFs) from the WYSIWYG editor."""
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
    unique_name = f"{uuid4().hex}_{filename}"
    upload_folder = os.path.join(current_app.root_path, "static", "uploads")
    file_path = os.path.join(upload_folder, unique_name)

    try:
        os.makedirs(upload_folder, exist_ok=True)
        file.save(file_path)
        file_url = url_for("static", filename=f"uploads/{unique_name}", _external=True)
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
    ti_tag = _get_ti_tag()
    if ti_tag:
        ids.add(ti_tag.id)
    return list(ids)


@app.context_processor
def inject_user_tag_helpers():
    """Expose user tag helper utilities to templates."""
    return dict(user_has_tag=user_has_tag)


@app.context_processor
def inject_task_tags():
    """Provide task-related tags for dynamic sidebar menus."""
    if not current_user.is_authenticated:
        return {"tasks_tags": []}
    tags = sorted(
        [t for t in current_user.tags if t.nome.lower() not in EXCLUDED_TASK_TAGS_LOWER],
        key=lambda t: t.nome,
    )
    return {"tasks_tags": tags}


@app.context_processor
def inject_notification_counts():
    """Expose the number of unread task notifications to templates."""

    if not current_user.is_authenticated:
        return {"unread_notifications_count": 0}
    unread = TaskNotification.query.filter(
        TaskNotification.user_id == current_user.id,
        TaskNotification.read_at.is_(None),
    ).count()
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
def home():
    """Render the authenticated home page."""
    return render_template("home.html")


@app.route("/announcements", methods=["GET", "POST"])
@login_required
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
            announcement = Announcement(
                date=form.date.data,
                subject=form.subject.data,
                content=form.content.data,
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

    announcements_query = (
        base_query.options(
            joinedload(Announcement.created_by),
            joinedload(Announcement.attachments),
        )
        .order_by(Announcement.date.desc(), Announcement.created_at.desc())
    )

    announcement_items = announcements_query.all()
    display_count = len(announcement_items)

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
        announcement.content = form.content.data

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
def diretoria_acordos():
    """Render and manage Diretoria JP agreements linked to portal users."""

    if current_user.role != "admin" and not user_has_tag("Gestão"):
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

    if current_user.role != "admin" and not user_has_tag("Gestão"):
        abort(403)

    agreement = DiretoriaAgreement.query.get_or_404(agreement_id)

    redirect_user_id = request.form.get("user_id", type=int) or agreement.user_id
    if redirect_user_id != agreement.user_id:
        redirect_user_id = agreement.user_id

    db.session.delete(agreement)
    db.session.commit()

    flash("Acordo removido com sucesso.", "success")

    return redirect(url_for("diretoria_acordos", user_id=redirect_user_id))


@app.route("/diretoria/eventos", methods=["GET", "POST"])
@login_required
def diretoria_eventos():
    """Render or persist Diretoria JP event planning data."""

    if current_user.role != "admin" and not user_has_tag("Gestão"):
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

    if current_user.role != "admin" and not user_has_tag("Gestão"):
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

    if current_user.role != "admin" and not user_has_tag("Gestão"):
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

    if current_user.role != "admin" and not user_has_tag("Gestão"):
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

    if current_user.role != "admin" and not user_has_tag("Gestão"):
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
def cursos():
    """Display the curated catalog of internal courses."""

    form = CourseForm()
    tag_form = CourseTagForm(prefix="tag")
    can_manage_courses = current_user.role == "admin"
    sector_choices = [
        (sector.id, sector.nome)
        for sector in Setor.query.order_by(Setor.nome.asc()).all()
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
            should_add_to_calendar = bool(form.submit_add_to_calendar.data)
            meeting_query_params: dict[str, Any] = {}
            if should_add_to_calendar:
                meeting_query_params = {"course_calendar": "1"}
                name_value = (form.name.data or "").strip()
                if name_value:
                    meeting_query_params["subject"] = name_value
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
    )


def _build_acessos_context(
    form: "AccessLinkForm | None" = None,
    *,
    open_modal: bool = False,
):
    """Return the template data used by the access hub pages."""

    categoria_links = {
        slug: (
            AccessLink.query.filter_by(category=slug)
            .order_by(AccessLink.created_at.desc())
            .all()
        )
        for slug in ACESSOS_CATEGORIES
    }

    display_columns: list[dict[str, Any]] = []
    for group in ACESSOS_DISPLAY_GROUPS:
        combined_links = []
        for category_slug in group["categories"]:
            combined_links.extend(categoria_links.get(category_slug, []))

        description = group.get("description")
        if not description:
            descriptions = [
                ACESSOS_CATEGORIES[category_slug].get("description")
                for category_slug in group["categories"]
                if ACESSOS_CATEGORIES.get(category_slug, {}).get("description")
            ]
            if descriptions:
                # Preserva ordem e remove duplicados
                description = " ".join(dict.fromkeys(descriptions))

        display_columns.append(
            {
                "slug": group["slug"],
                "title": group["title"],
                "icon": group.get("icon"),
                "description": description,
                "links": combined_links,
            }
        )

    return {
        "categorias": ACESSOS_CATEGORIES,
        "categoria_links": categoria_links,
        "display_columns": display_columns,
        "form": form,
        "open_modal": open_modal,
    }


@app.route("/acessos")
@login_required
def acessos():
    """Display the hub with the available access categories and saved shortcuts."""

    open_modal = request.args.get("modal") == "novo"
    preselected_category = request.args.get("category")

    form: AccessLinkForm | None = None
    if current_user.role == "admin":
        form = AccessLinkForm()
        form.category.choices = _access_category_choices()
        if (
            preselected_category
            and preselected_category in ACESSOS_CATEGORIES
            and not form.category.data
        ):
            form.category.data = preselected_category

    context = _build_acessos_context(form=form, open_modal=open_modal)
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


@app.route("/procedimentos", methods=["GET", "POST"])
@login_required
def procedimentos_operacionais():
    """Lista e permite criação de procedimentos operacionais (admin)."""

    form = OperationalProcedureForm()
    search_term = (request.args.get("q") or "").strip()

    query = OperationalProcedure.query
    if search_term:
        pattern = f"%{search_term}%"
        query = query.filter(
            sa.or_(
                OperationalProcedure.title.ilike(pattern),
                OperationalProcedure.description.ilike(pattern),
            )
        )

    procedures = query.order_by(OperationalProcedure.updated_at.desc()).all()

    if request.method == "POST":
        if current_user.role != "admin":
            abort(403)
        if form.validate_on_submit():
            proc = OperationalProcedure(
                title=form.title.data,
                description= sanitize_html(form.description.data or "") or None,
                created_by_id=current_user.id,
            )
            db.session.add(proc)
            db.session.commit()
            flash("Procedimento criado com sucesso.", "success")
            return redirect(url_for("procedimentos_operacionais"))
        flash("Não foi possível criar o procedimento. Corrija os erros do formulário.", "danger")

    return render_template(
        "procedimentos.html",
        form=form if current_user.role == "admin" else None,
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
        "description": proc.description or "",
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
        form.description.data = proc.description or ""
        return render_template("procedimento_edit.html", procedure=proc, form=form)

    if form.validate_on_submit():
        proc.title = form.title.data
        proc.description = sanitize_html(form.description.data or "") or None
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
@login_required
def ping():
    """Endpoint for client pings to keep the session active."""
    session.modified = True
    return ("", 204)


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
    unread_total = TaskNotification.query.filter(
        TaskNotification.user_id == current_user.id,
        TaskNotification.read_at.is_(None),
    ).count()

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
            task_title = None
            tag_name = None
            if task:
                task_title = task.title
                tag_name = task.tag.nome if task.tag else None
                target_url = url_for("tasks_sector", tag_id=task.tag_id) + f"#task-{task.id}"
            if not message:
                if task_title and tag_name:
                    message = f"Tarefa \"{task_title}\" atribuída no setor {tag_name}."
                elif task_title:
                    message = f"Tarefa \"{task_title}\" atribuída a você."
                else:
                    message = "Nova tarefa atribuída a você."
            action_label = "Abrir tarefa" if target_url else None

        created_at = notification.created_at
        if created_at.tzinfo is None:
            created_at_iso = created_at.isoformat() + "Z"
            display_dt = created_at.replace(tzinfo=timezone.utc).astimezone(SAO_PAULO_TZ)
        else:
            created_at_iso = created_at.isoformat()
            display_dt = created_at.astimezone(SAO_PAULO_TZ)

        items.append(
            {
                "id": notification.id,
                "type": notification_type.value,
                "message": message,
                "created_at": created_at_iso,
                "created_at_display": display_dt.strftime("%d/%m/%Y %H:%M"),
                "is_read": notification.is_read,
                "url": target_url,
                "action_label": action_label,
            }
        )

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


@app.route("/notifications", methods=["GET"])
@login_required
def list_notifications():
    """Return the most recent notifications for the user."""

    items, unread_total = _get_user_notification_items(limit=20)
    return jsonify({"notifications": items, "unread": unread_total})


@app.route("/notificacoes")
@login_required
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
    return jsonify({"success": True, "updated": updated or 0})


def _configure_consultoria_form(form: ConsultoriaForm) -> ConsultoriaForm:
    """Ensure consistent attributes for the consultoria form."""

    form.submit.label.text = "Salvar"
    render_kw = dict(form.senha.render_kw or {})
    render_kw["autocomplete"] = "off"
    form.senha.render_kw = render_kw
    return form


@app.route("/consultorias", methods=["GET", "POST"])
@login_required
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
def calendario_colaboradores():
    """Display and manage the internal collaborators calendar."""

    form = GeneralCalendarEventForm()
    populate_general_event_participants(form)
    can_manage = current_user.role == "admin" or user_has_tag("Gestão")
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
    can_manage = current_user.role == "admin" or user_has_tag("Gestão")
    if not can_manage:
        abort(403)
    if current_user.role != "admin" and event.created_by_id != current_user.id:
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
        seen_ids.add(participant.id_usuario)
        name = (
            participant.usuario.name
            if participant.usuario and participant.usuario.name
            else participant.username_usuario
        )
        candidates.append(
            {
                "id": participant.id_usuario,
                "name": name,
                "username": participant.username_usuario,
                "email": participant.usuario.email if participant.usuario else None,
            }
        )
    creator_name = meeting.criador.name or meeting.criador.username
    creator_entry = {
        "id": meeting.criador_id,
        "name": creator_name,
        "username": meeting.criador.username,
        "email": meeting.criador.email,
    }
    if meeting.criador_id not in seen_ids:
        candidates.append(creator_entry)
        seen_ids.add(meeting.criador_id)
    if meeting.meet_host_id and meeting.meet_host_id not in seen_ids:
        host_obj = meeting.meet_host
        host_name = (
            host_obj.name if host_obj and host_obj.name else (host_obj.username if host_obj else creator_name)
        )
        candidates.append(
            {
                "id": meeting.meet_host_id,
                "name": host_name,
                "username": host_obj.username if host_obj else "",
                "email": host_obj.email if host_obj else None,
            }
        )
    candidates.sort(key=lambda entry: (entry["name"] or "").lower())
    return candidates, creator_name


@app.route("/sala-reunioes", methods=["GET", "POST"])
@login_required
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
            if meeting and meeting.criador_id == current_user.id:
                if meeting.status in (
                    ReuniaoStatus.EM_ANDAMENTO,
                    ReuniaoStatus.REALIZADA,
                    ReuniaoStatus.CANCELADA,
                ):
                    flash(
                        "Reuniões em andamento ou realizadas não podem ser editadas.",
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
                    current_user.role == "admin"
                    or meeting.criador_id == current_user.id
                )
                participant_options, creator_name = _meeting_host_candidates(meeting)
                settings_dict = dict(meeting.meet_settings or default_meet_settings())
                meet_popup_data = {
                    "meeting_id": meeting.id,
                    "meet_link": meet_link,
                    "subject": meeting.assunto,
                    "host_candidates": participant_options,
                    "current_host_id": meeting.meet_host_id or 0,
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
    return render_template(
        "sala_reunioes.html",
        form=form,
        meet_config_form=meet_config_form,
        show_modal=show_modal,
        calendar_timezone=calendar_tz.key,
        meet_popup_data=meet_popup_data,
        meeting_status_options=status_options,
        reschedule_statuses=reschedule_statuses,
    )


@app.route("/reuniao/<int:meeting_id>/meet-config", methods=["POST"])
@login_required
def configure_meet_call(meeting_id: int):
    """Persist configuration options for the Google Meet room."""

    meeting = Reuniao.query.get_or_404(meeting_id)
    if current_user.role != "admin" and meeting.criador_id != current_user.id:
        abort(403)
    form = MeetConfigurationForm()
    candidate_entries, creator_name = _meeting_host_candidates(meeting)
    host_choices = [(0, f"Conta padrão ({creator_name})")] + [
        (entry["id"], entry["name"]) for entry in candidate_entries
    ]
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
            allowed_host_ids = {choice for choice, _ in host_choices if choice}
            if selected_host_raw and selected_host_raw not in allowed_host_ids:
                form.host_id.errors.append("Selecione um proprietário válido para o Meet.")
            else:
                host_id = selected_host_raw or None
                settings_payload = {
                    "quick_access_enabled": form.quick_access_enabled.data,
                    "mute_on_join": form.mute_on_join.data,
                    "allow_chat": form.allow_chat.data,
                    "allow_screen_share": form.allow_screen_share.data,
                }
                (
                    normalized_settings,
                    host,
                    sync_result,
                ) = update_meeting_configuration(
                    meeting, host_id, settings_payload
                )
                host_name = (
                    host.name or host.username
                    if host
                    else creator_name
                )
                warning_message = None
                if sync_result is False:
                    warning_message = (
                        "Não foi possível aplicar as configurações do Meet automaticamente. "
                        "Verifique manualmente na sala do Google Meet."
                    )
                response_payload = {
                    "success": True,
                    "message": "Configurações do Meet atualizadas com sucesso!",
                    "meet_settings": normalized_settings,
                    "meet_host": {
                        "id": host.id if host else None,
                        "name": host_name,
                    },
                    "meet_settings_applied": sync_result is not False,
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
    if current_user.role != "admin" and meeting.criador_id != current_user.id:
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
            current_user.role == "admin",
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


@app.route("/reuniao/<int:meeting_id>/delete", methods=["POST"])
@login_required
def delete_reuniao(meeting_id):
    """Delete a meeting and its corresponding Google Calendar event."""
    meeting = Reuniao.query.get_or_404(meeting_id)
    if current_user.role != "admin":
        if meeting.criador_id != current_user.id:
            flash("Você só pode excluir reuniões que você criou.", "danger")
            return redirect(url_for("sala_reunioes"))
        if meeting.status in (
            ReuniaoStatus.EM_ANDAMENTO,
            ReuniaoStatus.REALIZADA,
        ):
            flash(
                "Reuniões em andamento ou realizadas não podem ser excluídas.",
                "danger",
            )
            return redirect(url_for("sala_reunioes"))
    if delete_meeting(meeting):
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

    labels_consultoria = [c or "—" for c, _ in por_consultoria]
    counts_consultoria = [total for _, total in por_consultoria]
    fig_cons = go.Figure(
        data=[
            go.Bar(
                x=labels_consultoria,
                y=counts_consultoria,
                marker_color=qualitative.Pastel,
            )
        ]
    )
    fig_cons.update_layout(
        title_text="Inclusões por consultoria",
        template="seaborn",
        xaxis_title="Consultoria",
        yaxis_title="Total",
    )
    chart_consultoria = fig_cons.to_html(full_html=False, div_id="consultoria-chart")

    labels_usuario = [u or "—" for u, _ in por_usuario]
    counts_usuario = [total for _, total in por_usuario]
    fig_user = go.Figure(
        data=[
            go.Bar(x=labels_usuario, y=counts_usuario, marker_color=qualitative.Pastel)
        ]
    )
    fig_user.update_layout(
        title_text="Inclusões por usuário",
        template="seaborn",
        xaxis_title="Usuário",
        yaxis_title="Total",
    )
    chart_usuario = fig_user.to_html(full_html=False, div_id="usuario-chart")

    inclusoes = query.all()
    inclusoes_por_consultoria = {}
    inclusoes_por_usuario = {}
    for inc in inclusoes:
        label_cons = inc.consultoria or "—"
        inclusoes_por_consultoria.setdefault(label_cons, []).append(
            {
                "usuario": inc.usuario,
                "pergunta": inc.pergunta,
                "data": inc.data.strftime("%d/%m/%Y") if inc.data else "",
            }
        )
        label_user = inc.usuario or "—"
        inclusoes_por_usuario.setdefault(label_user, []).append(
            {
                "consultoria": inc.consultoria,
                "pergunta": inc.pergunta,
                "data": inc.data.strftime("%d/%m/%Y") if inc.data else "",
            }
        )

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
        inclusoes_por_consultoria=inclusoes_por_consultoria,
        inclusoes_por_usuario=inclusoes_por_usuario,
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

    tags = getattr(user, "tags", None)
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
def login():
    """Render the login page and handle authentication."""
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
            flash("Login bem-sucedido!", "success")
            return redirect(_determine_post_login_redirect(user))
        else:
            flash("Credenciais inválidas", "danger")
    return render_template("login.html", form=form, google_enabled=google_enabled)


@app.route("/dashboard")
@login_required
def dashboard():
    """Admin dashboard placeholder page."""
    return render_template("dashboard.html")


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
def api_reunioes():
    """Return meetings with up-to-date status as JSON."""
    raw_events = fetch_raw_events()
    calendar_tz = get_calendar_timezone()
    now = datetime.now(calendar_tz)
    events = combine_events(
        raw_events, now, current_user.id, current_user.role == "admin"
    )
    return jsonify(events)


@app.route("/api/calendario-eventos")
@login_required
def api_general_calendar_events():
    """Return collaborator calendar events as JSON."""

    can_manage = current_user.role == "admin" or user_has_tag("Gestão")
    events = serialize_events_for_calendar(
        current_user.id, can_manage, current_user.role == "admin"
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
def listar_empresas():
    """List companies with optional search and pagination."""
    search = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = 20
    query = Empresa.query

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

    if order == "desc":
        query = query.order_by(order_column.desc())
    else:
        query = query.order_by(order_column.asc())

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    empresas = pagination.items

    return render_template(
        "empresas/listar.html",
        empresas=empresas,
        pagination=pagination,
        search=search,
        sort=sort,
        order=order,
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

    if request.method == "POST":
        if empresa_form.validate():
            empresa_form.populate_obj(empresa)
            empresa.cnpj = re.sub(r"\D", "", empresa_form.cnpj.data)
            empresa.sistemas_consultorias = empresa_form.sistemas_consultorias.data
            try:
                empresa.acessos = json.loads(empresa_form.acessos_json.data or "[]")
            except Exception:
                empresa.acessos = []
            db.session.add(empresa)
            try:
                db.session.commit()
                flash("Dados da Empresa salvos com sucesso!", "success")
                return redirect(url_for("visualizar_empresa", id=id) + "#dados-empresa")
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

    fiscal = Departamento.query.filter_by(
        empresa_id=id, tipo="Departamento Fiscal"
    ).first()
    contabil = Departamento.query.filter_by(
        empresa_id=id, tipo="Departamento Contábil"
    ).first()
    pessoal = Departamento.query.filter_by(
        empresa_id=id, tipo="Departamento Pessoal"
    ).first()
    administrativo = Departamento.query.filter_by(
        empresa_id=id, tipo="Departamento Administrativo"
    ).first()
    financeiro = (
        Departamento.query.filter_by(
            empresa_id=id, tipo="Departamento Financeiro"
        ).first()
        if can_access_financeiro
        else None
    )
    notas_fiscais = Departamento.query.filter_by(
        empresa_id=id, tipo="Departamento Notas Fiscais"
    ).first()

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

    # monta contatos_list
    if fiscal and getattr(fiscal, "contatos", None):
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

    # fiscal_view: garante objeto mesmo quando fiscal é None
    if fiscal is None:
        fiscal_view = SimpleNamespace(
            formas_importacao=[], contatos_list=contatos_list, envio_fisico=[]
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
        setattr(fiscal_view, "contatos_list", contatos_list)
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

    fiscal = Departamento.query.filter_by(
        empresa_id=empresa_id, tipo="Departamento Fiscal"
    ).first()
    contabil = Departamento.query.filter_by(
        empresa_id=empresa_id, tipo="Departamento Contábil"
    ).first()
    pessoal = Departamento.query.filter_by(
        empresa_id=empresa_id, tipo="Departamento Pessoal"
    ).first()
    administrativo = Departamento.query.filter_by(
        empresa_id=empresa_id, tipo="Departamento Administrativo"
    ).first()
    financeiro = (
        Departamento.query.filter_by(
            empresa_id=empresa_id, tipo="Departamento Financeiro"
        ).first()
        if can_access_financeiro
        else None
    )
    notas_fiscais = Departamento.query.filter_by(
        empresa_id=empresa_id, tipo="Departamento Notas Fiscais"
    ).first()

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
    counts = [len(grouped[l]) for l in labels]
    fig = go.Figure(data=[go.Bar(x=labels, y=counts, marker_color=qualitative.Pastel)])
    fig.update_layout(
        title_text="Empresas por regime de tributação",
        template="seaborn",
        xaxis_title="Regime",
        yaxis_title="Quantidade",
    )
    chart_div = fig.to_html(full_html=False, div_id="empresa-tributacao-chart")

    sistema_labels = list(grouped_sistemas.keys())
    sistema_counts = [len(grouped_sistemas[l]) for l in sistema_labels]
    fig_sistemas = go.Figure(
        data=[
            go.Bar(x=sistema_labels, y=sistema_counts, marker_color=qualitative.Pastel)
        ]
    )
    fig_sistemas.update_layout(
        title_text="Empresas por sistema utilizado",
        template="seaborn",
        xaxis_title="Sistema",
        yaxis_title="Quantidade",
    )
    chart_div_sistema = fig_sistemas.to_html(
        full_html=False, div_id="empresa-sistema-chart"
    )

    return render_template(
        "admin/relatorio_empresas.html",
        chart_div=chart_div,
        empresas_por_slice=grouped,
        chart_div_sistema=chart_div_sistema,
        empresas_por_sistema=grouped_sistemas,
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
    counts_imp = [len(import_grouped[l]) for l in labels_imp]
    fig_imp = go.Figure(
        data=[go.Bar(x=labels_imp, y=counts_imp, marker_color=qualitative.Pastel)]
    )
    fig_imp.update_layout(
        title_text="Formas de Importação (Fiscal)",
        template="seaborn",
        xaxis_title="Forma",
        yaxis_title="Quantidade",
    )
    import_chart = fig_imp.to_html(full_html=False, div_id="fiscal-importacao-chart")
    labels_env = list(envio_grouped.keys())
    counts_env = [len(envio_grouped[l]) for l in labels_env]
    fig_env = go.Figure(
        data=[
            go.Pie(
                labels=labels_env,
                values=counts_env,
                hole=0.4,
                marker=dict(
                    colors=qualitative.Pastel,
                    line=dict(color="#FFFFFF", width=2),
                ),
                textinfo="label+percent",
            )
        ]
    )
    fig_env.update_layout(
        title_text="Envio de Documentos (Fiscal)",
        template="seaborn",
        legend=dict(orientation="h", y=-0.1, x=0.5, xanchor="center"),
    )
    envio_chart = fig_env.to_html(full_html=False, div_id="fiscal-envio-chart")
    labels_mal = list(malote_grouped.keys())
    counts_mal = [len(malote_grouped[l]) for l in labels_mal]
    fig_mal = go.Figure(
        data=[go.Bar(x=labels_mal, y=counts_mal, marker_color=qualitative.Pastel)]
    )
    fig_mal.update_layout(
        title_text="Coleta de Malote (Envio Físico)",
        template="seaborn",
        xaxis_title="Coleta",
        yaxis_title="Quantidade",
    )
    malote_chart = fig_mal.to_html(full_html=False, div_id="fiscal-malote-chart")
    return render_template(
        "admin/relatorio_fiscal.html",
        importacao_chart=import_chart,
        envio_chart=envio_chart,
        malote_chart=malote_chart,
        empresas_por_import=import_grouped,
        empresas_por_envio=envio_grouped,
        empresas_por_malote=malote_grouped,
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
    counts_imp = [len(import_grouped[l]) for l in labels_imp]
    fig_imp = go.Figure(
        data=[go.Bar(x=labels_imp, y=counts_imp, marker_color=qualitative.Pastel)]
    )
    fig_imp.update_layout(
        title_text="Métodos de Importação (Contábil)",
        template="seaborn",
        xaxis_title="Método",
        yaxis_title="Quantidade",
    )
    import_chart = fig_imp.to_html(full_html=False, div_id="contabil-importacao-chart")
    labels_env = list(envio_grouped.keys())
    counts_env = [len(envio_grouped[l]) for l in labels_env]
    fig_env = go.Figure(
        data=[
            go.Pie(
                labels=labels_env,
                values=counts_env,
                hole=0.4,
                marker=dict(
                    colors=qualitative.Pastel,
                    line=dict(color="#FFFFFF", width=2),
                ),
                textinfo="label+percent",
            )
        ]
    )
    fig_env.update_layout(
        title_text="Envio de Documentos (Contábil)",
        template="seaborn",
        legend=dict(orientation="h", y=-0.1, x=0.5, xanchor="center"),
    )
    envio_chart = fig_env.to_html(full_html=False, div_id="contabil-envio-chart")
    labels_mal = list(malote_grouped.keys())
    counts_mal = [len(malote_grouped[l]) for l in labels_mal]
    fig_mal = go.Figure(
        data=[go.Bar(x=labels_mal, y=counts_mal, marker_color=qualitative.Pastel)]
    )
    fig_mal.update_layout(
        title_text="Coleta de Malote (Envio Físico)",
        template="seaborn",
        xaxis_title="Coleta",
        yaxis_title="Quantidade",
    )
    malote_chart = fig_mal.to_html(full_html=False, div_id="contabil-malote-chart")
    labels_rel = list(relatorios_grouped.keys())
    counts_rel = [len(relatorios_grouped[l]) for l in labels_rel]
    fig_rel = go.Figure(
        data=[go.Bar(x=labels_rel, y=counts_rel, marker_color=qualitative.Pastel)]
    )
    fig_rel.update_layout(
        title_text="Controle de Relatórios (Contábil)",
        template="seaborn",
        xaxis_title="Relatório",
        yaxis_title="Quantidade",
    )
    relatorios_chart = fig_rel.to_html(
        full_html=False, div_id="contabil-relatorios-chart"
    )
    return render_template(
        "admin/relatorio_contabil.html",
        importacao_chart=import_chart,
        envio_chart=envio_chart,
        malote_chart=malote_chart,
        relatorios_chart=relatorios_chart,
        empresas_por_import=import_grouped,
        empresas_por_envio=envio_grouped,
        empresas_por_malote=malote_grouped,
        empresas_por_relatorios=relatorios_grouped,
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
    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels,
                values=counts,
                hole=0.4,
                marker=dict(
                    colors=qualitative.Pastel, line=dict(color="#FFFFFF", width=2)
                ),
                textinfo="label+percent",
            )
        ]
    )
    fig.update_layout(
        title_text="Usuários por tipo e status",
        template="seaborn",
        legend=dict(orientation="h", y=-0.1, x=0.5, xanchor="center"),
    )
    chart_div = fig.to_html(full_html=False, div_id="user-role-chart")
    return render_template(
        "admin/relatorio_usuarios.html",
        chart_div=chart_div,
        users_by_slice=grouped,
    )


@app.route("/logout", methods=["GET"])
@login_required
def logout():
    """Log out the current user."""
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
    tag_query = Tag.query.order_by(Tag.nome)
    tag_list = tag_query.all()
    form.tags.choices = [(t.id, t.nome) for t in tag_list]
    edit_form.tags.choices = [(t.id, t.nome) for t in tag_list]
    show_inactive = request.args.get("show_inactive") in ("1", "on")
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
                    if new_password:
                        if new_password != confirm_new_password:
                            edit_password_error = "As senhas devem ser iguais."
                        else:
                            editing_user.set_password(new_password)

                    if edit_password_error:
                        flash(edit_password_error, "danger")
                    else:
                        db.session.commit()
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
                        flash("Tag atualizada com sucesso!", "success")
                        return redirect(url_for("list_users", open_tag_modal="1"))

    users_query = User.query
    if not show_inactive:
        users_query = users_query.filter_by(ativo=True)
    users = users_query.order_by(User.ativo.desc(), User.name).all()
    return render_template(
        "list_users.html",
        users=users,
        form=form,
        edit_form=edit_form,
        tag_create_form=tag_create_form,
        tag_edit_form=tag_edit_form,
        edit_tag=edit_tag,
        tag_list=tag_list,
        show_inactive=show_inactive,
        open_tag_modal=open_tag_modal,
        open_user_modal=open_user_modal,
        open_edit_modal=open_edit_modal,
        editing_user=editing_user,
        editing_user_id=editing_user_id,
        edit_password_error=edit_password_error,
    )


@app.route("/admin/online-users")
@admin_required
def online_users():
    """List users active within the last five minutes."""
    cutoff = datetime.utcnow() - timedelta(minutes=5)
    users = (
        User.query.options(joinedload(User.tags))
        .filter(User.last_seen >= cutoff)
        .order_by(User.name)
        .all()
    )
    return render_template("admin/online_users.html", users=users)


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


@app.route("/tasks/overview")
@admin_required
def tasks_overview():
    """Kanban view of all tasks grouped by status."""
    assigned_param = (request.args.get("assigned_by_me", "") or "").lower()
    assigned_by_me = assigned_param in {"1", "true", "on", "yes"}
    query = (
        Task.query.join(Tag)
        .filter(Task.parent_id.is_(None), ~Tag.nome.in_(EXCLUDED_TASK_TAGS))
    )
    if assigned_by_me:
        query = query.filter(Task.created_by == current_user.id)
    tasks = (
        query.options(
            joinedload(Task.tag),
            joinedload(Task.assignee),
            joinedload(Task.finisher),
            joinedload(Task.status_history),
            joinedload(Task.children).joinedload(Task.assignee),
            joinedload(Task.children).joinedload(Task.finisher),
            joinedload(Task.children).joinedload(Task.tag),
            joinedload(Task.children).joinedload(Task.status_history),
        )
        .order_by(Task.due_date)
        .all()
    )
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
    )


@app.route("/tasks/new", methods=["GET", "POST"])
@login_required
def tasks_new():
    """Form to create a new task or subtask."""
    parent_id = request.args.get("parent_id", type=int)
    parent_task = Task.query.get(parent_id) if parent_id else None
    if parent_task and parent_task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    is_admin = current_user.role == "admin"
    requested_tag_id = request.args.get("tag_id", type=int)
    choices: list[tuple[int, str]] = []
    ti_tag: Tag | None = None
    ti_tag_id: int | None = None
    if not is_admin:
        ti_tag = _get_ti_tag()
        if not ti_tag:
            abort(403)
        ti_tag_id = ti_tag.id
        if parent_task and parent_task.tag_id != ti_tag_id:
            abort(403)
        if requested_tag_id and requested_tag_id != ti_tag_id:
            requested_tag_id = ti_tag_id

    form = TaskForm()
    tag = parent_task.tag if parent_task else None
    if parent_task:
        form.parent_id.data = parent_task.id
        form.tag_id.choices = [(parent_task.tag_id, parent_task.tag.nome)]
        form.tag_id.data = parent_task.tag_id
        form.tag_id.render_kw = {"disabled": True}
        users = [u for u in parent_task.tag.users if u.ativo]
        form.assigned_to.choices = [(0, "Sem responsável")] + [
            (u.id, u.name) for u in users
        ]
    else:
        if is_admin:
            tags_query = (
                Tag.query.filter(~Tag.nome.in_(EXCLUDED_TASK_TAGS)).order_by(Tag.nome)
            )
            choices = [(t.id, t.nome) for t in tags_query.all()]
        else:
            choices = [(ti_tag_id, ti_tag.nome)] if ti_tag else []
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
            if tag:
                users = [u for u in tag.users if u.ativo]
                form.assigned_to.choices = [(0, "Sem responsável")] + [
                    (u.id, u.name) for u in users
                ]
            else:
                form.assigned_to.choices = [(0, "Sem responsável")]
        else:
            form.assigned_to.choices = [(0, "Sem responsável")]
    if form.validate_on_submit():
        tag_id = parent_task.tag_id if parent_task else form.tag_id.data
        if not parent_task and (tag is None or tag.id != tag_id):
            tag = Tag.query.get(tag_id)
        if tag is None:
            abort(400)
        if not is_admin and ti_tag_id and tag.id != ti_tag_id:
            abort(403)
        assignee_id = form.assigned_to.data or None
        task = Task(
            title=form.title.data,
            description=form.description.data,
            tag_id=tag_id,
            priority=TaskPriority(form.priority.data),
            due_date=form.due_date.data,
            created_by=current_user.id,
            parent_id=parent_id,
            assigned_to=assignee_id,
        )
        if task.assigned_to and task.assigned_to == current_user.id:
            task._skip_assignment_notification = True
        db.session.add(task)
        db.session.commit()
        flash("Tarefa criada com sucesso!", "success")
        return redirect(url_for("tasks_sector", tag_id=tag_id))
    if parent_task:
        cancel_url = url_for("tasks_sector", tag_id=parent_task.tag_id)
    elif is_admin:
        cancel_url = url_for("tasks_overview")
    else:
        selected_tag_id = form.tag_id.data
        if not selected_tag_id:
            if choices:
                selected_tag_id = choices[0][0]
            else:
                selected_tag_id = ti_tag_id
        cancel_url = url_for("tasks_sector", tag_id=selected_tag_id)
    return render_template("tasks_new.html", form=form, parent_task=parent_task, cancel_url=cancel_url)


@app.route("/tasks/users/<int:tag_id>")
@admin_required
def tasks_users(tag_id):
    """Return active users for a given tag."""
    tag = Tag.query.get_or_404(tag_id)
    users = [
        {"id": u.id, "name": u.name}
        for u in tag.users
        if u.ativo
    ]
    return jsonify(users)


@app.route("/tasks/sector/<int:tag_id>")
@login_required
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
    query = Task.query.filter(Task.tag_id == tag_id, Task.parent_id.is_(None))
    if assigned_to_me:
        query = query.filter(Task.assigned_to == current_user.id)
    tasks = (
        query.options(
            joinedload(Task.tag),
            joinedload(Task.assignee),
            joinedload(Task.finisher),
            joinedload(Task.status_history),
            joinedload(Task.children).joinedload(Task.assignee),
            joinedload(Task.children).joinedload(Task.finisher),
            joinedload(Task.children).joinedload(Task.tag),
            joinedload(Task.children).joinedload(Task.status_history),
        )
        .order_by(Task.due_date)
        .all()
    )
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
        )
    else:
        tag = None
        if current_user.role == "admin":
            query = Task.query.filter(
                Task.parent_id.is_(None), Task.status == TaskStatus.DONE
            )
        else:
            tag_ids = _get_accessible_tag_ids(current_user)
            if not tag_ids:
                query = None
            else:
                query = Task.query.filter(
                    Task.parent_id.is_(None),
                    Task.status == TaskStatus.DONE,
                    Task.tag_id.in_(tag_ids),
                )
    if query is not None:
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
    return render_template(
        "tasks_history.html",
        tag=tag,
        tasks=tasks,
        assigned_to_me=assigned_to_me,
        assigned_by_me=assigned_by_me,
    )


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
        )
        .get_or_404(task_id)
    )
    if task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    if not _can_user_access_tag(task.tag, current_user):
        abort(403)
    priority_labels = {"low": "Baixa", "medium": "Média", "high": "Alta"}
    priority_order = ["low", "medium", "high"]
    cancel_url = url_for("tasks_history", tag_id=task.tag_id)
    return render_template(
        "tasks_view.html",
        task=task,
        priority_labels=priority_labels,
        priority_order=priority_order,
        cancel_url=cancel_url,
    )


@app.route("/tasks/<int:task_id>/status", methods=["POST"])
@login_required
def update_task_status(task_id):
    """Update a task status and record its history."""
    task = Task.query.get_or_404(task_id)
    if task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    if not _can_user_access_tag(task.tag, current_user):
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
        }
        if new_status not in allowed.get(task.status, set()):
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
        db.session.add(history)
        db.session.commit()
    return jsonify({"success": True})


def _delete_task_recursive(task: Task) -> None:
    """Delete a task and all of its subtasks recursively."""

    for child in list(task.children or []):
        _delete_task_recursive(child)
    TaskStatusHistory.query.filter_by(task_id=task.id).delete(synchronize_session=False)
    TaskNotification.query.filter_by(task_id=task.id).delete(synchronize_session=False)
    db.session.delete(task)


@app.route("/tasks/<int:task_id>/delete", methods=["POST"])
@admin_required
def delete_task(task_id):
    """Remove a task from the system, including its subtasks and history."""

    task = Task.query.get_or_404(task_id)
    if task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    _delete_task_recursive(task)
    db.session.commit()
    return jsonify({"success": True})
