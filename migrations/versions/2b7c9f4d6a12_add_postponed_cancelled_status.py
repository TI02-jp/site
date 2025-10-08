"""Add postponed and cancelled statuses for meetings."""

from alembic import op

# revision identifiers, used by Alembic.
revision = '2b7c9f4d6a12'
down_revision = '9a3ec81bf08d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE reuniao_status ADD VALUE IF NOT EXISTS 'ADIADA'")
    op.execute("ALTER TYPE reuniao_status ADD VALUE IF NOT EXISTS 'CANCELADA'")


def downgrade() -> None:
    op.execute(
        "UPDATE reunioes SET status = 'AGENDADA' WHERE status IN ('ADIADA', 'CANCELADA')"
    )
    op.execute("ALTER TABLE reunioes ALTER COLUMN status TYPE VARCHAR(20)")
    op.execute("DROP TYPE IF EXISTS reuniao_status")
    op.execute(
        "CREATE TYPE reuniao_status AS ENUM ('AGENDADA', 'EM_ANDAMENTO', 'REALIZADA')"
    )
    op.execute(
        "ALTER TABLE reunioes ALTER COLUMN status TYPE reuniao_status USING status::reuniao_status"
    )
