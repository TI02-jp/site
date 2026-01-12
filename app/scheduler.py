"""
Módulo de agendamento de tarefas
Gerencia jobs recorrentes usando APScheduler
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import logging
import atexit

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler()


def init_scheduler(app):
    """
    Inicializa o agendador de tarefas

    Args:
        app: Instância da aplicação Flask
    """
    # Importar dentro da função para evitar imports circulares
    from app.controllers.routes.blueprints.empresas import send_daily_tadeu_notification

    def job_wrapper():
        """Wrapper que executa a função dentro do contexto da aplicação Flask"""
        with app.app_context():
            try:
                send_daily_tadeu_notification()
            except Exception as e:
                logger.error(f"Erro ao executar notificação diária para Tadeu: {e}", exc_info=True)

    # Agendar notificação diária às 17h (horário de Brasília)
    scheduler.add_job(
        func=job_wrapper,
        trigger=CronTrigger(hour=17, minute=0, timezone='America/Sao_Paulo'),
        id='daily_tadeu_notification',
        name='Notificação diária para Tadeu - Inventário',
        replace_existing=True
    )

    # Iniciar o scheduler
    scheduler.start()
    logger.info("✓ Scheduler iniciado - Job diário configurado para 17h (America/Sao_Paulo)")

    # Desligar scheduler quando app terminar
    atexit.register(lambda: shutdown_scheduler())


def shutdown_scheduler():
    """Desliga o scheduler de forma segura"""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler desligado")
