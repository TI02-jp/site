"""
Blueprint para relatorios administrativos.

Este modulo contem rotas para geracao de relatorios
com controle de acesso por permissoes.

Rotas:
    - GET /relatorios: Index de relatorios
    - GET /relatorio_empresas: Relatorio de empresas
    - GET /relatorio_fiscal: Relatorio fiscal
    - GET /relatorio_contabil: Relatorio contabil
    - GET /relatorio_usuarios: Relatorio de usuarios
    - GET /relatorio_cursos: Relatorio de cursos
    - GET /relatorio_tarefas: Relatorio de tarefas
    - GET/POST /relatorios/permissoes: Gestao de permissoes

Dependencias:
    - models: ReportPermission, Empresa, User, Course, Task
    - decorators: report_access_required

Autor: Refatoracao automatizada
Data: 2024-12
"""

import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

import sqlalchemy as sa
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.forms import DepartamentoContabilForm, DepartamentoFiscalForm
from app.models.tables import (
    Departamento,
    Empresa,
    ReportPermission,
    SAO_PAULO_TZ,
    Tag,
    Task,
    TaskPriority,
    TaskStatus,
    TaskStatusHistory,
    User,
)
from app.services.courses import CourseStatus, get_courses_overview
from app.controllers.routes._base import encode_id


# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

relatorios_bp = Blueprint('relatorios', __name__)


# =============================================================================
# CONSTANTES
# =============================================================================

REPORT_DEFINITIONS: dict[str, dict[str, str]] = {
    "empresas": {"title": "Relatório de Empresas", "description": "Dados consolidados das empresas"},
    "fiscal": {"title": "Relatório Fiscal", "description": "Indicadores e obrigações fiscais"},
    "contabil": {"title": "Relatório Contábil", "description": "Visão contábil e controle de relatórios"},
    "usuarios": {"title": "Relatório de Usuários", "description": "Gestão e estatísticas de usuários"},
    "cursos": {"title": "Relatório de Cursos", "description": "Métricas do catálogo de treinamentos"},
    "tarefas": {"title": "Relatório de Tarefas", "description": "Painel de tarefas e indicadores"},
}

PORTAL_PERMISSION_DEFINITIONS: dict[str, dict[str, str]] = {
    "announcements_manage": {
        "title": "Mural - Criar e Gerenciar Comunicados",
        "description": "Criar, editar e excluir comunicados no mural da empresa",
    },
    "procedures_manage": {
        "title": "Procedimentos Operacionais - Gerenciar",
        "description": "Criar, editar e excluir procedimentos operacionais",
    },
}

ALL_PERMISSION_DEFINITIONS: dict[str, dict[str, str]] = {
    **REPORT_DEFINITIONS,
    **PORTAL_PERMISSION_DEFINITIONS,
}

EXCLUDED_TASK_TAGS = ["Reunião"]


# =============================================================================
# FUNÇÕES AUXILIARES
# =============================================================================

def utc3_now() -> datetime:
    """Return current datetime in São Paulo timezone."""
    return datetime.now(SAO_PAULO_TZ).replace(tzinfo=None)


def _require_master_admin() -> None:
    """Abort with 403 if current user is not admin or master."""
    from flask import abort
    if current_user.role != "admin" and not getattr(current_user, "is_master", False):
        abort(403)


# =============================================================================
# ROTAS
# =============================================================================

# Note: Decoradores @report_access_required são aplicados no registro do blueprint
# para evitar import circular. Veja register_blueprints() em __init__.py

@relatorios_bp.route("/relatorios")
def relatorios():
    """Render the reports landing page."""
    return render_template("admin/relatorios.html")

@relatorios_bp.route("/relatorio_empresas")
def relatorio_empresas():
    """Display aggregated company statistics."""
    empresas = Empresa.query.with_entities(
        Empresa.id,
        Empresa.nome_empresa,
        Empresa.cnpj,
        Empresa.codigo_empresa,
        Empresa.tributacao,
        Empresa.sistema_utilizado,
    ).all()

    categorias = ["Simples Nacional", "Lucro Presumido", "Lucro Real"]
    grouped = {cat: [] for cat in categorias}
    grouped_sistemas = {}

    for eid, nome, cnpj, codigo, trib, sistema in empresas:
        label = trib if trib in categorias else "Outros"
        grouped.setdefault(label, []).append(
            {
                "id": eid,
                "token": encode_id(eid, namespace="empresa"),
                "nome": nome,
                "cnpj": cnpj,
                "codigo": codigo,
            }
        )

        sistema_label = sistema.strip() if sistema else "Não informado"
        grouped_sistemas.setdefault(sistema_label, []).append(
            {"nome": nome, "cnpj": cnpj, "codigo": codigo}
        )

    for empresas_list in grouped.values():
        empresas_list.sort(key=lambda item: (item.get("codigo") or "").strip())

    labels = list(grouped.keys())
    counts = [len(grouped[label]) for label in labels]
    tributacao_chart = {
        "type": "bar",
        "title": "Empresas por regime de tributação",
        "datasetLabel": "Quantidade de empresas",
        "labels": labels,
        "values": counts,
        "xTitle": "Regime",
        "yTitle": "Quantidade",
        "total": sum(counts),
    }

    sistema_labels = list(grouped_sistemas.keys())
    sistema_counts = [len(grouped_sistemas[label]) for label in sistema_labels]
    sistema_chart = {
        "type": "bar",
        "title": "Empresas por sistema utilizado",
        "datasetLabel": "Quantidade de empresas",
        "labels": sistema_labels,
        "values": sistema_counts,
        "xTitle": "Sistema",
        "yTitle": "Quantidade",
        "total": sum(sistema_counts),
    }

    return render_template(
        "admin/relatorio_empresas.html",
        tributacao_chart=tributacao_chart,
        sistema_chart=sistema_chart,
        tributacao_companies=grouped,
    )

