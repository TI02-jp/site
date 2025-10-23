"""add_reunioes_performance_indices

Revision ID: 23ebc21a7eaa
Revises: c6b89eddea6a
Create Date: 2025-10-23 08:42:44.971897

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '23ebc21a7eaa'
down_revision = 'c6b89eddea6a'
branch_labels = None
depends_on = None


def upgrade():
    """Add critical performance indices to reunioes table to eliminate lock wait timeouts.

    These indices address the 50s lock wait timeout issue in /api/reunioes by:
    1. Optimizing date range queries (inicio, fim)
    2. Speeding up joinedload operations (criador_id, meet_host_id)
    3. Preventing duplicate Google event syncs (google_event_id)
    """

    # Composite index for date range queries - CRITICAL for combine_events()
    # Query: Reuniao.query.filter(inicio >= six_months_ago, inicio <= six_months_ahead)
    op.create_index(
        'idx_reunioes_inicio_fim',
        'reunioes',
        ['inicio', 'fim'],
        unique=False
    )

    # Index for criador_id - optimizes joinedload(Reuniao.criador)
    op.create_index(
        'idx_reunioes_criador_id',
        'reunioes',
        ['criador_id'],
        unique=False
    )

    # Index for meet_host_id - optimizes joinedload(Reuniao.meet_host)
    op.create_index(
        'idx_reunioes_meet_host_id',
        'reunioes',
        ['meet_host_id'],
        unique=False
    )

    # Index for google_event_id - prevents duplicate syncs and speeds up lookups
    op.create_index(
        'idx_reunioes_google_event_id',
        'reunioes',
        ['google_event_id'],
        unique=False
    )

    # Index for status - useful for filtering by meeting status
    op.create_index(
        'idx_reunioes_status',
        'reunioes',
        ['status'],
        unique=False
    )


def downgrade():
    """Remove reunioes performance indices."""

    op.drop_index('idx_reunioes_status', table_name='reunioes')
    op.drop_index('idx_reunioes_google_event_id', table_name='reunioes')
    op.drop_index('idx_reunioes_meet_host_id', table_name='reunioes')
    op.drop_index('idx_reunioes_criador_id', table_name='reunioes')
    op.drop_index('idx_reunioes_inicio_fim', table_name='reunioes')
