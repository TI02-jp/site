"""Cria tabela consultorias

Revision ID: 4bc81125fc31
Revises: f0ddbbea7d1f
Create Date: 2025-08-27 15:40:16.710343

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '4bc81125fc31'
down_revision = 'f0ddbbea7d1f'  # ou o ID da sua última migração válida
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'consultorias',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('nome', sa.String(length=100), nullable=False),
        sa.Column('usuario_id', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('senha', sa.String(length=255)),
    )


def downgrade():
    op.drop_table('consultorias')

