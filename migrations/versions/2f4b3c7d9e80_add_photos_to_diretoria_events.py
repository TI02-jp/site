"""Add photos field to Diretoria JP events"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "2f4b3c7d9e80"
down_revision = "0d9d3bb1c2f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "diretoria_events",
        sa.Column("photos", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("diretoria_events", "photos")
