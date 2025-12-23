"""
Modulo base com helpers e constantes compartilhados entre blueprints.

Este modulo centraliza funcoes utilitarias e helpers que sao
utilizados por multiplos blueprints da aplicacao, evitando duplicacao de codigo.

Conteudo:
    - Funcoes de formatacao (telefone, contatos, timestamps)
    - Helpers de cache (setores, consultorias, course tags)
    - Funcoes de encoding/decoding de IDs
    - Helpers para upload de arquivos

Nota: Constantes foram movidas para app/constants.py

Autor: Refatoracao automatizada
Data: 2024
"""

import os
import re
import time
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urlparse
from uuid import uuid4

from flask import abort, current_app, g, has_request_context, request
from itsdangerous import URLSafeSerializer, BadSignature
from mimetypes import guess_type
from werkzeug.utils import secure_filename

from app import db
from app.extensions.cache import cache, get_cache_timeout
from app.models.tables import (
    SAO_PAULO_TZ,
    Consultoria,
    CourseTag,
    DiretoriaEvent,
    Setor,
    Tag,
    TaskNotification,
)

# Re-export constants for backward compatibility
from app.constants import (
    ACESSOS_CATEGORIES,
    ALLOWED_EXTENSIONS_WITH_PDF,
    EVENT_AUDIENCE_LABELS,
    EVENT_CATEGORY_LABELS,
    EVENT_TYPE_LABELS,
    EXCLUDED_TASK_TAGS,
    EXCLUDED_TASK_TAGS_LOWER,
    GOOGLE_OAUTH_SCOPES,
    IMAGE_EXTENSIONS,
    IMAGE_MIME_TYPES,
    IMAGE_SIGNATURE_MAP,
    MANUAL_THUMBNAILS_SUBDIR,
    MANUAL_VIDEOS_SUBDIR,
    PDF_MIME_TYPES,
    PERSONAL_TAG_PREFIX,
    REPORT_DEFINITIONS,
    TASKS_UPLOAD_SUBDIR,
    VIDEO_EXTENSIONS,
    VIDEO_MAX_SIZE_MB,
    VIDEO_MIME_TYPES,
    VIDEO_SIGNATURES,
    CACHE_KEY_NOTIFICATION_COUNT_PREFIX as _NOTIFICATION_COUNT_KEY_PREFIX,
    CACHE_KEY_NOTIFICATION_VERSION as _NOTIFICATION_VERSION_KEY,
    CACHE_KEY_STATS_PREFIX as _STATS_CACHE_KEY_PREFIX,
)


# =============================================================================
# FUNCOES DE DATA/HORA
# =============================================================================

def utc3_now() -> datetime:
    """
    Retorna datetime atual no fuso horario de Sao Paulo.

    Returns:
        datetime: Data/hora atual sem timezone info (naive) para compatibilidade MySQL.
    """
    return datetime.now(SAO_PAULO_TZ).replace(tzinfo=None)


def format_event_timestamp(raw_dt: datetime | None) -> str:
    """
    Formata timestamp para exibicao em views da Diretoria JP.

    Args:
        raw_dt: Datetime a ser formatado (pode ser None).

    Returns:
        str: Data formatada como "DD/MM/YYYY HH:MM" ou "—" se None.
    """
    if raw_dt is None:
        return "—"

    timestamp = raw_dt
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    localized = timestamp.astimezone(SAO_PAULO_TZ)
    return localized.strftime("%d/%m/%Y %H:%M")


# =============================================================================
# FUNCOES DE FORMATACAO
# =============================================================================

def format_phone(digits: str) -> str:
    """
    Formata string de digitos em numero de telefone brasileiro.

    Args:
        digits: String contendo apenas digitos do telefone.

    Returns:
        str: Telefone formatado como "(XX) XXXXX-XXXX" ou "(XX) XXXX-XXXX".
    """
    if len(digits) >= 11:
        return f"({digits[:2]}) {digits[2:7]}-{digits[7:11]}"
    if len(digits) >= 10:
        return f"({digits[:2]}) {digits[2:6]}-{digits[6:10]}"
    return digits


