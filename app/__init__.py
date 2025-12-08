"""Flask application factory and common utilities."""

import os
import threading
import time
import logging
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from flask import Flask, request, redirect, session, g, jsonify
from flask_login import LoginManager, current_user
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_compress import Compress
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv
from markupsafe import Markup, escape
from sqlalchemy.exc import SQLAlchemyError

from app.utils.security import sanitize_html
from app.extensions.cache import cache, init_cache
from app.utils.performance_middleware import (
    get_request_tracker,
    register_performance_middleware,
    track_commit_end,
    track_commit_start,
)

load_dotenv()

SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")

app = Flask(__name__)

logger = logging.getLogger(__name__)

db_user = os.getenv('DB_USER')
db_password = os.getenv('DB_PASSWORD')
db_host = os.getenv('DB_HOST')
db_name = os.getenv('DB_NAME')

missing_db_vars = [
    name for name, value in (
        ('DB_USER', db_user),
        ('DB_PASSWORD', db_password),
        ('DB_HOST', db_host),
        ('DB_NAME', db_name),
    )
    if value is None
]

if missing_db_vars:
    logger.warning(
        "Variáveis de banco ausentes (%s); usando SQLite local em modo de fallback.",
        ", ".join(missing_db_vars),
    )
    os.makedirs(app.instance_path, exist_ok=True)
    fallback_db = os.path.join(app.instance_path, 'app.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{fallback_db}"
else:
    if db_password == "":
        logger.warning("DB_PASSWORD está vazio; conectando ao MySQL sem senha (apenas recomendado para desenvolvimento local).")
    database_uri = f"mysql+pymysql://{db_user}:{db_password}@{db_host}/{db_name}"
    app.config['SQLALCHEMY_DATABASE_URI'] = database_uri

secret_key = os.getenv("SECRET_KEY")
if not secret_key or secret_key == "umsegredoforteaqui123":
    secret_key = secrets.token_urlsafe(32)
    logger.warning("SECRET_KEY não definida; gerando valor temporário apenas para ambiente local.")
app.config['SECRET_KEY'] = secret_key
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024 * 1024  # 5 GB (evita 413 por tamanho)
app.config['MAX_FORM_MEMORY_SIZE'] = app.config['MAX_CONTENT_LENGTH']
app.config['WYSIWYG_UPLOAD_SOFT_LIMIT_MB'] = int(os.getenv("WYSIWYG_UPLOAD_SOFT_LIMIT_MB", "512"))
app.config['ENFORCE_HTTPS'] = os.getenv('ENFORCE_HTTPS') == '1'
app.config['SESSION_COOKIE_SECURE'] = app.config['ENFORCE_HTTPS']
app.config['REMEMBER_COOKIE_SECURE'] = app.config['ENFORCE_HTTPS']
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['REMEMBER_COOKIE_HTTPONLY'] = True
# ``SameSite=None`` requires the ``Secure`` flag; falling back to ``Lax``
# when HTTPS is not enforced prevents browsers from rejecting the cookie
# entirely, which would break logins in development environments.
if app.config['ENFORCE_HTTPS']:
    app.config['SESSION_COOKIE_SAMESITE'] = 'None'
    app.config['REMEMBER_COOKIE_SAMESITE'] = 'None'
else:
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)
app.config['REMEMBER_COOKIE_REFRESH_EACH_REQUEST'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['PREFERRED_URL_SCHEME'] = 'https' if app.config['ENFORCE_HTTPS'] else 'http'
app.config['WTF_CSRF_TIME_LIMIT'] = 60 * 60 * 24  # 24 horas
app.config['WTF_CSRF_SSL_STRICT'] = app.config['ENFORCE_HTTPS']
app.config['SESSION_COOKIE_NAME'] = os.getenv('SESSION_COOKIE_NAME', 'jp_portal_session')
app.config.setdefault('SQLALCHEMY_ENGINE_OPTIONS', {})
app.config['SQLALCHEMY_ENGINE_OPTIONS'].setdefault('pool_pre_ping', True)
app.config['SQLALCHEMY_ENGINE_OPTIONS'].setdefault('pool_recycle', 1800)
# Otimizações de pool de conexões para produção
# Aumentado para suportar 64 Waitress threads com margem de segurança
app.config['SQLALCHEMY_ENGINE_OPTIONS'].setdefault('pool_size', 30)  # ↑ 20→30 conexões permanentes
app.config['SQLALCHEMY_ENGINE_OPTIONS'].setdefault('max_overflow', 50)  # ↑ 40→50 (total: 80 conexões)
app.config['SQLALCHEMY_ENGINE_OPTIONS'].setdefault('pool_timeout', 30)  # Timeout de 30s para obter conexão
app.config['SQLALCHEMY_ENGINE_OPTIONS'].setdefault('pool_use_lifo', True)  # LIFO = reutiliza conexões quentes (melhor para MySQL)
app.config['GOOGLE_CLIENT_ID'] = os.getenv('GOOGLE_CLIENT_ID')
app.config['GOOGLE_CLIENT_SECRET'] = os.getenv('GOOGLE_CLIENT_SECRET')
app.config['GOOGLE_REDIRECT_URI'] = os.getenv('GOOGLE_REDIRECT_URI')
app.config['GOOGLE_SERVICE_ACCOUNT_FILE'] = os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE')
app.config['GOOGLE_MEETING_ROOM_EMAIL'] = os.getenv('GOOGLE_MEETING_ROOM_EMAIL')
app.config['PORTAL_STATS_CACHE_TIMEOUT'] = int(os.getenv('PORTAL_STATS_CACHE_TIMEOUT', '300'))
app.config['NOTIFICATION_COUNT_CACHE_TIMEOUT'] = int(os.getenv('NOTIFICATION_COUNT_CACHE_TIMEOUT', '60'))
app.config['SLOW_REQUEST_THRESHOLD_MS'] = float(os.getenv('SLOW_REQUEST_THRESHOLD_MS', '750'))
app.config['MEETING_CALENDAR_PAST_DAYS'] = int(os.getenv('MEETING_CALENDAR_PAST_DAYS', '60'))
app.config['MEETING_CALENDAR_FUTURE_DAYS'] = int(os.getenv('MEETING_CALENDAR_FUTURE_DAYS', str(365 * 3)))
app.config['APP_VERSION'] = os.getenv('APP_VERSION')
app.config['PWA_VERSION'] = os.getenv('PWA_VERSION')

if not app.config['ENFORCE_HTTPS']:
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

csrf = CSRFProtect(app)
db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

init_cache(app)

# Rate limiting configuration for DDoS/brute-force protection
rate_limit_storage = os.getenv('RATELIMIT_STORAGE_URI')
if not rate_limit_storage:
    redis_url = os.getenv('REDIS_URL')
    rate_limit_storage = redis_url if redis_url else "memory://"

raw_default_limits = os.getenv('RATELIMIT_DEFAULT_LIMITS', '').strip()
default_limits = [limit.strip() for limit in raw_default_limits.split(',') if limit.strip()]

# Rate limiting configuration for DDoS/brute-force protection
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=default_limits or None,
    storage_uri=rate_limit_storage,
    strategy="fixed-window",
    headers_enabled=True,
)

