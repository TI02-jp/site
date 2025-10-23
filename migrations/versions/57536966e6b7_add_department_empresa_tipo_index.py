"""add_department_empresa_tipo_index

Revision ID: 57536966e6b7
Revises: 23ebc21a7eaa
Create Date: 2025-10-23 10:00:45.787351

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '57536966e6b7'
down_revision = '23ebc21a7eaa'
branch_labels = None
depends_on = None


def upgrade():
    """Add composite index for departamentos (empresa_id, tipo).

    This index optimizes the company detail view query:
    Departamento.query.filter(empresa_id=X, tipo.in_([...])).all()

    Significantly speeds up department loading when viewing companies.
    """

    # Composite index for departamentos - optimizes company detail queries
    op.create_index(
        'idx_departamentos_empresa_tipo',
        'departamentos',
        ['empresa_id', 'tipo'],
        unique=False
    )

    # Single index for task_notifications (announcement_id) - optimizes announcement reads
    # Speeds up queries: TaskNotification.query.filter(announcement_id=X, user_id=Y)
    op.create_index(
        'idx_task_notifications_announcement',
        'task_notifications',
        ['announcement_id'],
        unique=False
    )


def downgrade():
    """Remove department and notification performance indices."""

    op.drop_index('idx_task_notifications_announcement', table_name='task_notifications')
    op.drop_index('idx_departamentos_empresa_tipo', table_name='departamentos')
