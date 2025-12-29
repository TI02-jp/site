"""
Blueprint para controle de notas de débito.

Este módulo contém rotas para gerenciar:
- Cadastros de notas (empresas e notas fiscais)
- Notas recorrentes com notificações automáticas
- Totalizador de notas com filtros e agrupamentos

Rotas:
    - GET /controle-notas/debito: Redirect para cadastro
    - POST /controle-notas/debito/<id>/forma-pagamento: Atualiza forma de pagamento
    - GET/POST /controle-notas/cadastro: CRUD de cadastros e notas
    - GET/POST /controle-notas/recorrentes: CRUD de notas recorrentes
    - GET /controle-notas/totalizador: Relatório com agregações

Dependências:
    - models: NotaDebito, CadastroNota, NotaRecorrente, User, TaskNotification
    - forms: NotaDebitoForm, CadastroNotaForm, NotaRecorrenteForm, PAGAMENTO_CHOICES

Autor: Refatoração automatizada
Data: 2024-12
"""

import calendar
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any

import sqlalchemy as sa
import pandas as pd
from fpdf import FPDF
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from app import db
from app.controllers.routes._base import utc3_now
from app.controllers.routes._decorators import meeting_only_access_check
from app.forms import (
    PAGAMENTO_CHOICES,
    CadastroNotaForm,
    NotaDebitoForm,
    NotaRecorrenteForm,
)
from app.models.tables import (
    CadastroNota,
    NotaDebito,
    NotaRecorrente,
    NotificationType,
    TaskNotification,
    User,
)
from app.utils.permissions import is_user_admin


# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

notas_bp = Blueprint('notas', __name__)


# =============================================================================
# CONSTANTES
# =============================================================================

_CONTROLE_NOTAS_ALLOWED_TAGS = {"gestao", "gestão", "financeiro", "emissornfe"}


# =============================================================================
# FUNÇÕES AUXILIARES - CONTROLE DE ACESSO
# =============================================================================

def can_access_controle_notas() -> bool:
    """
    Verifica se o usuário atual pode acessar o módulo de Controle de Notas.

    Regras:
    - Admin sempre tem acesso
    - Usuários com tags: Gestão, Financeiro ou Emissor NFe

    Returns:
        bool: True se o usuário tem permissão
    """
    if not current_user.is_authenticated:
        return False

    if is_user_admin(current_user):
        return True

    # Importa função user_has_tag para verificar tags
    from app.controllers.routes import user_has_tag
    if user_has_tag('Gestão') or user_has_tag('Financeiro') or user_has_tag('Emissor NFe'):
        return True

    return False


def can_access_notas_totalizador() -> bool:
    """
    Verifica se o usuário pode acessar a view de Totalizador de Notas.

    Regras:
    - Admin sempre tem acesso
    - Usuários com tag Financeiro

    Returns:
        bool: True se o usuário tem permissão
    """
    if not current_user.is_authenticated:
        return False

    if is_user_admin(current_user):
        return True

    from app.controllers.routes import user_has_tag
    return user_has_tag('Financeiro')


# =============================================================================
# FUNÇÕES AUXILIARES - CONVERSÃO E FORMATAÇÃO
# =============================================================================

def _parse_decimal_input(raw_value: str | None) -> Decimal | None:
    """
    Converte uma string localizada em Decimal.

    Suporta formatos brasileiros como "1.234,56" e "R$ 1.234,56".

    Args:
        raw_value: String com valor numérico localizado

    Returns:
        Decimal ou None se inválido
    """
    if not raw_value:
        return None
    cleaned = (raw_value or "").strip()
    if not cleaned:
        return None
    cleaned = cleaned.replace("R$", "").replace(" ", "")
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _format_decimal_input(value: Decimal | float | None) -> str:
    """
    Formata Decimal para string aceita pelo modal de nota recorrente.

    Formato brasileiro: "1.234,56"

    Args:
        value: Valor numérico para formatar

    Returns:
        String formatada ou vazia se None
    """
    if value is None:
        return ""
    if not isinstance(value, Decimal):
        value = Decimal(value)
    return (
        f"{value:,.2f}"
        .replace(",", "_")
        .replace(".", ",")
        .replace("_", ".")
    )


def _format_currency_br(value: Decimal | float | int | None) -> str:
    """Formata valor monetario em estilo brasileiro."""
    number = Decimal(value or 0)
    return (
        f"R$ {number:,.2f}"
        .replace(",", "_")
        .replace(".", ",")
        .replace("_", ".")
    )


