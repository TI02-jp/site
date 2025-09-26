"""add service details to management events

Revision ID: a1b2c3d4e5f6
Revises: d7b1e2f3a4c5
Create Date: 2024-05-04 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'd7b1e2f3a4c5'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'management_events',
        sa.Column('breakfast_description', sa.String(length=255), nullable=True),
    )
    op.add_column(
        'management_events',
        sa.Column('cost_breakfast_unit', sa.Numeric(10, 2), nullable=True),
    )
    op.add_column(
        'management_events',
        sa.Column('lunch_description', sa.String(length=255), nullable=True),
    )
    op.add_column(
        'management_events',
        sa.Column('cost_lunch_unit', sa.Numeric(10, 2), nullable=True),
    )
    op.add_column(
        'management_events',
        sa.Column('snack_description', sa.String(length=255), nullable=True),
    )
    op.add_column(
        'management_events',
        sa.Column('cost_snack_unit', sa.Numeric(10, 2), nullable=True),
    )


def downgrade():
    op.drop_column('management_events', 'cost_snack_unit')
    op.drop_column('management_events', 'snack_description')
    op.drop_column('management_events', 'cost_lunch_unit')
    op.drop_column('management_events', 'lunch_description')
    op.drop_column('management_events', 'cost_breakfast_unit')
    op.drop_column('management_events', 'breakfast_description')