def normalize_contatos(contatos: list | None) -> list:
    """
    Normaliza entradas de contatos em estrutura consistente.

    Converte diferentes formatos de contatos legados para o formato
    padrao utilizado pela aplicacao.

    Args:
        contatos: Lista de contatos em formato variado.

    Returns:
        list: Lista de contatos normalizados com estrutura {nome, meios: [{tipo, endereco}]}.
    """
    if not contatos:
        return []

    # Verifica se ja esta no formato novo (com 'meios')
    if all(isinstance(c, dict) and "meios" in c for c in contatos):
        for c in contatos:
            meios = c.get("meios") or []
            for m in meios:
                # Converte 'valor' para 'endereco' se necessario
                if "valor" in m and "endereco" not in m:
                    m["endereco"] = m.pop("valor")
                # Formata telefones
                if m.get("tipo") in ("telefone", "whatsapp"):
                    digits = re.sub(r"\D", "", m.get("endereco", ""))
                    m["endereco"] = format_phone(digits)
        return contatos

    # Converte formato legado para novo formato
    grouped: dict[str, dict] = {}
    for c in contatos:
        if not isinstance(c, dict):
            continue
        nome = (c.get("nome") or "").strip()
        if not nome:
            continue
        if nome not in grouped:
            grouped[nome] = {"nome": nome, "meios": []}

        # Adiciona meios de contato
        for key in ("email", "telefone", "whatsapp"):
            valor = c.get(key)
            if valor:
                valor_str = str(valor).strip()
                if key in ("telefone", "whatsapp"):
                    valor_str = format_phone(re.sub(r"\D", "", valor_str))
                grouped[nome]["meios"].append({"tipo": key, "endereco": valor_str})

    return list(grouped.values())


# =============================================================================
# FUNCOES DE CACHE - CATALOGOS
# =============================================================================

@cache.memoize(timeout=get_cache_timeout("SETORES_CACHE_TIMEOUT", 300))
def get_setores_catalog() -> list[Setor]:
    """
    Retorna catalogo de setores com cache.

    Returns:
        list[Setor]: Lista de setores ordenados por nome.
    """
    return Setor.query.order_by(Setor.nome).all()


def invalidate_setores_cache() -> None:
    """Limpa cache do catalogo de setores."""
    cache.delete_memoized(get_setores_catalog)


@cache.memoize(timeout=get_cache_timeout("COURSETAGS_CACHE_TIMEOUT", 600))
def get_course_tags_catalog() -> list[CourseTag]:
    """
    Retorna catalogo de tags de cursos com cache.

    Returns:
        list[CourseTag]: Lista de tags ordenadas por nome.
    """
    return CourseTag.query.order_by(CourseTag.name.asc()).all()


def invalidate_course_tags_cache() -> None:
    """Limpa cache do catalogo de tags de cursos."""
    cache.delete_memoized(get_course_tags_catalog)


@cache.memoize(timeout=get_cache_timeout("CONSULTORIAS_CACHE_TIMEOUT", 300))
def get_consultorias_catalog() -> list[Consultoria]:
    """
    Retorna catalogo de consultorias com cache.

    Returns:
        list[Consultoria]: Lista de consultorias ordenadas por nome.
    """
    return Consultoria.query.order_by(Consultoria.nome).all()


def invalidate_consultorias_cache() -> None:
    """Limpa cache do catalogo de consultorias."""
    cache.delete_memoized(get_consultorias_catalog)


# =============================================================================
# FUNCOES DE CACHE - NOTIFICACOES
# =============================================================================

def get_stats_cache_timeout() -> int:
    """Retorna timeout do cache de estatisticas do portal."""
    return get_cache_timeout("PORTAL_STATS_CACHE_TIMEOUT", 300)


def get_notification_cache_timeout() -> int:
    """Retorna timeout do cache de contagem de notificacoes."""
    return get_cache_timeout("NOTIFICATION_COUNT_CACHE_TIMEOUT", 60)


def get_notification_version() -> int:
    """
    Retorna versao atual do cache de notificacoes.

    Usado para invalidacao seletiva de cache por usuario.
    """
    version = cache.get(_NOTIFICATION_VERSION_KEY)
    if version is None:
        version = int(time.time())
        set_notification_version(int(version))
    return int(version)


def set_notification_version(version: int) -> None:
    """Define nova versao do cache de notificacoes."""
    ttl = max(get_notification_cache_timeout(), 300)
    cache.set(_NOTIFICATION_VERSION_KEY, int(version), timeout=ttl)


