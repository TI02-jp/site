"""add subject, urgency, status to support_tickets

Revision ID: 383df3cda47f
Revises: bad9f129e4fe
Create Date: 2025-09-08 11:26:16.441368

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '383df3cda47f'
down_revision = 'bad9f129e4fe'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('support_tickets', schema=None) as batch_op:
        batch_op.add_column(sa.Column('urgency', sa.String(length=20), nullable=False))


def downgrade():
    with op.batch_alter_table('support_tickets', schema=None) as batch_op:
        batch_op.drop_column('urgency')
