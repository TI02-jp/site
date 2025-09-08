"""add subject to support_tickets

Revision ID: bad9f129e4fe
Revises: 77d973d7115d
Create Date: 2025-09-08 11:23:00.441734

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'bad9f129e4fe'
down_revision = '77d973d7115d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('support_tickets', schema=None) as batch_op:
        batch_op.add_column(sa.Column('subject', sa.String(length=255), nullable=False))


def downgrade():
    with op.batch_alter_table('support_tickets', schema=None) as batch_op:
        batch_op.drop_column('subject')
