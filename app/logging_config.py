import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logging():
    """Configure logging with rotating file handler."""
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    log_dir = os.path.join(base_dir, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'app.log')

    handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding='utf-8')
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    handler.setFormatter(formatter)

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    # Avoid adding multiple handlers if called multiple times
    if not any(isinstance(h, RotatingFileHandler) and getattr(h, 'baseFilename', '') == log_file for h in logger.handlers):
        logger.addHandler(handler)
