"""Remove summary column from operational procedures.

Revision ID: d3e4f5a6b7c8
Revises: c4d5e6f7a8b9
Create Date: 2024-07-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d3e4f5a6b7c8"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("operational_procedures") as batch_op:
        batch_op.drop_column("summary")


def downgrade() -> None:
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
