"""WTForms definitions for application-specific forms."""

from datetime import date

from flask_wtf import FlaskForm
from flask_wtf.file import FileField, MultipleFileField
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
    FieldList,
    IntegerField,
    widgets
)
from wtforms.validators import (
    DataRequired,
    Email,
    Optional,
    Length,
    EqualTo,
    ValidationError,
    URL,
    InputRequired,
    NumberRange,
)
import re

from app.services.courses import CourseStatus
from app.constants import EMPRESA_TAG_CHOICES

ANNOUNCEMENT_FILE_EXTENSIONS = (
    "pdf",
    "doc",
    "docx",
    "xls",
    "xlsx",
    "ppt",
    "pptx",
    "png",
    "jpg",
    "jpeg",
)

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
    # Usuário para login; entre 3 e 20 caracteres
    username = StringField('Usuário', validators=[DataRequired(), Length(min=3, max=20)])
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
    tipo_empresa = RadioField(
        'Tag do Cliente',
        choices=EMPRESA_TAG_CHOICES,
        validators=[DataRequired()],
        default="Matriz"
    )
    socio_administrador = StringField('Sócio Administrador', validators=[Optional()])
    atividade_principal = StringField(
        'Atividade Principal',
        validators=[Optional(), Length(max=200, message="Atividade principal deve ter no maximo 200 caracteres.")],
    )
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
    contatos_json = HiddenField('Contatos', validators=[Optional()])
    ativo = BooleanField('Empresa Ativa')
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


class AccessLinkForm(FlaskForm):
    """Formulário para criar novos atalhos na central de acessos."""

    category = SelectField(
        "Categoria do atalho",
        validators=[DataRequired()],
        choices=[],
    )
    label = StringField(
        "Nome do botão",
        validators=[DataRequired(), Length(max=100)],
        render_kw={"placeholder": "Ex.: Portal Prefeitura"},
    )
    url = StringField(
        "Endereço do site",
        validators=[DataRequired(), URL(require_tld=False), Length(max=255)],
        render_kw={"placeholder": "https://exemplo.com"},
    )
    description = TextAreaField(
        "Descrição (opcional)",
        validators=[Optional(), Length(max=255)],
        render_kw={"rows": 2},
    )
    submit = SubmitField("Criar atalho")


class AnnouncementForm(FlaskForm):
    """Formulário para criação de comunicados internos."""

    date = DateField(
        "Data",
        format="%Y-%m-%d",
        validators=[DataRequired()],
        default=date.today,
    )
    subject = StringField(
        "Assunto",
        validators=[DataRequired(), Length(min=1, max=255)],
        filters=[lambda value: value.strip() if value else value],
    )
    content = TextAreaField(
        "Mensagem",
        validators=[DataRequired(), Length(min=1, max=20000)],
        render_kw={
            "rows": 4,
        },
        filters=[lambda value: value.strip() if value else value],
    )
    attachments = MultipleFileField(
        "Anexos",
        validators=[Optional()],
        render_kw={"multiple": True},
    )
    submit = SubmitField("Salvar")


class OperationalProcedureForm(FlaskForm):
    """Formulário para cadastrar/editar procedimentos operacionais."""

    title = StringField(
        "Título",
        validators=[DataRequired(), Length(max=200)],
        filters=[lambda v: v.strip() if v else v],
    )
    descricao = TextAreaField("Descrição", validators=[Optional()])
    submit = SubmitField("Salvar procedimento")