@relatorios_bp.route("/relatorio_fiscal")
def relatorio_fiscal():
    """Show summary charts for the fiscal department."""
    departamentos = (
        Departamento.query.filter_by(tipo="Departamento Fiscal")
        .join(Empresa)
        .with_entities(
            Empresa.nome_empresa,
            Empresa.codigo_empresa,
            Departamento.formas_importacao,
            Departamento.forma_movimento,
            Departamento.malote_coleta,
        )
        .all()
    )
    fiscal_form = DepartamentoFiscalForm()
    choice_map = dict(fiscal_form.formas_importacao.choices)
    import_grouped = {}
    envio_grouped = {}
    malote_grouped = {}
    for nome, codigo, formas, envio, malote in departamentos:
        formas_list = json.loads(formas) if isinstance(formas, str) else (formas or [])
        for f in formas_list:
            label = choice_map.get(f, f)
            import_grouped.setdefault(label, []).append(
                {"nome": nome, "codigo": codigo}
            )
        label_envio = envio if envio else "Não informado"
        envio_grouped.setdefault(label_envio, []).append(
            {"nome": nome, "codigo": codigo}
        )
        if envio in ("Fisico", "Digital e Físico"):
            label_malote = malote if malote else "Não informado"
            malote_grouped.setdefault(label_malote, []).append(
                {"nome": nome, "codigo": codigo}
            )
    labels_imp = list(import_grouped.keys())
    counts_imp = [len(import_grouped[label]) for label in labels_imp]
    importacao_chart = {
        "type": "bar",
        "title": "Formas de Importação (Fiscal)",
        "datasetLabel": "Quantidade de empresas",
        "labels": labels_imp,
        "values": counts_imp,
        "xTitle": "Forma",
        "yTitle": "Quantidade",
        "total": sum(counts_imp),
    }

    labels_env = list(envio_grouped.keys())
    counts_env = [len(envio_grouped[label]) for label in labels_env]
    envio_chart = {
        "type": "doughnut",
        "title": "Envio de Documentos (Fiscal)",
        "datasetLabel": "Distribuição",
        "labels": labels_env,
        "values": counts_env,
        "total": sum(counts_env),
    }

    labels_mal = list(malote_grouped.keys())
    counts_mal = [len(malote_grouped[label]) for label in labels_mal]
    malote_chart = {
        "type": "bar",
        "title": "Coleta de Malote (Envio Físico)",
        "datasetLabel": "Quantidade de empresas",
        "labels": labels_mal,
        "values": counts_mal,
        "xTitle": "Coleta",
        "yTitle": "Quantidade",
        "total": sum(counts_mal),
    }

    return render_template(
        "admin/relatorio_fiscal.html",
        importacao_chart=importacao_chart,
        envio_chart=envio_chart,
        malote_chart=malote_chart,
    )

@relatorios_bp.route("/relatorio_contabil")
def relatorio_contabil():
    """Show summary charts for the accounting department."""
    departamentos = (
        Departamento.query.filter_by(tipo="Departamento Contábil")
        .join(Empresa)
        .with_entities(
            Empresa.nome_empresa,
            Empresa.codigo_empresa,
            Departamento.metodo_importacao,
            Departamento.forma_movimento,
            Departamento.malote_coleta,
            Departamento.controle_relatorios,
        )
        .all()
    )
    contabil_form = DepartamentoContabilForm()
    metodo_map = dict(contabil_form.metodo_importacao.choices)
    relatorio_map = dict(contabil_form.controle_relatorios.choices)
    import_grouped = {}
    envio_grouped = {}
    malote_grouped = {}
    relatorios_grouped = {}
    for nome, codigo, metodo, envio, malote, relatorios in departamentos:
        metodo_list = json.loads(metodo) if isinstance(metodo, str) else (metodo or [])
        for m in metodo_list:
            label = metodo_map.get(m, m)
            import_grouped.setdefault(label, []).append(
                {"nome": nome, "codigo": codigo}
            )
        label_envio = envio if envio else "Não informado"
        envio_grouped.setdefault(label_envio, []).append(
            {"nome": nome, "codigo": codigo}
        )
        if envio in ("Fisico", "Digital e Físico"):
            label_malote = malote if malote else "Não informado"
            malote_grouped.setdefault(label_malote, []).append(
                {"nome": nome, "codigo": codigo}
            )
        rel_list = (
            json.loads(relatorios)
            if isinstance(relatorios, str)
            else (relatorios or [])
        )
        for r in rel_list:
            label = relatorio_map.get(r, r)
            relatorios_grouped.setdefault(label, []).append(
                {"nome": nome, "codigo": codigo}
            )
    labels_imp = list(import_grouped.keys())
    counts_imp = [len(import_grouped[label]) for label in labels_imp]
    importacao_chart = {
        "type": "bar",
        "title": "Métodos de Importação (Contábil)",
        "datasetLabel": "Quantidade de empresas",
        "labels": labels_imp,
        "values": counts_imp,
        "xTitle": "Método",
        "yTitle": "Quantidade",
        "total": sum(counts_imp),
    }

    labels_env = list(envio_grouped.keys())
    counts_env = [len(envio_grouped[label]) for label in labels_env]
    envio_chart = {
        "type": "doughnut",
        "title": "Envio de Documentos (Contábil)",
        "datasetLabel": "Distribuição",
        "labels": labels_env,
        "values": counts_env,
        "total": sum(counts_env),
    }

    labels_mal = list(malote_grouped.keys())
    counts_mal = [len(malote_grouped[label]) for label in labels_mal]
    malote_chart = {
        "type": "bar",
        "title": "Coleta de Malote (Envio Físico)",
        "datasetLabel": "Quantidade de empresas",
        "labels": labels_mal,
        "values": counts_mal,
        "xTitle": "Coleta",
        "yTitle": "Quantidade",
        "total": sum(counts_mal),
    }

    labels_rel = list(relatorios_grouped.keys())
    counts_rel = [len(relatorios_grouped[label]) for label in labels_rel]
    relatorios_chart = {
        "type": "bar",
        "title": "Controle de Relatórios (Contábil)",
        "datasetLabel": "Quantidade de empresas",
        "labels": labels_rel,
        "values": counts_rel,
        "xTitle": "Relatório",
        "yTitle": "Quantidade",
        "total": sum(counts_rel),
    }

    return render_template(
        "admin/relatorio_contabil.html",
        importacao_chart=importacao_chart,
        envio_chart=envio_chart,
        malote_chart=malote_chart,
        relatorios_chart=relatorios_chart,
    )

