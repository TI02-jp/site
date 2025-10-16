"""Database models used by the application."""

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

from flask_login import UserMixin
from sqlalchemy import event, inspect, select
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.types import TypeDecorator, String, Time
from werkzeug.security import generate_password_hash, check_password_hash

from app import db
from app.services.google_calendar import get_calendar_timezone


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".log"}


@dataclass(frozen=True)
class LegacyAnnouncementAttachment:
    """Simple wrapper exposing attachment attributes for legacy rows."""

    file_path: str
    original_name: str | None = None
    mime_type: str | None = None
    id: int | None = None

    @property
    def extension(self) -> str:
        """Return the lower-case extension for the stored file."""

        _, extension = os.path.splitext(self.file_path or "")
        return extension.lower()

    @property
    def is_image(self) -> bool:
        """Return ``True`` when the attachment is an image."""

        if self.mime_type and self.mime_type.startswith("image/"):
            return True
        return self.extension in IMAGE_EXTENSIONS

    @property
    def is_pdf(self) -> bool:
        """Return ``True`` when the attachment is a PDF."""

        if self.mime_type:
            return self.mime_type == "application/pdf"
        return self.extension == ".pdf"

    @property
    def is_text(self) -> bool:
        """Return ``True`` when the attachment is a plain-text file."""

        if self.mime_type and self.mime_type.startswith("text/"):
            return True
        return self.extension in TEXT_EXTENSIONS

    @property
    def display_name(self) -> str:
        """Return a human friendly name for the attachment."""

        return self.original_name or os.path.basename(self.file_path or "")

# Timezone for timestamp fields
# Default application timezone
SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")
# Timezone used for calendar-related timestamps
CALENDAR_TZ = get_calendar_timezone()


def _normalize_utc(dt: datetime) -> datetime:
    """Return ``dt`` converted to an aware UTC timestamp."""

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_sao_paulo(dt: datetime | None) -> datetime | None:
    """Return ``dt`` converted to the São Paulo timezone."""

    if dt is None:
        return None
    return _normalize_utc(dt).astimezone(SAO_PAULO_TZ)

# Association table linking users to tag records
user_tags = db.Table(
    'user_tags',
    db.Column('user_id', db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('tags.id', ondelete='CASCADE'), primary_key=True)
)

