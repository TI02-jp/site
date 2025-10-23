"""Script para limpar e rotacionar logs do portal.

Este script deve ser executado com o Waitress PARADO para evitar conflitos de arquivo.
Mantém as últimas N linhas de cada log e arquiva o resto.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

# Configuração
LOGS_DIR = Path(__file__).parent.parent.parent / "logs"
KEEP_LINES = 1000  # Manter últimas 1000 linhas
ARCHIVE_DIR = LOGS_DIR / "archive"

def setup_logging():
    """Configura logging para este script."""
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger(__name__)

logger = setup_logging()

def rotate_log(log_file: Path, keep_lines: int = KEEP_LINES):
    """Rotaciona um arquivo de log, mantendo apenas as últimas N linhas.

    Args:
        log_file: Caminho para o arquivo de log
        keep_lines: Número de linhas a manter no log ativo
    """
    if not log_file.exists():
        logger.warning(f"Arquivo não encontrado: {log_file}")
        return False

    # Obter tamanho atual
    size_mb = log_file.stat().st_size / (1024 * 1024)
    logger.info(f"Processando {log_file.name} ({size_mb:.2f} MB)...")

    # Se arquivo é pequeno, não precisa rotacionar
    if size_mb < 5:
        logger.info(f"  Arquivo pequeno ({size_mb:.2f} MB), pulando rotação.")
        return True

    # Criar diretório de arquivo se não existir
    ARCHIVE_DIR.mkdir(exist_ok=True)

    # Ler últimas N linhas
    try:
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        total_lines = len(lines)
        logger.info(f"  Total de linhas: {total_lines:,}")

        if total_lines <= keep_lines:
            logger.info(f"  Arquivo já tem {total_lines} linhas, não precisa rotacionar.")
            return True

        # Arquivar linhas antigas
        archive_name = f"{log_file.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        archive_path = ARCHIVE_DIR / archive_name

        logger.info(f"  Arquivando {total_lines - keep_lines:,} linhas antigas em {archive_name}...")
        with open(archive_path, 'w', encoding='utf-8') as f:
            f.writelines(lines[:-keep_lines])

        # Manter apenas últimas N linhas no arquivo ativo
        logger.info(f"  Mantendo últimas {keep_lines:,} linhas no arquivo ativo...")
        with open(log_file, 'w', encoding='utf-8') as f:
            f.writelines(lines[-keep_lines:])

        # Verificar novo tamanho
        new_size_mb = log_file.stat().st_size / (1024 * 1024)
        saved_mb = size_mb - new_size_mb
        logger.info(f"  ✓ Concluído! Tamanho reduzido de {size_mb:.2f} MB → {new_size_mb:.2f} MB (economizou {saved_mb:.2f} MB)")

        return True

    except Exception as e:
        logger.error(f"  ✗ Erro ao processar {log_file.name}: {e}")
        return False

def cleanup_old_archives(days_to_keep: int = 30):
    """Remove arquivos de arquivo mais antigos que N dias.

    Args:
        days_to_keep: Manter arquivos dos últimos N dias
    """
    if not ARCHIVE_DIR.exists():
        return

    logger.info(f"\nLimpando arquivos com mais de {days_to_keep} dias...")

    cutoff_time = datetime.now().timestamp() - (days_to_keep * 24 * 60 * 60)
    removed_count = 0
    removed_size = 0

    for archive_file in ARCHIVE_DIR.glob("*.log"):
        if archive_file.stat().st_mtime < cutoff_time:
            size_mb = archive_file.stat().st_size / (1024 * 1024)
            logger.info(f"  Removendo {archive_file.name} ({size_mb:.2f} MB)...")
            archive_file.unlink()
            removed_count += 1
            removed_size += size_mb

    if removed_count > 0:
        logger.info(f"  ✓ Removidos {removed_count} arquivos ({removed_size:.2f} MB total)")
    else:
        logger.info("  Nenhum arquivo antigo para remover.")

def main():
    """Executa rotação de todos os logs."""
    logger.info("=" * 60)
    logger.info("Iniciando limpeza de logs do Portal JP")
    logger.info("=" * 60)
    logger.info(f"Diretório de logs: {LOGS_DIR}")
    logger.info(f"Mantendo últimas {KEEP_LINES:,} linhas por arquivo\n")

    # Verificar se diretório existe
    if not LOGS_DIR.exists():
        logger.error(f"Diretório de logs não encontrado: {LOGS_DIR}")
        return 1

    # Rotacionar cada log
    logs_to_rotate = [
        LOGS_DIR / "app.log",
        LOGS_DIR / "error.log",
    ]

    success_count = 0
    for log_file in logs_to_rotate:
        if rotate_log(log_file):
            success_count += 1

    # Limpar arquivos antigos
    cleanup_old_archives(days_to_keep=30)

    # Sumário
    logger.info("\n" + "=" * 60)
    logger.info(f"Limpeza concluída: {success_count}/{len(logs_to_rotate)} logs processados")
    logger.info("=" * 60)

    return 0 if success_count == len(logs_to_rotate) else 1

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.warning("\nOperação cancelada pelo usuário.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"\nErro fatal: {e}", exc_info=True)
        sys.exit(1)
