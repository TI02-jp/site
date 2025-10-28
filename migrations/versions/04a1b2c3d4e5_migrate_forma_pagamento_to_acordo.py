"""migrate_forma_pagamento_to_acordo

Revision ID: 04a1b2c3d4e5
Revises: 03e8f20e9748
Create Date: 2025-10-28 14:45:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision = '04a1b2c3d4e5'
down_revision = '03e8f20e9748'
branch_labels = None
depends_on = None


def upgrade():
    # Migrar os valores antigos de forma_pagamento para o campo acordo
    # Valores antigos: SEM ACORDO, OK - PAGO, CORTESIA, A VISTA, DEBITAR, TADEU H.
    # Esses valores serão copiados para 'acordo' e 'forma_pagamento' será limpo

    connection = op.get_bind()

    # Copiar valores de forma_pagamento para acordo (apenas se acordo estiver vazio)
    connection.execute(text("""
        UPDATE cadastro_notas
        SET acordo = forma_pagamento
        WHERE acordo IS NULL OR acordo = ''
    """))

    # Limpar forma_pagamento para permitir que o usuário escolha as novas opções
    connection.execute(text("""
        UPDATE cadastro_notas
        SET forma_pagamento = ''
    """))


def downgrade():
    # Reverter a migração: copiar acordo de volta para forma_pagamento
    connection = op.get_bind()

    connection.execute(text("""
        UPDATE cadastro_notas
        SET forma_pagamento = acordo
        WHERE acordo IS NOT NULL
    """))

    connection.execute(text("""
        UPDATE cadastro_notas
        SET acordo = NULL
    """))