# Association table linking courses to course tag records
course_tag_links = db.Table(
    'course_tag_links',
    db.Column('course_id', db.Integer, db.ForeignKey('courses.id', ondelete='CASCADE'), primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('course_tags.id', ondelete='CASCADE'), primary_key=True),
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


def _coerce_time(value: time | str | timedelta | None) -> time | None:
    """Return ``datetime.time`` objects parsed from legacy values."""

    if value is None:
        return None
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                parsed = datetime.strptime(raw, fmt).time()
                return parsed.replace(second=0, microsecond=0)
            except ValueError:
                continue
        return None
    if isinstance(value, timedelta):
        total_seconds = int(value.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return time(hour=hours % 24, minute=minutes, second=seconds)
    return None


class TolerantTime(TypeDecorator):
    """TIME column that tolerates legacy string payloads on read/write."""

    impl = Time
    cache_ok = True

    def process_bind_param(self, value, dialect):  # type: ignore[override]
        return _coerce_time(value)

    def process_result_value(self, value, dialect):  # type: ignore[override]
        return _coerce_time(value)

    def result_processor(self, dialect, coltype):  # type: ignore[override]
        """Return a processor resilient to raw string payloads from MySQL."""

        impl_processor = super().result_processor(dialect, coltype)

        def process(value):
            if value is None:
                return None
            if isinstance(value, str):
                coerced = _coerce_time(value)
                if coerced is not None:
                    return coerced
            if impl_processor is None:
                return _coerce_time(value)
            try:
                processed = impl_processor(value)
            except AttributeError:
                # MySQL may attempt to treat raw strings as timedeltas.
                coerced = _coerce_time(value)
                if coerced is not None:
                    return coerced
                raise
            return _coerce_time(processed)

        return process

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


class AccessLink(db.Model):
    """Shortcut button available inside the access hub categories."""

    __tablename__ = "access_links"

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False)
    label = db.Column(db.String(100), nullable=False)
    url = db.Column(db.String(255), nullable=False)
    description = db.Column(db.String(255))
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    created_by = db.relationship("User", backref=db.backref("access_links", lazy=True))

    def __repr__(self):
        return f"<AccessLink {self.category}:{self.label}>"


class AnnouncementAttachment(db.Model):
    """Attachment stored for an :class:`Announcement`."""

    __tablename__ = "announcement_attachments"

    id = db.Column(db.Integer, primary_key=True)
    announcement_id = db.Column(
        db.Integer,
        db.ForeignKey("announcements.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_path = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255))
    mime_type = db.Column(db.String(128))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<AnnouncementAttachment {self.original_name or self.file_path}>"

    @property
    def extension(self) -> str:
        """Return the lower-case file extension for the attachment."""

        _, extension = os.path.splitext(self.file_path or "")
        return extension.lower()

    @property
    def is_image(self) -> bool:
        """Return ``True`` when the attachment is an image."""

        if self.mime_type and self.mime_type.startswith("image/"):
            return True
        return self.extension in IMAGE_EXTENSIONS

    @property
    def is_pdf(self) -> bool:
        """Return ``True`` when the attachment is a PDF document."""

        if self.mime_type:
            return self.mime_type == "application/pdf"
        return self.extension == ".pdf"

    @property
    def is_text(self) -> bool:
        """Return ``True`` when the attachment is a plain-text document."""

        if self.mime_type and self.mime_type.startswith("text/"):
            return True
        return self.extension in TEXT_EXTENSIONS

    @property
    def display_name(self) -> str:
        """Return a user-friendly attachment name."""

        return self.original_name or os.path.basename(self.file_path or "")


class NotificationType(str, Enum):
    """Type of notification stored in :class:`TaskNotification`."""

    TASK = "task"
    ANNOUNCEMENT = "announcement"


class Announcement(db.Model):
    """Internal announcement shared with all authenticated users."""

    __tablename__ = "announcements"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    subject = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text, nullable=False)
    attachment_path = db.Column(db.String(255))
    attachment_name = db.Column(db.String(255))
    created_by_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    created_by = db.relationship(
        "User",
        backref=db.backref("announcements", lazy=True, cascade="all, delete-orphan"),
    )
    attachments = db.relationship(
        "AnnouncementAttachment",
        backref="announcement",
        cascade="all, delete-orphan",
        order_by="AnnouncementAttachment.created_at.asc()",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Announcement {self.subject!r} on {self.date:%Y-%m-%d}>"

    @property
    def attachments_for_display(self) -> list[AnnouncementAttachment | LegacyAnnouncementAttachment]:
        """Return attachments to render, including legacy single files."""

        if self.attachments:
            return list(self.attachments)
        if self.attachment_path:
            return [
                LegacyAnnouncementAttachment(
                    file_path=self.attachment_path,
                    original_name=self.attachment_name,
                )
            ]
        return []

    @property
    def primary_attachment(self) -> AnnouncementAttachment | LegacyAnnouncementAttachment | None:
        """Return the first attachment available for quick previews."""

        attachments = self.attachments_for_display
        if attachments:
            return attachments[0]
        return None

    @property
    def attachment_is_image(self) -> bool:
        """Return ``True`` when the stored primary attachment is an image."""

        primary = self.primary_attachment
        return bool(primary and getattr(primary, "is_image", False))

    @property
    def attachment_extension(self) -> str:
        """Return the lower-case file extension for the primary attachment."""

        primary = self.primary_attachment
        if not primary:
            return ""
        return getattr(primary, "extension", "")

    @property
    def attachment_is_pdf(self) -> bool:
        """Return ``True`` when the stored primary attachment is a PDF document."""

        primary = self.primary_attachment
        return bool(primary and getattr(primary, "is_pdf", False))

    @property
    def created_at_sao_paulo(self) -> datetime | None:
        """Return the creation timestamp converted to the São Paulo timezone."""

        return _to_sao_paulo(self.created_at)

    def sync_legacy_attachment_fields(self) -> None:
        """Keep legacy attachment columns aligned with the stored attachments."""

        active_attachments = [
            attachment
            for attachment in self.attachments
            if not inspect(attachment).deleted
        ]

        primary = active_attachments[0] if active_attachments else None
        if primary:
            self.attachment_path = getattr(primary, "file_path", None)
            display_name = getattr(primary, "original_name", None) or getattr(
                primary, "display_name", None
            )
            self.attachment_name = display_name
            return

        self.attachment_path = None
        self.attachment_name = None


class Course(db.Model):
    """Internal training course available in the knowledge hub."""

    __tablename__ = "courses"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(150), nullable=False)
    instructor = db.Column(db.String(150), nullable=False)
    sectors = db.Column(db.Text, nullable=False)
    participants = db.Column(db.Text, nullable=False)
    workload = db.Column(TolerantTime(), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    schedule_start = db.Column(TolerantTime(), nullable=False)
    schedule_end = db.Column(TolerantTime(), nullable=False)
    completion_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="planejado")
    observation = db.Column(db.Text, nullable=True)
    tags = db.relationship(
        'CourseTag',
        secondary='course_tag_links',
        back_populates='courses',
        lazy='selectin',
    )

    def __repr__(self):
        return f"<Course {self.name} ({self.status})>"


class CourseTag(db.Model):
    """Represents a reusable tag for classifying courses."""

    __tablename__ = 'course_tags'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(80), nullable=False, unique=True)

    courses = db.relationship(
        'Course',
        secondary='course_tag_links',
        back_populates='tags',
    )

    def __repr__(self):
        return f"<CourseTag {self.name}>"


class DiretoriaEvent(db.Model):
    """Event planning record for Diretoria JP."""

    __tablename__ = "diretoria_events"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    event_type = db.Column(db.String(30), nullable=False)
    event_date = db.Column(db.Date, nullable=False)
    description = db.Column(db.Text)
    audience = db.Column(db.String(20), nullable=False)
    participants = db.Column(db.Integer, nullable=False, default=0)
    services = db.Column(db.JSON, nullable=False, default=dict)
    total_cost = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    photos = db.Column(db.JSON, nullable=True, default=list)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    created_by = db.relationship("User", backref=db.backref("diretoria_events", lazy=True))

    def __repr__(self):
        return f"<DiretoriaEvent {self.name} ({self.event_type})>"


class DiretoriaAgreement(db.Model):
    """Stores agreements and notes associated with Diretoria JP users."""

    __tablename__ = "diretoria_agreements"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    title = db.Column(db.String(150), nullable=False)
    agreement_date = db.Column(
        db.Date,
        nullable=False,
        default=date.today,
    )
    description = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(SAO_PAULO_TZ),
        nullable=False,
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(SAO_PAULO_TZ),
        onupdate=lambda: datetime.now(SAO_PAULO_TZ),
        nullable=False,
    )

    user = db.relationship(
        "User",
        backref=db.backref(
            "diretoria_agreements",
            cascade="all, delete-orphan",
            lazy="dynamic",
        ),
    )

    def __repr__(self) -> str:
        return f"<DiretoriaAgreement id={self.id} user={self.user_id}>"


