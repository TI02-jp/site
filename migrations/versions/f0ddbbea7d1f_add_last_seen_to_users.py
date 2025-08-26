"""add last_seen to users

Revision ID: f0ddbbea7d1f
Revises: 
Create Date: 2025-08-26 HH:MM:SS.XXXXXX
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f0ddbbea7d1f'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_seen', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('last_seen')