# Compressão HTTP para reduzir tamanho das respostas em ~70%
compress = Compress(app)
app.config['COMPRESS_MIMETYPES'] = [
    'text/html',
    'text/css',
    'text/javascript',
    'application/javascript',
    'application/json',
    'text/xml',
    'application/xml',
]
app.config['COMPRESS_LEVEL'] = 6  # Nível de compressão (1-9, 6 é bom balanço)
app.config['COMPRESS_MIN_SIZE'] = 500  # Só comprime respostas > 500 bytes



# Import centralized cache for session tracking
from app.extensions.cache import cache as session_cache


@app.url_defaults
def add_cache_buster(endpoint, values):
    """Append a timestamp query parameter to static asset URLs."""
    if endpoint == 'static' and 'cb' not in values:
        values['cb'] = int(time.time())


@app.before_request
def _enforce_https():
    """Redirect incoming HTTP requests to HTTPS when enforcement is enabled."""
    if app.config['ENFORCE_HTTPS'] and request.headers.get('X-Forwarded-Proto', request.scheme) != 'https':
        url = request.url.replace('http://', 'https://', 1)
        return redirect(url, code=301)


@app.before_request
def _start_request_timer():
    """Store the high-resolution start time for slow request logging."""
    g.request_started_at = time.perf_counter()


