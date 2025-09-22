"""Align video library with portal tags and file uploads"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '5f4c1d2e4b3c'
down_revision = 'd7b8bf3f3f2a'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('video_folders') as batch_op:
        batch_op.drop_column('cover_image')

    with op.batch_alter_table('video_modules') as batch_op:
        batch_op.drop_column('cover_image')

    op.drop_table('video_module_tags')
    op.drop_table('video_tags')

    with op.batch_alter_table('video_assets') as batch_op:
        batch_op.drop_column('video_url')
        batch_op.drop_column('storage_path')
        batch_op.add_column(sa.Column('file_path', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('original_filename', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('mime_type', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('file_size', sa.BigInteger(), nullable=True))

    op.create_table(
        'video_module_portal_tags',
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.Column('tag_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['module_id'], ['video_modules.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tag_id'], ['tags.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('module_id', 'tag_id'),
    )


def downgrade():
    op.drop_table('video_module_portal_tags')

    with op.batch_alter_table('video_assets') as batch_op:
        batch_op.drop_column('file_size')
        batch_op.drop_column('mime_type')
        batch_op.drop_column('original_filename')
        batch_op.drop_column('file_path')
        batch_op.add_column(sa.Column('storage_path', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('video_url', sa.String(length=255), nullable=True))

    op.create_table(
        'video_tags',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('slug', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
        sa.UniqueConstraint('slug'),
    )

    op.create_table(
        'video_module_tags',
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.Column('tag_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['module_id'], ['video_modules.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tag_id'], ['video_tags.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('module_id', 'tag_id'),
    )

    with op.batch_alter_table('video_modules') as batch_op:
        batch_op.add_column(sa.Column('cover_image', sa.String(length=255), nullable=True))

    with op.batch_alter_table('video_folders') as batch_op:
        batch_op.add_column(sa.Column('cover_image', sa.String(length=255), nullable=True))
