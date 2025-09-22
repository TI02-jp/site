"""Flask route handlers for the web application."""

from flask import (
    render_template,
    redirect,
    url_for,
    flash,
    request,
    abort,
    jsonify,
    current_app,
    session,
)
from functools import wraps
from collections import Counter
from flask_login import current_user, login_required, login_user, logout_user
from app import app, db, csrf
from app.utils.security import sanitize_html
from app.models.tables import (
    User,
    Empresa,
    Departamento,
    Consultoria,
    Setor,
    Tag,
    Inclusao,
    Session,
    SAO_PAULO_TZ,
    Reuniao,
    ReuniaoStatus,
    Task,
    TaskStatus,
    TaskPriority,
    TaskStatusHistory,
    TaskNotification,
    AccessLink,
    Course,
    VideoFolder,
    VideoModule,
    VideoAsset,
)
from app.forms import (
    # Formulários de autenticação
    LoginForm,
    RegistrationForm,
    # Demais formulários da aplicação
    EmpresaForm,
    EditUserForm,
    DepartamentoFiscalForm,
    DepartamentoContabilForm,
    DepartamentoPessoalForm,
    DepartamentoAdministrativoForm,
    DepartamentoFinanceiroForm,
    ConsultoriaForm,
    SetorForm,
    TagForm,
    MeetingForm,
    TaskForm,
    AccessLinkForm,
    CourseForm,
    VideoFolderForm,
    VideoModuleForm,
    VideoAssetForm,
    VIDEO_FILE_EXTENSIONS,
)
from flask_wtf.file import FileField, FileAllowed
from wtforms.validators import Optional
import os, json, re, secrets, unicodedata, mimetypes
import requests
from werkzeug.utils import secure_filename
from uuid import uuid4
from sqlalchemy import or_, cast, String
import sqlalchemy as sa
from sqlalchemy.orm import joinedload
from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token
from google.auth.transport.requests import Request
from app.services.cnpj import consultar_cnpj
from app.services.courses import CourseStatus, get_courses_overview
from app.services.google_calendar import get_calendar_timezone
from app.services.meeting_room import (
    populate_participants_choices,
    fetch_raw_events,
    create_meeting_and_event,
    update_meeting,
    combine_events,
    delete_meeting,
)
import plotly.graph_objects as go
from plotly.colors import qualitative
from werkzeug.exceptions import RequestEntityTooLarge
from datetime import datetime, timedelta, timezone
from typing import Any

GOOGLE_OAUTH_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/calendar",
]

EXCLUDED_TASK_TAGS = ["Reunião"]
EXCLUDED_TASK_TAGS_LOWER = {t.lower() for t in EXCLUDED_TASK_TAGS}


ACESSOS_CATEGORIES: dict[str, dict[str, Any]] = {
    "fiscal": {
        "title": "Fiscal",
        "description": "Sistemas utilizados pela equipe fiscal para gestão de obrigações e documentos.",
        "icon": "bi bi-clipboard-data",
    },
    "contabil": {
        "title": "Contábil",
        "description": "Ferramentas que apoiam a rotina contábil e o envio de documentos.",
        "icon": "bi bi-journal-check",
    },
}


ACESSOS_DIRECT_LINKS: list[dict[str, str]] = [
    {
        "label": "Acessórias",
        "url": "https://app.acessorias.com/sysmain.php",
        "description": "Acesse o sistema Acessórias para conferir obrigações fiscais.",
        "icon": "bi bi-box-arrow-up-right",
    },
    {
        "label": "SIEG",
        "url": "https://auth.sieg.com/login",
        "description": "Portal SIEG para captura de notas e integrações contábeis.",
        "icon": "bi bi-box-arrow-up-right",
    },
]


