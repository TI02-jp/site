"""Flask application factory and common utilities."""

import os
import time
import logging
import secrets
from datetime import datetime, timedelta

import sqlalchemy as sa
from flask import Flask, request, redirect, session
from flask_login import LoginManager, current_user
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from dotenv import load_dotenv
from markupsafe import Markup, escape
from sqlalchemy.exc import SQLAlchemyError

from app.utils.security import sanitize_html

load_dotenv()

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
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024  # 2 MB upload limit
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
app.config['GOOGLE_CLIENT_ID'] = os.getenv('GOOGLE_CLIENT_ID')
app.config['GOOGLE_CLIENT_SECRET'] = os.getenv('GOOGLE_CLIENT_SECRET')
app.config['GOOGLE_REDIRECT_URI'] = os.getenv('GOOGLE_REDIRECT_URI')
app.config['GOOGLE_SERVICE_ACCOUNT_FILE'] = os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE')
app.config['GOOGLE_MEETING_ROOM_EMAIL'] = os.getenv('GOOGLE_MEETING_ROOM_EMAIL')

if not app.config['ENFORCE_HTTPS']:
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

csrf = CSRFProtect(app)
db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = 'login'


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
def _update_last_seen():
    """Update ``current_user.last_seen`` and session activity."""
    if current_user.is_authenticated:
        from app.models.tables import Session, SAO_PAULO_TZ

        now_sp = datetime.now(SAO_PAULO_TZ)
        current_user.last_seen = datetime.utcnow()

        sid = session.get('sid')
        if sid:
            sess = Session.query.get(sid)
            if sess:
                sess.last_activity = now_sp
                sess.ip_address = request.remote_addr
                sess.user_agent = request.headers.get('User-Agent')
                sess.session_data = dict(session)
            else:
                sess = Session(
                    session_id=sid,
                    user_id=current_user.id,
                    ip_address=request.remote_addr,
                    user_agent=request.headers.get('User-Agent'),
                    last_activity=now_sp,
                )
                db.session.add(sess)
        db.session.commit()


@app.after_request
def _set_security_headers(response):
    """Apply security-related HTTP headers to responses."""
    if app.config['ENFORCE_HTTPS'] and request.headers.get('X-Forwarded-Proto', request.scheme) == 'https':
        response.headers.setdefault(
            'Strict-Transport-Security',
            'max-age=31536000; includeSubDomains',
        )
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
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
        "frame-ancestors 'none'; "
        "object-src 'none'; "
        "worker-src 'self' blob:; "
        "upgrade-insecure-requests"
    )
    response.headers.setdefault('Content-Security-Policy', csp)
    return response

# Importa rotas e modelos depois da criação do db
from app.models import tables
from app.controllers import routes

@login_manager.user_loader
def load_user(user_id):
    """Load a :class:`User` instance for Flask-Login."""
    from app.models.tables import User  # importa aqui para evitar circular import
    return User.query.get(int(user_id))

@app.context_processor
def inject_now():
    """Inject current UTC time into templates as ``now()``."""
    return {'now': datetime.utcnow}


@app.template_filter('time_since')
def _time_since(value):
    """Return human-readable time elapsed since ``value``.

    If less than a minute has passed, returns ``agora``; otherwise returns
    ``X minuto(s) atrás`` with proper pluralization.
    """
    if not value:
        return 'agora'
    delta = datetime.utcnow() - value
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
    db.create_all()

    try:
        inspector = sa.inspect(db.engine)

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
    except SQLAlchemyError as exc:
        app.logger.warning(
            "Não foi possível garantir as colunas obrigatórias: %s", exc
        )
