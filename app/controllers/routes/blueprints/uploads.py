"""
Blueprint para upload de arquivos.

Este modulo contem rotas para upload de imagens e arquivos
utilizados pelo editor WYSIWYG e outras funcionalidades.

Rotas:
    - POST /upload_image: Upload de imagens
    - POST /upload_file: Upload de arquivos (imagens + PDFs)

Dependencias:
    - validators: allowed_file, is_safe_image_upload, is_safe_pdf_upload
    - utils.audit: log_user_action

Autor: Refatoracao automatizada
Data: 2024
"""

import os
from io import BytesIO
from ipaddress import ip_address
from socket import gaierror, getaddrinfo
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, request, url_for
from flask_login import login_required
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename
from PIL import Image, ImageOps, UnidentifiedImageError

from app.controllers.routes._base import get_file_size_bytes
from app.controllers.routes._validators import (
    allowed_file,
    allowed_file_with_pdf,
    is_safe_image_upload,
    is_safe_pdf_upload,
)


# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

uploads_bp = Blueprint('uploads', __name__)


# =============================================================================
# ROTAS
# =============================================================================



def _is_public_image_host(hostname: str | None) -> bool:
    """Return True only for hostnames resolved to public IPs."""
    if not hostname:
        return False
    lowered = hostname.lower().strip()
    if lowered in {"localhost", "127.0.0.1", "::1"}:
        return False

    try:
        addr_info = getaddrinfo(lowered, None)
    except gaierror:
        return False

    for info in addr_info:
        ip_raw = info[4][0]
        try:
            parsed_ip = ip_address(ip_raw)
        except ValueError:
            continue
        if (
            parsed_ip.is_private
            or parsed_ip.is_loopback
            or parsed_ip.is_link_local
            or parsed_ip.is_multicast
            or parsed_ip.is_reserved
        ):
            return False
    return True


def _build_remote_image_filename(parsed_url, content_type: str) -> str:
    """Build a safe filename using remote path and MIME type."""
    path_name = os.path.basename(parsed_url.path or "") or "clipboard-image"
    safe_name = secure_filename(path_name) or "clipboard-image"

    if "." not in safe_name:
        mime_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/bmp": ".bmp",
            "image/webp": ".webp",
        }
        safe_name = f"{safe_name}{mime_map.get((content_type or '').lower(), '.png')}"
    return safe_name


def _download_remote_image_bytes(image_url: str, max_bytes: int) -> tuple[bytes, str]:
    """Download remote image with hard byte cap."""
    req = Request(
        image_url,
        headers={
            "User-Agent": "JPPortalImageImporter/1.0",
            "Accept": "image/*",
        },
    )
    with urlopen(req, timeout=10) as response:
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if not content_type.startswith("image/"):
            raise ValueError("URL nao retornou imagem")

        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("Imagem excede tamanho maximo permitido")

    return data, content_type


def _build_preview_url(original_path: str, unique_name: str) -> str | None:
    """Generate a lightweight preview while keeping the original untouched."""
    preview_rel = f"uploads/previews/{os.path.splitext(unique_name)[0]}.webp"
    preview_abs = os.path.join(current_app.root_path, "static", preview_rel)

    max_side = int(current_app.config.get("WYSIWYG_PREVIEW_MAX_SIDE", 1600))
    quality = int(current_app.config.get("WYSIWYG_PREVIEW_WEBP_QUALITY", 86))

    try:
        os.makedirs(os.path.dirname(preview_abs), exist_ok=True)
        with Image.open(original_path) as image:
            image = ImageOps.exif_transpose(image)
            if getattr(image, "is_animated", False):
                image.seek(0)
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGB")

            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            save_options = {"format": "WEBP", "quality": quality, "method": 4}
            image.save(preview_abs, **save_options)

        return url_for("static", filename=preview_rel, _external=True)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        current_app.logger.warning("Falha ao gerar preview da imagem %s: %s", unique_name, exc)
        return None

