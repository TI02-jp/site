"""add meet configuration fields

Revision ID: f2a1c3d4b5e6
Revises: 1d2f3a4b5c67
Create Date: 2025-02-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
import json


# revision identifiers, used by Alembic.
revision = 'f2a1c3d4b5e6'
down_revision = '1d2f3a4b5c67'
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
    op.add_column('reunioes', sa.Column('meet_host_id', sa.Integer(), nullable=True))
    op.add_column('reunioes', sa.Column('meet_settings', sa.JSON(), nullable=True))
    op.create_foreign_key(
        'fk_reunioes_meet_host_id_users',
        'reunioes',
        'users',
        ['meet_host_id'],
        ['id'],
        ondelete='SET NULL',
    )
    bind = op.get_bind()
    settings_json = json.dumps(_default_settings())
    bind.execute(sa.text("UPDATE reunioes SET meet_settings = :settings"), {"settings": settings_json})
    op.alter_column('reunioes', 'meet_settings', nullable=False)


def downgrade():
    op.drop_constraint('fk_reunioes_meet_host_id_users', 'reunioes', type_='foreignkey')
    op.drop_column('reunioes', 'meet_settings')
    op.drop_column('reunioes', 'meet_host_id')
