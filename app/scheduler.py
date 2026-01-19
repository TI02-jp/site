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

def _build_base_url(app) -> str:
    scheme = app.config.get("PREFERRED_URL_SCHEME", "http")
    server_name = app.config.get("SERVER_NAME")
    if server_name:
        return f"{scheme}://{server_name}"
    return os.getenv("APP_BASE_URL", "http://localhost")



def init_scheduler(app):
    """
    Inicializa o agendador de tarefas

    Args:
        app: Instância da aplicação Flask
    """
    global scheduler

    # Evitar reinicialização se já estiver rodando
    if scheduler.running:
        logger.warning("Scheduler já está rodando, pulando inicialização")
        return

    # Importar dentro da função para evitar imports circulares
    from app.controllers.routes.blueprints.empresas import send_daily_tadeu_notification
    from app.services.inventario_sync import sync_encerramento_fiscal

    def job_wrapper():
        """Wrapper que executa a fun????o dentro do contexto da aplica????o Flask"""
        with app.app_context():
            base_url = _build_base_url(app)
            with app.test_request_context(base_url=base_url):
                try:
                    send_daily_tadeu_notification()
                except Exception as e:
                    logger.error(f"Erro ao executar notifica????o di??ria para Tadeu: {e}", exc_info=True)

    def test_cristiano_wrapper():
        """Wrapper para envio de teste do inventario apenas para Cristiano."""
        with app.app_context():
            base_url = _build_base_url(app)
            with app.test_request_context(base_url=base_url):
                try:
                    send_daily_tadeu_notification(recipients=("Cristiano",), force=True)
                except Exception as e:
                    logger.error(f"Erro ao executar teste de inventario para Cristiano: {e}", exc_info=True)

    def sync_encerramento_wrapper():
        """Wrapper para sincronização automática de encerramento fiscal."""
        with app.app_context():
            try:
                result = sync_encerramento_fiscal()
                logger.info(
                    "Sync encerramento fiscal automático concluído",
                    extra=result.as_dict()
                )
            except Exception as e:
                logger.error(f"Erro no sync automático de encerramento fiscal: {e}", exc_info=True)

    # Agendar sincronização de encerramento fiscal às 6h (horário de Brasília)
    scheduler.add_job(
        func=sync_encerramento_wrapper,
        trigger=CronTrigger(hour=6, minute=0, timezone='America/Sao_Paulo'),
        id='sync_encerramento_fiscal',
        name='Sincronização automática encerramento fiscal',
        replace_existing=True
    )

    # Agendar notificação diária às 17h00 (horário de Brasília)
    scheduler.add_job(
        func=job_wrapper,
        trigger=CronTrigger(hour=17, minute=0, timezone='America/Sao_Paulo'),
        id='daily_tadeu_notification',
        name='Notificação diária para Tadeu e Cristiano - Inventário',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
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
    logger.info("🔄 Iniciando scheduler...")
    if not scheduler.running:
        scheduler.start()
        logger.info("✓ Scheduler INICIADO com sucesso")
    else:
        logger.warning("⚠️ Scheduler já estava rodando")

    # Listar jobs agendados
    jobs = scheduler.get_jobs()
    logger.info(f"📋 Jobs agendados: {len(jobs)}")
    for job in jobs:
        logger.info(f"  - {job.id}: {job.name} (próxima execução: {job.next_run_time})")

    # Desligar scheduler quando app terminar
    atexit.register(lambda: shutdown_scheduler())


def shutdown_scheduler():
    """Desliga o scheduler de forma segura"""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler desligado")
