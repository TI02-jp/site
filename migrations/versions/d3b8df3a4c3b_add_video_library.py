"""Add video library tables"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3b8df3a4c3b'
down_revision = '5aaf1e2d4c9b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'video_folders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('drive_folder_id', sa.String(length=255), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('drive_folder_id'),
    )

    op.create_table(
        'videos',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('folder_id', sa.Integer(), nullable=False),
        sa.Column('drive_file_id', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('mime_type', sa.String(length=120), nullable=True),
        sa.Column('size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('duration_ms', sa.BigInteger(), nullable=True),
        sa.Column('thumbnail_url', sa.String(length=500), nullable=True),
        sa.Column('drive_modified_time', sa.DateTime(), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['folder_id'], ['video_folders.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_videos_folder_id', 'videos', ['folder_id'], unique=False)
    op.create_index('ix_videos_drive_file_id', 'videos', ['drive_file_id'], unique=False)

    op.create_table(
        'video_permissions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('folder_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('sector_id', sa.Integer(), nullable=True),
        sa.Column('can_manage', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['folder_id'], ['video_folders.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['sector_id'], ['setores.id'], ondelete='CASCADE'),
        sa.CheckConstraint('NOT (user_id IS NULL AND sector_id IS NULL)', name='ck_video_permissions_target'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_video_permissions_folder_id', 'video_permissions', ['folder_id'], unique=False)
    op.create_index('ix_video_permissions_user_id', 'video_permissions', ['user_id'], unique=False)
    op.create_index('ix_video_permissions_sector_id', 'video_permissions', ['sector_id'], unique=False)
    op.create_unique_constraint('uq_video_permissions_user', 'video_permissions', ['folder_id', 'user_id'])
    op.create_unique_constraint('uq_video_permissions_sector', 'video_permissions', ['folder_id', 'sector_id'])


def downgrade() -> None:
    op.drop_constraint('uq_video_permissions_sector', 'video_permissions', type_='unique')
    op.drop_constraint('uq_video_permissions_user', 'video_permissions', type_='unique')
    op.drop_index('ix_video_permissions_sector_id', table_name='video_permissions')
    op.drop_index('ix_video_permissions_user_id', table_name='video_permissions')
    op.drop_index('ix_video_permissions_folder_id', table_name='video_permissions')
    op.drop_table('video_permissions')
    op.drop_index('ix_videos_drive_file_id', table_name='videos')
    op.drop_index('ix_videos_folder_id', table_name='videos')
    op.drop_table('videos')
    op.drop_table('video_folders')
