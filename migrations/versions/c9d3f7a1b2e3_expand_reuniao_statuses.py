"""add manual statuses for reunioes

Revision ID: c9d3f7a1b2e3
Revises: a7d1f3b29cde
Create Date: 2025-10-07 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c9d3f7a1b2e3'
down_revision = 'a7d1f3b29cde'
branch_labels = None
depends_on = None


def upgrade():
    status_enum = sa.Enum(
        'agendada',
        'em andamento',
        'realizada',
        'adiada',
        'cancelada',
        name='reuniao_status',
        create_type=False,
    )
    op.execute(
        "ALTER TABLE reunioes "
        "MODIFY COLUMN status "
        "ENUM('agendada','em andamento','realizada','adiada','cancelada') NOT NULL"
    )
    op.add_column('reunioes', sa.Column('status_override', status_enum, nullable=True))


def downgrade():
    op.drop_column('reunioes', 'status_override')
    op.execute(
        "ALTER TABLE reunioes "
        "MODIFY COLUMN status "
        "ENUM('agendada','em andamento','realizada') NOT NULL"
    )
