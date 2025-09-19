"""Convert course workload and schedule to time columns"""

from __future__ import annotations

from datetime import datetime, time as time_cls

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8f3aa2e55b15"
down_revision = "c5fd6f35ce45"
branch_labels = None
depends_on = None


def _normalize_to_time_string(value: object) -> str:
    """Return a HH:MM:SS string for the provided workload/schedule value."""

    if value in (None, ""):
        return "00:00:00"
    if isinstance(value, time_cls):
        return value.replace(second=0, microsecond=0).strftime("%H:%M:%S")
    if isinstance(value, str):
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                parsed = datetime.strptime(value, fmt).time()
                return parsed.replace(second=0, microsecond=0).strftime("%H:%M:%S")
            except ValueError:
                continue
    return "00:00:00"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"]: column for column in inspector.get_columns("courses")}

    workload_type = columns.get("workload", {}).get("type")
    schedule_type = columns.get("schedule", {}).get("type")

    if isinstance(workload_type, sa.Time) and isinstance(schedule_type, sa.Time):
        # Nothing to do if the columns are already of type TIME.
        return

    courses_table = sa.table(
        "courses",
        sa.column("id", sa.Integer),
        sa.column("workload", sa.String(length=50)),
        sa.column("schedule", sa.String(length=100)),
    )

    rows = bind.execute(
        sa.select(
            courses_table.c.id,
            courses_table.c.workload,
            courses_table.c.schedule,
        )
    ).all()
    for row in rows:
        bind.execute(
            courses_table.update()
            .where(courses_table.c.id == row.id)
            .values(
                workload=_normalize_to_time_string(row.workload),
                schedule=_normalize_to_time_string(row.schedule),
            )
        )

    with op.batch_alter_table("courses", schema=None) as batch_op:
        batch_op.alter_column(
            "workload",
            existing_type=sa.String(length=50),
            type_=sa.Time(),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "schedule",
            existing_type=sa.String(length=100),
            type_=sa.Time(),
            existing_nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"]: column for column in inspector.get_columns("courses")}

    workload_type = columns.get("workload", {}).get("type")
    schedule_type = columns.get("schedule", {}).get("type")

    if not (isinstance(workload_type, sa.Time) and isinstance(schedule_type, sa.Time)):
        # Columns are already string-like, so no downgrade is required.
        return

    with op.batch_alter_table("courses", schema=None) as batch_op:
        batch_op.alter_column(
            "workload",
            existing_type=sa.Time(),
            type_=sa.String(length=50),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "schedule",
            existing_type=sa.Time(),
            type_=sa.String(length=100),
            existing_nullable=False,
        )
