"""Script para sincronizar migrações do Alembic com o estado real do banco.

Este script resolve problemas quando o banco de dados foi modificado manualmente
e a tabela alembic_version está desatualizada.
"""

import logging
import os
import mysql.connector
from mysql.connector import errorcode
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_db_connection():
    """Cria uma conexão com o banco de dados."""
    host = os.getenv('DB_HOST')
    database = os.getenv('DB_NAME')
    user = os.getenv('DB_USER')
    password = os.getenv('DB_PASSWORD', '')  # Aceita vazio para desenvolvimento local

    if not host or not database or not user:
        raise EnvironmentError("DB_HOST, DB_NAME e DB_USER são obrigatórios")

    if not password:
        logger.warning("DB_PASSWORD está vazio; conectando ao MySQL sem senha (apenas recomendado para desenvolvimento local).")

    return mysql.connector.connect(
        host=host,
        database=database,
        user=user,
        password=password,
        autocommit=False
    )


def check_column_exists(cursor, table_name, column_name):
    """Verifica se uma coluna existe em uma tabela."""
    query = """
    SELECT COUNT(*) as count
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
    AND table_name = %s
    AND column_name = %s
    """
    cursor.execute(query, (table_name, column_name))
    result = cursor.fetchone()
    return result and result[0] > 0


def add_recurrence_columns():
    """Adiciona colunas de recorrência se não existirem."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        columns_to_add = [
            ('recorrencia_tipo', "ENUM('NENHUMA', 'DIARIA', 'SEMANAL', 'QUINZENAL', 'MENSAL', 'ANUAL') NOT NULL DEFAULT 'NENHUMA'"),
            ('recorrencia_fim', "DATE NULL"),
            ('recorrencia_grupo_id', "VARCHAR(36) NULL"),
            ('recorrencia_dias_semana', "VARCHAR(20) NULL"),
        ]

        for column_name, column_def in columns_to_add:
            if check_column_exists(cursor, 'reunioes', column_name):
                logger.info(f"Coluna {column_name} já existe na tabela reunioes")
                continue

            # Adicionar coluna
            add_column_query = f"""
            ALTER TABLE reunioes
            ADD COLUMN {column_name} {column_def}
            """

            try:
                cursor.execute(add_column_query)
                conn.commit()
                logger.info(f"Coluna {column_name} adicionada com sucesso na tabela reunioes")
            except Exception as e:
                conn.rollback()
                logger.error(f"Falha ao adicionar coluna {column_name}: {e}")
                return False

        logger.info("Colunas de recorrência verificadas/adicionadas com sucesso")
        cursor.close()
        conn.close()
        return True

    except Exception as e:
        logger.error(f"Erro durante adição de colunas: {e}")
        return False


def update_alembic_version():
    """Atualiza a versão do Alembic para a última migração."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Verificar se a tabela alembic_version existe
        cursor.execute("SHOW TABLES LIKE 'alembic_version'")
        if not cursor.fetchone():
            logger.error("Tabela alembic_version não encontrada")
            cursor.close()
            conn.close()
            return False

        # Atualizar para a última versão de migração que inclui recorrência
        # Esta é a migração add_meeting_recurrence_simple.py
        update_query = """
        UPDATE alembic_version
        SET version_num = 'add_meeting_recurrence'
        """

        cursor.execute(update_query)
        conn.commit()
        logger.info("Versão do Alembic atualizada para: add_meeting_recurrence")

        cursor.close()
        conn.close()
        return True

    except Exception as e:
        logger.error(f"Erro ao atualizar versão do Alembic: {e}")
        return False


if __name__ == "__main__":
    logger.info("Iniciando correção de migrações...")

    # Passo 1: Adicionar colunas de recorrência se não existirem
    if not add_recurrence_columns():
        logger.error("Falha ao adicionar colunas de recorrência")
        exit(1)

    # Passo 2: Atualizar versão do Alembic
    if not update_alembic_version():
        logger.error("Falha ao atualizar versão do Alembic")
        exit(1)

    logger.info("Correção de migrações concluída com sucesso!")
    logger.info("Agora você pode executar: flask db upgrade")