class DiretoriaAcordoForm(FlaskForm):
    """Formulário para registrar acordos individuais da Diretoria JP."""

    title = StringField(
        "Título",
        validators=[DataRequired(), Length(max=150)],
        filters=[lambda value: value.strip() if value else value],
    )
    agreement_date = DateField(
        "Data do acordo",
        format="%Y-%m-%d",
        validators=[DataRequired()],
        default=date.today,
    )
    description = TextAreaField("Descrição", validators=[Optional()])
    notify_user = BooleanField("Enviar notificação por e-mail", default=False)
    notification_destination = RadioField(
        "Destinatário do e-mail",
        choices=[("user", "E-mail do usuário"), ("custom", "Outro e-mail")],
        default="user",
        validators=[Optional()],
    )
    notification_email = StringField(
        "E-mail personalizado",
        validators=[Optional(), Email(), Length(max=255)],
        filters=[lambda value: value.strip() if value else value],
    )
    submit = SubmitField("Salvar acordo")

    def validate(self, extra_validators=None):
        """Ensure notification settings are coherent when enabled."""

        if not super().validate(extra_validators=extra_validators):
            return False

        if self.notify_user.data:
            destination = (self.notification_destination.data or "user").strip()
            if destination not in {"user", "custom"}:
                self.notification_destination.data = "user"
                destination = "user"

            if destination == "custom":
                if not self.notification_email.data:
                    self.notification_email.errors.append(
                        "Informe um e-mail válido para o envio da notificação."
                    )
                    return False
        return True


class DiretoriaFeedbackForm(FlaskForm):
    """Formulário para registrar feedbacks individuais da Diretoria JP."""

    title = StringField(
        "Título",
        validators=[DataRequired(), Length(max=150)],
        filters=[lambda value: value.strip() if value else value],
    )
    feedback_date = DateField(
        "Data do feedback",
        format="%Y-%m-%d",
        validators=[DataRequired()],
        default=date.today,
    )
    description = TextAreaField("Descrição", validators=[Optional()])
    notify_user = BooleanField("Enviar notificação por e-mail", default=False)
    notification_destination = RadioField(
        "Destinatário do e-mail",
        choices=[("user", "E-mail do usuário"), ("custom", "Outro e-mail")],
        default="user",
        validators=[Optional()],
    )
    notification_email = StringField(
        "E-mail personalizado",
        validators=[Optional(), Email(), Length(max=255)],
        filters=[lambda value: value.strip() if value else value],
    )
    submit = SubmitField("Salvar feedback")

    def validate(self, extra_validators=None):
        """Ensure notification settings are coherent when enabled."""

        if not super().validate(extra_validators=extra_validators):
            return False

        if self.notify_user.data:
            destination = (self.notification_destination.data or "user").strip()
            if destination not in {"user", "custom"}:
                self.notification_destination.data = "user"
                destination = "user"

            if destination == "custom":
                if not self.notification_email.data:
                    self.notification_email.errors.append(
                        "Informe um e-mail válido para o envio da notificação."
                    )
                    return False
        return True


class CourseForm(FlaskForm):
    """Formulário para cadastrar cursos internos."""

    course_id = HiddenField()
    name = StringField(
        "Nome do Curso",
        validators=[DataRequired(), Length(max=150)],
    )
    instructor = StringField(
        "Nome do Instrutor",
        validators=[DataRequired(), Length(max=150)],
    )
    sectors = SelectMultipleField(
        "Setores Participantes",
        coerce=int,
        validators=[Length(min=1, message="Selecione ao menos um setor.")],
        option_widget=widgets.CheckboxInput(),
        widget=widgets.ListWidget(prefix_label=False),
    )
    participants = SelectMultipleField(
        "Participantes (Usuários)",
        coerce=int,
        validators=[Length(min=1, message="Selecione ao menos um participante.")],
        option_widget=widgets.CheckboxInput(),
        widget=widgets.ListWidget(prefix_label=False),
    )
    tags = SelectMultipleField(
        "Tags do Curso",
        coerce=int,
        validators=[Optional()],
        option_widget=widgets.CheckboxInput(),
        widget=widgets.ListWidget(prefix_label=False),
    )
    workload = TimeField(
        "Carga Horária",
        format="%H:%M",
        validators=[DataRequired()],
        render_kw={"step": 300},
    )
    start_date = DateField(
        "Data de Início",
        format="%Y-%m-%d",
        validators=[DataRequired()],
    )
    schedule_start = TimeField(
        "Horário de Início",
        format="%H:%M",
        validators=[DataRequired()],
        render_kw={"step": 300},
    )
    schedule_end = TimeField(
        "Horário de Fim",
        format="%H:%M",
        validators=[DataRequired()],
        render_kw={"step": 300},
    )
    completion_date = DateField(
        "Data de Conclusão",
        format="%Y-%m-%d",
        validators=[Optional()],
    )
    status = SelectField(
        "Status",
        choices=[(status.value, status.value.capitalize()) for status in CourseStatus],
        validators=[DataRequired()],
        default=CourseStatus.PLANNED.value,
    )
    observation = TextAreaField(
        "Observação",
        validators=[Optional(), Length(max=2000)],
        render_kw={"rows": 3},
    )
    submit = SubmitField("Salvar curso")
    submit_add_to_calendar = SubmitField("Adicionar no calendário")
    submit_delete = SubmitField("Excluir curso")


