from flask import render_template, redirect, url_for, flash, request, abort
from functools import wraps
from flask_login import current_user, login_required, login_user, logout_user
from app import app, db
from app.loginForms import LoginForm, RegistrationForm
from app.models.tables import User, Empresa, Departamento
from app.forms import (
    EmpresaForm,
    EditUserForm,
    DepartamentoForm,
    DepartamentoFiscalForm,
    DepartamentoContabilForm,
    DepartamentoPessoalForm,
)
from datetime import datetime
import re

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
            login_user(user, remember=form.remember_me.data)
            flash('Login bem-sucedido!')
            return redirect(url_for('dashboard'))
        else:
            flash('Credenciais inválidas', 'danger')
    return render_template('login.html', form=form)

@app.route('/register', methods=['GET', 'POST'])
@login_required
@admin_required
def register():
    form = RegistrationForm()
    if form.validate_on_submit():
        existing_user = User.query.filter(
            (User.username == form.username.data) | (User.email == form.email.data)
        ).first()
        if existing_user:
            flash('Usuário ou email já cadastrado.', 'warning')
            return redirect(url_for('register'))

        user = User(
            username=form.username.data,
            email=form.email.data,
            name=form.name.data
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()

        flash('Parabéns, você agora é um usuário registrado!', 'success')
        return redirect(url_for('login'))
    return render_template('register.html', form=form)

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
            cnpj_limpo = re.sub(r'\D', '', form.cnpj.data)  # Remove caracteres não numéricos
            sistemas_consultorias_str = ",".join(form.sistemas_consultorias.data) if form.sistemas_consultorias.data else ""

            nova_empresa = Empresa(
                CodigoEmpresa=form.codigo_empresa.data,
                NomeEmpresa=form.nome_empresa.data,
                CNPJ=cnpj_limpo,
                DataAbertura=form.data_abertura.data,
                SocioAdministrador=form.socio_administrador.data,
                Tributacao=form.tributacao.data,
                RegimeLancamento=form.regime_lancamento.data,
                AtividadePrincipal=form.atividade_principal.data,
                SistemasConsultorias=sistemas_consultorias_str,
                SistemaUtilizado=form.sistema_utilizado.data
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

    # Converte DataAbertura para datetime se necessário
    for empresa in empresas:
        if empresa.DataAbertura and isinstance(empresa.DataAbertura, str):
            try:
                empresa.DataAbertura = datetime.strptime(empresa.DataAbertura, '%Y-%m-%d')
            except ValueError:
                empresa.DataAbertura = None

    return render_template('empresas/listar.html', empresas=empresas)

@app.route('/empresa/excluir/<int:id>', methods=['POST'])
@login_required
@admin_required
def excluir_empresa(id):
    empresa = Empresa.query.get_or_404(id)
    try:
        db.session.delete(empresa)
        db.session.commit()
        flash('Empresa excluída com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir empresa: {e}', 'danger')
    return redirect(url_for('listar_empresas'))

@app.route('/empresa/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_empresa(id):
    empresa = Empresa.query.get_or_404(id)
    if request.method == 'POST':
        empresa.NomeEmpresa = request.form.get('nome')
        cnpj_limpo = re.sub(r'\D', '', request.form.get('cnpj', ''))
        empresa.CNPJ = cnpj_limpo
        empresa.DataAbertura = request.form.get('data_abertura')
        try:
            db.session.commit()
            flash('Empresa atualizada com sucesso!', 'success')
            return redirect(url_for('listar_empresas'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar empresa: {e}', 'danger')
    return render_template('empresas/editar_empresa.html', empresa=empresa)

@app.route('/empresa/<int:empresa_id>/departamentos', methods=['GET', 'POST'])
@login_required
def gerenciar_departamentos(empresa_id):
    empresa = Empresa.query.get_or_404(empresa_id)

    fiscal = Departamento.query.filter_by(empresa_id=empresa_id, tipo='Departamento Fiscal').first()
    contabil = Departamento.query.filter_by(empresa_id=empresa_id, tipo='Departamento Contábil').first()
    pessoal = Departamento.query.filter_by(empresa_id=empresa_id, tipo='Departamento Pessoal').first()
    administrativo = Departamento.query.filter_by(empresa_id=empresa_id, tipo='Departamento Administrativo').first()

    fiscal_form = DepartamentoFiscalForm(obj=fiscal)
    contabil_form = DepartamentoContabilForm(obj=contabil)
    pessoal_form = DepartamentoPessoalForm(obj=pessoal)
    administrativo_form = DepartamentoForm(obj=administrativo)

    submitted = request.form.get('form_type')

    if submitted == 'fiscal' and fiscal_form.validate_on_submit():
        if not fiscal:
            fiscal = Departamento(empresa_id=empresa_id, tipo='Departamento Fiscal')
        fiscal.responsavel = fiscal_form.responsavel.data
        fiscal.descricao = fiscal_form.descricao.data
        fiscal.formas_importacao = fiscal_form.formas_importacao.data
        fiscal.link_prefeitura = fiscal_form.link_prefeitura.data
        fiscal.usuario_prefeitura = fiscal_form.usuario_prefeitura.data
        fiscal.senha_prefeitura = fiscal_form.senha_prefeitura.data
        fiscal.forma_movimento = fiscal_form.forma_movimento.data
        fiscal.envio_digital = fiscal_form.envio_digital.data
        fiscal.envio_digital_fisico = fiscal_form.envio_digital_fisico.data
        fiscal.observacao_movimento = fiscal_form.observacao_movimento.data
        fiscal.contatos = {
            'nome': fiscal_form.contato_nome.data,
            'meios': fiscal_form.contato_meios.data,
        }
        fiscal.particularidades_texto = fiscal_form.particularidades.data
        fiscal.particularidades_imagens = [f.filename for f in fiscal_form.particularidades_imagens.data if f]
        db.session.add(fiscal)
        db.session.commit()
        flash('Departamento Fiscal salvo!', 'success')
        return redirect(url_for('gerenciar_departamentos', empresa_id=empresa_id))

    if submitted == 'contabil' and contabil_form.validate_on_submit():
        if not contabil:
            contabil = Departamento(empresa_id=empresa_id, tipo='Departamento Contábil')
        contabil.responsavel = contabil_form.responsavel.data
        contabil.descricao = contabil_form.descricao.data
        contabil.metodo_importacao = contabil_form.metodo_importacao.data
        contabil.observacao_importacao = contabil_form.observacao_importacao.data
        contabil.forma_movimento = contabil_form.forma_movimento.data
        contabil.envio_digital = contabil_form.envio_digital.data
        contabil.envio_digital_fisico = contabil_form.envio_digital_fisico.data
        contabil.observacao_movimento = contabil_form.observacao_movimento.data
        contabil.controle_relatorios = contabil_form.controle_relatorios.data
        contabil.observacao_controle_relatorios = contabil_form.observacao_controle_relatorios.data
        contabil.particularidades_texto = contabil_form.particularidades.data
        contabil.particularidades_imagens = [f.filename for f in contabil_form.particularidades_imagens.data if f]
        db.session.add(contabil)
        db.session.commit()
        flash('Departamento Contábil salvo!', 'success')
        return redirect(url_for('gerenciar_departamentos', empresa_id=empresa_id))

    if submitted == 'pessoal' and pessoal_form.validate_on_submit():
        if not pessoal:
            pessoal = Departamento(empresa_id=empresa_id, tipo='Departamento Pessoal')
        pessoal.responsavel = pessoal_form.responsavel.data
        pessoal.descricao = pessoal_form.descricao.data
        pessoal.data_envio = pessoal_form.data_envio.data
        pessoal.registro_funcionarios = pessoal_form.registro_funcionarios.data
        pessoal.ponto_eletronico = pessoal_form.ponto_eletronico.data
        pessoal.pagamento_funcionario = pessoal_form.pagamento_funcionario.data
        pessoal.particularidades_texto = pessoal_form.particularidades.data
        pessoal.particularidades_imagens = [f.filename for f in pessoal_form.particularidades_imagens.data if f]
        db.session.add(pessoal)
        db.session.commit()
        flash('Departamento Pessoal salvo!', 'success')
        return redirect(url_for('gerenciar_departamentos', empresa_id=empresa_id))

    if submitted == 'administrativo' and administrativo_form.validate_on_submit():
        if not administrativo:
            administrativo = Departamento(empresa_id=empresa_id, tipo='Departamento Administrativo')
        administrativo.responsavel = administrativo_form.responsavel.data
        administrativo.descricao = administrativo_form.descricao.data
        db.session.add(administrativo)
        db.session.commit()
        flash('Departamento Administrativo salvo!', 'success')
        return redirect(url_for('gerenciar_departamentos', empresa_id=empresa_id))

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
        administrativo=administrativo,
    )


@app.route('/relatorios')
@login_required
@admin_required
def relatorios():
    # Aqui pode implementar lógica de relatórios
    return render_template('relatorios.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

# Rota para teste da conexão com banco
@app.route('/test_connection')
def test_connection():
    try:
        from sqlalchemy import text
        result = db.session.execute(text('SELECT 1'))
        return "Conexão bem-sucedida com o banco de dados!"
    except Exception as e:
        return f"Erro na conexão: {str(e)}", 500

# Exemplo para listar usuários cadastrados (somente para admins ou dev)
@app.route('/users')
@login_required
@admin_required
def list_users():
    users = User.query.all()
    return render_template('list_users.html', users=users)

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
        try:
            db.session.commit()
            flash('Usuário atualizado com sucesso!', 'success')
            return redirect(url_for('list_users'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar usuário: {e}', 'danger')
    return render_template('edit_user.html', form=form)

@app.route('/user/delete/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    try:
        db.session.delete(user)
        db.session.commit()
        flash('Usuário excluído com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir usuário: {e}', 'danger')
    return redirect(url_for('list_users'))
