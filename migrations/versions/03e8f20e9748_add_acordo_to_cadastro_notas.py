"""add_acordo_to_cadastro_notas

Revision ID: 03e8f20e9748
Revises: cde12a1b9391
Create Date: 2025-10-28 14:29:11.784345

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '03e8f20e9748'
down_revision = 'cde12a1b9391'
branch_labels = None
depends_on = None


def upgrade():
    # Add 'acordo' column to cadastro_notas table
    with op.batch_alter_table('cadastro_notas', schema=None) as batch_op:
        batch_op.add_column(sa.Column('acordo', sa.String(length=100), nullable=True))


def downgrade():
    # Remove 'acordo' column from cadastro_notas table
    with op.batch_alter_table('cadastro_notas', schema=None) as batch_op:
        batch_op.drop_column('acordo')