@relatorios_bp.route("/relatorio_usuarios")
def relatorio_usuarios():
    """Visualize user counts by role and status."""
    users = User.query.with_entities(
        User.username, User.name, User.email, User.role, User.ativo
    ).all()
    grouped = {}
    labels = []
    counts = []
    for username, name, email, role, ativo in users:
        tipo = "Admin" if role == "admin" else "Usuário"
        status = "Ativo" if ativo else "Inativo"
        label = f"{tipo} {status}"
        grouped.setdefault(label, []).append(
            {"username": username, "name": name, "email": email}
        )
    for label, usuarios in grouped.items():
        labels.append(label)
        counts.append(len(usuarios))
    users_chart = {
        "type": "doughnut",
        "title": "Usuários por tipo e status",
        "datasetLabel": "Distribuição",
        "labels": labels,
        "values": counts,
        "total": sum(counts),
    }

    return render_template(
        "admin/relatorio_usuarios.html",
        users_chart=users_chart,
    )

@relatorios_bp.route("/relatorio_cursos")
def relatorio_cursos():
    """Show aggregated metrics for the internal course catalog."""
    records = get_courses_overview()
    total_courses = len(records)
    today = date.today()

    def _normalize_date(value: date | datetime | None) -> date | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        return value
    status_counts: Counter[CourseStatus] = Counter(record.status for record in records)
    status_labels_map = {
        CourseStatus.PLANNED: "Planejado",
        CourseStatus.DELAYED: "Atrasado",
        CourseStatus.POSTPONED: "Adiamento",
        CourseStatus.CANCELLED: "Cancelado",
        CourseStatus.COMPLETED: "Concluido",
    }
    status_labels = []
    status_values = []
    for status in CourseStatus:
        status_labels.append(status_labels_map[status])
        status_values.append(status_counts.get(status, 0))
    status_chart = {
        "type": "doughnut",
        "title": "Distribuicao por status",
        "datasetLabel": "Cursos",
        "labels": status_labels,
        "values": status_values,
        "total": total_courses,
    }

    def _month_key(anchor: date, offset: int) -> date:
        year = anchor.year
        month = anchor.month - offset
        while month <= 0:
            month += 12
            year -= 1
        return date(year, month, 1)

    months_window = 6
    current_month_start = date(today.year, today.month, 1)
    month_labels: list[str] = []
    month_index: dict[tuple[int, int], int] = {}
    for offset in range(months_window - 1, -1, -1):
        month_date = _month_key(current_month_start, offset)
        label = month_date.strftime("%b/%Y")
        month_labels.append(label)
        month_index[(month_date.year, month_date.month)] = len(month_labels) - 1
    earliest_month = _month_key(current_month_start, months_window - 1)
    planned_counts = [0] * len(month_labels)
    completed_counts = [0] * len(month_labels)
    for record in records:
        start_date = _normalize_date(record.start_date)
        if start_date and start_date >= earliest_month:
            idx = month_index.get((start_date.year, start_date.month))
            if idx is not None and record.status != CourseStatus.COMPLETED:
                planned_counts[idx] += 1
        completion_date = _normalize_date(record.completion_date)
        if completion_date and completion_date >= earliest_month:
            idx = month_index.get((completion_date.year, completion_date.month))
            if idx is not None:
                completed_counts[idx] += 1
    flow_chart = {
        "type": "bar",
        "title": "Cronograma de cursos",
        "labels": month_labels,
        "datasets": [
            {
                "label": "Planejados",
                "values": planned_counts,
                "backgroundColor": "#f97316",
            },
            {
                "label": "Concluidos",
                "values": completed_counts,
                "backgroundColor": "#22c55e",
            },
        ],
        "xTitle": "Mes",
        "yTitle": "Quantidade",
    }

    instructor_counts: Counter[str] = Counter()
    sector_counts: Counter[str] = Counter()
    participant_total = 0
    participant_counts: Counter[str] = Counter()
    workload_hours_sum = 0.0
    workload_count = 0
    for record in records:
        instructor = (record.instructor or "Sem instrutor").strip() or "Sem instrutor"
        instructor_counts[instructor] += 1
        for sector in record.sectors:
            label = sector.strip()
            if label:
                sector_counts[label] += 1
        participant_total += len(record.participants_raw)
        for participant in record.participants_raw:
            label = (participant or "").strip()
            if label:
                participant_counts[label] += 1
        if record.workload:
            workload_hours_sum += record.workload.hour + record.workload.minute / 60
            workload_count += 1
    top_instructors = instructor_counts.most_common(5)
    instructor_chart = {
        "type": "bar",
        "title": "Instrutores com mais cursos",
        "datasetLabel": "Cursos",
        "labels": [name for name, _ in top_instructors],
        "values": [count for _, count in top_instructors],
        "xTitle": "Instrutor",
        "yTitle": "Quantidade",
        "total": sum(count for _, count in top_instructors),
    }
    top_sectors = sector_counts.most_common(6)
    sector_chart = {
        "type": "bar",
        "title": "Setores atendidos",
        "datasetLabel": "Cursos",
        "labels": [name for name, _ in top_sectors],
        "values": [count for _, count in top_sectors],
        "xTitle": "Setor",
        "yTitle": "Participacoes",
        "total": sum(count for _, count in top_sectors),
    }
    top_participants = participant_counts.most_common(10)
    participants_chart = (
        {
            "type": "bar",
            "title": "Participantes mais presentes",
            "datasetLabel": "Participacoes",
            "labels": [name for name, _ in top_participants],
            "values": [count for _, count in top_participants],
            "xTitle": "Participante",
            "yTitle": "Participacoes",
            "total": sum(count for _, count in top_participants),
        }
        if top_participants
        else None
    )

    upcoming_30 = sum(
        1
        for record in records
        if _normalize_date(record.start_date)
        and today <= _normalize_date(record.start_date) <= today + timedelta(days=30)
    )
    completed_90 = sum(
        1
        for record in records
        if record.status == CourseStatus.COMPLETED
        and _normalize_date(record.completion_date)
        and _normalize_date(record.completion_date) >= today - timedelta(days=90)
    )
    avg_workload_hours = (
        workload_hours_sum / workload_count if workload_count else None
    )
    active_courses = sum(
        1
        for record in records
        if record.status in {CourseStatus.PLANNED, CourseStatus.DELAYED, CourseStatus.POSTPONED}
    )

    kpis = [
        {
            "label": "Cursos ativos",
            "value": active_courses,
            "description": "Planejados ou em ajuste",
        },
        {
            "label": "Concluidos (90 dias)",
            "value": completed_90,
            "description": "Encerrados no ultimo trimestre",
        },
        {
            "label": "Previstos (30 dias)",
            "value": upcoming_30,
            "description": "Inicios previstos ate 30 dias",
        },
        {
            "label": "Participantes estimados",
            "value": participant_total,
            "description": "Somatorio dos inscritos",
        },
    ]

    return render_template(
        "admin/relatorio_cursos.html",
        kpis=kpis,
        status_chart=status_chart,
        flow_chart=flow_chart,
        instructor_chart=instructor_chart,
        sector_chart=sector_chart,
        participants_chart=participants_chart,
        avg_workload_hours=avg_workload_hours,
        total_courses=total_courses,
    )


