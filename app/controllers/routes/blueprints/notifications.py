"""
Blueprint para notificacoes e SSE (Server-Sent Events).

Este modulo contem rotas para centro de notificacoes, Web Push notifications
e Server-Sent Events (SSE) para atualizacoes em tempo real.

Rotas:
    - GET /notifications: Lista notificacoes do usuario (JSON)
    - GET /notifications/stream: SSE stream de notificacoes
    - GET /realtime/stream: SSE stream de atualizacoes gerais
    - GET /notificacoes: Centro de notificacoes (pagina)
    - POST /notifications/<int:notification_id>/read: Marca como lida
    - POST /notifications/read-all: Marca todas como lidas
    - POST /notifications/subscribe: Subscrever Web Push
    - POST /notifications/unsubscribe: Cancelar Web Push
    - GET /notifications/vapid-public-key: Chave publica VAPID
    - POST /notifications/test-push: Enviar push de teste

Dependencias:
    - models: TaskNotification, Task, PushSubscription
    - services: realtime (broadcaster), push_notifications
    - cache: Redis/Memcached para contadores
    - SSE: Server-Sent Events para notificacoes em tempo real

Autor: Refatoracao automatizada
Data: 2024
"""

from datetime import timedelta
from typing import Any, Optional
import json
import os
import time

