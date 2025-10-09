"""allow multiple diretoria agreements per user"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "4c871f2a7b1c"
down_revision = "1f3b5c7d8e90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("diretoria_agreements") as batch_op:
        batch_op.drop_constraint(
            "uq_diretoria_agreements_user_id",
            type_="unique",
        )
        batch_op.add_column(
            sa.Column(
                "agreement_date",
                sa.Date(),
                nullable=False,
                server_default=sa.text("CURRENT_DATE"),
            )
        )

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE diretoria_agreements "
            "SET agreement_date = COALESCE(DATE(created_at), CURRENT_DATE)"
        )
    )

    with op.batch_alter_table("diretoria_agreements") as batch_op:
        batch_op.alter_column("agreement_date", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("diretoria_agreements") as batch_op:
        batch_op.drop_column("agreement_date")
        batch_op.create_unique_constraint(
            "uq_diretoria_agreements_user_id",
            ["user_id"],
        )
