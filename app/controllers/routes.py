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
    has_request_context,
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
    DiretoriaEvent,
    GeneralCalendarEvent,
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
    GeneralCalendarEventForm,
    TaskForm,
    AccessLinkForm,
    CourseForm,
)
import os, json, re, secrets
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
from app.services.general_calendar import (
    populate_event_participants as populate_general_event_participants,
    create_calendar_event_from_form,
    update_calendar_event_from_form,
    delete_calendar_event,
    serialize_events_for_calendar,
)
import plotly.graph_objects as go
from plotly.colors import qualitative
from werkzeug.exceptions import RequestEntityTooLarge
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlunsplit

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


EVENT_TYPE_LABELS = {
    "treinamento": "Treinamento",
    "data_comemorativa": "Data comemorativa",
    "evento": "Evento",
}

EVENT_AUDIENCE_LABELS = {
    "interno": "Interno",
    "externo": "Externo",
    "ambos": "Ambos",
}

EVENT_CATEGORY_LABELS = {
    "cafe": "Café da manhã",
    "almoco": "Almoço",
    "lanche": "Lanche",
    "outros": "Outros serviços",
}


def parse_diretoria_event_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate and normalize Diretoria JP event data sent from the form."""

    name = (payload.get("name") or "").strip()
    event_type = payload.get("type")
    date_raw = payload.get("date")
    description = (payload.get("description") or "").strip()
    audience = payload.get("audience")
    participants_raw = payload.get("participants")
    categories_payload = payload.get("categories") or {}

    errors: list[str] = []

    if not name:
        errors.append("Informe o nome do evento.")

    if event_type not in EVENT_TYPE_LABELS:
        errors.append("Selecione um tipo de evento válido.")

    try:
        event_date = datetime.strptime(str(date_raw), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        errors.append("Informe uma data válida para o evento.")
        event_date = None

    if audience not in EVENT_AUDIENCE_LABELS:
        errors.append("Selecione o público participante do evento.")

    try:
        participants = int(participants_raw)
        if participants < 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("Informe o número de participantes do evento.")
        participants = 0

    services_payload: dict[str, dict[str, object]] = {}
    total_event = Decimal("0.00")

    for key in EVENT_CATEGORY_LABELS:
        category_data = (
            categories_payload.get(key, {})
            if isinstance(categories_payload, dict)
            else {}
        )
        items_data = []
        if isinstance(category_data, dict):
            items_data = category_data.get("items", []) or []

        processed_items: list[dict[str, object]] = []
        category_total = Decimal("0.00")

        if isinstance(items_data, list):
            for item in items_data:
                if not isinstance(item, dict):
                    continue

                item_name = (item.get("name") or "").strip()
                if not item_name:
                    continue

                try:
                    quantity = Decimal(str(item.get("quantity", "0")))
                    unit_value = Decimal(str(item.get("unit_value", "0")))
                except (InvalidOperation, TypeError):
                    continue

                if quantity < 0 or unit_value < 0:
                    continue

                line_total = (quantity * unit_value).quantize(Decimal("0.01"))

                processed_items.append(
                    {
                        "name": item_name,
                        "quantity": float(quantity),
                        "unit_value": float(unit_value),
                        "total": float(line_total),
                    }
                )

                category_total += line_total

        category_total = category_total.quantize(Decimal("0.01"))
        services_payload[key] = {
            "items": processed_items,
            "total": float(category_total),
        }
        total_event += category_total

    total_event = total_event.quantize(Decimal("0.01"))

    normalized = {
        "name": name,
        "event_type": event_type,
        "event_date": event_date,
        "description": description or None,
        "audience": audience,
        "participants": participants,
        "services": services_payload,
        "total_cost": total_event,
    }

    return normalized, errors


def build_google_flow(state: str | None = None) -> Flow:
    """Return a configured Google OAuth ``Flow`` instance."""
    client_id = current_app.config.get("GOOGLE_CLIENT_ID")
    client_secret = current_app.config.get("GOOGLE_CLIENT_SECRET")
    if not (client_id and client_secret):
        abort(404)

    redirect_uri = get_google_redirect_uri()

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        },
        scopes=GOOGLE_OAUTH_SCOPES,
        state=state,
    )
    flow.redirect_uri = redirect_uri
    return flow


def get_google_redirect_uri() -> str:
    """Return the redirect URI registered with Google."""

    configured_uri = current_app.config.get("GOOGLE_REDIRECT_URI")
    if configured_uri:
        return configured_uri

    callback_path = url_for("google_callback", _external=False)

    if has_request_context():
        scheme = request.scheme or "http"
        host = request.host

        forwarded = request.headers.get("Forwarded")
        if forwarded:
            forwarded = forwarded.split(",", 1)[0]
            forwarded_parts = {}
            for part in forwarded.split(";"):
                if "=" not in part:
                    continue
                key, value = part.split("=", 1)
                forwarded_parts[key.strip().lower()] = value.strip().strip('"')
            scheme = forwarded_parts.get("proto", scheme) or scheme
            host = forwarded_parts.get("host", host) or host

        forwarded_proto = request.headers.get("X-Forwarded-Proto")
        if forwarded_proto:
            scheme = forwarded_proto.split(",", 1)[0].strip() or scheme

        forwarded_host = request.headers.get("X-Forwarded-Host")
        if forwarded_host:
            host = forwarded_host.split(",", 1)[0].strip() or host

        forwarded_port = request.headers.get("X-Forwarded-Port")
        if forwarded_port:
            port = forwarded_port.split(",", 1)[0].strip()
            if port:
                default_port = "443" if scheme == "https" else "80"
                if ":" not in host and port != default_port:
                    host = f"{host}:{port}"

        scheme = scheme or current_app.config.get("PREFERRED_URL_SCHEME", "http")
        host = host or request.host

        return urlunsplit((scheme, host, callback_path, "", ""))

    scheme = current_app.config.get("PREFERRED_URL_SCHEME", "http")
    server_name = current_app.config.get("SERVER_NAME")
    if server_name:
        return urlunsplit((scheme, server_name, callback_path, "", ""))

    return url_for("google_callback", _external=True, _scheme=scheme)


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


# Allowed image file extensions for uploads
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}


def allowed_file(filename):
    """Check if a filename has an allowed extension."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


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

    if file and allowed_file(file.filename):
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


