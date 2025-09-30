"""Add observation column to courses"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a1b3c5d7e9f2"
down_revision = "2f4b3c7d9e80"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the observation column to store course notes."""

    op.add_column("courses", sa.Column("observation", sa.Text(), nullable=True))


def downgrade() -> None:
    """Remove the observation column."""

    op.drop_column("courses", "observation")
