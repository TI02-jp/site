"""Cria tabela inclusoes

Revision ID: 54223df5d51a
Revises: 56aa3b1bf5a1
Create Date: 2024-12-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '54223df5d51a'
down_revision = '56aa3b1bf5a1'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        'inclusoes',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('data', sa.Date(), nullable=True),
        sa.Column('usuario', sa.String(length=100), nullable=True),
        sa.Column('setor', sa.String(length=100), nullable=True),
        sa.Column('consultoria', sa.String(length=100), nullable=True),
        sa.Column('assunto', sa.String(length=200), nullable=True),
        sa.Column('pergunta', sa.Text(), nullable=True),
        sa.Column('resposta', sa.Text(), nullable=True),
    )

def downgrade():
    op.drop_table('inclusoes')