@app.route("/diretoria/eventos", methods=["GET", "POST"])
@login_required
def diretoria_eventos():
    """Render or persist Diretoria JP event planning data."""

    if current_user.role != "admin" and not user_has_tag("Gestão"):
        abort(403)

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        normalized, errors = parse_diretoria_event_payload(payload)

        if errors:
            return jsonify({"errors": errors}), 400

        diretoria_event = DiretoriaEvent(
            **normalized,
            created_by=current_user,
        )

        db.session.add(diretoria_event)
        db.session.commit()

        session["diretoria_event_feedback"] = {
            "message": f'Evento "{diretoria_event.name}" salvo com sucesso.',
            "category": "success",
        }

        return (
            jsonify(
                {
                    "success": True,
                    "redirect_url": url_for("diretoria_eventos_lista"),
                }
            ),
            201,
        )

    return render_template("diretoria/eventos.html")


@app.route("/diretoria/eventos/<int:event_id>/editar", methods=["GET", "POST"])
@login_required
def diretoria_eventos_editar(event_id: int):
    """Edit an existing Diretoria JP event."""

    if current_user.role != "admin" and not user_has_tag("Gestão"):
        abort(403)

    event = DiretoriaEvent.query.get_or_404(event_id)

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        normalized, errors = parse_diretoria_event_payload(payload)

        if errors:
            return jsonify({"errors": errors}), 400

        if not normalized.get("event_date"):
            return jsonify({"errors": ["Informe uma data válida para o evento."]}), 400

        event.name = normalized["name"]
        event.event_type = normalized["event_type"]
        event.event_date = normalized["event_date"]
        event.description = normalized["description"]
        event.audience = normalized["audience"]
        event.participants = normalized["participants"]
        event.services = normalized["services"]
        event.total_cost = normalized["total_cost"]

        db.session.commit()

        session["diretoria_event_feedback"] = {
            "message": f'Evento "{event.name}" atualizado com sucesso.',
            "category": "success",
        }

        return jsonify(
            {
                "success": True,
                "redirect_url": url_for("diretoria_eventos_lista"),
            }
        )

    event_payload = {
        "id": event.id,
        "name": event.name,
        "type": event.event_type,
        "date": event.event_date.strftime("%Y-%m-%d"),
        "description": event.description or "",
        "audience": event.audience,
        "participants": event.participants,
        "categories": event.services or {},
        "submit_url": url_for("diretoria_eventos_editar", event_id=event.id),
    }

    return render_template("diretoria/eventos.html", event_data=event_payload)


