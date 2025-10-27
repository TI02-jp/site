"""Add notas tables only

Revision ID: ff75f46e6ea5
Revises: 0d86f3268345
Create Date: 2025-10-27 15:59:06.453859

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ff75f46e6ea5'
down_revision = '0d86f3268345'
branch_labels = None
depends_on = None


def upgrade():
    # Create notas_debito table
    op.create_table('notas_debito',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('data_emissao', sa.Date(), nullable=False),
        sa.Column('empresa', sa.String(length=255), nullable=False),
        sa.Column('notas', sa.Integer(), nullable=False),
        sa.Column('qtde_itens', sa.Integer(), nullable=False),
        sa.Column('valor_un', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('total', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('acordo', sa.String(length=100), nullable=True),
        sa.Column('forma_pagamento', sa.String(length=50), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create cadastro_notas table
    op.create_table('cadastro_notas',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('pix', sa.String(length=100), nullable=True),
        sa.Column('cadastro', sa.String(length=255), nullable=False),
        sa.Column('valor', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('forma_pagamento', sa.String(length=50), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('cadastro_notas')
    op.drop_table('notas_debito')
