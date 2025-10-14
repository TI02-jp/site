"""Add indexes to sessions table for performance

Revision ID: ea2e86d74a68
Revises: fa09c1d0a697
Create Date: 2025-10-14 09:32:34.134228

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'ea2e86d74a68'
down_revision = 'fa09c1d0a697'
branch_labels = None
depends_on = None


def upgrade():
    # Adiciona índices na tabela sessions para melhor performance
    with op.batch_alter_table('sessions', schema=None) as batch_op:
        batch_op.create_index('idx_sessions_last_activity', ['last_activity'], unique=False)
        batch_op.create_index('idx_sessions_user_id', ['user_id'], unique=False)


def downgrade():
    # Remove os índices criados
    with op.batch_alter_table('sessions', schema=None) as batch_op:
        batch_op.drop_index('idx_sessions_user_id')
        batch_op.drop_index('idx_sessions_last_activity')
