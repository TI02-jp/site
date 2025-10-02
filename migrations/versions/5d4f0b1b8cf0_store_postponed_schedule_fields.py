"""store original schedule for postponed meetings

Revision ID: 5d4f0b1b8cf0
Revises: 53f1d2b83a1d
Create Date: 2024-05-05 00:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5d4f0b1b8cf0'
down_revision = '53f1d2b83a1d'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'reunioes',
        sa.Column('postponed_from_start', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'reunioes',
        sa.Column('postponed_from_end', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column('reunioes', 'postponed_from_end')
    op.drop_column('reunioes', 'postponed_from_start')
