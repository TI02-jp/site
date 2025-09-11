"""switch status to enum

Revision ID: 9a3ec81bf08d
Revises: 6ef0fb9ae2fa
Create Date: 2025-09-15 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '9a3ec81bf08d'
down_revision = '6ef0fb9ae2fa'
branch_labels = None
depends_on = None


def upgrade():
    status_enum = sa.Enum('agendada', 'em andamento', 'realizada', name='reuniao_status')
    status_enum.create(op.get_bind(), checkfirst=True)
    op.alter_column('reunioes', 'status', existing_type=sa.String(length=20), type_=status_enum, existing_nullable=False)


def downgrade():
    status_enum = sa.Enum('agendada', 'em andamento', 'realizada', name='reuniao_status')
    op.alter_column('reunioes', 'status', existing_type=status_enum, type_=sa.String(length=20), existing_nullable=False)
    status_enum.drop(op.get_bind(), checkfirst=True)
