"""add reforma tributaria progress table

Revision ID: d3f6c1b2e4a7
Revises: 1c2d3e4f5a6b
Create Date: 2024-10-01 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3f6c1b2e4a7'
down_revision = '1c2d3e4f5a6b'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'reforma_tributaria_progress',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('video_id', sa.String(length=128), nullable=False),
        sa.Column('video_title', sa.String(length=255), nullable=True),
        sa.Column('watched_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'video_id', name='uq_reforma_tributaria_progress'),
    )
    op.create_index(
        'ix_reforma_tributaria_progress_video_id',
        'reforma_tributaria_progress',
        ['video_id'],
    )


def downgrade():
    op.drop_index('ix_reforma_tributaria_progress_video_id', table_name='reforma_tributaria_progress')
    op.drop_table('reforma_tributaria_progress')
