"""Application entry point for local development."""
from waitress import serve
from app import app

if __name__ == "__main__":
    # Configuração otimizada para produção
    serve(
        app,
        host='0.0.0.0',
        port=5000,
        threads=8,  # Usa múltiplas threads (1 por core CPU)
        channel_timeout=60,  # Timeout de 60s para requisições
        connection_limit=500,  # Máximo de 500 conexões simultâneas
        backlog=256,  # Tamanho da fila de conexões pendentes
        asyncore_use_poll=True  # Usa poll() ao invés de select() (melhor performance)
    )
