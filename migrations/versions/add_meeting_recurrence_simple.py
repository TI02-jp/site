"""add meeting recurrence fields

Revision ID: add_meeting_recurrence
Revises: ac6cd670a66a
Create Date: 2025-10-13 16:50:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_meeting_recurrence'
down_revision = 'ac6cd670a66a'
branch_labels = None
depends_on = None


def upgrade():
    # Adicionar colunas de recorrência na tabela reunioes
    with op.batch_alter_table('reunioes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('recorrencia_tipo',
                                      sa.Enum('NENHUMA', 'DIARIA', 'SEMANAL', 'QUINZENAL', 'MENSAL', 'ANUAL',
                                             name='reuniao_recorrencia_tipo'),
                                      nullable=False,
                                      server_default='NENHUMA'))
        batch_op.add_column(sa.Column('recorrencia_fim', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('recorrencia_grupo_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('recorrencia_dias_semana', sa.String(length=20), nullable=True))


def downgrade():
    with op.batch_alter_table('reunioes', schema=None) as batch_op:
        batch_op.drop_column('recorrencia_dias_semana')
        batch_op.drop_column('recorrencia_grupo_id')
        batch_op.drop_column('recorrencia_fim')
        batch_op.drop_column('recorrencia_tipo')
