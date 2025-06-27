from flask_wtf import FlaskForm
from wtforms import StringField, RadioField, SubmitField, DateField, SelectMultipleField, SelectField, TextAreaField, PasswordField, MultipleFileField
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


class DepartamentoFiscalForm(DepartamentoForm):
    formas_importacao = SelectMultipleField('Formas de Importação', choices=[
        ('entradas_sped', 'Entradas por Sped'),
        ('entradas_xml', 'Entradas por XML'),
        ('entradas_sat', 'Entradas pelo SAT'),
        ('entradas_sieg', 'Entradas pelo Sieg'),
        ('saidas_sped', 'Saídas por Sped'),
        ('saidas_xml', 'Saídas por XML'),
        ('saidas_sieg', 'Saídas pelo SIEG'),
        ('nfce_sped', 'NFCe por Sped'),
        ('nfce_xml_sieg', 'NFCe por XML - Sieg'),
        ('nfce_xml_cliente', 'NFCe por XML - Copiado do cliente'),
        ('nenhum', 'Não importa nada')
    ])
    link_prefeitura = StringField('Link Prefeitura')
    usuario_prefeitura = StringField('Usuário Prefeitura')
    senha_prefeitura = PasswordField('Senha Prefeitura')
    forma_movimento = SelectField('Forma de Recebimento do Movimento', choices=[
        ('digital', 'Digital'),
        ('fisico', 'Físico'),
        ('ambos', 'Digital e Físico')
    ])
    envio_digital = SelectMultipleField('Envio Digital', choices=[
        ('email', 'Email'),
        ('whatsapp', 'Whatsapp'),
        ('skype', 'Skype'),
        ('acessorias', 'Acessórias')
    ])
    envio_digital_fisico = SelectMultipleField('Envio Digital e Físico', choices=[
        ('email', 'Email'),
        ('whatsapp', 'Whatsapp'),
        ('skype', 'Skype'),
        ('acessorias', 'Acessórias'),
        ('malote', 'Malote')
    ])
    observacao_movimento = StringField('Observação')
    contato_nome = StringField('Nome do Contato')
    contato_meios = SelectMultipleField('Formas de Contato', choices=[
        ('email', 'E-mail'),
        ('whatsapp', 'Whatsapp'),
        ('skype', 'Skype'),
        ('ligacao', 'Ligação Telefônica'),
        ('acessorias', 'Acessórias')
    ])
    particularidades = TextAreaField('Particularidades')
    particularidades_imagens = MultipleFileField('Imagens')


class DepartamentoContabilForm(DepartamentoForm):
    metodo_importacao = SelectField('Forma de Importação', choices=[
        ('importado', 'Importado'),
        ('digitado', 'Digitado')
    ])
    observacao_importacao = StringField('Observação Importação')
    forma_movimento = SelectField('Forma de Recebimento do Movimento', choices=[
        ('digital', 'Digital'),
        ('fisico', 'Físico'),
        ('ambos', 'Digital e Físico')
    ])
    envio_digital = SelectMultipleField('Envio Digital', choices=[
        ('email', 'Email'),
        ('whatsapp', 'Whatsapp'),
        ('skype', 'Skype'),
        ('acessorias', 'Acessórias')
    ])
    envio_digital_fisico = SelectMultipleField('Envio Digital e Físico', choices=[
        ('email', 'Email'),
        ('whatsapp', 'Whatsapp'),
        ('skype', 'Skype'),
        ('acessorias', 'Acessórias'),
        ('malote', 'Malote')
    ])
    observacao_movimento = StringField('Observação Movimento')
    controle_relatorios = SelectMultipleField('Controle por Relatórios', choices=[
        ('forn_cli_cota_unica', 'Fornecedor e clientes cota unica'),
        ('saldo_final_mes', 'Relatório com saldo final do mês'),
        ('adiantamentos', 'Relatório de adiantamentos'),
        ('contas_pagas', 'Relatório de contas pagas'),
        ('contas_recebidas', 'Relatório de contas recebidas'),
        ('adiantamentos2', 'Relatório de adiantamentos'),
        ('conferir_aplicacao', 'Conferir aplicação')
    ])
    observacao_controle_relatorios = StringField('Observação Relatórios')
    particularidades = TextAreaField('Particularidades')
    particularidades_imagens = MultipleFileField('Imagens')


class DepartamentoPessoalForm(DepartamentoForm):
    data_envio = StringField('Data de Envio')
    registro_funcionarios = StringField('Registro de Funcionários')
    ponto_eletronico = StringField('Ponto Eletrônico')
    pagamento_funcionario = StringField('Pagamento de Funcionário')
    particularidades = TextAreaField('Particularidades')
    particularidades_imagens = MultipleFileField('Imagens')
