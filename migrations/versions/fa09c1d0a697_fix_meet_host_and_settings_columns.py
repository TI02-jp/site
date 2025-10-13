"""fix_meet_host_and_settings_columns

Revision ID: fa09c1d0a697
Revises: add_meeting_recurrence
Create Date: 2025-10-13 17:00:11.490135

"""
from alembic import op
import sqlalchemy as sa
import json


# revision identifiers, used by Alembic.
revision = 'fa09c1d0a697'
down_revision = 'add_meeting_recurrence'
branch_labels = None
depends_on = None


def _default_settings():
    return {
        "quick_access_enabled": True,
        "mute_on_join": False,
        "allow_chat": True,
        "allow_screen_share": True,
    }


def upgrade():
    # Check if owner_id exists and rename it to meet_host_id
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [col['name'] for col in inspector.get_columns('reunioes')]

    if 'owner_id' in columns and 'meet_host_id' not in columns:
        # Rename owner_id to meet_host_id using raw SQL for MySQL
        bind.execute(sa.text(
            "ALTER TABLE reunioes CHANGE COLUMN owner_id meet_host_id INT(11) NULL"
        ))
    elif 'owner_id' not in columns and 'meet_host_id' not in columns:
        # Neither exists, add meet_host_id
        op.add_column('reunioes', sa.Column('meet_host_id', sa.Integer(), nullable=True))
        op.create_foreign_key(
            'fk_reunioes_meet_host_id_users',
            'reunioes',
            'users',
            ['meet_host_id'],
            ['id'],
            ondelete='SET NULL',
        )

    # Add meet_settings column if it doesn't exist
    if 'meet_settings' not in columns:
        op.add_column('reunioes', sa.Column('meet_settings', sa.JSON(), nullable=True))
        settings_json = json.dumps(_default_settings())
        bind.execute(sa.text("UPDATE reunioes SET meet_settings = :settings"), {"settings": settings_json})
        bind.execute(sa.text("ALTER TABLE reunioes MODIFY COLUMN meet_settings JSON NOT NULL"))


def downgrade():
    # Rename back to owner_id using raw SQL for MySQL
    bind = op.get_bind()
    bind.execute(sa.text(
        "ALTER TABLE reunioes CHANGE COLUMN meet_host_id owner_id INT(11) NULL"
    ))
    # Drop meet_settings
    op.drop_column('reunioes', 'meet_settings')
