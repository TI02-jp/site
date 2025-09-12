from datetime import datetime
from enum import Enum
from app import db
from app.models.tables import Setor, User


class Prioridade(Enum):
    BAIXA = "baixa"
    MEDIA = "media"
    ALTA = "alta"
    CRITICA = "critica"


class StatusTarefa(Enum):
    PENDENTE = "pendente"
    ANDAMENTO = "andamento"
    CONCLUIDA = "concluida"
    BLOQUEADA = "bloqueada"


class MuralTarefa(db.Model):
    __tablename__ = "mural_tarefas"

    id = db.Column(db.Integer, primary_key=True)
    setor_id = db.Column(db.Integer, db.ForeignKey("setores.id"), nullable=False, index=True)
    titulo = db.Column(db.String(200), nullable=False)
    descricao = db.Column(db.Text)
    prioridade = db.Column(db.Enum(Prioridade), nullable=False, default=Prioridade.MEDIA, index=True)
    status = db.Column(db.Enum(StatusTarefa), nullable=False, default=StatusTarefa.PENDENTE, index=True)
    data_limite = db.Column(db.Date)
    criada_por_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    responsavel_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    setor = db.relationship("Setor")
    criada_por = db.relationship("User", foreign_keys=[criada_por_id])
    responsavel = db.relationship("User", foreign_keys=[responsavel_id])

    def set_status(self, novo_status: StatusTarefa, by_user: User | None):
        antigo = self.status
        if antigo == novo_status:
            return
        self.status = novo_status
        db.session.add(
            MuralTarefaHistorico(
                tarefa_id=self.id,
                status_antigo=antigo,
                status_novo=novo_status,
                alterado_por_id=by_user.id if by_user else None,
            )
        )


class MuralTarefaHistorico(db.Model):
    __tablename__ = "mural_tarefas_historico"

    id = db.Column(db.Integer, primary_key=True)
    tarefa_id = db.Column(db.Integer, db.ForeignKey("mural_tarefas.id"), nullable=False, index=True)
    status_antigo = db.Column(db.Enum(StatusTarefa), nullable=False)
    status_novo = db.Column(db.Enum(StatusTarefa), nullable=False)
    alterado_por_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    alterado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