class CourseTagForm(FlaskForm):
    """Formulário para cadastrar novas tags de cursos."""

    name = StringField(
        "Nome da tag",
        validators=[DataRequired(), Length(max=80)],
        filters=[lambda value: value.strip() if value else value],
    )
    submit = SubmitField("Adicionar tag")

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


class DepartamentoAdministrativoForm(DepartamentoForm):
    """Formulário para o Departamento Administrativo."""
    particularidades_texto = TextAreaField('Particularidades', validators=[Optional()])


class DepartamentoFinanceiroForm(DepartamentoForm):
    """Formulário para o Departamento Financeiro."""
    particularidades_texto = TextAreaField('Particularidades', validators=[Optional()])


class ClienteReuniaoForm(FlaskForm):
    """Formulário para registrar reuniões com clientes."""

    data = DateField("Data da reunião", format="%Y-%m-%d", validators=[Optional()])
    participantes = SelectMultipleField(
        "Participantes",
        coerce=int,
        validators=[Optional()],
        option_widget=widgets.CheckboxInput(),
        widget=widgets.ListWidget(prefix_label=False),
    )
    setor_id = SelectField("Setor", coerce=int, validators=[Optional()], choices=[])
    topicos_json = HiddenField("Tópicos", validators=[Optional()])
    decisoes = TextAreaField("Decisões", validators=[Optional()])
    acompanhar_ate = DateField("Acompanhar até", format="%Y-%m-%d", validators=[Optional()])
    submit = SubmitField("Salvar reunião")


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


PAGAMENTO_CHOICES = [
    ('', ''),
    ('PIX', 'PIX'),
    ('DINHEIRO', 'DINHEIRO'),
    ('DÉBITO', 'DÉBITO')
]

ACORDO_CHOICES = [
    ('', ''),
    ('SEM ACORDO', 'SEM ACORDO'),
    ('OK - PAGO', 'OK - PAGO'),
    ('CORTESIA', 'CORTESIA'),
    ('A VISTA', 'A VISTA'),
    ('DEBITAR', 'DEBITAR'),
    ('TADEU H.', 'TADEU H.')
]


class NotaDebitoForm(FlaskForm):
    """Formulário para cadastro de notas para débito."""
    data_emissao = DateField('Data de Emissão', validators=[Optional()])
    empresa = StringField('Empresa', validators=[Optional()])
    notas = StringField('Notas', validators=[Optional()])
    qtde_itens = StringField('Qtde Itens', validators=[Optional()])
    valor_un = StringField('Valor UN', validators=[Optional()])
    total = StringField('Total', validators=[Optional()])
    acordo = StringField('Acordo', validators=[Optional()])
    forma_pagamento = SelectField('Pagamento', choices=PAGAMENTO_CHOICES, validators=[Optional()])
    tem_observacao = BooleanField('Adicionar Observação')
    observacao = TextAreaField('Observação', validators=[Optional()])
    submit = SubmitField('Salvar')


class CadastroNotaForm(FlaskForm):
    """Formulário para cadastro de notas."""
    cadastro = StringField('Cadastro', validators=[Optional()])
    valor = StringField('Valor', validators=[Optional()])
    acordo = SelectField('Acordos', choices=ACORDO_CHOICES, validators=[Optional()])
    usuario = StringField('Usuário', validators=[Optional(), Length(max=255)])
    senha = StringField('Senha', validators=[Optional(), Length(max=255)])
    submit = SubmitField('Salvar')


