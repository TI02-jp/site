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
            return redirect(url_for('listar_empresas'))

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


def _cadastrar_departamento(empresa_id, tipo_nome):
    form = DepartamentoForm()
    departamento = Departamento.query.filter_by(empresa_id=empresa_id, tipo=tipo_nome).first()
    if departamento:
        form.responsavel.data = departamento.responsavel
        form.descricao.data = departamento.descricao
    if form.validate_on_submit():
        if not departamento:
            departamento = Departamento(empresa_id=empresa_id, tipo=tipo_nome)
        departamento.responsavel = form.responsavel.data
        departamento.descricao = form.descricao.data
        try:
            db.session.add(departamento)
            db.session.commit()
            flash(f'{tipo_nome} cadastrado com sucesso!', 'success')
            return redirect(url_for('listar_empresas'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar {tipo_nome.lower()}: {e}', 'danger')
    empresa = Empresa.query.get_or_404(empresa_id)
    return render_template('departamentos/cadastrar.html', form=form, empresa=empresa, tipo_nome=tipo_nome, departamento=departamento)


@app.route('/empresa/<int:empresa_id>/departamentos/fiscal', methods=['GET', 'POST'])
@login_required
def cadastrar_departamento_fiscal(empresa_id):
    empresa = Empresa.query.get_or_404(empresa_id)
    departamento = Departamento.query.filter_by(empresa_id=empresa_id, tipo='Departamento Fiscal').first()
    form = DepartamentoFiscalForm(obj=departamento)
    if form.validate_on_submit():
        if not departamento:
            departamento = Departamento(empresa_id=empresa_id, tipo='Departamento Fiscal')
        departamento.responsavel = form.responsavel.data
        departamento.descricao = form.descricao.data
        departamento.formas_importacao = form.formas_importacao.data
        departamento.link_prefeitura = form.link_prefeitura.data
        departamento.usuario_prefeitura = form.usuario_prefeitura.data
        departamento.senha_prefeitura = form.senha_prefeitura.data
        departamento.forma_movimento = form.forma_movimento.data
        departamento.envio_digital = form.envio_digital.data
        departamento.envio_digital_fisico = form.envio_digital_fisico.data
        departamento.observacao_movimento = form.observacao_movimento.data
        departamento.contatos = {
            'nome': form.contato_nome.data,
            'meios': form.contato_meios.data
        }
        departamento.particularidades_texto = form.particularidades.data
        departamento.particularidades_imagens = [f.filename for f in form.particularidades_imagens.data if f]
        try:
            db.session.add(departamento)
            db.session.commit()
            flash('Departamento Fiscal cadastrado com sucesso!', 'success')
            return redirect(url_for('listar_empresas'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar departamento fiscal: {e}', 'danger')
    return render_template('departamentos/cadastrar_fiscal.html', form=form, empresa=empresa, tipo_nome='Departamento Fiscal', departamento=departamento)


@app.route('/empresa/<int:empresa_id>/departamentos/contabil', methods=['GET', 'POST'])
@login_required
def cadastrar_departamento_contabil(empresa_id):
    empresa = Empresa.query.get_or_404(empresa_id)
    departamento = Departamento.query.filter_by(empresa_id=empresa_id, tipo='Departamento Contábil').first()
    form = DepartamentoContabilForm(obj=departamento)
    if form.validate_on_submit():
        if not departamento:
            departamento = Departamento(empresa_id=empresa_id, tipo='Departamento Contábil')
        departamento.responsavel = form.responsavel.data
        departamento.descricao = form.descricao.data
        departamento.metodo_importacao = form.metodo_importacao.data
        departamento.observacao_importacao = form.observacao_importacao.data
        departamento.forma_movimento = form.forma_movimento.data
        departamento.envio_digital = form.envio_digital.data
        departamento.envio_digital_fisico = form.envio_digital_fisico.data
        departamento.observacao_movimento = form.observacao_movimento.data
        departamento.controle_relatorios = form.controle_relatorios.data
        departamento.observacao_controle_relatorios = form.observacao_controle_relatorios.data
        departamento.particularidades_texto = form.particularidades.data
        departamento.particularidades_imagens = [f.filename for f in form.particularidades_imagens.data if f]
        try:
            db.session.add(departamento)
            db.session.commit()
            flash('Departamento Contábil cadastrado com sucesso!', 'success')
            return redirect(url_for('listar_empresas'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar departamento contábil: {e}', 'danger')
    return render_template('departamentos/cadastrar_contabil.html', form=form, empresa=empresa, tipo_nome='Departamento Contábil', departamento=departamento)


@app.route('/empresa/<int:empresa_id>/departamentos/pessoal', methods=['GET', 'POST'])
@login_required
def cadastrar_departamento_pessoal(empresa_id):
    empresa = Empresa.query.get_or_404(empresa_id)
    departamento = Departamento.query.filter_by(empresa_id=empresa_id, tipo='Departamento Pessoal').first()
    form = DepartamentoPessoalForm(obj=departamento)
    if form.validate_on_submit():
        if not departamento:
            departamento = Departamento(empresa_id=empresa_id, tipo='Departamento Pessoal')
        departamento.responsavel = form.responsavel.data
        departamento.descricao = form.descricao.data
        departamento.data_envio = form.data_envio.data
        departamento.registro_funcionarios = form.registro_funcionarios.data
        departamento.ponto_eletronico = form.ponto_eletronico.data
        departamento.pagamento_funcionario = form.pagamento_funcionario.data
        departamento.particularidades_texto = form.particularidades.data
        departamento.particularidades_imagens = [f.filename for f in form.particularidades_imagens.data if f]
        try:
            db.session.add(departamento)
            db.session.commit()
            flash('Departamento Pessoal cadastrado com sucesso!', 'success')
            return redirect(url_for('listar_empresas'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar departamento pessoal: {e}', 'danger')
    return render_template('departamentos/cadastrar_pessoal.html', form=form, empresa=empresa, tipo_nome='Departamento Pessoal', departamento=departamento)


@app.route('/empresa/<int:empresa_id>/departamentos/administrativo', methods=['GET', 'POST'])
@login_required
def cadastrar_departamento_administrativo(empresa_id):
    return _cadastrar_departamento(empresa_id, 'Departamento Administrativo')

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