@uploads_bp.route("/upload_image", methods=["POST"])
@login_required
def upload_image():
    """
    Processa upload de imagens do editor WYSIWYG.

    Validacoes realizadas:
        - Arquivo presente na requisicao
        - Nome de arquivo valido
        - Extensao permitida (png, jpg, jpeg, gif)
        - MIME type e assinatura corretos

    Request:
        files['image']: Arquivo de imagem

    Returns:
        200: {"image_url": "URL da imagem salva"}
        400: {"error": "Mensagem de erro"}
        500: {"error": "Erro ao processar upload"}
    """
    from app.utils.audit import log_user_action, ActionType, ResourceType

    # Validacao: arquivo presente
    if "image" not in request.files:
        return jsonify({"error": "Nenhuma imagem enviada"}), 400

    file = request.files["image"]

    # Validacao: nome de arquivo
    if not file.filename:
        return jsonify({"error": "Nome de arquivo vazio"}), 400

    # Validacao: extensao e conteudo
    if not allowed_file(file.filename) or not is_safe_image_upload(file):
        return jsonify({"error": "Imagem invalida ou nao permitida"}), 400

    filename = secure_filename(file.filename)
    file_size = get_file_size_bytes(file)

    # Alerta se exceder limite orientativo
    soft_limit_mb = current_app.config.get("WYSIWYG_UPLOAD_SOFT_LIMIT_MB")
    if soft_limit_mb and file_size > soft_limit_mb * 1024 * 1024:
        current_app.logger.warning(
            "Upload de imagem excedeu limite orientativo: %s (%.2f MB)",
            filename,
            file_size / (1024 * 1024),
        )

    # Salva arquivo com nome unico
    unique_name = f"{uuid4().hex}_{filename}"
    upload_folder = os.path.join(current_app.root_path, "static", "uploads")
    file_path = os.path.join(upload_folder, unique_name)

    try:
        os.makedirs(upload_folder, exist_ok=True)
        file.save(file_path)
        file_url = url_for("static", filename=f"uploads/{unique_name}", _external=True)
        preview_url = _build_preview_url(file_path, unique_name)

        # Registra acao no log de auditoria
        log_user_action(
            action_type=ActionType.UPLOAD,
            resource_type=ResourceType.FILE,
            action_description=f'Fez upload de imagem {filename}',
            new_values={
                'original_filename': filename,
                'saved_filename': unique_name,
                'file_size_bytes': file_size,
                'file_type': 'image',
                'file_url': file_url,
            }
        )

        return jsonify({"image_url": file_url, "preview_url": preview_url or file_url})

    except Exception as exc:
        current_app.logger.exception("Falha ao salvar upload de imagem", exc_info=exc)
        return jsonify({"error": "Erro ao processar upload"}), 500


