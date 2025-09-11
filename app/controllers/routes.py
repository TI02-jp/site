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
from flask_login import current_user, login_required, login_user, logout_user
from app import app, db
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
)
from app.services.google_calendar import get_calendar_timezone
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
)
import os, json, re, secrets
import requests
from werkzeug.utils import secure_filename
from uuid import uuid4
from sqlalchemy import or_, cast, String
from sqlalchemy.orm import joinedload
from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token
from google.auth.transport.requests import Request
from app.services.cnpj import consultar_cnpj
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
from datetime import datetime, timedelta

GOOGLE_OAUTH_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/calendar",
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


def validate_contatos(contatos):
    """Validate contact data ensuring proper formats."""
    email_re = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    for c in contatos:
        meios = c.get("meios")
        if meios is None:
            meios = [{"tipo": c.get("tipo"), "endereco": c.get("endereco", "")}]
        validated = []
        for m in meios:
            tipo = m.get("tipo")
            endereco = m.get("endereco", "")
            if tipo == "email":
                if not email_re.match(endereco):
                    raise ValueError(f"E-mail inválido: {endereco}")
            elif tipo in ("telefone", "whatsapp"):
                digits = re.sub(r"\D", "", endereco)
                if not digits:
                    raise ValueError(f"Número inválido: {endereco}")
                endereco = format_phone(digits)
            validated.append({"tipo": tipo, "endereco": endereco})
        c["meios"] = validated
        c.pop("tipo", None)
        c.pop("endereco", None)
    return contatos


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


@app.route("/")
def index():
    """Redirect users to the appropriate first page."""
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    return redirect(url_for("login"))


@app.route("/home")
@login_required
def home():
    """Render the authenticated home page."""
    return render_template("home.html")


@app.route("/ping")
@login_required
def ping():
    """Endpoint for client pings to keep the session active."""
    session.modified = True
    return ("", 204)


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
    events: list[dict] = []
    show_modal = False
    raw_events = fetch_raw_events()
    calendar_tz = get_calendar_timezone()
    now = datetime.now(calendar_tz)
    if form.validate_on_submit():
        if form.meeting_id.data:
            meeting = Reuniao.query.get(int(form.meeting_id.data))
            if meeting and meeting.criador_id == current_user.id:
                success = update_meeting(form, raw_events, now, meeting)
                if success:
                    return redirect(url_for("sala_reunioes"))
                show_modal = True
            else:
                flash(
                    "Você só pode editar reuniões que você criou.",
                    "danger",
                )
        else:
            success = create_meeting_and_event(
                form, raw_events, now, current_user.id
            )
            if success:
                return redirect(url_for("sala_reunioes"))
            show_modal = True
    if request.method == "POST":
        show_modal = True
    events = combine_events(raw_events, now, current_user.id)
    return render_template(
        "sala_reunioes.html",
        form=form,
        events=events,
        show_modal=show_modal,
        calendar_timezone=calendar_tz.key,
    )


@app.route("/reuniao/<int:meeting_id>/delete", methods=["POST"])
@login_required
def delete_reuniao(meeting_id):
    """Delete a meeting and its corresponding Google Calendar event."""
    meeting = Reuniao.query.get_or_404(meeting_id)
    if meeting.criador_id != current_user.id:
        flash("Você só pode excluir reuniões que você criou.", "danger")
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

    ## Rota para cadastrar uma nova empresa


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


def processar_dados_fiscal(request):
    """Função auxiliar para processar dados do departamento fiscal"""
    responsavel = request.form.get("responsavel")
    descricao = request.form.get("descricao")
    acessos_json = request.form.get("acessos_json", "[]")
    try:
        acessos = json.loads(acessos_json) if acessos_json else []
    except Exception:
        acessos = []
    forma_movimento = request.form.get("forma_movimento")
    observacao_movimento = request.form.get("observacao_movimento")
    observacao_importacao = request.form.get("observacao_importacao")
    observacao_contato = request.form.get("observacao_contato")
    particularidades = sanitize_html(request.form.get("particularidades"))
    formas_importacao_json = request.form.get("formas_importacao_json", "[]")
    formas_importacao = (
        json.loads(formas_importacao_json) if formas_importacao_json else []
    )
    envio_digital = request.form.getlist("envio_digital")
    envio_fisico = request.form.getlist("envio_fisico")
    malote_coleta = request.form.get("malote_coleta")
    contatos_json = request.form.get("contatos_json", "null")
    contatos = json.loads(contatos_json) if contatos_json != "null" else None
    if contatos is not None:
        contatos = validate_contatos(contatos)

    return {
        "responsavel": responsavel,
        "descricao": descricao,
        "formas_importacao": formas_importacao,
        "acessos": acessos,
        "forma_movimento": forma_movimento,
        "envio_digital": envio_digital,
        "envio_fisico": envio_fisico,
        "malote_coleta": malote_coleta,
        "observacao_movimento": observacao_movimento,
        "observacao_importacao": observacao_importacao,
        "observacao_contato": observacao_contato,
        "contatos": contatos,
        "particularidades_texto": particularidades,
    }


def processar_dados_contabil(request):
    """Função auxiliar para processar dados do departamento contábil"""
    responsavel = request.form.get("responsavel")
    descricao = request.form.get("descricao")
    metodo_importacao = request.form.getlist("metodo_importacao")
    forma_movimento = request.form.get("forma_movimento")
    particularidades = sanitize_html(request.form.get("particularidades"))
    envio_digital = request.form.getlist("envio_digital")
    envio_fisico = request.form.getlist("envio_fisico")
    malote_coleta = request.form.get("malote_coleta")
    controle_relatorios_json = request.form.get("controle_relatorios_json", "[]")
    controle_relatorios = (
        json.loads(controle_relatorios_json) if controle_relatorios_json else []
    )
    observacao_movimento = request.form.get("observacao_movimento")
    observacao_controle_relatorios = request.form.get("observacao_controle_relatorios")

    return {
        "responsavel": responsavel,
        "descricao": descricao,
        "metodo_importacao": metodo_importacao,
        "forma_movimento": forma_movimento,
        "envio_digital": envio_digital,
        "envio_fisico": envio_fisico,
        "malote_coleta": malote_coleta,
        "controle_relatorios": controle_relatorios,
        "observacao_movimento": observacao_movimento,
        "observacao_controle_relatorios": observacao_controle_relatorios,
        "particularidades_texto": particularidades,
    }


def processar_dados_pessoal(request):
    """Função auxiliar para processar dados do departamento pessoal"""
    return {
        "responsavel": request.form.get("responsavel"),
        "descricao": request.form.get("descricao"),
        "data_envio": request.form.get("data_envio"),
        "registro_funcionarios": request.form.get("registro_funcionarios"),
        "ponto_eletronico": request.form.get("ponto_eletronico"),
        "pagamento_funcionario": request.form.get("pagamento_funcionario"),
        "particularidades_texto": sanitize_html(request.form.get("particularidades")),
    }


def processar_dados_administrativo(request):
    """Função auxiliar para processar dados do departamento administrativo"""
    return {
        "particularidades_texto": sanitize_html(request.form.get("particularidades"))
    }


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
