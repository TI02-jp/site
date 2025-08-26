"""add last_seen to users

Revision ID: ab12c3d4e5f6
Revises: 
Create Date: 2024-06-01 00:00:00

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'ab12c3d4e5f6'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('last_seen', sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column('users', 'last_seen')
