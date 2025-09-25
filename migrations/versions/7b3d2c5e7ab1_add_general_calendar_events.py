"""add general calendar events tables"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7b3d2c5e7ab1"
down_revision = "9a3ec81bf08d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "general_calendar_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"],),
    )

    op.create_table(
        "general_calendar_event_participants",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("user_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint([
            "event_id"
        ], ["general_calendar_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],),
    )


def downgrade() -> None:
    op.drop_table("general_calendar_event_participants")
    op.drop_table("general_calendar_events")
