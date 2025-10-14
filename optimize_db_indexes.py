"""Script para criar índices no banco de dados e melhorar performance.

Este script cria índices importantes nas tabelas principais para melhorar
a performance das queries mais frequentes do portal.
"""

import logging
from database import DatabaseManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_database_indexes():
    """Cria índices importantes para melhorar performance."""
    db = DatabaseManager()

    if not db.connect():
        logger.error("Falha ao conectar no banco de dados")
        return False

    try:
        # Índices para tabela reunioes (melhorar performance de queries por data)
        indexes_to_create = [
            # Índice composto para buscar reuniões por data de início e status
            ("reunioes", "idx_reunioes_inicio_status", ["inicio", "status"]),
            # Índice para buscar reuniões por criador
            ("reunioes", "idx_reunioes_criador", ["criador_id"]),
            # Índice para buscar reuniões por evento do Google
            ("reunioes", "idx_reunioes_google_event", ["google_event_id"]),
            # Índice para buscar reuniões por grupo de recorrência
            ("reunioes", "idx_reunioes_recorrencia_grupo", ["recorrencia_grupo_id"]),

            # Índices para tabela reuniao_participantes
            ("reuniao_participantes", "idx_reuniao_part_reuniao", ["reuniao_id"]),
            ("reuniao_participantes", "idx_reuniao_part_usuario", ["id_usuario"]),

            # Índices para tabela users (melhorar buscas por email e status)
            ("users", "idx_users_ativo", ["ativo"]),
            ("users", "idx_users_email", ["email"]),

            # Índices para tabela tasks
            ("tasks", "idx_tasks_status", ["status"]),
            ("tasks", "idx_tasks_tag", ["tag_id"]),
            ("tasks", "idx_tasks_assigned", ["assigned_to"]),
            ("tasks", "idx_tasks_created_by", ["created_by"]),

            # Índices para tabela general_calendar_events
            ("general_calendar_events", "idx_gen_cal_start_date", ["start_date"]),
            ("general_calendar_events", "idx_gen_cal_end_date", ["end_date"]),
            ("general_calendar_events", "idx_gen_cal_created_by", ["created_by_id"]),
        ]

        for table_name, index_name, columns in indexes_to_create:
            # Verificar se o índice já existe
            check_query = """
            SELECT COUNT(*) as count
            FROM information_schema.statistics
            WHERE table_schema = DATABASE()
            AND table_name = %s
            AND index_name = %s
            """
            result = db.fetch_one(check_query, (table_name, index_name))

            if result and result['count'] > 0:
                logger.info(f"Índice {index_name} já existe na tabela {table_name}")
                continue

            # Criar índice
            columns_str = ", ".join([f"`{col}`" for col in columns])
            create_index_query = f"""
            CREATE INDEX `{index_name}` ON `{table_name}` ({columns_str})
            """

            if db.execute_query(create_index_query):
                logger.info(f"Índice {index_name} criado com sucesso na tabela {table_name}")
            else:
                logger.warning(f"Falha ao criar índice {index_name} na tabela {table_name}")

        logger.info("Processo de criação de índices concluído com sucesso")
        return True

    except Exception as e:
        logger.error(f"Erro durante criação de índices: {e}")
        return False
    finally:
        db.close()


if __name__ == "__main__":
    logger.info("Iniciando otimização de índices do banco de dados...")
    success = create_database_indexes()
    if success:
        logger.info("Otimização concluída com sucesso!")
    else:
        logger.error("Otimização falhou. Verifique os logs acima.")
