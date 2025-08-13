from flask import render_template, redirect, url_for, flash, request, abort, jsonify, current_app, Flask
from functools import wraps
from flask_login import current_user, login_required, login_user, logout_user, current_user
from app import app, db
from app.loginForms import LoginForm, RegistrationForm
from app.models.tables import User, Empresa, Departamento
from app.forms import (
    EmpresaForm,
    EditUserForm,
    DepartamentoForm,
    DepartamentoFiscalForm,
    DepartamentoContabilForm,
    DepartamentoPessoalForm
)
from datetime import datetime
import os, json, re
from werkzeug.utils import secure_filename
from uuid import uuid4
from app.forms import DepartamentoFiscalForm, DepartamentoContabilForm, DepartamentoPessoalForm
from sqlalchemy import or_

@app.context_processor
def inject_stats():
    if current_user.is_authenticated:
        total_empresas = Empresa.query.count()
        total_usuarios = User.query.count() if current_user.role == 'admin' else 0
        return {
            'total_empresas': total_empresas,
            'total_usuarios': total_usuarios
        }
    return {}

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def format_phone(digits: str) -> str:
    if len(digits) >= 11:
        return f"({digits[:2]}) {digits[2:7]}-{digits[7:11]}"
    if len(digits) >= 10:
        return f"({digits[:2]}) {digits[2:6]}-{digits[6:10]}"
    return digits


def normalize_contatos(contatos):
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

    ## Rota para upload de imagens

@app.route('/upload_image', methods=['POST'])
@login_required
def upload_image():
    print("--- Rota /upload_image foi chamada! ---")

    if 'image' not in request.files:
        print("ERRO: 'image' não está no request.files.")
        return jsonify({'error': 'Nenhuma imagem enviada'}), 400

    file = request.files['image']
    print(f"Arquivo recebido: {file.filename}")

    if file.filename == '':
        print("ERRO: Nome de arquivo vazio.")
        return jsonify({'error': 'Nome de arquivo vazio'}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        unique_name = f"{uuid4().hex}_{filename}"
        
        upload_folder = os.path.join(current_app.root_path, 'static', 'uploads')
        file_path = os.path.join(upload_folder, unique_name)
        print(f"Tentando salvar em: {file_path}")

        try:
            os.makedirs(upload_folder, exist_ok=True)
            file.save(file_path)
            print("SUCESSO: file.save() executado sem erros!")

            file_url = url_for('static', filename=f'uploads/{unique_name}', _external=True)
            return jsonify({'image_url': file_url})

        except Exception as e:
            print(f"!!! ERRO AO SALVAR O ARQUIVO: {e} !!!")
            return jsonify({'error': f'Erro no servidor ao salvar: {e}'}), 500

    print(f"ERRO: Arquivo não permitido. Nome: {file.filename}")
    return jsonify({'error': 'Arquivo inválido ou não permitido'}), 400

def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role != 'admin':
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data):
            if not user.ativo:
                flash('Seu usuário está inativo. Contate o administrador.', 'danger')
                return redirect(url_for('login'))
            login_user(user, remember=form.remember_me.data)
            flash('Login bem-sucedido!')
            return redirect(url_for('dashboard'))
        else:
            flash('Credenciais inválidas', 'danger')
    return render_template('login.html', form=form)

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

    ## Rota para cadastrar uma nova empresa

@app.route('/cadastrar_empresa', methods=['GET', 'POST'])
@login_required
def cadastrar_empresa():
    form = EmpresaForm()
    if request.method == 'GET':
        form.sistemas_consultorias.data = form.sistemas_consultorias.data or []
    if form.validate_on_submit():
        try:
            cnpj_limpo = re.sub(r'\D', '', form.cnpj.data)
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
                sistema_utilizado=form.sistema_utilizado.data
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
    search = request.args.get('q', '').strip()
    query = Empresa.query

    if search:
        like_pattern = f"%{search}%"
        query = query.filter(
            or_(
                Empresa.nome_empresa.ilike(like_pattern),
                Empresa.codigo_empresa.ilike(like_pattern)
            )
        )

    empresas = query.all()

    for empresa in empresas:
        if empresa.data_abertura and isinstance(empresa.data_abertura, str):
            try:
                empresa.data_abertura = datetime.strptime(empresa.data_abertura, '%d-%m-%Y')
            except ValueError:
                empresa.data_abertura = None

    return render_template('empresas/listar.html', empresas=empresas, search=search)