def build_google_flow(state: str | None = None) -> Flow:
    """Return a configured Google OAuth ``Flow`` instance."""
    if not (
        current_app.config.get("GOOGLE_CLIENT_ID")
        and current_app.config.get("GOOGLE_CLIENT_SECRET")
    ):
        abort(404)
    return Flow.from_client_config(
        {
            "web": {
                "client_id": current_app.config["GOOGLE_CLIENT_ID"],
                "client_secret": current_app.config["GOOGLE_CLIENT_SECRET"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=GOOGLE_OAUTH_SCOPES,
        state=state,
    )


def get_google_redirect_uri() -> str:
    """Return the redirect URI registered with Google."""
    return current_app.config.get("GOOGLE_REDIRECT_URI") or url_for(
        "google_callback",
        _external=True,
        _scheme=current_app.config["PREFERRED_URL_SCHEME"],
    )


def credentials_to_dict(credentials):
    """Convert Google credentials object to a serializable dict."""
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }


@app.context_processor
def inject_stats():
    """Inject global statistics into templates."""
    if current_user.is_authenticated:
        total_empresas = Empresa.query.count()
        total_usuarios = User.query.count() if current_user.role == "admin" else 0
        online_count = 0
        if current_user.role == "admin":
            cutoff = datetime.utcnow() - timedelta(minutes=5)
            online_count = User.query.filter(User.last_seen >= cutoff).count()
        return {
            "total_empresas": total_empresas,
            "total_usuarios": total_usuarios,
            "online_users_count": online_count,
        }
    return {}


# Allowed file extensions for uploads
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
ALLOWED_VIDEO_EXTENSIONS = {"mp4", "m4v", "mov", "mkv", "avi", "wmv", "webm"}


def allowed_image_file(filename: str) -> bool:
    """Return ``True`` when ``filename`` has an allowed image extension."""

    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def allowed_video_file(filename: str) -> bool:
    """Return ``True`` when ``filename`` has an allowed video extension."""

    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_VIDEO_EXTENSIONS


def slugify(value: str) -> str:
    """Return a URL-friendly representation of ``value``."""

    if not value:
        return uuid4().hex[:8]
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return cleaned or uuid4().hex[:8]


def _save_uploaded_video(file_storage) -> dict[str, str | int] | None:
    """Persist an uploaded video inside ``static/uploads/videos/assets``."""

    if not file_storage or file_storage.filename == "":
        return None

    if not allowed_video_file(file_storage.filename):
        return None

    filename = secure_filename(file_storage.filename)
    unique_name = f"{uuid4().hex}_{filename}"
    upload_folder = os.path.join(
        current_app.root_path,
        "static",
        "uploads",
        "videos",
        "assets",
    )
    os.makedirs(upload_folder, exist_ok=True)
    destination = os.path.join(upload_folder, unique_name)

    try:
        file_storage.save(destination)
    except OSError as exc:
        current_app.logger.exception("Erro ao salvar vídeo enviado: %s", exc)
        return None

    mime_type, _ = mimetypes.guess_type(destination)
    try:
        file_size = os.path.getsize(destination)
    except OSError:
        file_size = 0

    return {
        "relative_path": f"uploads/videos/assets/{unique_name}",
        "original_name": filename,
        "mime_type": mime_type or "application/octet-stream",
        "file_size": file_size,
    }


def _delete_static_file(static_path: str | None) -> None:
    """Remove a stored static file if it exists."""

    if not static_path:
        return
    absolute_path = os.path.join(current_app.root_path, "static", static_path)
    try:
        if os.path.exists(absolute_path):
            os.remove(absolute_path)
    except OSError:
        current_app.logger.warning("Não foi possível remover o arquivo %s", static_path)


@app.errorhandler(RequestEntityTooLarge)
def handle_large_file(e):
    """Return JSON error when uploaded file exceeds limit."""
    return jsonify({"error": "Arquivo excede o tamanho permitido"}), 413


def format_phone(digits: str) -> str:
    """Format raw digit strings into phone numbers."""
    if len(digits) >= 11:
        return f"({digits[:2]}) {digits[2:7]}-{digits[7:11]}"
    if len(digits) >= 10:
        return f"({digits[:2]}) {digits[2:6]}-{digits[6:10]}"
    return digits


def normalize_contatos(contatos):
    """Normalize contact entries into a consistent structure."""
    if not contatos:
        return []
    if all(isinstance(c, dict) and "meios" in c for c in contatos):
        for c in contatos:
            meios = c.get("meios") or []
            for m in meios:
                if "valor" in m and "endereco" not in m:
                    m["endereco"] = m.pop("valor")
                if m.get("tipo") in ("telefone", "whatsapp"):
                    digits = re.sub(r"\D", "", m.get("endereco", ""))
                    m["endereco"] = format_phone(digits)
        return contatos
    grouped = {}
    for c in contatos:
        if not isinstance(c, dict):
            continue
        nome = c.get("nome", "")
        tipo = c.get("tipo")
        endereco = c.get("endereco") or c.get("valor", "")
        if tipo in ("telefone", "whatsapp"):
            digits = re.sub(r"\D", "", endereco)
            endereco = format_phone(digits)
        contato = grouped.setdefault(nome, {"nome": nome, "meios": []})
        contato["meios"].append({"tipo": tipo, "endereco": endereco})
    return list(grouped.values())


@app.route("/upload_image", methods=["POST"])
@login_required
def upload_image():
    """Handle image uploads from the WYSIWYG editor."""
    if "image" not in request.files:
        return jsonify({"error": "Nenhuma imagem enviada"}), 400

    file = request.files["image"]
    if file.filename == "":
        return jsonify({"error": "Nome de arquivo vazio"}), 400

    if file and allowed_image_file(file.filename):
        filename = secure_filename(file.filename)
        unique_name = f"{uuid4().hex}_{filename}"
        upload_folder = os.path.join(current_app.root_path, "static", "uploads")
        file_path = os.path.join(upload_folder, unique_name)

        try:
            os.makedirs(upload_folder, exist_ok=True)
            file.save(file_path)
            file_url = url_for("static", filename=f"uploads/{unique_name}")
            return jsonify({"image_url": file_url})
        except Exception as e:
            return jsonify({"error": f"Erro no servidor ao salvar: {e}"}), 500

    return jsonify({"error": "Arquivo inválido ou não permitido"}), 400


def admin_required(f):
    """Decorator that restricts access to admin users."""

    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role != "admin":
            abort(403)
        return f(*args, **kwargs)

    return decorated_function


def user_has_tag(tag_name: str) -> bool:
    """Return True if current user has a tag with the given name."""
    return any(tag.nome.lower() == tag_name.lower() for tag in current_user.tags)


@app.context_processor
def inject_user_tag_helpers():
    """Expose user tag helper utilities to templates."""
    return dict(user_has_tag=user_has_tag)


@app.context_processor
def inject_task_tags():
    """Provide task-related tags for dynamic sidebar menus."""
    if not current_user.is_authenticated:
        return {"tasks_tags": []}
    tags = sorted(
        [t for t in current_user.tags if t.nome.lower() not in EXCLUDED_TASK_TAGS_LOWER],
        key=lambda t: t.nome,
    )
    return {"tasks_tags": tags}


@app.context_processor
def inject_notification_counts():
    """Expose the number of unread task notifications to templates."""

    if not current_user.is_authenticated:
        return {"unread_notifications_count": 0}
    unread = TaskNotification.query.filter(
        TaskNotification.user_id == current_user.id,
        TaskNotification.read_at.is_(None),
    ).count()
    return {"unread_notifications_count": unread}


@app.route("/")
def index():
    """Redirect users to the appropriate first page."""
    if current_user.is_authenticated:
        if current_user.role == "admin":
            return redirect(url_for("tasks_overview"))
        first_tag = current_user.tags[0] if current_user.tags else None
        if first_tag:
            return redirect(url_for("tasks_sector", tag_id=first_tag.id))
        return redirect(url_for("home"))
    return redirect(url_for("login"))


@app.route("/home")
@login_required
def home():
    """Render the authenticated home page."""
    return render_template("home.html")


@app.route("/cursos", methods=["GET", "POST"])
@login_required
def cursos():
    """Display the curated catalog of internal courses."""

    form = CourseForm()
    sector_choices = [
        (sector.id, sector.nome)
        for sector in Setor.query.order_by(Setor.nome.asc()).all()
    ]
    participant_choices = [
        (user.id, user.name)
        for user in User.query.filter_by(ativo=True).order_by(User.name.asc()).all()
    ]
    form.sectors.choices = sector_choices
    form.participants.choices = participant_choices

    sector_lookup = {value: label for value, label in sector_choices}
    participant_lookup = {value: label for value, label in participant_choices}

    course_id_raw = (form.course_id.data or "").strip()

    if form.validate_on_submit():
        course_id: int | None = None
        if course_id_raw:
            try:
                course_id = int(course_id_raw)
            except ValueError:
                course_id = None

        selected_sector_names = [
            sector_lookup[sector_id]
            for sector_id in form.sectors.data
            if sector_id in sector_lookup
        ]
        selected_participant_names = [
            participant_lookup[user_id]
            for user_id in form.participants.data
            if user_id in participant_lookup
        ]
        if course_id is not None:
            existing_id = db.session.execute(
                sa.select(Course.id).where(Course.id == course_id)
            ).scalar_one_or_none()

            if existing_id is None:
                flash("O curso selecionado não foi encontrado. Tente novamente.", "danger")
                return redirect(url_for("cursos"))

            db.session.execute(
                sa.update(Course)
                .where(Course.id == course_id)
                .values(
                    name=form.name.data.strip(),
                    instructor=form.instructor.data.strip(),
                    sectors=", ".join(selected_sector_names),
                    participants=", ".join(selected_participant_names),
                    workload=form.workload.data,
                    start_date=form.start_date.data,
                    schedule_start=form.schedule_start.data,
                    schedule_end=form.schedule_end.data,
                    completion_date=form.completion_date.data,
                    status=form.status.data,
                )
            )
            db.session.commit()
            flash("Curso atualizado com sucesso!", "success")
        else:
            course = Course(
                name=form.name.data.strip(),
                instructor=form.instructor.data.strip(),
                sectors=", ".join(selected_sector_names),
                participants=", ".join(selected_participant_names),
                workload=form.workload.data,
                start_date=form.start_date.data,
                schedule_start=form.schedule_start.data,
                schedule_end=form.schedule_end.data,
                completion_date=form.completion_date.data,
                status=form.status.data,
            )
            db.session.add(course)
            db.session.commit()
            flash("Curso cadastrado com sucesso!", "success")
        return redirect(url_for("cursos"))

    elif request.method == "POST":
        flash(
            "Não foi possível salvar o curso. Verifique os campos destacados e tente novamente.",
            "danger",
        )

    courses = get_courses_overview()
    status_counts = Counter(course.status for course in courses)
    status_classes = {
        CourseStatus.COMPLETED: "status-pill--completed",
        CourseStatus.PLANNED: "status-pill--planned",
        CourseStatus.DELAYED: "status-pill--delayed",
    }
    return render_template(
        "cursos.html",
        courses=courses,
        status_counts=status_counts,
        status_classes=status_classes,
        CourseStatus=CourseStatus,
        form=form,
        editing_course_id=course_id_raw,
    )


@app.route("/videos")
@login_required
def videos():
    """Render the video library with optional folder and tag filters."""

    selected_folder_id = request.args.get("folder", type=int)
    selected_tag_slug = request.args.get("tag", type=str)

    module_query = VideoModule.query.options(
        joinedload(VideoModule.folder),
        joinedload(VideoModule.tags),
        joinedload(VideoModule.videos),
    )

    if selected_folder_id:
        module_query = module_query.filter(VideoModule.folder_id == selected_folder_id)

    tags = Tag.query.order_by(Tag.nome.asc()).all()
    tag_options = [
        {
            "id": tag.id,
            "name": tag.nome,
            "slug": slugify(tag.nome),
            "model": tag,
        }
        for tag in tags
    ]

    selected_tag = next((tag for tag in tag_options if tag["slug"] == selected_tag_slug), None)

    if selected_tag:
        module_query = module_query.join(VideoModule.tags).filter(
            Tag.id == selected_tag["id"]
        )
    elif selected_tag_slug:
        module_query = module_query.filter(sa.false())

    modules = module_query.order_by(VideoModule.title.asc()).all()
    folders = VideoFolder.query.order_by(VideoFolder.name.asc()).all()
    selected_folder = next(
        (folder for folder in folders if folder.id == selected_folder_id), None
    )

    return render_template(
        "videos.html",
        modules=modules,
        folders=folders,
        tags=tag_options,
        selected_folder_id=selected_folder_id,
        selected_tag_slug=selected_tag_slug,
        selected_tag=selected_tag,
        selected_folder=selected_folder,
    )


def _populate_video_forms(
    module_form: VideoModuleForm | None = None,
    video_form: VideoAssetForm | None = None,
) -> tuple[list[tuple[int, str]], list[tuple[int, str]], list[tuple[int, str]]]:
    """Populate dynamic choices for video-related forms."""

    folder_choices = [
        (folder.id, folder.name)
        for folder in VideoFolder.query.order_by(VideoFolder.name.asc()).all()
    ]

    tag_choices = [
        (tag.id, tag.nome)
        for tag in Tag.query.order_by(Tag.nome.asc()).all()
    ]

    module_choices = [
        (module.id, module.title)
        for module in VideoModule.query.order_by(VideoModule.title.asc()).all()
    ]

    if module_form is not None:
        module_form.folder_id.choices = folder_choices
        module_form.tags.choices = tag_choices

    if video_form is not None:
        _ensure_video_upload_field(video_form)
        video_form.module_id.choices = module_choices

    return folder_choices, tag_choices, module_choices


def _ensure_video_upload_field(video_form: VideoAssetForm) -> None:
    """Guarantee the long-form video form exposes a file upload field.

    Some deployments reported ``video_form.video_file`` being missing at
    runtime, which makes the management template crash while also preventing
    uploads. To avoid this hard failure we recreate the field dynamically when
    it is absent so both the view layer and the POST handler can operate
    normally.
    """

    if hasattr(video_form, "video_file") and video_form.video_file is not None:
        return

    file_field = FileField(
        "Arquivo do vídeo",
        validators=[
            Optional(),
            FileAllowed(
                VIDEO_FILE_EXTENSIONS,
                "Formatos permitidos: "
                + ", ".join(ext.upper() for ext in VIDEO_FILE_EXTENSIONS),
            ),
        ],
    ).bind(form=video_form, name="video_file")

    video_form._fields["video_file"] = file_field
    setattr(video_form, "video_file", file_field)


@app.route("/videos/gerenciar")
@admin_required
def videos_manage():
    """Display the management dashboard for the video library."""

    folder_form = VideoFolderForm()
    module_form = VideoModuleForm()
    video_form = VideoAssetForm()

    folder_choices, tag_choices, module_choices = _populate_video_forms(
        module_form=module_form, video_form=video_form
    )

    editing_folder = None
    editing_module = None
    editing_video = None

    folder_id = request.args.get("folder_id", type=int)
    if folder_id:
        editing_folder = VideoFolder.query.get_or_404(folder_id)
        folder_form.folder_id.data = str(editing_folder.id)
        folder_form.name.data = editing_folder.name
        folder_form.description.data = editing_folder.description

    module_id = request.args.get("module_id", type=int)
    if module_id:
        editing_module = VideoModule.query.get_or_404(module_id)
        module_form.module_id.data = str(editing_module.id)
        if editing_module.folder_id:
            module_form.folder_id.data = editing_module.folder_id
        module_form.title.data = editing_module.title
        module_form.description.data = editing_module.description
        module_form.tags.data = [tag.id for tag in editing_module.tags]

    video_id = request.args.get("video_id", type=int)
    if video_id:
        editing_video = VideoAsset.query.get_or_404(video_id)
        video_form.video_id.data = str(editing_video.id)
        if editing_video.module_id:
            video_form.module_id.data = editing_video.module_id
        video_form.title.data = editing_video.title
        video_form.description.data = editing_video.description
        video_form.duration_minutes.data = editing_video.duration_minutes

    folders = (
        VideoFolder.query.options(
            joinedload(VideoFolder.modules)
            .joinedload(VideoModule.videos),
            joinedload(VideoFolder.modules).joinedload(VideoModule.tags),
        )
        .order_by(VideoFolder.name.asc())
        .all()
    )

    return render_template(
        "videos_manage.html",
        folder_form=folder_form,
        module_form=module_form,
        video_form=video_form,
        editing_folder=editing_folder,
        editing_module=editing_module,
        editing_video=editing_video,
        folders=folders,
        tag_choices=tag_choices,
    )


def _report_form_errors(form):
    """Flash form validation messages in a consistent format."""

    for field_name, errors in form.errors.items():
        field = getattr(form, field_name)
        label = field.label.text if hasattr(field, "label") else field_name
        for error in errors:
            flash(f"{label}: {error}", "danger")


@app.route("/videos/pastas/salvar", methods=["POST"])
@admin_required
def save_video_folder():
    """Create or update a video folder."""

    form = VideoFolderForm()
    if form.validate_on_submit():
        folder_id_raw = (form.folder_id.data or "").strip()
        folder: VideoFolder | None = None
        if folder_id_raw:
            try:
                folder_id = int(folder_id_raw)
            except ValueError:
                folder_id = None
            if folder_id:
                folder = VideoFolder.query.get(folder_id)
                if folder is None:
                    flash("A pasta selecionada não foi encontrada.", "danger")
                    return redirect(url_for("videos_manage"))
        if folder is None:
            folder = VideoFolder()
            db.session.add(folder)

        folder.name = form.name.data.strip()
        folder.description = (form.description.data or "").strip() or None

        db.session.commit()
        flash("Pasta salva com sucesso!", "success")
        return redirect(url_for("videos_manage"))

    _report_form_errors(form)
    return redirect(url_for("videos_manage"))


@app.route("/videos/pastas/<int:folder_id>/excluir", methods=["POST"])
@admin_required
def delete_video_folder(folder_id: int):
    """Remove a video folder and its child modules."""

    folder = VideoFolder.query.get_or_404(folder_id)
    for module in list(folder.modules):
        for video in list(module.videos):
            _delete_static_file(video.file_path)
    db.session.delete(folder)
    db.session.commit()
    flash("Pasta removida com sucesso.", "success")
    return redirect(url_for("videos_manage"))


@app.route("/videos/modulos/salvar", methods=["POST"])
@admin_required
def save_video_module():
    """Create or update a video module."""

    form = VideoModuleForm()
    folder_choices, tag_choices, _ = _populate_video_forms(module_form=form)

    if not folder_choices:
        flash("Cadastre uma pasta antes de criar módulos.", "warning")
        return redirect(url_for("videos_manage"))

    if form.validate_on_submit():
        module_id_raw = (form.module_id.data or "").strip()
        module: VideoModule | None = None
        if module_id_raw:
            try:
                module_id = int(module_id_raw)
            except ValueError:
                module_id = None
            if module_id:
                module = VideoModule.query.get(module_id)
                if module is None:
                    flash("O módulo selecionado não foi encontrado.", "danger")
                    return redirect(url_for("videos_manage"))
        if module is None:
            module = VideoModule()
            db.session.add(module)

        module.title = form.title.data.strip()
        module.description = (form.description.data or "").strip() or None
        module.folder_id = form.folder_id.data

        selected_tags: list[Tag] = []
        if form.tags.data:
            selected_tags = (
                Tag.query.filter(Tag.id.in_(form.tags.data)).all()
            )

        module.tags = selected_tags
        db.session.commit()
        flash("Módulo salvo com sucesso!", "success")
        return redirect(url_for("videos_manage"))

    _report_form_errors(form)
    return redirect(url_for("videos_manage"))


@app.route("/videos/modulos/<int:module_id>/excluir", methods=["POST"])
@admin_required
def delete_video_module(module_id: int):
    """Delete a video module."""

    module = VideoModule.query.get_or_404(module_id)
    for video in list(module.videos):
        _delete_static_file(video.file_path)
    db.session.delete(module)
    db.session.commit()
    flash("Módulo removido com sucesso.", "success")
    return redirect(url_for("videos_manage"))


@app.route("/videos/conteudos/salvar", methods=["POST"])
@admin_required
def save_video_asset():
    """Create or update a long-form video entry."""

    form = VideoAssetForm()
    _ensure_video_upload_field(form)
    _, _, module_choices = _populate_video_forms(video_form=form)

    if not module_choices:
        flash("Cadastre um módulo antes de adicionar vídeos.", "warning")
        return redirect(url_for("videos_manage"))

    if form.validate_on_submit():
        video_id_raw = (form.video_id.data or "").strip()
        video: VideoAsset | None = None
        if video_id_raw:
            try:
                video_id = int(video_id_raw)
            except ValueError:
                video_id = None
            if video_id:
                video = VideoAsset.query.get(video_id)
                if video is None:
                    flash("O vídeo selecionado não foi encontrado.", "danger")
                    return redirect(url_for("videos_manage"))
        is_new = False
        if video is None:
            video = VideoAsset()
            is_new = True

        video.title = form.title.data.strip()
        video.description = (form.description.data or "").strip() or None
        video.module_id = form.module_id.data
        video.duration_minutes = form.duration_minutes.data

        file_storage = form.video_file.data
        filename = getattr(file_storage, "filename", "") if file_storage else ""
        if filename:
            saved_video = _save_uploaded_video(file_storage)
            if saved_video is None:
                db.session.rollback()
                flash(
                    "Não foi possível salvar o vídeo enviado. Verifique o formato e tente novamente.",
                    "danger",
                )
                return redirect(url_for("videos_manage"))
            _delete_static_file(video.file_path)
            video.file_path = saved_video["relative_path"]
            video.original_filename = saved_video["original_name"]
            video.mime_type = saved_video["mime_type"]
            video.file_size = saved_video["file_size"]
        elif not video.file_path:
            db.session.rollback()
            flash("Envie um arquivo de vídeo para salvar o conteúdo.", "danger")
            return redirect(url_for("videos_manage"))

        if is_new:
            db.session.add(video)

        db.session.commit()
        flash("Vídeo salvo com sucesso!", "success")
        return redirect(url_for("videos_manage"))

    _report_form_errors(form)
    return redirect(url_for("videos_manage"))


@app.route("/videos/conteudos/<int:video_id>/excluir", methods=["POST"])
@admin_required
def delete_video_asset(video_id: int):
    """Remove a stored video entry."""

    video = VideoAsset.query.get_or_404(video_id)
    _delete_static_file(video.file_path)
    db.session.delete(video)
    db.session.commit()
    flash("Vídeo removido com sucesso.", "success")
    return redirect(url_for("videos_manage"))


@app.route("/acessos")
@login_required
def acessos():
    """Display the hub with the available access categories and direct shortcuts."""

    categoria_counts = {
        slug: AccessLink.query.filter_by(category=slug).count()
        for slug in ACESSOS_CATEGORIES
    }
    return render_template(
        "acessos.html",
        categorias=ACESSOS_CATEGORIES,
        links=ACESSOS_DIRECT_LINKS,
        categoria_counts=categoria_counts,
    )


@app.route("/acessos/<categoria_slug>")
@login_required
def acessos_categoria(categoria_slug: str):
    """Show the shortcuts for a specific access category."""

    categoria = ACESSOS_CATEGORIES.get(categoria_slug.lower())
    if not categoria:
        abort(404)

    links = (
        AccessLink.query.filter_by(category=categoria_slug.lower())
        .order_by(AccessLink.created_at.desc())
        .all()
    )
    return render_template(
        "acessos_categoria.html",
        categoria=categoria,
        categoria_slug=categoria_slug.lower(),
        categorias=ACESSOS_CATEGORIES,
        links=links,
    )


@app.route("/acessos/<categoria_slug>/novo", methods=["GET", "POST"])
@login_required
def acessos_categoria_novo(categoria_slug: str):
    """Display and process the form to create a new shortcut within a category."""

    if current_user.role != "admin":
        abort(403)

    categoria = ACESSOS_CATEGORIES.get(categoria_slug.lower())
    if not categoria:
        abort(404)

    form = AccessLinkForm()
    if form.validate_on_submit():
        novo_link = AccessLink(
            category=categoria_slug.lower(),
            label=form.label.data.strip(),
            url=form.url.data.strip(),
            description=(form.description.data or "").strip() or None,
            created_by=current_user,
        )
        db.session.add(novo_link)
        db.session.commit()
        flash("Novo atalho criado com sucesso!", "success")
        return redirect(url_for("acessos_categoria", categoria_slug=categoria_slug.lower()))

    return render_template(
        "acessos_categoria_novo.html",
        categoria=categoria,
        categoria_slug=categoria_slug.lower(),
        form=form,
    )


@app.route("/ping")
@login_required
def ping():
    """Endpoint for client pings to keep the session active."""
    session.modified = True
    return ("", 204)


def _get_user_notification_items(limit: int | None = 20):
    """Return serialized notifications and unread totals for the current user."""

    notifications_query = (
        TaskNotification.query.filter(TaskNotification.user_id == current_user.id)
        .options(joinedload(TaskNotification.task).joinedload(Task.tag))
        .order_by(TaskNotification.created_at.desc())
    )
    if limit is not None:
        notifications_query = notifications_query.limit(limit)
    notifications = notifications_query.all()
    unread_total = TaskNotification.query.filter(
        TaskNotification.user_id == current_user.id,
        TaskNotification.read_at.is_(None),
    ).count()

    items = []
    for notification in notifications:
        task = notification.task
        task_url = None
        tag_name = None
        task_title = None
        if task:
            task_title = task.title
            tag_name = task.tag.nome if task.tag else None
            task_url = url_for("tasks_sector", tag_id=task.tag_id) + f"#task-{task.id}"
        message = notification.message
        if not message:
            if task_title and tag_name:
                message = f"Tarefa \"{task_title}\" atribuída no setor {tag_name}."
            elif task_title:
                message = f"Tarefa \"{task_title}\" atribuída a você."
            else:
                message = "Nova tarefa atribuída a você."

        created_at = notification.created_at
        if created_at.tzinfo is None:
            created_at_iso = created_at.isoformat() + "Z"
            display_dt = created_at.replace(tzinfo=timezone.utc).astimezone(SAO_PAULO_TZ)
        else:
            created_at_iso = created_at.isoformat()
            display_dt = created_at.astimezone(SAO_PAULO_TZ)

        items.append(
            {
                "id": notification.id,
                "message": message,
                "created_at": created_at_iso,
                "created_at_display": display_dt.strftime("%d/%m/%Y %H:%M"),
                "is_read": notification.is_read,
                "url": task_url,
            }
        )

    return items, unread_total


@app.route("/notifications", methods=["GET"])
@login_required
def list_notifications():
    """Return the most recent task notifications for the user."""

    items, unread_total = _get_user_notification_items(limit=20)
    return jsonify({"notifications": items, "unread": unread_total})


@app.route("/notificacoes")
@login_required
def notifications_center():
    """Render the notification center page."""

    items, unread_total = _get_user_notification_items(limit=50)
    return render_template(
        "notifications.html",
        notifications=items,
        unread_total=unread_total,
    )


@app.route("/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def mark_notification_read(notification_id):
    """Mark a single notification as read."""

    notification = TaskNotification.query.filter(
        TaskNotification.id == notification_id,
        TaskNotification.user_id == current_user.id,
    ).first_or_404()
    if not notification.read_at:
        notification.read_at = datetime.utcnow()
        db.session.commit()
    return jsonify({"success": True})


@app.route("/notifications/read-all", methods=["POST"])
@login_required
def mark_all_notifications_read():
    """Mark all unread notifications for the current user as read."""

    updated = (
        TaskNotification.query.filter(
            TaskNotification.user_id == current_user.id,
            TaskNotification.read_at.is_(None),
        ).update(
            {TaskNotification.read_at: datetime.utcnow()},
            synchronize_session=False,
        )
    )
    db.session.commit()
    return jsonify({"success": True, "updated": updated or 0})


@app.route("/consultorias")
@login_required
def consultorias():
    """List registered consultorias."""
    consultorias = Consultoria.query.all()
    return render_template("consultorias.html", consultorias=consultorias)


@app.route("/sala-reunioes", methods=["GET", "POST"])
@login_required
def sala_reunioes():
    """List and create meetings using Google Calendar."""
    form = MeetingForm()
    populate_participants_choices(form)
    show_modal = False
    raw_events = fetch_raw_events()
    calendar_tz = get_calendar_timezone()
    now = datetime.now(calendar_tz)
    if form.validate_on_submit():
        if form.meeting_id.data:
            meeting = Reuniao.query.get(int(form.meeting_id.data))
            if meeting and meeting.criador_id == current_user.id:
                if meeting.status != ReuniaoStatus.AGENDADA:
                    flash(
                        "Reuniões em andamento ou realizadas não podem ser editadas.",
                        "danger",
                    )
                    return redirect(url_for("sala_reunioes"))
                success, meet_link = update_meeting(form, raw_events, now, meeting)
                if success:
                    if meet_link:
                        session["meet_link"] = meet_link
                    return redirect(url_for("sala_reunioes"))
                show_modal = True
            else:
                flash(
                    "Você só pode editar reuniões que você criou.",
                    "danger",
                )
        else:
            success, meet_link = create_meeting_and_event(
                form, raw_events, now, current_user.id
            )
            if success:
                if meet_link:
                    session["meet_link"] = meet_link
                return redirect(url_for("sala_reunioes"))
            show_modal = True
    if request.method == "POST":
        show_modal = True
    meet_popup_link = session.pop("meet_link", None)
    return render_template(
        "sala_reunioes.html",
        form=form,
        show_modal=show_modal,
        calendar_timezone=calendar_tz.key,
        meet_popup_link=meet_popup_link,
    )


@app.route("/reuniao/<int:meeting_id>/delete", methods=["POST"])
@login_required
def delete_reuniao(meeting_id):
    """Delete a meeting and its corresponding Google Calendar event."""
    meeting = Reuniao.query.get_or_404(meeting_id)
    if current_user.role != "admin":
        if meeting.criador_id != current_user.id:
            flash("Você só pode excluir reuniões que você criou.", "danger")
            return redirect(url_for("sala_reunioes"))
        if meeting.status != ReuniaoStatus.AGENDADA:
            flash(
                "Reuniões em andamento ou realizadas não podem ser excluídas.",
                "danger",
            )
            return redirect(url_for("sala_reunioes"))
    if delete_meeting(meeting):
        flash("Reunião excluída com sucesso!", "success")
    else:
        flash("Não foi possível remover o evento do Google Calendar.", "danger")
    return redirect(url_for("sala_reunioes"))


@app.route("/consultorias/cadastro", methods=["GET", "POST"])
@admin_required
def cadastro_consultoria():
    """Render and handle the Cadastro de Consultoria page."""
    form = ConsultoriaForm()
    if form.validate_on_submit():
        consultoria = Consultoria(
            nome=form.nome.data,
            usuario=form.usuario.data,
            senha=form.senha.data,
        )
        db.session.add(consultoria)
        db.session.commit()
        flash("Consultoria registrada com sucesso.", "success")
        return redirect(url_for("consultorias"))
    return render_template("cadastro_consultoria.html", form=form)


@app.route("/consultorias/editar/<int:id>", methods=["GET", "POST"])
@admin_required
def editar_consultoria_cadastro(id):
    """Edit an existing consultoria entry."""
    consultoria = Consultoria.query.get_or_404(id)
    form = ConsultoriaForm(obj=consultoria)
    if form.validate_on_submit():
        consultoria.nome = form.nome.data
        consultoria.usuario = form.usuario.data
        consultoria.senha = form.senha.data
        db.session.commit()
        flash("Consultoria atualizada com sucesso.", "success")
        return redirect(url_for("consultorias"))
    return render_template(
        "cadastro_consultoria.html", form=form, consultoria=consultoria
    )


@app.route("/consultorias/setores")
@login_required
def setores():
    """List registered setores."""
    setores = Setor.query.all()
    return render_template("setores.html", setores=setores)


@app.route("/consultorias/setores/cadastro", methods=["GET", "POST"])
@admin_required
def cadastro_setor():
    """Render and handle the Cadastro de Setor page."""
    form = SetorForm()
    if form.validate_on_submit():
        setor = Setor(nome=form.nome.data)
        db.session.add(setor)
        db.session.commit()
        flash("Setor registrado com sucesso.", "success")
        return redirect(url_for("setores"))
    return render_template("cadastro_setor.html", form=form)


@app.route("/consultorias/setores/editar/<int:id>", methods=["GET", "POST"])
@admin_required
def editar_setor(id):
    """Edit a registered setor."""
    setor = Setor.query.get_or_404(id)
    form = SetorForm(obj=setor)
    if form.validate_on_submit():
        setor.nome = form.nome.data
        db.session.commit()
        flash("Setor atualizado com sucesso.", "success")
        return redirect(url_for("setores"))
    return render_template("cadastro_setor.html", form=form, setor=setor)


@app.route("/tags")
@login_required
def tags():
    """List registered tags."""
    tags = Tag.query.all()
    return render_template("tags.html", tags=tags)


@app.route("/tags/cadastro", methods=["GET", "POST"])
@admin_required
def cadastro_tag():
    """Render and handle the Cadastro de Tag page."""
    form = TagForm()
    if form.validate_on_submit():
        tag = Tag(nome=form.nome.data)
        db.session.add(tag)
        db.session.commit()
        flash("Tag registrada com sucesso.", "success")
        return redirect(url_for("tags"))
    return render_template("cadastro_tag.html", form=form)


@app.route("/tags/editar/<int:id>", methods=["GET", "POST"])
@admin_required
def editar_tag(id):
    """Edit a registered tag."""
    tag = Tag.query.get_or_404(id)
    form = TagForm(obj=tag)
    if form.validate_on_submit():
        tag.nome = form.nome.data
        db.session.commit()
        flash("Tag atualizada com sucesso.", "success")
        return redirect(url_for("tags"))
    return render_template("cadastro_tag.html", form=form, tag=tag)


@app.route("/consultorias/relatorios")
@admin_required
def relatorios_consultorias():
    """Display reports of inclusões grouped by consultoria, user, and date."""
    inicio_raw = request.args.get("inicio")
    fim_raw = request.args.get("fim")
    query = Inclusao.query

    inicio = None
    if inicio_raw:
        try:
            inicio = datetime.strptime(inicio_raw, "%Y-%m-%d").date()
            query = query.filter(Inclusao.data >= inicio)
        except ValueError:
            inicio = None

    fim = None
    if fim_raw:
        try:
            fim = datetime.strptime(fim_raw, "%Y-%m-%d").date()
            query = query.filter(Inclusao.data <= fim)
        except ValueError:
            fim = None

    por_consultoria = (
        query.with_entities(Inclusao.consultoria, db.func.count(Inclusao.id))
        .group_by(Inclusao.consultoria)
        .order_by(db.func.count(Inclusao.id).desc())
        .all()
    )

    por_usuario = (
        query.with_entities(Inclusao.usuario, db.func.count(Inclusao.id))
        .group_by(Inclusao.usuario)
        .order_by(db.func.count(Inclusao.id).desc())
        .all()
    )

    labels_consultoria = [c or "—" for c, _ in por_consultoria]
    counts_consultoria = [total for _, total in por_consultoria]
    fig_cons = go.Figure(
        data=[
            go.Bar(
                x=labels_consultoria,
                y=counts_consultoria,
                marker_color=qualitative.Pastel,
            )
        ]
    )
    fig_cons.update_layout(
        title_text="Inclusões por consultoria",
        template="seaborn",
        xaxis_title="Consultoria",
        yaxis_title="Total",
    )
    chart_consultoria = fig_cons.to_html(full_html=False, div_id="consultoria-chart")

    labels_usuario = [u or "—" for u, _ in por_usuario]
    counts_usuario = [total for _, total in por_usuario]
    fig_user = go.Figure(
        data=[
            go.Bar(x=labels_usuario, y=counts_usuario, marker_color=qualitative.Pastel)
        ]
    )
    fig_user.update_layout(
        title_text="Inclusões por usuário",
        template="seaborn",
        xaxis_title="Usuário",
        yaxis_title="Total",
    )
    chart_usuario = fig_user.to_html(full_html=False, div_id="usuario-chart")

    inclusoes = query.all()
    inclusoes_por_consultoria = {}
    inclusoes_por_usuario = {}
    for inc in inclusoes:
        label_cons = inc.consultoria or "—"
        inclusoes_por_consultoria.setdefault(label_cons, []).append(
            {
                "usuario": inc.usuario,
                "pergunta": inc.pergunta,
                "data": inc.data.strftime("%d/%m/%Y") if inc.data else "",
            }
        )
        label_user = inc.usuario or "—"
        inclusoes_por_usuario.setdefault(label_user, []).append(
            {
                "consultoria": inc.consultoria,
                "pergunta": inc.pergunta,
                "data": inc.data.strftime("%d/%m/%Y") if inc.data else "",
            }
        )

    por_data = []
    if inicio or fim:
        por_data = (
            query.filter(Inclusao.data.isnot(None))
            .with_entities(Inclusao.data, db.func.count(Inclusao.id))
            .group_by(Inclusao.data)
            .order_by(Inclusao.data)
            .all()
        )

    return render_template(
        "relatorios_consultorias.html",
        chart_consultoria=chart_consultoria,
        chart_usuario=chart_usuario,
        inclusoes_por_consultoria=inclusoes_por_consultoria,
        inclusoes_por_usuario=inclusoes_por_usuario,
        por_data=por_data,
        inicio=inicio.strftime("%Y-%m-%d") if inicio else "",
        fim=fim.strftime("%Y-%m-%d") if fim else "",
    )


@app.route("/consultorias/inclusoes")
@login_required
def inclusoes():
    """List and search Consultorias."""
    search_raw = request.args.get("q", "")
    page = request.args.get("page", 1, type=int)
    query = Inclusao.query

    if search_raw:
        like = f"%{search_raw}%"
        query = query.filter(
            or_(
                cast(Inclusao.data, String).ilike(like),
                Inclusao.usuario.ilike(like),
                Inclusao.assunto.ilike(like),
            )
        )

    pagination = query.order_by(Inclusao.data.desc()).paginate(page=page, per_page=50)

    return render_template(
        "inclusoes.html",
        inclusoes=pagination.items,
        pagination=pagination,
        search=search_raw,
    )


@app.route("/consultorias/inclusoes/nova", methods=["GET", "POST"])
@login_required
def nova_inclusao():
    """Render and handle Consultoria form."""
    users = User.query.order_by(User.name).all()
    if request.method == "POST":
        user_id = request.form.get("usuario")
        user = User.query.get(int(user_id)) if user_id else None
        data_str = request.form.get("data")
        data = datetime.strptime(data_str, "%Y-%m-%d").date() if data_str else None
        inclusao = Inclusao(
            data=data,
            usuario=user.name if user else "",
            setor=request.form.get("setor"),
            consultoria=request.form.get("consultoria"),
            assunto=(request.form.get("assunto") or "").upper(),
            pergunta=sanitize_html(request.form.get("pergunta")),
            resposta=sanitize_html(request.form.get("resposta")),
        )
        db.session.add(inclusao)
        db.session.commit()
        flash("Consultoria registrada com sucesso.", "success")
        return redirect(url_for("inclusoes"))
    return render_template(
        "nova_inclusao.html",
        users=users,
        setores=Setor.query.order_by(Setor.nome).all(),
        consultorias=Consultoria.query.order_by(Consultoria.nome).all(),
    )


@app.route("/consultorias/inclusoes/<int:codigo>")
@login_required
def visualizar_consultoria(codigo):
    """Display details for a single consultoria."""
    inclusao = Inclusao.query.get_or_404(codigo)
    return render_template(
        "visualizar_consultoria.html",
        inclusao=inclusao,
        data_formatada=inclusao.data_formatada,
    )


@app.route("/consultorias/inclusoes/<int:codigo>/editar", methods=["GET", "POST"])
@login_required
def editar_consultoria(codigo):
    """Render and handle editing of a consultoria."""
    inclusao = Inclusao.query.get_or_404(codigo)
    users = User.query.order_by(User.name).all()
    if request.method == "POST":
        user_id = request.form.get("usuario")
        user = User.query.get(int(user_id)) if user_id else None
        data_str = request.form.get("data")
        inclusao.data = (
            datetime.strptime(data_str, "%Y-%m-%d").date() if data_str else None
        )
        inclusao.usuario = user.name if user else ""
        inclusao.setor = request.form.get("setor")
        inclusao.consultoria = request.form.get("consultoria")
        inclusao.assunto = (request.form.get("assunto") or "").upper()
        inclusao.pergunta = sanitize_html(request.form.get("pergunta"))
        inclusao.resposta = sanitize_html(request.form.get("resposta"))
        db.session.commit()
        flash("Consultoria atualizada com sucesso.", "success")
        return redirect(url_for("inclusoes"))
    return render_template(
        "nova_inclusao.html",
        users=users,
        setores=Setor.query.order_by(Setor.nome).all(),
        consultorias=Consultoria.query.order_by(Consultoria.nome).all(),
        inclusao=inclusao,
    )


@app.route("/cookies")
def cookies():
    """Render the cookie policy page."""
    return render_template("cookie_policy.html")


@app.route("/cookies/revoke")
def revoke_cookies():
    """Revoke cookie consent and redirect to index."""
    resp = redirect(url_for("index"))
    resp.delete_cookie("cookie_consent")
    flash("Consentimento de cookies revogado.", "info")
    return resp


@app.route("/login/google")
def google_login():
    """Start OAuth login with Google."""
    flow = build_google_flow()
    flow.redirect_uri = get_google_redirect_uri()
    authorization_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    session["oauth_state"] = state
    return redirect(authorization_url)


@app.route("/oauth2callback")
def google_callback():
    """Handle OAuth callback from Google."""
    state = session.get("oauth_state")
    session.pop("oauth_state", None)
    if state is None or state != request.args.get("state"):
        flash("Falha ao validar resposta do Google. Tente novamente.", "danger")
        return redirect(url_for("login"))
    flow = build_google_flow(state=state)
    flow.redirect_uri = get_google_redirect_uri()
    try:
        flow.fetch_token(authorization_response=request.url)
    except Exception:
        flash("Não foi possível completar a autenticação com o Google.", "danger")
        return redirect(url_for("login"))
    credentials = flow.credentials
    request_session = requests.Session()
    token_request = Request(session=request_session)
    id_info = id_token.verify_oauth2_token(
        credentials.id_token, token_request, current_app.config["GOOGLE_CLIENT_ID"]
    )
    google_id = id_info.get("sub")
    email = id_info.get("email")
    name = id_info.get("name", email)
    user = User.query.filter(
        (User.google_id == google_id) | (User.email == email)
    ).first()
    if not user:
        base_username = email.split("@")[0]
        username = base_username
        counter = 1
        while User.query.filter_by(username=username).first():
            username = f"{base_username}{counter}"
            counter += 1
        user = User(username=username, email=email, name=name, google_id=google_id)
        random_password = secrets.token_hex(16)
        user.set_password(random_password)
        db.session.add(user)
        db.session.commit()
    if credentials.refresh_token:
        user.google_refresh_token = credentials.refresh_token
        db.session.commit()
    login_user(user, remember=True, duration=timedelta(days=30))
    session.permanent = True
    sid = uuid4().hex
    session["sid"] = sid
    session["credentials"] = credentials_to_dict(credentials)
    db.session.add(
        Session(
            session_id=sid,
            user_id=user.id,
            session_data=dict(session),
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
            last_activity=datetime.now(SAO_PAULO_TZ),
        )
    )
    db.session.commit()
    flash("Login com Google bem-sucedido!", "success")
    return redirect(url_for("home"))


@app.route("/login", methods=["GET", "POST"])
def login():
    """Render the login page and handle authentication."""
    form = LoginForm()
    google_enabled = bool(
        current_app.config.get("GOOGLE_CLIENT_ID")
        and current_app.config.get("GOOGLE_CLIENT_SECRET")
    )
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data):
            if not user.ativo:
                flash("Seu usuário está inativo. Contate o administrador.", "danger")
                return redirect(url_for("login"))
            login_user(
                user,
                remember=form.remember_me.data,
                duration=timedelta(days=30),
            )
            session.permanent = form.remember_me.data
            sid = uuid4().hex
            session["sid"] = sid
            db.session.add(
                Session(
                    session_id=sid,
                    user_id=user.id,
                    session_data=dict(session),
                    ip_address=request.remote_addr,
                    user_agent=request.headers.get("User-Agent"),
                    last_activity=datetime.now(SAO_PAULO_TZ),
                )
            )
            db.session.commit()
            flash("Login bem-sucedido!", "success")
            if user.role == "admin":
                return redirect(url_for("tasks_overview"))
            first_tag = user.tags[0] if user.tags else None
            if first_tag:
                return redirect(url_for("tasks_sector", tag_id=first_tag.id))
            return redirect(url_for("home"))
        else:
            flash("Credenciais inválidas", "danger")
    return render_template("login.html", form=form, google_enabled=google_enabled)


@app.route("/dashboard")
@login_required
def dashboard():
    """Admin dashboard placeholder page."""
    return render_template("dashboard.html")


@app.route("/api/cnpj/<cnpj>")
@login_required
def api_cnpj(cnpj):
    """Provide a JSON API for CNPJ lookups."""
    try:
        dados = consultar_cnpj(cnpj)
    except ValueError as e:
        msg = str(e)
        status = 400 if "inválido" in msg.lower() or "invalido" in msg.lower() else 404
        if status == 404:
            msg = "CNPJ não está cadastrado"
        return jsonify({"error": msg}), status
    except Exception:
        return jsonify({"error": "Erro ao consultar CNPJ"}), 500
    if not dados:
        return jsonify({"error": "CNPJ não está cadastrado"}), 404
    return jsonify(dados)


@app.route("/api/reunioes")
@login_required
@csrf.exempt
def api_reunioes():
    """Return meetings with up-to-date status as JSON."""
    raw_events = fetch_raw_events()
    calendar_tz = get_calendar_timezone()
    now = datetime.now(calendar_tz)
    events = combine_events(
        raw_events, now, current_user.id, current_user.role == "admin"
    )
    return jsonify(events)


@app.route("/cadastrar_empresa", methods=["GET", "POST"])
@login_required
def cadastrar_empresa():
    """Create a new company record."""
    form = EmpresaForm()
    if request.method == "GET":
        form.sistemas_consultorias.data = form.sistemas_consultorias.data or []
        form.regime_lancamento.data = form.regime_lancamento.data or []
    if form.validate_on_submit():
        try:
            cnpj_limpo = re.sub(r"\D", "", form.cnpj.data)
            acessos_json = form.acessos_json.data or "[]"
            try:
                acessos = json.loads(acessos_json) if acessos_json else []
            except Exception:
                acessos = []
            nova_empresa = Empresa(
                codigo_empresa=form.codigo_empresa.data,
                nome_empresa=form.nome_empresa.data,
                cnpj=cnpj_limpo,
                data_abertura=form.data_abertura.data,
                socio_administrador=form.socio_administrador.data,
                tributacao=form.tributacao.data,
                regime_lancamento=form.regime_lancamento.data,
                atividade_principal=form.atividade_principal.data,
                sistemas_consultorias=form.sistemas_consultorias.data,
                sistema_utilizado=form.sistema_utilizado.data,
                acessos=acessos,
            )
            db.session.add(nova_empresa)
            db.session.commit()
            flash("Empresa cadastrada com sucesso!", "success")
            return redirect(
                url_for("gerenciar_departamentos", empresa_id=nova_empresa.id)
            )
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao cadastrar empresa: {e}", "danger")
    else:
        print("Formulário não validado:")
        print(form.errors)

    return render_template("empresas/cadastrar.html", form=form)


@app.route("/listar_empresas")
@login_required
def listar_empresas():
    """List companies with optional search and pagination."""
    search = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = 20
    query = Empresa.query

    if search:
        like_pattern = f"%{search}%"
        query = query.filter(
            or_(
                Empresa.nome_empresa.ilike(like_pattern),
                Empresa.codigo_empresa.ilike(like_pattern),
            )
        )

    sort = request.args.get("sort", "nome")
    order = request.args.get("order", "asc")

    if sort == "codigo":
        order_column = Empresa.codigo_empresa
    else:
        order_column = Empresa.nome_empresa

    if order == "desc":
        query = query.order_by(order_column.desc())
    else:
        query = query.order_by(order_column.asc())

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    empresas = pagination.items

    return render_template(
        "empresas/listar.html",
        empresas=empresas,
        pagination=pagination,
        search=search,
        sort=sort,
        order=order,
    )


@app.route("/empresa/editar/<int:id>", methods=["GET", "POST"])
@login_required
def editar_empresa(id):
    """Edit an existing company and its details."""
    empresa = Empresa.query.get_or_404(id)
    empresa_form = EmpresaForm(request.form, obj=empresa)

    if request.method == "GET":
        empresa_form.sistemas_consultorias.data = empresa.sistemas_consultorias or []
        empresa_form.regime_lancamento.data = empresa.regime_lancamento or []
        empresa_form.acessos_json.data = json.dumps(empresa.acessos or [])

    if request.method == "POST":
        if empresa_form.validate():
            empresa_form.populate_obj(empresa)
            empresa.cnpj = re.sub(r"\D", "", empresa_form.cnpj.data)
            empresa.sistemas_consultorias = empresa_form.sistemas_consultorias.data
            try:
                empresa.acessos = json.loads(empresa_form.acessos_json.data or "[]")
            except Exception:
                empresa.acessos = []
            db.session.add(empresa)
            try:
                db.session.commit()
                flash("Dados da Empresa salvos com sucesso!", "success")
                return redirect(url_for("visualizar_empresa", id=id) + "#dados-empresa")
            except Exception as e:
                db.session.rollback()
                flash(f"Erro ao salvar: {str(e)}", "danger")
        else:
            for field, errors in empresa_form.errors.items():
                for error in errors:
                    flash(f"Erro: {error}", "danger")

    return render_template(
        "empresas/editar_empresa.html",
        empresa=empresa,
        empresa_form=empresa_form,
    )


@app.route("/empresa/visualizar/<int:id>")
@login_required
def visualizar_empresa(id):
    """Display a detailed view of a company."""
    from types import SimpleNamespace

    empresa = Empresa.query.get_or_404(id)

    # display para regime de lançamento
    empresa.regime_lancamento_display = empresa.regime_lancamento or []

    can_access_financeiro = user_has_tag("financeiro")

    fiscal = Departamento.query.filter_by(
        empresa_id=id, tipo="Departamento Fiscal"
    ).first()
    contabil = Departamento.query.filter_by(
        empresa_id=id, tipo="Departamento Contábil"
    ).first()
    pessoal = Departamento.query.filter_by(
        empresa_id=id, tipo="Departamento Pessoal"
    ).first()
    administrativo = Departamento.query.filter_by(
        empresa_id=id, tipo="Departamento Administrativo"
    ).first()
    financeiro = (
        Departamento.query.filter_by(
            empresa_id=id, tipo="Departamento Financeiro"
        ).first()
        if can_access_financeiro
        else None
    )

    def _prepare_envio_fisico(departamento):
        if not departamento:
            return []
        try:
            lista = (
                json.loads(departamento.envio_fisico)
                if isinstance(departamento.envio_fisico, str)
                else (departamento.envio_fisico or [])
            )
        except Exception:
            lista = []
        if "malote" in lista and getattr(departamento, "malote_coleta", None):
            lista = [
                "Malote - " + departamento.malote_coleta if item == "malote" else item
                for item in lista
            ]
        return lista

    # monta contatos_list
    if fiscal and getattr(fiscal, "contatos", None):
        try:
            contatos_list = (
                json.loads(fiscal.contatos)
                if isinstance(fiscal.contatos, str)
                else fiscal.contatos
            )
        except Exception:
            contatos_list = []
    else:
        contatos_list = []
    contatos_list = normalize_contatos(contatos_list)

    # fiscal_view: garante objeto mesmo quando fiscal é None
    if fiscal is None:
        fiscal_view = SimpleNamespace(
            formas_importacao=[], contatos_list=contatos_list, envio_fisico=[]
        )
    else:
        fiscal_view = fiscal
        # normaliza formas_importacao
        formas = getattr(fiscal_view, "formas_importacao", None)
        if isinstance(formas, str):
            try:
                fiscal_view.formas_importacao = json.loads(formas)
            except Exception:
                fiscal_view.formas_importacao = []
        elif not formas:
            fiscal_view.formas_importacao = []
        # injeta listas sem risco
        setattr(fiscal_view, "contatos_list", contatos_list)
        setattr(fiscal_view, "envio_fisico", _prepare_envio_fisico(fiscal_view))

    if contabil:
        contabil.envio_fisico = _prepare_envio_fisico(contabil)
    if pessoal:
        pessoal.envio_fisico = _prepare_envio_fisico(pessoal)
    if administrativo:
        administrativo.envio_fisico = _prepare_envio_fisico(administrativo)
    if financeiro:
        financeiro.envio_fisico = _prepare_envio_fisico(financeiro)

    return render_template(
        "empresas/visualizar.html",
        empresa=empresa,
        fiscal=fiscal_view,
        contabil=contabil,
        pessoal=pessoal,
        administrativo=administrativo,
        financeiro=financeiro,
        can_access_financeiro=can_access_financeiro,
    )

    ## Rota para gerenciar departamentos de uma empresa


@app.route("/empresa/<int:empresa_id>/departamentos", methods=["GET", "POST"])
@login_required
def gerenciar_departamentos(empresa_id):
    """Create or update department data for a company."""
    empresa = Empresa.query.get_or_404(empresa_id)

    can_access_financeiro = user_has_tag("financeiro")

    fiscal = Departamento.query.filter_by(
        empresa_id=empresa_id, tipo="Departamento Fiscal"
    ).first()
    contabil = Departamento.query.filter_by(
        empresa_id=empresa_id, tipo="Departamento Contábil"
    ).first()
    pessoal = Departamento.query.filter_by(
        empresa_id=empresa_id, tipo="Departamento Pessoal"
    ).first()
    administrativo = Departamento.query.filter_by(
        empresa_id=empresa_id, tipo="Departamento Administrativo"
    ).first()
    financeiro = (
        Departamento.query.filter_by(
            empresa_id=empresa_id, tipo="Departamento Financeiro"
        ).first()
        if can_access_financeiro
        else None
    )

    fiscal_form = DepartamentoFiscalForm(request.form, obj=fiscal)
    contabil_form = DepartamentoContabilForm(request.form, obj=contabil)
    pessoal_form = DepartamentoPessoalForm(request.form, obj=pessoal)
    administrativo_form = DepartamentoAdministrativoForm(
        request.form, obj=administrativo
    )
    financeiro_form = (
        DepartamentoFinanceiroForm(request.form, obj=financeiro)
        if can_access_financeiro
        else None
    )

    if request.method == "GET":
        fiscal_form = DepartamentoFiscalForm(obj=fiscal)
        if fiscal:
            fiscal_form.envio_digital.data = (
                fiscal.envio_digital
                if isinstance(fiscal.envio_digital, list)
                else json.loads(fiscal.envio_digital) if fiscal.envio_digital else []
            )
            fiscal_form.envio_fisico.data = (
                fiscal.envio_fisico
                if isinstance(fiscal.envio_fisico, list)
                else json.loads(fiscal.envio_fisico) if fiscal.envio_fisico else []
            )

            if fiscal.contatos:
                try:
                    contatos_list = (
                        json.loads(fiscal.contatos)
                        if isinstance(fiscal.contatos, str)
                        else fiscal.contatos
                    )
                except Exception:
                    contatos_list = []
            else:
                contatos_list = []
            contatos_list = normalize_contatos(contatos_list)
            fiscal_form.contatos_json.data = json.dumps(contatos_list)

        contabil_form = DepartamentoContabilForm(obj=contabil)
        if contabil:
            contabil_form.metodo_importacao.data = (
                contabil.metodo_importacao
                if isinstance(contabil.metodo_importacao, list)
                else (
                    json.loads(contabil.metodo_importacao)
                    if contabil.metodo_importacao
                    else []
                )
            )
            contabil_form.envio_digital.data = (
                contabil.envio_digital
                if isinstance(contabil.envio_digital, list)
                else (
                    json.loads(contabil.envio_digital) if contabil.envio_digital else []
                )
            )
            contabil_form.envio_fisico.data = (
                contabil.envio_fisico
                if isinstance(contabil.envio_fisico, list)
                else json.loads(contabil.envio_fisico) if contabil.envio_fisico else []
            )
            contabil_form.controle_relatorios.data = (
                contabil.controle_relatorios
                if isinstance(contabil.controle_relatorios, list)
                else (
                    json.loads(contabil.controle_relatorios)
                    if contabil.controle_relatorios
                    else []
                )
            )

    form_type = request.form.get("form_type")

    if request.method == "POST":
        form_processed_successfully = False

        if form_type == "fiscal" and fiscal_form.validate():
            if not fiscal:
                fiscal = Departamento(empresa_id=empresa_id, tipo="Departamento Fiscal")
                db.session.add(fiscal)

            fiscal_form.populate_obj(fiscal)
            if "malote" not in (fiscal_form.envio_fisico.data or []):
                fiscal.malote_coleta = None
            else:
                fiscal.malote_coleta = fiscal_form.malote_coleta.data
            try:
                fiscal.contatos = json.loads(fiscal_form.contatos_json.data or "[]")
            except Exception:
                fiscal.contatos = []
            flash("Departamento Fiscal salvo com sucesso!", "success")
            form_processed_successfully = True

        elif form_type == "contabil" and contabil_form.validate():
            if not contabil:
                contabil = Departamento(
                    empresa_id=empresa_id, tipo="Departamento Contábil"
                )
                db.session.add(contabil)

            contabil_form.populate_obj(contabil)
            if "malote" not in (contabil_form.envio_fisico.data or []):
                contabil.malote_coleta = None
            else:
                contabil.malote_coleta = contabil_form.malote_coleta.data

            contabil.metodo_importacao = contabil_form.metodo_importacao.data or []
            contabil.envio_digital = contabil_form.envio_digital.data or []
            contabil.envio_fisico = contabil_form.envio_fisico.data or []
            contabil.controle_relatorios = contabil_form.controle_relatorios.data or []

            flash("Departamento Contábil salvo com sucesso!", "success")
            form_processed_successfully = True

        elif form_type == "pessoal" and pessoal_form.validate():
            if not pessoal:
                pessoal = Departamento(
                    empresa_id=empresa_id, tipo="Departamento Pessoal"
                )
                db.session.add(pessoal)

            pessoal_form.populate_obj(pessoal)
            flash("Departamento Pessoal salvo com sucesso!", "success")
            form_processed_successfully = True

        elif form_type == "administrativo" and administrativo_form.validate():
            if not administrativo:
                administrativo = Departamento(
                    empresa_id=empresa_id, tipo="Departamento Administrativo"
                )
                db.session.add(administrativo)

            administrativo_form.populate_obj(administrativo)
            flash("Departamento Administrativo salvo com sucesso!", "success")
            form_processed_successfully = True
        elif form_type == "financeiro":
            if not can_access_financeiro:
                abort(403)
            if financeiro_form and financeiro_form.validate():
                if not financeiro:
                    financeiro = Departamento(
                        empresa_id=empresa_id, tipo="Departamento Financeiro"
                    )
                    db.session.add(financeiro)

                financeiro_form.populate_obj(financeiro)
                flash("Departamento Financeiro salvo com sucesso!", "success")
                form_processed_successfully = True

        if form_processed_successfully:
            try:
                db.session.commit()

                hash_ancoras = {
                    "fiscal": "fiscal",
                    "contabil": "contabil",
                    "pessoal": "pessoal",
                    "administrativo": "administrativo",
                    "financeiro": "financeiro",
                }
                hash_ancora = hash_ancoras.get(form_type, "")

                return redirect(
                    url_for("visualizar_empresa", id=empresa_id) + f"#{hash_ancora}"
                )

            except Exception as e:
                db.session.rollback()
                flash(f"Ocorreu um erro ao salvar: {str(e)}", "danger")

        else:
            active_form = {
                "fiscal": fiscal_form,
                "contabil": contabil_form,
                "pessoal": pessoal_form,
                "administrativo": administrativo_form,
                "financeiro": financeiro_form,
            }.get(form_type)
            if active_form and active_form.errors:
                for field, errors in active_form.errors.items():
                    for error in errors:
                        flash(
                            f"Erro no formulário {form_type.capitalize()}: {error}",
                            "danger",
                        )

    return render_template(
        "empresas/departamentos.html",
        empresa=empresa,
        fiscal_form=fiscal_form,
        contabil_form=contabil_form,
        pessoal_form=pessoal_form,
        administrativo_form=administrativo_form,
        financeiro_form=financeiro_form,
        fiscal=fiscal,
        contabil=contabil,
        pessoal=pessoal,
        administrativo=administrativo,
        financeiro=financeiro,
        can_access_financeiro=can_access_financeiro,
    )


@app.route("/relatorios")
@admin_required
def relatorios():
    """Render the reports landing page."""
    return render_template("admin/relatorios.html")


@app.route("/relatorio_empresas")
@admin_required
def relatorio_empresas():
    """Display aggregated company statistics."""
    empresas = Empresa.query.with_entities(
        Empresa.nome_empresa,
        Empresa.cnpj,
        Empresa.codigo_empresa,
        Empresa.tributacao,
        Empresa.sistema_utilizado,
    ).all()

    categorias = ["Simples Nacional", "Lucro Presumido", "Lucro Real"]
    grouped = {cat: [] for cat in categorias}
    grouped_sistemas = {}

    for nome, cnpj, codigo, trib, sistema in empresas:
        label = trib if trib in categorias else "Outros"
        grouped.setdefault(label, []).append(
            {"nome": nome, "cnpj": cnpj, "codigo": codigo}
        )

        sistema_label = sistema.strip() if sistema else "Não informado"
        grouped_sistemas.setdefault(sistema_label, []).append(
            {"nome": nome, "cnpj": cnpj, "codigo": codigo}
        )

    labels = list(grouped.keys())
    counts = [len(grouped[l]) for l in labels]
    fig = go.Figure(data=[go.Bar(x=labels, y=counts, marker_color=qualitative.Pastel)])
    fig.update_layout(
        title_text="Empresas por regime de tributação",
        template="seaborn",
        xaxis_title="Regime",
        yaxis_title="Quantidade",
    )
    chart_div = fig.to_html(full_html=False, div_id="empresa-tributacao-chart")

    sistema_labels = list(grouped_sistemas.keys())
    sistema_counts = [len(grouped_sistemas[l]) for l in sistema_labels]
    fig_sistemas = go.Figure(
        data=[
            go.Bar(x=sistema_labels, y=sistema_counts, marker_color=qualitative.Pastel)
        ]
    )
    fig_sistemas.update_layout(
        title_text="Empresas por sistema utilizado",
        template="seaborn",
        xaxis_title="Sistema",
        yaxis_title="Quantidade",
    )
    chart_div_sistema = fig_sistemas.to_html(
        full_html=False, div_id="empresa-sistema-chart"
    )

    return render_template(
        "admin/relatorio_empresas.html",
        chart_div=chart_div,
        empresas_por_slice=grouped,
        chart_div_sistema=chart_div_sistema,
        empresas_por_sistema=grouped_sistemas,
    )


@app.route("/relatorio_fiscal")
@admin_required
def relatorio_fiscal():
    """Show summary charts for the fiscal department."""
    departamentos = (
        Departamento.query.filter_by(tipo="Departamento Fiscal")
        .join(Empresa)
        .with_entities(
            Empresa.nome_empresa,
            Empresa.codigo_empresa,
            Departamento.formas_importacao,
            Departamento.forma_movimento,
            Departamento.malote_coleta,
        )
        .all()
    )
    fiscal_form = DepartamentoFiscalForm()
    choice_map = dict(fiscal_form.formas_importacao.choices)
    import_grouped = {}
    envio_grouped = {}
    malote_grouped = {}
    for nome, codigo, formas, envio, malote in departamentos:
        formas_list = json.loads(formas) if isinstance(formas, str) else (formas or [])
        for f in formas_list:
            label = choice_map.get(f, f)
            import_grouped.setdefault(label, []).append(
                {"nome": nome, "codigo": codigo}
            )
        label_envio = envio if envio else "Não informado"
        envio_grouped.setdefault(label_envio, []).append(
            {"nome": nome, "codigo": codigo}
        )
        if envio in ("Fisico", "Digital e Físico"):
            label_malote = malote if malote else "Não informado"
            malote_grouped.setdefault(label_malote, []).append(
                {"nome": nome, "codigo": codigo}
            )
    labels_imp = list(import_grouped.keys())
    counts_imp = [len(import_grouped[l]) for l in labels_imp]
    fig_imp = go.Figure(
        data=[go.Bar(x=labels_imp, y=counts_imp, marker_color=qualitative.Pastel)]
    )
    fig_imp.update_layout(
        title_text="Formas de Importação (Fiscal)",
        template="seaborn",
        xaxis_title="Forma",
        yaxis_title="Quantidade",
    )
    import_chart = fig_imp.to_html(full_html=False, div_id="fiscal-importacao-chart")
    labels_env = list(envio_grouped.keys())
    counts_env = [len(envio_grouped[l]) for l in labels_env]
    fig_env = go.Figure(
        data=[
            go.Pie(
                labels=labels_env,
                values=counts_env,
                hole=0.4,
                marker=dict(
                    colors=qualitative.Pastel,
                    line=dict(color="#FFFFFF", width=2),
                ),
                textinfo="label+percent",
            )
        ]
    )
    fig_env.update_layout(
        title_text="Envio de Documentos (Fiscal)",
        template="seaborn",
        legend=dict(orientation="h", y=-0.1, x=0.5, xanchor="center"),
    )
    envio_chart = fig_env.to_html(full_html=False, div_id="fiscal-envio-chart")
    labels_mal = list(malote_grouped.keys())
    counts_mal = [len(malote_grouped[l]) for l in labels_mal]
    fig_mal = go.Figure(
        data=[go.Bar(x=labels_mal, y=counts_mal, marker_color=qualitative.Pastel)]
    )
    fig_mal.update_layout(
        title_text="Coleta de Malote (Envio Físico)",
        template="seaborn",
        xaxis_title="Coleta",
        yaxis_title="Quantidade",
    )
    malote_chart = fig_mal.to_html(full_html=False, div_id="fiscal-malote-chart")
    return render_template(
        "admin/relatorio_fiscal.html",
        importacao_chart=import_chart,
        envio_chart=envio_chart,
        malote_chart=malote_chart,
        empresas_por_import=import_grouped,
        empresas_por_envio=envio_grouped,
        empresas_por_malote=malote_grouped,
    )


@app.route("/relatorio_contabil")
@admin_required
def relatorio_contabil():
    """Show summary charts for the accounting department."""
    departamentos = (
        Departamento.query.filter_by(tipo="Departamento Contábil")
        .join(Empresa)
        .with_entities(
            Empresa.nome_empresa,
            Empresa.codigo_empresa,
            Departamento.metodo_importacao,
            Departamento.forma_movimento,
            Departamento.malote_coleta,
            Departamento.controle_relatorios,
        )
        .all()
    )
    contabil_form = DepartamentoContabilForm()
    metodo_map = dict(contabil_form.metodo_importacao.choices)
    relatorio_map = dict(contabil_form.controle_relatorios.choices)
    import_grouped = {}
    envio_grouped = {}
    malote_grouped = {}
    relatorios_grouped = {}
    for nome, codigo, metodo, envio, malote, relatorios in departamentos:
        metodo_list = json.loads(metodo) if isinstance(metodo, str) else (metodo or [])
        for m in metodo_list:
            label = metodo_map.get(m, m)
            import_grouped.setdefault(label, []).append(
                {"nome": nome, "codigo": codigo}
            )
        label_envio = envio if envio else "Não informado"
        envio_grouped.setdefault(label_envio, []).append(
            {"nome": nome, "codigo": codigo}
        )
        if envio in ("Fisico", "Digital e Físico"):
            label_malote = malote if malote else "Não informado"
            malote_grouped.setdefault(label_malote, []).append(
                {"nome": nome, "codigo": codigo}
            )
        rel_list = (
            json.loads(relatorios)
            if isinstance(relatorios, str)
            else (relatorios or [])
        )
        for r in rel_list:
            label = relatorio_map.get(r, r)
            relatorios_grouped.setdefault(label, []).append(
                {"nome": nome, "codigo": codigo}
            )
    labels_imp = list(import_grouped.keys())
    counts_imp = [len(import_grouped[l]) for l in labels_imp]
    fig_imp = go.Figure(
        data=[go.Bar(x=labels_imp, y=counts_imp, marker_color=qualitative.Pastel)]
    )
    fig_imp.update_layout(
        title_text="Métodos de Importação (Contábil)",
        template="seaborn",
        xaxis_title="Método",
        yaxis_title="Quantidade",
    )
    import_chart = fig_imp.to_html(full_html=False, div_id="contabil-importacao-chart")
    labels_env = list(envio_grouped.keys())
    counts_env = [len(envio_grouped[l]) for l in labels_env]
    fig_env = go.Figure(
        data=[
            go.Pie(
                labels=labels_env,
                values=counts_env,
                hole=0.4,
                marker=dict(
                    colors=qualitative.Pastel,
                    line=dict(color="#FFFFFF", width=2),
                ),
                textinfo="label+percent",
            )
        ]
    )
    fig_env.update_layout(
        title_text="Envio de Documentos (Contábil)",
        template="seaborn",
        legend=dict(orientation="h", y=-0.1, x=0.5, xanchor="center"),
    )
    envio_chart = fig_env.to_html(full_html=False, div_id="contabil-envio-chart")
    labels_mal = list(malote_grouped.keys())
    counts_mal = [len(malote_grouped[l]) for l in labels_mal]
    fig_mal = go.Figure(
        data=[go.Bar(x=labels_mal, y=counts_mal, marker_color=qualitative.Pastel)]
    )
    fig_mal.update_layout(
        title_text="Coleta de Malote (Envio Físico)",
        template="seaborn",
        xaxis_title="Coleta",
        yaxis_title="Quantidade",
    )
    malote_chart = fig_mal.to_html(full_html=False, div_id="contabil-malote-chart")
    labels_rel = list(relatorios_grouped.keys())
    counts_rel = [len(relatorios_grouped[l]) for l in labels_rel]
    fig_rel = go.Figure(
        data=[go.Bar(x=labels_rel, y=counts_rel, marker_color=qualitative.Pastel)]
    )
    fig_rel.update_layout(
        title_text="Controle de Relatórios (Contábil)",
        template="seaborn",
        xaxis_title="Relatório",
        yaxis_title="Quantidade",
    )
    relatorios_chart = fig_rel.to_html(
        full_html=False, div_id="contabil-relatorios-chart"
    )
    return render_template(
        "admin/relatorio_contabil.html",
        importacao_chart=import_chart,
        envio_chart=envio_chart,
        malote_chart=malote_chart,
        relatorios_chart=relatorios_chart,
        empresas_por_import=import_grouped,
        empresas_por_envio=envio_grouped,
        empresas_por_malote=malote_grouped,
        empresas_por_relatorios=relatorios_grouped,
    )


@app.route("/relatorio_usuarios")
@admin_required
def relatorio_usuarios():
    """Visualize user counts by role and status."""
    users = User.query.with_entities(
        User.username, User.name, User.email, User.role, User.ativo
    ).all()
    grouped = {}
    labels = []
    counts = []
    for username, name, email, role, ativo in users:
        tipo = "Admin" if role == "admin" else "Usuário"
        status = "Ativo" if ativo else "Inativo"
        label = f"{tipo} {status}"
        grouped.setdefault(label, []).append(
            {"username": username, "name": name, "email": email}
        )
    for label, usuarios in grouped.items():
        labels.append(label)
        counts.append(len(usuarios))
    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels,
                values=counts,
                hole=0.4,
                marker=dict(
                    colors=qualitative.Pastel, line=dict(color="#FFFFFF", width=2)
                ),
                textinfo="label+percent",
            )
        ]
    )
    fig.update_layout(
        title_text="Usuários por tipo e status",
        template="seaborn",
        legend=dict(orientation="h", y=-0.1, x=0.5, xanchor="center"),
    )
    chart_div = fig.to_html(full_html=False, div_id="user-role-chart")
    return render_template(
        "admin/relatorio_usuarios.html",
        chart_div=chart_div,
        users_by_slice=grouped,
    )


