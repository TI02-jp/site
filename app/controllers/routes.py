from flask import render_template, redirect, url_for, flash, request, abort
from functools import wraps
from flask_login import current_user, login_required, login_user, logout_user
from app import app, db
from app.loginForms import LoginForm, RegistrationForm
from app.models.tables import User, Empresa
from app.forms import EmpresaForm, EditUserForm
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