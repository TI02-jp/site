"""add_performance_indices

Revision ID: 7f8e48bdadf8
Revises: c8f9d2e3a4b1
Create Date: 2025-10-22 16:03:15.874347

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7f8e48bdadf8'
down_revision = 'c8f9d2e3a4b1'
branch_labels = None
depends_on = None


def upgrade():
    """Add performance indices to improve query performance with concurrent users."""

    # Index for departamentos (empresa_id, tipo) - used in visualizar_empresa and gerenciar_departamentos
    # This composite index speeds up the consolidated department queries
    op.create_index(
        'idx_departamentos_empresa_tipo',
        'departamentos',
        ['empresa_id', 'tipo'],
        unique=False
    )

    # Index for task_notifications (user_id, id) - used in notifications_stream SSE
    # This index speeds up the incremental notification queries
    op.create_index(
        'idx_task_notifications_user_id',
        'task_notifications',
        ['user_id', 'id'],
        unique=False
    )

    # Index for users (ativo, last_seen) - used in stats cache and online user counts
    # This composite index speeds up active user queries
    op.create_index(
        'idx_users_ativo_last_seen',
        'users',
        ['ativo', 'last_seen'],
        unique=False
    )

    # Index for tasks (tag_id, parent_id, status) - used in task overview queries
    # This composite index speeds up task filtering by tag and status
    op.create_index(
        'idx_tasks_tag_parent_status',
        'tasks',
        ['tag_id', 'parent_id', 'status'],
        unique=False
    )


def downgrade():
    """Remove performance indices."""

    op.drop_index('idx_tasks_tag_parent_status', table_name='tasks')
    op.drop_index('idx_users_ativo_last_seen', table_name='users')
    op.drop_index('idx_task_notifications_user_id', table_name='task_notifications')
    op.drop_index('idx_departamentos_empresa_tipo', table_name='departamentos')
