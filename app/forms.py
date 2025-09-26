"""WTForms definitions for application-specific forms."""

from datetime import date
from decimal import Decimal, InvalidOperation

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
    FieldList,
    IntegerField,
    DecimalField,
    widgets,
)
from wtforms.validators import (
    DataRequired,
    Email,
    Optional,
    Length,
    EqualTo,
    ValidationError,
    URL,
    NumberRange,
    InputRequired,
    AnyOf,
)
import re
import json

from app.services.courses import CourseStatus

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


class AccessLinkForm(FlaskForm):
    """Formulário para criar novos atalhos na central de acessos."""

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
    )
    submit = SubmitField("Salvar curso")
    submit_add_to_calendar = SubmitField("Adicionar no calendário")
    submit_delete = SubmitField("Excluir curso")


# Opções padrão de "Sim" e "Não" utilizadas em selects binários.
BOOLEAN_SELECT_CHOICES: list[tuple[int, str]] = [(0, "Não"), (1, "Sim")]


class ManagementEventForm(FlaskForm):
    """Formulário utilizado pela Diretoria JP para registrar eventos."""

    event_type = SelectField(
        "Tipo de Registro",
        choices=[
            ("treinamento", "Treinamento"),
            ("data_comemorativa", "Data Comemorativa"),
            ("evento", "Evento"),
        ],
        validators=[DataRequired(message="Selecione o tipo de registro.")],
        default="treinamento",
    )
    event_date = DateField(
        "Data",
        format="%Y-%m-%d",
        validators=[DataRequired(message="Informe a data do evento.")],
    )
    description = TextAreaField(
        "Descrição",
        validators=[DataRequired(message="Informe a descrição do evento."), Length(max=1000)],
        render_kw={"rows": 3},
    )
    attendance_scope = SelectField(
        "Participação",
        choices=[
            ("interna", "Interna"),
            ("externa", "Externa"),
            ("ambos", "Ambos"),
        ],
        validators=[
            DataRequired(message="Informe o tipo de participação."),
            AnyOf(["interna", "externa", "ambos"]),
        ],
        default="interna",
    )
    participants_count = IntegerField(
        "Participantes (Nº de pessoas)",
        validators=[
            InputRequired(message="Informe o número de participantes."),
            NumberRange(min=0, message="O número de participantes deve ser positivo."),
        ],
        render_kw={"min": 0},
    )
    include_breakfast = SelectField(
        "Café da manhã",
        choices=BOOLEAN_SELECT_CHOICES,
        coerce=int,
        default=0,
    )
    cost_breakfast = DecimalField(
        "Custo total",
        places=2,
        rounding=None,
        validators=[Optional()],
        render_kw={"min": 0, "step": "0.01"},
    )
    breakfast_items_raw = HiddenField()
    include_lunch = SelectField(
        "Almoço",
        choices=BOOLEAN_SELECT_CHOICES,
        coerce=int,
        default=0,
    )
    cost_lunch = DecimalField(
        "Custo total",
        places=2,
        rounding=None,
        validators=[Optional()],
        render_kw={"min": 0, "step": "0.01"},
    )
    lunch_items_raw = HiddenField()
    include_dinner = SelectField(
        "Janta",
        choices=BOOLEAN_SELECT_CHOICES,
        coerce=int,
        default=0,
    )
    cost_dinner = DecimalField(
        "Custo total",
        places=2,
        rounding=None,
        validators=[Optional()],
        render_kw={"min": 0, "step": "0.01"},
    )
    dinner_items_raw = HiddenField()
    other_materials_raw = HiddenField()
    submit = SubmitField("Salvar evento")

    def validate(self, extra_validators=None):
        """Validate catering selections and normalize additional materials."""

        if not super().validate(extra_validators=extra_validators):
            return False

        valid = True

        def _to_decimal(value: str | None) -> Decimal | None:
            if value is None:
                return None
            raw = value.strip()
            if not raw:
                return None
            normalized = raw.replace(" ", "")
            if normalized.count(",") == 1 and normalized.count(".") == 0:
                normalized = normalized.replace(",", ".")
            elif normalized.count(",") == 1 and normalized.count(".") >= 1:
                normalized = normalized.replace(".", "").replace(",", ".")
            else:
                normalized = normalized.replace(",", "")
            try:
                return Decimal(normalized)
            except InvalidOperation:
                return None
        def _parse_service_items(raw_value: str) -> list[dict[str, str]] | None:
            try:
                parsed = json.loads(raw_value or "[]")
            except json.JSONDecodeError:
                return None

            if not isinstance(parsed, list):
                return None

            cleaned: list[dict[str, str]] = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                description = (item.get("description") or "").strip()
                quantity = (item.get("quantity") or "").strip()
                unit_cost = (item.get("unit_cost") or "").strip()
                total_cost = (item.get("total_cost") or "").strip()
                if not any([description, quantity, unit_cost, total_cost]):
                    continue
                cleaned.append(
                    {
                        "description": description,
                        "quantity": quantity,
                        "unit_cost": unit_cost,
                        "total_cost": total_cost,
                    }
                )
            return cleaned

        self._service_items: dict[str, list[dict[str, str]]] = {}
        self._service_totals: dict[str, Decimal] = {}

        catering_groups = [
            ("breakfast", self.include_breakfast, self.cost_breakfast, self.breakfast_items_raw),
            ("lunch", self.include_lunch, self.cost_lunch, self.lunch_items_raw),
            ("dinner", self.include_dinner, self.cost_dinner, self.dinner_items_raw),
        ]

        for slug, include_field, total_field, raw_items_field in catering_groups:
            if include_field.data == 1:
                items = _parse_service_items(raw_items_field.data or "[]")
                if items is None:
                    raw_items_field.errors.append(
                        "Não foi possível processar os itens informados."
                    )
                    valid = False
                    continue

                total_sum = Decimal("0")
                normalized_items: list[dict[str, str]] = []

                for item in items:
                    description = item.get("description", "")
                    quantity_raw = item.get("quantity", "")
                    unit_cost_raw = item.get("unit_cost", "")
                    line_total_raw = item.get("total_cost", "")

                    quantity_decimal = _to_decimal(quantity_raw)
                    unit_cost_decimal = _to_decimal(unit_cost_raw)
                    line_total_decimal = _to_decimal(line_total_raw)

                    if quantity_decimal is not None and quantity_decimal < 0:
                        raw_items_field.errors.append(
                            "As quantidades devem ser positivas."
                        )
                        valid = False
                        break

                    if unit_cost_decimal is not None and unit_cost_decimal < 0:
                        raw_items_field.errors.append(
                            "Os valores unitários devem ser positivos."
                        )
                        valid = False
                        break

                    if line_total_decimal is None and quantity_decimal is not None and unit_cost_decimal is not None:
                        line_total_decimal = quantity_decimal * unit_cost_decimal

                    if line_total_decimal is None:
                        if quantity_decimal is None and unit_cost_decimal is None and not description:
                            continue
                        raw_items_field.errors.append(
                            "Informe o total ou dados suficientes para calcular o custo do item."
                        )
                        valid = False
                        break

                    if line_total_decimal < 0:
                        raw_items_field.errors.append(
                            "Os custos totais devem ser positivos."
                        )
                        valid = False
                        break

                    total_sum += line_total_decimal

                    normalized_items.append(
                        {
                            "description": description,
                            "quantity": quantity_raw,
                            "unit_cost": unit_cost_raw,
                            "total_cost": f"{line_total_decimal:.2f}",
                        }
                    )

                if not valid:
                    continue

                total_value = total_sum
                if total_field.data is None:
                    total_field.data = total_sum
                elif total_field.data < 0:
                    total_field.errors.append(
                        "Informe um valor positivo para o custo total."
                    )
                    valid = False
                else:
                    try:
                        total_value = Decimal(str(total_field.data))
                    except InvalidOperation:
                        total_field.errors.append(
                            "Informe um valor válido para o custo total."
                        )
                        valid = False
                raw_items_field.data = json.dumps(normalized_items, ensure_ascii=False)
                self._service_items[slug] = normalized_items
                self._service_totals[slug] = total_value
            else:
                total_field.data = None
                raw_items_field.data = json.dumps([], ensure_ascii=False)
                self._service_items[slug] = []
                self._service_totals[slug] = Decimal("0")

        raw_materials = self.other_materials_raw.data or "[]"
        try:
            parsed_materials = json.loads(raw_materials)
        except json.JSONDecodeError:
            self.other_materials_raw.errors.append(
                "Não foi possível processar os outros materiais informados."
            )
            return False

        if not isinstance(parsed_materials, list):
            self.other_materials_raw.errors.append("Formato inválido para outros materiais.")
            return False

        cleaned_materials: list[dict[str, str]] = []
        for item in parsed_materials:
            if not isinstance(item, dict):
                continue
            description = (item.get("description") or "").strip()
            value = (item.get("value") or "").strip()
            if not description and not value:
                continue
            if value and not re.match(r"^\d+(?:[\.,]\d{0,2})?$", value):
                self.other_materials_raw.errors.append(
                    "Informe valores numéricos válidos para os materiais adicionais."
                )
                return False
            cleaned_materials.append({"description": description, "value": value})

        self.other_materials_raw.data = json.dumps(cleaned_materials, ensure_ascii=False)

        materials_total = Decimal("0")
        for item in cleaned_materials:
            material_value = _to_decimal(item.get("value", "") if isinstance(item, dict) else "")
            if material_value is not None:
                if material_value < 0:
                    self.other_materials_raw.errors.append(
                        "Os valores dos materiais devem ser positivos."
                    )
                    return False
                materials_total += material_value

        self._other_materials = cleaned_materials
        self._other_materials_total = materials_total
        self._event_total = sum(self._service_totals.values(), Decimal("0")) + materials_total

        return valid

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
    apply_more_days = BooleanField("Aplicar a mais dias")
    additional_dates = FieldList(
        DateField(
            "Aplicar também em",
            format="%Y-%m-%d",
            validators=[Optional()],
        ),
        min_entries=1,
    )
    submit = SubmitField("Agendar")

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators):
            return False
        if self.apply_more_days.data:
            valid_dates: list[date] = []
            has_error = False
            for idx, field in enumerate(self.additional_dates):
                if not field.data:
                    continue
                if field.data == self.date.data:
                    field.errors.append(
                        "Escolha uma data diferente da reunião original."
                    )
                    has_error = True
                    continue
                if field.data in valid_dates:
                    field.errors.append("Datas duplicadas não são permitidas.")
                    has_error = True
                    continue
                valid_dates.append(field.data)
            if not valid_dates:
                if self.additional_dates:
                    self.additional_dates[0].errors.append(
                        "Selecione pelo menos uma data adicional para replicar a reunião."
                    )
                else:
                    self.additional_dates.errors.append(
                        "Selecione pelo menos uma data adicional para replicar a reunião."
                    )
                return False
            if has_error:
                return False
        return True


class GeneralCalendarEventForm(FlaskForm):
    """Formulário para eventos do calendário interno."""

    participants = SelectMultipleField(
        "Participantes",
        coerce=int,
        validators=[Length(min=1, message="Selecione pelo menos um participante")],
        option_widget=widgets.CheckboxInput(),
        widget=widgets.ListWidget(prefix_label=False),
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
        if not super().validate(extra_validators):
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
    priority = SelectField(
        "Prioridade",
        choices=[("low", "Baixa"), ("medium", "Média"), ("high", "Alta")],
        default="medium",
        validators=[DataRequired()],
    )
    due_date = DateField("Prazo", format="%Y-%m-%d", validators=[Optional()])
    parent_id = HiddenField()
    submit = SubmitField("Salvar")
