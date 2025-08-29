"""create meeting room events table

Revision ID: db0c23d8020f
Revises: e5d8e8454900
Create Date: 2025-01-15 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'db0c23d8020f'
down_revision = 'e5d8e8454900'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        'meeting_room_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('title', sa.String(length=100), nullable=False),
        sa.Column('start_time', sa.DateTime(), nullable=False),
        sa.Column('end_time', sa.DateTime(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'])
    )


def downgrade():
    op.drop_table('meeting_room_events')
