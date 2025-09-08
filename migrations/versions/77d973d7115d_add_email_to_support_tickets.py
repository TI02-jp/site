"""add email to support_tickets

Revision ID: 77d973d7115d
Revises: 27d4165a9a77
Create Date: 2025-09-08 11:17:54.866888

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '77d973d7115d'
down_revision = '27d4165a9a77'
branch_labels = None
depends_on = None



def upgrade():
       with op.batch_alter_table('support_tickets', schema=None) as batch_op:
        batch_op.add_column(sa.Column('email', sa.String(length=120), nullable=False))



def downgrade():
    with op.batch_alter_table('support_tickets', schema=None) as batch_op:
        batch_op.drop_column('email')