@app.route("/logout", methods=["GET"])
@login_required
def logout():
    """Log out the current user."""
    sid = session.get("sid")
    if sid:
        Session.query.filter_by(session_id=sid).delete()
        db.session.commit()
        session.pop("sid", None)
    logout_user()
    return redirect(url_for("index"))


@app.route("/users", methods=["GET", "POST"])
@admin_required
def list_users():
    """List and register users in the admin panel."""
    form = RegistrationForm()
    form.tags.choices = [(t.id, t.nome) for t in Tag.query.order_by(Tag.nome).all()]
    show_inactive = request.args.get("show_inactive") in ("1", "on")

    if form.validate_on_submit():
        existing_user = User.query.filter(
            (User.username == form.username.data) | (User.email == form.email.data)
        ).first()
        if existing_user:
            flash("Usuário ou email já cadastrado.", "warning")
        else:
            user = User(
                username=form.username.data,
                email=form.email.data,
                name=form.name.data,
                role=form.role.data,
            )
            user.set_password(form.password.data)
            if form.tags.data:
                user.tags = Tag.query.filter(Tag.id.in_(form.tags.data)).all()
            db.session.add(user)
            db.session.commit()
            flash("Novo usuário cadastrado com sucesso!", "success")
        return redirect(url_for("list_users"))

    users_query = User.query
    if not show_inactive:
        users_query = users_query.filter_by(ativo=True)
    users = users_query.order_by(User.ativo.desc(), User.name).all()
    return render_template(
        "list_users.html", users=users, form=form, show_inactive=show_inactive
    )