class NotaRecorrenteForm(FlaskForm):
    """Formulário para notas fiscais emitidas de forma recorrente."""

    empresa = StringField('Empresa', validators=[DataRequired(), Length(max=255)])
    descricao = StringField('Descrição', validators=[Optional(), Length(max=255)])
    valor = StringField('Valor', validators=[Optional(), Length(max=50)])
    periodo_inicio = IntegerField(
        'Dia inicial do período',
        validators=[DataRequired(), NumberRange(min=1, max=31)],
    )
    periodo_fim = IntegerField(
        'Dia final do período',
        validators=[DataRequired(), NumberRange(min=1, max=31)],
    )
    dia_emissao = IntegerField(
        'Dia da emissão',
        validators=[DataRequired(), NumberRange(min=1, max=31)],
    )
    observacao = TextAreaField('Observação', validators=[Optional()])
    ativo = BooleanField('Ativo', default=True)
    submit = SubmitField('Salvar nota recorrente')


class TagForm(FlaskForm):
    """Formulário para cadastro de tags."""
    nome = StringField('Tag', validators=[DataRequired()])
    submit = SubmitField('Salvar')


class TagDeleteForm(FlaskForm):
    """Formulário para exclusão de tags."""

    tag_id = HiddenField(validators=[DataRequired()])
    submit = SubmitField('Excluir')


class MeetingForm(FlaskForm):
    """Formulário para agendamento de reuniões."""
    participants = SelectMultipleField(
        "Participantes",
        coerce=int,
        validators=[Length(min=1, message="Selecione pelo menos um participante")],
        option_widget=widgets.CheckboxInput(),
        widget=widgets.ListWidget(prefix_label=False),
    )
    meeting_id = HiddenField()
    course_id = HiddenField()
    date = DateField("Data da Reunião", format="%Y-%m-%d", validators=[DataRequired()])
    start_time = TimeField("Hora de Início", format="%H:%M", validators=[DataRequired()])
    end_time = TimeField("Hora de Fim", format="%H:%M", validators=[DataRequired()])
    subject = StringField("Assunto", validators=[DataRequired()], render_kw={"placeholder": "Assunto"})
    description = TextAreaField(
        "Descrição (opcional)", validators=[Optional()], render_kw={"placeholder": "Detalhes", "rows": 3}
    )
    create_meet = BooleanField("Gerar sala no Google Meet")
    notify_attendees = BooleanField(
        "Notificar participantes por e-mail",
        default=True,
    )
    # Campos de recorrência
    recorrencia_tipo = SelectField(
        "Repetir",
        choices=[
            ('NENHUMA', 'Não repetir'),
            ('DIARIA', 'Diariamente'),
            ('SEMANAL', 'Semanalmente'),
            ('QUINZENAL', 'A cada 2 semanas'),
            ('MENSAL', 'Mensalmente'),
            ('ANUAL', 'Anualmente'),
        ],
        default='NENHUMA',
        validators=[Optional()],
    )
    recorrencia_fim = DateField(
        "Repetir até",
        format="%Y-%m-%d",
        validators=[Optional()],
    )
    recorrencia_dias_semana = SelectMultipleField(
        "Repetir nos dias",
        choices=[
            ('0', 'Segunda-feira'),
            ('1', 'Terça-feira'),
            ('2', 'Quarta-feira'),
            ('3', 'Quinta-feira'),
            ('4', 'Sexta-feira'),
            ('5', 'Sábado'),
            ('6', 'Domingo'),
        ],
        validators=[Optional()],
        option_widget=widgets.CheckboxInput(),
        widget=widgets.ListWidget(prefix_label=False),
    )
    submit = SubmitField("Agendar")

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators):
            return False

        # Validar recorrência
        if self.recorrencia_tipo.data and self.recorrencia_tipo.data != 'NENHUMA':
            if not self.recorrencia_fim.data:
                self.recorrencia_fim.errors.append(
                    "Informe até quando a reunião deve se repetir."
                )
                return False
            if self.recorrencia_fim.data <= self.date.data:
                self.recorrencia_fim.errors.append(
                    "A data final deve ser posterior à data inicial."
                )
                return False

        return True


