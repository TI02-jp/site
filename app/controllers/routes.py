"""Flask route handlers for the web application."""

from flask import render_template, redirect, url_for, flash, request, abort, jsonify, current_app, session
from functools import wraps
from flask_login import current_user, login_required, login_user, logout_user
from app import app, db
from app.utils.security import sanitize_html
from app.loginForms import LoginForm, RegistrationForm
from app.models.tables import (
    User,
    Empresa,
    Departamento,
    Consultoria,
    Setor,
    Inclusao,
    MeetingRoomEvent,
)
from app.forms import (
    EmpresaForm,
    EditUserForm,
    DepartamentoFiscalForm,
    DepartamentoContabilForm,
    DepartamentoPessoalForm,
    DepartamentoAdministrativoForm,
    ConsultoriaForm,
    SetorForm,
)
import os, json, re
from werkzeug.utils import secure_filename
from uuid import uuid4
from sqlalchemy import or_
from app.services.cnpj import consultar_cnpj
import plotly.graph_objects as go
from plotly.colors import qualitative
from werkzeug.exceptions import RequestEntityTooLarge
from datetime import datetime, timedelta
from itsdangerous import URLSafeSerializer

@app.context_processor
def inject_stats():
    """Inject global statistics into templates."""
    if current_user.is_authenticated:
        total_empresas = Empresa.query.count()
        total_usuarios = User.query.count() if current_user.role == 'admin' else 0
        online_count = 0
        if current_user.role == 'admin':
            cutoff = datetime.utcnow() - timedelta(minutes=5)
            online_count = User.query.filter(User.last_seen >= cutoff).count()
        return {
            'total_empresas': total_empresas,
            'total_usuarios': total_usuarios,
            'online_users_count': online_count
        }
    return {}

# Allowed image file extensions for uploads
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    """Check if a filename has an allowed extension."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS



@app.errorhandler(RequestEntityTooLarge)
def handle_large_file(e):
    """Return JSON error when uploaded file exceeds limit."""
    return jsonify({'error': 'Arquivo excede o tamanho permitido'}), 413


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
    if all(isinstance(c, dict) and 'meios' in c for c in contatos):
        for c in contatos:
            meios = c.get('meios') or []
            for m in meios:
                if 'valor' in m and 'endereco' not in m:
                    m['endereco'] = m.pop('valor')
                if m.get('tipo') in ('telefone', 'whatsapp'):
                    digits = re.sub(r'\D', '', m.get('endereco', ''))
                    m['endereco'] = format_phone(digits)
        return contatos
    grouped = {}
    for c in contatos:
        if not isinstance(c, dict):
            continue
        nome = c.get('nome', '')
        tipo = c.get('tipo')
        endereco = c.get('endereco') or c.get('valor', '')
        if tipo in ('telefone', 'whatsapp'):
            digits = re.sub(r'\D', '', endereco)
            endereco = format_phone(digits)
        contato = grouped.setdefault(nome, {'nome': nome, 'meios': []})
        contato['meios'].append({'tipo': tipo, 'endereco': endereco})
    return list(grouped.values())


def validate_contatos(contatos):
    """Validate contact data ensuring proper formats."""
    email_re = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
    for c in contatos:
        meios = c.get('meios')
        if meios is None:
            meios = [{'tipo': c.get('tipo'), 'endereco': c.get('endereco', '')}]
        validated = []
        for m in meios:
            tipo = m.get('tipo')
            endereco = m.get('endereco', '')
            if tipo == 'email':
                if not email_re.match(endereco):
                    raise ValueError(f"E-mail inválido: {endereco}")
            elif tipo in ('telefone', 'whatsapp'):
                digits = re.sub(r'\D', '', endereco)
                if not digits:
                    raise ValueError(f"Número inválido: {endereco}")
                endereco = format_phone(digits)
            validated.append({'tipo': tipo, 'endereco': endereco})
        c['meios'] = validated
        c.pop('tipo', None)
        c.pop('endereco', None)
    return contatos

@app.route('/upload_image', methods=['POST'])
@login_required
def upload_image():
    """Handle image uploads from the WYSIWYG editor."""
    if 'image' not in request.files:
        return jsonify({'error': 'Nenhuma imagem enviada'}), 400

    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'Nome de arquivo vazio'}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        unique_name = f"{uuid4().hex}_{filename}"
        upload_folder = os.path.join(current_app.root_path, 'static', 'uploads')
        file_path = os.path.join(upload_folder, unique_name)

        try:
            os.makedirs(upload_folder, exist_ok=True)
            file.save(file_path)
            file_url = url_for('static', filename=f'uploads/{unique_name}')
            return jsonify({'image_url': file_url})
        except Exception as e:
            return jsonify({'error': f'Erro no servidor ao salvar: {e}'}), 500

    return jsonify({'error': 'Arquivo inválido ou não permitido'}), 400

def admin_required(f):
    """Decorator that restricts access to admin users."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role != 'admin':
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    """Redirect users to the appropriate first page."""
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    return redirect(url_for('login'))


@app.route('/home')
@login_required
def home():
    """Render the authenticated home page."""
    return render_template('home.html')


@app.route('/consultorias')
@login_required
def consultorias():
    """List registered consultorias."""
    consultorias = Consultoria.query.all()
    return render_template('consultorias.html', consultorias=consultorias)