@app.route("/admin/online-users")
@admin_required
def online_users():
    """List users active within the last five minutes."""
    cutoff = datetime.utcnow() - timedelta(minutes=5)
    users = (
        User.query.options(joinedload(User.tags))
        .filter(User.last_seen >= cutoff)
        .order_by(User.name)
        .all()
    )
    return render_template("admin/online_users.html", users=users)


@app.route("/novo_usuario", methods=["GET", "POST"])
@admin_required
def novo_usuario():
    """Create a new user from the admin interface."""
    form = RegistrationForm()
    form.tags.choices = [(t.id, t.nome) for t in Tag.query.order_by(Tag.nome).all()]
    if form.validate_on_submit():
        existing_user = User.query.filter(
            (User.username == form.username.data) | (User.email == form.email.data)
        ).first()
        if existing_user:
            flash("Usuário ou email já cadastrado.", "warning")
        else:
            user = User(
                username=form.username.data,
                email=form.email.data,
                name=form.name.data,
                role=form.role.data,
            )
            user.set_password(form.password.data)
            if form.tags.data:
                user.tags = Tag.query.filter(Tag.id.in_(form.tags.data)).all()
            db.session.add(user)
            db.session.commit()
            flash("Novo usuário cadastrado com sucesso!", "success")
            return redirect(url_for("list_users"))
    return render_template("admin/novo_usuario.html", form=form)