@relatorios_bp.route("/relatorio_tarefas")
def relatorio_tarefas():
    """Expose tactical dashboards about the global task workload."""
    today = date.today()
    upcoming_limit = today + timedelta(days=7)
    now = utc3_now()
    trend_weeks = 6
    overview_query = (
        Task.query.join(Tag)
        .filter(Task.parent_id.is_(None))
        .filter(Task.is_private.is_(False))
        .filter(~Tag.nome.in_(EXCLUDED_TASK_TAGS))
    )
    open_tasks_query = overview_query.filter(Task.status != TaskStatus.DONE)

    total_tasks = overview_query.count()
    open_tasks = open_tasks_query.count()
    completed_last_30 = (
        overview_query.filter(
            Task.status == TaskStatus.DONE,
            Task.completed_at.isnot(None),
            Task.completed_at >= now - timedelta(days=30),
        ).count()
    )
    overdue_tasks = (
        open_tasks_query.filter(
            Task.due_date.isnot(None),
            Task.due_date < today,
        ).count()
    )
    due_soon_tasks = (
        open_tasks_query.filter(
            Task.due_date.isnot(None),
            Task.due_date >= today,
            Task.due_date <= upcoming_limit,
        ).count()
    )
    no_due_date_tasks = open_tasks_query.filter(Task.due_date.is_(None)).count()
    unassigned_tasks = open_tasks_query.filter(Task.assigned_to.is_(None)).count()
    on_track_tasks = max(open_tasks - overdue_tasks - due_soon_tasks - no_due_date_tasks, 0)

    status_rows = (
        overview_query.with_entities(Task.status, db.func.count(Task.id))
        .group_by(Task.status)
        .all()
    )
    status_labels_map = {
        TaskStatus.PENDING: "Pendentes",
        TaskStatus.IN_PROGRESS: "Em andamento",
        TaskStatus.DONE: "Concluidas",
    }
    status_labels = []
    status_values = []
    for status in TaskStatus:
        status_labels.append(status_labels_map[status])
        count = next((qty for st, qty in status_rows if st == status), 0)
        status_values.append(count)
    status_chart = {
        "type": "doughnut",
        "title": "Distribuicao por status",
        "datasetLabel": "Tarefas",
        "labels": status_labels,
        "values": status_values,
        "total": total_tasks,
    }

    priority_rows = (
        overview_query.with_entities(Task.priority, db.func.count(Task.id))
        .filter(Task.status != TaskStatus.DONE)
        .group_by(Task.priority)
        .all()
    )
    priority_labels_map = {
        TaskPriority.LOW: "Baixa",
        TaskPriority.MEDIUM: "Media",
        TaskPriority.HIGH: "Alta",
    }
    priority_labels = []
    priority_values = []
    for priority in TaskPriority:
        priority_labels.append(priority_labels_map[priority])
        count = next((qty for pr, qty in priority_rows if pr == priority), 0)
        priority_values.append(count)
    priority_chart = {
        "type": "bar",
        "title": "Prioridade das tarefas abertas",
        "datasetLabel": "Tarefas abertas",
        "labels": priority_labels,
        "values": priority_values,
        "xTitle": "Prioridade",
        "yTitle": "Quantidade",
        "total": sum(priority_values),
    }

    deadline_chart = {
        "type": "doughnut",
        "title": "Risco de prazo (tarefas abertas)",
        "datasetLabel": "Tarefas",
        "labels": [
            "Atrasadas",
            "Proximos 7 dias",
            "Sem prazo definido",
            "No prazo",
        ],
        "values": [
            overdue_tasks,
            due_soon_tasks,
            no_due_date_tasks,
            on_track_tasks,
        ],
        "total": open_tasks,
    }

    trend_anchor = today - timedelta(days=today.weekday())
    week_windows: list[tuple[date, date]] = []
    for index in range(trend_weeks):
        start = trend_anchor - timedelta(weeks=trend_weeks - 1 - index)
        end = start + timedelta(days=6)
        week_windows.append((start, end))
    first_window_start = week_windows[0][0]
    trend_start_dt = datetime.combine(first_window_start, datetime.min.time())
    completed_recent = (
        overview_query.with_entities(Task.completed_at)
        .filter(
            Task.status == TaskStatus.DONE,
            Task.completed_at.isnot(None),
            Task.completed_at >= trend_start_dt,
        )
        .all()
    )
    created_recent = (
        overview_query.with_entities(Task.created_at)
        .filter(
            Task.created_at.isnot(None),
            Task.created_at >= trend_start_dt,
        )
        .all()
    )
    completion_by_day: dict[date, int] = {}
    for (completed_at,) in completed_recent:
        completion_by_day.setdefault(completed_at.date(), 0)
        completion_by_day[completed_at.date()] += 1
    creation_by_day: dict[date, int] = {}
    for (created_at,) in created_recent:
        creation_by_day.setdefault(created_at.date(), 0)
        creation_by_day[created_at.date()] += 1
    trend_labels: list[str] = []
    completion_counts: list[int] = []
    creation_counts: list[int] = []
    for start, end in week_windows:
        label = f"{start.strftime('%d/%m')} - {end.strftime('%d/%m')}"
        trend_labels.append(label)
        completed_count = 0
        created_count = 0
        span = (end - start).days + 1
        for offset in range(span):
            day = start + timedelta(days=offset)
            completed_count += completion_by_day.get(day, 0)
            created_count += creation_by_day.get(day, 0)
        completion_counts.append(completed_count)
        creation_counts.append(created_count)
    flow_chart = {
        "type": "line",
        "title": "Fluxo semanal: criacoes x conclusoes",
        "labels": trend_labels,
        "datasets": [
            {
                "label": "Criadas",
                "values": creation_counts,
                "borderColor": "#0ea5e9",
                "backgroundColor": "rgba(14,165,233,0.2)",
                "fill": False,
                "tension": 0.35,
            },
            {
                "label": "Concluidas",
                "values": completion_counts,
                "borderColor": "#22c55e",
                "backgroundColor": "rgba(34,197,94,0.2)",
                "fill": False,
                "tension": 0.35,
            },
        ],
        "xTitle": "Semana",
        "yTitle": "Quantidade",
    }

    def _subtract_months(base: date, months: int) -> date:
        year = base.year
        month = base.month - months
        while month <= 0:
            month += 12
            year -= 1
        return date(year, month, 1)

    months_window = 4
    current_month_start = date(today.year, today.month, 1)
    month_labels: list[str] = []
    month_index: dict[tuple[int, int], int] = {}
    for offset in range(months_window - 1, -1, -1):
        month_date = _subtract_months(current_month_start, offset)
        label = month_date.strftime("%b/%Y")
        month_labels.append(label)
        month_index[(month_date.year, month_date.month)] = len(month_labels) - 1
    earliest_month = _subtract_months(current_month_start, months_window - 1)
    area_rows = (
        overview_query.with_entities(Task.created_at, Tag.nome)
        .filter(Task.created_at.isnot(None))
        .filter(Task.created_at >= datetime.combine(earliest_month, datetime.min.time()))
        .all()
    )
    counts_by_area: dict[str, list[int]] = {}
    for created_at, tag_name in area_rows:
        if not created_at:
            continue
        created_date = created_at.date()
        month_key = (created_date.year, created_date.month)
        idx = month_index.get(month_key)
        if idx is None:
            continue
        label = tag_name or "Sem setor"
        counts_by_area.setdefault(label, [0] * len(month_labels))
        counts_by_area[label][idx] += 1
    area_datasets: list[dict[str, object]] = []
    if counts_by_area:
        sorted_areas = sorted(
            counts_by_area.items(), key=lambda item: sum(item[1]), reverse=True
        )
        for area_label, values in sorted_areas[:4]:
            area_datasets.append(
                {
                    "label": area_label,
                    "values": values,
                    "type": "line",
                    "fill": False,
                }
            )
    service_area_chart = (
        {
            "type": "line",
            "title": "Chamados por area de atendimento (ultimos meses)",
            "labels": month_labels,
            "datasets": area_datasets,
            "xTitle": "Mes",
            "yTitle": "Chamados",
        }
        if area_datasets
        else None
    )

    sector_rows = (
        overview_query.with_entities(Tag.nome, db.func.count(Task.id))
        .filter(Task.status != TaskStatus.DONE)
        .group_by(Tag.nome)
        .order_by(db.func.count(Task.id).desc())
        .limit(8)
        .all()
    )
    sector_labels = []
    sector_values = []
    for nome, quantidade in sector_rows:
        sector_labels.append(nome or "Sem setor")
        sector_values.append(quantidade)
    sector_chart = {
        "type": "bar",
        "title": "Setores com mais tarefas abertas",
        "datasetLabel": "Tarefas abertas",
        "labels": sector_labels,
        "values": sector_values,
        "xTitle": "Setor",
        "yTitle": "Quantidade",
        "total": sum(sector_values),
    }

    user_rows = (
        overview_query.join(User, User.id == Task.assigned_to)
        .with_entities(User.id, User.name, User.username, db.func.count(Task.id))
        .filter(Task.status != TaskStatus.DONE)
        .group_by(User.id, User.name, User.username)
        .order_by(db.func.count(Task.id).desc())
        .limit(8)
        .all()
    )
    user_labels = []
    user_values = []
    for user_id, name, username, quantidade in user_rows:
        display_name = (name or username or "").strip()
        if not display_name:
            display_name = f"Usuario {user_id}"
        user_labels.append(display_name)
        user_values.append(quantidade)
    user_chart = {
        "type": "bar",
        "title": "Usuarios com mais tarefas abertas",
        "datasetLabel": "Tarefas abertas",
        "labels": user_labels,
        "values": user_values,
        "xTitle": "Usuario",
        "yTitle": "Quantidade",
        "total": sum(user_values),
    }

    subject_rows = (
        overview_query.with_entities(Task.title, db.func.count(Task.id))
        .group_by(Task.title)
        .order_by(db.func.count(Task.id).desc())
        .limit(10)
        .all()
    )
    subject_labels = []
    subject_values = []
    for title, quantidade in subject_rows:
        label = (title or "Sem assunto").strip() or "Sem assunto"
        subject_labels.append(label)
        subject_values.append(quantidade)

    open_task_dates = open_tasks_query.with_entities(Task.created_at).all()
    age_buckets = [
        ("0-7 dias", 0, 7),
        ("8-14 dias", 8, 14),
        ("15-30 dias", 15, 30),
        ("+30 dias", 31, None),
    ]
    bucket_counts = {label: 0 for label, _, _ in age_buckets}
    total_age_days = 0
    open_age_samples = 0
    for (created_at,) in open_task_dates:
        if not created_at:
            continue
        age_days = (today - created_at.date()).days
        if age_days < 0:
            continue
        total_age_days += age_days
        open_age_samples += 1
        for label, start, end in age_buckets:
            if end is None and age_days >= start:
                bucket_counts[label] += 1
                break
            if end is not None and start <= age_days <= end:
                bucket_counts[label] += 1
                break
    avg_open_age_days = (total_age_days / open_age_samples) if open_age_samples else None
    aging_chart = {
        "type": "bar",
        "title": "Idade das tarefas em aberto",
        "datasetLabel": "Quantidade de tarefas",
        "labels": [label for label, _, _ in age_buckets],
        "values": [bucket_counts[label] for label, _, _ in age_buckets],
        "xTitle": "Faixa de idade",
        "yTitle": "Quantidade",
        "total": sum(bucket_counts.values()),
    } if open_age_samples else None

    completed_speed_rows = (
        overview_query.with_entities(Task.created_at, Task.completed_at)
        .filter(
            Task.status == TaskStatus.DONE,
            Task.completed_at.isnot(None),
            Task.created_at.isnot(None),
            Task.completed_at >= now - timedelta(days=30),
        )
        .all()
    )
    avg_completion_days: float | None = None
    if completed_speed_rows:
        total_days = 0.0
        for created_at, completed_at in completed_speed_rows:
            delta = completed_at - created_at
            total_days += delta.total_seconds() / 86400
        avg_completion_days = total_days / len(completed_speed_rows)

    overview_task_rows = overview_query.with_entities(
        Task.id, Task.created_at, Task.completed_at
    ).all()
    task_meta = {
        task_id: {"created_at": created_at, "completed_at": completed_at}
        for task_id, created_at, completed_at in overview_task_rows
    }
    task_ids = list(task_meta.keys())

    time_to_completion_seconds: list[float] = []
    for meta in task_meta.values():
        created_at = meta["created_at"]
        completed_at = meta["completed_at"]
        if created_at and completed_at:
            delta = completed_at - created_at
            seconds = delta.total_seconds()
            if seconds >= 0:
                time_to_completion_seconds.append(seconds)

    pending_to_progress_seconds: list[float] = []
    progress_to_done_seconds: list[float] = []
    reopened_last_30 = 0
    reopened_current_month = 0
    month_start = date(today.year, today.month, 1)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    if task_ids:
        history_cutoff = now - timedelta(days=30)
        history_rows = (
            TaskStatusHistory.query.with_entities(
                TaskStatusHistory.task_id,
                TaskStatusHistory.from_status,
                TaskStatusHistory.to_status,
                TaskStatusHistory.changed_at,
            )
            .filter(TaskStatusHistory.task_id.in_(task_ids))
            .order_by(TaskStatusHistory.task_id, TaskStatusHistory.changed_at)
            .all()
        )
        history_map: dict[int, list[tuple[TaskStatus | None, TaskStatus, datetime]]] = defaultdict(list)
        for task_id, from_status, to_status, changed_at in history_rows:
            history_map[task_id].append((from_status, to_status, changed_at))

        for task_id, entries in history_map.items():
            meta = task_meta.get(task_id)
            if not meta:
                continue
            last_pending_time = meta["created_at"]
            last_in_progress_time = None
            for from_status, to_status, changed_at in entries:
                if not changed_at:
                    continue
                if (
                    from_status == TaskStatus.DONE
                    and to_status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)
                ):
                    if changed_at >= history_cutoff:
                        reopened_last_30 += 1
                    if month_start <= changed_at.date() < next_month:
                        reopened_current_month += 1
                if to_status == TaskStatus.PENDING:
                    last_pending_time = changed_at
                elif to_status == TaskStatus.IN_PROGRESS:
                    if last_pending_time:
                        delta = changed_at - last_pending_time
                        seconds = delta.total_seconds()
                        if seconds >= 0:
                            pending_to_progress_seconds.append(seconds)
                    last_in_progress_time = changed_at
                elif to_status == TaskStatus.DONE:
                    if last_in_progress_time:
                        delta = changed_at - last_in_progress_time
                        seconds = delta.total_seconds()
                        if seconds >= 0:
                            progress_to_done_seconds.append(seconds)

    def _avg_seconds(values: list[float]) -> float | None:
        if not values:
            return None
        return sum(values) / len(values)

    avg_creation_to_completion_seconds = _avg_seconds(time_to_completion_seconds)
    avg_pending_to_progress_seconds = _avg_seconds(pending_to_progress_seconds)
    avg_progress_to_done_seconds = _avg_seconds(progress_to_done_seconds)

    def _format_duration(seconds: float | None) -> str | None:
        if seconds is None:
            return None
        total_seconds = int(seconds)
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes = remainder // 60
        parts: list[str] = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if not parts and minutes:
            parts.append(f"{minutes}min")
        if not parts:
            parts.append("menos de 1 min")
        return " ".join(parts[:2])

    def _duration_source_label(count: int, noun: str) -> str:
        if count:
            return f"Media baseada em {count} {noun}"
        return "Sem dados suficientes"

    duration_metrics = [
        {
            "label": "Abertura -> Conclusao",
            "value": _format_duration(avg_creation_to_completion_seconds),
            "description": _duration_source_label(
                len(time_to_completion_seconds), "tarefas concluidas"
            ),
        },
        {
            "label": "Pendente -> Em andamento",
            "value": _format_duration(avg_pending_to_progress_seconds),
            "description": _duration_source_label(
                len(pending_to_progress_seconds), "transicoes registradas"
            ),
        },
        {
            "label": "Em andamento -> Concluida",
            "value": _format_duration(avg_progress_to_done_seconds),
            "description": _duration_source_label(
                len(progress_to_done_seconds), "transicoes registradas"
            ),
        },
    ]

    def _percent(part: int, whole: int) -> float:
        if not whole:
            return 0.0
        return (part / whole) * 100

    created_this_month = (
        overview_query.filter(
            Task.created_at.isnot(None),
            Task.created_at >= datetime.combine(month_start, datetime.min.time()),
            Task.created_at < datetime.combine(next_month, datetime.min.time()),
        ).count()
    )
    completed_this_month = (
        overview_query.filter(
            Task.completed_at.isnot(None),
            Task.completed_at >= datetime.combine(month_start, datetime.min.time()),
            Task.completed_at < datetime.combine(next_month, datetime.min.time()),
        ).count()
    )
    general_overview_month = {
        "created": created_this_month,
        "completed": completed_this_month,
        "reopened": reopened_current_month,
        "net": created_this_month - completed_this_month,
        "backlog": open_tasks,
    }
    general_overview_chart = {
        "type": "doughnut",
        "title": "Distribuicao do mes atual",
        "datasetLabel": "Tarefas",
        "labels": ["Criadas", "Concluidas", "Reabertas", "Backlog"],
        "values": [
            created_this_month,
            completed_this_month,
            reopened_current_month,
            open_tasks,
        ],
        "total": (
            created_this_month
            + completed_this_month
            + reopened_current_month
            + open_tasks
        ),
    }


    insights = [
        {
            "title": "Tarefas sem responsavel",
            "value": unassigned_tasks,
            "detail": f"{_percent(unassigned_tasks, open_tasks):.1f}% do backlog em aberto"
            if open_tasks
            else "Sem backlog em aberto",
        },
        {
            "title": "Sem prazo definido",
            "value": no_due_date_tasks,
            "detail": f"{_percent(no_due_date_tasks, open_tasks):.1f}% das tarefas abertas"
            if open_tasks
            else "Sem backlog em aberto",
        },
        {
            "title": "Idade media do backlog",
            "value": f"{avg_open_age_days:.1f} dias" if avg_open_age_days is not None else "Sem dados",
            "detail": "Tempo medio desde a criacao ate agora das tarefas abertas",
        },
        {
            "title": "Tarefas reabertas (30 dias)",
            "value": reopened_last_30,
            "detail": "Transicoes de concluida para pendente/em andamento nos ultimos 30 dias",
        },
    ]

    done_tasks = next((qty for st, qty in status_rows if st == TaskStatus.DONE), 0)
    completion_rate = (done_tasks / total_tasks * 100) if total_tasks else 0
    kpis = [
        {
            "label": "Tarefas totais",
            "value": total_tasks,
            "icon": "bi-stack",
            "description": "Acumulado em todo o sistema",
        },
        {
            "label": "Em aberto",
            "value": open_tasks,
            "icon": "bi-kanban",
            "description": "Pendentes + em andamento",
        },
        {
            "label": "Atrasadas",
            "value": overdue_tasks,
            "icon": "bi-exclamation-octagon",
            "description": "Necessitam atencao imediata",
        },
        {
            "label": "Concluidas (30 dias)",
            "value": completed_last_30,
            "icon": "bi-check2-circle",
            "description": "Fluxo recente de entregas",
        },
    ]

    return render_template(
        "admin/relatorio_tarefas.html",
        kpis=kpis,
        status_chart=status_chart,
        priority_chart=priority_chart,
        deadline_chart=deadline_chart,
        flow_chart=flow_chart,
        sector_chart=sector_chart,
        user_chart=user_chart,
        aging_chart=aging_chart,
        service_area_chart=service_area_chart,
        general_overview_month=general_overview_month,
        general_overview_chart=general_overview_chart,
        current_date=today,
        insights=insights,
        duration_metrics=duration_metrics,
        completion_rate=completion_rate,
        avg_completion_days=avg_completion_days,
        overdue_tasks=overdue_tasks,
        due_soon_tasks=due_soon_tasks,
        no_due_date_tasks=no_due_date_tasks,
        open_tasks=open_tasks,
        total_tasks=total_tasks,
    )