def notification_cache_key(user_id: int) -> str:
    """Gera chave de cache para contagem de notificacoes de um usuario."""
    return f"{_NOTIFICATION_COUNT_KEY_PREFIX}{get_notification_version()}:{user_id}"


# =============================================================================
# FUNCOES DE ENCODING/DECODING DE IDs
# =============================================================================

_id_serializers: dict[str, URLSafeSerializer] = {}


def _get_id_serializer(namespace: str = "default") -> URLSafeSerializer:
    """
    Retorna serializer para encoding de IDs.

    Args:
        namespace: Namespace para isolar diferentes tipos de IDs.

    Returns:
        URLSafeSerializer: Serializer configurado.
    """
    if namespace not in _id_serializers:
        secret = current_app.config.get("SECRET_KEY", "fallback-secret")
        _id_serializers[namespace] = URLSafeSerializer(secret, salt=f"id-{namespace}")
    return _id_serializers[namespace]


def encode_id(value: int, namespace: str = "default") -> str:
    """
    Cria token assinado para um ID numerico.

    Args:
        value: ID numerico a ser codificado.
        namespace: Namespace para o serializer.

    Returns:
        str: Token assinado representando o ID.
    """
    return _get_id_serializer(namespace).dumps(int(value))


def decode_id(token: str, namespace: str = "default", *, allow_plain_int: bool = True) -> int:
    """
    Decodifica token assinado de volta para ID numerico.

    Args:
        token: Token a ser decodificado.
        namespace: Namespace do serializer.
        allow_plain_int: Se True, aceita IDs numericos simples.

    Returns:
        int: ID numerico decodificado.

    Raises:
        404: Se token invalido ou mal formatado.
    """
    cleaned = (token or "").strip()
    if not cleaned:
        abort(404)

    if allow_plain_int and cleaned.isdigit():
        return int(cleaned)

    try:
        value = _get_id_serializer(namespace).loads(cleaned)
    except BadSignature:
        abort(404)

    if not isinstance(value, int):
        abort(404)

    return value


# =============================================================================
# FUNCOES DE UPLOAD - HELPERS
# =============================================================================

def peek_stream(filestorage, size: int = 512) -> bytes:
    """
    Retorna os primeiros bytes de um upload sem consumir o stream.

    Args:
        filestorage: Objeto FileStorage do werkzeug.
        size: Quantidade de bytes a ler.

    Returns:
        bytes: Primeiros bytes do arquivo.
    """
    stream = filestorage.stream
    position = stream.tell()
    chunk = stream.read(size)
    stream.seek(position)
    return chunk


def get_file_size_bytes(filestorage) -> int:
    """
    Retorna tamanho do arquivo sem consumir o stream.

    Args:
        filestorage: Objeto FileStorage do werkzeug.

    Returns:
        int: Tamanho do arquivo em bytes.
    """
    stream = filestorage.stream
    position = stream.tell()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(position)
    return size


def save_task_file(uploaded_file) -> dict[str, str | None]:
    """
    Persiste arquivo de upload para tarefa e retorna metadados.

    Args:
        uploaded_file: Arquivo enviado via formulario.

    Returns:
        dict: Metadados do arquivo {path, name, mime_type}.
    """
    original_name = secure_filename(uploaded_file.filename or "")
    extension = os.path.splitext(original_name)[1].lower()
    unique_name = f"{uuid4().hex}{extension}"

    upload_directory = os.path.join(
        current_app.root_path, "static", TASKS_UPLOAD_SUBDIR
    )
    os.makedirs(upload_directory, exist_ok=True)

    stored_path = os.path.join(upload_directory, unique_name)
    uploaded_file.save(stored_path)

    relative_path = os.path.join(TASKS_UPLOAD_SUBDIR, unique_name).replace("\\", "/")
    mime_type = uploaded_file.mimetype or guess_type(original_name)[0]

    return {
        "path": relative_path,
        "name": original_name or None,
        "mime_type": mime_type,
    }


# =============================================================================
# FUNCOES DE FOTOS - DIRETORIA
# =============================================================================