@app.after_request
def _log_slow_requests(response):
    """Emit warnings for requests that exceed the configured threshold."""
    started_at = getattr(g, 'request_started_at', None)
    threshold_ms = app.config.get('SLOW_REQUEST_THRESHOLD_MS', 0) or 0
    if started_at is not None and threshold_ms > 0:
        duration_ms = (time.perf_counter() - started_at) * 1000
        endpoint = request.endpoint or 'unknown'
        if duration_ms >= threshold_ms and endpoint != 'static':
            user_id = current_user.get_id() if current_user.is_authenticated else 'anonymous'
            app.logger.warning(
                "Slow request: %s %s took %.1f ms (status=%s, user=%s, endpoint=%s, ip=%s)",
                request.method,
                request.path,
                duration_ms,
                response.status_code,
                user_id,
                endpoint,
                request.remote_addr,
            )
    return response


@app.before_request
def _update_session_activity():
    """Persist minimal session activity without touching ``users.last_seen``."""
    if request.endpoint in (
        'static',
        'ping',
        'health_check',
        'readiness_check',
        'liveness_check',
        'db_pool_status',
        'notifications_stream',
        'realtime_stream',
    ):
        return

    if not current_user.is_authenticated:
        return

    from app.models.tables import Session, SAO_PAULO_TZ

    now_utc = datetime.now(SAO_PAULO_TZ).replace(tzinfo=None)
    sid = session.get('sid')
    if not sid:
        return

    now_sp = datetime.now(SAO_PAULO_TZ).replace(tzinfo=None)
    user_agent = request.headers.get('User-Agent')
    ip_address = request.remote_addr

    cache_key = f"session_throttle:{sid}:{ip_address}:{user_agent}"
    last_update = session_cache.get(cache_key)
    if last_update and (now_utc - last_update).total_seconds() < 60:
        return

    session_cache.set(cache_key, now_utc, timeout=120)

    try:
        sess = db.session.query(Session).filter_by(session_id=sid).first()
        if sess:
            updates = {}
            if sess.last_activity != now_sp:
                updates['last_activity'] = now_sp
            if sess.ip_address != ip_address:
                updates['ip_address'] = ip_address
            if sess.user_agent != user_agent:
                updates['user_agent'] = user_agent

            if updates:
                db.session.query(Session).filter_by(session_id=sid).update(updates)
                g.session_updated = True
        else:
            sess = Session(
                session_id=sid,
                user_id=current_user.id,
                ip_address=ip_address,
                user_agent=user_agent,
                last_activity=now_sp,
                session_data={},
            )
            db.session.add(sess)
            g.session_updated = True
    except SQLAlchemyError:
        db.session.rollback()


@app.after_request
def _set_security_headers(response):
    """Apply security-related HTTP headers to responses."""
    allow_iframe_self = request.args.get("embed") == "1" and request.endpoint in {"visualizar_empresa"}
    frame_ancestors = "'self'" if allow_iframe_self else "'none'"
    x_frame_option = "SAMEORIGIN" if allow_iframe_self else "DENY"

    if app.config['ENFORCE_HTTPS'] and request.headers.get('X-Forwarded-Proto', request.scheme) == 'https':
        response.headers.setdefault(
            'Strict-Transport-Security',
            'max-age=31536000; includeSubDomains',
        )
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers['X-Frame-Options'] = x_frame_option
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy', 'geolocation=(), microphone=(), camera=()')
    response.headers.setdefault('X-XSS-Protection', '0')
    response.headers.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
    response.headers.setdefault('Cross-Origin-Resource-Policy', 'same-origin')

    csp = (
        "default-src 'self'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "img-src 'self' data: https:; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net data:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.quilljs.com https://cdn.jsdelivr.net; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://code.jquery.com https://cdn.quilljs.com https://cdn.plot.ly https://accounts.google.com https://apis.google.com; "
        "connect-src 'self' https://www.googleapis.com https://accounts.google.com; "
        f"frame-ancestors {frame_ancestors}; "
        "object-src 'none'; "
        "worker-src 'self' blob:; "
        "upgrade-insecure-requests"
    )
    response.headers['Content-Security-Policy'] = csp
    return response

# Importa rotas e modelos depois da criação do db
from app.models import tables
from app.controllers import routes, health
routes.register_blueprints(app)

@login_manager.user_loader
def load_user(user_id):
    """Load a :class:`User` instance for Flask-Login."""
    from app.models.tables import User  # importa aqui para evitar circular import
    return User.query.get(int(user_id))

@app.context_processor
def inject_now():
    """Inject current UTC time into templates as ``now()``."""
    return {'now': lambda: datetime.now(SAO_PAULO_TZ).replace(tzinfo=None)}


