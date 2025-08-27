"""Cria tabela setores

Revision ID: e57a4926ea16
Revises: 4bc81125fc31
Create Date: 2025-08-27 15:49:13.303364

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'e57a4926ea16'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'setores',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('nome', sa.String(length=100), nullable=False)
    )



def downgrade():
    op.drop_table('setores')
