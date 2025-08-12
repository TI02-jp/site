import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from flask_login import LoginManager
from dotenv import load_dotenv
from datetime import datetime
from markupsafe import Markup
from config import Config

load_dotenv()

app = Flask(__name__)

database_url = os.getenv('DATABASE_URL') or (
    f"mysql+mysqlconnector://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@"
    f"{os.getenv('DB_HOST')}/{os.getenv('DB_NAME')}"
)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
app.config.from_object(Config)
Config.validate()

csrf = CSRFProtect(app)
db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Importa rotas e modelos depois da criação do db
from app.models import tables
from app.controllers import routes
if Config.ACESSORIAS_ENABLED:
    from app.controllers.acessorias import bp_acessorias
    app.register_blueprint(bp_acessorias)

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
    badges = [f'<span class="{classes}"><i class="bi {icon} me-1"></i>{item}</span>' for item in items]
    return Markup(' '.join(badges))

with app.app_context():
    db.create_all()
