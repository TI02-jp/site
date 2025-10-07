"""add owner to reunioes

Revision ID: a7d1f3b29cde
Revises: 9a3ec81bf08d
Create Date: 2025-10-07 13:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a7d1f3b29cde'
down_revision = '9a3ec81bf08d'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('reunioes', sa.Column('owner_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_reunioes_owner_id_users',
        'reunioes',
        'users',
        ['owner_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index('ix_reunioes_owner_id', 'reunioes', ['owner_id'])


def downgrade():
    op.drop_index('ix_reunioes_owner_id', table_name='reunioes')
    op.drop_constraint('fk_reunioes_owner_id_users', 'reunioes', type_='foreignkey')
    op.drop_column('reunioes', 'owner_id')
