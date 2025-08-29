"""Utility helpers for running standalone database migrations.

This module provides :class:`DatabaseManager`, a small wrapper around
``mysql.connector`` that abstracts connection handling and common
operations used by maintenance scripts.  It is intentionally lightweight
and does not depend on the application's SQLAlchemy models.
"""

import mysql.connector
from mysql.connector import errorcode
import logging
from dotenv import load_dotenv
import os
import re

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()


class DatabaseManager:
    """Simple wrapper for raw MySQL operations."""

    def __init__(self):
        """Initialize without establishing a connection."""
        self.connection = None
        self.cursor = None
        # Matches ``%s`` placeholders to enforce parameterized queries
        self._param_pattern = re.compile(r"%s")
        
    def connect(self):
        """Estabelece conexão com o banco de dados"""
        try:
            host = os.getenv('DB_HOST')
            database = os.getenv('DB_NAME')
            user = os.getenv('DB_USER')
            password = os.getenv('DB_PASSWORD')

            missing = [k for k, v in {
                'DB_HOST': host,
                'DB_NAME': database,
                'DB_USER': user,
                'DB_PASSWORD': password
            }.items() if not v]

            if missing:
                raise EnvironmentError(
                    f"Missing required environment variables: {', '.join(missing)}"
                )

            self.connection = mysql.connector.connect(
                host=host,
                database=database,
                user=user,
                password=password,
                autocommit=False
            )
            self.cursor = self.connection.cursor(dictionary=True)
            logger.info("Conexão com o MySQL estabelecida com sucesso")
            return True
            
        except mysql.connector.Error as err:
            if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
                logger.error("Erro: Acesso negado. Verifique usuário e senha")
            elif err.errno == errorcode.ER_BAD_DB_ERROR:
                logger.error(f"Banco de dados {os.getenv('DB_NAME')} não existe")
            else:
                logger.error(f"Erro de conexão: {err}")
            return False

    def _ensure_safe_params(self, query, params):
        """Evita execução de queries sem parâmetros quando placeholders estão presentes"""
        if self._param_pattern.search(query) and params is None:
            raise ValueError("Parâmetros devem ser fornecidos separadamente para evitar SQL Injection")

    def execute_query(self, query, params=None):
        """Executa uma query SQL de modificação (INSERT, UPDATE, DELETE, ALTER)"""
        try:
            self._ensure_safe_params(query, params)
            self.cursor.execute(query, params or ())
            self.connection.commit()
            logger.info("Query executada com sucesso")
            return True
        except (mysql.connector.Error, ValueError) as err:
            self.connection.rollback()
            logger.error(f"Erro na query: {err}\nQuery: {query}")
            return False

    def fetch_one(self, query, params=None):
        """Executa uma query SQL de consulta e retorna uma linha"""
        try:
            self._ensure_safe_params(query, params)
            self.cursor.execute(query, params or ())
            return self.cursor.fetchone()
        except (mysql.connector.Error, ValueError) as err:
            logger.error(f"Erro na query: {err}\nQuery: {query}")
            return None

    def fetch_all(self, query, params=None):
        """Executa uma query SQL de consulta e retorna todas as linhas"""
        try:
            self._ensure_safe_params(query, params)
            self.cursor.execute(query, params or ())
            return self.cursor.fetchall()
        except (mysql.connector.Error, ValueError) as err:
            logger.error(f"Erro na query: {err}\nQuery: {query}")
            return None

    def check_table_exists(self, table_name):
        """Verifica se uma tabela existe no banco de dados"""
        query = """
        SELECT COUNT(*) as count
        FROM information_schema.tables
        WHERE table_schema = %s AND table_name = %s
        """
        result = self.fetch_one(query, (os.getenv('DB_NAME'), table_name))
        return result['count'] > 0 if result else False

    def close(self):
        """Fecha a conexão com o banco de dados"""
        if self.connection and self.connection.is_connected():
            if self.cursor:
                self.cursor.close()
            self.connection.close()
            logger.info("Conexão MySQL encerrada")

def main():
    """Execute database maintenance operations when run as a script."""
    db = DatabaseManager()
    
    if not db.connect():
        return
    
    try:
        # 1. Verificar se a tabela existe
        if not db.check_table_exists('tbl_empresas'):
            logger.error("Tabela tbl_empresas não encontrada")
            return
        
        # 2. Alterar coluna para NOT NULL
        # CORREÇÃO: Alterado de 'DataAberturaEmpresa' para 'DataAbertura' para corresponder ao modelo.
        alter_query = """
        ALTER TABLE tbl_empresas
        MODIFY COLUMN DataAbertura DATE NOT NULL;
        """
        if db.execute_query(alter_query):
            logger.info("Coluna DataAbertura alterada para NOT NULL")
        else:
            logger.warning("Falha ao alterar coluna DataAbertura")

        # 3. Verificar se já existe PRIMARY KEY antes de tentar adicionar
        pk_check_query = """
        SELECT COUNT(*) as pk_count
        FROM information_schema.table_constraints
        WHERE table_schema = %s
        AND table_name = %s
        AND constraint_type = 'PRIMARY KEY'
        """
        pk_exists = db.fetch_one(pk_check_query, (os.getenv('DB_NAME'), 'tbl_empresas'))
        
        if pk_exists and pk_exists['pk_count'] == 0:
            # CORREÇÃO: Alterado de 'IdEmpresas' para 'id' para corresponder ao modelo.
            pk_query = """
            ALTER TABLE tbl_empresas
            ADD PRIMARY KEY (id);
            """
            if db.execute_query(pk_query):
                logger.info("Primary Key adicionada com sucesso na coluna 'id'")
            else:
                logger.warning("Falha ao adicionar Primary Key")
        else:
            logger.info("Primary Key já existe na tabela")

    except Exception as e:
        logger.error(f"Erro durante as operações: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
