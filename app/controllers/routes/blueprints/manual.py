"""
Blueprint para gestao do Manual do Usuario (videos tutoriais).

Este modulo contem rotas para visualizacao publica de videos tutoriais
e gerenciamento administrativo completo incluindo upload, edicao, exclusao,
reordenacao via drag-and-drop e CRUD de categorias.

Rotas Publicas (usuarios logados):
    - GET /manual: Lista videos com busca, filtros e ordenacao
    - GET /manual/assistir/<id>: Player de video dedicado

Rotas Administrativas (apenas admins):
    - GET /manual/admin: Painel de gerenciamento com drag-and-drop
    - GET/POST /manual/admin/video/novo: Upload de video
    - GET/POST /manual/admin/video/<id>/editar: Edicao de video
    - POST /manual/admin/video/<id>/deletar: Remocao de video
    - POST /manual/admin/categorias: CRUD de categorias (modal)
    - POST /manual/admin/reordenar: Atualizacao de ordem via AJAX

Dependencias:
    - models: ManualCategory, ManualVideo, User
    - forms: ManualCategoryForm, ManualVideoForm
    - validators: validate_video_upload, validate_image_upload

Autor: Claude Code
Data: 2025-12-18
"""

import os
import re
import subprocess
from shutil import which
from uuid import uuid4

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import or_
from werkzeug.utils import secure_filename

from app import cache, db
from app.forms import ManualCategoryForm, ManualVideoForm
from app.controllers.routes._base import (
    MANUAL_VIDEOS_SUBDIR,
    MANUAL_THUMBNAILS_SUBDIR,
)
from app.controllers.routes._decorators import admin_required
from app.controllers.routes._validators import (
    validate_video_upload,
    validate_image_upload,
)
from app.models.tables import ManualCategory, ManualVideo


# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

manual_bp = Blueprint("manual", __name__, url_prefix="/manual")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

@cache.memoize(timeout=300)
def _get_categories_catalog() -> list[ManualCategory]:
    """Catalogo cacheado de categorias ordenadas por display_order."""
    return ManualCategory.query.order_by(ManualCategory.display_order).all()


def _invalidate_categories_cache() -> None:
    """Limpa cache do catalogo de categorias."""
    cache.delete_memoized(_get_categories_catalog)


def _save_video_file(uploaded_file) -> dict[str, str | int | None]:
    """
    Persiste arquivo de video e retorna metadados.

    Args:
        uploaded_file: Arquivo enviado via formulario.

    Returns:
        dict: Metadados do arquivo {path, name, mime_type, size}.
    """
    original_name = secure_filename(uploaded_file.filename or "")
    extension = os.path.splitext(original_name)[1].lower()
    unique_name = f"{uuid4().hex}{extension}"

    upload_directory = os.path.join(
        current_app.root_path, "static", MANUAL_VIDEOS_SUBDIR
    )
    os.makedirs(upload_directory, exist_ok=True)

    stored_path = os.path.join(upload_directory, unique_name)
    uploaded_file.save(stored_path)

    # Obter tamanho do arquivo
    file_size = os.path.getsize(stored_path)

    relative_path = os.path.join(MANUAL_VIDEOS_SUBDIR, unique_name).replace("\\", "/")
    mime_type = uploaded_file.mimetype or "video/mp4"

    return {
        "path": relative_path,
        "name": original_name or None,
        "mime_type": mime_type,
        "size": file_size,
    }


def _save_thumbnail_file(uploaded_file) -> str:
    """
    Persiste thumbnail e retorna caminho relativo.

    Args:
        uploaded_file: Arquivo enviado via formulario.

    Returns:
        str: Caminho relativo da thumbnail.
    """
    original_name = secure_filename(uploaded_file.filename or "")
    extension = os.path.splitext(original_name)[1].lower()
    unique_name = f"{uuid4().hex}{extension}"

    upload_directory = os.path.join(
        current_app.root_path, "static", MANUAL_THUMBNAILS_SUBDIR
    )
    os.makedirs(upload_directory, exist_ok=True)

    stored_path = os.path.join(upload_directory, unique_name)
    uploaded_file.save(stored_path)

    relative_path = os.path.join(MANUAL_THUMBNAILS_SUBDIR, unique_name).replace("\\", "/")

    return relative_path


