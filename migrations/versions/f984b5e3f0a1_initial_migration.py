"""initial migration

Revision ID: f984b5e3f0a1
Revises: 
Create Date: 2025-09-10 10:47:40.445737

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'f984b5e3f0a1'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('reunioes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('meet_link', sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table('reunioes', schema=None) as batch_op:
        batch_op.drop_column('meet_link')