@app.context_processor
def inject_versions():
    """Expose portal and PWA version identifiers to templates."""
    return {
        "app_version": app.config.get("APP_VERSION", "local"),
        "pwa_version": app.config.get("PWA_VERSION", "local"),
    }


@app.template_filter('time_since')
def _time_since(value):
    """Return human-readable time elapsed since ``value``.

    If less than a minute has passed, returns ``agora``; otherwise returns
    ``X minuto(s) atrás`` with proper pluralization.
    """
    if not value:
        return 'agora'
    delta = datetime.now(SAO_PAULO_TZ).replace(tzinfo=None) - value
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return 'agora'
    minutes = seconds // 60
    if minutes == 1:
        return '1 minuto atrás'
    return f'{minutes} minutos atrás'

@app.template_global()
def render_badge_list(items, classes, icon, placeholder):
    """Render a list of strings as styled badges in templates."""
    if not items or not isinstance(items, (list, tuple)):
        return Markup(placeholder)
    badges = [
        f'<span class="{classes}"><i class="bi {icon} me-1"></i>{escape(item)}</span>'
        for item in items
    ]
    return Markup(' '.join(badges))


@app.template_filter('sanitize')
def _sanitize_filter(value):
    """Jinja filter to strip unsafe HTML and mark the result safe."""
    return Markup(sanitize_html(value))