class MeetConfigurationForm(FlaskForm):
    """Formulário para configurar opções da sala do Google Meet."""

    meeting_id = HiddenField(validators=[DataRequired()])
    host_id = SelectField(
        "Proprietário da sala",
        choices=[],
        coerce=int,
        validators=[InputRequired()],
        default=None,
    )
    # Commented out: Google Meet API not enabled
    # quick_access_enabled = BooleanField("Acesso rápido habilitado", default=True)
    # mute_on_join = BooleanField("Silenciar participantes ao entrar")
    # allow_chat = BooleanField("Permitir chat durante a reunião", default=True)
    # allow_screen_share = BooleanField("Permitir compartilhamento de tela", default=True)
    submit = SubmitField("Salvar configurações")


class GeneralCalendarEventForm(FlaskForm):
    """Formulário para eventos do calendário interno."""

    participants = SelectMultipleField(
        "Participantes",
        coerce=int,
        validators=[Optional()],
        option_widget=widgets.CheckboxInput(),
        widget=widgets.ListWidget(prefix_label=False),
    )
    is_birthday = BooleanField("Marcar como aniversário", default=False)
    is_absence = BooleanField("Ausência", default=False)
    is_vacation = BooleanField("Férias", default=False)
    is_notice = BooleanField("Aviso (Todo o escritório)", default=False)
    birthday_user_id = SelectField(
        "Colaborador aniversariante",
        coerce=int,
        validators=[Optional()],
        default=0,
        choices=[],
    )
    birthday_recurs_annually = BooleanField(
        "Repetir todos os anos",
        default=True,
    )
    birthday_recurrence_years = SelectField(
        "Repetir pelos próximos",
        choices=[(i, f"{i} ano" + ("s" if i > 1 else "")) for i in range(1, 11)],
        coerce=int,
        default=1,
        validators=[Optional()],
    )
    event_id = HiddenField()
    start_date = DateField(
        "Data inicial",
        format="%Y-%m-%d",
        validators=[DataRequired()],
        render_kw={"min": "1900-01-01"},
    )
    end_date = DateField(
        "Data final (opcional)",
        format="%Y-%m-%d",
        validators=[Optional()],
        render_kw={"min": "1900-01-01"},
    )
    start_time = TimeField(
        "Hora inicial (opcional)",
        format="%H:%M",
        validators=[Optional()],
        render_kw={"step": 60},
    )
    end_time = TimeField(
        "Hora final (opcional)",
        format="%H:%M",
        validators=[Optional()],
        render_kw={"step": 60},
    )
    title = StringField(
        "Título",
        validators=[DataRequired(), Length(max=100)],
        render_kw={"placeholder": "Título do evento"},
    )
    description = TextAreaField(
        "Descrição (opcional)",
        validators=[Optional()],
        render_kw={"rows": 3, "placeholder": "Informações adicionais"},
    )
    submit = SubmitField("Salvar")

    def validate_end_date(self, field):
        if field.data and self.start_date.data and field.data < self.start_date.data:
            raise ValidationError("A data final deve ser igual ou posterior à data inicial.")

    def validate(self, extra_validators=None):
        if self.is_birthday.data and self.birthday_user_id.data:
            try:
                selected_participants = set(self.participants.data or [])
            except TypeError:
                selected_participants = set()
            if self.birthday_user_id.data not in selected_participants:
                selected_participants.add(self.birthday_user_id.data)
                self.participants.data = list(selected_participants)
        if not self.is_birthday.data:
            self.birthday_recurs_annually.data = False
            self.birthday_recurrence_years.data = 1
        if not super().validate(extra_validators):
            return False
        # Validar que pelo menos um participante foi selecionado (exceto para avisos)
        if not self.is_notice.data and (not self.participants.data or len(self.participants.data) == 0):
            self.participants.errors.append("Selecione pelo menos um participante ou marque como Aviso")
            return False
        # Validar que apenas um tipo especial foi selecionado
        special_flags = sum(
            bool(flag)
            for flag in (
                self.is_birthday.data,
                self.is_absence.data,
                self.is_vacation.data,
                self.is_notice.data,
            )
        )
        if special_flags > 1:
            message = "Selecione apenas um tipo especial por evento."
            for field in (self.is_birthday, self.is_absence, self.is_vacation, self.is_notice):
                field.errors.append(message)
            return False
        start_date = self.start_date.data
        end_date = self.end_date.data or start_date
        start_time = self.start_time.data
        end_time = self.end_time.data
        if start_time and not end_time:
            self.end_time.errors.append("Informe a hora de término.")
            return False
        if end_time and not start_time:
            self.start_time.errors.append("Informe a hora de início.")
            return False
        if start_date and end_date and start_date != end_date and (start_time or end_time):
            self.start_time.errors.append("Remova os horários para eventos com mais de um dia.")
            return False
        if start_time and end_time and end_time <= start_time:
            self.end_time.errors.append("A hora de término deve ser posterior à hora de início.")
            return False
        if self.is_birthday.data:
            if not self.birthday_user_id.data:
                self.birthday_user_id.errors.append("Selecione o colaborador aniversariante.")
                return False
            if end_date != start_date:
                self.end_date.errors.append("Aniversários devem ocorrer em um único dia.")
                return False
            if self.birthday_recurs_annually.data:
                try:
                    years = int(self.birthday_recurrence_years.data or 1)
                except (TypeError, ValueError):
                    years = 0
                if years < 1:
                    self.birthday_recurrence_years.errors.append("Informe pelo menos 1 ano de recorrência.")
                    return False
                self.birthday_recurrence_years.data = years
        return True


