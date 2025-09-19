"""Split course schedule into start and end columns"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "5aaf1e2d4c9b"
down_revision = "8f3aa2e55b15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Introduce schedule start/end columns and migrate existing values."""

    op.add_column("courses", sa.Column("schedule_start", sa.Time(), nullable=True))
    op.add_column("courses", sa.Column("schedule_end", sa.Time(), nullable=True))

    courses = sa.Table(
        "courses",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("schedule", sa.Time()),
        sa.Column("schedule_start", sa.Time()),
        sa.Column("schedule_end", sa.Time()),
    )

    bind = op.get_bind()
    results = bind.execute(sa.select(courses.c.id, courses.c.schedule)).all()
    for course_id, schedule in results:
        bind.execute(
            courses.update()
            .where(courses.c.id == course_id)
            .values(schedule_start=schedule, schedule_end=schedule)
        )

    op.drop_column("courses", "schedule")

    op.alter_column("courses", "schedule_start", existing_type=sa.Time(), nullable=False)
    op.alter_column("courses", "schedule_end", existing_type=sa.Time(), nullable=False)


def downgrade() -> None:
    """Restore the single schedule column."""

    op.add_column("courses", sa.Column("schedule", sa.Time(), nullable=True))

    courses = sa.Table(
        "courses",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("schedule", sa.Time()),
        sa.Column("schedule_start", sa.Time()),
        sa.Column("schedule_end", sa.Time()),
    )

    bind = op.get_bind()
    results = bind.execute(
        sa.select(courses.c.id, courses.c.schedule_start, courses.c.schedule_end)
    ).all()
    for course_id, start, end in results:
        bind.execute(
            courses.update()
            .where(courses.c.id == course_id)
            .values(schedule=start or end)
        )

    op.alter_column("courses", "schedule", existing_type=sa.Time(), nullable=False)
    op.drop_column("courses", "schedule_end")
    op.drop_column("courses", "schedule_start")
