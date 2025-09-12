"""WTForms definitions for application-specific forms."""

from flask_wtf import FlaskForm
from wtforms import (
    StringField,
    RadioField,
    SubmitField,
    DateField,
    TimeField,
    DateTimeLocalField,
    SelectMultipleField,
    SelectField,
    TextAreaField,
    PasswordField,
    BooleanField,
    HiddenField,
    widgets
)
from wtforms.validators import DataRequired, Email, Optional, Length, EqualTo, ValidationError
import re

REGIME_LANCAMENTO_CHOICES = [
    ('Caixa', 'Caixa'),
    ('Competência', 'Competência')
]

class LoginForm(FlaskForm):
    """Formulário para login de usuários."""
    # Nome de usuário para autenticação
    username = StringField("Usuário", validators=[DataRequired()])
    # Campo de senha do usuário
    password = PasswordField("Senha", validators=[DataRequired()])
    # Checkbox para manter a sessão ativa
    remember_me = BooleanField("Lembrar-me")
    # Botão de envio do formulário
    submit = SubmitField("Entrar")

class RegistrationForm(FlaskForm):
    """Formulário para registrar novos usuários."""
    # Usuário para login; entre 4 e 20 caracteres
    username = StringField('Usuário', validators=[DataRequired(), Length(min=4, max=20)])
    # Email de contato do usuário
    email = StringField('Email', validators=[DataRequired(), Email()])
    # Nome completo utilizado para identificação
    name = StringField('Nome Completo', validators=[DataRequired()])
    # Senha de acesso
    password = PasswordField('Senha', validators=[DataRequired(), Length(min=6)])
    # Confirmação da senha para evitar erros de digitação
    confirm_password = PasswordField(
        'Confirmar Senha',
        validators=[DataRequired(), EqualTo('password', message='As senhas devem ser iguais.')]
    )
    # Perfil do usuário (admin ou comum)
    role = SelectField('Perfil', choices=[('user', 'Usuário'), ('admin', 'Administrador')], validators=[DataRequired()])
    # Tags adicionais do usuário
    tags = SelectMultipleField(
        'Tags',
        coerce=int,
        validators=[Optional()],
        option_widget=widgets.CheckboxInput(),
        widget=widgets.ListWidget(prefix_label=False)
    )
    # Botão de envio do formulário de cadastro
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
    regime_lancamento = SelectMultipleField(
        'Regime de Lançamento',
        choices=REGIME_LANCAMENTO_CHOICES,
        validators=[DataRequired()]
    )
    sistemas_consultorias = SelectMultipleField('Sistemas e Consultorias', choices=[
        ('IOB', 'IOB'), ('ACESSORIAS', 'Acessórias'), ('ACESSO_AO_SAT', 'Acesso ao SAT'),
        ('ITC', 'ITC'), ('QUESTOR', 'Questor'), ('ECONET', 'Econet'),
        ('QUESTOR_NET', 'Questor Net'), ('SIEG', 'Sieg'), ('SIEG_TAG', 'Sieg - Utiliza TAGs')
    ], validators=[Optional()])
    sistema_utilizado = StringField('Sistema Utilizado', validators=[Optional()])
    acessos_json = HiddenField('Acessos', validators=[Optional()])
    submit = SubmitField('Cadastrar Empresa')

class EditUserForm(FlaskForm):
    """Formulário para editar um usuário existente."""
    username = StringField('Usuário', validators=[DataRequired()])
    email = StringField('Email', validators=[DataRequired(), Email()])
    name = StringField('Nome', validators=[DataRequired()])
    role = SelectField('Perfil', choices=[('user', 'Usuário'), ('admin', 'Administrador')], validators=[DataRequired()])
    tags = SelectMultipleField(
        'Tags',
        coerce=int,
        validators=[Optional()],
        option_widget=widgets.CheckboxInput(),
        widget=widgets.ListWidget(prefix_label=False)
    )
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
        ('entradas_webservice', 'Entradas pelo Web Service'),
        ('saidas_sped', 'Saídas por Sped'), ('saidas_xml', 'Saídas por XML'),
        ('saidas_sieg', 'Saídas pelo SIEG'), ('nfce_sped', 'NFCe por Sped'),
        ('nfce_xml_sieg', 'NFCe por XML - Sieg'), ('nfce_xml_cliente', 'NFCe por XML - Copiado do cliente'),
        ('nenhum', 'Não importa nada')], validators=[Optional()])
    observacao_importacao = TextAreaField('Observação', validators=[Optional()])
    forma_movimento = SelectField('Envio de Documento', choices=[
        ('', 'Selecione'), ('Digital', 'Digital'), ('Fisico', 'Físico'), ('Digital e Físico', 'Digital e Físico')
    ], validators=[Optional()])
    envio_digital = SelectMultipleField('Envio Digital', choices=[
        ('email', 'Email'), ('whatsapp', 'Whatsapp'), ('acessorias', 'Acessórias'),
        ('google_chat', 'Google Chat')
    ], validators=[Optional()])
    envio_fisico = SelectMultipleField('Envio Físico', choices=[
        ('malote', 'Malote')
    ], validators=[Optional()])
    malote_coleta = SelectField('Coleta do Malote', choices=[
        ('', 'Selecione'), ('Cliente Traz', 'Cliente Traz'), ('JP Busca', 'JP Busca')
    ], validators=[Optional()])
    observacao_movimento = TextAreaField('Observação', validators=[Optional()])
    observacao_contato = TextAreaField('Observação', validators=[Optional()])
    contatos_json = HiddenField('Contatos', validators=[Optional()])
    particularidades_texto = TextAreaField('Particularidades', validators=[Optional()])

