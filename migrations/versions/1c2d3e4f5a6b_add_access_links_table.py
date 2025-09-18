"""add access links table

Revision ID: 1c2d3e4f5a6b
Revises: b2f3c0d6d9c4
Create Date: 2024-06-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '1c2d3e4f5a6b'
down_revision = 'b2f3c0d6d9c4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'access_links',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('category', sa.String(length=50), nullable=False),
        sa.Column('label', sa.String(length=100), nullable=False),
        sa.Column('url', sa.String(length=255), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_access_links_category',
        'access_links',
        ['category'],
    )


def downgrade():
    op.drop_index('ix_access_links_category', table_name='access_links')
    op.drop_table('access_links')
