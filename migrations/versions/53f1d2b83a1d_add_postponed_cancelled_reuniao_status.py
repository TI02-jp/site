"""add postponed and cancelled meeting statuses

Revision ID: 53f1d2b83a1d
Revises: 9a3ec81bf08d
Create Date: 2024-05-05 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '53f1d2b83a1d'
down_revision = '9a3ec81bf08d'
branch_labels = None
depends_on = None


def _get_dialect_name() -> str:
    bind = op.get_bind()
    return bind.dialect.name if bind is not None else ""


def upgrade():
    dialect = _get_dialect_name()
    if dialect == "postgresql":
        op.execute("ALTER TYPE reuniao_status ADD VALUE IF NOT EXISTS 'adiada'")
        op.execute("ALTER TYPE reuniao_status ADD VALUE IF NOT EXISTS 'cancelada'")
        return

    if dialect == "mysql":
        op.execute(
            "ALTER TABLE reunioes MODIFY COLUMN status "
            "ENUM('agendada', 'em andamento', 'realizada', 'adiada', 'cancelada') "
            "NOT NULL DEFAULT 'agendada'"
        )
        return

    existing_type = sa.Enum(
        'agendada', 'em andamento', 'realizada', name='reuniao_status'
    )
    new_type = sa.Enum(
        'agendada', 'em andamento', 'realizada', 'adiada', 'cancelada',
        name='reuniao_status'
    )
    with op.batch_alter_table('reunioes') as batch_op:
        batch_op.alter_column(
            'status',
            existing_type=existing_type,
            type_=new_type,
            existing_nullable=False,
        )


def downgrade():
    dialect = _get_dialect_name()
    op.execute(
        "UPDATE reunioes SET status = 'agendada' "
        "WHERE status IN ('adiada', 'cancelada')"
    )

    if dialect == "postgresql":
        op.execute(
            "CREATE TYPE reuniao_status_old AS "
            "ENUM ('agendada', 'em andamento', 'realizada')"
        )
        op.execute(
            "ALTER TABLE reunioes ALTER COLUMN status TYPE reuniao_status_old "
            "USING status::text::reuniao_status_old"
        )
        op.execute("DROP TYPE reuniao_status")
        op.execute("ALTER TYPE reuniao_status_old RENAME TO reuniao_status")
        return

    if dialect == "mysql":
        op.execute(
            "ALTER TABLE reunioes MODIFY COLUMN status "
            "ENUM('agendada', 'em andamento', 'realizada') "
            "NOT NULL DEFAULT 'agendada'"
        )
        return

    expanded_type = sa.Enum(
        'agendada', 'em andamento', 'realizada', 'adiada', 'cancelada',
        name='reuniao_status'
    )
    original_type = sa.Enum(
        'agendada', 'em andamento', 'realizada', name='reuniao_status'
    )
    with op.batch_alter_table('reunioes') as batch_op:
        batch_op.alter_column(
            'status',
            existing_type=expanded_type,
            type_=original_type,
            existing_nullable=False,
        )
