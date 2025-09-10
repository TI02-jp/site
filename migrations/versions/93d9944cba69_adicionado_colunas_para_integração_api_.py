"""adicionado colunas para integração API do google

Revision ID: 93d9944cba69
Revises: 
Create Date: 2025-09-10 08:00:21.543888

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '93d9944cba69'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('google_id', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('google_refresh_token', sa.String(length=255), nullable=True))
        batch_op.create_unique_constraint('uq_users_google_id', ['google_id'])

def downgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_constraint('uq_users_google_id', type_='unique')
        batch_op.drop_column('google_refresh_token')
        batch_op.drop_column('google_id')
