"""Add course tags and course-tag link tables"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d8a6f8c2b3e4"
down_revision = "a1b3c5d7e9f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create tables to support reusable course tags."""

    op.create_table(
        "course_tags",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=80), nullable=False, unique=True),
    )

    op.create_table(
        "course_tag_links",
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["course_tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("course_id", "tag_id"),
    )


def downgrade() -> None:
    """Drop course tag support tables."""

    op.drop_table("course_tag_links")
    op.drop_table("course_tags")