def _parse_date_str(value: str | None) -> date | None:
    """Parseia string no formato ISO (YYYY-MM-DD) em date segura."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _wrap_text(text: str, pdf_obj: FPDF, max_width: float) -> list[str]:
    """Quebra texto em linhas respeitando a largura informada."""
    if text is None:
        return [""]
    text = str(text)
    if not text:
        return [""]
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if pdf_obj.get_string_width(candidate) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            # Se palavra sozinha estoura, quebra por caracteres
            chunk = ""
            for ch in word:
                if pdf_obj.get_string_width(chunk + ch) <= max_width:
                    chunk += ch
                else:
                    if chunk:
                        lines.append(chunk)
                    chunk = ch
            current = chunk
    if current:
        lines.append(current)
    return lines or [""]


# =============================================================================
# FUNÇÕES AUXILIARES - DATAS E RECORRÊNCIA
# =============================================================================

def _get_month_day(year: int, month: int, desired_day: int) -> date:
    """
    Retorna data válida para o dia desejado dentro do ano/mês.

    Ajusta automaticamente para o último dia do mês se necessário.

    Args:
        year: Ano
        month: Mês (1-12)
        desired_day: Dia desejado (1-31)

    Returns:
        date: Data válida ajustada
    """
    last_day = calendar.monthrange(year, month)[1]
    clamped_day = max(1, min(desired_day, last_day))
    return date(year, month, clamped_day)


def _next_emission_date(recorrente: NotaRecorrente, reference: date | None = None) -> date:
    """
    Retorna a próxima data de emissão para uma nota recorrente.

    Args:
        recorrente: Registro de nota recorrente
        reference: Data de referência (default: hoje)

    Returns:
        date: Próxima data de emissão
    """
    if reference is None:
        reference = date.today()

    candidate = _get_month_day(reference.year, reference.month, recorrente.dia_emissao)
    if candidate >= reference:
        return candidate

    next_month = reference.month + 1
    next_year = reference.year
    if next_month > 12:
        next_month = 1
        next_year += 1
    return _get_month_day(next_year, next_month, recorrente.dia_emissao)


# =============================================================================
# FUNÇÕES AUXILIARES - NOTIFICAÇÕES
# =============================================================================

def _normalize_tag_slug(name: str | None) -> str:
    """
    Normaliza labels de tags para comparações.

    Remove acentos e converte para lowercase sem espaços.

    Args:
        name: Nome da tag

    Returns:
        str: Slug normalizado
    """
    if not name:
        return ""
    normalized = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return ascii_only.lower().replace(" ", "")


def _controle_notas_notification_user_ids() -> list[int]:
    """
    Retorna IDs dos usuários que devem receber lembretes de Controle de Notas.

    Inclui:
    - Todos os admins
    - Usuários com tags: Gestão, Financeiro, Emissor NFe

    Returns:
        list[int]: Lista de IDs de usuários
    """
    users = (
        User.query.options(joinedload(User.tags))
        .filter(User.ativo.is_(True))
        .all()
    )
    ids: list[int] = []
    for user in users:
        if not user.id:
            continue
        if is_user_admin(user):
            ids.append(user.id)
            continue
        for tag in getattr(user, "tags", []) or []:
            slug = _normalize_tag_slug(getattr(tag, "nome", ""))
            if slug in _CONTROLE_NOTAS_ALLOWED_TAGS:
                ids.append(user.id)
                break
    return ids


def _trigger_recorrente_notifications(reference_date: date | None = None) -> int:
    """
    Emite notificações para notas fiscais recorrentes devido na data de referência.

    Verifica todas as notas recorrentes ativas e cria notificações para
    os usuários autorizados quando a data de emissão corresponde à data de referência.

    Args:
        reference_date: Data para verificar (default: hoje)

    Returns:
        int: Número de notificações criadas
    """
    today = reference_date or date.today()
    ativos = NotaRecorrente.query.filter(NotaRecorrente.ativo.is_(True)).all()
    if not ativos:
        return 0

    user_ids = _controle_notas_notification_user_ids()
    if not user_ids:
        return 0

    due_records: list[NotaRecorrente] = []
    for registro in ativos:
        due_date = _get_month_day(today.year, today.month, registro.dia_emissao)
        if due_date != today:
            continue
        if registro.ultimo_aviso == today:
            continue

        # Auto-reset completion status if we've moved to a new emission day
        if registro.concluida and registro.data_conclusao:
            proxima_emissao = _next_emission_date(registro, registro.data_conclusao)
            if today >= proxima_emissao:
                registro.concluida = False
                registro.data_conclusao = None

        due_records.append(registro)

    if not due_records:
        return 0

    now = utc3_now()
    created_notifications: list[tuple[int, TaskNotification]] = []
    touched_users: set[int] = set()

    for registro in due_records:
        descricao = (registro.descricao or "").strip()
        periodo = registro.periodo_formatado
        base_message = f"Emitir NF {registro.empresa}"
        if descricao:
            base_message += f" - {descricao}"
        message = f"{base_message} (período {periodo})." if periodo else base_message
        truncated = message[:255]

        for user_id in user_ids:
            notification = TaskNotification(
                user_id=user_id,
                task_id=None,
                announcement_id=None,
                type=NotificationType.RECURRING_INVOICE.value,
                message=truncated,
                created_at=now,
            )
            db.session.add(notification)
            created_notifications.append((user_id, notification))
            touched_users.add(user_id)
        registro.ultimo_aviso = today

    if not created_notifications:
        return 0

    db.session.commit()

    # Invalida cache de notificações
    from app.controllers.routes.blueprints.notifications import _invalidate_notification_cache
    for user_id in touched_users:
        _invalidate_notification_cache(user_id)

    # Broadcast em tempo real
    try:
        from app.services.realtime import get_broadcaster

        broadcaster = get_broadcaster()
        for user_id, notification in created_notifications:
            broadcaster.broadcast(
                event_type="notification:created",
                data={
                    "id": notification.id,
                    "type": notification.type,
                    "message": notification.message,
                    "created_at": notification.created_at.isoformat()
                    if notification.created_at
                    else None,
                },
                user_id=user_id,
                scope="notifications",
            )
    except Exception:
        pass

    return len(created_notifications)


# =============================================================================
# ROTAS
# =============================================================================

@notas_bp.route("/controle-notas/debito", methods=["GET", "POST"])
@login_required
@meeting_only_access_check
def notas_debito():
    """
    Entrypoint legado - redireciona para a view de cadastro.

    Mantido para compatibilidade com links antigos.
    """
    if not can_access_controle_notas():
        abort(403)

    flash("O controle de notas foi incorporado à tela de Cadastros.", "info")
    return redirect(url_for("notas.cadastro_notas"))


@notas_bp.route("/controle-notas/debito/<int:nota_id>/forma-pagamento", methods=["POST"])
@login_required
@meeting_only_access_check
def notas_debito_update_forma_pagamento(nota_id: int):
    """
    Atualiza a forma de pagamento de uma nota via requisição assíncrona.

    Args:
        nota_id: ID da nota de débito

    Returns:
        JSON com sucesso/erro e dados atualizados
    """
    if not can_access_controle_notas():
        abort(403)

    from app.controllers.routes import user_has_tag
    pode_ver_forma_pagamento = is_user_admin(current_user) or user_has_tag('Gestão') or user_has_tag('Financeiro')
    if not pode_ver_forma_pagamento:
        abort(403)

    payload = request.get_json(silent=True) or {}
    raw_value = payload.get("forma_pagamento", "")
    if not isinstance(raw_value, str):
        raw_value = ""

    new_value = raw_value.strip().upper()
    valid_values = {(choice or "").upper() for choice, _ in PAGAMENTO_CHOICES}
    if new_value not in valid_values:
        return jsonify({"success": False, "message": "Forma de pagamento inválida."}), 400

    nota = NotaDebito.query.get_or_404(nota_id)
    nota.forma_pagamento = new_value
    db.session.commit()

    label_map = {(choice or "").upper(): label for choice, label in PAGAMENTO_CHOICES}
    return jsonify(
        {
            "success": True,
            "forma_pagamento": nota.forma_pagamento,
            "forma_pagamento_label": label_map.get(new_value, nota.forma_pagamento),
        }
    )


@notas_bp.route("/controle-notas/cadastro", methods=["GET", "POST"])
@login_required
@meeting_only_access_check
def cadastro_notas():
    """
    Lista e gerencia Cadastros de Notas com CRUD modal integrado às notas.

    Funcionalidades:
    - CRUD de cadastros (empresas)
    - CRUD de notas de débito
    - Busca por empresa
    - Filtro por período de emissão
    - Agrupamento por empresa
    - Trigger de notificações recorrentes
    """
    if not can_access_controle_notas():
        abort(403)

    _trigger_recorrente_notifications()

    from app.controllers.routes import user_has_tag
    pode_ver_forma_pagamento = (
        is_user_admin(current_user) or user_has_tag('Gestão') or user_has_tag('Financeiro')
    )
    cadastro_form = CadastroNotaForm(prefix="cadastro")
    nota_form = NotaDebitoForm(prefix="nota")
    search_term = (request.args.get("q") or "").strip()
    cadastros_query = CadastroNota.query.filter(CadastroNota.ativo.is_(True))
    if search_term:
        pattern = f"%{search_term}%"
        cadastros_query = cadastros_query.filter(sa.func.upper(CadastroNota.cadastro).ilike(pattern.upper()))
    cadastros = cadastros_query.order_by(CadastroNota.cadastro).all()

    data_inicial_raw = (request.args.get("data_inicial") or "").strip()
    data_final_raw = (request.args.get("data_final") or "").strip()

    def _parse_date(value: str) -> date | None:
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    data_inicial = _parse_date(data_inicial_raw)
    data_final = _parse_date(data_final_raw)

    notas_query = NotaDebito.query
    if data_inicial and data_final:
        if data_inicial > data_final:
            data_inicial, data_final = data_final, data_inicial
        notas_query = notas_query.filter(
            NotaDebito.data_emissao >= data_inicial,
            NotaDebito.data_emissao <= data_final,
        )
    notas_registradas = notas_query.order_by(NotaDebito.data_emissao.desc()).all()

    open_cadastro_modal = request.args.get("open_cadastro_modal") in ("1", "true", "True")
    open_nota_modal = request.args.get("open_nota_modal") in ("1", "true", "True")
    editing_cadastro: CadastroNota | None = None
    editing_nota: NotaDebito | None = None

    notas_por_empresa: dict[str, list[NotaDebito]] = defaultdict(list)
    for nota in notas_registradas:
        empresa_key = (nota.empresa or "").strip().upper() or "SEM EMPRESA"
        notas_por_empresa[empresa_key].append(nota)

    def _format_currency_value(value: Decimal | float | int | None) -> str:
        number = Decimal(value or 0)
        return (
            f"R$ {number:,.2f}"
            .replace(",", "_")
            .replace(".", ",")
            .replace("_", ".")
        )

    cadastros_info: list[dict[str, object]] = []
    cadastro_empresas: set[str] = set()

    for cadastro in cadastros:
        empresa_key = (cadastro.cadastro or "").strip().upper() or "SEM EMPRESA"
        cadastro_empresas.add(empresa_key)
        notas_relacionadas = notas_por_empresa.get(empresa_key, [])
        total_valor = Decimal("0")
        total_itens = 0
        ultima_data = None
        for nota in notas_relacionadas:
            total_valor += Decimal(nota.total or 0)
            total_itens += int(nota.qtde_itens or 0)
            if nota.data_emissao:
                if ultima_data is None or nota.data_emissao > ultima_data:
                    ultima_data = nota.data_emissao
        cadastros_info.append(
            {
                "cadastro": cadastro,
                "empresa_key": empresa_key,
                "notas": notas_relacionadas,
                "total_notas": len(notas_relacionadas),
                "total_itens": total_itens,
                "total_valor": total_valor,
                "total_valor_formatado": _format_currency_value(total_valor),
                "ultima_data": ultima_data,
                "ultima_data_formatada": ultima_data.strftime("%d/%m/%Y") if ultima_data else "-",
            }
        )

    cadastros_info.sort(key=lambda item: item["empresa_key"])

    notas_sem_cadastro: list[dict[str, object]] = []
    for empresa_key, notas_lista in notas_por_empresa.items():
        if empresa_key in cadastro_empresas:
            continue
        total_valor = Decimal("0")
        total_itens = 0
        for nota in notas_lista:
            total_valor += Decimal(nota.total or 0)
            total_itens += int(nota.qtde_itens or 0)
        notas_sem_cadastro.append(
            {
                "empresa": empresa_key,
                "notas": notas_lista,
                "total_notas": len(notas_lista),
                "total_itens": total_itens,
                "total_valor_formatado": _format_currency_value(total_valor),
            }
        )

    notas_sem_cadastro.sort(key=lambda item: item["empresa"])

    if request.method == "GET":
        edit_id_raw = request.args.get("edit_cadastro_id")
        if edit_id_raw:
            try:
                edit_id = int(edit_id_raw)
            except (TypeError, ValueError):
                abort(404)
            editing_cadastro = CadastroNota.query.get_or_404(edit_id)
            cadastro_form = CadastroNotaForm(prefix="cadastro", obj=editing_cadastro)
            open_cadastro_modal = True

        edit_nota_id_raw = request.args.get("edit_nota_id")
        if edit_nota_id_raw:
            try:
                edit_nota_id = int(edit_nota_id_raw)
            except (TypeError, ValueError):
                abort(404)
            editing_nota = NotaDebito.query.get_or_404(edit_nota_id)
            nota_form = NotaDebitoForm(prefix="nota", obj=editing_nota)
            open_nota_modal = True

    if request.method == "POST":
        form_name = request.form.get("form_name")
        if form_name == "cadastro_create":
            open_cadastro_modal = True
            if cadastro_form.validate_on_submit():
                try:
                    valor = float(cadastro_form.valor.data.replace(',', '.'))
                except (ValueError, AttributeError):
                    flash("Valor inválido.", "warning")
                else:
                    cadastro = CadastroNota(
                        pix=None,
                        cadastro=cadastro_form.cadastro.data.strip().upper() if cadastro_form.cadastro.data else '',
                        valor=valor,
                        acordo=cadastro_form.acordo.data.strip().upper() if cadastro_form.acordo.data else None,
                        forma_pagamento='',
                        usuario=cadastro_form.usuario.data.strip() if cadastro_form.usuario.data else None,
                        senha=cadastro_form.senha.data.strip() if cadastro_form.senha.data else None,
                    )
                    db.session.add(cadastro)
                    db.session.commit()
                    flash("Cadastro registrado com sucesso.", "success")
                    return redirect(url_for("notas.cadastro_notas"))
        elif form_name == "cadastro_update":
            open_cadastro_modal = True
            cadastro_id_raw = request.form.get("cadastro_id")
            try:
                cadastro_id = int(cadastro_id_raw)
            except (TypeError, ValueError):
                abort(400)
            editing_cadastro = CadastroNota.query.get_or_404(cadastro_id)
            if cadastro_form.validate_on_submit():
                try:
                    valor = float(cadastro_form.valor.data.replace(',', '.'))
                except (ValueError, AttributeError):
                    flash("Valor inválido.", "warning")
                else:
                    editing_cadastro.pix = None
                    editing_cadastro.cadastro = cadastro_form.cadastro.data.strip().upper() if cadastro_form.cadastro.data else ''
                    editing_cadastro.valor = valor
                    editing_cadastro.acordo = cadastro_form.acordo.data.strip().upper() if cadastro_form.acordo.data else None
                    editing_cadastro.usuario = cadastro_form.usuario.data.strip() if cadastro_form.usuario.data else None
                    editing_cadastro.senha = cadastro_form.senha.data.strip() if cadastro_form.senha.data else None
                    db.session.commit()
                    flash("Cadastro atualizado com sucesso.", "success")
                    return redirect(url_for("notas.cadastro_notas"))
        elif form_name == "cadastro_delete":
            cadastro_id_raw = request.form.get("cadastro_id")
            try:
                cadastro_id = int(cadastro_id_raw)
            except (TypeError, ValueError):
                abort(400)
            cadastro = CadastroNota.query.get_or_404(cadastro_id)
            cadastro.ativo = False
            db.session.commit()
            flash("Cadastro desativado com sucesso.", "success")
            return redirect(url_for("notas.cadastro_notas"))
        elif form_name == "nota_create":
            open_nota_modal = True
            if nota_form.validate_on_submit():
                try:
                    notas_int = int(nota_form.notas.data)
                    qtde_int = int(nota_form.qtde_itens.data)
                except (ValueError, TypeError):
                    flash("Quantidade de notas/itens inválida.", "warning")
                else:
                    valor_un = _parse_decimal_input(nota_form.valor_un.data)
                    total = _parse_decimal_input(nota_form.total.data)
                    nota = NotaDebito(
                        data_emissao=nota_form.data_emissao.data,
                        empresa=nota_form.empresa.data.strip().upper() if nota_form.empresa.data else '',
                        notas=notas_int,
                        qtde_itens=qtde_int,
                        valor_un=valor_un,
                        total=total,
                        acordo=nota_form.acordo.data.strip().upper() if nota_form.acordo.data else None,
                        forma_pagamento=(nota_form.forma_pagamento.data or '').upper() if pode_ver_forma_pagamento else '',
                        observacao=nota_form.observacao.data.strip() if nota_form.observacao.data else None
                    )
                    db.session.add(nota)
                    db.session.commit()
                    flash("Nota registrada com sucesso.", "success")
                    return redirect(url_for("notas.cadastro_notas"))
        elif form_name == "nota_update":
            open_nota_modal = True
            nota_id_raw = request.form.get("nota_id")
            try:
                nota_id = int(nota_id_raw)
            except (TypeError, ValueError):
                abort(400)
            editing_nota = NotaDebito.query.get_or_404(nota_id)
            if nota_form.validate_on_submit():
                try:
                    notas_int = int(nota_form.notas.data)
                    qtde_int = int(nota_form.qtde_itens.data)
                except (ValueError, TypeError):
                    flash("Quantidade de notas/itens inválida.", "warning")
                else:
                    valor_un = _parse_decimal_input(nota_form.valor_un.data)
                    total = _parse_decimal_input(nota_form.total.data)
                    editing_nota.data_emissao = nota_form.data_emissao.data
                    editing_nota.empresa = nota_form.empresa.data.strip().upper() if nota_form.empresa.data else ''
                    editing_nota.notas = notas_int
                    editing_nota.qtde_itens = qtde_int
                    editing_nota.valor_un = valor_un
                    editing_nota.total = total
                    editing_nota.acordo = nota_form.acordo.data.strip().upper() if nota_form.acordo.data else None
                    if pode_ver_forma_pagamento:
                        editing_nota.forma_pagamento = (nota_form.forma_pagamento.data or '').upper()
                    editing_nota.observacao = nota_form.observacao.data.strip() if nota_form.observacao.data else None
                    db.session.commit()
                    flash("Nota atualizada com sucesso.", "success")
                    return redirect(url_for("notas.cadastro_notas"))
        elif form_name == "nota_delete":
            nota_id_raw = request.form.get("nota_id")
            try:
                nota_id = int(nota_id_raw)
            except (TypeError, ValueError):
                abort(400)
            nota = NotaDebito.query.get_or_404(nota_id)
            db.session.delete(nota)
            db.session.commit()
            flash("Nota excluída com sucesso.", "success")
            return redirect(url_for("notas.cadastro_notas"))

    pode_acessar_totalizador = can_access_notas_totalizador()

    return render_template(
        "cadastro_notas.html",
        cadastros=cadastros,
        cadastros_info=cadastros_info,
        notas_sem_cadastro=notas_sem_cadastro,
        cadastro_form=cadastro_form,
        nota_form=nota_form,
        open_cadastro_modal=open_cadastro_modal,
        open_nota_modal=open_nota_modal,
        editing_cadastro=editing_cadastro,
        editing_nota=editing_nota,
        pode_ver_forma_pagamento=pode_ver_forma_pagamento,
        pode_acessar_totalizador=pode_acessar_totalizador,
        data_inicial=data_inicial,
        data_final=data_final,
        search_term=search_term,
    )


@notas_bp.route("/controle-notas/recorrentes", methods=["GET", "POST"])
@login_required
@meeting_only_access_check
def notas_recorrentes():
    """
    Gerencia notas fiscais recorrentes que disparam notificações mensais.

    Funcionalidades:
    - CRUD de notas recorrentes
    - Cálculo automático da próxima data de emissão
    - Toggle de ativação/desativação
    - Trigger de notificações ao carregar
    """
    if not can_access_controle_notas():
        abort(403)

    _trigger_recorrente_notifications()

    recorrente_form = NotaRecorrenteForm(prefix="recorrente")
    open_recorrente_modal = request.args.get("open_recorrente_modal") in ("1", "true", "True")
    editing_recorrente: NotaRecorrente | None = None

    if request.method == "GET":
        edit_id_raw = request.args.get("edit_recorrente_id")
        if edit_id_raw:
            try:
                edit_id = int(edit_id_raw)
            except (TypeError, ValueError):
                abort(404)
            editing_recorrente = NotaRecorrente.query.get_or_404(edit_id)
            recorrente_form = NotaRecorrenteForm(prefix="recorrente", obj=editing_recorrente)
            recorrente_form.valor.data = _format_decimal_input(editing_recorrente.valor)
            open_recorrente_modal = True

    if request.method == "POST":
        form_name = request.form.get("form_name")
        if form_name in {"recorrente_create", "recorrente_update"}:
            open_recorrente_modal = True
            target: NotaRecorrente | None = None
            if form_name == "recorrente_update":
                recorrente_id_raw = request.form.get("recorrente_id")
                try:
                    recorrente_id = int(recorrente_id_raw)
                except (TypeError, ValueError):
                    abort(400)
                target = NotaRecorrente.query.get_or_404(recorrente_id)
                editing_recorrente = target

            if recorrente_form.validate_on_submit():
                valor_decimal = _parse_decimal_input(recorrente_form.valor.data)
                enterprise = (recorrente_form.empresa.data or "").strip().upper()
                descricao = (recorrente_form.descricao.data or "").strip()
                observacao = (recorrente_form.observacao.data or "").strip()
                is_active = bool(recorrente_form.ativo.data)
                periodo_inicio = recorrente_form.periodo_inicio.data or 1
                periodo_fim = recorrente_form.periodo_fim.data or 1
                dia_emissao = recorrente_form.dia_emissao.data or 1

                if target is None:
                    target = NotaRecorrente()
                    db.session.add(target)

                target.empresa = enterprise
                target.descricao = descricao or None
                target.valor = valor_decimal
                target.observacao = observacao or None
                target.ativo = is_active
                target.periodo_inicio = periodo_inicio
                target.periodo_fim = periodo_fim
                target.dia_emissao = dia_emissao

                db.session.commit()
                flash("Nota recorrente salva com sucesso.", "success")
                return redirect(url_for("notas.notas_recorrentes"))
        elif form_name == "recorrente_delete":
            recorrente_id_raw = request.form.get("recorrente_id")
            try:
                recorrente_id = int(recorrente_id_raw)
            except (TypeError, ValueError):
                abort(400)
            registro = NotaRecorrente.query.get_or_404(recorrente_id)
            db.session.delete(registro)
            db.session.commit()
            flash("Nota recorrente removida.", "success")
            return redirect(url_for("notas.notas_recorrentes"))
        elif form_name == "recorrente_toggle":
            recorrente_id_raw = request.form.get("recorrente_id")
            try:
                recorrente_id = int(recorrente_id_raw)
            except (TypeError, ValueError):
                abort(400)
            registro = NotaRecorrente.query.get_or_404(recorrente_id)
            registro.ativo = not bool(registro.ativo)
            db.session.commit()
            status_label = "ativada" if registro.ativo else "pausada"
            flash(f"Nota recorrente {status_label}.", "success")
            return redirect(url_for("notas.notas_recorrentes"))
        elif form_name == "recorrente_complete":
            recorrente_id_raw = request.form.get("recorrente_id")
            try:
                recorrente_id = int(recorrente_id_raw)
            except (TypeError, ValueError):
                abort(400)
            registro = NotaRecorrente.query.get_or_404(recorrente_id)

            # Calculate the emission date for this period
            proxima = _next_emission_date(registro, date.today())

            # Toggle completion status
            registro.concluida = not bool(registro.concluida)
            if registro.concluida:
                registro.data_conclusao = proxima
            else:
                registro.data_conclusao = None

            db.session.commit()
            status_label = "concluída" if registro.concluida else "pendente"
            flash(f"Nota recorrente marcada como {status_label}.", "success")
            return redirect(url_for("notas.notas_recorrentes"))

    hoje = date.today()
    recorrentes = (
        NotaRecorrente.query.order_by(
            NotaRecorrente.ativo.desc(),
            sa.func.upper(NotaRecorrente.empresa),
            NotaRecorrente.dia_emissao,
        ).all()
    )
    recorrentes_info = []
    for registro in recorrentes:
        proxima = _next_emission_date(registro, hoje)
        recorrentes_info.append(
            {
                "registro": registro,
                "proxima_data": proxima,
                "dias_restantes": (proxima - hoje).days,
                "emissao_hoje": proxima == hoje,
                "valor_input": _format_decimal_input(registro.valor),
                "concluida": registro.concluida,
            }
        )

    pode_acessar_totalizador = can_access_notas_totalizador()
    cadastros = (
        CadastroNota.query.filter(CadastroNota.ativo.is_(True))
        .order_by(CadastroNota.cadastro)
        .all()
    )

    return render_template(
        "notas_recorrentes.html",
        recorrentes_info=recorrentes_info,
        recorrente_form=recorrente_form,
        open_recorrente_modal=open_recorrente_modal,
        editing_recorrente=editing_recorrente,
        editing_recorrente_valor=_format_decimal_input(editing_recorrente.valor) if editing_recorrente else "",
        pode_acessar_totalizador=pode_acessar_totalizador,
        cadastros=cadastros,
    )


@notas_bp.route("/controle-notas/totalizador", methods=["GET"])
@login_required
@meeting_only_access_check
def notas_totalizador():
    """
    Exibe dados agregados de Notas de Débito com filtros de período opcionais.

    Funcionalidades:
    - Filtro por período de emissão
    - Agrupamento por empresa, acordo e forma de pagamento
    - Resumo geral com totais
    - Formatação de moeda brasileira
    - Trigger de notificações ao carregar
    """
    if not can_access_controle_notas():
        abort(403)

    if not can_access_notas_totalizador():
        abort(403)

    _trigger_recorrente_notifications()

    today = date.today()
    default_start = today.replace(day=1)
    default_end = today

    data_inicial_raw = (request.args.get("data_inicial") or "").strip()
    data_final_raw = (request.args.get("data_final") or "").strip()

    data_inicial = _parse_date_str(data_inicial_raw) or default_start
    data_final = _parse_date_str(data_final_raw) or default_end

    if data_inicial > data_final:
        flash("A data inicial não pode ser maior que a data final.", "warning")
        data_inicial, data_final = default_start, default_end

    base_query = NotaDebito.query.filter(
        NotaDebito.data_emissao >= data_inicial,
        NotaDebito.data_emissao <= data_final,
    )

    nota_form = NotaDebitoForm()
    pagamento_choices = nota_form.forma_pagamento.choices
    pagamento_label_map = {
        (choice or "").upper(): label for choice, label in pagamento_choices
    }
    pagamento_value_map = {
        (choice or "").upper(): (choice or "") for choice, _ in pagamento_choices
    }

    def format_currency(value: Decimal | None) -> str:
        number = value if isinstance(value, Decimal) else Decimal(value or 0)
        return (
            f"R$ {number:,.2f}"
            .replace(",", "_")
            .replace(".", ",")
            .replace("_", ".")
        )

    dados_totalizador: list[dict[str, object]] = []
    dados_totalizador_acordo: list[dict[str, object]] = []
    dados_totalizador_pagamento: list[dict[str, object]] = []
    notas_por_empresa: dict[str, dict[str, object]] = {}
    notas_por_acordo: dict[str, dict[str, object]] = {}
    notas_por_pagamento: dict[str, dict[str, object]] = {}

    notas_list = (
        base_query.order_by(
            sa.func.lower(NotaDebito.empresa),
            NotaDebito.data_emissao.desc(),
            NotaDebito.id.desc(),
        ).all()
    )

    total_registros = 0
    total_notas = 0
    total_itens = 0
    total_valor = Decimal("0")

    def _get_or_create_group(
        storage: dict[str, dict[str, object]],
        key: str,
        titulo: str,
        extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        grupo = storage.get(key)
        if not grupo:
            grupo = {
                "titulo": titulo,
                "qtd_registros": 0,
                "total_notas": 0,
                "total_itens": 0,
                "valor_total": Decimal("0"),
                "notas": [],
            }
            if extra:
                grupo.update(extra)
            storage[key] = grupo
        return grupo

    for nota in notas_list:
        empresa_key = (nota.empresa or "").strip().upper()
        if not empresa_key:
            empresa_key = "SEM EMPRESA"

        grupo_empresa = _get_or_create_group(
            notas_por_empresa,
            empresa_key,
            empresa_key,
            {"empresa": empresa_key},
        )

        valor_total = nota.total or Decimal("0")
        valor_un = nota.valor_un or Decimal("0")

        forma_pagamento_raw = (nota.forma_pagamento or "").strip()
        forma_pagamento_value = forma_pagamento_raw.upper()
        acordo_key = (nota.acordo or "").strip().upper() or "SEM ACORDO"
        pagamento_key = forma_pagamento_value or "SEM PAGAMENTO"
        pagamento_label = pagamento_label_map.get(
            pagamento_key, pagamento_key
        ) or "SEM PAGAMENTO"

        grupo_acordo = _get_or_create_group(
            notas_por_acordo,
            acordo_key,
            acordo_key,
            {"acordo": acordo_key},
        )
        grupo_pagamento = _get_or_create_group(
            notas_por_pagamento,
            pagamento_key,
            pagamento_label,
            {
                "forma_pagamento": pagamento_key,
                "forma_pagamento_label": pagamento_label,
            },
        )

        registro_nota = {
            "id": nota.id,
            "data_emissao": nota.data_emissao,
            "data_emissao_formatada": nota.data_emissao_formatada,
            "empresa": empresa_key,
            "notas": nota.notas,
            "qtde_itens": nota.qtde_itens,
            "valor_un": valor_un,
            "valor_un_formatado": nota.valor_un_formatado,
            "valor_total": valor_total,
            "valor_total_formatado": nota.total_formatado,
            "acordo": (nota.acordo or "").upper() if nota.acordo else "#N/A",
            "forma_pagamento": forma_pagamento_raw,
            "forma_pagamento_upper": forma_pagamento_value,
            "forma_pagamento_choice_value": pagamento_value_map.get(
                forma_pagamento_value, forma_pagamento_raw
            ),
            "forma_pagamento_label": pagamento_label_map.get(
                forma_pagamento_value, forma_pagamento_value
            ),
            "observacao": nota.observacao or "",
        }

        for grupo_destino in (grupo_empresa, grupo_acordo, grupo_pagamento):
            grupo_destino["qtd_registros"] += 1
            grupo_destino["total_notas"] += int(nota.notas or 0)
            grupo_destino["total_itens"] += int(nota.qtde_itens or 0)
            grupo_destino["valor_total"] += Decimal(valor_total)
            grupo_destino["notas"].append(registro_nota)

        total_registros += 1
        total_notas += int(nota.notas or 0)
        total_itens += int(nota.qtde_itens or 0)
        total_valor += Decimal(valor_total)

    def _sort_key_empresa(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value or "")
        return "".join(ch for ch in normalized if not unicodedata.combining(ch)).casefold()

    def _finalizar_grupos(storage: dict[str, dict[str, object]]) -> list[dict[str, object]]:
        grupos_finalizados: list[dict[str, object]] = []
        for chave in sorted(storage.keys(), key=_sort_key_empresa):
            grupo = storage[chave]
            grupo["valor_total_formatado"] = format_currency(grupo["valor_total"])
            grupos_finalizados.append(grupo)
        return grupos_finalizados

    dados_totalizador = _finalizar_grupos(notas_por_empresa)
    dados_totalizador_acordo = _finalizar_grupos(notas_por_acordo)
    dados_totalizador_pagamento = _finalizar_grupos(notas_por_pagamento)

    tipos_empresa = [grupo["titulo"] for grupo in dados_totalizador]
    tipos_acordo = [grupo["titulo"] for grupo in dados_totalizador_acordo]
    tipos_pagamento: list[dict[str, str]] = [
        {
            "value": grupo.get("forma_pagamento", ""),
            "label": grupo.get("titulo")
            or grupo.get("forma_pagamento_label")
            or grupo.get("forma_pagamento", ""),
        }
        for grupo in dados_totalizador_pagamento
    ]

    resumo_geral = {
        "qtd_registros": total_registros,
        "total_notas": total_notas,
        "total_itens": total_itens,
        "valor_total": total_valor,
        "valor_total_formatado": format_currency(total_valor),
    }

    from app.controllers.routes import user_has_tag
    pode_ver_forma_pagamento = (
        is_user_admin(current_user)
        or user_has_tag("Gestão")
        or user_has_tag("Financeiro")
    )

    return render_template(
        "notas_totalizador.html",
        dados_totalizador=dados_totalizador,
        resumo_geral=resumo_geral,
        data_inicial=data_inicial.isoformat(),
        data_final=data_final.isoformat(),
        pagamento_choices=pagamento_choices,
        pode_ver_forma_pagamento=pode_ver_forma_pagamento,
        dados_totalizador_acordo=dados_totalizador_acordo,
        dados_totalizador_pagamento=dados_totalizador_pagamento,
        tipos_empresa=tipos_empresa,
        tipos_acordo=tipos_acordo,
        tipos_pagamento=tipos_pagamento,
    )


@notas_bp.route("/controle-notas/totalizador/export", methods=["GET"])
@login_required
@meeting_only_access_check
def notas_totalizador_export():
    """
    Exporta notas filtradas por periodo para Excel ou PDF, sem observacao.
    """
    if not can_access_controle_notas():
        abort(403)

    if not can_access_notas_totalizador():
        abort(403)

    today = date.today()
    default_start = today.replace(day=1)
    default_end = today

    data_inicial_raw = (request.args.get("data_inicial") or "").strip()
    data_final_raw = (request.args.get("data_final") or "").strip()
    formato = (request.args.get("formato") or "xlsx").lower()

    data_inicial = _parse_date_str(data_inicial_raw) or default_start
    data_final = _parse_date_str(data_final_raw) or default_end
    if data_inicial > data_final:
        data_inicial, data_final = data_final, data_inicial

    base_query = NotaDebito.query.filter(
        NotaDebito.data_emissao >= data_inicial,
        NotaDebito.data_emissao <= data_final,
    ).order_by(
        NotaDebito.data_emissao.asc(),
        sa.func.lower(NotaDebito.empresa),
        NotaDebito.id.asc(),
    )
    notas_list = base_query.all()

    pagamento_label_map = {(choice or "").upper(): label for choice, label in PAGAMENTO_CHOICES}

    from app.controllers.routes import user_has_tag
    pode_ver_forma_pagamento = (
        is_user_admin(current_user)
        or user_has_tag("Gestão")
        or user_has_tag("Financeiro")
    )

    registros: list[dict[str, object]] = []
    for nota in notas_list:
        forma_pagamento_raw = (nota.forma_pagamento or "").strip()
        forma_pagamento_label = pagamento_label_map.get(
            forma_pagamento_raw.upper(), forma_pagamento_raw
        )
        registros.append(
            {
                "Data Emissao": nota.data_emissao.strftime("%d/%m/%Y") if nota.data_emissao else "",
                "Empresa": (nota.empresa or "").upper(),
                "Notas": nota.notas,
                "Itens": nota.qtde_itens,
                "Valor UN": float(nota.valor_un or 0),
                "Valor Total": float(nota.total or 0),
                "Acordo": (nota.acordo or "").upper() if nota.acordo else "",
                "Pagamento": forma_pagamento_label if pode_ver_forma_pagamento else "",
            }
        )

    if formato == "pdf":
        class NotasPDF(FPDF):
            pass

        pdf = NotasPDF(orientation="L")
        pdf.set_auto_page_break(auto=True, margin=12)
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 10, "Notas do período", ln=True, align="L")
        pdf.set_font("Helvetica", size=9)
        pdf.cell(0, 8, f"Período: {data_inicial.strftime('%d/%m/%Y')} a {data_final.strftime('%d/%m/%Y')}", ln=True)

        colunas = [
            {"key": "Data Emissao", "label": "Data Emissão", "width": 24, "align": "C"},
            {"key": "Empresa", "label": "Empresa", "width": 70, "align": "L"},
            {"key": "Notas", "label": "Notas", "width": 16, "align": "C"},
            {"key": "Itens", "label": "Itens", "width": 16, "align": "C"},
            {"key": "Valor UN", "label": "Valor UN", "width": 24, "align": "R"},
            {"key": "Valor Total", "label": "Valor Total", "width": 26, "align": "R"},
            {"key": "Acordo", "label": "Acordo", "width": 42, "align": "L"},
        ]
        if pode_ver_forma_pagamento:
            colunas.append({"key": "Pagamento", "label": "Pagamento", "width": 32, "align": "L"})

        line_height = 6.0

        def draw_header() -> None:
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_fill_color(240, 240, 240)
            for col in colunas:
                pdf.cell(col["width"], line_height + 1, col["label"], border=1, align="C", fill=True)
            pdf.ln()
            pdf.set_font("Helvetica", size=8)
            pdf.set_fill_color(255, 255, 255)

        draw_header()

        for registro in registros:
            col_lines: list[tuple[dict, list[str]]] = []
            max_lines = 1
            for col in colunas:
                valor = registro.get(col["key"], "")
                if col["key"] in {"Valor UN", "Valor Total"}:
                    valor = _format_currency_br(valor)
                lines = _wrap_text(str(valor), pdf, col["width"] - 2)
                max_lines = max(max_lines, len(lines))
                col_lines.append((col, lines))

            row_height = max_lines * line_height
            if pdf.will_page_break(row_height):
                pdf.add_page()
                pdf.set_font("Helvetica", "B", 12)
                pdf.cell(0, 10, "Notas do período", ln=True, align="L")
                pdf.set_font("Helvetica", size=9)
                pdf.cell(0, 8, f"Período: {data_inicial.strftime('%d/%m/%Y')} a {data_final.strftime('%d/%m/%Y')}", ln=True)
                draw_header()

            y_start = pdf.get_y()
            for col, lines in col_lines:
                x_start = pdf.get_x()
                text_block = "\n".join(lines + [""] * (max_lines - len(lines)))
                align = col.get("align", "L")
                pdf.multi_cell(col["width"], line_height, text_block, border=1, align=align)
                pdf.set_xy(x_start + col["width"], y_start)
            pdf.set_y(y_start + row_height)

        pdf_raw = pdf.output(dest="S")
        pdf_bytes = pdf_raw.encode("latin-1") if isinstance(pdf_raw, str) else pdf_raw
        buffer = BytesIO(pdf_bytes)
        filename = f"notas_{data_inicial.isoformat()}_{data_final.isoformat()}.pdf"
        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype="application/pdf",
        )

    # Default Excel export
    df = pd.DataFrame(registros)
    if not pode_ver_forma_pagamento and "Pagamento" in df.columns:
        df = df.drop(columns=["Pagamento"])
    buffer = BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)
    filename = f"notas_{data_inicial.isoformat()}_{data_final.isoformat()}.xlsx"
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