@app.route("/user/edit/<int:user_id>", methods=["GET", "POST"])
@admin_required
def edit_user(user_id):
    """Edit an existing user."""
    user = User.query.get_or_404(user_id)
    if user.is_master and current_user.id != user.id:
        abort(403)
    form = EditUserForm(obj=user)
    form.tags.choices = [(t.id, t.nome) for t in Tag.query.order_by(Tag.nome).all()]
    if user.is_master:
        form.role.data = user.role
        form.ativo.data = True
    if request.method == "GET":
        form.tags.data = [t.id for t in user.tags]

    if form.validate_on_submit():
        user.username = form.username.data
        user.email = form.email.data
        user.name = form.name.data
        if not user.is_master:
            user.role = form.role.data
            user.ativo = form.ativo.data
        else:
            user.ativo = True
        if form.tags.data:
            user.tags = Tag.query.filter(Tag.id.in_(form.tags.data)).all()
        else:
            user.tags = []

        # Process optional password change
        new_password = request.form.get("new_password")
        confirm_new_password = request.form.get("confirm_new_password")
        if new_password:
            if new_password != confirm_new_password:
                flash("As senhas devem ser iguais.", "danger")
                return redirect(url_for("edit_user", user_id=user.id))
            user.set_password(new_password)

        db.session.commit()
        flash("Usuário atualizado com sucesso!", "success")
        return redirect(url_for("list_users"))

    return render_template("edit_user.html", form=form, user=user)