@app.route("/diretoria/eventos/lista")
@login_required
def diretoria_eventos_lista():
    """Display saved Diretoria JP events with search support."""

    if current_user.role != "admin" and not user_has_tag("Gestão"):
        abort(403)

    search_query = (request.args.get("q") or "").strip()

    events_query = DiretoriaEvent.query
    if search_query:
        events_query = events_query.filter(
            DiretoriaEvent.name.ilike(f"%{search_query}%")
        )

    events = (
        events_query.order_by(
            DiretoriaEvent.event_date.desc(), DiretoriaEvent.created_at.desc()
        ).all()
    )

    feedback = session.pop("diretoria_event_feedback", None)
    if isinstance(feedback, dict) and feedback.get("message"):
        flash(feedback["message"], feedback.get("category", "success"))

    return render_template(
        "diretoria/eventos_lista.html",
        events=events,
        search_query=search_query,
        event_type_labels=EVENT_TYPE_LABELS,
        audience_labels=EVENT_AUDIENCE_LABELS,
        category_labels=EVENT_CATEGORY_LABELS,
    )


@app.route("/diretoria/eventos/<int:event_id>/excluir", methods=["POST"])
@login_required
def diretoria_eventos_excluir(event_id: int):
    """Remove a Diretoria JP event."""

    if current_user.role != "admin" and not user_has_tag("Gestão"):
        abort(403)

    event = DiretoriaEvent.query.get_or_404(event_id)
    event_name = event.name

    db.session.delete(event)
    db.session.commit()

    flash(f'Evento "{event_name}" removido com sucesso.', "success")

    return redirect(url_for("diretoria_eventos_lista"))


@app.route("/cursos", methods=["GET", "POST"])
@login_required
def cursos():
    """Display the curated catalog of internal courses."""

    form = CourseForm()
    can_manage_courses = current_user.role == "admin"
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

    if request.method == "POST" and not can_manage_courses:
        flash("Apenas administradores podem cadastrar ou editar cursos.", "danger")
        return redirect(url_for("cursos"))

    if request.method == "POST" and form.submit_delete.data:
        if not course_id_raw:
            flash("Selecione um curso para excluir.", "danger")
            return redirect(url_for("cursos"))

        try:
            course_id = int(course_id_raw)
        except ValueError:
            flash("O curso selecionado não foi encontrado. Tente novamente.", "danger")
            return redirect(url_for("cursos"))

        existing_course_id = db.session.execute(
            sa.select(Course.id).where(Course.id == course_id)
        ).scalar_one_or_none()

        if existing_course_id is None:
            flash("O curso selecionado não foi encontrado. Tente novamente.", "danger")
            return redirect(url_for("cursos"))

        linked_meetings = Reuniao.query.filter_by(course_id=course_id).all()
        for meeting in linked_meetings:
            if not delete_meeting(meeting):
                flash(
                    "Não foi possível remover a reunião vinculada no calendário. Tente novamente em alguns instantes.",
                    "danger",
                )
                return redirect(url_for("cursos"))

        db.session.execute(sa.delete(Course).where(Course.id == course_id))
        db.session.commit()
        if linked_meetings:
            flash(
                "Curso e reuniões associadas excluídos com sucesso!",
                "success",
            )
        else:
            flash("Curso excluído com sucesso!", "success")
        return redirect(url_for("cursos"))

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
        should_add_to_calendar = bool(form.submit_add_to_calendar.data)
        meeting_query_params: dict[str, Any] = {}
        if should_add_to_calendar:
            meeting_query_params = {"course_calendar": "1"}
            name_value = (form.name.data or "").strip()
            if name_value:
                meeting_query_params["subject"] = name_value
            if form.start_date.data:
                meeting_query_params["date"] = form.start_date.data.isoformat()
            if form.schedule_start.data:
                meeting_query_params["start"] = form.schedule_start.data.strftime("%H:%M")
            if form.schedule_end.data:
                meeting_query_params["end"] = form.schedule_end.data.strftime("%H:%M")
            participant_ids = [str(user_id) for user_id in form.participants.data]
            if participant_ids:
                meeting_query_params["participants"] = participant_ids

        success_message = ""
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
            success_message = "Curso atualizado com sucesso!"
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
            course_id = course.id
            success_message = "Curso cadastrado com sucesso!"
        if success_message:
            flash(success_message, "success")
        if (
            should_add_to_calendar
            and meeting_query_params.get("subject")
            and meeting_query_params.get("date")
        ):
            if course_id is not None:
                meeting_query_params["course_id"] = str(course_id)
            return redirect(url_for("sala_reunioes", **meeting_query_params))
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
        can_manage_courses=can_manage_courses,
    )


