"""Ensure Diretoria agreements table has the manual agreement date column."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7b123456789a"
down_revision = "4c871f2a7b1c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    columns = {column["name"] for column in inspector.get_columns("diretoria_agreements")}
    unique_constraints = {
        constraint["name"] for constraint in inspector.get_unique_constraints("diretoria_agreements")
    }

    needs_column = "agreement_date" not in columns
    needs_unique_drop = "uq_diretoria_agreements_user_id" in unique_constraints

    if needs_column or needs_unique_drop:
        with op.batch_alter_table("diretoria_agreements") as batch_op:
            if needs_unique_drop:
                batch_op.drop_constraint("uq_diretoria_agreements_user_id", type_="unique")
            if needs_column:
                batch_op.add_column(sa.Column("agreement_date", sa.Date(), nullable=True))

    if needs_column:
        bind.execute(
            sa.text(
                "UPDATE diretoria_agreements "
                "SET agreement_date = COALESCE(DATE(created_at), CURRENT_DATE)"
            )
        )

    with op.batch_alter_table("diretoria_agreements") as batch_op:
        batch_op.alter_column(
            "agreement_date",
            existing_type=sa.Date(),
            nullable=False,
        )
        batch_op.alter_column(
            "agreement_date",
            existing_type=sa.Date(),
            server_default=None,
        )


def downgrade() -> None:
    with op.batch_alter_table("diretoria_agreements") as batch_op:
        batch_op.alter_column(
            "agreement_date",
            existing_type=sa.Date(),
            nullable=True,
        )
        batch_op.drop_column("agreement_date")
        batch_op.create_unique_constraint(
            "uq_diretoria_agreements_user_id",
            ["user_id"],
        )
