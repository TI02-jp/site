"""change_particularidades_texto_to_longtext

Revision ID: 808f5ba98cb4
Revises: 04a1b2c3d4e5
Create Date: 2025-10-29 12:35:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '808f5ba98cb4'
down_revision = '04a1b2c3d4e5'
branch_labels = None
depends_on = None


def upgrade():
    # Change particularidades_texto from TEXT to LONGTEXT
    with op.batch_alter_table('departamentos', schema=None) as batch_op:
        batch_op.alter_column('particularidades_texto',
               existing_type=mysql.TEXT(charset='utf8mb4', collation='utf8mb4_general_ci'),
               type_=mysql.LONGTEXT(charset='utf8mb4', collation='utf8mb4_general_ci'),
               existing_nullable=True)


def downgrade():
    # Revert LONGTEXT back to TEXT (data may be truncated if larger than 64KB)
    with op.batch_alter_table('departamentos', schema=None) as batch_op:
        batch_op.alter_column('particularidades_texto',
               existing_type=mysql.LONGTEXT(charset='utf8mb4', collation='utf8mb4_general_ci'),
               type_=mysql.TEXT(charset='utf8mb4', collation='utf8mb4_general_ci'),
               existing_nullable=True)
