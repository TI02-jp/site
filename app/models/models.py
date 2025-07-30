from app import db
from sqlalchemy.types import TypeDecorator, String
import json
from enum import Enum

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

class RegimeLancamentoEnum(Enum):
    CAIXA = 'CAIXA'
    COMPETENCIA = 'COMPETENCIA'

class Empresa(db.Model):
    __tablename__ = 'tbl_empresas'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    nome_empresa = db.Column(db.String(100), nullable=False)
    cnpj = db.Column(db.String(18), unique=True, nullable=False)
    atividade_principal = db.Column(db.String(100))
    data_abertura = db.Column(db.Date, nullable=False)
    socio_administrador = db.Column(db.String(100))
    tributacao = db.Column(db.String(50))
    regime_lancamento = db.Column(db.Enum(RegimeLancamentoEnum), nullable=False)
    sistemas_consultorias = db.Column(JsonString(500))
    sistema_utilizado = db.Column(db.String(150))
    codigo_empresa = db.Column(db.String(100), nullable=False)

    def __repr__(self):
        return f"<Empresa {self.nome_empresa}>"
