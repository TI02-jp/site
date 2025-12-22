"""
Validadores de upload e entrada de dados.

Este modulo centraliza todas as funcoes de validacao utilizadas na aplicacao,
especialmente para uploads de arquivos, garantindo seguranca e consistencia.

Funcoes de Validacao de Upload:
    - allowed_file: Verifica extensao de imagem
    - allowed_file_with_pdf: Verifica extensao de imagem ou PDF
    - is_safe_image_upload: Validacao completa de imagem (MIME + assinatura)
    - is_safe_pdf_upload: Validacao completa de PDF (MIME + assinatura)

Autor: Refatoracao automatizada
Data: 2024
"""

import filetype

from app.controllers.routes._base import (
    IMAGE_EXTENSIONS,
    ALLOWED_EXTENSIONS_WITH_PDF,
    IMAGE_MIME_TYPES,
    IMAGE_SIGNATURE_MAP,
    PDF_MIME_TYPES,
    VIDEO_EXTENSIONS,
    VIDEO_MIME_TYPES,
    VIDEO_SIGNATURES,
    peek_stream,
)


# =============================================================================
# VALIDACAO DE EXTENSAO (BASICA)
# =============================================================================

def allowed_file(filename: str) -> bool:
    """
    Verifica se arquivo tem extensao de imagem permitida.

    Validacao basica apenas por extensao. Para validacao completa
    incluindo MIME type e assinatura, use is_safe_image_upload().

    Args:
        filename: Nome do arquivo a validar.

    Returns:
        bool: True se extensao e permitida.
    """
    return "." in filename and filename.rsplit(".", 1)[1].lower() in IMAGE_EXTENSIONS


def allowed_file_with_pdf(filename: str) -> bool:
    """
    Verifica se arquivo tem extensao de imagem ou PDF permitida.

    Validacao basica apenas por extensao. Para validacao completa,
    use is_safe_image_upload() ou is_safe_pdf_upload().

    Args:
        filename: Nome do arquivo a validar.

    Returns:
        bool: True se extensao e permitida.
    """
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS_WITH_PDF


# =============================================================================
# VALIDACAO COMPLETA (MIME + ASSINATURA)
# =============================================================================

def is_safe_image_upload(file) -> bool:
    """
    Valida upload de imagem verificando MIME type e assinatura do arquivo.

    Realiza tres verificacoes:
    1. Extensao do arquivo esta na lista permitida
    2. MIME type corresponde a uma imagem valida
    3. Assinatura do arquivo (magic bytes) corresponde ao tipo declarado

    Args:
        file: Objeto FileStorage do werkzeug.

    Returns:
        bool: True se arquivo e uma imagem valida e segura.

    Exemplo:
        if is_safe_image_upload(request.files['foto']):
            file.save(path)
    """
    filename = file.filename or ""
    extension = filename.rsplit(".", 1)[1].lower() if "." in filename else ""

    # Verifica extensao
    if extension not in IMAGE_EXTENSIONS:
        return False

    # Verifica MIME type
    if file.mimetype not in IMAGE_MIME_TYPES:
        return False

    # Verifica assinatura do arquivo (magic bytes)
    header = peek_stream(file)
    guess = filetype.guess(header)
    detected = guess.extension if guess else None
    if not detected:
        return False

    # Verifica se assinatura corresponde a extensao declarada
    allowed_extensions = IMAGE_SIGNATURE_MAP.get(detected, set())
    return extension in allowed_extensions


def is_safe_pdf_upload(file) -> bool:
    """
    Valida upload de PDF verificando MIME type e assinatura.

    Realiza duas verificacoes:
    1. MIME type corresponde a application/pdf
    2. Arquivo comeca com assinatura PDF (%PDF)

    Args:
        file: Objeto FileStorage do werkzeug.

    Returns:
        bool: True se arquivo e um PDF valido.

    Exemplo:
        if is_safe_pdf_upload(request.files['documento']):
            file.save(path)
    """
    # Verifica MIME type
    if file.mimetype not in PDF_MIME_TYPES:
        return False

    # Verifica assinatura do arquivo
    header = peek_stream(file, size=5)
    return header.startswith(b"%PDF")


def is_safe_file_upload(file) -> bool:
    """
    Valida upload verificando se e imagem ou PDF valido.

    Combina is_safe_image_upload() e is_safe_pdf_upload()
    para validacao de uploads que aceitam ambos os tipos.

    Args:
        file: Objeto FileStorage do werkzeug.

    Returns:
        bool: True se arquivo e imagem ou PDF valido.
    """
    return is_safe_image_upload(file) or is_safe_pdf_upload(file)


# =============================================================================
# VALIDACAO DE TAMANHO
# =============================================================================

