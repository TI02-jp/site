import os
from flask import Flask, request, redirect
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
app.config['PREFERRED_URL_SCHEME'] = 'https' if app.config['ENFORCE_HTTPS'] else 'http'

csrf = CSRFProtect(app)
db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = 'login'


@app.before_request
def _enforce_https():
    if app.config['ENFORCE_HTTPS'] and request.headers.get('X-Forwarded-Proto', request.scheme) != 'https':
        url = request.url.replace('http://', 'https://', 1)
        return redirect(url, code=301)


@app.before_request
def _update_last_seen():
    if current_user.is_authenticated:
        now = datetime.utcnow()
        if not current_user.last_seen or (now - current_user.last_seen) > timedelta(minutes=1):
            current_user.last_seen = now
            db.session.commit()


@app.after_request
def _set_security_headers(response):
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
    from app.models.tables import User  # importa aqui para evitar circular import
    return User.query.get(int(user_id))

@app.context_processor
def inject_now():
    return {'now': datetime.now}

@app.template_global()
def render_badge_list(items, classes, icon, placeholder):
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