class DepartamentoContabilForm(DepartamentoForm):
    """Formulário para o Departamento Contábil."""
    metodo_importacao = SelectMultipleField('Formas de Importação', choices=[
        ('importado', 'Importado'), ('digitado', 'Digitado')
    ], validators=[Optional()])
    forma_movimento = SelectField('Envio de Documento', choices=[
        ('', 'Selecione'), ('Digital', 'Digital'), ('Fisico', 'Físico'), ('Digital e Físico', 'Digital e Físico')
    ], validators=[Optional()])
    envio_digital = SelectMultipleField('Envio Digital', choices=[
        ('email', 'Email'), ('whatsapp', 'Whatsapp'), ('acessorias', 'Acessórias'),
        ('google_chat', 'Google Chat')
    ], validators=[Optional()])
    envio_fisico = SelectMultipleField('Envio Físico', choices=[
        ('malote', 'Malote')
    ], validators=[Optional()])
    malote_coleta = SelectField('Coleta do Malote', choices=[
        ('', 'Selecione'), ('Cliente Traz', 'Cliente Traz'), ('JP Busca', 'JP Busca')
    ], validators=[Optional()])
    observacao_movimento = TextAreaField('Observação', validators=[Optional()])
    controle_relatorios = SelectMultipleField('Controle por Relatórios', choices=[
        ('forn_cli_cota_unica', 'Fornecedor e clientes conta única'),
        ('saldo_final_mes', 'Relatório com saldo final do mês'),
        ('adiantamentos', 'Relatório de adiantamentos'), ('contas_pagas', 'Relatório de contas pagas'),
        ('contas_recebidas', 'Relatório de contas recebidas'),
        ('conferir_aplicacao', 'Conferir aplicação')], validators=[Optional()])
    observacao_controle_relatorios = TextAreaField('Observação', validators=[Optional()])
    particularidades_texto = TextAreaField('Particularidades', validators=[Optional()])

class DepartamentoPessoalForm(DepartamentoForm):
    """Formulário para o Departamento Pessoal."""
    data_envio = StringField('Data de Envio', validators=[Optional()])
    registro_funcionarios = StringField('Registro de Funcionários', validators=[Optional()])
    ponto_eletronico = StringField('Ponto Eletrônico', validators=[Optional()])
    pagamento_funcionario = StringField('Pagamento de Funcionário', validators=[Optional()])
    particularidades_texto = TextAreaField('Particularidades', validators=[Optional()])


class DepartamentoAdministrativoForm(FlaskForm):
    """Formulário para o Departamento Administrativo."""
    particularidades_texto = TextAreaField('Particularidades', validators=[Optional()])


class DepartamentoFinanceiroForm(FlaskForm):
    """Formulário para o Departamento Financeiro."""
    particularidades_texto = TextAreaField('Particularidades', validators=[Optional()])


class ConsultoriaForm(FlaskForm):
    """Formulário para cadastro de consultorias."""
    nome = StringField('Nome da Consultoria', validators=[DataRequired()])
    usuario = StringField('Usuário na Consultoria', validators=[DataRequired()])
    senha = StringField('Senha na Consultoria', validators=[Optional()])
    submit = SubmitField('Salvar')


class SetorForm(FlaskForm):
    """Formulário para cadastro de setores."""
    nome = StringField('Setor', validators=[DataRequired()])
    submit = SubmitField('Salvar')


class TagForm(FlaskForm):
    """Formulário para cadastro de tags."""
    nome = StringField('Tag', validators=[DataRequired()])
    submit = SubmitField('Salvar')

class TaskForm(FlaskForm):
    """Formulário para criação de tarefas."""

    title = StringField("Título", validators=[DataRequired()])
    description = TextAreaField("Descrição", validators=[Optional()])
    tag_id = SelectField("Setor", coerce=int, validators=[DataRequired()])
    priority = SelectField(
        "Prioridade",
        choices=[("low", "Baixa"), ("medium", "Média"), ("high", "Alta")],
        default="medium",
        validators=[DataRequired()],
    )
    due_date = DateField("Prazo", format="%Y-%m-%d", validators=[Optional()])
    parent_id = HiddenField()
    submit = SubmitField("Salvar")
