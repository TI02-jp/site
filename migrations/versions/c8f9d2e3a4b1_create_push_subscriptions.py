"""create_push_subscriptions

Revision ID: c8f9d2e3a4b1
Revises: b57bef681887
Create Date: 2025-10-22 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c8f9d2e3a4b1'
down_revision = 'b57bef681887'
branch_labels = None
depends_on = None


def upgrade():
    # Criar tabela push_subscriptions
    op.create_table(
        'push_subscriptions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('endpoint', sa.String(length=500), nullable=False),
        sa.Column('p256dh_key', sa.String(length=200), nullable=False),
        sa.Column('auth_key', sa.String(length=100), nullable=False),
        sa.Column('user_agent', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('endpoint')
    )


def downgrade():
    op.drop_table('push_subscriptions')
