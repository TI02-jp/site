import logging
import os
from logging.handlers import RotatingFileHandler

def setup_logging():
    """Configure root logger to write to logs/app.log."""
    log_dir = os.path.join(os.path.dirname(__file__), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'app.log')

    formatter = logging.Formatter('%(asctime)s %(levelname)s [%(module)s] %(message)s')

    file_handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=5)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    logger = logging.getLogger()
    if not any(isinstance(h, RotatingFileHandler) and h.baseFilename == file_handler.baseFilename for h in logger.handlers):
        logger.setLevel(logging.INFO)
        logger.addHandler(file_handler)
        # also log werkzeug requests
        logging.getLogger('werkzeug').addHandler(file_handler)
