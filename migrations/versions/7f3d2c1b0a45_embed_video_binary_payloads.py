"""Embed binary payloads for video assets."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql
import os

# revision identifiers, used by Alembic.
revision = '7f3d2c1b0a45'
down_revision = '5f4c1d2e4b3c'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name if bind else None

    file_data_type = sa.LargeBinary()
    if dialect == 'mysql':
        file_data_type = mysql.LONGBLOB()

    op.add_column('video_assets', sa.Column('file_data', file_data_type, nullable=True))

    if not bind:
        return

    results = bind.execute(sa.text("SELECT id, file_path FROM video_assets"))
    rows = results.fetchall()
    if not rows:
        return

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    static_root = os.path.join(project_root, 'app', 'static')

    update_stmt = sa.text(
        "UPDATE video_assets SET file_data = :data, file_size = COALESCE(file_size, :size) WHERE id = :id"
    )

    for row in rows:
        file_path = row.file_path
        if not file_path:
            continue
        absolute_path = os.path.join(static_root, file_path)
        if not os.path.exists(absolute_path):
            continue
        try:
            with open(absolute_path, 'rb') as fh:
                data = fh.read()
        except OSError:
            continue
        bind.execute(update_stmt, {"data": data, "size": len(data), "id": row.id})


def downgrade():
    op.drop_column('video_assets', 'file_data')