def normalize_photo_entry(value: str) -> str | None:
    """
    Retorna referencia de foto sanitizada ou None se invalida.

    Args:
        value: URL ou caminho da foto.

    Returns:
        str | None: URL/caminho normalizado ou None.
    """
    if not isinstance(value, str):
        return None

    trimmed = value.strip()
    if not trimmed:
        return None

    parsed = urlparse(trimmed)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc

    if scheme == "https" and netloc:
        return parsed.geturl()

    if scheme == "http" and netloc:
        static_path = parsed.path or ""
        if not static_path.startswith("/static/"):
            return None

        # Aceita scheme inseguro apenas para host da aplicacao
        allowed_hosts: set[str] = set()
        if has_request_context():
            host = (request.host or "").lower()
            if host:
                allowed_hosts.add(host)
        server_name = (current_app.config.get("SERVER_NAME") or "").lower()
        if server_name:
            allowed_hosts.add(server_name)

        normalized_netloc = netloc.lower()
        if not allowed_hosts:
            return static_path if static_path.startswith("/static/uploads/") else None

        netloc_base = normalized_netloc.split(":", 1)[0]
        for allowed in allowed_hosts:
            if not allowed:
                continue
            allowed_base = allowed.split(":", 1)[0]
            if normalized_netloc == allowed or netloc_base == allowed_base:
                return static_path

        return None

    if scheme and scheme not in {"http", "https"}:
        return None

    if not parsed.scheme and not parsed.netloc:
        if trimmed.startswith("/"):
            return "/" + trimmed.lstrip("/")
        if trimmed.lower().startswith("static/"):
            return "/" + trimmed.lstrip("/")

    return None


def resolve_local_photo_path(normalized_photo_url: str) -> str | None:
    """
    Retorna caminho no filesystem para upload em /static.

    Args:
        normalized_photo_url: URL normalizada da foto.

    Returns:
        str | None: Caminho absoluto no filesystem ou None.
    """
    parsed = urlparse(normalized_photo_url)
    path = parsed.path if parsed.scheme else normalized_photo_url
    if not path:
        return None

    relative_path = path.lstrip("/")
    if not relative_path.startswith("static/uploads/"):
        return None

    safe_relative = os.path.normpath(relative_path)
    if not safe_relative.startswith("static/uploads/"):
        return None

    return os.path.join(current_app.root_path, safe_relative)


def cleanup_diretoria_photo_uploads(
    photo_urls: Iterable[str], *, exclude_event_id: int | None = None
) -> None:
    """
    Deleta arquivos de fotos nao utilizados da pasta uploads.

    Args:
        photo_urls: URLs das fotos a verificar.
        exclude_event_id: ID do evento a excluir da verificacao.
    """
    normalized_to_path: dict[str, str] = {}
    for photo_url in photo_urls:
        normalized = normalize_photo_entry(photo_url)
        if not normalized:
            continue

        file_path = resolve_local_photo_path(normalized)
        if not file_path:
            continue

        normalized_to_path[normalized] = file_path

    if not normalized_to_path:
        return

    query = DiretoriaEvent.query
    if exclude_event_id is not None:
        query = query.filter(DiretoriaEvent.id != exclude_event_id)

    still_in_use: set[str] = set()
    for _, other_photos in query.with_entities(
        DiretoriaEvent.id, DiretoriaEvent.photos
    ):
        if not isinstance(other_photos, list):
            continue

        for other_photo in other_photos:
            normalized_other = normalize_photo_entry(other_photo)
            if normalized_other in normalized_to_path:
                still_in_use.add(normalized_other)

        if len(still_in_use) == len(normalized_to_path):
            break

    for normalized, file_path in normalized_to_path.items():
        if normalized in still_in_use:
            continue

        if not os.path.exists(file_path):
            continue

        try:
            os.remove(file_path)
        except OSError:
            current_app.logger.warning(
                "Nao foi possivel remover o arquivo de foto nao utilizado: %s",
                file_path,
                exc_info=True,
            )


# =============================================================================
# FUNCOES DE TAG - HELPERS
# =============================================================================

def get_ti_tag() -> Tag | None:
    """
    Retorna a tag TI se existir (cached por request).

    Returns:
        Tag | None: Tag TI ou None se nao existir.
    """
    import sqlalchemy as sa

    if not has_request_context():
        return Tag.query.filter(sa.func.lower(Tag.nome) == "ti").first()

    if not hasattr(g, "_ti_tag"):
        g._ti_tag = Tag.query.filter(sa.func.lower(Tag.nome) == "ti").first()

    return g._ti_tag
