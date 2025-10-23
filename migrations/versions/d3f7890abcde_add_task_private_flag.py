"""Add is_private flag to tasks table.

Revision ID: d3f7890abcde
Revises: 57536966e6b7
Create Date: 2025-10-23 14:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d3f7890abcde"
down_revision = "57536966e6b7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tasks",
        sa.Column(
            "is_private",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.execute("UPDATE tasks SET is_private = 0 WHERE is_private IS NULL")
    op.alter_column("tasks", "is_private", server_default=None)


def downgrade():
    op.drop_column("tasks", "is_private")