def _delete_video_files(video: ManualVideo) -> None:
    """
    Remove arquivos fisicos de video e thumbnail do storage.

    Args:
        video: Instancia do video a ter arquivos removidos.
    """
    # Remove video
    if video.video_path:
        video_full_path = os.path.join(current_app.root_path, "static", video.video_path)
        try:
            if os.path.exists(video_full_path):
                os.remove(video_full_path)
        except Exception as e:
            current_app.logger.warning(f"Erro ao remover video {video_full_path}: {e}")

    # Remove thumbnail
    if video.thumbnail_path:
        thumb_full_path = os.path.join(current_app.root_path, "static", video.thumbnail_path)
        try:
            if os.path.exists(thumb_full_path):
                os.remove(thumb_full_path)
        except Exception as e:
            current_app.logger.warning(f"Erro ao remover thumbnail {thumb_full_path}: {e}")


def _extract_video_duration(video_path: str) -> tuple[int | None, str | None]:
    """
    Extrai duracao do video usando ffmpeg/ffprobe.

    Args:
        video_path: Caminho relativo do video.

    Returns:
        tuple: (duracao_segundos, duracao_formatada) ou (None, None) em caso de erro.
    """
    try:
        full_path = os.path.join(current_app.root_path, "static", video_path)
        ffmpeg_bin = _get_ffmpeg_binary()
        if not ffmpeg_bin:
            current_app.logger.warning("ffmpeg/ffprobe não encontrado para extrair duração")
            return None, None

        result = subprocess.run(
            [
                ffmpeg_bin,
                "-i",
                full_path,
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        output = result.stderr or result.stdout or ""
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.?\d*", output)
        if match:
            hours, minutes, seconds = map(int, match.groups()[:3])
            duration_seconds = hours * 3600 + minutes * 60 + seconds
            formatted = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            return duration_seconds, formatted
    except Exception as e:
        current_app.logger.warning(f"Erro ao extrair duracao: {e}")

    return None, None


def _generate_thumbnail_from_video(video_path: str, timestamp: float = 1.0) -> str | None:
    """
    Gera thumbnail (primeiro frame) usando ffmpeg.

    Args:
        video_path: Caminho relativo do video dentro de /static.
        timestamp: Ponto do video (em segundos) para capturar o frame.

    Returns:
        str | None: Caminho relativo da thumbnail ou None em caso de erro.
    """
    try:
        ffmpeg_bin = _get_ffmpeg_binary()
        if not ffmpeg_bin:
            current_app.logger.warning("ffmpeg não encontrado para gerar thumbnail")
            return None

        video_full_path = os.path.join(current_app.root_path, "static", video_path)
        if not os.path.exists(video_full_path):
            return None

        thumb_directory = os.path.join(
            current_app.root_path, "static", MANUAL_THUMBNAILS_SUBDIR
        )
        os.makedirs(thumb_directory, exist_ok=True)

        filename = f"{uuid4().hex}.jpg"
        output_full_path = os.path.join(thumb_directory, filename)

        # Usa ffmpeg para capturar um frame e gerar uma imagem otimizada
        result = subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-ss",
                str(timestamp),
                "-i",
                video_full_path,
                "-frames:v",
                "1",
                "-vf",
                "scale=1280:-1",
                "-q:v",
                "5",
                output_full_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            current_app.logger.warning(
                "ffmpeg falhou ao gerar thumbnail: %s", result.stderr
            )
            return None

        relative_path = os.path.join(MANUAL_THUMBNAILS_SUBDIR, filename).replace(
            "\\", "/"
        )
        return relative_path
    except Exception as e:
        current_app.logger.warning(f"Erro ao gerar thumbnail: {e}")
        return None


def _get_ffmpeg_binary() -> str | None:
    """
    Retorna caminho para binário do ffmpeg (PATH ou imageio-ffmpeg).
    """
    # 1) PATH
    ffmpeg_in_path = which("ffmpeg")
    if ffmpeg_in_path:
        return ffmpeg_in_path

    # 2) imageio-ffmpeg (baixa binário se necessário)
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _ensure_video_metadata(video: ManualVideo) -> bool:
    """
    Garante que o video tenha duracao e thumbnail geradas.

    Returns:
        bool: True se algum campo foi atualizado.
    """
    changed = False
    video_full_path = os.path.join(current_app.root_path, "static", video.video_path)

    if not os.path.exists(video_full_path):
        return False

    if not video.duration_seconds or not video.duration_formatted:
        duration_seconds, duration_formatted = _extract_video_duration(video.video_path)
        if duration_seconds and duration_formatted:
            video.duration_seconds = duration_seconds
            video.duration_formatted = duration_formatted
            changed = True

    thumb_missing = not video.thumbnail_path or not os.path.exists(
        os.path.join(current_app.root_path, "static", video.thumbnail_path)
    )
    if thumb_missing:
        thumbnail_path = _generate_thumbnail_from_video(video.video_path)
        if thumbnail_path:
            video.thumbnail_path = thumbnail_path
            changed = True

    return changed


# =============================================================================
# ROTAS PUBLICAS
# =============================================================================

@manual_bp.route("/")
@login_required
def listar_videos():
    """Lista videos com busca, filtros e ordenacao."""
    search = request.args.get("search", "").strip()
    category_id = request.args.get("category", type=int)
    order = request.args.get("order", "recent")  # recent ou az

    query = ManualVideo.query.filter_by(is_active=True)

    # Filtro por categoria
    if category_id:
        query = query.filter_by(category_id=category_id)

    # Busca por titulo/descricao
    if search:
        like_pattern = f"%{search}%"
        query = query.filter(
            or_(
                ManualVideo.title.ilike(like_pattern),
                ManualVideo.description.ilike(like_pattern)
            )
        )

    # Ordenacao
    if order == "az":
        query = query.order_by(ManualVideo.title)
    else:
        query = query.order_by(ManualVideo.created_at.desc())

    videos = query.all()
    categories = _get_categories_catalog()

    # Backfill de metadados (thumbnail/duração) para vídeos sem informação
    updated = False
    for video in videos:
        if _ensure_video_metadata(video):
            updated = True
    if updated:
        db.session.commit()

    return render_template(
        "manual/listar_videos.html",
        videos=videos,
        categories=categories,
        search=search,
        selected_category=category_id,
        order=order
    )


@manual_bp.route("/assistir/<int:video_id>")
@login_required
def assistir_video(video_id):
    """Pagina do player de video dedicado."""
    video = ManualVideo.query.get_or_404(video_id)

    if not video.is_active:
        abort(404)

    # Garante metadata antes de exibir
    if _ensure_video_metadata(video):
        db.session.commit()

    # Videos relacionados (mesma categoria, exceto o atual)
    related_videos = (
        ManualVideo.query
        .filter_by(category_id=video.category_id, is_active=True)
        .filter(ManualVideo.id != video_id)
        .order_by(ManualVideo.display_order, ManualVideo.created_at.desc())
        .limit(5)
        .all()
    )

    return render_template(
        "manual/assistir_video.html",
        video=video,
        related_videos=related_videos
    )


# =============================================================================
# ROTAS ADMINISTRATIVAS
# =============================================================================

@manual_bp.route("/admin")
@login_required
@admin_required
def admin_painel():
    """Painel de gerenciamento com drag-and-drop."""
    videos = (
        ManualVideo.query
        .order_by(ManualVideo.display_order, ManualVideo.created_at.desc())
        .all()
    )
    categories = _get_categories_catalog()

    # Estatisticas
    total_videos = len(videos)
    active_videos = sum(1 for v in videos if v.is_active)
    total_storage_mb = sum(v.file_size for v in videos) / (1024 * 1024)

    return render_template(
        "manual/admin_painel.html",
        videos=videos,
        categories=categories,
        total_videos=total_videos,
        active_videos=active_videos,
        total_storage_mb=round(total_storage_mb, 2)
    )


@manual_bp.route("/admin/video/novo", methods=["GET", "POST"])
@login_required
@admin_required
def admin_video_novo():
    """Upload de novo video."""
    form = ManualVideoForm()

    # Preenche choices de categorias
    categories = _get_categories_catalog()
    form.category_id.choices = [(c.id, c.name) for c in categories]

    if form.validate_on_submit():
        # Validacao do arquivo de video (obrigatorio)
        if not form.video_file.data:
            flash("Selecione um arquivo de video.", "danger")
            return render_template("manual/form_video.html", form=form, title="Novo Video")

        # Validacao de video
        valid, error = validate_video_upload(form.video_file.data, 1024)
        if not valid:
            flash(error, "danger")
            return render_template("manual/form_video.html", form=form, title="Novo Video")

        # Salva video
        video_data = _save_video_file(form.video_file.data)

        # Salva thumbnail (upload ou geracao automatica)
        thumbnail_path = None
        if form.thumbnail.data:
            valid_thumb, error_thumb = validate_image_upload(form.thumbnail.data, 10)
            if valid_thumb:
                thumbnail_path = _save_thumbnail_file(form.thumbnail.data)
            else:
                flash(f"Aviso: {error_thumb}. Thumbnail nao foi salva.", "warning")
        else:
            thumbnail_path = _generate_thumbnail_from_video(video_data["path"])

        # Extrai duracao
        duration_seconds, duration_formatted = _extract_video_duration(video_data["path"])

        # Cria registro
        video = ManualVideo(
            title=form.title.data,
            description=form.description.data,
            category_id=form.category_id.data,
            video_path=video_data["path"],
            original_filename=video_data["name"],
            thumbnail_path=thumbnail_path,
            mime_type=video_data["mime_type"],
            file_size=video_data["size"],
            duration_seconds=duration_seconds,
            duration_formatted=duration_formatted,
            display_order=0,
            is_active=True,
            created_by_id=current_user.id
        )

        db.session.add(video)
        db.session.commit()

        flash(f"Video '{video.title}' adicionado com sucesso!", "success")
        return redirect(url_for("manual.admin_painel"))

    return render_template("manual/form_video.html", form=form, title="Novo Video")


@manual_bp.route("/admin/video/<int:video_id>/editar", methods=["GET", "POST"])
@login_required
@admin_required
def admin_video_editar(video_id):
    """Edicao de video existente."""
    video = ManualVideo.query.get_or_404(video_id)
    form = ManualVideoForm(obj=video)

    # Preenche choices de categorias
    categories = _get_categories_catalog()
    form.category_id.choices = [(c.id, c.name) for c in categories]

    if form.validate_on_submit():
        # Atualiza metadados
        video.title = form.title.data
        video.description = form.description.data
        video.category_id = form.category_id.data

        # Substitui video se novo arquivo foi enviado
        if form.video_file.data:
            valid, error = validate_video_upload(form.video_file.data, 1024)
            if not valid:
                flash(error, "danger")
                return render_template("manual/form_video.html", form=form, title="Editar Video", video=video)

            # Remove video antigo
            old_video_path = video.video_path
            if old_video_path:
                old_full_path = os.path.join(current_app.root_path, "static", old_video_path)
                try:
                    if os.path.exists(old_full_path):
                        os.remove(old_full_path)
                except Exception as e:
                    current_app.logger.warning(f"Erro ao remover video antigo: {e}")

            # Salva novo video
            video_data = _save_video_file(form.video_file.data)
            video.video_path = video_data["path"]
            video.original_filename = video_data["name"]
            video.mime_type = video_data["mime_type"]
            video.file_size = video_data["size"]

            # Reextrair duracao
            duration_seconds, duration_formatted = _extract_video_duration(video_data["path"])
            video.duration_seconds = duration_seconds
            video.duration_formatted = duration_formatted

            # Regenerar thumbnail se nao for enviada uma nova
            if not form.thumbnail.data:
                if video.thumbnail_path:
                    old_thumb_path = os.path.join(
                        current_app.root_path, "static", video.thumbnail_path
                    )
                    try:
                        if os.path.exists(old_thumb_path):
                            os.remove(old_thumb_path)
                    except Exception as e:
                        current_app.logger.warning(f"Erro ao remover thumbnail antiga: {e}")

                generated_thumb = _generate_thumbnail_from_video(video.video_path)
                if generated_thumb:
                    video.thumbnail_path = generated_thumb

        # Substitui thumbnail se novo arquivo foi enviado
        if form.thumbnail.data:
            valid_thumb, error_thumb = validate_image_upload(form.thumbnail.data, 10)
            if valid_thumb:
                # Remove thumbnail antiga
                if video.thumbnail_path:
                    old_thumb_path = os.path.join(current_app.root_path, "static", video.thumbnail_path)
                    try:
                        if os.path.exists(old_thumb_path):
                            os.remove(old_thumb_path)
                    except Exception as e:
                        current_app.logger.warning(f"Erro ao remover thumbnail antiga: {e}")

                video.thumbnail_path = _save_thumbnail_file(form.thumbnail.data)
            else:
                flash(f"Aviso: {error_thumb}. Thumbnail nao foi atualizada.", "warning")

        db.session.commit()

        flash(f"Video '{video.title}' atualizado com sucesso!", "success")
        return redirect(url_for("manual.admin_painel"))

    return render_template("manual/form_video.html", form=form, title="Editar Video", video=video)


@manual_bp.route("/admin/video/<int:video_id>/deletar", methods=["POST"])
@login_required
@admin_required
def admin_video_deletar(video_id):
    """Remocao de video."""
    video = ManualVideo.query.get_or_404(video_id)

    # Remove arquivos fisicos
    _delete_video_files(video)

    # Remove registro
    db.session.delete(video)
    db.session.commit()

    flash(f"Video '{video.title}' removido com sucesso!", "success")
    return redirect(url_for("manual.admin_painel"))


@manual_bp.route("/admin/reordenar", methods=["POST"])
@login_required
@admin_required
def admin_reordenar():
    """Atualizacao de ordem via AJAX (drag-and-drop)."""
    try:
        data = request.get_json()
        video_ids = data.get("video_ids", [])

        if not video_ids:
            return jsonify({"success": False, "error": "Nenhum video fornecido"}), 400

        # Atualiza display_order de cada video
        for index, video_id in enumerate(video_ids):
            video = ManualVideo.query.get(video_id)
            if video:
                video.display_order = index

        db.session.commit()

        return jsonify({"success": True})
    except Exception as e:
        current_app.logger.error(f"Erro ao reordenar videos: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@manual_bp.route("/admin/categorias", methods=["POST"])
@login_required
@admin_required
def admin_categorias():
    """CRUD de categorias via modal."""
    form_name = request.form.get("form_name")
    category_id = request.form.get("category_id", type=int)

    if form_name == "category_create":
        form = ManualCategoryForm()
        if form.validate_on_submit():
            # Verifica duplicidade
            existing = ManualCategory.query.filter_by(name=form.name.data).first()
            if existing:
                flash("Ja existe uma categoria com esse nome.", "warning")
                return redirect(url_for("manual.admin_painel"))

            category = ManualCategory(
                name=form.name.data,
                description=form.description.data,
                display_order=0
            )
            db.session.add(category)
            db.session.commit()

            _invalidate_categories_cache()

            flash(f"Categoria '{category.name}' criada com sucesso!", "success")
        else:
            flash("Erro ao criar categoria. Verifique os dados.", "danger")

    elif form_name == "category_update" and category_id:
        category = ManualCategory.query.get_or_404(category_id)
        form = ManualCategoryForm()

        if form.validate_on_submit():
            # Verifica duplicidade (exceto a propria)
            existing = (
                ManualCategory.query
                .filter(ManualCategory.name == form.name.data)
                .filter(ManualCategory.id != category_id)
                .first()
            )
            if existing:
                flash("Ja existe uma categoria com esse nome.", "warning")
                return redirect(url_for("manual.admin_painel"))

            category.name = form.name.data
            category.description = form.description.data
            db.session.commit()

            _invalidate_categories_cache()

            flash(f"Categoria '{category.name}' atualizada com sucesso!", "success")
        else:
            flash("Erro ao atualizar categoria. Verifique os dados.", "danger")

    elif form_name == "category_delete" and category_id:
        category = ManualCategory.query.get_or_404(category_id)

        # Verifica se ha videos nesta categoria
        videos_count = ManualVideo.query.filter_by(category_id=category_id).count()
        if videos_count > 0:
            flash(f"Nao e possivel excluir a categoria '{category.name}' pois ela contem {videos_count} video(s).", "danger")
            return redirect(url_for("manual.admin_painel"))

        category_name = category.name
        db.session.delete(category)
        db.session.commit()

        _invalidate_categories_cache()

        flash(f"Categoria '{category_name}' removida com sucesso!", "success")

    return redirect(url_for("manual.admin_painel"))
