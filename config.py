import os
import logging

logger = logging.getLogger(__name__)

class Config:
    """Application configuration."""
    ACESSORIAS_API_BASE = os.getenv("ACESSORIAS_API_BASE", "https://api.acessorias.com/documentation")
    ACESSORIAS_API_TOKEN = os.getenv("ACESSORIAS_API_TOKEN")
    ACESSORIAS_ENABLED = bool(ACESSORIAS_API_TOKEN)

    @classmethod
    def validate(cls) -> None:
        if not cls.ACESSORIAS_API_TOKEN:
            logger.warning("ACESSORIAS_API_TOKEN not set - Acessorias integration disabled")
