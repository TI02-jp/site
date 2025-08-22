import logging
import os
import re

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()


class DatabaseManager:
    def __init__(self):
        self.engine = None
        self.session = None

    def connect(self):
        """Estabelece conexão com o banco de dados usando SQLAlchemy"""
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

            url = f"mysql+mysqlconnector://{user}:{password}@{host}/{database}"
            self.engine = create_engine(url, future=True)
            self.session = Session(self.engine)
            logger.info("Conexão com o MySQL estabelecida com sucesso via SQLAlchemy")
            return True
        except SQLAlchemyError as err:
            logger.error(f"Erro de conexão: {err}")
            return False
        except Exception as err:
            logger.error(f"Erro de configuração: {err}")
            return False

    def _sanitize_params(self, params):
        """Sanitiza parametros para prevenir SQL injection simples."""
        sanitized = []
        for p in params or ():
            if isinstance(p, str):
                p = re.sub(r"['\";--]", "", p)
            sanitized.append(p)
        return tuple(sanitized)

    def execute_query(self, query, params=None):
        """Executa uma query SQL de modificação (INSERT, UPDATE, DELETE, ALTER)"""
        try:
            safe_params = self._sanitize_params(params)
            self.session.execute(text(query), safe_params)
            self.session.commit()
            logger.info("Query executada com sucesso")
            return True
        except SQLAlchemyError as err:
            self.session.rollback()
            logger.error(f"Erro na query: {err}\nQuery: {query}")
            return False

    def fetch_one(self, query, params=None):
        """Executa uma query SQL de consulta e retorna uma linha"""
        try:
            safe_params = self._sanitize_params(params)
            result = self.session.execute(text(query), safe_params)
            return result.mappings().first()
        except SQLAlchemyError as err:
            logger.error(f"Erro na query: {err}\nQuery: {query}")
            return None

    def fetch_all(self, query, params=None):
        """Executa uma query SQL de consulta e retorna todas as linhas"""
        try:
            safe_params = self._sanitize_params(params)
            result = self.session.execute(text(query), safe_params)
            return result.mappings().all()
        except SQLAlchemyError as err:
            logger.error(f"Erro na query: {err}\nQuery: {query}")
            return None

    def check_table_exists(self, table_name):
        """Verifica se uma tabela existe no banco de dados"""
        query = (
            """
        SELECT COUNT(*) as count
        FROM information_schema.tables
        WHERE table_schema = :schema AND table_name = :table
        """
        )
        result = self.fetch_one(query, {'schema': os.getenv('DB_NAME'), 'table': table_name})
        return result['count'] > 0 if result else False

    def close(self):
        """Fecha a conexão com o banco de dados"""
        if self.session:
            self.session.close()
        if self.engine:
            self.engine.dispose()
            logger.info("Conexão SQLAlchemy encerrada")


def main():
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
        WHERE table_schema = :schema
        AND table_name = :table
        AND constraint_type = 'PRIMARY KEY'
        """
        pk_exists = db.fetch_one(pk_check_query, {'schema': os.getenv('DB_NAME'), 'table': 'tbl_empresas'})

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