with app.app_context():
    # Import models inside the application context so SQLAlchemy metadata
    # knows about every table before ``create_all``/inspector logic runs.
    from app.models import tables as _models  # noqa: F401

    db.create_all()

    try:
        inspector = sa.inspect(db.engine)

        if not inspector.has_table("diretoria_feedbacks"):
            diretoria_feedback_table = db.metadata.tables.get("diretoria_feedbacks")
            if diretoria_feedback_table is not None:
                try:
                    diretoria_feedback_table.create(bind=db.engine)
                except SQLAlchemyError as exc:
                    app.logger.warning(
                        "Falha ao criar tabela diretoria_feedbacks automaticamente: %s",
                        exc,
                    )

        meeting_columns = {col["name"] for col in inspector.get_columns("reunioes")}
        if "course_id" not in meeting_columns:
            with db.engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "ALTER TABLE reunioes ADD COLUMN course_id INTEGER NULL"
                    )
                )
                conn.execute(
                    sa.text(
                        """
                        ALTER TABLE reunioes
                        ADD CONSTRAINT fk_reunioes_course_id_courses
                        FOREIGN KEY (course_id) REFERENCES courses (id)
                        ON DELETE SET NULL
                        """
                    )
                )

        diretoria_columns = {
            col["name"] for col in inspector.get_columns("diretoria_events")
        }
        if "photos" not in diretoria_columns:
            with db.engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "ALTER TABLE diretoria_events ADD COLUMN photos JSON NULL"
                    )
                )

        if inspector.has_table("announcements"):
            announcement_columns = {
                col["name"] for col in inspector.get_columns("announcements")
            }
            if "content" not in announcement_columns:
                with db.engine.begin() as conn:
                    conn.execute(
                        sa.text(
                            "ALTER TABLE announcements ADD COLUMN content TEXT NULL"
                        )
                    )
                    conn.execute(
                        sa.text(
                            "UPDATE announcements SET content = '' WHERE content IS NULL"
                        )
                    )
                    conn.execute(
                        sa.text(
                            "ALTER TABLE announcements MODIFY COLUMN content TEXT NOT NULL"
                        )
                    )
            if "attachment_name" not in announcement_columns:
                with db.engine.begin() as conn:
                    conn.execute(
                        sa.text(
                            "ALTER TABLE announcements ADD COLUMN attachment_name VARCHAR(255) NULL"
                        )
                    )

        attachments_table_exists = inspector.has_table("announcement_attachments")
        if not attachments_table_exists and inspector.has_table("announcements"):
            with db.engine.begin() as conn:
                conn.execute(
                    sa.text(
                        """
                        CREATE TABLE announcement_attachments (
                            id INTEGER NOT NULL AUTO_INCREMENT,
                            announcement_id INTEGER NOT NULL,
                            file_path VARCHAR(255) NOT NULL,
                            original_name VARCHAR(255) NULL,
                            mime_type VARCHAR(128) NULL,
                            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            PRIMARY KEY (id),
                            CONSTRAINT fk_announcement_attachments_announcement_id_announcements
                                FOREIGN KEY (announcement_id) REFERENCES announcements (id)
                                ON DELETE CASCADE
                        )
                        """
                    )
                )
            attachments_table_exists = True

        if attachments_table_exists:
            attachment_columns = {
                col["name"] for col in inspector.get_columns("announcement_attachments")
            }
            if "mime_type" not in attachment_columns:
                with db.engine.begin() as conn:
                    conn.execute(
                        sa.text(
                            "ALTER TABLE announcement_attachments ADD COLUMN mime_type VARCHAR(128) NULL"
                        )
                    )
            if "created_at" not in attachment_columns:
                with db.engine.begin() as conn:
                    conn.execute(
                        sa.text(
                            """
                            ALTER TABLE announcement_attachments
                            ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                            """
                        )
                    )

        if attachments_table_exists and inspector.has_table("announcements"):
            with db.engine.begin() as conn:
                legacy_attachments = conn.execute(
                    sa.text(
                        """
                        SELECT a.id AS announcement_id,
                               a.attachment_path AS file_path,
                               a.attachment_name AS original_name,
                               a.created_at AS created_at
                        FROM announcements AS a
                        WHERE a.attachment_path IS NOT NULL
                          AND NOT EXISTS (
                              SELECT 1 FROM announcement_attachments aa
                              WHERE aa.announcement_id = a.id
                          )
                        """
                    )
                )

                for row in legacy_attachments.mappings():
                    conn.execute(
                        sa.text(
                            """
                            INSERT INTO announcement_attachments (
                                announcement_id,
                                file_path,
                                original_name,
                                mime_type,
                                created_at
                            ) VALUES (
                                :announcement_id,
                                :file_path,
                                :original_name,
                                NULL,
                                COALESCE(:created_at, NOW())
                            )
                            """
                        ),
                        {
                            "announcement_id": row["announcement_id"],
                            "file_path": row["file_path"],
                            "original_name": row["original_name"],
                            "created_at": row["created_at"],
                        },
                    )

        if inspector.has_table("task_notifications"):
            notification_columns = {
                col["name"] for col in inspector.get_columns("task_notifications")
            }
            if "type" not in notification_columns:
                with db.engine.begin() as conn:
                    conn.execute(
                        sa.text(
                            "ALTER TABLE task_notifications ADD COLUMN type VARCHAR(20) "
                            "NOT NULL DEFAULT 'task'"
                        )
                    )
                    conn.execute(
                        sa.text(
                            "UPDATE task_notifications SET type = 'task' WHERE type IS NULL"
                        )
                    )
            if "announcement_id" not in notification_columns and inspector.has_table(
                "announcements"
            ):
                existing_fks = inspector.get_foreign_keys("task_notifications")
                has_announcement_fk = any(
                    fk.get("constrained_columns") == ["announcement_id"]
                    for fk in existing_fks
                )
                with db.engine.begin() as conn:
                    conn.execute(
                        sa.text(
                            "ALTER TABLE task_notifications ADD COLUMN announcement_id INTEGER NULL"
                        )
                    )
                    if not has_announcement_fk:
                        conn.execute(
                            sa.text(
                                """
                                ALTER TABLE task_notifications
                                ADD CONSTRAINT fk_task_notifications_announcement_id_announcements
                                FOREIGN KEY (announcement_id) REFERENCES announcements (id)
                                ON DELETE CASCADE
                                """
                                )
                            )
            task_id_column = next(
                (col for col in inspector.get_columns("task_notifications") if col["name"] == "task_id"),
                None,
            )
            if task_id_column and not task_id_column.get("nullable", True):
                with db.engine.begin() as conn:
                    conn.execute(
                        sa.text(
                            "ALTER TABLE task_notifications MODIFY COLUMN task_id INTEGER NULL"
                        )
                    )

        if inspector.has_table("operational_procedures"):
            procedure_columns = {
                col["name"]: col for col in inspector.get_columns("operational_procedures")
            }

            descricao_column = procedure_columns.get("descricao")
            description_column = procedure_columns.get("description")

            if (
                db.engine.dialect.name == "mysql"
                and not descricao_column
                and description_column
            ):
                # Legacy databases still use the old ``description`` column name; rename it for compatibility.
                with db.engine.begin() as conn:
                    conn.execute(
                        sa.text(
                            "ALTER TABLE operational_procedures CHANGE COLUMN description descricao LONGTEXT NULL"
                        )
                    )
                descricao_column = description_column

            if (
                db.engine.dialect.name == "mysql"
                and descricao_column
                and "longtext" not in str(descricao_column["type"]).lower()
            ):
                with db.engine.begin() as conn:
                    conn.execute(
                        sa.text(
                            "ALTER TABLE operational_procedures MODIFY COLUMN descricao LONGTEXT NULL"
                        )
                    )

        # Migration: Add contatos column to tbl_empresas and migrate data from departamentos
        if inspector.has_table("tbl_empresas"):
            empresa_columns = {
                col["name"] for col in inspector.get_columns("tbl_empresas")
            }
            if "ativo" not in empresa_columns:
                with db.engine.begin() as conn:
                    conn.execute(
                        sa.text(
                            """
                            ALTER TABLE tbl_empresas
                            ADD COLUMN ativo TINYINT(1) NOT NULL DEFAULT 1
                            """
                        )
                    )
                    conn.execute(
                        sa.text(
                            "UPDATE tbl_empresas SET ativo = 1 WHERE ativo IS NULL"
                        )
                    )
            if "contatos" not in empresa_columns:
                with db.engine.begin() as conn:
                    # Add contatos column to tbl_empresas
                    conn.execute(
                        sa.text(
                            "ALTER TABLE tbl_empresas ADD COLUMN contatos VARCHAR(255) NULL"
                        )
                    )

                    # Migrate contatos from departamento fiscal to empresa
                    # Only migrate if departamentos table exists
                    if inspector.has_table("departamentos"):
                        conn.execute(
                            sa.text(
                                """
                                UPDATE tbl_empresas e
                                INNER JOIN departamentos d ON d.empresa_id = e.id
                                SET e.contatos = d.contatos
                                WHERE d.tipo = 'fiscal' AND d.contatos IS NOT NULL
                                """
                            )
                        )
    except SQLAlchemyError as exc:
        app.logger.warning(
            "Não foi possível garantir as colunas obrigatórias: %s", exc
        )

    # Setup performance middleware (needs to be inside app context to access db.engine)
    register_performance_middleware(app, db)

