"""add video library tables

Revision ID: d5d1a89ec0a9
Revises: 8f3aa2e55b15
Create Date: 2025-09-20 00:00:00.000000

"""
from __future__ import annotations

from datetime import datetime

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d5d1a89ec0a9"
down_revision = "8f3aa2e55b15"
branch_labels = None
depends_on = None


video_collections_table = sa.table(
    "video_collections",
    sa.column("id", sa.Integer()),
    sa.column("name", sa.String(length=120)),
    sa.column("description", sa.String(length=255)),
    sa.column("drive_folder_url", sa.String(length=255)),
    sa.column("created_at", sa.DateTime()),
    sa.column("updated_at", sa.DateTime()),
)


def upgrade():
    op.create_table(
        "video_collections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("drive_folder_url", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "video_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("collection_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=150), nullable=False),
        sa.Column("url", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint([
            "collection_id"
        ], ["video_collections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    now = datetime.utcnow()
    op.bulk_insert(
        video_collections_table,
        [
            {
                "name": "Vídeos JP",
                "description": "Treinamentos e comunicados da JP Contábil.",
                "drive_folder_url": "https://drive.google.com/drive/u/1/folders/1DJHoZjMX88LaZQr1T_bi50oQB_BIg97a",
                "created_at": now,
                "updated_at": now,
            }
        ],
    )


def downgrade():
    op.drop_table("video_links")
    op.drop_table("video_collections")
