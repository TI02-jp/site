"""add criador_id to reunioes

Revision ID: 30561a4fe41e
Revises: f984b5e3f0a1
Create Date: 2025-09-10 10:47:40.445737

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '30561a4fe41e'
down_revision = 'f984b5e3f0a1'
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table('reunioes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('criador_id', sa.Integer(), nullable=False))
        batch_op.create_foreign_key(
            'fk_reunioes_criador_id_users',
            'users',
            ['criador_id'],
            ['id'],
            ondelete='CASCADE'
        )

def downgrade():
    with op.batch_alter_table('reunioes', schema=None) as batch_op:
        batch_op.drop_constraint('fk_reunioes_criador_id_users', type_='foreignkey')
        batch_op.drop_column('criador_id')
