"""
Add summary column to operational procedures.

Revision ID: c4d5e6f7a8b9
Revises: a3b4c5d6e7f8
Create Date: 2024-07-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c4d5e6f7a8b9"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("operational_procedures") as batch_op:
        batch_op.add_column(
            sa.Column("summary", sa.Text(), nullable=False, server_default="")
        )

    op.execute(
        sa.text(
            "UPDATE operational_procedures SET summary = description WHERE summary = ''"
        )
    )

    with op.batch_alter_table("operational_procedures") as batch_op:
        batch_op.alter_column("summary", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("operational_procedures") as batch_op:
        batch_op.drop_column("summary")
