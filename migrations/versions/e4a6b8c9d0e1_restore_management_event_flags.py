"""restore management event flags"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e4a6b8c9d0e1"
down_revision = "2b3c4d5e6f78"
branch_labels = None
depends_on = None


def _column_exists(inspector: sa.engine.reflection.Inspector, table: str, column: str) -> bool:
    return any(col["name"] == column for col in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _column_exists(inspector, "management_events", "include_dinner") and not _column_exists(
        inspector, "management_events", "include_snack"
    ):
        op.alter_column(
            "management_events",
            "include_dinner",
            new_column_name="include_snack",
            existing_type=sa.Boolean(),
            existing_nullable=False,
        )

    inspector = sa.inspect(bind)

    if _column_exists(inspector, "management_events", "cost_dinner") and not _column_exists(
        inspector, "management_events", "cost_snack"
    ):
        op.alter_column(
            "management_events",
            "cost_dinner",
            new_column_name="cost_snack",
            existing_type=sa.Numeric(10, 2),
            existing_nullable=True,
        )

    inspector = sa.inspect(bind)

    if not _column_exists(inspector, "management_events", "attendees_internal"):
        op.add_column(
            "management_events",
            sa.Column(
                "attendees_internal",
                sa.Boolean(),
                nullable=False,
                server_default=sa.sql.expression.false(),
            ),
        )
        op.alter_column(
            "management_events",
            "attendees_internal",
            server_default=None,
        )

    if not _column_exists(inspector, "management_events", "attendees_external"):
        op.add_column(
            "management_events",
            sa.Column(
                "attendees_external",
                sa.Boolean(),
                nullable=False,
                server_default=sa.sql.expression.false(),
            ),
        )
        op.alter_column(
            "management_events",
            "attendees_external",
            server_default=None,
        )

    inspector = sa.inspect(bind)

    if not _column_exists(inspector, "management_events", "include_snack"):
        op.add_column(
            "management_events",
            sa.Column(
                "include_snack",
                sa.Boolean(),
                nullable=False,
                server_default=sa.sql.expression.false(),
            ),
        )
        op.alter_column("management_events", "include_snack", server_default=None)

    if not _column_exists(inspector, "management_events", "cost_snack"):
        op.add_column(
            "management_events",
            sa.Column("cost_snack", sa.Numeric(10, 2), nullable=True),
        )

    for deprecated in [
        "attendance_scope",
        "breakfast_items",
        "lunch_items",
        "dinner_items",
    ]:
        inspector = sa.inspect(bind)
        if _column_exists(inspector, "management_events", deprecated):
            op.drop_column("management_events", deprecated)

    inspector = sa.inspect(bind)

    if not _column_exists(inspector, "management_events", "event_total"):
        op.add_column(
            "management_events",
            sa.Column("event_total", sa.Numeric(10, 2), nullable=True),
        )

    if _column_exists(inspector, "management_events", "other_materials"):
        op.execute(
            sa.text(
                "UPDATE management_events SET other_materials = '[]' "
                "WHERE other_materials IS NULL"
            )
        )
    else:
        op.add_column(
            "management_events",
            sa.Column("other_materials", sa.JSON(), nullable=False, server_default="[]"),
        )
        op.alter_column("management_events", "other_materials", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for column in ["attendees_internal", "attendees_external", "include_snack", "cost_snack", "event_total"]:
        if _column_exists(inspector, "management_events", column):
            op.drop_column("management_events", column)

    if not _column_exists(inspector, "management_events", "include_dinner"):
        op.add_column(
            "management_events",
            sa.Column(
                "include_dinner",
                sa.Boolean(),
                nullable=False,
                server_default=sa.sql.expression.false(),
            ),
        )
        op.alter_column("management_events", "include_dinner", server_default=None)

    if not _column_exists(inspector, "management_events", "cost_dinner"):
        op.add_column(
            "management_events",
            sa.Column("cost_dinner", sa.Numeric(10, 2), nullable=True),
        )

    for column in ["attendance_scope", "breakfast_items", "lunch_items", "dinner_items"]:
        if not _column_exists(inspector, "management_events", column):
            op.add_column("management_events", sa.Column(column, sa.JSON(), nullable=True))

    if not _column_exists(inspector, "management_events", "other_materials"):
        op.add_column(
            "management_events",
            sa.Column("other_materials", sa.JSON(), nullable=False, server_default="[]"),
        )
        op.alter_column("management_events", "other_materials", server_default=None)
