"""add pautas column to reunioes

Revision ID: 5a9391b4ba25
Revises: add_ativo_to_empresas
Create Date: 2025-10-22 08:43:39.088311

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "5a9391b4ba25"
down_revision = "add_ativo_to_empresas"
branch_labels = None
depends_on = None


def upgrade():
    """Add the pautas text column to reunioes."""

    op.add_column("reunioes", sa.Column("pautas", sa.Text(), nullable=True))


def downgrade():
    """Drop the pautas column."""

    op.drop_column("reunioes", "pautas")