# ---------------------- Task Management Routes ----------------------


@app.route("/tasks/overview")
@admin_required
def tasks_overview():
    """Kanban view of all tasks grouped by status."""
    assigned_param = (request.args.get("assigned_by_me", "") or "").lower()
    assigned_by_me = assigned_param in {"1", "true", "on", "yes"}
    query = (
        Task.query.join(Tag)
        .filter(Task.parent_id.is_(None), ~Tag.nome.in_(EXCLUDED_TASK_TAGS))
    )
    if assigned_by_me:
        query = query.filter(Task.created_by == current_user.id)
    tasks = (
        query.options(
            joinedload(Task.tag),
            joinedload(Task.assignee),
            joinedload(Task.finisher),
            joinedload(Task.status_history),
            joinedload(Task.children).joinedload(Task.assignee),
            joinedload(Task.children).joinedload(Task.finisher),
            joinedload(Task.children).joinedload(Task.tag),
            joinedload(Task.children).joinedload(Task.status_history),
        )
        .order_by(Task.due_date)
        .all()
    )
    tasks_by_status = {status: [] for status in TaskStatus}
    for t in tasks:
        status = t.status
        if isinstance(status, str):
            try:
                status = TaskStatus(status)
            except Exception:
                status = TaskStatus.PENDING
        if status not in tasks_by_status:
            status = TaskStatus.PENDING
        tasks_by_status[status].append(t)
    done_sorted = sorted(
        tasks_by_status[TaskStatus.DONE],
        key=lambda x: x.completed_at or datetime.min,
        reverse=True,
    )
    history_count = max(0, len(done_sorted) - 5)
    tasks_by_status[TaskStatus.DONE] = done_sorted[:5]
    return render_template(
        "tasks_overview.html",
        tasks_by_status=tasks_by_status,
        TaskStatus=TaskStatus,
        history_count=history_count,
        assigned_by_me=assigned_by_me,
    )


