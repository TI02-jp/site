"""Altera campo de usuário em consultorias para texto

Revision ID: 56aa3b1bf5a1
Revises: e57a4926ea16
Create Date: 2024-12-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '56aa3b1bf5a1'
down_revision = 'e57a4926ea16'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('consultorias', sa.Column('usuario', sa.String(length=100), nullable=True))
    op.drop_constraint('consultorias_usuario_id_fkey', 'consultorias', type_='foreignkey')
    op.drop_column('consultorias', 'usuario_id')


def downgrade():
    op.add_column('consultorias', sa.Column('usuario_id', sa.Integer(), nullable=True))
    op.create_foreign_key('consultorias_usuario_id_fkey', 'consultorias', 'users', ['usuario_id'], ['id'])
    op.drop_column('consultorias', 'usuario')
