"""add google_event_id to reunioes

Revision ID: 6ef0fb9ae2fa
Revises: 30561a4fe41e
Create Date: 2025-09-10 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '6ef0fb9ae2fa'
down_revision = '30561a4fe41e'
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table('reunioes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('google_event_id', sa.String(length=255), nullable=True))

def downgrade():
    with op.batch_alter_table('reunioes', schema=None) as batch_op:
        batch_op.drop_column('google_event_id')
