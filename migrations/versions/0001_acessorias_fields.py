"""add acessorias fields"""

from alembic import op
import sqlalchemy as sa

revision = '0001_acessorias_fields'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tbl_empresas', sa.Column('acessorias_identifier', sa.String(length=32), nullable=True))
    op.add_column('tbl_empresas', sa.Column('acessorias_company_id', sa.Integer(), nullable=True))
    op.add_column('tbl_empresas', sa.Column('acessorias_synced_at', sa.DateTime(), nullable=True))
    op.create_index('ix_tbl_empresas_acessorias_identifier', 'tbl_empresas', ['acessorias_identifier'], unique=True)

    op.create_table(
        'company_obligations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('tbl_empresas.id'), nullable=False),
        sa.Column('nome', sa.String(length=100), nullable=False),
        sa.Column('status', sa.String(length=50)),
        sa.Column('entregues', sa.Integer()),
        sa.Column('atrasadas', sa.Integer()),
        sa.Column('proximos_30d', sa.Integer()),
        sa.Column('futuras_30p', sa.Integer()),
        sa.UniqueConstraint('company_id', 'nome', name='uq_company_obligation'),
    )


def downgrade():
    op.drop_table('company_obligations')
    op.drop_index('ix_tbl_empresas_acessorias_identifier', table_name='tbl_empresas')
    op.drop_column('tbl_empresas', 'acessorias_synced_at')
    op.drop_column('tbl_empresas', 'acessorias_company_id')
    op.drop_column('tbl_empresas', 'acessorias_identifier')