@app.route("/tasks/new", methods=["GET", "POST"])
@admin_required
def tasks_new():
    """Form to create a new task or subtask."""
    parent_id = request.args.get("parent_id", type=int)
    parent_task = Task.query.get(parent_id) if parent_id else None
    if parent_task and parent_task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    form = TaskForm()
    tag = parent_task.tag if parent_task else None
    if parent_task:
        form.parent_id.data = parent_task.id
        form.tag_id.choices = [(parent_task.tag_id, parent_task.tag.nome)]
        form.tag_id.data = parent_task.tag_id
        form.tag_id.render_kw = {"disabled": True}
        users = [u for u in parent_task.tag.users if u.ativo]
        form.assigned_to.choices = [(0, "Sem responsável")] + [
            (u.id, u.name) for u in users
        ]
    else:
        tags_query = (
            Tag.query.filter(~Tag.nome.in_(EXCLUDED_TASK_TAGS)).order_by(Tag.nome)
        )
        form.tag_id.choices = [(t.id, t.nome) for t in tags_query.all()]
        if form.tag_id.data:
            tag = Tag.query.get(form.tag_id.data)
            if tag:
                users = [u for u in tag.users if u.ativo]
                form.assigned_to.choices = [(0, "Sem responsável")] + [
                    (u.id, u.name) for u in users
                ]
        else:
            form.assigned_to.choices = [(0, "Sem responsável")]
    if form.validate_on_submit():
        tag_id = parent_task.tag_id if parent_task else form.tag_id.data
        if not parent_task and (tag is None or tag.id != tag_id):
            tag = Tag.query.get(tag_id)
        assignee_id = form.assigned_to.data or None
        task = Task(
            title=form.title.data,
            description=form.description.data,
            tag_id=tag_id,
            priority=TaskPriority(form.priority.data),
            due_date=form.due_date.data,
            created_by=current_user.id,
            parent_id=parent_id,
            assigned_to=assignee_id,
        )
        if task.assigned_to and task.assigned_to == current_user.id:
            task._skip_assignment_notification = True
        db.session.add(task)
        db.session.commit()
        flash("Tarefa criada com sucesso!", "success")
        return redirect(url_for("tasks_sector", tag_id=tag_id))
    cancel_url = (
        url_for("tasks_sector", tag_id=parent_task.tag_id)
        if parent_task
        else url_for("tasks_overview")
    )
    return render_template("tasks_new.html", form=form, parent_task=parent_task, cancel_url=cancel_url)


