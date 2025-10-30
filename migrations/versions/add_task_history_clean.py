"""add task history table clean

Revision ID: add_task_history_clean
Revises: 1f09d5d123ab
Create Date: 2025-10-30 16:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_task_history_clean'
down_revision = '1f09d5d123ab'
branch_labels = None
depends_on = None


def upgrade():
    # Create task_history table
    op.create_table(
        'task_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('changed_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('changed_by', sa.Integer(), nullable=True),
        sa.Column('field_name', sa.String(length=50), nullable=False),
        sa.Column('old_value', sa.Text(), nullable=True),
        sa.Column('new_value', sa.Text(), nullable=True),
        sa.Column('change_type', sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['changed_by'], ['users.id'], ondelete='SET NULL'),
    )

    # Add index for faster queries
    op.create_index('idx_task_history_task_id', 'task_history', ['task_id'])
    op.create_index('idx_task_history_changed_at', 'task_history', ['changed_at'])


def downgrade():
    op.drop_index('idx_task_history_changed_at', table_name='task_history')
    op.drop_index('idx_task_history_task_id', table_name='task_history')
    op.drop_table('task_history')
