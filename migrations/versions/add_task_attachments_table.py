"""Add table for storing task attachments."""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_task_attachments'
down_revision = '1a2b3c4d5e67'  # última migração existente
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'task_attachments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'task_id',
            sa.Integer(),
            sa.ForeignKey('tasks.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('file_path', sa.String(length=255), nullable=False),
        sa.Column('original_name', sa.String(length=255), nullable=True),
        sa.Column('mime_type', sa.String(length=128), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('CURRENT_TIMESTAMP'),
        ),
    )

    op.alter_column(
        'task_attachments',
        'created_at',
        server_default=None,
        existing_type=sa.DateTime(),
    )


def downgrade() -> None:
    op.drop_table('task_attachments')
