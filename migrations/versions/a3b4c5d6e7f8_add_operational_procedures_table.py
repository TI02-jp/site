"""add operational procedures table

Revision ID: a3b4c5d6e7f8
Revises: c5fd6f35ce45
Create Date: 2024-07-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a3b4c5d6e7f8"
down_revision = "c5fd6f35ce45"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operational_procedures",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_operational_procedures_title",
        "operational_procedures",
        ["title"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_operational_procedures_title",
        table_name="operational_procedures",
    )
    op.drop_table("operational_procedures")
