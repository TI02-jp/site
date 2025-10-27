"""Add observacao to notas_debito

Revision ID: cde12a1b9391
Revises: ff75f46e6ea5
Create Date: 2025-10-27 16:54:31.433600

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'cde12a1b9391'
down_revision = 'ff75f46e6ea5'
branch_labels = None
depends_on = None


def upgrade():
    # Add observacao column to notas_debito table
    op.add_column('notas_debito', sa.Column('observacao', sa.Text(), nullable=True))


def downgrade():
    # Remove observacao column from notas_debito table
    op.drop_column('notas_debito', 'observacao')