# Setup structured logging with rotation (after app context is ready)
from app.utils.logging_config import setup_logging, log_request_info
import time

setup_logging(app)

def _register_diagnostics_routes(flask_app: Flask) -> None:
    diagnostics_enabled = os.getenv("ENABLE_DIAGNOSTICS", "0") == "1"
    diagnostics_token = os.getenv("DIAGNOSTICS_TOKEN")

    if not diagnostics_enabled:
        return

    @flask_app.route("/_diagnostics/thread-state")
    def _diagnostics_thread_state():
        if diagnostics_token and request.headers.get("X-Diagnostics-Token") != diagnostics_token:
            return jsonify({"error": "unauthorized"}), 403

        threads = [
            {
                "name": thread.name,
                "ident": thread.ident,
                "daemon": thread.daemon,
                "alive": thread.is_alive(),
            }
            for thread in threading.enumerate()
        ]

        waitress_threads = [
            thread for thread in threads if "waitress" in (thread["name"] or "").lower()
        ]

        configured_threads = int(os.getenv("WAITRESS_THREADS", "64"))

        payload = {
            "thread_count": len(threads),
            "waitress_thread_count": len(waitress_threads),
            "configured_threads": configured_threads,
            "threads": threads,
        }
        return jsonify(payload)

_register_diagnostics_routes(app)

# Add request/response logging middleware
@app.before_request
def _log_request_start():
    """Record request start time for duration tracking."""
    g.request_start_time = time.perf_counter()

@app.after_request
def _log_request_end(response):
    """Log request completion with timing information."""
    tracker = get_request_tracker()
    if tracker:
        tracker.finish()
        duration_ms = tracker.total_duration_ms or 0.0
    elif hasattr(g, 'request_start_time'):
        duration_ms = (time.perf_counter() - g.request_start_time) * 1000
    else:
        duration_ms = 0.0
    request_id = getattr(g, "request_id", None)
    if request_id:
        response.headers.setdefault("X-Request-ID", request_id)
    log_request_info(request, response, duration_ms, request_id=request_id)
    return response

@app.teardown_appcontext
def _commit_session_updates(exception=None):
    """Commit session updates after request completes (non-blocking optimization)."""
    # Commit session updates (if any)
    if hasattr(g, 'session_updated') and g.session_updated:
        try:
            commit_started = track_commit_start()
            db.session.commit()
            track_commit_end(commit_started)
        except SQLAlchemyError as e:
            logger.warning("Failed to commit session update: %s", e)
            db.session.rollback()
        finally:
            db.session.remove()