class Session(db.Model):
    """Shared user session for Python and PHP applications."""
    __tablename__ = "sessions"
    __table_args__ = (
        db.Index('idx_sessions_user_id', 'user_id'),
        db.Index('idx_sessions_last_activity', 'last_activity'),
    )

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
    contatos = db.Column(JsonString(255))

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

    AGENDADA = "AGENDADA"
    EM_ANDAMENTO = "EM_ANDAMENTO"
    REALIZADA = "REALIZADA"
    ADIADA = "ADIADA"
    CANCELADA = "CANCELADA"


def default_meet_settings() -> dict[str, bool]:
    """Return the default configuration applied to new Google Meet rooms."""

    return {
        "quick_access_enabled": True,
        "mute_on_join": False,
        "allow_chat": True,
        "allow_screen_share": True,
    }


class ReuniaoRecorrenciaTipo(str, Enum):
    """Enumeration of recurrence types for meetings."""

    NENHUMA = "NENHUMA"
    DIARIA = "DIARIA"
    SEMANAL = "SEMANAL"
    QUINZENAL = "QUINZENAL"
    MENSAL = "MENSAL"
    ANUAL = "ANUAL"


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
    course_id = db.Column(
        db.Integer,
        db.ForeignKey('courses.id', ondelete='SET NULL'),
        nullable=True,
    )
    meet_host_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True,
    )
    meet_settings = db.Column(
        MutableDict.as_mutable(db.JSON),
        nullable=False,
        default=default_meet_settings,
    )
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
    # Campos de recorrência
    recorrencia_tipo = db.Column(
        db.Enum(ReuniaoRecorrenciaTipo, name="reuniao_recorrencia_tipo"),
        nullable=False,
        default=ReuniaoRecorrenciaTipo.NENHUMA,
    )
    recorrencia_fim = db.Column(db.Date, nullable=True)
    recorrencia_grupo_id = db.Column(db.String(36), nullable=True)
    recorrencia_dias_semana = db.Column(db.String(20), nullable=True)  # Ex: "1,3,5" para segunda, quarta, sexta

    participantes = db.relationship(
        'ReuniaoParticipante',
        backref='reuniao',
        cascade='all, delete-orphan',
        lazy=True,
    )
    criador = db.relationship('User', foreign_keys=[criador_id])
    meet_host = db.relationship('User', foreign_keys=[meet_host_id])
    course = db.relationship('Course', backref=db.backref('meetings', lazy=True))


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


