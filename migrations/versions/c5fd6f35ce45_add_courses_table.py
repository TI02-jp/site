"""add courses table

Revision ID: c5fd6f35ce45
Revises: 1c2d3e4f5a6b
Create Date: 2024-07-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c5fd6f35ce45'
down_revision = '1c2d3e4f5a6b'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'courses',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('instructor', sa.String(length=150), nullable=False),
        sa.Column('sectors', sa.Text(), nullable=False),
        sa.Column('participants', sa.Text(), nullable=False),
        sa.Column('workload', sa.String(length=50), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('schedule', sa.String(length=100), nullable=False),
        sa.Column('completion_date', sa.Date(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='planejado'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('courses')
