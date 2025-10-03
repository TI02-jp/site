"""Add content and attachment name to announcements."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text


# revision identifiers, used by Alembic.
revision = "e2d5f74ad36c"
down_revision = "b6f1a4d8e9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("announcements", sa.Column("content", sa.Text(), nullable=True))
    op.add_column(
        "announcements",
        sa.Column("attachment_name", sa.String(length=255), nullable=True),
    )

    op.execute(text("UPDATE announcements SET content = '' WHERE content IS NULL"))

    op.alter_column("announcements", "content", existing_type=sa.Text(), nullable=False)


def downgrade() -> None:
    op.drop_column("announcements", "attachment_name")
    op.drop_column("announcements", "content")
