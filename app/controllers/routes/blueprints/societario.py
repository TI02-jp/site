"""Blueprint do modulo societario com CRUD de processos."""

from __future__ import annotations

from datetime import datetime
import unicodedata
from functools import wraps

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import inspect, func

from app import db
from app.controllers.routes._base import utc3_now
from app.controllers.routes._decorators import meeting_only_access_check
from app.models.tables import (
    Empresa,
    ProcessoSocietario,
    ProcessoSocietarioHistorico,
    ProcessoSocietarioStatus,
    ProcessoSocietarioTipo,
)

societario_bp = Blueprint("societario", __name__)

TIPO_PROCESSO_CHOICES = [
    (ProcessoSocietarioTipo.ALTERACAO.value, "ALTERAÇÃO"),
    (ProcessoSocietarioTipo.RERATIFICACAO.value, "RERATIFICAÇÃO"),
    (ProcessoSocietarioTipo.TRANSFORMACAO.value, "TRANSFORMAÇÃO"),
    (ProcessoSocietarioTipo.BAIXA.value, "BAIXA"),
    (ProcessoSocietarioTipo.CONSTITUICAO.value, "CONSTITUIÇÃO"),
    (ProcessoSocietarioTipo.ATUALIZACAO_CNPJ_RECEITA.value, "ATUALIZAÇÃO CNPJ RECEITA"),
    (ProcessoSocietarioTipo.CRIACAO_FILIAL.value, "CRIAÇÃO FILIAL"),
    (ProcessoSocietarioTipo.CLIENTE_TRANSFERIDO.value, "CLIENTE TRANSFERIDO"),
]

STATUS_PROCESSO_CHOICES = [
    (ProcessoSocietarioStatus.VIABILIDADE.value, "VIABILIDADE"),
    (ProcessoSocietarioStatus.DIGITACAO.value, "DIGITAÇÃO"),
    (ProcessoSocietarioStatus.CORRECAO.value, "CORREÇÃO"),
    (ProcessoSocietarioStatus.ASSINATURA.value, "ASSINATURA"),
    (ProcessoSocietarioStatus.JUCESC.value, "JUCESC"),
    (ProcessoSocietarioStatus.FINALIZADA.value, "FINALIZADA"),
    (ProcessoSocietarioStatus.PARALISADA.value, "PARALISADA"),
    (ProcessoSocietarioStatus.DEFERIDO.value, "DEFERIDO"),
    (ProcessoSocietarioStatus.REGISTRADA.value, "REGISTRADA"),
]

TIPO_LABELS = {value: label for value, label in TIPO_PROCESSO_CHOICES}
STATUS_LABELS = {value: label for value, label in STATUS_PROCESSO_CHOICES}
_HISTORY_TABLE_CHECKED = False
_SOCIETARIO_ALLOWED_NAMES = {"juliane", "tadeu", "leticia"}
_DEFAULT_API_LIMIT = 100
_MAX_API_LIMIT = 500