@app.route('/sala-reunioes')
@login_required
def sala_reunioes():
    """Display meeting room agenda via external system.

    Generates a signed token containing the current user's ID and name
    and appends it as a query parameter to the iframe source. The
    external calendar service can verify this token to identify who is
    creating events.
    """
    serializer = URLSafeSerializer(current_app.config['SECRET_KEY'])
    user_payload = {'id': current_user.id, 'name': current_user.name}
    token = serializer.dumps(user_payload)
    iframe_src = f"http://192.168.0.211:4000?token={token}"
    return render_template('sala_reunioes.html', iframe_src=iframe_src)


@app.route('/sala-reunioes/novo', methods=['GET', 'POST'])
@admin_required
def novo_evento():
    """Create a new meeting room event."""
    users = User.query.order_by(User.name).all()
    if request.method == 'POST':
        title = sanitize_html(request.form.get('title'))
        date_str = request.form.get('date')
        start_str = request.form.get('start_time')
        end_str = request.form.get('end_time')
        user_id = request.form.get('user_id')

        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else None
        start_time = (
            datetime.combine(date_obj, datetime.strptime(start_str, '%H:%M').time())
            if date_obj and start_str
            else None
        )
        end_time = (
            datetime.combine(date_obj, datetime.strptime(end_str, '%H:%M').time())
            if date_obj and end_str
            else None
        )
        event = MeetingRoomEvent(
            title=title,
            date=date_obj,
            start_time=start_time,
            end_time=end_time,
            user_id=int(user_id) if user_id else None,
        )
        db.session.add(event)
        db.session.commit()
        flash('Evento criado com sucesso.', 'success')
        return redirect(url_for('sala_reunioes'))
    return render_template('novo_evento.html', users=users)


@app.route('/consultorias/cadastro', methods=['GET', 'POST'])
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
        flash('Consultoria registrada com sucesso.', 'success')
        return redirect(url_for('consultorias'))
    return render_template('cadastro_consultoria.html', form=form)


@app.route('/consultorias/editar/<int:id>', methods=['GET', 'POST'])
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
        flash('Consultoria atualizada com sucesso.', 'success')
        return redirect(url_for('consultorias'))
    return render_template('cadastro_consultoria.html', form=form, consultoria=consultoria)

@app.route('/consultorias/setores')
@login_required
def setores():
    """List registered setores."""
    setores = Setor.query.all()
    return render_template('setores.html', setores=setores)


@app.route('/consultorias/setores/cadastro', methods=['GET', 'POST'])
@admin_required
def cadastro_setor():
    """Render and handle the Cadastro de Setor page."""
    form = SetorForm()
    if form.validate_on_submit():
        setor = Setor(nome=form.nome.data)
        db.session.add(setor)
        db.session.commit()
        flash('Setor registrado com sucesso.', 'success')
        return redirect(url_for('setores'))
    return render_template('cadastro_setor.html', form=form)


@app.route('/consultorias/setores/editar/<int:id>', methods=['GET', 'POST'])
@admin_required
def editar_setor(id):
    """Edit a registered setor."""
    setor = Setor.query.get_or_404(id)
    form = SetorForm(obj=setor)
    if form.validate_on_submit():
        setor.nome = form.nome.data
        db.session.commit()
        flash('Setor atualizado com sucesso.', 'success')
        return redirect(url_for('setores'))
    return render_template('cadastro_setor.html', form=form, setor=setor)


