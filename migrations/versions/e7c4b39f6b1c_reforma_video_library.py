"""Create Reforma Tributária video library tables"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e7c4b39f6b1c'
down_revision = 'd3f6c1b2e4a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'reforma_modules',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )

    op.create_table(
        'reforma_videos',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=True),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=True),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('content_type', sa.String(length=127), nullable=True),
        sa.Column('file_size', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['module_id'], ['reforma_modules.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('filename'),
    )

    op.drop_table('reforma_tributaria_progress')

    op.create_table(
        'reforma_tributaria_progress',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('video_id', sa.Integer(), nullable=False),
        sa.Column('video_title', sa.String(length=255), nullable=True),
        sa.Column('watched_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['video_id'], ['reforma_videos.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'video_id', name='uq_reforma_tributaria_progress'),
    )


def downgrade() -> None:
    op.drop_table('reforma_tributaria_progress')
    op.drop_table('reforma_videos')
    op.drop_table('reforma_modules')

    op.create_table(
        'reforma_tributaria_progress',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('video_id', sa.String(length=128), nullable=False),
        sa.Column('video_title', sa.String(length=255), nullable=True),
        sa.Column('watched_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'video_id', name='uq_reforma_tributaria_progress'),
    )
