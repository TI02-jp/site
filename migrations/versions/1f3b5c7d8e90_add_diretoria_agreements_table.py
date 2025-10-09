"""add diretoria agreements table

Revision ID: 1f3b5c7d8e90
Revises: 8f3aa2e55b15
Create Date: 2024-06-13 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1f3b5c7d8e90"
down_revision = "8f3aa2e55b15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "diretoria_agreements",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_diretoria_agreements_user_id"),
    )


def downgrade() -> None:
    op.drop_table("diretoria_agreements")