def processar_dados_fiscal(request):
    """Função auxiliar para processar dados do departamento fiscal"""
    responsavel = request.form.get('responsavel')
    descricao = request.form.get('descricao')
    links_prefeitura_json = request.form.get('links_prefeitura_json', '[]')
    try:
        links_prefeitura = json.loads(links_prefeitura_json) if links_prefeitura_json else []
    except Exception:
        links_prefeitura = []
    forma_movimento = request.form.get('forma_movimento')
    observacao_movimento = request.form.get('observacao_movimento')
    particularidades = request.form.get('particularidades')
    formas_importacao_json = request.form.get('formas_importacao_json', '[]')
    formas_importacao = json.loads(formas_importacao_json) if formas_importacao_json else []
    envio_digital_json = request.form.get('envio_digital_json', '[]')
    envio_digital = json.loads(envio_digital_json) if envio_digital_json else []
    envio_digital_fisico_json = request.form.get('envio_digital_fisico_json', '[]')
    envio_digital_fisico = json.loads(envio_digital_fisico_json) if envio_digital_fisico_json else []
    contatos_json = request.form.get('contatos_json', 'null')
    contatos = json.loads(contatos_json) if contatos_json != 'null' else None
    if contatos is not None:
        contatos = validate_contatos(contatos)
    
    return {
        'responsavel': responsavel,
        'descricao': descricao,
        'formas_importacao': formas_importacao,
        'links_prefeitura': links_prefeitura,
        'forma_movimento': forma_movimento,
        'envio_digital': envio_digital,
        'envio_digital_fisico': envio_digital_fisico,
        'observacao_movimento': observacao_movimento,
        'contatos': contatos,
        'particularidades_texto': particularidades
    }

def processar_dados_contabil(request):
    """Função auxiliar para processar dados do departamento contábil"""
    responsavel = request.form.get('responsavel')
    descricao = request.form.get('descricao')
    metodo_importacao = request.form.get('metodo_importacao')
    forma_movimento = request.form.get('forma_movimento')
    observacao_movimento = request.form.get('observacao_movimento')
    observacao_controle_relatorios = request.form.get('observacao_controle_relatorios')
    particularidades = request.form.get('particularidades')
    envio_digital_json = request.form.get('envio_digital_json', '[]')
    envio_digital = json.loads(envio_digital_json) if envio_digital_json else []    
    envio_digital_fisico_json = request.form.get('envio_digital_fisico_json', '[]')
    envio_digital_fisico = json.loads(envio_digital_fisico_json) if envio_digital_fisico_json else []    
    controle_relatorios_json = request.form.get('controle_relatorios_json', '[]')
    controle_relatorios = json.loads(controle_relatorios_json) if controle_relatorios_json else []
    
    return {
        'responsavel': responsavel,
        'descricao': descricao,
        'metodo_importacao': metodo_importacao,
        'forma_movimento': forma_movimento,
        'envio_digital': envio_digital,
        'envio_digital_fisico': envio_digital_fisico,
        'observacao_movimento': observacao_movimento,
        'controle_relatorios': controle_relatorios,
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
        'particularidades_texto': request.form.get('particularidades')
    }

def processar_dados_administrativo(request):
    """Função auxiliar para processar dados do departamento administrativo"""
    return {
        'responsavel': request.form.get('responsavel'),
        'descricao': request.form.get('descricao')
    }