@relatorios_bp.route("/relatorios/permissoes", methods=["GET", "POST"])
@login_required
def report_permissions():
    """Manage report access per tag for master/admin users."""

    _require_master_admin()

    tags = Tag.query.order_by(sa.func.lower(Tag.nome)).all()
    users = (
        User.query.filter(User.ativo.is_(True))
        .order_by(User.name.asc(), User.username.asc())
        .all()
    )
    existing_permissions = ReportPermission.query.all()

    permitted_tags: dict[str, set[int]] = {code: set() for code in ALL_PERMISSION_DEFINITIONS}
    permitted_users: dict[str, set[int]] = {code: set() for code in ALL_PERMISSION_DEFINITIONS}
    for permission in existing_permissions:
        code = permission.report_code
        if code not in ALL_PERMISSION_DEFINITIONS:
            continue
        if permission.tag_id:
            permitted_tags[code].add(permission.tag_id)
        if permission.user_id:
            permitted_users[code].add(permission.user_id)

    if request.method == "POST":
        for code in ALL_PERMISSION_DEFINITIONS:
            submitted_tag_ids: set[int] = set()
            for raw in request.form.getlist(f"tags_{code}"):
                try:
                    submitted_tag_ids.add(int(raw))
                except (TypeError, ValueError):
                    continue

            submitted_user_ids: set[int] = set()
            for raw in request.form.getlist(f"users_{code}"):
                try:
                    submitted_user_ids.add(int(raw))
                except (TypeError, ValueError):
                    continue

            db.session.query(ReportPermission).filter(
                ReportPermission.report_code == code,
            ).delete(synchronize_session=False)

            for tag_id in submitted_tag_ids:
                db.session.add(ReportPermission(report_code=code, tag_id=tag_id))
            for user_id in submitted_user_ids:
                db.session.add(ReportPermission(report_code=code, user_id=user_id))

            permitted_tags[code] = submitted_tag_ids
            permitted_users[code] = submitted_user_ids

        db.session.commit()
        flash("Permissões atualizadas com sucesso.", "success")
        return redirect(url_for("relatorios.report_permissions"))

    permission_sections = [
        ("Relatórios", REPORT_DEFINITIONS),
        ("Portal", PORTAL_PERMISSION_DEFINITIONS),
    ]

    return render_template(
        "admin/report_permissions.html",
        tags=tags,
        users=users,
        permission_sections=permission_sections,
        permitted_tags=permitted_tags,
        permitted_users=permitted_users,
    )
