"""add postponed and cancelled meeting statuses

Revision ID: 53f1d2b83a1d
Revises: 9a3ec81bf08d
Create Date: 2024-05-05 00:00:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = '53f1d2b83a1d'
down_revision = '9a3ec81bf08d'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE reuniao_status ADD VALUE IF NOT EXISTS 'adiada'")
    op.execute("ALTER TYPE reuniao_status ADD VALUE IF NOT EXISTS 'cancelada'")


def downgrade():
    # Map postponed and cancelled meetings back to the default status before
    # dropping the expanded enumeration.
    op.execute(
        "UPDATE reunioes SET status = 'agendada' "
        "WHERE status IN ('adiada', 'cancelada')"
    )
    op.execute(
        "CREATE TYPE reuniao_status_old AS ENUM ('agendada', 'em andamento', 'realizada')"
    )
    op.execute(
        "ALTER TABLE reunioes ALTER COLUMN status TYPE reuniao_status_old "
        "USING status::text::reuniao_status_old"
    )
    op.execute("DROP TYPE reuniao_status")
    op.execute("ALTER TYPE reuniao_status_old RENAME TO reuniao_status")