def validate_file_size(file, max_size_mb: float = 5.0) -> tuple[bool, str | None]:
    """
    Valida tamanho do arquivo contra limite maximo.

    Args:
        file: Objeto FileStorage do werkzeug.
        max_size_mb: Tamanho maximo permitido em megabytes.

    Returns:
        tuple[bool, str | None]: (valido, mensagem_erro)

    Exemplo:
        valid, error = validate_file_size(file, max_size_mb=10)
        if not valid:
            flash(error, 'error')
    """
    from app.controllers.routes._base import get_file_size_bytes

    size_bytes = get_file_size_bytes(file)
    max_bytes = int(max_size_mb * 1024 * 1024)

    if size_bytes > max_bytes:
        return False, f"Arquivo muito grande. Tamanho maximo permitido: {max_size_mb}MB"

    return True, None


def validate_image_upload(file, max_size_mb: float = 5.0) -> tuple[bool, str | None]:
    """
    Validacao completa de upload de imagem.

    Combina validacao de tipo (MIME + assinatura) e tamanho.

    Args:
        file: Objeto FileStorage do werkzeug.
        max_size_mb: Tamanho maximo permitido em megabytes.

    Returns:
        tuple[bool, str | None]: (valido, mensagem_erro)
    """
    if not file or not file.filename:
        return False, "Nenhum arquivo selecionado."

    if not is_safe_image_upload(file):
        return False, "Tipo de arquivo invalido. Envie apenas imagens (PNG, JPG, JPEG, GIF)."

    valid, error = validate_file_size(file, max_size_mb)
    if not valid:
        return False, error

    return True, None


def validate_pdf_upload(file, max_size_mb: float = 10.0) -> tuple[bool, str | None]:
    """
    Validacao completa de upload de PDF.

    Combina validacao de tipo (MIME + assinatura) e tamanho.

    Args:
        file: Objeto FileStorage do werkzeug.
        max_size_mb: Tamanho maximo permitido em megabytes.

    Returns:
        tuple[bool, str | None]: (valido, mensagem_erro)
    """
    if not file or not file.filename:
        return False, "Nenhum arquivo selecionado."

    if not is_safe_pdf_upload(file):
        return False, "Tipo de arquivo invalido. Envie apenas arquivos PDF."

    valid, error = validate_file_size(file, max_size_mb)
    if not valid:
        return False, error

    return True, None


# =============================================================================
# VALIDACAO DE VIDEO
# =============================================================================

def is_safe_video_upload(file) -> bool:
    """
    Valida upload de video verificando MIME type e assinatura do arquivo.

    Realiza tres verificacoes:
    1. Extensao do arquivo esta na lista permitida
    2. MIME type corresponde a um video valido
    3. Assinatura do arquivo (magic bytes) corresponde ao tipo declarado

    Args:
        file: Objeto FileStorage do werkzeug.

    Returns:
        bool: True se arquivo e um video valido e seguro.

    Exemplo:
        if is_safe_video_upload(request.files['video']):
            file.save(path)
    """
    filename = file.filename or ""
    extension = filename.rsplit(".", 1)[1].lower() if "." in filename else ""

    # Verifica extensao
    if extension not in VIDEO_EXTENSIONS:
        return False

    # Verifica MIME type
    if file.mimetype not in VIDEO_MIME_TYPES:
        return False

    # Verifica assinatura do arquivo (magic bytes)
    header = peek_stream(file, size=24)  # Precisa de mais bytes para video

    # Validacao de MP4: verifica estrutura basica do container MP4
    # MP4 comeca com: 00 00 00 XX ftyp (onde XX e o tamanho)
    # Existem muitas variantes de ftyp (mp41, mp42, isom, avc1, iso2, etc.)
    if extension == "mp4":
        # Verifica se tem a estrutura basica de MP4
        if len(header) >= 8:
            # Verifica os primeiros 3 bytes (devem ser 00 00 00)
            if header[0:3] == b"\x00\x00\x00":
                # Verifica se contem 'ftyp' na posicao 4-8
                if b"ftyp" in header[4:12]:
                    return True

    # Validacao de WebM: verifica assinatura especifica
    if extension == "webm":
        if header.startswith(b"\x1A\x45\xDF\xA3"):
            return True

    return False


def validate_video_upload(file, max_size_mb: float = 1024.0) -> tuple[bool, str | None]:
    """
    Validacao completa de upload de video.

    Combina validacao de tipo (MIME + assinatura) e tamanho.

    Args:
        file: Objeto FileStorage do werkzeug.
        max_size_mb: Tamanho maximo permitido em megabytes (padrao: 1 GB).

    Returns:
        tuple[bool, str | None]: (valido, mensagem_erro)

    Exemplo:
        valid, error = validate_video_upload(request.files['video'], 500)
        if not valid:
            flash(error, 'danger')
    """
    if not file or not file.filename:
        return False, "Nenhum arquivo de video selecionado."

    if not is_safe_video_upload(file):
        return False, "Tipo de arquivo invalido. Envie apenas videos MP4 ou WebM."

    valid, error = validate_file_size(file, max_size_mb)
    if not valid:
        return False, error

    return True, None