from flask import (
    Blueprint,
    Response,
    current_app,
    g,
    jsonify,
    render_template,
    request,
    stream_with_context,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from app import cache, db, limiter
from app.controllers.routes._base import SAO_PAULO_TZ, utc3_now
from app.controllers.routes._decorators import meeting_only_access_check
from app.models.tables import NotificationType, PushSubscription, Task, TaskNotification
from app.utils.performance_middleware import track_custom_span


# =============================================================================
# BLUEPRINT DEFINITION
# =============================================================================

notifications_bp = Blueprint('notifications', __name__)


# =============================================================================
# CONSTANTES PARA CACHE
# =============================================================================

_NOTIFICATION_VERSION_KEY = "notification_version"
_NOTIFICATION_COUNT_KEY_PREFIX = "user_notifications:"


# =============================================================================
# HELPER FUNCTIONS - CACHE
# =============================================================================

def get_cache_timeout(config_key: str, default: int) -> int:
    """
    Obtem timeout de cache da configuracao ou usa valor padrao.

    Args:
        config_key: Chave de configuracao
        default: Valor padrao em segundos

    Returns:
        int: Timeout em segundos
    """
    return current_app.config.get(config_key, default)


def _get_notification_cache_timeout() -> int:
    """Retorna o timeout de cache para contadores de notificacoes."""
    return get_cache_timeout("NOTIFICATION_COUNT_CACHE_TIMEOUT", 60)


def _get_notification_version() -> int:
    """Retorna a versao atual do cache de notificacoes."""
    version = cache.get(_NOTIFICATION_VERSION_KEY)
    if version is None:
        version = int(time.time())
        _set_notification_version(int(version))
    return int(version)


def _set_notification_version(version: int) -> None:
    """Define a versao do cache de notificacoes."""
    ttl = max(_get_notification_cache_timeout(), 300)
    cache.set(_NOTIFICATION_VERSION_KEY, int(version), timeout=ttl)


def _notification_cache_key(user_id: int) -> str:
    """Gera chave de cache versionada para notificacoes do usuario."""
    return f"{_NOTIFICATION_COUNT_KEY_PREFIX}{_get_notification_version()}:{user_id}"


@cache.memoize(timeout=60)
def _memoized_unread_notifications(user_id: int) -> int:
    """
    Contador memoizado de notificacoes nao lidas (por usuario).

    Args:
        user_id: ID do usuario

    Returns:
        int: Quantidade de notificacoes nao lidas
    """
    return int(
        TaskNotification.query.filter(
            TaskNotification.user_id == user_id,
            TaskNotification.read_at.is_(None),
        ).count()
    )


def _get_unread_notifications_count(user_id: int, allow_cache: bool = True) -> int:
    """
    Recupera contagem de notificacoes nao lidas com suporte a cache.

    Args:
        user_id: ID do usuario
        allow_cache: Se deve usar cache

    Returns:
        int: Quantidade de notificacoes nao lidas
    """
    if allow_cache:
        return _memoized_unread_notifications(user_id)

    unread = TaskNotification.query.filter(
        TaskNotification.user_id == user_id,
        TaskNotification.read_at.is_(None),
    ).count()
    return int(unread)


def _invalidate_notification_cache(user_id: Optional[int] = None) -> None:
    """
    Invalida cache de notificacoes nao lidas.

    Args:
        user_id: ID do usuario (None = todos os usuarios)
    """
    if user_id is None:
        cache.delete_memoized(_memoized_unread_notifications)
        return
    cache.delete_memoized(_memoized_unread_notifications, user_id)


# =============================================================================
# HELPER FUNCTIONS - SERIALIZACAO E DADOS
# =============================================================================

def _user_can_access_task(task: Task, user) -> bool:
    """
    Verifica se usuario pode acessar uma tarefa.

    Args:
        task: Tarefa a verificar
        user: Usuario atual

    Returns:
        bool: True se o usuario pode acessar a tarefa
    """
    if not task or not user:
        return False
    if getattr(user, "role", None) == "admin":
        return True
    # Tarefas pÇ§blicas podem ser visualizadas pelo usuÇ½rio autenticado
    if not getattr(task, "is_private", False):
        return True
    user_id = getattr(user, "id", None)
    if not user_id:
        return False
    if (
        getattr(task, "assigned_to", None) == user_id
        or getattr(task, "created_by", None) == user_id
        or getattr(task, "completed_by", None) == user_id
    ):
        return True
    followers = getattr(task, "follow_up_assignments", None) or []
    return any(getattr(f, "user_id", None) == user_id for f in followers)


def _serialize_notification(notification: TaskNotification) -> dict[str, Any]:
    """
    Serializa uma TaskNotification em dict JSON-friendly.

    Args:
        notification: Notificacao a serializar

    Returns:
        dict: Notificacao serializada
    """
    raw_type = notification.type or NotificationType.TASK.value
    try:
        notification_type = NotificationType(raw_type)
    except ValueError:
        notification_type = NotificationType.TASK

    message = (notification.message or "").strip() or None
    action_label = None
    target_url = None

    if notification_type is NotificationType.ANNOUNCEMENT:
        announcement = notification.announcement
        if announcement:
            if not message:
                subject = (announcement.subject or "").strip()
                if subject:
                    message = f"Novo comunicado: {subject}"
                else:
                    message = "Novo comunicado publicado."
            target_url = url_for("announcements") + f"#announcement-{announcement.id}"
        else:
            if not message:
                message = "Comunicado removido."
        action_label = "Abrir comunicado" if target_url else None
    elif notification_type is NotificationType.RECURRING_INVOICE:
        if not message:
            message = "Emitir nota fiscal recorrente."
        target_url = url_for("notas_recorrentes")
        action_label = "Abrir notas recorrentes"
    else:
        task = notification.task
        if task:
            task_title = (task.title or "").strip()
            query_params: dict[str, object] = {"highlight_task": task.id}
            if notification_type is NotificationType.TASK_RESPONSE:
                query_params["open_responses"] = "1"
            if task.is_private:
                if current_user.is_authenticated and _user_can_access_task(task, current_user):
                    overview_endpoint = (
                        "tasks_overview" if current_user.role == "admin" else "tasks_overview_mine"
                    )
                    target_url = url_for(overview_endpoint, **query_params) + f"#task-{task.id}"
            else:
                target_url = url_for("tasks_sector", tag_id=task.tag_id, **query_params) + f"#task-{task.id}"
            if not message:
                prefix = (
                    "Tarefa atualizada"
                    if notification_type is NotificationType.TASK
                    else "Notificação"
                )
                if task_title:
                    message = f"{prefix}: {task_title}"
                else:
                    message = f"{prefix} atribuída a você."
        else:
            if not message:
                message = "Tarefa removida."
        if not action_label:
            if notification_type is NotificationType.TASK_RESPONSE:
                action_label = "Ver resposta" if target_url else None
            else:
                action_label = "Abrir tarefa" if target_url else None

    if not message:
        message = "Atualização disponível."

    created_at = notification.created_at or utc3_now()
    if created_at.tzinfo is None:
        # Notifications are stored in local (Sao Paulo) time as naive datetimes.
        # Explicitly attach the timezone so the frontend receives the correct offset.
        localized = created_at.replace(tzinfo=SAO_PAULO_TZ)
    else:
        localized = created_at.astimezone(SAO_PAULO_TZ)

    created_at_iso = localized.isoformat()
    display_dt = localized

    return {
        "id": notification.id,
        "type": notification_type.value,
        "message": message,
        "created_at": created_at_iso,
        "created_at_display": display_dt.strftime("%d/%m/%Y %H:%M"),
        "is_read": notification.is_read,
        "url": target_url,
        "action_label": action_label,
    }


def _prune_old_notifications(retention_days: int = 60) -> int:
    """
    Remove notificacoes antigas alem da janela de retencao.

    Args:
        retention_days: Dias de retencao

    Returns:
        int: Quantidade de notificacoes removidas
    """
    threshold = utc3_now() - timedelta(days=retention_days)
    deleted = (
        TaskNotification.query.filter(TaskNotification.created_at < threshold)
        .delete(synchronize_session=False)
    )
    if deleted:
        db.session.commit()
    return deleted


def _get_user_notification_items(limit: int | None = 20):
    """
    Retorna notificacoes serializadas e total de nao lidas para o usuario atual.

    Args:
        limit: Limite de notificacoes a retornar

    Returns:
        tuple: (lista de notificacoes serializadas, total de nao lidas)
    """
    _prune_old_notifications()
    notifications_query = (
        TaskNotification.query.filter(TaskNotification.user_id == current_user.id)
        .options(
            joinedload(TaskNotification.task).joinedload(Task.tag),
            joinedload(TaskNotification.announcement),
        )
        .order_by(TaskNotification.created_at.desc())
    )
    if limit is not None:
        notifications_query = notifications_query.limit(limit)
    notifications = notifications_query.all()
    unread_total = _get_unread_notifications_count(current_user.id)

    items = []
    for notification in notifications:
        items.append(_serialize_notification(notification))

    return items, unread_total


# =============================================================================
# CONTEXT PROCESSOR
# =============================================================================

@notifications_bp.app_context_processor
def inject_notification_counts():
    """
    Expoe o numero de notificacoes nao lidas aos templates.

    Returns:
        dict: Dicionario com unread_notifications_count
    """
    if not current_user.is_authenticated:
        return {"unread_notifications_count": 0}
    cached = getattr(g, "_cached_unread_notifications", None)
    if cached is not None:
        return {"unread_notifications_count": cached}
    with track_custom_span("sidebar", "load_unread_notifications"):
        unread = _get_unread_notifications_count(current_user.id)
    g._cached_unread_notifications = unread
    return {"unread_notifications_count": unread}


# =============================================================================
# ROTAS - NOTIFICACOES
# =============================================================================

@notifications_bp.route("/notifications", methods=["GET"])
@login_required
def list_notifications():
    """
    Retorna as notificacoes mais recentes do usuario (JSON).

    Returns:
        JSON: Lista de notificacoes e contagem de nao lidas
    """
    items, unread_total = _get_user_notification_items(limit=20)
    return jsonify({"notifications": items, "unread": unread_total})


@notifications_bp.route("/notificacoes")
@login_required
@meeting_only_access_check
def notifications_center():
    """
    Renderiza a pagina do centro de notificacoes.

    Returns:
        str: Template renderizado
    """
    items, unread_total = _get_user_notification_items(limit=50)
    return render_template(
        "notifications.html",
        notifications=items,
        unread_total=unread_total,
    )


@notifications_bp.route("/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def mark_notification_read(notification_id):
    """
    Marca uma notificacao como lida.

    Args:
        notification_id: ID da notificacao

    Returns:
        JSON: Resultado da operacao
    """
    notification = TaskNotification.query.filter(
        TaskNotification.id == notification_id,
        TaskNotification.user_id == current_user.id,
    ).first_or_404()
    if not notification.read_at:
        notification.read_at = utc3_now()
        db.session.commit()
        _invalidate_notification_cache(current_user.id)
    return jsonify({"success": True})


@notifications_bp.route("/notifications/read-all", methods=["POST"])
@login_required
def mark_all_notifications_read():
    """
    Marca todas as notificacoes nao lidas do usuario como lidas.

    Returns:
        JSON: Resultado da operacao e quantidade atualizada
    """
    updated = (
        TaskNotification.query.filter(
            TaskNotification.user_id == current_user.id,
            TaskNotification.read_at.is_(None),
        ).update(
            {TaskNotification.read_at: utc3_now()},
            synchronize_session=False,
        )
    )
    db.session.commit()
    if updated:
        _invalidate_notification_cache(current_user.id)
    return jsonify({"success": True, "updated": updated or 0})


# =============================================================================
# ROTAS - SERVER-SENT EVENTS (SSE)
# =============================================================================

@notifications_bp.route("/notifications/stream")
@login_required
@limiter.exempt  # SSE connections remain open; exempt from standard rate limiting
def notifications_stream():
    """
    Server-Sent Events stream entregando notificacoes em tempo real.

    CRITICO: Libera conexao de banco antes de entrar no loop de streaming
    para prevenir esgotamento do pool de conexoes.

    Returns:
        Response: Stream SSE
    """
    from app.services.realtime import get_broadcaster

    since_id = request.args.get("since", type=int) or 0
    batch_limit = current_app.config.get("NOTIFICATIONS_STREAM_BATCH", 50)
    user_id = current_user.id

    # Query DB once to get the initial last_sent_id, then release connection
    if not since_id:
        last_existing = (
            TaskNotification.query.filter(TaskNotification.user_id == user_id)
            .order_by(TaskNotification.id.desc())
            .with_entities(TaskNotification.id)
            .limit(1)
            .scalar()
        )
        since_id = last_existing or 0

    # CRITICAL: Release database connection before entering streaming loop
    # This prevents connection pool exhaustion from long-running SSE connections
    db.session.remove()

    broadcaster = get_broadcaster()
    client_id = broadcaster.register_client(user_id, subscribed_scopes={"notifications", "all"})
    # Reduced heartbeat to 15s to prevent worker exhaustion (was 45s)
    heartbeat_interval = current_app.config.get("NOTIFICATIONS_HEARTBEAT_INTERVAL", 15)

    def event_stream() -> Any:
        last_sent_id = since_id

        try:
            while True:
                # Check for new notifications in the database
                # We create a new session for each check to avoid holding connections
                new_notifications = (
                    TaskNotification.query.filter(
                        TaskNotification.user_id == user_id,
                        TaskNotification.id > last_sent_id,
                    )
                    .options(
                        joinedload(TaskNotification.task).joinedload(Task.tag),
                        joinedload(TaskNotification.announcement),
                    )
                    .order_by(TaskNotification.id.asc())
                    .limit(batch_limit)
                    .all()
                )

                if new_notifications:
                    serialized = [
                        _serialize_notification(notification)
                        for notification in new_notifications
                    ]
                    last_sent_id = max(notification.id for notification in new_notifications)
                    # Use cache for unread count to reduce database queries
                    unread_total = _get_unread_notifications_count(user_id, allow_cache=True)
                    payload = json.dumps(
                        {
                            "notifications": serialized,
                            "unread": unread_total,
                            "last_id": last_sent_id,
                        }
                    )
                    # Release DB connection immediately after query
                    db.session.remove()
                    yield f"data: {payload}\n\n"
                else:
                    # No new notifications - release connection and send keep-alive
                    db.session.remove()
                    yield ": keep-alive\n\n"

                # Wait for broadcaster events or timeout
                # This doesn't hold a DB connection
                triggered = broadcaster.wait_for_events(
                    user_id,
                    client_id,
                    timeout=heartbeat_interval,
                )

                # Small sleep to avoid busy-looping even after broadcast
                if triggered:
                    time.sleep(0.5)  # Brief delay to batch notifications

        except GeneratorExit:
            broadcaster.unregister_client(user_id, client_id)
            db.session.remove()
            return
        finally:
            broadcaster.unregister_client(user_id, client_id)
            db.session.remove()

    response = Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"  # Disable nginx buffering
    return response


@notifications_bp.route("/realtime/stream")
@login_required
@limiter.exempt  # SSE connections remain open; rate limiting causa reconexões agressivas
def realtime_stream():
    """
    Server-Sent Events stream para atualizacoes do sistema em tempo real.

    Suporta escopos via query param: ?scopes=notifications,tasks,all

    Returns:
        Response: Stream SSE
    """
    from app.services.realtime import get_broadcaster

    # Get subscribed scopes from query params (comma-separated)
    scopes_param = request.args.get("scopes", "all")
    subscribed_scopes = set(s.strip() for s in scopes_param.split(",") if s.strip())

    user_id = current_user.id

    # CRITICAL: Release database connection before entering streaming loop
    # This prevents connection pool exhaustion from long-running SSE connections
    db.session.remove()

    broadcaster = get_broadcaster()
    client_id = broadcaster.register_client(user_id, subscribed_scopes)
    # Reduced heartbeat to 10s to prevent worker exhaustion (was 30s)
    heartbeat_interval = current_app.config.get("REALTIME_HEARTBEAT_INTERVAL", 10)

    def event_stream() -> Any:
        try:
            last_event_id = 0
            while True:
                events = broadcaster.get_events(user_id, client_id, since_id=last_event_id)

                if events:
                    for event in events:
                        yield event.to_sse()
                        last_event_id = max(last_event_id, event.id)
                    continue

                triggered = broadcaster.wait_for_events(
                    user_id,
                    client_id,
                    timeout=heartbeat_interval,
                )
                if not triggered:
                    yield ": keep-alive\n\n"
        except GeneratorExit:
            broadcaster.unregister_client(user_id, client_id)
            db.session.remove()
            return
        finally:
            broadcaster.unregister_client(user_id, client_id)
            db.session.remove()

    response = Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"  # Disable nginx buffering
    return response


# =============================================================================
# ROTAS - WEB PUSH NOTIFICATIONS
# =============================================================================

@notifications_bp.route("/notifications/subscribe", methods=["POST"])
@login_required
def subscribe_push_notifications():
    """
    Subscrever Web Push notifications.

    Espera JSON com endpoint, keys.p256dh e keys.auth.

    Returns:
        JSON: Resultado da operacao
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Dados inválidos"}), 400

    endpoint = data.get("endpoint")
    keys = data.get("keys", {})
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")

    if not endpoint or not p256dh or not auth:
        return jsonify({"error": "Dados de subscrição incompletos"}), 400

    # Verificar se já existe uma subscrição para este endpoint
    existing = PushSubscription.query.filter_by(endpoint=endpoint).first()

    if existing:
        # Atualizar usuário se mudou e timestamp
        existing.user_id = current_user.id
        existing.p256dh_key = p256dh
        existing.auth_key = auth
        existing.user_agent = request.headers.get("User-Agent", "")[:500]
        existing.last_used_at = utc3_now()
    else:
        # Criar nova subscrição
        subscription = PushSubscription(
            user_id=current_user.id,
            endpoint=endpoint,
            p256dh_key=p256dh,
            auth_key=auth,
            user_agent=request.headers.get("User-Agent", "")[:500],
        )
        db.session.add(subscription)

    try:
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@notifications_bp.route("/notifications/unsubscribe", methods=["POST"])
@login_required
def unsubscribe_push_notifications():
    """
    Cancelar subscricao de Web Push notifications.

    Espera JSON com endpoint.

    Returns:
        JSON: Resultado da operacao
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Dados inválidos"}), 400

    endpoint = data.get("endpoint")
    if not endpoint:
        return jsonify({"error": "Endpoint não fornecido"}), 400

    # Remover subscrição
    PushSubscription.query.filter_by(
        endpoint=endpoint,
        user_id=current_user.id,
    ).delete()

    db.session.commit()
    return jsonify({"success": True})


@notifications_bp.route("/notifications/vapid-public-key", methods=["GET"])
def get_vapid_public_key():
    """
    Retorna a chave publica VAPID para subscricao push.

    Returns:
        JSON: Chave publica VAPID
    """
    public_key = os.getenv("VAPID_PUBLIC_KEY", "")
    if not public_key:
        return jsonify({"error": "VAPID não configurado"}), 500
    return jsonify({"publicKey": public_key})


@notifications_bp.route("/notifications/test-push", methods=["POST"])
@login_required
def test_push_notification():
    """
    Envia uma notificacao push de teste para o usuario atual.

    Returns:
        JSON: Resultado da operacao
    """
    from app.services.push_notifications import test_push_notification as send_test

    result = send_test(current_user.id)
    return jsonify(result)


# =============================================================================
# ALIASES PARA COMPATIBILIDADE
# =============================================================================

# Nota: Os endpoints sao registrados como notifications.list_notifications, etc.
# Para compatibilidade com templates antigos, registrar aliases no __init__.py
