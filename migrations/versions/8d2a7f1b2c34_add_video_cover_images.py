from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '8d2a7f1b2c34'
down_revision = '5f4c1d2e4b3c'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('video_folders') as batch_op:
        batch_op.add_column(sa.Column('cover_image_path', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('cover_image_name', sa.String(length=255), nullable=True))

    with op.batch_alter_table('video_modules') as batch_op:
        batch_op.add_column(sa.Column('cover_image_path', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('cover_image_name', sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table('video_modules') as batch_op:
        batch_op.drop_column('cover_image_name')
        batch_op.drop_column('cover_image_path')

    with op.batch_alter_table('video_folders') as batch_op:
        batch_op.drop_column('cover_image_name')
        batch_op.drop_column('cover_image_path')
