"""add cancelled fields to mural tasks

Revision ID: b9dce3a32ab7
Revises: 1f3b52b27c4a
Create Date: 2025-09-16 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b9dce3a32ab7'
down_revision = '1f3b52b27c4a'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('mural_tasks', sa.Column('cancelled', sa.Boolean(), nullable=False, server_default=sa.text('0')))
    op.add_column('mural_tasks', sa.Column('cancelled_at', sa.DateTime(), nullable=True))
    op.add_column('mural_tasks', sa.Column('cancelled_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True))


def downgrade():
    op.drop_column('mural_tasks', 'cancelled_by_id')
    op.drop_column('mural_tasks', 'cancelled_at')
    op.drop_column('mural_tasks', 'cancelled')
