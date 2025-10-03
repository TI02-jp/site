"""Expand notifications table to support announcements."""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7c5a9d3f4b2a'
down_revision = 'fe0c2c1a5b6d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'task_notifications',
        sa.Column('type', sa.String(length=20), nullable=False, server_default='task'),
    )
    op.add_column(
        'task_notifications',
        sa.Column('announcement_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_task_notifications_announcement_id_announcements',
        'task_notifications',
        'announcements',
        ['announcement_id'],
        ['id'],
        ondelete='CASCADE',
    )
    op.alter_column(
        'task_notifications',
        'task_id',
        existing_type=sa.Integer(),
        nullable=True,
    )
    connection = op.get_bind()
    connection.execute(sa.text("UPDATE task_notifications SET type = 'task' WHERE type IS NULL"))


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("DELETE FROM task_notifications WHERE announcement_id IS NOT NULL")
    )
    op.alter_column(
        'task_notifications',
        'task_id',
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_constraint(
        'fk_task_notifications_announcement_id_announcements',
        'task_notifications',
        type_='foreignkey',
    )
    op.drop_column('task_notifications', 'announcement_id')
    op.drop_column('task_notifications', 'type')
