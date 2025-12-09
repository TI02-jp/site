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
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, request, url_for
from flask_login import login_required
from werkzeug.utils import secure_filename

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

        return jsonify({"image_url": file_url})

    except Exception as exc:
        current_app.logger.exception("Falha ao salvar upload de imagem", exc_info=exc)
        return jsonify({"error": "Erro ao processar upload"}), 500


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

        return jsonify({"file_url": file_url})

    except Exception as exc:
        current_app.logger.exception("Falha ao salvar upload de arquivo", exc_info=exc)
        return jsonify({"error": "Erro ao processar upload"}), 500
