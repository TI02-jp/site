import json
from sqlalchemy.types import TypeDecorator, String
from app import db
from enum import Enum
from datetime import date
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

# JsonString para salvar listas como JSON em String
class JsonString(TypeDecorator):
    impl = String

    def __init__(self, length=255, **kwargs):
        super().__init__(length=length, **kwargs)

    def process_bind_param(self, value, dialect):
        if value is not None:
            return json.dumps(value)
        return None

    def process_result_value(self, value, dialect):
        if value is not None:
            return json.loads(value)
        return None

class User(db.Model, UserMixin):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)

    role = db.Column(db.String(20), default='user')

    def set_password(self, password):
        self.password = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password, password)

class Post(db.Model):
    __tablename__ = "posts"
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    id_user = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    user = db.relationship('User', backref=db.backref('posts', lazy=True))

class RegimeLancamento(Enum):
    CAIXA = 'Caixa'
    COMPETENCIA = 'Competência'

class Empresa(db.Model):
    __tablename__ = 'tbl_empresas'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    NomeEmpresa = db.Column(db.String(100), nullable=False)
    CNPJ = db.Column(db.String(18), unique=True, nullable=False)
    AtividadePrincipal = db.Column(db.String(100))
    DataAbertura = db.Column(db.String(10), nullable=False)
    SocioAdministrador = db.Column(db.String(100))
    Tributacao = db.Column(db.String(50))
    RegimeLancamento = db.Column(db.Enum('CAIXA', 'COMPETENCIA'))
    SistemasConsultorias = db.Column(db.String(200))
    SistemaUtilizado = db.Column(db.String(150))
    CodigoEmpresa = db.Column(db.String(100), nullable=False)

    def __repr__(self):
        return f"<Empresa {self.NomeEmpresa}>"
