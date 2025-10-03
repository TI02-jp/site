"""Add table for storing multiple announcement attachments."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import table, column, select

# revision identifiers, used by Alembic.
revision = 'fe0c2c1a5b6d'
down_revision = 'e2d5f74ad36c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'announcement_attachments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'announcement_id',
            sa.Integer(),
            sa.ForeignKey('announcements.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('file_path', sa.String(length=255), nullable=False),
        sa.Column('original_name', sa.String(length=255), nullable=True),
        sa.Column('mime_type', sa.String(length=128), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('CURRENT_TIMESTAMP'),
        ),
    )

    connection = op.get_bind()
    announcements = table(
        'announcements',
        column('id', sa.Integer()),
        column('attachment_path', sa.String(length=255)),
        column('attachment_name', sa.String(length=255)),
        column('created_at', sa.DateTime()),
    )
    attachments = table(
        'announcement_attachments',
        column('announcement_id', sa.Integer()),
        column('file_path', sa.String(length=255)),
        column('original_name', sa.String(length=255)),
        column('mime_type', sa.String(length=128)),
        column('created_at', sa.DateTime()),
    )

    legacy_rows = connection.execute(
        select(
            announcements.c.id,
            announcements.c.attachment_path,
            announcements.c.attachment_name,
            announcements.c.created_at,
        ).where(announcements.c.attachment_path.isnot(None))
    ).fetchall()

    if legacy_rows:
        connection.execute(
            attachments.insert(),
            [
                {
                    'announcement_id': row.id,
                    'file_path': row.attachment_path,
                    'original_name': row.attachment_name,
                    'mime_type': None,
                    'created_at': row.created_at,
                }
                for row in legacy_rows
            ],
        )

    op.alter_column(
        'announcement_attachments',
        'created_at',
        server_default=None,
        existing_type=sa.DateTime(),
    )


def downgrade() -> None:
    op.drop_table('announcement_attachments')
