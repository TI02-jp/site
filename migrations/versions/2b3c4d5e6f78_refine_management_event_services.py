"""refine management event services"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from decimal import Decimal, InvalidOperation
import json


# revision identifiers, used by Alembic.
revision = "2b3c4d5e6f78"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("management_events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("attendance_scope", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("breakfast_items", sa.JSON(), nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("lunch_items", sa.JSON(), nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("dinner_items", sa.JSON(), nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("event_total", sa.Numeric(10, 2), nullable=True))
        batch_op.alter_column("include_snack", new_column_name="include_dinner")
        batch_op.alter_column("cost_snack", new_column_name="cost_dinner")

    op.alter_column("management_events", "breakfast_items", server_default=None)
    op.alter_column("management_events", "lunch_items", server_default=None)
    op.alter_column("management_events", "dinner_items", server_default=None)

    management_events = sa.table(
        "management_events",
        sa.column("id", sa.Integer),
        sa.column("attendance_scope", sa.String(20)),
        sa.column("attendees_internal", sa.Boolean),
        sa.column("attendees_external", sa.Boolean),
        sa.column("include_breakfast", sa.Boolean),
        sa.column("breakfast_description", sa.String(255)),
        sa.column("cost_breakfast", sa.Numeric(10, 2)),
        sa.column("cost_breakfast_unit", sa.Numeric(10, 2)),
        sa.column("include_lunch", sa.Boolean),
        sa.column("lunch_description", sa.String(255)),
        sa.column("cost_lunch", sa.Numeric(10, 2)),
        sa.column("cost_lunch_unit", sa.Numeric(10, 2)),
        sa.column("include_dinner", sa.Boolean),
        sa.column("snack_description", sa.String(255)),
        sa.column("cost_dinner", sa.Numeric(10, 2)),
        sa.column("cost_snack_unit", sa.Numeric(10, 2)),
        sa.column("breakfast_items", sa.JSON),
        sa.column("lunch_items", sa.JSON),
        sa.column("dinner_items", sa.JSON),
        sa.column("other_materials", sa.JSON),
        sa.column("event_total", sa.Numeric(10, 2)),
    )

    connection = op.get_bind()
    rows = connection.execute(sa.select(management_events)).fetchall()

    def _decimal_or_none(value: Decimal | float | None) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except Exception:  # pragma: no cover - defensive conversion
            return None

    for row in rows:
        attendance_scope = "interna"
        if row.attendees_internal and row.attendees_external:
            attendance_scope = "ambos"
        elif row.attendees_external and not row.attendees_internal:
            attendance_scope = "externa"

        def _build_items(include_flag: bool, description: str | None, total: Decimal | None, unit: Decimal | None) -> list[dict[str, float | str]]:
            if not include_flag:
                return []
            has_data = description or total is not None or unit is not None
            if not has_data:
                return []
            item: dict[str, float | str] = {}
            if description:
                item["description"] = description
            if unit is not None:
                item["unit_cost"] = float(unit)
            quantity: Decimal | None = None
            if unit not in (None, Decimal(0)) and total not in (None, Decimal(0)):
                try:
                    quantity = (total or Decimal(0)) / unit
                except (ZeroDivisionError, InvalidOperation):  # pragma: no cover - defensive
                    quantity = None
            elif total is not None:
                quantity = Decimal(1)
            if quantity is not None:
                item["quantity"] = float(quantity)
            if total is not None:
                item["total_cost"] = float(total)
            return [item]

        breakfast_items = _build_items(
            bool(row.include_breakfast),
            row.breakfast_description,
            _decimal_or_none(row.cost_breakfast),
            _decimal_or_none(row.cost_breakfast_unit),
        )
        lunch_items = _build_items(
            bool(row.include_lunch),
            row.lunch_description,
            _decimal_or_none(row.cost_lunch),
            _decimal_or_none(row.cost_lunch_unit),
        )
        dinner_items = _build_items(
            bool(row.include_dinner),
            row.snack_description,
            _decimal_or_none(row.cost_dinner),
            _decimal_or_none(row.cost_snack_unit),
        )

        service_totals = [
            _decimal_or_none(row.cost_breakfast) or Decimal(0),
            _decimal_or_none(row.cost_lunch) or Decimal(0),
            _decimal_or_none(row.cost_dinner) or Decimal(0),
        ]

        materials_total = Decimal(0)
        materials = row.other_materials or []
        if isinstance(materials, str):
            try:
                materials = json.loads(materials)
            except json.JSONDecodeError:
                materials = []
        if isinstance(materials, list):
            for material in materials:
                if isinstance(material, dict):
                    value_raw = material.get("value")
                    decimal_value = _decimal_or_none(value_raw)
                    if decimal_value is not None:
                        materials_total += decimal_value

        event_total = sum(service_totals, materials_total)

        connection.execute(
            management_events.update()
            .where(management_events.c.id == row.id)
            .values(
                attendance_scope=attendance_scope,
                breakfast_items=breakfast_items,
                lunch_items=lunch_items,
                dinner_items=dinner_items,
                event_total=event_total,
            )
        )

    with op.batch_alter_table("management_events", schema=None) as batch_op:
        batch_op.drop_column("attendees_internal")
        batch_op.drop_column("attendees_external")
        batch_op.drop_column("breakfast_description")
        batch_op.drop_column("cost_breakfast_unit")
        batch_op.drop_column("lunch_description")
        batch_op.drop_column("cost_lunch_unit")
        batch_op.drop_column("snack_description")
        batch_op.drop_column("cost_snack_unit")


def downgrade() -> None:
    with op.batch_alter_table("management_events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("attendees_internal", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("attendees_external", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("breakfast_description", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("cost_breakfast_unit", sa.Numeric(10, 2), nullable=True))
        batch_op.add_column(sa.Column("lunch_description", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("cost_lunch_unit", sa.Numeric(10, 2), nullable=True))
        batch_op.add_column(sa.Column("snack_description", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("cost_snack_unit", sa.Numeric(10, 2), nullable=True))
        batch_op.alter_column("include_dinner", new_column_name="include_snack")
        batch_op.alter_column("cost_dinner", new_column_name="cost_snack")
        batch_op.drop_column("event_total")
        batch_op.drop_column("dinner_items")
        batch_op.drop_column("lunch_items")
        batch_op.drop_column("breakfast_items")
        batch_op.drop_column("attendance_scope")

    op.alter_column("management_events", "attendees_internal", server_default=None)
    op.alter_column("management_events", "attendees_external", server_default=None)

    op.execute(
        sa.text(
            "UPDATE management_events SET attendees_internal = 0, attendees_external = 0, "
            "breakfast_description = NULL, cost_breakfast_unit = NULL, "
            "lunch_description = NULL, cost_lunch_unit = NULL, "
            "snack_description = NULL, cost_snack_unit = NULL"
        )
    )