@app.route("/acessos")
@login_required
def acessos():
    """Display the hub with the available access categories and direct shortcuts."""

    categoria_links = {
        slug: (
            AccessLink.query.filter_by(category=slug)
            .order_by(AccessLink.created_at.desc())
            .all()
        )
        for slug in ACESSOS_CATEGORIES
    }
    return render_template(
        "acessos.html",
        categorias=ACESSOS_CATEGORIES,
        links=ACESSOS_DIRECT_LINKS,
        categoria_links=categoria_links,
    )


@app.route("/acessos/<categoria_slug>")
@login_required
def acessos_categoria(categoria_slug: str):
    """Legacy endpoint kept for compatibility; redirects to the main listing."""

    if categoria_slug.lower() not in ACESSOS_CATEGORIES:
        abort(404)

    return redirect(url_for("acessos"))


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
        return redirect(url_for("acessos"))

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


@app.route("/calendario-colaboradores", methods=["GET", "POST"])
@login_required
def calendario_colaboradores():
    """Display and manage the internal collaborators calendar."""

    form = GeneralCalendarEventForm()
    populate_general_event_participants(form)
    can_manage = current_user.role == "admin" or user_has_tag("Gestão")
    show_modal = False
    if form.validate_on_submit():
        if not can_manage:
            abort(403)
        event_id_raw = form.event_id.data
        if event_id_raw:
            try:
                event_id = int(event_id_raw)
            except (TypeError, ValueError):
                abort(400)
            event = GeneralCalendarEvent.query.get_or_404(event_id)
            if current_user.role != "admin" and event.created_by_id != current_user.id:
                flash("Você só pode editar eventos que você criou.", "danger")
                return redirect(url_for("calendario_colaboradores"))
            update_calendar_event_from_form(event, form)
        else:
            create_calendar_event_from_form(form, current_user.id)
        return redirect(url_for("calendario_colaboradores"))
    elif request.method == "POST":
        show_modal = True

    calendar_tz = get_calendar_timezone()
    return render_template(
        "calendario_colaboradores.html",
        form=form,
        can_manage=can_manage,
        show_modal=show_modal,
        calendar_timezone=calendar_tz.key,
    )


@app.route("/calendario-eventos/<int:event_id>/delete", methods=["POST"])
@login_required
def delete_calendario_evento(event_id):
    """Delete an event from the collaborators calendar."""

    event = GeneralCalendarEvent.query.get_or_404(event_id)
    can_manage = current_user.role == "admin" or user_has_tag("Gestão")
    if not can_manage:
        abort(403)
    if current_user.role != "admin" and event.created_by_id != current_user.id:
        flash("Você só pode excluir eventos que você criou.", "danger")
        return redirect(url_for("calendario_colaboradores"))
    delete_calendar_event(event)
    flash("Evento removido com sucesso!", "success")
    return redirect(url_for("calendario_colaboradores"))


