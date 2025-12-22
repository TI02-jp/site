"""Módulo de criptografia para senhas de terceiros.

Este módulo fornece criptografia simétrica (AES-256 via Fernet) para proteger
senhas de consultorias e outros sistemas de terceiros armazenadas no banco de dados.

IMPORTANTE: Senhas permanecem visíveis na interface para usuários autorizados,
mas são criptografadas no banco de dados para proteção contra dumps SQL e backups expostos.
"""

from cryptography.fernet import Fernet
import os
import logging

logger = logging.getLogger(__name__)

# Cache global do cipher para evitar recriação
_cipher = None


def _get_cipher() -> Fernet:
    """Obtém ou cria o cipher Fernet com a chave do ambiente.

    Returns:
        Fernet: Instância do cipher configurado

    Raises:
        ValueError: Se ENCRYPTION_KEY não estiver configurada no ambiente
    """
    global _cipher
    if _cipher is None:
        key = os.getenv('ENCRYPTION_KEY')
        if not key:
            raise ValueError(
                "ENCRYPTION_KEY não configurada no ambiente. "
                "Execute: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' "
                "e adicione ao arquivo .env como: ENCRYPTION_KEY=<chave_gerada>"
            )
        # Converte string para bytes se necessário
        key_bytes = key.encode() if isinstance(key, str) else key
        _cipher = Fernet(key_bytes)
        logger.info("Cipher de criptografia inicializado com sucesso")
    return _cipher


def encrypt_field(plaintext: str | None) -> str | None:
    """Criptografa um campo de texto.

    Args:
        plaintext: Texto em claro para criptografar

    Returns:
        str | None: Texto criptografado em base64, ou None se entrada for vazia

    Examples:
        >>> encrypt_field("minha_senha_secreta")
        'gAAAAABh...'
        >>> encrypt_field(None)
        None
        >>> encrypt_field("")
        None
    """
    if not plaintext:
        return None

    try:
        cipher = _get_cipher()
        encrypted_bytes = cipher.encrypt(plaintext.encode('utf-8'))
        return encrypted_bytes.decode('utf-8')
    except Exception as e:
        logger.error(f"Erro ao criptografar campo: {e}")
        raise


def decrypt_field(encrypted: str | None) -> str | None:
    """Descriptografa um campo de texto.

    Args:
        encrypted: Texto criptografado em base64

    Returns:
        str | None: Texto em claro, ou None se entrada for vazia

    Examples:
        >>> encrypted = encrypt_field("minha_senha_secreta")
        >>> decrypt_field(encrypted)
        'minha_senha_secreta'
        >>> decrypt_field(None)
        None
        >>> decrypt_field("")
        None
    """
    if not encrypted:
        return None

    try:
        cipher = _get_cipher()
        decrypted_bytes = cipher.decrypt(encrypted.encode('utf-8'))
        return decrypted_bytes.decode('utf-8')
    except Exception as e:
        logger.error(f"Erro ao descriptografar campo: {e}")
        # Em caso de erro, pode retornar None ou relançar exceção
        # dependendo da estratégia de tratamento de erros
        raise


def generate_key() -> str:
    """Gera uma nova chave de criptografia.

    Esta função é útil para setup inicial. A chave gerada deve ser:
    1. Salva no arquivo .env como ENCRYPTION_KEY=<chave>
    2. NUNCA commitada no git
    3. Mantida em segredo e backup seguro

    Returns:
        str: Nova chave Fernet em formato string (base64)

    Example:
        >>> key = generate_key()
        >>> print(f"Adicione ao .env: ENCRYPTION_KEY={key}")
    """
    return Fernet.generate_key().decode('utf-8')


if __name__ == "__main__":
    # Script para gerar nova chave de criptografia
    print("=" * 70)
    print("GERADOR DE CHAVE DE CRIPTOGRAFIA")
    print("=" * 70)
    print()
    print("Nova chave gerada:")
    print()
    new_key = generate_key()
    print(f"ENCRYPTION_KEY={new_key}")
    print()
    print("INSTRUÇÕES:")
    print("1. Copie a linha acima")
    print("2. Cole no arquivo .env")
    print("3. NUNCA commite esta chave no git")
    print("4. Faça backup seguro da chave")
    print()
    print("AVISO: Se você perder esta chave, NÃO SERÁ POSSÍVEL descriptografar")
    print("       as senhas existentes no banco de dados!")
    print("=" * 70)
