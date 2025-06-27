import json
from sqlalchemy.types import TypeDecorator, String
from app import db
from enum import Enum
from datetime import date, datetime
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


class Departamento(db.Model):
    __tablename__ = 'departamentos'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('tbl_empresas.id'), nullable=False)
    tipo = db.Column(db.String(50), nullable=False)
    responsavel = db.Column(db.String(100))
    descricao = db.Column(db.String(200))
    formas_importacao = db.Column(JsonString(255))
    link_prefeitura = db.Column(db.String(200))
    usuario_prefeitura = db.Column(db.String(100))
    senha_prefeitura = db.Column(db.String(100))
    forma_movimento = db.Column(db.String(20))
    envio_digital = db.Column(JsonString(200))
    envio_digital_fisico = db.Column(JsonString(200))
    observacao_movimento = db.Column(db.String(200))
    metodo_importacao = db.Column(db.String(20))
    observacao_importacao = db.Column(db.String(200))
    controle_relatorios = db.Column(JsonString(255))
    observacao_controle_relatorios = db.Column(db.String(200))
    contatos = db.Column(JsonString(255))
    data_envio = db.Column(db.String(100))
    registro_funcionarios = db.Column(db.String(200))
    ponto_eletronico = db.Column(db.String(200))
    pagamento_funcionario = db.Column(db.String(200))
    particularidades_texto = db.Column(db.Text)
    particularidades_imagens = db.Column(JsonString(255))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    empresa = db.relationship('Empresa', backref=db.backref('departamentos', lazy=True))

    def __repr__(self):
        return f"<Departamento {self.tipo} - Empresa {self.empresa_id}>"

