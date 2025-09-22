"""Rename Reforma Tributária video tables to generic media names."""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'e7c4b39f6b1c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table('reforma_modules', 'media_folders')
    op.rename_table('reforma_videos', 'media_videos')
    op.rename_table('reforma_tributaria_progress', 'media_video_progress')

    with op.batch_alter_table('media_videos', schema=None) as batch_op:
        batch_op.alter_column(
            'module_id',
            new_column_name='folder_id',
            existing_type=sa.Integer(),
            existing_nullable=True,
        )

    with op.batch_alter_table('media_video_progress', schema=None) as batch_op:
        batch_op.drop_constraint('uq_reforma_tributaria_progress', type_='unique')
        batch_op.create_unique_constraint('uq_media_video_progress', ['user_id', 'video_id'])


def downgrade() -> None:
    with op.batch_alter_table('media_video_progress', schema=None) as batch_op:
        batch_op.drop_constraint('uq_media_video_progress', type_='unique')
        batch_op.create_unique_constraint('uq_reforma_tributaria_progress', ['user_id', 'video_id'])

    with op.batch_alter_table('media_videos', schema=None) as batch_op:
        batch_op.alter_column(
            'folder_id',
            new_column_name='module_id',
            existing_type=sa.Integer(),
            existing_nullable=True,
        )

    op.rename_table('media_video_progress', 'reforma_tributaria_progress')
    op.rename_table('media_videos', 'reforma_videos')
    op.rename_table('media_folders', 'reforma_modules')
