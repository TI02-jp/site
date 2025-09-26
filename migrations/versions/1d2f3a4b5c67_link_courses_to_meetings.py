"""link courses to meetings

Revision ID: 1d2f3a4b5c67
Revises: 8f3aa2e55b15
Create Date: 2024-06-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1d2f3a4b5c67'
down_revision = '8f3aa2e55b15'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('reunioes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('course_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_reunioes_course_id_courses',
            'courses',
            ['course_id'],
            ['id'],
            ondelete='SET NULL',
        )


def downgrade():
    with op.batch_alter_table('reunioes', schema=None) as batch_op:
        batch_op.drop_constraint('fk_reunioes_course_id_courses', type_='foreignkey')
        batch_op.drop_column('course_id')
