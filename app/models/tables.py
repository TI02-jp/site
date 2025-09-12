"""Database models used by the application."""

import json
from sqlalchemy.types import TypeDecorator, String
from app import db
from datetime import datetime
from zoneinfo import ZoneInfo
from app.services.google_calendar import get_calendar_timezone
from enum import Enum

# Timezone for timestamp fields
# Default application timezone
SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")
# Timezone used for calendar-related timestamps
CALENDAR_TZ = get_calendar_timezone()
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

# Association table linking users to tag records
user_tags = db.Table(
    'user_tags',
    db.Column('user_id', db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('tags.id', ondelete='CASCADE'), primary_key=True)
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
    is_master = db.Column(db.Boolean, default=False)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    tags = db.relationship('Tag', secondary=user_tags, backref=db.backref('users', lazy=True))
    google_id = db.Column(db.String(255), unique=True)
    google_refresh_token = db.Column(db.String(255))

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


class Tag(db.Model):
    """Represents a tag for user categorization."""
    __tablename__ = 'tags'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    nome = db.Column(db.String(100), nullable=False, unique=True)

    def __repr__(self):
        return f"<Tag {self.nome}>"


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


class ReuniaoStatus(str, Enum):
    """Enumeration of possible meeting states."""
    AGENDADA = "agendada"
    EM_ANDAMENTO = "em andamento"
    REALIZADA = "realizada"


class Reuniao(db.Model):
    """Meeting scheduled in the system."""
    __tablename__ = 'reunioes'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    inicio = db.Column(db.DateTime(timezone=True), nullable=False)
    fim = db.Column(db.DateTime(timezone=True), nullable=False)
    assunto = db.Column(db.String(50), nullable=False)
    descricao = db.Column(db.Text)
    meet_link = db.Column(db.String(255))
    google_event_id = db.Column(db.String(255))
    status = db.Column(
        db.Enum(ReuniaoStatus, name="reuniao_status"),
        nullable=False,
        default=ReuniaoStatus.AGENDADA,
    )
    criador_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    data_criacao = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(CALENDAR_TZ),
    )

    participantes = db.relationship(
        'ReuniaoParticipante',
        backref='reuniao',
        cascade='all, delete-orphan',
        lazy=True,
    )
    criador = db.relationship('User', foreign_keys=[criador_id])


class ReuniaoParticipante(db.Model):
    """Participant linked to a meeting."""
    __tablename__ = 'reuniao_participantes'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    reuniao_id = db.Column(
        db.Integer, db.ForeignKey('reunioes.id', ondelete='CASCADE'), nullable=False
    )
    id_usuario = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    username_usuario = db.Column(db.String(255), nullable=False)
    status_participacao = db.Column(db.String(20), nullable=False, default='pendente')
    data_criacao = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(CALENDAR_TZ),
    )

    usuario = db.relationship('User')


class TaskStatus(Enum):
    """Enumeration of possible task states."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"


class TaskPriority(Enum):
    """Enumeration of task priority levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Task(db.Model):
    """Represents a task assigned to a specific tag/sector."""
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.Enum(TaskStatus), nullable=False, default=TaskStatus.PENDING)
    priority = db.Column(db.Enum(TaskPriority), nullable=False, default=TaskPriority.MEDIUM)
    due_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    tag_id = db.Column(db.Integer, db.ForeignKey("tags.id"), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("tasks.id"))
    assigned_to = db.Column(db.Integer, db.ForeignKey("users.id"))
    completed_by = db.Column(db.Integer, db.ForeignKey("users.id"))

    tag = db.relationship("Tag")
    creator = db.relationship("User")
    assignee = db.relationship("User", foreign_keys=[assigned_to])
    finisher = db.relationship("User", foreign_keys=[completed_by])
    children = db.relationship(
        "Task", backref=db.backref("parent", remote_side=[id]), lazy="joined"
    )

    @property
    def progress(self) -> int:
        """Return completion percentage based on direct subtasks."""
        if not self.children:
            return 100 if self.status == TaskStatus.DONE else 0
        total = len(self.children)
        completed = len([c for c in self.children if c.status == TaskStatus.DONE])
        return int((completed / total) * 100)

    def __repr__(self):
        return f"<Task {self.title}>"


class TaskStatusHistory(db.Model):
    """Tracks changes to task statuses over time."""
    __tablename__ = "task_status_history"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(
        db.Integer, db.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    from_status = db.Column(db.Enum(TaskStatus))
    to_status = db.Column(db.Enum(TaskStatus), nullable=False)
    changed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    changed_by = db.Column(db.Integer, db.ForeignKey("users.id"))

    task = db.relationship(
        "Task", backref=db.backref("status_history", lazy=True, cascade="all, delete-orphan")
    )
    user = db.relationship("User")

    def __repr__(self):
        return f"<TaskStatusHistory task={self.task_id} {self.from_status}->{self.to_status}>"

