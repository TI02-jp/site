"""
Módulo de agendamento de tarefas
Gerencia jobs recorrentes usando APScheduler
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from datetime import datetime, timedelta
import logging
import atexit
import os
from zoneinfo import ZoneInfo

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

    def test_cristiano_wrapper():
        """Wrapper para envio de teste do inventario apenas para Cristiano."""
        with app.app_context():
            try:
                send_daily_tadeu_notification(recipients=("Cristiano",))
            except Exception as e:
                logger.error(f"Erro ao executar teste de inventario para Cristiano: {e}", exc_info=True)

    # Agendar notificação diária às 17h30 (horário de Brasília)
    scheduler.add_job(
        func=job_wrapper,
        trigger=CronTrigger(hour=17, minute=30, timezone='America/Sao_Paulo'),
        id='daily_tadeu_notification',
        name='Notificação diária para Tadeu e Cristiano - Inventário',
        replace_existing=True
    )

    if os.getenv("INVENTARIO_TEST_CRISTIANO_AT_14") == "1":
        tz = ZoneInfo("America/Sao_Paulo")
        now = datetime.now(tz)
        run_at = now.replace(hour=14, minute=30, second=0, microsecond=0)
        if run_at <= now:
            run_at = run_at + timedelta(days=1)
        scheduler.add_job(
            func=test_cristiano_wrapper,
            trigger=DateTrigger(run_date=run_at),
            id='inventario_test_cristiano_14h',
            name='Teste inventario Cristiano 14h',
            replace_existing=True
        )
        logger.info("Teste inventario Cristiano agendado para %s", run_at.isoformat())

    # Disparo imediato para testes
    if os.getenv("INVENTARIO_TEST_CRISTIANO_NOW") == "1":
        tz = ZoneInfo("America/Sao_Paulo")
        now = datetime.now(tz)
        run_at = now + timedelta(seconds=5)
        scheduler.add_job(
            func=test_cristiano_wrapper,
            trigger=DateTrigger(run_date=run_at),
            id='inventario_test_cristiano_now',
            name='Teste inventario Cristiano AGORA',
            replace_existing=True
        )
        logger.info("🔥 Teste inventario Cristiano agendado para AGORA (5 segundos): %s", run_at.isoformat())

    # Iniciar o scheduler
    scheduler.start()
    logger.info("✓ Scheduler iniciado - Job diário configurado para 17h30 (America/Sao_Paulo)")

    # Desligar scheduler quando app terminar
    atexit.register(lambda: shutdown_scheduler())


def shutdown_scheduler():
    """Desliga o scheduler de forma segura"""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler desligado")