class TaskForm(FlaskForm):
    """Formulário para criação de tarefas."""

    title = StringField("Título", validators=[DataRequired()])
    description = TextAreaField(
        "Descrição", validators=[Optional()], render_kw={"rows": 4}
    )
    tag_id = SelectField("Setor", coerce=int, validators=[DataRequired()])
    assigned_to = SelectField(
        "Usuário", coerce=int, validators=[Optional()], default=0, choices=[]
    )
    only_me = BooleanField(
        "Somente para mim",
        false_values=(False, "false", "", "0"),
        default=False,
    )
    follow_up_users = SelectMultipleField(
        "Usuários para acompanhamento",
        coerce=int,
        validators=[Optional()],
        choices=[],
        render_kw={"class": "form-select"},
    )
    follow_up = BooleanField(
        "Acompanhamento",
        false_values=(False, "false", "", "0"),
        default=False,
    )
    priority = SelectField(
        "Prioridade",
        choices=[("low", "Baixa"), ("medium", "Média"), ("high", "Alta")],
        default="medium",
        validators=[DataRequired()],
    )
    due_date = DateField("Prazo", format="%Y-%m-%d", validators=[Optional()])
    attachments = MultipleFileField(
        "Anexos / Prints",
        validators=[Optional()],
        render_kw={"multiple": True, "accept": "image/*,.pdf,.doc,.docx,.xls,.xlsx,.txt"},
    )
    parent_id = HiddenField()
    task_id = HiddenField()
    submit = SubmitField("Salvar")


class ManualCategoryForm(FlaskForm):
    """Formulário para CRUD de categorias do manual."""

    name = StringField(
        "Nome da categoria",
        validators=[DataRequired(), Length(max=100)]
    )
    description = StringField(
        "Descrição (opcional)",
        validators=[Optional(), Length(max=255)]
    )
    submit = SubmitField("Salvar categoria")


class ManualVideoForm(FlaskForm):
    """Formulário para upload e edição de vídeos do manual."""

    title = StringField(
        "Título do vídeo",
        validators=[DataRequired(), Length(max=200)]
    )
    description = TextAreaField(
        "Descrição (opcional)",
        validators=[Optional()],
        render_kw={"rows": 4}
    )
    category_id = SelectField(
        "Categoria",
        coerce=int,
        validators=[DataRequired()],
        choices=[]
    )
    video_file = FileField(
        "Arquivo de vídeo",
        validators=[Optional()],
        render_kw={"accept": "video/mp4,video/webm"}
    )
    thumbnail = FileField(
        "Thumbnail (opcional)",
        validators=[Optional()],
        render_kw={"accept": "image/*"}
    )
    submit = SubmitField("Salvar vídeo")
