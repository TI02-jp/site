"""Database models used by the application."""

import json
from sqlalchemy.types import TypeDecorator, String
from app import db
from datetime import datetime
from zoneinfo import ZoneInfo

# Timezone for timestamp fields
SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

# Association table linking users to setores (tags)
user_setores = db.Table(
    'user_setores',
    db.Column('user_id', db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    db.Column('setor_id', db.Integer, db.ForeignKey('setores.id', ondelete='CASCADE'), primary_key=True)
)

class JsonString(TypeDecorator):
    """Store JSON as a serialized string."""
    impl = String

    def __init__(self, length=255, **kwargs):
        super().__init__(length=length, **kwargs)

    def process_bind_param(self, value, dialect):
        """Serialize Python objects to JSON before storing in the DB."""
        if value is not None:
            return json.dumps(value)
        return None

    def process_result_value(self, value, dialect):
        """Deserialize JSON strings from the DB into Python objects."""
        if value is not None:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return None
        return None

class User(db.Model, UserMixin):
    """Application user account."""
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    ativo = db.Column(db.Boolean, default=True)
    role = db.Column(db.String(20), default='user')
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    setores = db.relationship('Setor', secondary=user_setores, backref=db.backref('users', lazy=True))

    def set_password(self, password):
        """Hash and store the user's password."""
        self.password = generate_password_hash(password)

    def check_password(self, password):
        """Validate a plaintext password against the stored hash."""
        return check_password_hash(self.password, password)

    @property
    def is_active(self):
        """Return True if the user is marked as active."""
        return self.ativo

class Post(db.Model):
    """User-generated post."""
    __tablename__ = "posts"
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    id_user = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    user = db.relationship('User', backref=db.backref('posts', lazy=True))


class Session(db.Model):
    """Shared user session for Python and PHP applications."""
    __tablename__ = "sessions"

    session_id = db.Column(db.String(128), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    session_data = db.Column(db.JSON)
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.Text)
    last_activity = db.Column(
        db.DateTime,
        default=lambda: datetime.now(SAO_PAULO_TZ),
        onupdate=lambda: datetime.now(SAO_PAULO_TZ),
        nullable=False,
    )

    user = db.relationship('User', backref=db.backref('sessions', lazy=True))


class Consultoria(db.Model):
    """Stores consulting company credentials."""
    __tablename__ = 'consultorias'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    nome = db.Column(db.String(100), nullable=False)
    usuario = db.Column(db.String(100))
    senha = db.Column(db.String(255))

    def __repr__(self):
        return f"<Consultoria {self.nome}>"


class Setor(db.Model):
    """Represents a business sector."""
    __tablename__ = 'setores'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    nome = db.Column(db.String(100), nullable=False)

    def __repr__(self):
        return f"<Setor {self.nome}>"


class Inclusao(db.Model):
    """Records FAQ entries with questions and answers."""
    __tablename__ = 'inclusoes'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    data = db.Column(db.Date)
    usuario = db.Column(db.String(100))
    setor = db.Column(db.String(100))
    consultoria = db.Column(db.String(100))
    assunto = db.Column(db.String(200))
    pergunta = db.Column(db.Text)
    resposta = db.Column(db.Text)

    @property
    def data_formatada(self):
        return self.data.strftime('%d/%m/%Y') if self.data else ''

    def __repr__(self):
        return f"<Inclusao {self.assunto}>"


class MeetingRoomEvent(db.Model):
    """Scheduled meeting room event."""
    __tablename__ = 'meeting_room_events'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    user = db.relationship('User', backref=db.backref('meeting_room_events', lazy=True))

    def __repr__(self):
        return f"<MeetingRoomEvent {self.title}>"

class Empresa(db.Model):
    """Company registered in the system."""
    __tablename__ = 'tbl_empresas'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    nome_empresa = db.Column(db.String(100), nullable=False)
    cnpj = db.Column(db.String(18), unique=True, nullable=False)
    atividade_principal = db.Column(db.String(100))
    data_abertura = db.Column(db.Date, nullable=False)
    socio_administrador = db.Column(db.String(100))
    tributacao = db.Column(db.String(50))
    regime_lancamento = db.Column(JsonString(50), nullable=False)
    sistemas_consultorias = db.Column(JsonString(255))
    sistema_utilizado = db.Column(db.String(150))
    acessos = db.Column(JsonString(255))
    observacao_acessos = db.Column(db.String(200))
    codigo_empresa = db.Column(db.String(100), nullable=False)

    def __repr__(self):
        return f"<Empresa {self.nome_empresa}>"

class Departamento(db.Model):
    """Department belonging to a company."""
    __tablename__ = 'departamentos'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('tbl_empresas.id'), nullable=False)
    tipo = db.Column(db.String(50), nullable=False)
    responsavel = db.Column(db.String(100))
    descricao = db.Column(db.String(200))
    formas_importacao = db.Column(JsonString(255))
    forma_movimento = db.Column(db.String(20))
    envio_digital = db.Column(JsonString(200))
    envio_fisico = db.Column(JsonString(200))
    malote_coleta = db.Column(db.String(20))
    observacao_movimento = db.Column(db.String(200))
    observacao_importacao = db.Column(db.String(200))
    observacao_contato = db.Column(db.String(200))
    metodo_importacao = db.Column(JsonString(255))
    controle_relatorios = db.Column(JsonString(255))
    observacao_controle_relatorios = db.Column(db.String(200))
    contatos = db.Column(JsonString(255))
    data_envio = db.Column(db.String(100))
    registro_funcionarios = db.Column(db.String(200))
    ponto_eletronico = db.Column(db.String(200))
    pagamento_funcionario = db.Column(db.String(200))
    particularidades_texto = db.Column(db.Text)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(SAO_PAULO_TZ),
        onupdate=lambda: datetime.now(SAO_PAULO_TZ),
    )
    empresa = db.relationship('Empresa', backref=db.backref('departamentos', lazy=True))

    def __repr__(self):
        return f"<Departamento {self.tipo} - Empresa {self.empresa_id}>"

