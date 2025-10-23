"""add_critical_performance_indices

Revision ID: c6b89eddea6a
Revises: 7f8e48bdadf8
Create Date: 2025-10-22 16:39:42.991141

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c6b89eddea6a'
down_revision = '7f8e48bdadf8'
branch_labels = None
depends_on = None


def upgrade():
    """Add critical performance indices to resolve query slowdowns with concurrent users."""

    # Index for task_notifications (user_id, read_at) - used heavily in unread notification queries
    # Speeds up queries like: TaskNotification.query.filter(user_id=X, read_at=None).count()
    op.create_index(
        'idx_task_notifications_user_read',
        'task_notifications',
        ['user_id', 'read_at'],
        unique=False
    )

    # Index for task_notifications (user_id, created_at) - used in notification ordering/pagination
    # Speeds up queries like: TaskNotification.query.filter(user_id=X).order_by(created_at.desc())
    op.create_index(
        'idx_task_notifications_user_created',
        'task_notifications',
        ['user_id', 'created_at'],
        unique=False
    )

    # Index for tasks (tag_id, status) - used in Kanban board views and task overview
    # Speeds up queries like: Task.query.filter(tag_id=X, status=Y)
    op.create_index(
        'idx_tasks_tag_status',
        'tasks',
        ['tag_id', 'status'],
        unique=False
    )

    # Index for tasks (created_by) - used in task filtering by creator
    # Speeds up queries like: Task.query.filter(created_by=X)
    op.create_index(
        'idx_tasks_created_by',
        'tasks',
        ['created_by'],
        unique=False
    )

    # Index for users (last_seen) - CRITICAL for online user count queries
    # Speeds up queries like: User.query.filter(last_seen >= cutoff).count()
    op.create_index(
        'idx_users_last_seen',
        'users',
        ['last_seen'],
        unique=False
    )

    # Index for users (ativo) - used in active user filtering
    # Speeds up queries like: User.query.filter(ativo=True)
    op.create_index(
        'idx_users_ativo',
        'users',
        ['ativo'],
        unique=False
    )

    # Composite index for sessions (user_id, last_activity) - improves session cleanup and user activity queries
    # Note: idx_sessions_user_id and idx_sessions_last_activity already exist from previous migration
    # This composite index further optimizes queries that filter by both columns
    op.create_index(
        'idx_sessions_user_activity',
        'sessions',
        ['user_id', 'last_activity'],
        unique=False
    )


def downgrade():
    """Remove critical performance indices."""

    op.drop_index('idx_sessions_user_activity', table_name='sessions')
    op.drop_index('idx_users_ativo', table_name='users')
    op.drop_index('idx_users_last_seen', table_name='users')
    op.drop_index('idx_tasks_created_by', table_name='tasks')
    op.drop_index('idx_tasks_tag_status', table_name='tasks')
    op.drop_index('idx_task_notifications_user_created', table_name='task_notifications')
    op.drop_index('idx_task_notifications_user_read', table_name='task_notifications')
