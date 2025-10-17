"""Rename description column on operational procedures.

Revision ID: 1a2b3c4d5e67
Revises: ea2e86d74a68
Create Date: 2025-10-16 13:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1a2b3c4d5e67"
down_revision = "ea2e86d74a68"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "operational_procedures",
        "description",
        new_column_name="descricao",
        existing_type=sa.Text(),
    )


def downgrade():
    op.alter_column(
        "operational_procedures",
        "descricao",
        new_column_name="description",
        existing_type=sa.Text(),
    )
