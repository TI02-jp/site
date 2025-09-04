"""Flask application factory and common utilities."""

import os
from flask import Flask, request, redirect, session
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from flask_login import LoginManager, current_user
from dotenv import load_dotenv
from datetime import datetime, timedelta
from markupsafe import Markup, escape
from app.utils.security import sanitize_html

load_dotenv()

app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+mysqlconnector://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}/{os.getenv('DB_NAME')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024  # 2 MB upload limit
app.config['ENFORCE_HTTPS'] = os.getenv('ENFORCE_HTTPS') == '1'
app.config['SESSION_COOKIE_SECURE'] = app.config['ENFORCE_HTTPS']
app.config['REMEMBER_COOKIE_SECURE'] = app.config['ENFORCE_HTTPS']
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)
app.config['REMEMBER_COOKIE_REFRESH_EACH_REQUEST'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['PREFERRED_URL_SCHEME'] = 'https' if app.config['ENFORCE_HTTPS'] else 'http'

csrf = CSRFProtect(app)
db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = 'login'


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