@app.route('/empresa/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_empresa(id):
    empresa = Empresa.query.get_or_404(id)
    fiscal = Departamento.query.filter_by(empresa_id=id, tipo='Departamento Fiscal').first()
    contabil = Departamento.query.filter_by(empresa_id=id, tipo='Departamento Contábil').first()
    pessoal = Departamento.query.filter_by(empresa_id=id, tipo='Departamento Pessoal').first()
    administrativo = Departamento.query.filter_by(empresa_id=id, tipo='Departamento Administrativo').first()

    empresa_form = EmpresaForm(request.form, obj=empresa)
    fiscal_form = DepartamentoFiscalForm(request.form, obj=fiscal)
    contabil_form = DepartamentoContabilForm(request.form, obj=contabil)
    pessoal_form = DepartamentoPessoalForm(request.form, obj=pessoal)
    administrativo_form = DepartamentoForm(request.form, obj=administrativo)

    if request.method == 'GET':
        if empresa:
            empresa_form.sistemas_consultorias.data = empresa.sistemas_consultorias or []
        if empresa:
            empresa_form.regime_lancamento.data = empresa.regime_lancamento.value

        if fiscal:
            fiscal_form.formas_importacao.data = fiscal.formas_importacao or []
            fiscal_form.envio_digital.data = fiscal.envio_digital or []
            fiscal_form.envio_digital_fisico.data = fiscal.envio_digital_fisico or []

            if fiscal.links_prefeitura:
                try:
                    if isinstance(fiscal.links_prefeitura, str):
                        prefeituras_list = json.loads(fiscal.links_prefeitura)
                    else:
                        prefeituras_list = fiscal.links_prefeitura
                except Exception:
                    prefeituras_list = []
            else:
                prefeituras_list = []
            if not prefeituras_list and (
                getattr(fiscal, 'link_prefeitura', None) or
                getattr(fiscal, 'usuario_prefeitura', None) or
                getattr(fiscal, 'senha_prefeitura', None)
            ):
                prefeituras_list = [{
                    'cidade': '',
                    'link': getattr(fiscal, 'link_prefeitura', '') or '',
                    'usuario': getattr(fiscal, 'usuario_prefeitura', '') or '',
                    'senha': getattr(fiscal, 'senha_prefeitura', '') or ''
                }]
            fiscal_form.links_prefeitura_json.data = json.dumps(prefeituras_list)

            if fiscal.contatos:
                try:
                    if isinstance(fiscal.contatos, str):
                        contatos_list = json.loads(fiscal.contatos)
                    else:
                        contatos_list = fiscal.contatos
                except Exception:
                    contatos_list = []
            else:
                contatos_list = []
            contatos_list = normalize_contatos(contatos_list)
            fiscal_form.contatos_json.data = json.dumps(contatos_list)
            
        if contabil:
            contabil_form.envio_digital.data = contabil.envio_digital or []
            contabil_form.envio_digital_fisico.data = contabil.envio_digital_fisico or []
            contabil_form.controle_relatorios.data = contabil.controle_relatorios or []
            
        if pessoal:
            pessoal_form.data_envio.data = pessoal.data_envio or ''
            pessoal_form.registro_funcionarios.data = pessoal.registro_funcionarios or ''
            pessoal_form.ponto_eletronico.data = pessoal.ponto_eletronico or ''
            pessoal_form.pagamento_funcionario.data = pessoal.pagamento_funcionario or ''
            
        if administrativo:
            administrativo_form.responsavel.data = administrativo.responsavel or ''
            administrativo_form.descricao.data = administrativo.descricao or ''

    if request.method == 'POST':
        form_map = {
            'empresa': (empresa_form, empresa),
            'fiscal': (fiscal_form, fiscal or Departamento(empresa_id=id, tipo='Departamento Fiscal')),
            'contabil': (contabil_form, contabil or Departamento(empresa_id=id, tipo='Departamento Contábil')),
            'pessoal': (pessoal_form, pessoal or Departamento(empresa_id=id, tipo='Departamento Pessoal')),
            'administrativo': (administrativo_form, administrativo or Departamento(empresa_id=id, tipo='Departamento Administrativo'))
        }
        
        form_type = request.form.get('form_type')
        if form_type in form_map:
            form, obj = form_map[form_type]
            if form.validate():
                form.populate_obj(obj)
                if form_type == 'empresa':
                    obj.cnpj = re.sub(r'\D', '', form.cnpj.data)
                    obj.sistemas_consultorias = form.sistemas_consultorias.data
                elif form_type == 'fiscal':
                    try:
                        contatos_data = json.loads(form.contatos_json.data or '[]')
                        contatos_data = validate_contatos(contatos_data)
                    except Exception as e:
                        flash(f'Erro nos contatos: {e}', 'danger')
                        contatos_data = []
                    obj.contatos = contatos_data
                    try:
                        obj.links_prefeitura = json.loads(form.links_prefeitura_json.data or '[]')
                    except Exception:
                        obj.links_prefeitura = []
                db.session.add(obj)
                try:
                    db.session.commit()
                    flash(f'Dados de "{form_type.capitalize()}" salvos com sucesso!', 'success')
                    return redirect(url_for('editar_empresa', id=id))
                except Exception as e:
                    db.session.rollback()
                    flash(f'Erro ao salvar: {str(e)}', 'danger')
            else:
                for field, errors in form.errors.items():
                    for error in errors:
                        flash(f"Erro no formulário '{form_type.capitalize()}': {error}", 'danger')
        else:
            flash('Tipo de formulário inválido', 'danger')

    return render_template(
        'empresas/editar_empresa.html',
        empresa=empresa, empresa_form=empresa_form, fiscal_form=fiscal_form,
        contabil_form=contabil_form, pessoal_form=pessoal_form, administrativo_form=administrativo_form,
        fiscal=fiscal, contabil=contabil, pessoal=pessoal, administrativo=administrativo
    )

@app.route('/empresa/visualizar/<int:id>')
@login_required
def visualizar_empresa(id):
    from types import SimpleNamespace

    empresa = Empresa.query.get_or_404(id)

    # display para enum (ou None)
    empresa.regime_lancamento_display = (
        empresa.regime_lancamento.value if empresa.regime_lancamento else None
    )

    fiscal = Departamento.query.filter_by(empresa_id=id, tipo='Departamento Fiscal').first()
    contabil = Departamento.query.filter_by(empresa_id=id, tipo='Departamento Contábil').first()
    pessoal = Departamento.query.filter_by(empresa_id=id, tipo='Departamento Pessoal').first()
    administrativo = Departamento.query.filter_by(empresa_id=id, tipo='Departamento Administrativo').first()

    # monta contatos_list
    if fiscal and getattr(fiscal, "contatos", None):
        try:
            contatos_list = json.loads(fiscal.contatos) if isinstance(fiscal.contatos, str) else fiscal.contatos
        except Exception:
            contatos_list = []
    else:
        contatos_list = []
    contatos_list = normalize_contatos(contatos_list)

    # monta links_prefeitura
    if fiscal and getattr(fiscal, "links_prefeitura", None):
        try:
            prefeituras_list = json.loads(fiscal.links_prefeitura) if isinstance(fiscal.links_prefeitura, str) else fiscal.links_prefeitura
        except Exception:
            prefeituras_list = []
    else:
        prefeituras_list = []

    # fiscal_view: garante objeto mesmo quando fiscal é None
    if fiscal is None:
        fiscal_view = SimpleNamespace(formas_importacao=[], contatos_list=contatos_list, links_prefeitura=prefeituras_list)
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
        setattr(fiscal_view, "links_prefeitura", prefeituras_list)

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
    empresa = Empresa.query.get_or_404(empresa_id)

    fiscal = Departamento.query.filter_by(empresa_id=empresa_id, tipo='Departamento Fiscal').first()
    contabil = Departamento.query.filter_by(empresa_id=empresa_id, tipo='Departamento Contábil').first()
    pessoal = Departamento.query.filter_by(empresa_id=empresa_id, tipo='Departamento Pessoal').first()
    administrativo = Departamento.query.filter_by(empresa_id=empresa_id, tipo='Departamento Administrativo').first()
    
    fiscal_form = DepartamentoFiscalForm(request.form, obj=fiscal)
    contabil_form = DepartamentoContabilForm(request.form, obj=contabil)
    pessoal_form = DepartamentoPessoalForm(request.form, obj=pessoal)
    administrativo_form = DepartamentoForm(request.form, obj=administrativo)
    
    if request.method == 'GET':
        fiscal_form = DepartamentoFiscalForm(obj=fiscal)
        if fiscal and fiscal.contatos:
            try:
                contatos_list = json.loads(fiscal.contatos) if isinstance(fiscal.contatos, str) else fiscal.contatos
            except Exception:
                contatos_list = []
        else:
            contatos_list = []
        contatos_list = normalize_contatos(contatos_list)
        fiscal_form.contatos_json.data = json.dumps(contatos_list)

        if fiscal and fiscal.links_prefeitura:
            try:
                prefeituras_list = json.loads(fiscal.links_prefeitura) if isinstance(fiscal.links_prefeitura, str) else fiscal.links_prefeitura
            except Exception:
                prefeituras_list = []
        else:
            prefeituras_list = []
        if not prefeituras_list and (
            getattr(fiscal, 'link_prefeitura', None) or
            getattr(fiscal, 'usuario_prefeitura', None) or
            getattr(fiscal, 'senha_prefeitura', None)
        ):
            prefeituras_list = [{
                'cidade': '',
                'link': getattr(fiscal, 'link_prefeitura', '') or '',
                'usuario': getattr(fiscal, 'usuario_prefeitura', '') or '',
                'senha': getattr(fiscal, 'senha_prefeitura', '') or ''
            }]
        fiscal_form.links_prefeitura_json.data = json.dumps(prefeituras_list)

        contabil_form = DepartamentoContabilForm(obj=contabil)
        if contabil:
            try:
                contabil_form.envio_digital.data = json.loads(contabil.envio_digital) if contabil.envio_digital else []
            except Exception:
                contabil_form.envio_digital.data = []
            
            try:
                contabil_form.envio_digital_fisico.data = json.loads(contabil.envio_digital_fisico) if contabil.envio_digital_fisico else []
            except Exception:
                contabil_form.envio_digital_fisico.data = []
            
            try:
                contabil_form.controle_relatorios.data = json.loads(contabil.controle_relatorios) if contabil.controle_relatorios else []
            except Exception:
                contabil_form.controle_relatorios.data = []

    form_type = request.form.get('form_type')

    if request.method == 'POST':
        form_processed_successfully = False

        if form_type == 'fiscal' and fiscal_form.validate():
            if not fiscal:
                fiscal = Departamento(empresa_id=empresa_id, tipo='Departamento Fiscal')
                db.session.add(fiscal)

            fiscal_form.populate_obj(fiscal)
            try:
                fiscal.contatos = json.loads(fiscal_form.contatos_json.data or '[]')
            except Exception:
                fiscal.contatos = []
            try:
                fiscal.links_prefeitura = json.loads(fiscal_form.links_prefeitura_json.data or '[]')
            except Exception:
                fiscal.links_prefeitura = []
            flash('Departamento Fiscal salvo com sucesso!', 'success')
            form_processed_successfully = True

        elif form_type == 'contabil' and contabil_form.validate():
            if not contabil:
                contabil = Departamento(empresa_id=empresa_id, tipo='Departamento Contábil')
                db.session.add(contabil)
            
            contabil_form.populate_obj(contabil)
            
            contabil.envio_digital = json.dumps(contabil_form.envio_digital.data or [])
            contabil.envio_digital_fisico = json.dumps(contabil_form.envio_digital_fisico.data or [])
            contabil.controle_relatorios = json.dumps(contabil_form.controle_relatorios.data or [])
            
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
@login_required
@admin_required
def relatorios():
    return render_template('admin/relatorios.html')

@app.route('/logout', methods=['GET'])
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/test_connection')
def test_connection():
    try:
        from sqlalchemy import text
        result = db.session.execute(text('SELECT 1'))
        return "Conexão bem-sucedida com o banco de dados!"
    except Exception as e:
        return f"Erro na conexão: {str(e)}", 500
    
    ## Rota para listar usuários

@app.route('/users', methods=['GET', 'POST'])
@login_required
@admin_required
def list_users():
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

    users = User.query.all()
    return render_template('list_users.html', users=users, form=form)

@app.route('/novo_usuario', methods=['GET', 'POST'])
@login_required
@admin_required
def novo_usuario():
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
@login_required
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    form = EditUserForm(obj=user)

    if form.validate_on_submit():
        user.username = form.username.data
        user.email = form.email.data
        user.name = form.name.data
        user.role = form.role.data
        user.ativo = form.ativo.data
        db.session.commit()
        flash('Usuário atualizado com sucesso!', 'success')
        return redirect(url_for('list_users'))

    return render_template('edit_user.html', form=form)