def _normalize_person_name(value: str | None) -> str:
    raw = (value or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", raw)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def can_access_societario(user=None) -> bool:
    user_obj = user or current_user
    if not user_obj or not getattr(user_obj, "is_authenticated", False):
        return False
    if getattr(user_obj, "is_master", False):
        return True
    full_name = _normalize_person_name(getattr(user_obj, "name", ""))
    username = _normalize_person_name(getattr(user_obj, "username", ""))
    first_name = full_name.split(" ", 1)[0] if full_name else ""
    return (
        full_name in _SOCIETARIO_ALLOWED_NAMES
        or username in _SOCIETARIO_ALLOWED_NAMES
        or first_name in _SOCIETARIO_ALLOWED_NAMES
    )


def societario_access_required(view_fn):
    @wraps(view_fn)
    def wrapper(*args, **kwargs):
        if not can_access_societario(current_user):
            abort(403)
        return view_fn(*args, **kwargs)

    return wrapper


def _parse_iso_date(raw_value: str | None):
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _resolve_empresa(nome_empresa: str | None, empresa_id_raw: str | int | None) -> tuple[str, int | None]:
    empresa_id = None
    if empresa_id_raw not in (None, ""):
        try:
            empresa_id = int(empresa_id_raw)
        except (TypeError, ValueError):
            empresa_id = None

    if empresa_id:
        empresa = Empresa.query.get(empresa_id)
        if empresa:
            return (empresa.nome_empresa or "").strip().upper(), empresa.id

    clean_name = (nome_empresa or "").strip().upper()
    if not clean_name:
        return "", None

    empresa_match = Empresa.query.filter(
        db.func.upper(Empresa.nome_empresa) == clean_name
    ).first()
    if empresa_match:
        return clean_name, empresa_match.id
    return clean_name, None


def _build_update_log(change_labels: list[str]) -> str:
    return utc3_now().strftime("%d/%m/%Y %H:%M")


def _request_payload() -> dict:
    """Return update payload from JSON or form submissions."""
    json_payload = request.get_json(silent=True)
    if isinstance(json_payload, dict) and json_payload:
        return json_payload
    if request.form:
        return request.form.to_dict()
    return {}


def _ensure_history_table() -> None:
    """Ensure societario history table exists (local safety for pending migrations)."""
    global _HISTORY_TABLE_CHECKED
    if _HISTORY_TABLE_CHECKED:
        return
    bind = db.session.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("societario_processos_historico"):
        ProcessoSocietarioHistorico.__table__.create(bind=bind, checkfirst=True)
    _HISTORY_TABLE_CHECKED = True


def _serialize_processo(processo: ProcessoSocietario, history_count: int | None = None) -> dict:
    return {
        "id": processo.id,
        "nome_empresa": processo.nome_empresa,
        "empresa_id": processo.empresa_id,
        "tipo_processo": processo.tipo_processo.value if processo.tipo_processo else "",
        "tipo_processo_label": TIPO_LABELS.get(processo.tipo_processo.value, "") if processo.tipo_processo else "",
        "data_inicio": processo.data_inicio.strftime("%Y-%m-%d") if processo.data_inicio else "",
        "data_inicio_formatada": processo.data_inicio.strftime("%d/%m/%Y") if processo.data_inicio else "",
        "status": processo.status.value if processo.status else "",
        "status_label": STATUS_LABELS.get(processo.status.value, "") if processo.status else "",
        "observacao": processo.observacao or "",
        "conclusao": processo.conclusao or "",
        "updated_at": processo.updated_at.strftime("%d/%m/%Y %H:%M") if processo.updated_at else "",
        "created_at": processo.created_at.strftime("%d/%m/%Y %H:%M") if processo.created_at else "",
        "history_count": int(history_count or 0),
    }


def _status_counts_payload() -> dict[str, int]:
    grouped_status = (
        db.session.query(ProcessoSocietario.status, func.count(ProcessoSocietario.id))
        .group_by(ProcessoSocietario.status)
        .all()
    )
    status_counts = {value: 0 for value, _ in STATUS_PROCESSO_CHOICES}
    for status_value, count in grouped_status:
        key = status_value.value if hasattr(status_value, "value") else str(status_value)
        if key in status_counts:
            status_counts[key] = int(count or 0)
    return status_counts


def _apply_processo_changes(processo: ProcessoSocietario, payload: dict) -> tuple[bool, list[str], tuple[dict, int] | None]:
    changed = False
    change_labels: list[str] = []

    if "nome_empresa" in payload or "empresa_id" in payload:
        old_nome_empresa = processo.nome_empresa
        nome_empresa, empresa_id = _resolve_empresa(
            payload.get("nome_empresa", processo.nome_empresa),
            payload.get("empresa_id", processo.empresa_id),
        )
        if not nome_empresa:
            return False, change_labels, ({"success": False, "message": "Informe o nome da empresa."}, 400)
        if processo.nome_empresa != nome_empresa:
            processo.nome_empresa = nome_empresa
            changed = True
            change_labels.append(f"empresa de '{old_nome_empresa}' para '{nome_empresa}'")
        if processo.empresa_id != empresa_id:
            processo.empresa_id = empresa_id
            changed = True

    if "tipo_processo" in payload:
        tipo_raw = (payload.get("tipo_processo") or "").strip()
        if tipo_raw not in TIPO_LABELS:
            return False, change_labels, ({"success": False, "message": "Tipo de processo invalido."}, 400)
        new_tipo = ProcessoSocietarioTipo(tipo_raw)
        if processo.tipo_processo != new_tipo:
            old_tipo_label = TIPO_LABELS.get(processo.tipo_processo.value, processo.tipo_processo.value)
            processo.tipo_processo = new_tipo
            changed = True
            new_tipo_label = TIPO_LABELS.get(new_tipo.value, new_tipo.value)
            change_labels.append(f"tipo de '{old_tipo_label}' para '{new_tipo_label}'")

    if "data_inicio" in payload:
        data_inicio = _parse_iso_date(payload.get("data_inicio"))
        if data_inicio is None:
            return False, change_labels, ({"success": False, "message": "Data de inicio invalida."}, 400)
        if processo.data_inicio != data_inicio:
            old_data_label = processo.data_inicio.strftime("%d/%m/%Y") if processo.data_inicio else "-"
            processo.data_inicio = data_inicio
            changed = True
            new_data_label = data_inicio.strftime("%d/%m/%Y")
            change_labels.append(f"data inicio de '{old_data_label}' para '{new_data_label}'")

    if "observacao" in payload:
        observacao = (payload.get("observacao") or "").strip() or None
        if processo.observacao != observacao:
            processo.observacao = observacao
            changed = True
            change_labels.append("observacao atualizada")

    if "status" in payload:
        status_raw = (payload.get("status") or "").strip()
        if status_raw not in STATUS_LABELS:
            return False, change_labels, ({"success": False, "message": "Status invalido."}, 400)
        new_status = ProcessoSocietarioStatus(status_raw)
        if processo.status != new_status:
            old_status_label = STATUS_LABELS.get(processo.status.value, processo.status.value)
            processo.status = new_status
            changed = True
            new_status_label = STATUS_LABELS.get(new_status.value, new_status.value)
            change_labels.append(f"status de '{old_status_label}' para '{new_status_label}'")

    return changed, change_labels, None


def _persist_processo_update(processo: ProcessoSocietario, change_labels: list[str]) -> tuple[bool, dict]:
    processo.conclusao = _build_update_log(change_labels)
    history_change_text = "; ".join(change_labels) if change_labels else "Alteracao"
    history_entry = ProcessoSocietarioHistorico(
        processo_id=processo.id,
        changed_by_id=current_user.id if current_user.is_authenticated else None,
        alteracao=history_change_text,
    )
    db.session.add(history_entry)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Erro ao salvar processo societario %s", processo.id)
        return False, {"success": False, "message": f"Erro ao salvar: {exc}"}

    history_count = (
        db.session.query(func.count(ProcessoSocietarioHistorico.id))
        .filter(ProcessoSocietarioHistorico.processo_id == processo.id)
        .scalar()
    ) or 0
    return True, {
        "success": True,
        "conclusao": processo.conclusao or "",
        "status_label": STATUS_LABELS.get(processo.status.value, processo.status.value),
        "tipo_label": TIPO_LABELS.get(processo.tipo_processo.value, processo.tipo_processo.value),
        "updated_at": processo.updated_at.strftime("%d/%m/%Y %H:%M") if processo.updated_at else "",
        "history_count": int(history_count),
        "history_entry": {
            "changed_at": history_entry.changed_at.strftime("%d/%m/%Y %H:%M") if history_entry.changed_at else "",
            "alteracao": history_entry.alteracao or "",
            "changed_by": (current_user.name or current_user.username or "").strip(),
            "changed_by_id": current_user.id if current_user.is_authenticated else None,
        },
    }


@societario_bp.route("/societario", methods=["GET"])
@login_required
@meeting_only_access_check
@societario_access_required
def societario():
    """Renderiza a pagina principal de processos societarios."""
    _ensure_history_table()
    status_arg = (request.args.get("status") or "").strip().upper()
    status_values = {value for value, _ in STATUS_PROCESSO_CHOICES}
    active_status = status_arg if status_arg in status_values else ""

    processos_query = ProcessoSocietario.query
    if active_status:
        processos_query = processos_query.filter(
            ProcessoSocietario.status == ProcessoSocietarioStatus(active_status)
        )
    else:
        processos_query = processos_query.filter(
            ProcessoSocietario.status != ProcessoSocietarioStatus.FINALIZADA
        )

    processos = processos_query.order_by(
        ProcessoSocietario.data_inicio.asc(),
        ProcessoSocietario.id.asc(),
    ).all()
    processo_ids = [p.id for p in processos]
    history_counts: dict[int, int] = {}
    if processo_ids:
        count_rows = (
            db.session.query(
                ProcessoSocietarioHistorico.processo_id,
                func.count(ProcessoSocietarioHistorico.id),
            )
            .filter(ProcessoSocietarioHistorico.processo_id.in_(processo_ids))
            .group_by(ProcessoSocietarioHistorico.processo_id)
            .all()
        )
        history_counts = {int(pid): int(total or 0) for pid, total in count_rows}

    grouped_status = (
        db.session.query(ProcessoSocietario.status, func.count(ProcessoSocietario.id))
        .group_by(ProcessoSocietario.status)
        .all()
    )
    status_counts = {value: 0 for value, _ in STATUS_PROCESSO_CHOICES}
    for status_value, count in grouped_status:
        key = status_value.value if hasattr(status_value, "value") else str(status_value)
        if key in status_counts:
            status_counts[key] = int(count or 0)

    total_processos = sum(
        count for status, count in status_counts.items()
        if status != ProcessoSocietarioStatus.FINALIZADA.value
    )
    # Cache empresas ativas por 5 minutos (lista muda raramente)
    from app.extensions.cache import cached_query

    @cached_query(timeout=300, key_prefix='empresas_ativas')
    def _get_empresas_ativas():
        return Empresa.query.filter(Empresa.ativo.is_(True)).order_by(Empresa.nome_empresa.asc()).all()

    empresas = _get_empresas_ativas()
    return render_template(
        "societario.html",
        processos=processos,
        history_counts=history_counts,
        empresas=empresas,
        tipo_choices=TIPO_PROCESSO_CHOICES,
        status_choices=STATUS_PROCESSO_CHOICES,
        status_counts=status_counts,
        active_status=active_status,
        total_processos=total_processos,
    )


@societario_bp.route("/societario/processos", methods=["POST"])
@login_required
@meeting_only_access_check
@societario_access_required
def societario_create():
    """Cria processo societario."""
    nome_empresa, empresa_id = _resolve_empresa(
        request.form.get("nome_empresa"),
        request.form.get("empresa_id"),
    )
    if not nome_empresa:
        abort(400, "Informe o nome da empresa.")

    tipo_raw = (request.form.get("tipo_processo") or "").strip()
    status_raw = (request.form.get("status") or "").strip() or ProcessoSocietarioStatus.VIABILIDADE.value
    data_inicio = _parse_iso_date(request.form.get("data_inicio"))
    observacao = (request.form.get("observacao") or "").strip() or None

    if tipo_raw not in TIPO_LABELS:
        abort(400, "Tipo de processo invalido.")
    if status_raw not in STATUS_LABELS:
        abort(400, "Status invalido.")
    if data_inicio is None:
        abort(400, "Data de inicio invalida.")

    processo = ProcessoSocietario(
        nome_empresa=nome_empresa,
        empresa_id=empresa_id,
        tipo_processo=ProcessoSocietarioTipo(tipo_raw),
        data_inicio=data_inicio,
        status=ProcessoSocietarioStatus(status_raw),
        observacao=observacao,
    )
    db.session.add(processo)
    db.session.commit()
    return redirect(url_for("societario.societario"))


@societario_bp.route("/societario/processos/<int:processo_id>", methods=["PUT", "POST", "PATCH"])
@login_required
@meeting_only_access_check
@societario_access_required
def societario_update(processo_id: int):
    """Atualiza campos do processo via autosave."""
    _ensure_history_table()
    processo = ProcessoSocietario.query.get_or_404(processo_id)
    payload = _request_payload()
    changed, change_labels, error = _apply_processo_changes(processo, payload)
    if error:
        body, status = error
        return jsonify(body), status

    if not changed:
        return jsonify({"success": True, "message": "Sem alteracoes."})

    ok, payload_out = _persist_processo_update(processo, change_labels)
    if not ok:
        return jsonify(payload_out), 500
    return jsonify(payload_out)


@societario_bp.route("/societario/processos/<int:processo_id>/historico", methods=["GET"])
@login_required
@meeting_only_access_check
@societario_access_required
def societario_history(processo_id: int):
    """Retorna historico completo de um processo em JSON."""
    _ensure_history_table()
    ProcessoSocietario.query.get_or_404(processo_id)
    items = (
        ProcessoSocietarioHistorico.query.filter_by(processo_id=processo_id)
        .order_by(ProcessoSocietarioHistorico.changed_at.desc(), ProcessoSocietarioHistorico.id.desc())
        .all()
    )
    payload = []
    for item in items:
        payload.append(
            {
                "changed_at": item.changed_at_formatado,
                "alteracao": item.alteracao or "",
                "changed_by": (item.changed_by.name or item.changed_by.username) if item.changed_by else "",
                "changed_by_id": item.changed_by_id,
            }
        )
    return jsonify({"success": True, "items": payload, "count": len(payload)})


@societario_bp.route("/societario/processos/<int:processo_id>/delete", methods=["POST"])
@login_required
@meeting_only_access_check
@societario_access_required
def societario_delete(processo_id: int):
    """Exclui um processo societario."""
    processo = ProcessoSocietario.query.get_or_404(processo_id)
    db.session.delete(processo)
    db.session.commit()
    return redirect(url_for("societario.societario"))