class GeneralCalendarEvent(db.Model):
    """Company-wide calendar event managed within the application."""

    __tablename__ = "general_calendar_events"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=True)
    end_time = db.Column(db.Time, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(CALENDAR_TZ),
        nullable=False,
    )

    participants = db.relationship(
        "GeneralCalendarEventParticipant",
        backref="event",
        cascade="all, delete-orphan",
        lazy=True,
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])


class GeneralCalendarEventParticipant(db.Model):
    """Participant assigned to a general calendar event."""

    __tablename__ = "general_calendar_event_participants"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    event_id = db.Column(
        db.Integer,
        db.ForeignKey("general_calendar_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    user_name = db.Column(db.String(255), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(CALENDAR_TZ),
        nullable=False,
    )

    user = db.relationship("User")


class TaskStatus(Enum):
    """Enumeration of possible task states."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"


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
    completed_at = db.Column(db.DateTime)
    tag_id = db.Column(db.Integer, db.ForeignKey("tags.id"), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("tasks.id"))
    assigned_to = db.Column(db.Integer, db.ForeignKey("users.id"))
    completed_by = db.Column(db.Integer, db.ForeignKey("users.id"))

    tag = db.relationship("Tag")
    creator = db.relationship("User", foreign_keys=[created_by])
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


    @property
    def started_at(self):
        """Return the most recent timestamp when the task entered "in progress"."""
        history = getattr(self, "status_history", None)
        if not history:
            return None
        latest = max(
            (
                _normalize_utc(entry.changed_at)
                for entry in history
                if entry.to_status == TaskStatus.IN_PROGRESS and entry.changed_at
            ),
            default=None,
        )
        return _to_sao_paulo(latest)

    @property
    def finished_at(self):
        """Return the recorded completion timestamp for the task."""
        if self.completed_at:
            return _to_sao_paulo(self.completed_at)
        history = getattr(self, "status_history", None)
        if not history:
            return None
        latest = max(
            (
                _normalize_utc(entry.changed_at)
                for entry in history
                if entry.to_status == TaskStatus.DONE and entry.changed_at
            ),
            default=None,
        )
        return _to_sao_paulo(latest)


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


class TaskNotification(db.Model):
    """Notification emitted for tasks or announcements."""

    __tablename__ = "task_notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    task_id = db.Column(
        db.Integer, db.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True
    )
    announcement_id = db.Column(
        db.Integer,
        db.ForeignKey("announcements.id", ondelete="CASCADE"),
        nullable=True,
    )
    type = db.Column(
        db.String(20),
        nullable=False,
        default=NotificationType.TASK.value,
        server_default=NotificationType.TASK.value,
    )
    message = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    read_at = db.Column(db.DateTime)

    user = db.relationship("User")
    task = db.relationship("Task")
    announcement = db.relationship("Announcement")

    @property
    def is_read(self) -> bool:
        """Return ``True`` when the notification has been acknowledged."""

        return self.read_at is not None

    def __repr__(self):
        return (
            f"<TaskNotification type={self.type} task={self.task_id} "
            f"announcement={self.announcement_id} user={self.user_id}>"
        )


class OperationalProcedure(db.Model):
    """Operational procedure with rich description supporting images."""

    __tablename__ = "operational_procedures"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    created_by = db.relationship(
        "User", backref=db.backref("operational_procedures", lazy=True)
    )

    def __repr__(self):
        return f"<OperationalProcedure {self.title}>"


def _get_assignment_context(connection, task: Task) -> tuple[str, str | None]:
    """Return the task title and related tag name for assignment messaging."""

    title = (task.title or "Nova tarefa").strip() or "Nova tarefa"
    tag_name = None
    if task.tag_id:
        tag_name = connection.execute(
            select(Tag.nome).where(Tag.id == task.tag_id)
        ).scalar_one_or_none()
    return title, tag_name


def _build_assignment_message(title: str, tag_name: str | None) -> str:
    """Return a human-friendly notification message for a task assignment."""

    if tag_name:
        return f'Tarefa "{title}" atribuída no setor {tag_name}.'
    return f'Tarefa "{title}" atribuída a você.'


def _create_task_assignment_notification(connection, task: Task, assignee_id: int) -> None:
    """Persist a ``TaskNotification`` for the given assignment event."""

    if not assignee_id:
        if hasattr(task, "_skip_assignment_notification"):
            delattr(task, "_skip_assignment_notification")
        return

    if getattr(task, "_skip_assignment_notification", False):
        delattr(task, "_skip_assignment_notification")
        return

    title, tag_name = _get_assignment_context(connection, task)
    message = _build_assignment_message(title, tag_name)
    connection.execute(
        TaskNotification.__table__.insert().values(
            user_id=assignee_id,
            task_id=task.id,
            type=NotificationType.TASK.value,
            message=(message[:255] if message else None),
            created_at=datetime.utcnow(),
        )
    )
    if hasattr(task, "_skip_assignment_notification"):
        delattr(task, "_skip_assignment_notification")


@event.listens_for(Task, "after_insert")
def _task_assignment_after_insert(mapper, connection, target):
    """Emit a notification when a new task is created with an assignee."""

    if target.assigned_to:
        _create_task_assignment_notification(connection, target, target.assigned_to)


@event.listens_for(Task, "after_update")
def _task_assignment_after_update(mapper, connection, target):
    """Emit notifications whenever a task is reassigned to a user."""

    state = inspect(target)
    history = state.attrs.assigned_to.history
    if not history.has_changes():
        if hasattr(target, "_skip_assignment_notification"):
            delattr(target, "_skip_assignment_notification")
        return

    new_assignee = next((value for value in history.added if value is not None), None)
    if not new_assignee:
        if hasattr(target, "_skip_assignment_notification"):
            delattr(target, "_skip_assignment_notification")
        return

    _create_task_assignment_notification(connection, target, new_assignee)


def _build_completion_message(title: str, completer_name: str, tag_name: str | None) -> str:
    """Return a human-friendly notification message for task completion."""

    if tag_name:
        return f'Tarefa "{title}" concluída por {completer_name} no setor {tag_name}.'
    return f'Tarefa "{title}" concluída por {completer_name}.'


def _notify_creator_on_completion(connection, task: Task, completer_id: int) -> None:
    """Notify the task creator when the task is completed by another user."""

    # Don't notify if creator is the same as completer or no creator exists
    if not task.created_by or task.created_by == completer_id:
        return

    # Get task title
    title = (task.title or "Tarefa").strip() or "Tarefa"

    # Get completer name
    completer = connection.execute(
        select(User.name).where(User.id == completer_id)
    ).scalar_one_or_none()

    # Get tag name if exists
    tag_name = None
    if task.tag_id:
        tag_name = connection.execute(
            select(Tag.nome).where(Tag.id == task.tag_id)
        ).scalar_one_or_none()

    # Build message
    completer_name = completer or "Usuário"
    message = _build_completion_message(title, completer_name, tag_name)

    # Create notification
    connection.execute(
        TaskNotification.__table__.insert().values(
            user_id=task.created_by,
            task_id=task.id,
            type=NotificationType.TASK.value,
            message=message[:255] if message else None,
            created_at=datetime.utcnow(),
        )
    )


@event.listens_for(Task, "after_update")
def _task_completion_notification(mapper, connection, target):
    """Notify task creator when task is marked as completed."""

    state = inspect(target)
    status_history = state.attrs.status.history

    # Check if status has changed
    if not status_history.has_changes():
        return

    # Check if status changed TO "DONE"
    new_status = next((s for s in status_history.added if s is not None), None)
    if new_status != TaskStatus.DONE:
        return

    # Check if it wasn't already DONE (don't notify on re-completion)
    old_status = next((s for s in status_history.deleted if s is not None), None)
    if old_status == TaskStatus.DONE:
        return

    # Notify creator
    if target.completed_by:
        _notify_creator_on_completion(connection, target, target.completed_by)

