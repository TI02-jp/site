"""Add composite indexes focused on /inventario route filters.

Revision ID: ab12cd34ef56
Revises: 9ac9ec3e703a
Create Date: 2026-02-12 18:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "ab12cd34ef56"
down_revision = "9ac9ec3e703a"
branch_labels = None
depends_on = None


def _create_index_if_missing(table_name, index_name, columns):
    inspector = sa.inspect(op.get_bind())
    existing = {idx.get("name") for idx in inspector.get_indexes(table_name)}
    if index_name in existing:
        return
    table_columns = {col.get("name") for col in inspector.get_columns(table_name)}
    if any(col not in table_columns for col in columns):
        return
    op.create_index(index_name, table_name, columns, unique=False)


def _drop_index_if_exists(table_name, index_name):
    inspector = sa.inspect(op.get_bind())
    existing = {idx.get("name") for idx in inspector.get_indexes(table_name)}
    if index_name in existing:
        op.drop_index(index_name, table_name=table_name)


def upgrade():
    # /inventario filters: status + encerramento_fiscal, then join by empresa_id.
    _create_index_if_missing(
        "tbl_inventario",
        "idx_inventario_status_encerramento_empresa",
        ["status", "encerramento_fiscal", "empresa_id"],
    )

    # /inventario base filters/order: ativo + tipo_empresa + tributacao + nome_empresa.
    _create_index_if_missing(
        "tbl_empresas",
        "idx_empresas_inventario_filters_nome",
        ["ativo", "tipo_empresa", "tributacao", "nome_empresa"],
    )

    # Supports sort by codigo under the same filter profile.
    _create_index_if_missing(
        "tbl_empresas",
        "idx_empresas_inventario_filters_codigo",
        ["ativo", "tipo_empresa", "tributacao", "codigo_empresa"],
    )


def downgrade():
    _drop_index_if_exists("tbl_empresas", "idx_empresas_inventario_filters_codigo")
    _drop_index_if_exists("tbl_empresas", "idx_empresas_inventario_filters_nome")
    _drop_index_if_exists("tbl_inventario", "idx_inventario_status_encerramento_empresa")