@app.route('/consultorias/relatorios')
@admin_required
def relatorios_consultorias():
    """Display reports of inclusões grouped by consultoria, user, and date."""
    inicio_raw = request.args.get('inicio')
    fim_raw = request.args.get('fim')
    query = Inclusao.query

    inicio = None
    if inicio_raw:
        try:
            inicio = datetime.strptime(inicio_raw, '%Y-%m-%d').date()
            query = query.filter(Inclusao.data >= inicio)
        except ValueError:
            inicio = None

    fim = None
    if fim_raw:
        try:
            fim = datetime.strptime(fim_raw, '%Y-%m-%d').date()
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

    labels_consultoria = [c or '—' for c, _ in por_consultoria]
    counts_consultoria = [total for _, total in por_consultoria]
    fig_cons = go.Figure(
        data=[
            go.Bar(x=labels_consultoria, y=counts_consultoria, marker_color=qualitative.Pastel)
        ]
    )
    fig_cons.update_layout(
        title_text="Inclusões por consultoria",
        template="seaborn",
        xaxis_title="Consultoria",
        yaxis_title="Total",
    )
    chart_consultoria = fig_cons.to_html(full_html=False, div_id='consultoria-chart')

    labels_usuario = [u or '—' for u, _ in por_usuario]
    counts_usuario = [total for _, total in por_usuario]
    fig_user = go.Figure(
        data=[go.Bar(x=labels_usuario, y=counts_usuario, marker_color=qualitative.Pastel)]
    )
    fig_user.update_layout(
        title_text="Inclusões por usuário",
        template="seaborn",
        xaxis_title="Usuário",
        yaxis_title="Total",
    )
    chart_usuario = fig_user.to_html(full_html=False, div_id='usuario-chart')

    inclusoes = query.all()
    inclusoes_por_consultoria = {}
    inclusoes_por_usuario = {}
    for inc in inclusoes:
        label_cons = inc.consultoria or '—'
        inclusoes_por_consultoria.setdefault(label_cons, []).append(
            {
                'usuario': inc.usuario,
                'pergunta': inc.pergunta,
                'data': inc.data.strftime('%d/%m/%Y') if inc.data else '',
            }
        )
        label_user = inc.usuario or '—'
        inclusoes_por_usuario.setdefault(label_user, []).append(
            {
                'consultoria': inc.consultoria,
                'pergunta': inc.pergunta,
                'data': inc.data.strftime('%d/%m/%Y') if inc.data else '',
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
        'relatorios_consultorias.html',
        chart_consultoria=chart_consultoria,
        chart_usuario=chart_usuario,
        inclusoes_por_consultoria=inclusoes_por_consultoria,
        inclusoes_por_usuario=inclusoes_por_usuario,
        por_data=por_data,
        inicio=inicio.strftime('%Y-%m-%d') if inicio else '',
        fim=fim.strftime('%Y-%m-%d') if fim else '',
    )


@app.route('/consultorias/inclusoes')
@login_required
def inclusoes():
    """List and search Consultorias."""
    search_raw = request.args.get('q', '')
    page = request.args.get('page', 1, type=int)
    query = Inclusao.query

    if search_raw:
        like = f"%{search_raw}%"
        query = query.filter(
            or_(
                Inclusao.assunto.ilike(like),
                Inclusao.usuario.ilike(like),
                Inclusao.consultoria.ilike(like),
                Inclusao.setor.ilike(like),
                Inclusao.pergunta.ilike(like),
                Inclusao.resposta.ilike(like),
            )
        )

    pagination = query.order_by(Inclusao.data.desc()).paginate(page=page, per_page=50)

    return render_template(
        'inclusoes.html',
        inclusoes=pagination.items,
        pagination=pagination,
        search=search_raw,
    )


@app.route('/consultorias/inclusoes/nova', methods=['GET', 'POST'])
@login_required
def nova_inclusao():
    """Render and handle Consultoria form."""
    users = User.query.order_by(User.name).all()
    if request.method == 'POST':
        user_id = request.form.get('usuario')
        user = User.query.get(int(user_id)) if user_id else None
        data_str = request.form.get('data')
        data = datetime.strptime(data_str, '%Y-%m-%d').date() if data_str else None
        inclusao = Inclusao(
            data=data,
            usuario=user.name if user else '',
            setor=request.form.get('setor'),
            consultoria=request.form.get('consultoria'),
            assunto=request.form.get('assunto'),
            pergunta=sanitize_html(request.form.get('pergunta')),
            resposta=sanitize_html(request.form.get('resposta')),
        )
        db.session.add(inclusao)
        db.session.commit()
        flash('Consultoria registrada com sucesso.', 'success')
        return redirect(url_for('inclusoes'))
    return render_template(
        'nova_inclusao.html',
        users=users,
        setores=Setor.query.order_by(Setor.nome).all(),
        consultorias=Consultoria.query.order_by(Consultoria.nome).all(),
    )


@app.route('/consultorias/inclusoes/<int:codigo>')
@login_required
def visualizar_consultoria(codigo):
    """Display details for a single consultoria."""
    inclusao = Inclusao.query.get_or_404(codigo)
    return render_template(
        'visualizar_consultoria.html',
        inclusao=inclusao,
        data_formatada=inclusao.data_formatada,
    )


@app.route('/consultorias/inclusoes/<int:codigo>/editar', methods=['GET', 'POST'])
@login_required
def editar_consultoria(codigo):
    """Render and handle editing of a consultoria."""
    inclusao = Inclusao.query.get_or_404(codigo)
    users = User.query.order_by(User.name).all()
    if request.method == 'POST':
        user_id = request.form.get('usuario')
        user = User.query.get(int(user_id)) if user_id else None
        data_str = request.form.get('data')
        inclusao.data = datetime.strptime(data_str, '%Y-%m-%d').date() if data_str else None
        inclusao.usuario = user.name if user else ''
        inclusao.setor = request.form.get('setor')
        inclusao.consultoria = request.form.get('consultoria')
        inclusao.assunto = request.form.get('assunto')
        inclusao.pergunta = sanitize_html(request.form.get('pergunta'))
        inclusao.resposta = sanitize_html(request.form.get('resposta'))
        db.session.commit()
        flash('Consultoria atualizada com sucesso.', 'success')
        return redirect(url_for('inclusoes'))
    return render_template(
        'nova_inclusao.html',
        users=users,
        setores=Setor.query.order_by(Setor.nome).all(),
        consultorias=Consultoria.query.order_by(Consultoria.nome).all(),
        inclusao=inclusao,
    )

@app.route('/cookies')
def cookies():
    """Render the cookie policy page."""
    return render_template('cookie_policy.html')


@app.route('/cookies/revoke')
def revoke_cookies():
    """Revoke cookie consent and redirect to index."""
    resp = redirect(url_for('index'))
    resp.delete_cookie('cookie_consent')
    flash('Consentimento de cookies revogado.', 'info')
    return resp

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Render the login page and handle authentication."""
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data):
            if not user.ativo:
                flash('Seu usuário está inativo. Contate o administrador.', 'danger')
                return redirect(url_for('login'))
            login_user(
                user,
                remember=form.remember_me.data,
                duration=timedelta(days=30),
            )
            session.permanent = form.remember_me.data
            flash('Login bem-sucedido!')
            return redirect(url_for('home'))
        else:
            flash('Credenciais inválidas', 'danger')
    return render_template('login.html', form=form)

@app.route('/dashboard')
@login_required
def dashboard():
    """Admin dashboard placeholder page."""
    return render_template('dashboard.html')

@app.route('/api/cnpj/<cnpj>')
@login_required
def api_cnpj(cnpj):
    """Provide a JSON API for CNPJ lookups."""
    try:
        dados = consultar_cnpj(cnpj)
    except ValueError as e:
        msg = str(e)
        status = 400 if 'inválido' in msg.lower() or 'invalido' in msg.lower() else 404
        if status == 404:
            msg = 'CNPJ não está cadastrado'
        return jsonify({'error': msg}), status
    except Exception:
        return jsonify({'error': 'Erro ao consultar CNPJ'}), 500
    if not dados:
        return jsonify({'error': 'CNPJ não está cadastrado'}), 404
    return jsonify(dados)

    ## Rota para cadastrar uma nova empresa

@app.route('/cadastrar_empresa', methods=['GET', 'POST'])
@login_required
def cadastrar_empresa():
    """Create a new company record."""
    form = EmpresaForm()
    if request.method == 'GET':
        form.sistemas_consultorias.data = form.sistemas_consultorias.data or []
        form.regime_lancamento.data = form.regime_lancamento.data or []
    if form.validate_on_submit():
        try:
            cnpj_limpo = re.sub(r'\D', '', form.cnpj.data)
            acessos_json = form.acessos_json.data or '[]'
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
                acessos=acessos
            )
            db.session.add(nova_empresa)
            db.session.commit()
            flash('Empresa cadastrada com sucesso!', 'success')
            return redirect(url_for('gerenciar_departamentos', empresa_id=nova_empresa.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar empresa: {e}', 'danger')
    else:
        print("Formulário não validado:")
        print(form.errors)

    return render_template('empresas/cadastrar.html', form=form)

@app.route('/listar_empresas')
@login_required
def listar_empresas():
    """List companies with optional search and pagination."""
    search = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 20
    query = Empresa.query

    if search:
        like_pattern = f"%{search}%"
        query = query.filter(
            or_(
                Empresa.nome_empresa.ilike(like_pattern),
                Empresa.codigo_empresa.ilike(like_pattern)
            )
        )

    sort = request.args.get('sort', 'nome')
    order = request.args.get('order', 'asc')

    if sort == 'codigo':
        order_column = Empresa.codigo_empresa
    else:
        order_column = Empresa.nome_empresa

    if order == 'desc':
        query = query.order_by(order_column.desc())
    else:
        query = query.order_by(order_column.asc())

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    empresas = pagination.items

    return render_template(
        'empresas/listar.html',
        empresas=empresas,
        pagination=pagination,
        search=search,
        sort=sort,
        order=order,
    )

def processar_dados_fiscal(request):
    """Função auxiliar para processar dados do departamento fiscal"""
    responsavel = request.form.get('responsavel')
    descricao = request.form.get('descricao')
    acessos_json = request.form.get('acessos_json', '[]')
    try:
        acessos = json.loads(acessos_json) if acessos_json else []
    except Exception:
        acessos = []
    forma_movimento = request.form.get('forma_movimento')
    observacao_movimento = request.form.get('observacao_movimento')
    observacao_importacao = request.form.get('observacao_importacao')
    observacao_contato = request.form.get('observacao_contato')
    particularidades = sanitize_html(request.form.get('particularidades'))
    formas_importacao_json = request.form.get('formas_importacao_json', '[]')
    formas_importacao = json.loads(formas_importacao_json) if formas_importacao_json else []
    envio_digital = request.form.getlist('envio_digital')
    envio_fisico = request.form.getlist('envio_fisico')
    malote_coleta = request.form.get('malote_coleta')
    contatos_json = request.form.get('contatos_json', 'null')
    contatos = json.loads(contatos_json) if contatos_json != 'null' else None
    if contatos is not None:
        contatos = validate_contatos(contatos)
    
    return {
        'responsavel': responsavel,
        'descricao': descricao,
        'formas_importacao': formas_importacao,
        'acessos': acessos,
        'forma_movimento': forma_movimento,
        'envio_digital': envio_digital,
        'envio_fisico': envio_fisico,
        'malote_coleta': malote_coleta,
        'observacao_movimento': observacao_movimento,
        'observacao_importacao': observacao_importacao,
        'observacao_contato': observacao_contato,
        'contatos': contatos,
        'particularidades_texto': particularidades
    }

def processar_dados_contabil(request):
    """Função auxiliar para processar dados do departamento contábil"""
    responsavel = request.form.get('responsavel')
    descricao = request.form.get('descricao')
    metodo_importacao = request.form.getlist('metodo_importacao')
    forma_movimento = request.form.get('forma_movimento')
    particularidades = sanitize_html(request.form.get('particularidades'))
    envio_digital = request.form.getlist('envio_digital')
    envio_fisico = request.form.getlist('envio_fisico')
    malote_coleta = request.form.get('malote_coleta')
    controle_relatorios_json = request.form.get('controle_relatorios_json', '[]')
    controle_relatorios = json.loads(controle_relatorios_json) if controle_relatorios_json else []
    observacao_movimento = request.form.get('observacao_movimento')
    observacao_controle_relatorios = request.form.get('observacao_controle_relatorios')
    
    return {
        'responsavel': responsavel,
        'descricao': descricao,
        'metodo_importacao': metodo_importacao,
        'forma_movimento': forma_movimento,
        'envio_digital': envio_digital,
        'envio_fisico': envio_fisico,
        'malote_coleta': malote_coleta,
        'controle_relatorios': controle_relatorios,
        'observacao_movimento': observacao_movimento,
        'observacao_controle_relatorios': observacao_controle_relatorios,
        'particularidades_texto': particularidades
    }

def processar_dados_pessoal(request):
    """Função auxiliar para processar dados do departamento pessoal"""
    return {
        'responsavel': request.form.get('responsavel'),
        'descricao': request.form.get('descricao'),
        'data_envio': request.form.get('data_envio'),
        'registro_funcionarios': request.form.get('registro_funcionarios'),
        'ponto_eletronico': request.form.get('ponto_eletronico'),
        'pagamento_funcionario': request.form.get('pagamento_funcionario'),
        'particularidades_texto': sanitize_html(request.form.get('particularidades'))
    }

def processar_dados_administrativo(request):
    """Função auxiliar para processar dados do departamento administrativo"""
    return {
        'particularidades_texto': sanitize_html(request.form.get('particularidades'))
    }

@app.route('/empresa/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_empresa(id):
    """Edit an existing company and its details."""
    empresa = Empresa.query.get_or_404(id)
    empresa_form = EmpresaForm(request.form, obj=empresa)

    if request.method == 'GET':
        empresa_form.sistemas_consultorias.data = empresa.sistemas_consultorias or []
        empresa_form.regime_lancamento.data = empresa.regime_lancamento or []
        empresa_form.acessos_json.data = json.dumps(empresa.acessos or [])

    if request.method == 'POST':
        if empresa_form.validate():
            empresa_form.populate_obj(empresa)
            empresa.cnpj = re.sub(r'\D', '', empresa_form.cnpj.data)
            empresa.sistemas_consultorias = empresa_form.sistemas_consultorias.data
            try:
                empresa.acessos = json.loads(empresa_form.acessos_json.data or '[]')
            except Exception:
                empresa.acessos = []
            db.session.add(empresa)
            try:
                db.session.commit()
                flash('Dados da Empresa salvos com sucesso!', 'success')
                return redirect(url_for('visualizar_empresa', id=id) + '#dados-empresa')
            except Exception as e:
                db.session.rollback()
                flash(f'Erro ao salvar: {str(e)}', 'danger')
        else:
            for field, errors in empresa_form.errors.items():
                for error in errors:
                    flash(f"Erro: {error}", 'danger')

    return render_template(
        'empresas/editar_empresa.html',
        empresa=empresa,
        empresa_form=empresa_form,
    )

@app.route('/empresa/visualizar/<int:id>')
@login_required
def visualizar_empresa(id):
    """Display a detailed view of a company."""
    from types import SimpleNamespace

    empresa = Empresa.query.get_or_404(id)

    # display para regime de lançamento
    empresa.regime_lancamento_display = empresa.regime_lancamento or []

    fiscal = Departamento.query.filter_by(empresa_id=id, tipo='Departamento Fiscal').first()
    contabil = Departamento.query.filter_by(empresa_id=id, tipo='Departamento Contábil').first()
    pessoal = Departamento.query.filter_by(empresa_id=id, tipo='Departamento Pessoal').first()
    administrativo = Departamento.query.filter_by(empresa_id=id, tipo='Departamento Administrativo').first()

    def _prepare_envio_fisico(departamento):
        if not departamento:
            return []
        try:
            lista = json.loads(departamento.envio_fisico) if isinstance(departamento.envio_fisico, str) else (departamento.envio_fisico or [])
        except Exception:
            lista = []
        if 'malote' in lista and getattr(departamento, 'malote_coleta', None):
            lista = ['Malote - ' + departamento.malote_coleta if item == 'malote' else item for item in lista]
        return lista

    # monta contatos_list
    if fiscal and getattr(fiscal, "contatos", None):
        try:
            contatos_list = json.loads(fiscal.contatos) if isinstance(fiscal.contatos, str) else fiscal.contatos
        except Exception:
            contatos_list = []
    else:
        contatos_list = []
    contatos_list = normalize_contatos(contatos_list)

    # fiscal_view: garante objeto mesmo quando fiscal é None
    if fiscal is None:
        fiscal_view = SimpleNamespace(formas_importacao=[], contatos_list=contatos_list, envio_fisico=[])
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

    return render_template(
        'empresas/visualizar.html',
        empresa=empresa,
        fiscal=fiscal_view,
        contabil=contabil,
        pessoal=pessoal,
        administrativo=administrativo
    )
    
    ## Rota para gerenciar departamentos de uma empresa

@app.route('/empresa/<int:empresa_id>/departamentos', methods=['GET', 'POST'])
@login_required
def gerenciar_departamentos(empresa_id):
    """Create or update department data for a company."""
    empresa = Empresa.query.get_or_404(empresa_id)

    fiscal = Departamento.query.filter_by(empresa_id=empresa_id, tipo='Departamento Fiscal').first()
    contabil = Departamento.query.filter_by(empresa_id=empresa_id, tipo='Departamento Contábil').first()
    pessoal = Departamento.query.filter_by(empresa_id=empresa_id, tipo='Departamento Pessoal').first()
    administrativo = Departamento.query.filter_by(empresa_id=empresa_id, tipo='Departamento Administrativo').first()
    
    fiscal_form = DepartamentoFiscalForm(request.form, obj=fiscal)
    contabil_form = DepartamentoContabilForm(request.form, obj=contabil)
    pessoal_form = DepartamentoPessoalForm(request.form, obj=pessoal)
    administrativo_form = DepartamentoAdministrativoForm(request.form, obj=administrativo)
    
    if request.method == 'GET':
        fiscal_form = DepartamentoFiscalForm(obj=fiscal)
        if fiscal:
            fiscal_form.envio_digital.data = (
                fiscal.envio_digital if isinstance(fiscal.envio_digital, list)
                else json.loads(fiscal.envio_digital) if fiscal.envio_digital else []
            )
            fiscal_form.envio_fisico.data = (
                fiscal.envio_fisico if isinstance(fiscal.envio_fisico, list)
                else json.loads(fiscal.envio_fisico) if fiscal.envio_fisico else []
            )

            if fiscal.contatos:
                try:
                    contatos_list = json.loads(fiscal.contatos) if isinstance(fiscal.contatos, str) else fiscal.contatos
                except Exception:
                    contatos_list = []
            else:
                contatos_list = []
            contatos_list = normalize_contatos(contatos_list)
            fiscal_form.contatos_json.data = json.dumps(contatos_list)


        contabil_form = DepartamentoContabilForm(obj=contabil)
        if contabil:
            contabil_form.metodo_importacao.data = (
                contabil.metodo_importacao if isinstance(contabil.metodo_importacao, list)
                else json.loads(contabil.metodo_importacao) if contabil.metodo_importacao else []
            )
            contabil_form.envio_digital.data = (
                contabil.envio_digital if isinstance(contabil.envio_digital, list)
                else json.loads(contabil.envio_digital) if contabil.envio_digital else []
            )
            contabil_form.envio_fisico.data = (
                contabil.envio_fisico if isinstance(contabil.envio_fisico, list)
                else json.loads(contabil.envio_fisico) if contabil.envio_fisico else []
            )
            contabil_form.controle_relatorios.data = (
                contabil.controle_relatorios if isinstance(contabil.controle_relatorios, list)
                else json.loads(contabil.controle_relatorios) if contabil.controle_relatorios else []
            )

    form_type = request.form.get('form_type')

    if request.method == 'POST':
        form_processed_successfully = False

        if form_type == 'fiscal' and fiscal_form.validate():
            if not fiscal:
                fiscal = Departamento(empresa_id=empresa_id, tipo='Departamento Fiscal')
                db.session.add(fiscal)

            fiscal_form.populate_obj(fiscal)
            if 'malote' not in (fiscal_form.envio_fisico.data or []):
                fiscal.malote_coleta = None
            else:
                fiscal.malote_coleta = fiscal_form.malote_coleta.data
            try:
                fiscal.contatos = json.loads(fiscal_form.contatos_json.data or '[]')
            except Exception:
                fiscal.contatos = []
            flash('Departamento Fiscal salvo com sucesso!', 'success')
            form_processed_successfully = True

        elif form_type == 'contabil' and contabil_form.validate():
            if not contabil:
                contabil = Departamento(empresa_id=empresa_id, tipo='Departamento Contábil')
                db.session.add(contabil)

            contabil_form.populate_obj(contabil)
            if 'malote' not in (contabil_form.envio_fisico.data or []):
                contabil.malote_coleta = None
            else:
                contabil.malote_coleta = contabil_form.malote_coleta.data

            contabil.metodo_importacao = contabil_form.metodo_importacao.data or []
            contabil.envio_digital = contabil_form.envio_digital.data or []
            contabil.envio_fisico = contabil_form.envio_fisico.data or []
            contabil.controle_relatorios = contabil_form.controle_relatorios.data or []
            
            flash('Departamento Contábil salvo com sucesso!', 'success')
            form_processed_successfully = True

        elif form_type == 'pessoal' and pessoal_form.validate():
            if not pessoal:
                pessoal = Departamento(empresa_id=empresa_id, tipo='Departamento Pessoal')
                db.session.add(pessoal)

            pessoal_form.populate_obj(pessoal)
            flash('Departamento Pessoal salvo com sucesso!', 'success')
            form_processed_successfully = True
        
        elif form_type == 'administrativo' and administrativo_form.validate():
            if not administrativo:
                administrativo = Departamento(empresa_id=empresa_id, tipo='Departamento Administrativo')
                db.session.add(administrativo)
            
            administrativo_form.populate_obj(administrativo)
            flash('Departamento Administrativo salvo com sucesso!', 'success')
            form_processed_successfully = True

        if form_processed_successfully:
            try:
                db.session.commit()

                hash_ancoras = {
                    'fiscal': 'fiscal',
                    'contabil': 'contabil',
                    'pessoal': 'pessoal',
                    'administrativo': 'administrativo'
                }
                hash_ancora = hash_ancoras.get(form_type, '')

                return redirect(url_for('visualizar_empresa', id=empresa_id) + f'#{hash_ancora}')

            except Exception as e:
                db.session.rollback()
                flash(f'Ocorreu um erro ao salvar: {str(e)}', 'danger')
        
        else:
            active_form = {
                'fiscal': fiscal_form, 
                'contabil': contabil_form, 
                'pessoal': pessoal_form,
                'administrativo': administrativo_form
            }.get(form_type)
            if active_form and active_form.errors:
                for field, errors in active_form.errors.items():
                    for error in errors:
                        flash(f"Erro no formulário {form_type.capitalize()}: {error}", 'danger')

    return render_template(
        'empresas/departamentos.html',
        empresa=empresa,
        fiscal_form=fiscal_form,
        contabil_form=contabil_form,
        pessoal_form=pessoal_form,
        administrativo_form=administrativo_form,
        fiscal=fiscal,
        contabil=contabil,
        pessoal=pessoal,
        administrativo=administrativo
    )

@app.route('/relatorios')
@admin_required
def relatorios():
    """Render the reports landing page."""
    return render_template('admin/relatorios.html')


@app.route('/relatorio_empresas')
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

    categorias = ['Simples Nacional', 'Lucro Presumido', 'Lucro Real']
    grouped = {cat: [] for cat in categorias}
    grouped_sistemas = {}

    for nome, cnpj, codigo, trib, sistema in empresas:
        label = trib if trib in categorias else 'Outros'
        grouped.setdefault(label, []).append(
            {"nome": nome, "cnpj": cnpj, "codigo": codigo}
        )

        sistema_label = sistema.strip() if sistema else 'Não informado'
        grouped_sistemas.setdefault(sistema_label, []).append(
            {"nome": nome, "cnpj": cnpj, "codigo": codigo}
        )

    labels = list(grouped.keys())
    counts = [len(grouped[l]) for l in labels]
    fig = go.Figure(
        data=[go.Bar(x=labels, y=counts, marker_color=qualitative.Pastel)]
    )
    fig.update_layout(
        title_text="Empresas por regime de tributação",
        template="seaborn",
        xaxis_title="Regime",
        yaxis_title="Quantidade",
    )
    chart_div = fig.to_html(full_html=False, div_id='empresa-tributacao-chart')

    sistema_labels = list(grouped_sistemas.keys())
    sistema_counts = [len(grouped_sistemas[l]) for l in sistema_labels]
    fig_sistemas = go.Figure(
        data=[go.Bar(x=sistema_labels, y=sistema_counts, marker_color=qualitative.Pastel)]
    )
    fig_sistemas.update_layout(
        title_text="Empresas por sistema utilizado",
        template="seaborn",
        xaxis_title="Sistema",
        yaxis_title="Quantidade",
    )
    chart_div_sistema = fig_sistemas.to_html(
        full_html=False, div_id='empresa-sistema-chart'
    )

    return render_template(
        'admin/relatorio_empresas.html',
        chart_div=chart_div,
        empresas_por_slice=grouped,
        chart_div_sistema=chart_div_sistema,
        empresas_por_sistema=grouped_sistemas,
    )


@app.route('/relatorio_fiscal')
@admin_required
def relatorio_fiscal():
    """Show summary charts for the fiscal department."""
    departamentos = (
        Departamento.query.filter_by(tipo='Departamento Fiscal')
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
        formas_list = (
            json.loads(formas)
            if isinstance(formas, str)
            else (formas or [])
        )
        for f in formas_list:
            label = choice_map.get(f, f)
            import_grouped.setdefault(label, []).append(
                {"nome": nome, "codigo": codigo}
            )
        label_envio = envio if envio else 'Não informado'
        envio_grouped.setdefault(label_envio, []).append(
            {"nome": nome, "codigo": codigo}
        )
        if envio in ('Fisico', 'Digital e Físico'):
            label_malote = malote if malote else 'Não informado'
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
    import_chart = fig_imp.to_html(
        full_html=False, div_id='fiscal-importacao-chart'
    )
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
    envio_chart = fig_env.to_html(full_html=False, div_id='fiscal-envio-chart')
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
    malote_chart = fig_mal.to_html(full_html=False, div_id='fiscal-malote-chart')
    return render_template(
        'admin/relatorio_fiscal.html',
        importacao_chart=import_chart,
        envio_chart=envio_chart,
        malote_chart=malote_chart,
        empresas_por_import=import_grouped,
        empresas_por_envio=envio_grouped,
        empresas_por_malote=malote_grouped,
    )


@app.route('/relatorio_contabil')
@admin_required
def relatorio_contabil():
    """Show summary charts for the accounting department."""
    departamentos = (
        Departamento.query.filter_by(tipo='Departamento Contábil')
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
        metodo_list = (
            json.loads(metodo) if isinstance(metodo, str) else (metodo or [])
        )
        for m in metodo_list:
            label = metodo_map.get(m, m)
            import_grouped.setdefault(label, []).append({"nome": nome, "codigo": codigo})
        label_envio = envio if envio else 'Não informado'
        envio_grouped.setdefault(label_envio, []).append({"nome": nome, "codigo": codigo})
        if envio in ('Fisico', 'Digital e Físico'):
            label_malote = malote if malote else 'Não informado'
            malote_grouped.setdefault(label_malote, []).append({"nome": nome, "codigo": codigo})
        rel_list = (
            json.loads(relatorios) if isinstance(relatorios, str) else (relatorios or [])
        )
        for r in rel_list:
            label = relatorio_map.get(r, r)
            relatorios_grouped.setdefault(label, []).append({"nome": nome, "codigo": codigo})
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
    import_chart = fig_imp.to_html(
        full_html=False, div_id='contabil-importacao-chart'
    )
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
    envio_chart = fig_env.to_html(full_html=False, div_id='contabil-envio-chart')
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
    malote_chart = fig_mal.to_html(full_html=False, div_id='contabil-malote-chart')
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
    relatorios_chart = fig_rel.to_html(full_html=False, div_id='contabil-relatorios-chart')
    return render_template(
        'admin/relatorio_contabil.html',
        importacao_chart=import_chart,
        envio_chart=envio_chart,
        malote_chart=malote_chart,
        relatorios_chart=relatorios_chart,
        empresas_por_import=import_grouped,
        empresas_por_envio=envio_grouped,
        empresas_por_malote=malote_grouped,
        empresas_por_relatorios=relatorios_grouped,
    )

@app.route('/relatorio_usuarios')
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
        tipo = 'Admin' if role == 'admin' else 'Usuário'
        status = 'Ativo' if ativo else 'Inativo'
        label = f'{tipo} {status}'
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
                marker=dict(colors=qualitative.Pastel, line=dict(color="#FFFFFF", width=2)),
                textinfo="label+percent",
            )
        ]
    )
    fig.update_layout(
        title_text="Usuários por tipo e status",
        template="seaborn",
        legend=dict(orientation="h", y=-0.1, x=0.5, xanchor="center"),
    )
    chart_div = fig.to_html(full_html=False, div_id='user-role-chart')
    return render_template(
        'admin/relatorio_usuarios.html',
        chart_div=chart_div,
        users_by_slice=grouped,
    )

@app.route('/logout', methods=['GET'])
@login_required
def logout():
    """Log out the current user."""
    logout_user()
    return redirect(url_for('index'))

@app.route('/users', methods=['GET', 'POST'])
@admin_required
def list_users():
    """List and register users in the admin panel."""
    form = RegistrationForm()
    show_inactive = request.args.get('show_inactive') in ('1', 'on')

    if form.validate_on_submit():
        existing_user = User.query.filter(
            (User.username == form.username.data) | (User.email == form.email.data)
        ).first()
        if existing_user:
            flash('Usuário ou email já cadastrado.', 'warning')
        else:
            user = User(
                username=form.username.data,
                email=form.email.data,
                name=form.name.data,
                role=form.role.data
            )
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.commit()
            flash('Novo usuário cadastrado com sucesso!', 'success')
        return redirect(url_for('list_users'))

    users_query = User.query
    if not show_inactive:
        users_query = users_query.filter_by(ativo=True)
    users = users_query.order_by(User.ativo.desc(), User.name).all()
    return render_template('list_users.html', users=users, form=form, show_inactive=show_inactive)


@app.route('/admin/online-users')
@admin_required
def online_users():
    """List users active within the last five minutes."""
    cutoff = datetime.utcnow() - timedelta(minutes=5)
    users = User.query.filter(User.last_seen >= cutoff).order_by(User.name).all()
    return render_template('admin/online_users.html', users=users)

@app.route('/novo_usuario', methods=['GET', 'POST'])
@admin_required
def novo_usuario():
    """Create a new user from the admin interface."""
    form = RegistrationForm()
    if form.validate_on_submit():
        existing_user = User.query.filter(
            (User.username == form.username.data) | (User.email == form.email.data)
        ).first()
        if existing_user:
            flash('Usuário ou email já cadastrado.', 'warning')
        else:
            user = User(
                username=form.username.data,
                email=form.email.data,
                name=form.name.data,
                role=form.role.data
            )
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.commit()
            flash('Novo usuário cadastrado com sucesso!', 'success')
            return redirect(url_for('list_users'))
    return render_template('admin/novo_usuario.html', form=form)

@app.route('/user/edit/<int:user_id>', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    """Edit an existing user."""
    user = User.query.get_or_404(user_id)
    form = EditUserForm(obj=user)

    if form.validate_on_submit():
        user.username = form.username.data
        user.email = form.email.data
        user.name = form.name.data
        user.role = form.role.data
        user.ativo = form.ativo.data

        # Process optional password change
        new_password = request.form.get('new_password')
        confirm_new_password = request.form.get('confirm_new_password')
        if new_password:
            if new_password != confirm_new_password:
                flash('As senhas devem ser iguais.', 'danger')
                return redirect(url_for('edit_user', user_id=user.id))
            user.set_password(new_password)

        db.session.commit()
        flash('Usuário atualizado com sucesso!', 'success')
        return redirect(url_for('list_users'))

    return render_template('edit_user.html', form=form)

