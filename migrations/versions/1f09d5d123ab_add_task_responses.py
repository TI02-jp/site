"""Add task responses conversation tables

Revision ID: 1f09d5d123ab
Revises: e297fce9b964
Create Date: 2025-10-29 16:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1f09d5d123ab"
down_revision = "e297fce9b964"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_responses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_responses_task_id", "task_responses", ["task_id"])
    op.create_index("ix_task_responses_author_id", "task_responses", ["author_id"])
    op.alter_column(
        "task_responses",
        "created_at",
        server_default=None,
        existing_type=sa.DateTime(),
    )

    op.create_table(
        "task_response_participants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("last_read_at", sa.DateTime(), nullable=True),
        sa.Column("last_notified_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "user_id", name="uq_task_response_participants_task_user"),
    )
    op.create_index(
        "ix_task_response_participants_task_id",
        "task_response_participants",
        ["task_id"],
    )
    op.create_index(
        "ix_task_response_participants_user_id",
        "task_response_participants",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_response_participants_user_id", table_name="task_response_participants")
    op.drop_index("ix_task_response_participants_task_id", table_name="task_response_participants")
    op.drop_table("task_response_participants")
    op.drop_index("ix_task_responses_author_id", table_name="task_responses")
    op.drop_index("ix_task_responses_task_id", table_name="task_responses")
    op.drop_table("task_responses")