@uploads_bp.route("/upload_image_from_url", methods=["POST"])
@login_required
def upload_image_from_url():
    from app.utils.audit import log_user_action, ActionType, ResourceType

    payload = request.get_json(silent=True) or {}
    image_url = (payload.get("image_url") or "").strip()
    if not image_url:
        return jsonify({"error": "URL da imagem nao informada"}), 400

    parsed = urlparse(image_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return jsonify({"error": "URL invalida"}), 400

    if not _is_public_image_host(parsed.hostname):
        return jsonify({"error": "Host da URL nao permitido"}), 400

    max_size_mb = int(current_app.config.get("WYSIWYG_URL_IMPORT_MAX_MB", 8))
    max_size_bytes = max_size_mb * 1024 * 1024

    try:
        data, content_type = _download_remote_image_bytes(image_url, max_size_bytes)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        return jsonify({"error": f"Falha ao importar imagem: {exc}"}), 400
    except Exception as exc:
        current_app.logger.exception("Falha ao baixar imagem remota", exc_info=exc)
        return jsonify({"error": "Erro ao importar imagem remota"}), 500

    filename = _build_remote_image_filename(parsed, content_type)
    file_obj = FileStorage(
        stream=BytesIO(data),
        filename=filename,
        content_type=content_type,
    )

    if not allowed_file(file_obj.filename or "") or not is_safe_image_upload(file_obj):
        return jsonify({"error": "Imagem remota invalida ou nao permitida"}), 400

    file_size = len(data)
    soft_limit_mb = current_app.config.get("WYSIWYG_UPLOAD_SOFT_LIMIT_MB")
    if soft_limit_mb and file_size > soft_limit_mb * 1024 * 1024:
        current_app.logger.warning(
            "Importacao remota excedeu limite orientativo: %s (%.2f MB)",
            filename,
            file_size / (1024 * 1024),
        )

    unique_name = f"{uuid4().hex}_{secure_filename(filename)}"
    upload_folder = os.path.join(current_app.root_path, "static", "uploads")
    file_path = os.path.join(upload_folder, unique_name)

    try:
        os.makedirs(upload_folder, exist_ok=True)
        file_obj.stream.seek(0)
        file_obj.save(file_path)
        file_url = url_for("static", filename=f"uploads/{unique_name}", _external=True)
        preview_url = _build_preview_url(file_path, unique_name)

        log_user_action(
            action_type=ActionType.UPLOAD,
            resource_type=ResourceType.FILE,
            action_description=f'Importou imagem remota {filename}',
            new_values={
                'original_filename': filename,
                'saved_filename': unique_name,
                'file_size_bytes': file_size,
                'file_type': 'image',
                'file_url': file_url,
                'source_url': image_url,
            }
        )

        return jsonify({"image_url": file_url, "preview_url": preview_url or file_url})
    except Exception as exc:
        current_app.logger.exception("Falha ao salvar imagem remota importada", exc_info=exc)
        return jsonify({"error": "Erro ao processar importacao da imagem"}), 500


@uploads_bp.route("/upload_file", methods=["POST"])
@login_required
def upload_file():
    """
    Processa upload de arquivos (imagens + PDFs) do editor WYSIWYG.

    Validacoes realizadas:
        - Arquivo presente na requisicao
        - Nome de arquivo valido
        - Extensao permitida (imagens + pdf)
        - MIME type e assinatura corretos

    Request:
        files['file']: Arquivo de imagem ou PDF

    Returns:
        200: {"file_url": "URL do arquivo salvo"}
        400: {"error": "Mensagem de erro"}
        500: {"error": "Erro ao processar upload"}
    """
    from app.utils.audit import log_user_action, ActionType, ResourceType

    # Validacao: arquivo presente
    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400

    file = request.files["file"]

    # Validacao: nome de arquivo
    if not file.filename:
        return jsonify({"error": "Nome de arquivo vazio"}), 400

    # Validacao: extensao
    if not allowed_file_with_pdf(file.filename):
        return jsonify({"error": "Extensao de arquivo nao permitida"}), 400

    # Validacao: conteudo (PDF ou imagem)
    is_pdf = file.filename.lower().endswith(".pdf")
    if is_pdf:
        if not is_safe_pdf_upload(file):
            return jsonify({"error": "PDF invalido ou corrompido"}), 400
    else:
        if not is_safe_image_upload(file):
            return jsonify({"error": "Imagem invalida ou nao permitida"}), 400

    filename = secure_filename(file.filename)
    file_size = get_file_size_bytes(file)

    # Alerta se exceder limite orientativo
    soft_limit_mb = current_app.config.get("WYSIWYG_UPLOAD_SOFT_LIMIT_MB")
    if soft_limit_mb and file_size > soft_limit_mb * 1024 * 1024:
        current_app.logger.warning(
            "Upload de arquivo excedeu limite orientativo: %s (%.2f MB)",
            filename,
            file_size / (1024 * 1024),
        )

    # Salva arquivo com nome unico
    unique_name = f"{uuid4().hex}_{filename}"
    upload_folder = os.path.join(current_app.root_path, "static", "uploads")
    file_path = os.path.join(upload_folder, unique_name)

    try:
        os.makedirs(upload_folder, exist_ok=True)
        file.save(file_path)
        file_url = url_for("static", filename=f"uploads/{unique_name}", _external=True)

        # Registra acao no log de auditoria
        log_user_action(
            action_type=ActionType.UPLOAD,
            resource_type=ResourceType.FILE,
            action_description=f'Fez upload de arquivo {filename}',
            new_values={
                'original_filename': filename,
                'saved_filename': unique_name,
                'file_size_bytes': file_size,
                'file_type': 'pdf' if is_pdf else 'image',
                'file_url': file_url,
            }
        )

        return jsonify({"file_url": file_url, "filename": filename})

    except Exception as exc:
        current_app.logger.exception("Falha ao salvar upload de arquivo", exc_info=exc)
        return jsonify({"error": "Erro ao processar upload"}), 500
