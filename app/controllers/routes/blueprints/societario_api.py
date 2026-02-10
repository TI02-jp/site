"""Blueprint de API para o modulo societario."""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import login_required
from sqlalchemy import func

from app import db
from app.controllers.routes._decorators import meeting_only_access_check
from app.controllers.routes.blueprints.societario import (
    STATUS_LABELS,
    STATUS_PROCESSO_CHOICES,
    TIPO_LABELS,
    TIPO_PROCESSO_CHOICES,
    _DEFAULT_API_LIMIT,
    _MAX_API_LIMIT,
    _apply_processo_changes,
    _ensure_history_table,
    _persist_processo_update,
    _request_payload,
    _resolve_empresa,
    _serialize_processo,
    _status_counts_payload,
    _parse_iso_date,
    societario_access_required,
)
from app.models.tables import ProcessoSocietario, ProcessoSocietarioHistorico, ProcessoSocietarioStatus, ProcessoSocietarioTipo

societario_api_bp = Blueprint("societario_api", __name__)


@societario_api_bp.route("/api/societario/meta", methods=["GET"])
@login_required
@meeting_only_access_check
@societario_access_required
def api_societario_meta():
    """Metadados para clientes API (enums e labels)."""
    return jsonify(
        {
            "success": True,
            "tipos": [{"value": value, "label": label} for value, label in TIPO_PROCESSO_CHOICES],
            "status": [{"value": value, "label": label} for value, label in STATUS_PROCESSO_CHOICES],
        }
    )


@societario_api_bp.route("/api/societario/dashboard", methods=["GET"])
@login_required
@meeting_only_access_check
@societario_access_required
def api_societario_dashboard():
    """Contadores de processos por status."""
    counts = _status_counts_payload()
    return jsonify(
        {
            "success": True,
            "status_counts": counts,
            "total": int(sum(counts.values())),
            "total_sem_finalizadas": int(
                sum(v for k, v in counts.items() if k != ProcessoSocietarioStatus.FINALIZADA.value)
            ),
        }
    )


@societario_api_bp.route("/api/societario/processos", methods=["GET"])
@login_required
@meeting_only_access_check
@societario_access_required
def api_societario_list():
    """Lista processos societarios com filtros."""
    _ensure_history_table()
    status_arg = (request.args.get("status") or "").strip().upper()
    empresa_arg = (request.args.get("empresa") or "").strip().upper()
    include_finalizadas = (request.args.get("include_finalizadas") or "").strip().lower() in {"1", "true", "yes"}
    try:
        limit = int((request.args.get("limit") or _DEFAULT_API_LIMIT))
    except (TypeError, ValueError):
        limit = _DEFAULT_API_LIMIT
    limit = max(1, min(limit, _MAX_API_LIMIT))

    query = ProcessoSocietario.query
    if status_arg and status_arg in STATUS_LABELS:
        query = query.filter(ProcessoSocietario.status == ProcessoSocietarioStatus(status_arg))
    elif not include_finalizadas:
        query = query.filter(ProcessoSocietario.status != ProcessoSocietarioStatus.FINALIZADA)

    if empresa_arg:
        query = query.filter(func.upper(ProcessoSocietario.nome_empresa).like(f"%{empresa_arg}%"))

    processos = query.order_by(ProcessoSocietario.data_inicio.asc(), ProcessoSocietario.id.asc()).limit(limit).all()
    ids = [p.id for p in processos]
    counts_map: dict[int, int] = {}
    if ids:
        rows = (
            db.session.query(
                ProcessoSocietarioHistorico.processo_id,
                func.count(ProcessoSocietarioHistorico.id),
            )
            .filter(ProcessoSocietarioHistorico.processo_id.in_(ids))
            .group_by(ProcessoSocietarioHistorico.processo_id)
            .all()
        )
        counts_map = {int(pid): int(total or 0) for pid, total in rows}

    return jsonify(
        {
            "success": True,
            "items": [_serialize_processo(p, counts_map.get(p.id, 0)) for p in processos],
            "count": len(processos),
            "limit": limit,
        }
    )


@societario_api_bp.route("/api/societario/processos/<int:processo_id>", methods=["GET"])
@login_required
@meeting_only_access_check
@societario_access_required
def api_societario_detail(processo_id: int):
    """Retorna detalhes de um processo."""
    _ensure_history_table()
    processo = ProcessoSocietario.query.get_or_404(processo_id)
    history_count = (
        db.session.query(func.count(ProcessoSocietarioHistorico.id))
        .filter(ProcessoSocietarioHistorico.processo_id == processo.id)
        .scalar()
    ) or 0
    return jsonify({"success": True, "item": _serialize_processo(processo, int(history_count))})


@societario_api_bp.route("/api/societario/processos", methods=["POST"])
@login_required
@meeting_only_access_check
@societario_access_required
def api_societario_create():
    """Cria processo via API."""
    payload = _request_payload()
    nome_empresa, empresa_id = _resolve_empresa(
        payload.get("nome_empresa"),
        payload.get("empresa_id"),
    )
    if not nome_empresa:
        return jsonify({"success": False, "message": "Informe o nome da empresa."}), 400

    tipo_raw = (payload.get("tipo_processo") or "").strip()
    status_raw = (payload.get("status") or "").strip() or ProcessoSocietarioStatus.VIABILIDADE.value
    data_inicio = _parse_iso_date(payload.get("data_inicio"))
    observacao = (payload.get("observacao") or "").strip() or None

    if tipo_raw not in TIPO_LABELS:
        return jsonify({"success": False, "message": "Tipo de processo invalido."}), 400
    if status_raw not in STATUS_LABELS:
        return jsonify({"success": False, "message": "Status invalido."}), 400
    if data_inicio is None:
        return jsonify({"success": False, "message": "Data de inicio invalida."}), 400

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
    return jsonify({"success": True, "item": _serialize_processo(processo, 0)}), 201


@societario_api_bp.route("/api/societario/processos/<int:processo_id>/update", methods=["POST"])
@login_required
@meeting_only_access_check
@societario_access_required
def api_societario_update(processo_id: int):
    """Atualiza processo via API (POST)."""
    _ensure_history_table()
    processo = ProcessoSocietario.query.get_or_404(processo_id)
    payload = _request_payload()
    changed, change_labels, error = _apply_processo_changes(processo, payload)
    if error:
        body, status = error
        return jsonify(body), status
    if not changed:
        return jsonify({"success": True, "message": "Sem alteracoes.", "item": _serialize_processo(processo, 0)})

    ok, out = _persist_processo_update(processo, change_labels)
    if not ok:
        return jsonify(out), 500
    out["item"] = _serialize_processo(processo, out.get("history_count", 0))
    return jsonify(out)


@societario_api_bp.route("/api/societario/processos/<int:processo_id>/delete", methods=["POST"])
@login_required
@meeting_only_access_check
@societario_access_required
def api_societario_delete(processo_id: int):
    """Exclui processo via API."""
    processo = ProcessoSocietario.query.get_or_404(processo_id)
    db.session.delete(processo)
    db.session.commit()
    return jsonify({"success": True, "deleted_id": processo_id})
