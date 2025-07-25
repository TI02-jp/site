import os, json, re
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
from werkzeug.utils import secure_filename
from uuid import uuid4
from app.forms import DepartamentoFiscalForm, DepartamentoContabilForm, DepartamentoPessoalForm

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

@app.route('/cadastrar_empresa', methods=['GET', 'POST'])
@login_required
def cadastrar_empresa():
    form = EmpresaForm()
    if form.validate_on_submit():
        print("Formulário validado, tentando cadastrar...")
        try:
            cnpj_limpo = re.sub(r'\D', '', form.cnpj.data)
            sistemas_consultorias_str = ",".join(form.sistemas_consultorias.data) if form.sistemas_consultorias.data else ""

            nova_empresa = Empresa(
                codigo_empresa=form.codigo_empresa.data,
                nome_empresa=form.nome_empresa.data,
                cnpj=cnpj_limpo,
                data_abertura=form.data_abertura.data,
                socio_administrador=form.socio_administrador.data,
                tributacao=form.tributacao.data,
                regime_lancamento=form.regime_lancamento.data,
                atividade_principal=form.atividade_principal.data,
                sistemas_consultorias=sistemas_consultorias_str,
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
    empresas = Empresa.query.all()

    return render_template('empresas/listar.html', empresas=empresas)

def processar_dados_fiscal(request):
    """Função auxiliar para processar dados do departamento fiscal"""
    # Dados básicos do formulário
    responsavel = request.form.get('responsavel')
    descricao = request.form.get('descricao')
    link_prefeitura = request.form.get('link_prefeitura')
    usuario_prefeitura = request.form.get('usuario_prefeitura')
    senha_prefeitura = request.form.get('senha_prefeitura')
    forma_movimento = request.form.get('forma_movimento')
    observacao_movimento = request.form.get('observacao_movimento')
    particularidades = request.form.get('particularidades')
    
    # Processar dados JSON dos checkboxes
    formas_importacao_json = request.form.get('formas_importacao_json', '[]')
    formas_importacao = json.loads(formas_importacao_json) if formas_importacao_json else []
    
    envio_digital_json = request.form.get('envio_digital_json', '[]')
    envio_digital = json.loads(envio_digital_json) if envio_digital_json else []
    
    envio_digital_fisico_json = request.form.get('envio_digital_fisico_json', '[]')
    envio_digital_fisico = json.loads(envio_digital_fisico_json) if envio_digital_fisico_json else []
    
    # Processar dados de contato
    contatos_json = request.form.get('contatos_json', 'null')
    contatos = json.loads(contatos_json) if contatos_json != 'null' else None
    
    return {
        'responsavel': responsavel,
        'descricao': descricao,
        'formas_importacao': formas_importacao,
        'link_prefeitura': link_prefeitura,
        'usuario_prefeitura': usuario_prefeitura,
        'senha_prefeitura': senha_prefeitura,
        'forma_movimento': forma_movimento,
        'envio_digital': envio_digital,
        'envio_digital_fisico': envio_digital_fisico,
        'observacao_movimento': observacao_movimento,
        'contatos': contatos,
        'particularidades_texto': particularidades
    }

def processar_dados_contabil(request):
    """Função auxiliar para processar dados do departamento contábil"""
    # Dados básicos do formulário
    responsavel = request.form.get('responsavel')
    descricao = request.form.get('descricao')
    metodo_importacao = request.form.get('metodo_importacao')
    forma_movimento = request.form.get('forma_movimento')
    observacao_movimento = request.form.get('observacao_movimento')
    observacao_controle_relatorios = request.form.get('observacao_controle_relatorios')
    particularidades = request.form.get('particularidades')
    
    # Processar dados JSON dos checkboxes
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
    # 1. Carrega todos os objetos do banco
    empresa = Empresa.query.get_or_404(id)
    fiscal = Departamento.query.filter_by(empresa_id=id, tipo='Departamento Fiscal').first()
    contabil = Departamento.query.filter_by(empresa_id=id, tipo='Departamento Contábil').first()
    pessoal = Departamento.query.filter_by(empresa_id=id, tipo='Departamento Pessoal').first()
    administrativo = Departamento.query.filter_by(empresa_id=id, tipo='Departamento Administrativo').first()

    # 2. Instancia todos os formulários.
    # Para GET, preenche com 'obj'. Para POST, com 'request.form'.
    empresa_form = EmpresaForm(request.form, obj=empresa)
    fiscal_form = DepartamentoFiscalForm(request.form, obj=fiscal)
    contabil_form = DepartamentoContabilForm(request.form, obj=contabil)
    pessoal_form = DepartamentoPessoalForm(request.form, obj=pessoal)
    administrativo_form = DepartamentoForm(request.form, obj=administrativo)

    # 3. Lógica para quando o formulário é ENVIADO (POST)
    if request.method == 'POST':
        form_type = request.form.get('form_type')

        form_map = {
            'empresa': (empresa_form, empresa),
            'fiscal': (fiscal_form, fiscal or Departamento(empresa_id=id, tipo='Departamento Fiscal')),
            'contabil': (contabil_form, contabil or Departamento(empresa_id=id, tipo='Departamento Contábil')),
            'pessoal': (pessoal_form, pessoal or Departamento(empresa_id=id, tipo='Departamento Pessoal')),
            'administrativo': (administrativo_form, administrativo or Departamento(empresa_id=id, tipo='Departamento Administrativo'))
        }
        form_to_validate, obj_to_populate = form_map.get(form_type, (None, None))

        if form_to_validate and form_to_validate.validate():
            # A MÁGICA ACONTECE AQUI!
            # populate_obj agora funciona perfeitamente.
            form_to_validate.populate_obj(obj_to_populate)
            
            # Tratamentos manuais que ainda são necessários
            if form_type == 'empresa':
                obj_to_populate.cnpj = re.sub(r'\D', '', form_to_validate.cnpj.data or '')
                obj_to_populate.sistemas_consultorias = ",".join(form_to_validate.sistemas_consultorias.data or [])
            
            if form_type == 'fiscal':
                obj_to_populate.contatos = {
                    "nome": form_to_validate.contato_nome.data,
                    "meios": form_to_validate.contato_meios.data
                }
            
            db.session.add(obj_to_populate)
            try:
                db.session.commit()
                flash(f'Dados de "{form_type.capitalize()}" salvos com sucesso!', 'success')
                return redirect(url_for('editar_empresa', id=id))
            except Exception as e:
                db.session.rollback()
                flash(f'Ocorreu um erro ao salvar: {str(e)}', 'danger')
        
        elif form_to_validate:
            flash(f"Erro de validação no formulário '{form_type.capitalize()}'. Por favor, verifique os campos.", 'danger')

    # 4. Tratamentos manuais para GET
    if request.method == 'GET':
        if fiscal and fiscal.contatos and isinstance(fiscal.contatos, dict):
            fiscal_form.contato_nome.data = fiscal.contatos.get('nome')
            fiscal_form.contato_meios.data = fiscal.contatos.get('meios')
        
        # --- CORREÇÃO APLICADA AQUI ---
        if isinstance(empresa.sistemas_consultorias, str):
            empresa_form.sistemas_consultorias.data = [item.strip() for item in empresa.sistemas_consultorias.split(',') if item.strip()]

    # 5. Renderiza o template no final.
    return render_template(
        'empresas/editar_empresa.html',
        empresa=empresa,
        empresa_form=empresa_form,
        fiscal_form=fiscal_form,
        contabil_form=contabil_form,
        pessoal_form=pessoal_form,
        administrativo_form=administrativo_form
    )

@app.route('/empresa/visualizar/<int:id>')
@login_required
def visualizar_empresa(id):
    empresa = Empresa.query.get_or_404(id)
    
    fiscal = Departamento.query.filter_by(empresa_id=id, tipo='Departamento Fiscal').first()
    contabil = Departamento.query.filter_by(empresa_id=id, tipo='Departamento Contábil').first()
    pessoal = Departamento.query.filter_by(empresa_id=id, tipo='Departamento Pessoal').first()
    administrativo = Departamento.query.filter_by(empresa_id=id, tipo='Departamento Administrativo').first()
    
    if fiscal and fiscal.contatos:
        try:
            if isinstance(fiscal.contatos, dict):
                fiscal.contato_nome = fiscal.contatos.get('nome', '')
                fiscal.contato_meios = ','.join(fiscal.contatos.get('meios', []))
            else:
                fiscal.contato_nome = ''
                fiscal.contato_meios = ''
        except:
            fiscal.contato_nome = ''
            fiscal.contato_meios = ''

    if fiscal and fiscal.formas_importacao:
        if isinstance(fiscal.formas_importacao, str):
            try:
                fiscal.formas_importacao = json.loads(fiscal.formas_importacao)
            except:
                fiscal.formas_importacao = []
    else:
        fiscal.formas_importacao = []

    
    return render_template('empresas/visualizar.html',
                         empresa=empresa,
                         fiscal=fiscal,
                         contabil=contabil,
                         pessoal=pessoal,
                         administrativo=administrativo)

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
        if fiscal and fiscal.contatos and isinstance(fiscal.contatos, dict):
            fiscal_form.contato_nome.data = fiscal.contatos.get('nome')
            fiscal_form.contato_meios.data = fiscal.contatos.get('meios')

    form_type = request.form.get('form_type')

    if request.method == 'POST':
        form_processed_successfully = False

        if form_type == 'fiscal' and fiscal_form.validate():
            if not fiscal:
                fiscal = Departamento(empresa_id=empresa_id, tipo='Departamento Fiscal')
                db.session.add(fiscal)
            
            fiscal_form.populate_obj(fiscal)
            fiscal.contatos = {
                "nome": fiscal_form.contato_nome.data,
                "meios": fiscal_form.contato_meios.data
            }
            flash('Departamento Fiscal salvo com sucesso!', 'success')
            form_processed_successfully = True

        elif form_type == 'contabil' and contabil_form.validate():
            if not contabil:
                contabil = Departamento(empresa_id=empresa_id, tipo='Departamento Contábil')
                db.session.add(contabil)
            
            contabil_form.populate_obj(contabil)
            flash('Departamento Contábil salvo com sucesso!', 'success')
            form_processed_successfully = True

        elif form_type == 'pessoal' and pessoal_form.validate():
            if not pessoal:
                pessoal = Departamento(empresa_id=empresa_id, tipo='Departamento Pessoal')
                db.session.add(pessoal)

            pessoal_form.populate_obj(pessoal)
            flash('Departamento Pessoal salvo com sucesso!', 'success')
            form_processed_successfully = True
        
        # CORREÇÃO: Faltava a lógica para salvar o formulário administrativo
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
                return redirect(url_for('gerenciar_departamentos', empresa_id=empresa_id))
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