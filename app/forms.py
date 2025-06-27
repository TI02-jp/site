from flask_wtf import FlaskForm
from wtforms import StringField, RadioField, SubmitField, DateField, SelectMultipleField, SelectField
from wtforms.validators import DataRequired, Email

class EmpresaForm(FlaskForm):
    codigo_empresa = StringField('Código da Empresa', validators=[DataRequired()])
    nome_empresa = StringField('Nome da Empresa', validators=[DataRequired()])
    cnpj = StringField('CNPJ', validators=[DataRequired()])
    # Mantém formato ISO para compatibilidade com input type=date
    data_abertura = DateField('Data de Abertura', format='%Y-%m-%d', validators=[DataRequired()])
    socio_administrador = StringField('Sócio Administrador', validators=[DataRequired()])

    tributacao = RadioField('Tributação', choices=[
        ('Simples Nacional', 'Simples Nacional'), 
        ('Lucro Presumido', 'Lucro Presumido'), 
        ('Lucro Real', 'Lucro Real')], validators=[DataRequired()])

    regime_lancamento = RadioField('Regime de Lançamento', choices=[
        ('CAIXA', 'Caixa'), 
        ('COMPETENCIA', 'Competência')], validators=[DataRequired()])

    atividade_principal = StringField('Atividade Principal', validators=[DataRequired()])

    sistemas_consultorias = SelectMultipleField('Sistemas e Consultorias', choices=[
        ('IOB', 'IOB'), 
        ('ACESSÓRIAS', 'ACESSÓRIAS'),
        ('ACESSO AO SAT', 'ACESSO AO SAT'), 
        ('ITC', 'ITC'), 
        ('QUESTOR', 'QUESTOR'),
        ('ECONET', 'ECONET'), 
        ('QUESTOR NET', 'QUESTOR NET'), 
        ('SIEG', 'SIEG'),
        ('SIEG - Utiliza TAGs', 'SIEG - Utiliza TAGs')],
        default=[],
        validators=[])

    sistema_utilizado = StringField('Sistema Utilizado')
    submit = SubmitField('Cadastrar Empresa')

class EditUserForm(FlaskForm):
    username = StringField('Usuário', validators=[DataRequired()])
    email = StringField('Email', validators=[DataRequired(), Email()])
    name = StringField('Nome', validators=[DataRequired()])
    role = SelectField('Perfil', choices=[('user', 'Usuário'), ('admin', 'Administrador')], validators=[DataRequired()])


class DepartamentoForm(FlaskForm):
    responsavel = StringField('Responsável', validators=[DataRequired()])
    descricao = StringField('Descrição')
    submit = SubmitField('Cadastrar')
