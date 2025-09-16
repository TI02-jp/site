"""add task notifications table

Revision ID: b2f3c0d6d9c4
Revises: 6ef0fb9ae2fa
Create Date: 2024-05-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b2f3c0d6d9c4'
down_revision = '6ef0fb9ae2fa'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'task_notifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('message', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('read_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(
        'ix_task_notifications_user_id_read_at',
        'task_notifications',
        ['user_id', 'read_at']
    )


def downgrade():
    op.drop_index('ix_task_notifications_user_id_read_at', table_name='task_notifications')
    op.drop_table('task_notifications')
