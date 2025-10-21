"""Add ativo column to tbl_empresas table."""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_ativo_to_empresas'
down_revision = 'add_task_attachments'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add ativo column with default value True
    op.add_column(
        'tbl_empresas',
        sa.Column('ativo', sa.Boolean(), nullable=False, server_default='1'),
    )


def downgrade() -> None:
    # Remove ativo column
    op.drop_column('tbl_empresas', 'ativo')