@app.route("/tasks/users/<int:tag_id>")
@admin_required
def tasks_users(tag_id):
    """Return active users for a given tag."""
    tag = Tag.query.get_or_404(tag_id)
    users = [
        {"id": u.id, "name": u.name}
        for u in tag.users
        if u.ativo
    ]
    return jsonify(users)


@app.route("/tasks/sector/<int:tag_id>")
@login_required
def tasks_sector(tag_id):
    """Kanban board of tasks for a specific sector/tag."""
    tag = Tag.query.get_or_404(tag_id)
    if tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    if current_user.role != "admin" and tag not in current_user.tags:
        abort(403)
    assigned_param = (request.args.get("assigned_to_me", "") or "").lower()
    assigned_to_me = assigned_param in {"1", "true", "on", "yes"}
    query = Task.query.filter(Task.tag_id == tag_id, Task.parent_id.is_(None))
    if assigned_to_me:
        query = query.filter(Task.assigned_to == current_user.id)
    tasks = (
        query.options(
            joinedload(Task.tag),
            joinedload(Task.assignee),
            joinedload(Task.finisher),
            joinedload(Task.status_history),
            joinedload(Task.children).joinedload(Task.assignee),
            joinedload(Task.children).joinedload(Task.finisher),
            joinedload(Task.children).joinedload(Task.tag),
            joinedload(Task.children).joinedload(Task.status_history),
        )
        .order_by(Task.due_date)
        .all()
    )
    tasks_by_status = {status: [] for status in TaskStatus}
    for t in tasks:
        status = t.status
        if isinstance(status, str):
            try:
                status = TaskStatus(status)
            except Exception:
                status = TaskStatus.PENDING
        if status not in tasks_by_status:
            status = TaskStatus.PENDING
        tasks_by_status[status].append(t)
    done_sorted = sorted(
        tasks_by_status[TaskStatus.DONE],
        key=lambda x: x.completed_at or datetime.min,
        reverse=True,
    )
    history_count = max(0, len(done_sorted) - 5)
    tasks_by_status[TaskStatus.DONE] = done_sorted[:5]
    return render_template(
        "tasks_board.html",
        tag=tag,
        tasks_by_status=tasks_by_status,
        TaskStatus=TaskStatus,
        history_count=history_count,
        assigned_to_me=assigned_to_me,
    )


@app.route("/tasks/history")
@app.route("/tasks/history/<int:tag_id>")
@login_required
def tasks_history(tag_id=None):
    """Display archived tasks beyond the visible limit."""
    assigned_param = (request.args.get("assigned_to_me", "") or "").lower()
    assigned_to_me = assigned_param in {"1", "true", "on", "yes"}
    assigned_by_param = (request.args.get("assigned_by_me", "") or "").lower()
    assigned_by_me = assigned_by_param in {"1", "true", "on", "yes"}
    if tag_id:
        tag = Tag.query.get_or_404(tag_id)
        if tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
            abort(404)
        if current_user.role != "admin" and tag not in current_user.tags:
            abort(403)
        query = Task.query.filter(
            Task.tag_id == tag_id,
            Task.parent_id.is_(None),
            Task.status == TaskStatus.DONE,
        )
    else:
        tag = None
        if current_user.role == "admin":
            query = Task.query.filter(
                Task.parent_id.is_(None), Task.status == TaskStatus.DONE
            )
        else:
            tag_ids = [t.id for t in current_user.tags]
            query = Task.query.filter(
                Task.parent_id.is_(None),
                Task.status == TaskStatus.DONE,
                Task.tag_id.in_(tag_ids),
            )
    if assigned_to_me:
        query = query.filter(Task.assigned_to == current_user.id)
    if assigned_by_me:
        query = query.filter(Task.created_by == current_user.id)
    tasks = (
        query.order_by(Task.completed_at.desc())
        .options(joinedload(Task.tag), joinedload(Task.finisher))
        .offset(5)
        .all()
    )
    return render_template(
        "tasks_history.html",
        tag=tag,
        tasks=tasks,
        assigned_to_me=assigned_to_me,
        assigned_by_me=assigned_by_me,
    )


@app.route("/tasks/<int:task_id>")
@login_required
def tasks_view(task_id):
    """Display details of a completed task."""
    task = (
        Task.query.options(
            joinedload(Task.tag),
            joinedload(Task.assignee),
            joinedload(Task.finisher),
            joinedload(Task.parent),
            joinedload(Task.status_history),
        )
        .get_or_404(task_id)
    )
    if task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    if current_user.role != "admin" and task.tag not in current_user.tags:
        abort(403)
    priority_labels = {"low": "Baixa", "medium": "Média", "high": "Alta"}
    priority_order = ["low", "medium", "high"]
    cancel_url = url_for("tasks_history", tag_id=task.tag_id)
    return render_template(
        "tasks_view.html",
        task=task,
        priority_labels=priority_labels,
        priority_order=priority_order,
        cancel_url=cancel_url,
    )


@app.route("/tasks/<int:task_id>/status", methods=["POST"])
@login_required
def update_task_status(task_id):
    """Update a task status and record its history."""
    task = Task.query.get_or_404(task_id)
    if task.tag.nome.lower() in EXCLUDED_TASK_TAGS_LOWER:
        abort(404)
    if current_user.role != "admin" and task.tag not in current_user.tags:
        abort(403)
    data = request.get_json() or {}
    status_value = data.get("status")
    try:
        new_status = TaskStatus(status_value)
    except Exception:
        abort(400)
    if current_user.role != "admin":
        allowed = {
            TaskStatus.PENDING: {TaskStatus.IN_PROGRESS},
            TaskStatus.IN_PROGRESS: {TaskStatus.DONE},
        }
        if new_status not in allowed.get(task.status, set()):
            abort(403)
    if task.status != new_status:
        history = TaskStatusHistory(
            task_id=task.id,
            from_status=task.status,
            to_status=new_status,
            changed_by=current_user.id,
        )
        old_status = task.status
        task.status = new_status
        if new_status == TaskStatus.IN_PROGRESS:
            if old_status != TaskStatus.DONE or current_user.role != "admin":
                if task.assigned_to != current_user.id:
                    task._skip_assignment_notification = True
                task.assigned_to = current_user.id
            task.completed_by = None
            task.completed_at = None
        elif new_status == TaskStatus.DONE:
            task.completed_by = current_user.id
            task.completed_at = datetime.utcnow()
        elif new_status == TaskStatus.PENDING:
            task.assigned_to = None
            task.completed_by = None
            task.completed_at = None
        else:
            task.completed_by = None
            task.completed_at = None
        db.session.add(history)
        db.session.commit()
    return jsonify({"success": True})
