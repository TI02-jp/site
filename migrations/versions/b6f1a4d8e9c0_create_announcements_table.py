"""Create announcements table"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b6f1a4d8e9c0"
down_revision = "0d9d3bb1c2f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("attachment_path", sa.String(length=255), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
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
            ["created_by_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_announcements_date",
        "announcements",
        ["date"],
    )


def downgrade() -> None:
    op.drop_index("ix_announcements_date", table_name="announcements")
    op.drop_table("announcements")
