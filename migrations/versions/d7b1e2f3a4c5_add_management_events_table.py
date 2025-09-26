"""add management events table

Revision ID: d7b1e2f3a4c5
Revises: 0d9d3bb1c2f0
Create Date: 2024-07-05 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd7b1e2f3a4c5'
down_revision = '0d9d3bb1c2f0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'management_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('event_date', sa.Date(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('attendees_internal', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('attendees_external', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('participants_count', sa.Integer(), nullable=True),
        sa.Column('include_breakfast', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('cost_breakfast', sa.Numeric(10, 2), nullable=True),
        sa.Column('include_lunch', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('cost_lunch', sa.Numeric(10, 2), nullable=True),
        sa.Column('include_snack', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('cost_snack', sa.Numeric(10, 2), nullable=True),
        sa.Column('other_materials', sa.JSON(), server_default='[]', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_management_events_event_date',
        'management_events',
        ['event_date'],
    )


def downgrade():
    op.drop_index('ix_management_events_event_date', table_name='management_events')
    op.drop_table('management_events')