@app.route("/sala-reunioes", methods=["GET", "POST"])
@login_required
def sala_reunioes():
    """List and create meetings using Google Calendar."""
    form = MeetingForm()
    populate_participants_choices(form)
    show_modal = False
    prefill_from_course = request.method == "GET" and request.args.get("course_calendar") == "1"
    if prefill_from_course:
        subject = (request.args.get("subject") or "").strip()
        if subject:
            form.subject.data = subject
        date_raw = request.args.get("date")
        if date_raw:
            try:
                form.date.data = datetime.strptime(date_raw, "%Y-%m-%d").date()
            except ValueError:
                pass
        start_raw = request.args.get("start")
        if start_raw:
            try:
                form.start_time.data = datetime.strptime(start_raw, "%H:%M").time()
            except ValueError:
                pass
        end_raw = request.args.get("end")
        if end_raw:
            try:
                form.end_time.data = datetime.strptime(end_raw, "%H:%M").time()
            except ValueError:
                pass
        participant_ids: list[int] = []
        for raw_id in request.args.getlist("participants"):
            try:
                parsed_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if parsed_id in participant_ids:
                continue
            if any(choice_id == parsed_id for choice_id, _ in form.participants.choices):
                participant_ids.append(parsed_id)
        if participant_ids:
            form.participants.data = participant_ids
        form.apply_more_days.data = False
        form.notify_attendees.data = True
        form.course_id.data = request.args.get("course_id", "")
        show_modal = True
        flash(
            "Revise os dados do curso, ajuste se necessário e confirme o agendamento da reunião.",
            "info",
        )
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
        next_url = request.form.get("next") or request.args.get("next")
        if next_url:
            return redirect(next_url)
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
    authorization_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    session["oauth_state"] = state
    # ``google-auth`` only attaches the PKCE verifier to the flow instance, so we
    # persist it explicitly to reuse on the callback.
    code_verifier = getattr(flow, "code_verifier", None)
    if isinstance(code_verifier, bytes):
        code_verifier = code_verifier.decode()
    if code_verifier:
        session["oauth_code_verifier"] = code_verifier
    else:
        session.pop("oauth_code_verifier", None)
    return redirect(authorization_url)


@app.route("/oauth2callback")
def google_callback():
    """Handle OAuth callback from Google."""
    state = session.get("oauth_state")
    code_verifier = session.get("oauth_code_verifier")
    if state is None or state != request.args.get("state"):
        session.pop("oauth_state", None)
        session.pop("oauth_code_verifier", None)
        flash("Falha ao validar resposta do Google. Tente novamente.", "danger")
        return redirect(url_for("login"))
    flow = build_google_flow(state=state)
    try:
        # ``request.url`` may reflect the internal HTTP scheme when the app is behind
        # a reverse proxy performing TLS termination. Reconstruct the callback URL
        # from the configured redirect URI so Google receives the same host and
        # scheme that was originally registered.
        authorization_response = flow.redirect_uri or request.url
        if request.query_string:
            query_string = request.query_string.decode()
            separator = "&" if "?" in authorization_response else "?"
            authorization_response = f"{authorization_response}{separator}{query_string}"

        callback_scope = request.args.get("scope")
        if callback_scope and hasattr(flow, "oauth2session"):
            callback_scopes = callback_scope.split()
            requested_scopes = set(flow.oauth2session.scope or [])
            returned_scopes = set(callback_scopes)
            if requested_scopes and returned_scopes != requested_scopes:
                current_app.logger.warning(
                    "Escopos do Google retornados divergiram do solicitado. Solicitado: %s. Retornado: %s.",
                    sorted(requested_scopes),
                    sorted(returned_scopes),
                )
            flow.oauth2session.scope = callback_scopes

        fetch_kwargs = {"authorization_response": authorization_response}
        if code_verifier:
            fetch_kwargs["code_verifier"] = code_verifier
        flow.fetch_token(**fetch_kwargs)
    except Exception:
        current_app.logger.exception("Falha ao trocar código OAuth do Google por token")
        flash("Não foi possível completar a autenticação com o Google.", "danger")
        return redirect(url_for("login"))
    finally:
        session.pop("oauth_state", None)
        session.pop("oauth_code_verifier", None)
    credentials = flow.credentials
    request_session = requests.Session()
    token_request = Request(session=request_session)
    try:
        id_info = id_token.verify_oauth2_token(
            credentials.id_token, token_request, current_app.config["GOOGLE_CLIENT_ID"]
        )
    except ValueError:
        current_app.logger.exception("ID token do Google inválido durante login")
        flash("Não foi possível validar a resposta do Google.", "danger")
        return redirect(url_for("login"))
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


@app.route("/api/calendario-eventos")
@login_required
def api_general_calendar_events():
    """Return collaborator calendar events as JSON."""

    can_manage = current_user.role == "admin" or user_has_tag("Gestão")
    events = serialize_events_for_calendar(
        current_user.id, can_manage, current_user.role == "admin"
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
