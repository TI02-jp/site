"""Add start and end time to general calendar events"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0d9d3bb1c2f0"
down_revision = "7b3d2c5e7ab1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "general_calendar_events",
        sa.Column("start_time", sa.Time(), nullable=True),
    )
    op.add_column(
        "general_calendar_events",
        sa.Column("end_time", sa.Time(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("general_calendar_events", "end_time")
    op.drop_column("general_calendar_events", "start_time")
