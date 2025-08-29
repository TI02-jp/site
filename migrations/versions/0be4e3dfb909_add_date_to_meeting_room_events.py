"""Add date column to meeting room events

Revision ID: 0be4e3dfb909
Revises: 9257644b33aa
Create Date: 2025-10-03 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0be4e3dfb909'
down_revision = '9257644b33aa'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('meeting_room_events', sa.Column('date', sa.Date(), nullable=True))
    op.execute("UPDATE meeting_room_events SET date = DATE(start_time)")
    op.alter_column('meeting_room_events', 'date', nullable=False)


def downgrade():
    op.drop_column('meeting_room_events', 'date')
