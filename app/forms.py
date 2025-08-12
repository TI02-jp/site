from flask_wtf import FlaskForm
from wtforms import (
    StringField,
    RadioField,
    SubmitField,
    DateField,
    SelectMultipleField,
    SelectField,
    TextAreaField,
    PasswordField,
    BooleanField,
    HiddenField
)
from wtforms.validators import DataRequired, Email, Optional, Length, EqualTo, ValidationError
import re
from app.models.tables import RegimeLancamento

class LoginForm(FlaskForm):
    """Formulário para login de usuários."""
    username = StringField("Usuário", validators=[DataRequired()])
    password = PasswordField("Senha", validators=[DataRequired()])
    remember_me = BooleanField("Lembrar-me")
    submit = SubmitField("Entrar")

class RegistrationForm(FlaskForm):
    """Formulário para registrar novos usuários."""
    username = StringField('Usuário', validators=[DataRequired(), Length(min=4, max=20)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    name = StringField('Nome Completo', validators=[DataRequired()])
    password = PasswordField('Senha', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirmar Senha', validators=[DataRequired(), EqualTo('password', message='As senhas devem ser iguais.')])
    role = SelectField('Perfil', choices=[('user', 'Usuário'), ('admin', 'Administrador')], validators=[DataRequired()])
    submit = SubmitField('Cadastrar')

# --- Formulários da Aplicação ---

def validar_cnpj(form, field):
    """Valida um CNPJ utilizando os dígitos verificadores."""
    cnpj = re.sub(r"\D", "", field.data or "")

    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        raise ValidationError("CNPJ inválido")

    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    soma1 = sum(int(num) * peso for num, peso in zip(cnpj[:12], pesos1))
    digito1 = 11 - (soma1 % 11)
    digito1 = 0 if digito1 >= 10 else digito1

    pesos2 = [6] + pesos1
    soma2 = sum(int(num) * peso for num, peso in zip(cnpj[:13], pesos2))
    digito2 = 11 - (soma2 % 11)
    digito2 = 0 if digito2 >= 10 else digito2

    if cnpj[-2:] != f"{digito1}{digito2}":
        raise ValidationError("CNPJ inválido")

    field.data = cnpj

class EmpresaForm(FlaskForm):
    """Formulário para cadastrar ou editar uma empresa."""
    codigo_empresa = StringField('Código da Empresa', validators=[DataRequired()])
    nome_empresa = StringField('Nome da Empresa', validators=[DataRequired()])
    cnpj = StringField('CNPJ', validators=[DataRequired(), validar_cnpj])
    data_abertura = DateField('Data de Abertura', format='%Y-%m-%d', validators=[DataRequired()])
    socio_administrador = StringField('Sócio Administrador', validators=[Optional()])
    atividade_principal = StringField('Atividade Principal', validators=[Optional()])
    tributacao = RadioField('Tributação', choices=[
        ('Simples Nacional', 'Simples Nacional'), 
        ('Lucro Presumido', 'Lucro Presumido'), 
        ('Lucro Real', 'Lucro Real')], validators=[DataRequired()])
    regime_lancamento = RadioField('Regime de Lançamento', choices=[
        (e.value, e.value) for e in RegimeLancamento
    ], validators=[DataRequired()])
    sistemas_consultorias = SelectMultipleField('Sistemas e Consultorias', choices=[
        ('IOB', 'IOB'), ('ACESSORIAS', 'Acessórias'), ('ACESSO_AO_SAT', 'Acesso ao SAT'),
        ('ITC', 'ITC'), ('QUESTOR', 'Questor'), ('ECONET', 'Econet'),
        ('QUESTOR_NET', 'Questor Net'), ('SIEG', 'Sieg'), ('SIEG_TAG', 'Sieg - Utiliza TAGs')
    ], validators=[Optional()])
    sistema_utilizado = StringField('Sistema Utilizado', validators=[Optional()])
    submit = SubmitField('Cadastrar Empresa')

class EditUserForm(FlaskForm):
    """Formulário para editar um usuário existente."""
    username = StringField('Usuário', validators=[DataRequired()])
    email = StringField('Email', validators=[DataRequired(), Email()])
    name = StringField('Nome', validators=[DataRequired()])
    role = SelectField('Perfil', choices=[('user', 'Usuário'), ('admin', 'Administrador')], validators=[DataRequired()])
    ativo = BooleanField('Usuário Ativo')

class DepartamentoForm(FlaskForm):
    """Formulário base para departamentos."""
    responsavel = StringField('Responsável', validators=[Optional()])
    descricao = StringField('Descrição', validators=[Optional()])

class DepartamentoFiscalForm(DepartamentoForm):
    """Formulário para o Departamento Fiscal."""
    formas_importacao = SelectMultipleField('Formas de Importação', choices=[
        ('entradas_sped', 'Entradas por Sped'), ('entradas_xml', 'Entradas por XML'),
        ('entradas_sat', 'Entradas pelo SAT'), ('entradas_sieg', 'Entradas pelo Sieg'),
        ('saidas_sped', 'Saídas por Sped'), ('saidas_xml', 'Saídas por XML'),
        ('saidas_sieg', 'Saídas pelo SIEG'), ('nfce_sped', 'NFCe por Sped'),
        ('nfce_xml_sieg', 'NFCe por XML - Sieg'), ('nfce_xml_cliente', 'NFCe por XML - Copiado do cliente'),
        ('nenhum', 'Não importa nada')], validators=[Optional()])
    prefeituras_json = HiddenField('Prefeituras', validators=[Optional()])
    forma_movimento = SelectField('Forma de Recebimento do Movimento', choices=[
        ('', 'Selecione'), ('Digital', 'Digital'), ('Fisico', 'Físico'), ('Digital e Físico', 'Digital e Físico')
    ], validators=[Optional()])
    envio_digital = SelectMultipleField('Envio Digital', choices=[
        ('email', 'Email'), ('whatsapp', 'Whatsapp'), ('skype', 'Skype'), ('acessorias', 'Acessórias')
    ], validators=[Optional()])
    envio_digital_fisico = SelectMultipleField('Envio Digital e Físico', choices=[
        ('email', 'Email'), ('whatsapp', 'Whatsapp'), ('skype', 'Skype'), 
        ('acessorias', 'Acessórias'), ('malote', 'Malote')], validators=[Optional()])
    observacao_movimento = TextAreaField('Observação', validators=[Optional()])
    contatos_json = HiddenField('Contatos', validators=[Optional()])
    particularidades_texto = TextAreaField('Particularidades', validators=[Optional()])

class DepartamentoContabilForm(DepartamentoForm):
    """Formulário para o Departamento Contábil."""
    metodo_importacao = SelectField('Forma de Importação', choices=[
        ('', 'Selecione'), ('importado', 'Importado'), ('digitado', 'Digitado')
    ], validators=[Optional()])
    forma_movimento = SelectField('Forma de Recebimento do Movimento', choices=[
        ('', 'Selecione'), ('Digital', 'Digital'), ('Fisico', 'Físico'), ('Digital e Físico', 'Digital e Físico')
    ], validators=[Optional()])
    envio_digital = SelectMultipleField('Envio Digital', choices=[
        ('email', 'Email'), ('whatsapp', 'Whatsapp'), ('skype', 'Skype'), ('acessorias', 'Acessórias')
    ], validators=[Optional()])
    envio_digital_fisico = SelectMultipleField('Envio Digital e Físico', choices=[
        ('email', 'Email'), ('whatsapp', 'Whatsapp'), ('skype', 'Skype'), 
        ('acessorias', 'Acessórias'), ('malote', 'Malote')], validators=[Optional()])
    observacao_movimento = TextAreaField('Observação Movimento', validators=[Optional()])
    controle_relatorios = SelectMultipleField('Controle por Relatórios', choices=[
        ('forn_cli_cota_unica', 'Fornecedor e clientes cota única'),
        ('saldo_final_mes', 'Relatório com saldo final do mês'),
        ('adiantamentos', 'Relatório de adiantamentos'), ('contas_pagas', 'Relatório de contas pagas'),
        ('contas_recebidas', 'Relatório de contas recebidas'),
        ('conferir_aplicacao', 'Conferir aplicação')], validators=[Optional()])
    observacao_controle_relatorios = TextAreaField('Observação Relatórios', validators=[Optional()])
    particularidades_texto = TextAreaField('Particularidades', validators=[Optional()])

class DepartamentoPessoalForm(DepartamentoForm):
    """Formulário para o Departamento Pessoal."""
    data_envio = StringField('Data de Envio', validators=[Optional()])
    registro_funcionarios = StringField('Registro de Funcionários', validators=[Optional()])
    ponto_eletronico = StringField('Ponto Eletrônico', validators=[Optional()])
    pagamento_funcionario = StringField('Pagamento de Funcionário', validators=[Optional()])
    particularidades_texto = TextAreaField('Particularidades', validators=[Optional()])
