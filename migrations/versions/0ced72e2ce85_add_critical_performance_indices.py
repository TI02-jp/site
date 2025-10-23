"""add_critical_performance_indices

Revision ID: 0ced72e2ce85
Revises: d3f7890abcde
Create Date: 2025-10-23 14:34:59.035890

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0ced72e2ce85'
down_revision = 'd3f7890abcde'
branch_labels = None
depends_on = None


def upgrade():
    """Add critical performance indices to optimize slow queries.

    These indices address the 5+ second response time in /api/reunioes by:
    1. idx_reunioes_date_range: Optimizes date range queries (inicio >= X AND inicio <= Y)
    2. idx_reunioes_status_date: Optimizes filtering by status with date ordering
    3. idx_user_tags_lookup: Optimizes user-tag joins in sidebar queries
    4. idx_reuniao_participantes_lookup: Optimizes participant lookups
    """

    # Skip index creation if they already exist (for safety)
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Check reunioes table indices
    existing_reunioes_indices = {idx['name'] for idx in inspector.get_indexes('reunioes')}

    if 'idx_reunioes_date_range' not in existing_reunioes_indices:
        op.create_index(
            'idx_reunioes_date_range',
            'reunioes',
            ['inicio', 'fim'],
            unique=False
        )

    if 'idx_reunioes_status_date' not in existing_reunioes_indices:
        op.create_index(
            'idx_reunioes_status_date',
            'reunioes',
            ['status', 'inicio'],
            unique=False
        )

    # Check user_tags table indices
    if inspector.has_table('user_tags'):
        existing_user_tags_indices = {idx['name'] for idx in inspector.get_indexes('user_tags')}

        if 'idx_user_tags_lookup' not in existing_user_tags_indices:
            op.create_index(
                'idx_user_tags_lookup',
                'user_tags',
                ['user_id', 'tag_id'],
                unique=False
            )

    # Check reuniao_participantes table indices
    if inspector.has_table('reuniao_participantes'):
        existing_participantes_indices = {idx['name'] for idx in inspector.get_indexes('reuniao_participantes')}

        if 'idx_reuniao_participantes_lookup' not in existing_participantes_indices:
            op.create_index(
                'idx_reuniao_participantes_lookup',
                'reuniao_participantes',
                ['reuniao_id', 'id_usuario'],
                unique=False
            )


def downgrade():
    """Remove performance indices."""

    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Drop reunioes indices
    existing_reunioes_indices = {idx['name'] for idx in inspector.get_indexes('reunioes')}

    if 'idx_reunioes_status_date' in existing_reunioes_indices:
        op.drop_index('idx_reunioes_status_date', table_name='reunioes')

    if 'idx_reunioes_date_range' in existing_reunioes_indices:
        op.drop_index('idx_reunioes_date_range', table_name='reunioes')

    # Drop user_tags indices
    if inspector.has_table('user_tags'):
        existing_user_tags_indices = {idx['name'] for idx in inspector.get_indexes('user_tags')}

        if 'idx_user_tags_lookup' in existing_user_tags_indices:
            op.drop_index('idx_user_tags_lookup', table_name='user_tags')

    # Drop reuniao_participantes indices
    if inspector.has_table('reuniao_participantes'):
        existing_participantes_indices = {idx['name'] for idx in inspector.get_indexes('reuniao_participantes')}

        if 'idx_reuniao_participantes_lookup' in existing_participantes_indices:
            op.drop_index('idx_reuniao_participantes_lookup', table_name='reuniao_participantes')
