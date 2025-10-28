"""Helpers for meeting room scheduling logic."""

import time
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Sequence
from flask import flash, current_app
from markupsafe import Markup
from dateutil.parser import isoparse

from app import db
from app.models.tables import (
    User,
    Reuniao,
    ReuniaoParticipante,
    ReuniaoStatus,
    ReuniaoRecorrenciaTipo,
    default_meet_settings,
)
from app.utils.permissions import is_user_admin
from app.services.meeting_recurrence import (
    generate_recurrence_dates,
    generate_recurrence_group_id,
)
from app.services.google_calendar import (
    list_upcoming_events,
    create_meet_event,
    create_event,
    update_event,
    delete_event,
    get_calendar_timezone,
    update_meet_space_preferences,
)
from app.services.calendar_cache import calendar_cache
from app.services.background import submit_background_job
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

# Lazy-load calendar timezone to avoid API call at module import
_CALENDAR_TZ = None

# Global state for Meet preference sync cooldown handling
_meet_sync_disabled_until: float = 0.0
_meet_sync_disable_lock = threading.Lock()
_pending_meet_sync: set[int] = set()
_pending_meet_sync_lock = threading.Lock()

def get_calendar_tz():
    """Get calendar timezone with lazy initialization."""
    global _CALENDAR_TZ
    if _CALENDAR_TZ is None:
        _CALENDAR_TZ = get_calendar_timezone()
    return _CALENDAR_TZ

CALENDAR_TZ = get_calendar_tz()

# Cache de mapeamento email→username para evitar múltiplas queries
_user_email_cache: dict[str, str] = {}
_user_cache_expires: datetime | None = None
_user_cache_ttl = timedelta(minutes=5)  # Mesmo TTL do cache de eventos

MIN_GAP = timedelta(minutes=2)

# Request coalescing para prevenir múltiplas chamadas simultâneas à API do Google Calendar
# Usa Event ao invés de Lock para permitir que múltiplas threads esperem pelo mesmo fetch
_fetch_event: threading.Event | None = None
_fetch_event_lock = threading.Lock()
_fetch_timeout = 5.0  # segundos (reduzido de 10s para falhar mais rápido)

# Combined cache version control for proper invalidation
_combined_cache_version: int = 0
_combined_cache_version_lock = threading.Lock()


def get_users_by_email_cached(emails: set[str]) -> dict[str, str]:
    """Return email→username mapping with caching to reduce database queries.

    Cache expires after 5 minutes (same as calendar events cache).
    """
    global _user_email_cache, _user_cache_expires

    now = datetime.now(CALENDAR_TZ)

    # Check if cache expired
    if _user_cache_expires is None or now >= _user_cache_expires:
        _user_email_cache = {}
        _user_cache_expires = now + _user_cache_ttl

    # Find emails not yet in cache
    missing_emails = emails - set(_user_email_cache.keys())

    # Query database only for missing emails
    if missing_emails:
        users = User.query.filter(User.email.in_(list(missing_emails))).all()
        for u in users:
            _user_email_cache[u.email] = u.username

    # Return only requested emails from cache
    return {email: _user_email_cache[email] for email in emails if email in _user_email_cache}

STATUS_SEQUENCE = [
    ReuniaoStatus.AGENDADA,
    ReuniaoStatus.EM_ANDAMENTO,
    ReuniaoStatus.REALIZADA,
    ReuniaoStatus.ADIADA,
    ReuniaoStatus.CANCELADA,
]

STATUS_DETAILS = {
    ReuniaoStatus.AGENDADA: {"label": "Agendada", "color": "#ffc107"},
    ReuniaoStatus.EM_ANDAMENTO: {"label": "Em Andamento", "color": "#198754"},
    ReuniaoStatus.REALIZADA: {"label": "Realizada", "color": "#dc3545"},
    ReuniaoStatus.ADIADA: {"label": "Adiada", "color": "#0d6efd"},
    ReuniaoStatus.CANCELADA: {"label": "Cancelada", "color": "#6c757d"},
}

EDITABLE_STATUSES = {ReuniaoStatus.AGENDADA, ReuniaoStatus.ADIADA}
CONFIGURABLE_STATUSES = {
    ReuniaoStatus.AGENDADA,
    ReuniaoStatus.EM_ANDAMENTO,
    ReuniaoStatus.ADIADA,
}
RESCHEDULE_REQUIRED_STATUSES = {ReuniaoStatus.ADIADA}


@dataclass(slots=True)
class MeetingOperationResult:
    """Lightweight data returned after creating or updating a meeting."""

    meeting_id: int
    meet_link: str | None


def get_status_label(status: ReuniaoStatus) -> str:
    """Return the human readable label for a meeting status."""

    return STATUS_DETAILS.get(status, {}).get("label", status.value.title())


def get_status_color(status: ReuniaoStatus) -> str:
    """Return the calendar color associated with a meeting status."""

    return STATUS_DETAILS.get(status, {}).get("color", "#6c757d")


def _compose_calendar_description(
    base_description: str | None,
    participant_usernames: Sequence[str],
    status_label: str,
) -> str:
    """Build the description sent to Google Calendar."""

    parts: list[str] = []
    base = (base_description or "").strip()
    if base:
        parts.append(base)
    if participant_usernames:
        parts.append("Participantes: " + ", ".join(participant_usernames))
    parts.append(f"Status: {status_label}")
    return "\n".join(parts)


class MeetingStatusConflictError(Exception):
    """Raised when a requested reschedule conflicts with existing meetings."""

    def __init__(self, messages: Sequence[str]):
        self.messages = list(messages)
        super().__init__(" ".join(self.messages))


def _normalize_meet_settings(raw: Any | None = None) -> dict[str, bool]:
    """Return a normalized dictionary with all Meet configuration flags."""

    defaults = default_meet_settings()
    normalized = defaults.copy()
    if isinstance(raw, dict):
        for key in defaults:
            if key in raw:
                normalized[key] = bool(raw[key])
    return normalized


def _apply_meet_preferences(meeting: Reuniao) -> tuple[bool | None, str | None]:
    """Sync stored Meet preferences with the Google Meet space.

    Adds adaptive backoff so repeated failures (e.g. missing permissions) do not
    impact request latency.

    Returns:
        Tuple of (success: bool | None, error_message: str | None)
        - (True, None): Successfully synced
        - (False, error_msg): Failed after retries
        - (None, error_msg): Skipped due to cooldown or no meet_link
    """
    global _meet_sync_disabled_until

    if not meeting.meet_link:
        return None, "Reunião não possui link do Google Meet"

    now_ts = time.time()
    disable_seconds = current_app.config.get("MEET_SYNC_DISABLE_SECONDS", 600)
    retry_cooldown = current_app.config.get("MEET_SYNC_RETRY_COOLDOWN", 120)

    with _meet_sync_disable_lock:
        disabled_until = _meet_sync_disabled_until
    if disabled_until and now_ts < disabled_until:
        remaining_seconds = int(disabled_until - now_ts)
        current_app.logger.debug(
            "Skipping Meet preference sync for meeting %s (cooldown %.1fs remaining)",
            meeting.id,
            disabled_until - now_ts,
        )
        return None, f"Sincronização temporariamente desabilitada (aguarde {remaining_seconds}s)"

    settings = _normalize_meet_settings(meeting.meet_settings)
    max_retries = max(1, int(current_app.config.get("MEET_PREFERENCES_MAX_RETRIES", 3)))
    base_delay = current_app.config.get("MEET_PREFERENCES_RETRY_BASE_DELAY", 0.5)

    for attempt in range(1, max_retries + 1):
        try:
            current_app.logger.info(
                "Applying Meet settings for meeting %s (attempt %s/%s)",
                meeting.id,
                attempt,
                max_retries,
            )
            update_meet_space_preferences(meeting.meet_link, settings)
            current_app.logger.info(
                "Successfully applied Meet settings for meeting %s on attempt %s",
                meeting.id,
                attempt,
            )
            return True, None

        except Exception as exc:
            error_msg = (
                f"Attempt {attempt}/{max_retries} failed to apply Meet settings for meeting "
                f"{meeting.id}: {type(exc).__name__}: {exc}"
            )
            message = str(exc)
            unauthorized = False
            if isinstance(exc, RefreshError) and "unauthorized_client" in message:
                unauthorized = True
            elif isinstance(exc, HttpError) and getattr(exc, "resp", None) and getattr(exc.resp, "status", None) in (401, 403):
                unauthorized = True

            if unauthorized:
                current_app.logger.error(
                    "Skipping Meet preference sync for meeting %s: service account lacks required permissions. "
                    "Verify Google Meet scopes for the configured service account.",
                    meeting.id,
                    exc_info=exc,
                )
                cooldown_target = time.time() + disable_seconds
                with _meet_sync_disable_lock:
                    _meet_sync_disabled_until = max(_meet_sync_disabled_until, cooldown_target)
                return False, "Permissões insuficientes para configurar o Google Meet. Contate o administrador."

            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))
                current_app.logger.warning("%s. Retrying in %.1fs...", error_msg, delay)
                time.sleep(delay)
            else:
                current_app.logger.exception("%s. All retry attempts exhausted.", error_msg)
                cooldown_target = time.time() + retry_cooldown
                with _meet_sync_disable_lock:
                    _meet_sync_disabled_until = max(_meet_sync_disabled_until, cooldown_target)
                user_friendly_error = f"Falha ao configurar Google Meet após {max_retries} tentativas. Tente novamente mais tarde."
                return False, user_friendly_error

    return False, "Erro desconhecido ao configurar Google Meet"


def _queue_meet_preferences_sync(meeting_id: int, reason: str = "unspecified") -> None:
    """Queue Meet preference synchronization in a background worker."""
    if not meeting_id:
        return

    with _pending_meet_sync_lock:
        if meeting_id in _pending_meet_sync:
            current_app.logger.debug(
                "Meet sync already queued for meeting %s (reason=%s)",
                meeting_id,
                reason,
            )
            return
        _pending_meet_sync.add(meeting_id)

    def _job(meeting_id: int, reason: str) -> None:
        try:
            meeting = db.session.get(Reuniao, meeting_id)
            if not meeting or not meeting.meet_link:
                current_app.logger.debug(
                    "Skipping Meet sync for meeting %s (reason=%s) - no record or Meet link",
                    meeting_id,
                    reason,
                )
                return
            result, error_message = _apply_meet_preferences(meeting)
            if result is False:
                current_app.logger.warning(
                    "Meet preference sync failed for meeting %s (reason=%s): %s",
                    meeting_id,
                    reason,
                    error_message or "Unknown error",
                )
        except Exception:
            current_app.logger.exception(
                "Unhandled error while syncing Meet preferences (meeting=%s, reason=%s)",
                meeting_id,
                reason,
            )
        finally:
            db.session.remove()
            with _pending_meet_sync_lock:
                _pending_meet_sync.discard(meeting_id)

    queued = submit_background_job(_job, meeting_id, reason=reason)
    if not queued:
        current_app.logger.debug(
            "Meet preference sync executed synchronously for meeting %s (reason=%s)",
            meeting_id,
            reason,
        )


def _parse_course_id(form) -> int | None:
    """Return the course identifier associated with the form submission."""

    field = getattr(form, "course_id", None)
    if not field or not getattr(field, "data", None):
        return None
    try:
        return int(field.data)
    except (TypeError, ValueError):
        return None


def populate_participants_choices(form):
    """Populate participant choices ordered by name."""
    form.participants.choices = [
        (u.id, u.name)
        for u in User.query.filter_by(ativo=True).order_by(User.name).all()
    ]


def fetch_raw_events():
    """Fetch upcoming events from Google Calendar with caching and request coalescing.

    Uses a 3-tier protection strategy:
    1. Primary cache (5 minutes) - frequently accessed
    2. Stale cache (15 minutes) - fallback during API failures
    3. Request coalescing - multiple concurrent requests wait for single fetch

    Thread safety (request coalescing pattern):
    - First thread: Creates event, fetches from API, sets event to signal completion
    - Concurrent threads: Wait on existing event (up to 5s), then read from cache
    - Timeout: 5 seconds to fail fast
    """
    global _fetch_event

    # Fast path: Try primary cache first (no lock needed)
    cached_events = calendar_cache.get("raw_calendar_events")
    if cached_events is not None:
        return cached_events

    # Cache miss - check if another thread is already fetching
    with _fetch_event_lock:
        if _fetch_event is not None:
            # Another thread is fetching - wait for it
            event_to_wait = _fetch_event
        else:
            # We're the first - create event and fetch
            _fetch_event = threading.Event()
            event_to_wait = None

    # If another thread is fetching, wait for it to complete
    if event_to_wait is not None:
        wait_success = event_to_wait.wait(timeout=_fetch_timeout)

        if not wait_success:
            # Timeout waiting - try stale cache
            current_app.logger.warning(
                "Timeout waiting for calendar fetch after %.1fs - using stale cache",
                _fetch_timeout
            )
            stale_events = calendar_cache.get("raw_calendar_events_stale")
            if stale_events is not None:
                return stale_events
            # No stale cache - raise timeout
            raise TimeoutError(
                f"Timeout waiting for calendar fetch (>{_fetch_timeout}s) and no stale cache available"
            )

        # Fetch completed by other thread - check cache
        cached_events = calendar_cache.get("raw_calendar_events")
        if cached_events is not None:
            return cached_events

        # Cache somehow missing - fall through to fetch ourselves
        current_app.logger.warning(
            "Cache missing after fetch event signaled - fetching again"
        )

    # We're responsible for fetching - do it now
    try:
        current_app.logger.debug("Fetching calendar events from Google API")
        fetch_start = time.perf_counter()

        # Get stale cache as fallback
        stale_events = calendar_cache.get("raw_calendar_events_stale")
        calendar_tz = CALENDAR_TZ
        now = datetime.now(calendar_tz)
        future_window_days = max(
            int(current_app.config.get("MEETING_CALENDAR_FUTURE_DAYS", 365 * 3)),
            0,
        )
        time_max = now + timedelta(days=future_window_days)

        try:
            # Fetch from Google Calendar API
            events = list_upcoming_events(max_results=None, time_max=time_max)
            fetch_duration = (time.perf_counter() - fetch_start) * 1000

            # Update both primary and stale caches
            calendar_cache.set("raw_calendar_events", events, ttl=300)  # 5 minutes
            calendar_cache.set("raw_calendar_events_stale", events, ttl=900)  # 15 minutes

            current_app.logger.info(
                "Successfully fetched %d calendar events from Google API in %.2fms",
                len(events),
                fetch_duration
            )

            return events

        except Exception as e:
            fetch_duration = (time.perf_counter() - fetch_start) * 1000

            # If API call fails but we have stale data, use it
            if stale_events is not None:
                current_app.logger.warning(
                    "Google Calendar API failed after %.2fms, using stale cache: %s",
                    fetch_duration,
                    str(e)
                )
                # Refresh primary cache with stale data to avoid repeated API calls
                calendar_cache.set("raw_calendar_events", stale_events, ttl=60)
                return stale_events

            # No cache available, re-raise the exception
            current_app.logger.error(
                "Google Calendar API failed after %.2fms and no stale cache available: %s",
                fetch_duration,
                str(e)
            )
            raise

    finally:
        # Signal waiting threads and clear event
        with _fetch_event_lock:
            if _fetch_event is not None:
                _fetch_event.set()
                _fetch_event = None


def invalidate_calendar_cache():
    """Soft invalidate: shorten TTL instead of deleting cache completely.

    This prevents cache stampede - multiple requests won't hit the API simultaneously.
    Old data is still available for 30 seconds while being refreshed.
    """
    # Get current cache
    cached_events = calendar_cache.get("raw_calendar_events")
    if cached_events is not None:
        # Shorten TTL to 30 seconds instead of deleting
        # This allows in-flight requests to complete without stampeding the API
        calendar_cache.set("raw_calendar_events", cached_events, ttl=30)
    # Don't touch the stale cache - it serves as fallback

    # Invalidate combined_events cache for all users
    # Since SimpleCache doesn't support pattern matching, we increment a version counter
    # All cached entries will check this version and invalidate themselves if stale
    global _combined_cache_version
    with _combined_cache_version_lock:
        _combined_cache_version += 1
        current_app.logger.debug(f"Invalidated combined cache, new version: {_combined_cache_version}")


def _next_available(
    start: datetime, dur: timedelta, intervals: list[tuple[datetime, datetime]]
):
    intervals = sorted(intervals, key=lambda x: x[0])
    while True:
        for s, e in intervals:
            if start < e + MIN_GAP and start + dur > s - MIN_GAP:
                start = e + MIN_GAP
                break
        else:
            return start


def _collect_intervals(
    raw_events,
    *,
    exclude_meeting_id: int | None = None,
    exclude_event_id: str | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
):
    intervals: list[tuple[datetime, datetime]] = []
    for e in raw_events:
        if exclude_event_id and e.get("id") == exclude_event_id:
            continue
        existing_start = isoparse(
            e["start"].get("dateTime") or e["start"].get("date")
        ).astimezone(CALENDAR_TZ)
        existing_end = isoparse(
            e["end"].get("dateTime") or e["end"].get("date")
        ).astimezone(CALENDAR_TZ)
        intervals.append((existing_start, existing_end))

    query = Reuniao.query.with_entities(
        Reuniao.id, Reuniao.inicio, Reuniao.fim, Reuniao.google_event_id
    )
    if window_start is not None:
        query = query.filter(Reuniao.fim >= window_start - MIN_GAP)
    if window_end is not None:
        query = query.filter(Reuniao.inicio <= window_end + MIN_GAP)
    if exclude_meeting_id is not None:
        query = query.filter(Reuniao.id != exclude_meeting_id)

    for meeting_id, inicio, fim, google_event_id in query.all():
        if exclude_event_id and google_event_id == exclude_event_id:
            continue
        existing_start = inicio.astimezone(CALENDAR_TZ)
        existing_end = fim.astimezone(CALENDAR_TZ)
        intervals.append((existing_start, existing_end))

    return intervals


def _calculate_adjusted_start(
    start_dt: datetime, duration: timedelta, intervals: list[tuple[datetime, datetime]], now
) -> tuple[datetime, list[str]]:
    proposed_start = max(start_dt, now + MIN_GAP)
    messages: list[str] = []
    if proposed_start != start_dt:
        messages.append(
            "Reuniões devem ser agendadas com pelo menos 2 minutos de antecedência."
        )
    adjusted_start = _next_available(proposed_start, duration, intervals)
    if adjusted_start != proposed_start:
        messages.append("Horário conflita com outra reunião.")
    return adjusted_start, messages


def _auto_progress_status(
    meeting: Reuniao, start_dt: datetime, end_dt: datetime, now: datetime
) -> bool:
    """Update the meeting status according to the current time.

    Returns ``True`` if the status changed.
    """

    status = meeting.status
    new_status = status
    if status in {ReuniaoStatus.ADIADA, ReuniaoStatus.CANCELADA, ReuniaoStatus.REALIZADA}:
        return False
    if status == ReuniaoStatus.AGENDADA:
        if now >= end_dt:
            new_status = ReuniaoStatus.REALIZADA
        elif start_dt <= now < end_dt:
            new_status = ReuniaoStatus.EM_ANDAMENTO
    elif status == ReuniaoStatus.EM_ANDAMENTO:
        if now >= end_dt:
            new_status = ReuniaoStatus.REALIZADA
        elif now < start_dt:
            new_status = ReuniaoStatus.AGENDADA
    if new_status != status:
        meeting.status = new_status
        return True
    return False


def _meeting_participant_metadata(meeting: Reuniao):
    """Return participant information used in calendar serialization."""

    participant_usernames: list[str] = []
    participant_ids: list[int] = []
    participant_details: list[dict[str, Any]] = []
    participant_emails: list[str] = []
    host_candidates: list[dict[str, Any]] = []
    seen_host_ids: set[int] = set()
    participant_id_set: set[int] = set()
    for participant in meeting.participantes:
        participant_usernames.append(participant.username_usuario)
        participant_ids.append(participant.id_usuario)
        participant_id_set.add(participant.id_usuario)
        if participant.usuario and participant.usuario.name:
            display_name = participant.usuario.name
        else:
            display_name = participant.username_usuario
        participant_details.append(
            {
                "id": participant.id_usuario,
                "name": display_name,
                "username": participant.username_usuario,
                "email": participant.usuario.email if participant.usuario else None,
            }
        )
        if participant.usuario and participant.usuario.email:
            participant_emails.append(participant.usuario.email)
        if participant.id_usuario not in seen_host_ids:
            host_candidates.append({"id": participant.id_usuario, "name": display_name})
            seen_host_ids.add(participant.id_usuario)
    creator_name = ""
    creator_username = ""
    if meeting.criador:
        creator_name = meeting.criador.name or meeting.criador.username
        creator_username = meeting.criador.username
        if meeting.criador.email:
            participant_emails.append(meeting.criador.email)
        if (
            meeting.criador.id in participant_id_set
            and meeting.criador.id not in seen_host_ids
        ):
            host_candidates.append({"id": meeting.criador.id, "name": creator_name})
            seen_host_ids.add(meeting.criador.id)
    host_display: str | None = None
    if meeting.meet_host_id:
        if meeting.meet_host:
            host_display = meeting.meet_host.name or meeting.meet_host.username
        else:
            for detail in participant_details:
                if detail["id"] == meeting.meet_host_id:
                    host_display = detail["name"]
                    break
        if (
            meeting.meet_host_id in participant_id_set
            and meeting.meet_host_id not in seen_host_ids
            and host_display
        ):
            host_candidates.append({"id": meeting.meet_host_id, "name": host_display})
            seen_host_ids.add(meeting.meet_host_id)
    return {
        "usernames": participant_usernames,
        "ids": participant_ids,
        "details": participant_details,
        "emails": [email for email in participant_emails if email],
        "host_candidates": host_candidates,
        "host_display": host_display,
        "creator_name": creator_name,
        "creator_username": creator_username,
    }


def serialize_meeting_event(
    meeting: Reuniao,
    now: datetime,
    current_user_id: int,
    is_admin: bool,
    *,
    auto_progress: bool = True,
):
    """Serialize a meeting instance to the structure used by the calendar."""

    start_dt = meeting.inicio.astimezone(CALENDAR_TZ)
    end_dt = meeting.fim.astimezone(CALENDAR_TZ)
    if auto_progress:
        status_changed = _auto_progress_status(meeting, start_dt, end_dt, now)
    else:
        status_changed = False
    status = meeting.status
    label = get_status_label(status)
    color = get_status_color(status)
    participant_meta = _meeting_participant_metadata(meeting)
    normalized_settings = _normalize_meet_settings(meeting.meet_settings)
    can_update_status = is_admin or meeting.criador_id == current_user_id
    # Admins podem editar qualquer reunião; criadores só podem editar AGENDADAS ou ADIADAS
    can_edit = is_admin or (meeting.criador_id == current_user_id and status in EDITABLE_STATUSES)
    can_configure = (
        bool(meeting.meet_link)
        and (is_admin or meeting.criador_id == current_user_id)
        and status in CONFIGURABLE_STATUSES
    )
    # Admins podem excluir qualquer reunião; criadores só podem excluir se podem editar
    can_delete = is_admin or can_edit
    can_edit_pautas = (
        status == ReuniaoStatus.REALIZADA
        and (is_admin or meeting.criador_id == current_user_id)
    )
    event_data = {
        "id": meeting.id,
        "title": meeting.assunto,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "color": color,
        "description": meeting.descricao,
        "pautas": meeting.pautas or "",
        "status": label,
        "status_code": status.value,
        "creator": participant_meta["creator_name"] or participant_meta["creator_username"],
        "creator_username": participant_meta["creator_username"],
        "participants": participant_meta["usernames"],
        "participant_ids": participant_meta["ids"],
        "participant_details": participant_meta["details"],
        "meeting_id": meeting.id,
        "can_edit": can_edit,
        "can_delete": can_delete,
        "can_configure": can_configure,
        "can_update_status": can_update_status,
        "can_edit_pautas": can_edit_pautas,
        "course_id": meeting.course_id,
        "meet_settings": normalized_settings,
        "host_candidates": participant_meta["host_candidates"],
        "meet_host_id": meeting.meet_host_id,
    }
    if meeting.meet_link:
        event_data["meet_link"] = meeting.meet_link
    if participant_meta["host_display"]:
        event_data["meet_host_name"] = participant_meta["host_display"]
    return event_data, status_changed


def _create_additional_meeting(
    additional_date,
    form,
    duration: timedelta,
    selected_users: list[User],
    description: str,
    participant_emails: list[str],
    should_notify: bool,
    user_id: int,
    course_id: int | None,
):
    start_dt = datetime.combine(
        additional_date, form.start_time.data, tzinfo=CALENDAR_TZ
    )
    end_dt = start_dt + duration
    if form.create_meet.data:
        event = create_meet_event(
            form.subject.data,
            start_dt,
            end_dt,
            description,
            participant_emails,
            notify_attendees=should_notify,
        )
        meet_link = event.get("hangoutLink")
    else:
        event = create_event(
            form.subject.data,
            start_dt,
            end_dt,
            description,
            participant_emails,
            notify_attendees=should_notify,
        )
        meet_link = None
    meeting = Reuniao(
        inicio=start_dt,
        fim=end_dt,
        assunto=form.subject.data,
        descricao=form.description.data,
        status=ReuniaoStatus.AGENDADA,
        meet_link=meet_link,
        google_event_id=event["id"],
        criador_id=user_id,
        course_id=course_id,
    )
    meeting.meet_host_id = user_id
    meeting.meet_settings = _normalize_meet_settings()
    db.session.add(meeting)
    db.session.flush()
    for u in selected_users:
        db.session.add(
            ReuniaoParticipante(
                reuniao_id=meeting.id, id_usuario=u.id, username_usuario=u.username
            )
        )
    db.session.commit()
    if meeting.meet_link:
        _queue_meet_preferences_sync(meeting.id, reason="replicated_meeting")
    formatted_date = additional_date.strftime("%d/%m/%Y")
    if meet_link:
        flash(
            Markup(
                f"Reunião replicada para {formatted_date}! "
                f"<a href=\"{meet_link}\" target=\"_blank\">Link do Meet</a>"
            ),
            "success",
        )
    else:
        flash(f"Reunião replicada para {formatted_date}!", "success")
    return meet_link


def create_meeting_and_event(form, raw_events, now, user_id: int):
    """Create meeting adjusting times to avoid conflicts."""

    course_id_value = _parse_course_id(form)
    start_dt = datetime.combine(
        form.date.data, form.start_time.data, tzinfo=CALENDAR_TZ
    )
    end_dt = datetime.combine(
        form.date.data, form.end_time.data, tzinfo=CALENDAR_TZ
    )
    duration = end_dt - start_dt
    search_padding = max(duration, timedelta(hours=1))
    intervals = _collect_intervals(
        raw_events,
        window_start=start_dt - search_padding,
        window_end=end_dt + search_padding,
    )

    adjusted_start, messages = _calculate_adjusted_start(
        start_dt, duration, intervals, now
    )
    if adjusted_start != start_dt:
        adjusted_end = adjusted_start + duration
        form.date.data = adjusted_start.date()
        form.start_time.data = adjusted_start.time()
        form.end_time.data = adjusted_end.time()
        flash(
            f"{' '.join(messages)} Horario ajustado para o proximo horario livre.",
            "warning",
        )
        return False, None

    recorrencia_tipo_field = getattr(form, "recorrencia_tipo", None)
    recorrencia_fim_field = getattr(form, "recorrencia_fim", None)
    recorrencia_dias_semana_field = getattr(form, "recorrencia_dias_semana", None)

    recurrence_dates: list[date] = []
    group_id: str | None = None
    recorrencia_tipo_value = ReuniaoRecorrenciaTipo.NENHUMA
    recorrencia_fim_value: date | None = None
    recorrencia_dias_semana_value: str | None = None

    if (
        recorrencia_tipo_field
        and recorrencia_tipo_field.data
        and recorrencia_tipo_field.data != "NENHUMA"
        and recorrencia_fim_field
        and recorrencia_fim_field.data
    ):
        try:
            recorrencia_tipo_value = ReuniaoRecorrenciaTipo(recorrencia_tipo_field.data)
            recorrencia_fim_value = recorrencia_fim_field.data

            weekdays = None
            if recorrencia_tipo_value == ReuniaoRecorrenciaTipo.SEMANAL and recorrencia_dias_semana_field:
                if recorrencia_dias_semana_field.data:
                    weekdays = [int(d) for d in recorrencia_dias_semana_field.data]
                    recorrencia_dias_semana_value = ','.join(recorrencia_dias_semana_field.data)

            all_dates = generate_recurrence_dates(
                start_date=form.date.data,
                end_date=recorrencia_fim_value,
                recurrence_type=recorrencia_tipo_value,
                weekdays=weekdays,
            )
            recurrence_dates = [d for d in all_dates if d != form.date.data]

            if recurrence_dates:
                group_id = generate_recurrence_group_id()

        except (ValueError, AttributeError) as exc:
            flash(f"Erro ao processar recorrencia: {exc}", "danger")
            return False, None

    selected_users = User.query.filter(User.id.in_(form.participants.data)).all()
    participant_emails = [u.email for u in selected_users if u.email]
    participant_usernames = [u.username for u in selected_users]
    description = _compose_calendar_description(
        form.description.data,
        participant_usernames,
        get_status_label(ReuniaoStatus.AGENDADA),
    )
    notify_attendees = getattr(form, "notify_attendees", None)
    should_notify = bool(notify_attendees.data) if notify_attendees else False
    if form.create_meet.data:
        event = create_meet_event(
            form.subject.data,
            start_dt,
            end_dt,
            description,
            participant_emails,
            notify_attendees=should_notify,
        )
        meet_link = event.get("hangoutLink")
    else:
        event = create_event(
            form.subject.data,
            start_dt,
            end_dt,
            description,
            participant_emails,
            notify_attendees=should_notify,
        )
        meet_link = None

    meeting = Reuniao(
        inicio=start_dt,
        fim=end_dt,
        assunto=form.subject.data,
        descricao=form.description.data,
        status=ReuniaoStatus.AGENDADA,
        meet_link=meet_link,
        google_event_id=event["id"],
        criador_id=user_id,
        course_id=course_id_value,
        recorrencia_tipo=recorrencia_tipo_value,
        recorrencia_fim=recorrencia_fim_value,
        recorrencia_grupo_id=group_id,
        recorrencia_dias_semana=recorrencia_dias_semana_value,
    )
    meeting.meet_host_id = user_id
    meeting.meet_settings = _normalize_meet_settings()
    db.session.add(meeting)
    db.session.flush()
    for user in selected_users:
        db.session.add(
            ReuniaoParticipante(
                reuniao_id=meeting.id,
                id_usuario=user.id,
                username_usuario=user.username,
            )
        )
    db.session.commit()

    meetings_to_sync: list[tuple[int, str]] = []
    if meeting.meet_link:
        meetings_to_sync.append((meeting.id, "create_meeting"))

    additional_meet_link = None

    if recurrence_dates:
        recurrence_count = 0
        recurrence_conflicts = []

        for recurrence_date in recurrence_dates:
            start_dt_recurrent = datetime.combine(
                recurrence_date, form.start_time.data, tzinfo=CALENDAR_TZ
            )
            end_dt_recurrent = datetime.combine(
                recurrence_date, form.end_time.data, tzinfo=CALENDAR_TZ
            )

            # Validate conflicts for each recurrent meeting
            duration = end_dt_recurrent - start_dt_recurrent
            padding = max(duration, timedelta(hours=1))
            recurrent_intervals = _collect_intervals(
                raw_events,
                exclude_meeting_id=meeting.id,
                exclude_event_id=meeting.google_event_id,
                window_start=start_dt_recurrent - padding,
                window_end=end_dt_recurrent + padding,
            )
            adjusted_start_recurrent, conflict_messages = _calculate_adjusted_start(
                start_dt_recurrent, duration, recurrent_intervals, now
            )

            # If there's a conflict, record it but continue creating
            if adjusted_start_recurrent != start_dt_recurrent:
                recurrence_conflicts.append({
                    'date': recurrence_date.strftime('%d/%m/%Y'),
                    'original_time': start_dt_recurrent.strftime('%H:%M'),
                    'suggested_time': adjusted_start_recurrent.strftime('%H:%M'),
                    'messages': conflict_messages
                })
                # Skip creating this conflicting occurrence
                continue

            if form.create_meet.data:
                event_recurrent = create_meet_event(
                    form.subject.data,
                    start_dt_recurrent,
                    end_dt_recurrent,
                    description,
                    participant_emails,
                    notify_attendees=should_notify,
                )
                meet_link_recurrent = event_recurrent.get("hangoutLink")
            else:
                event_recurrent = create_event(
                    form.subject.data,
                    start_dt_recurrent,
                    end_dt_recurrent,
                    description,
                    participant_emails,
                    notify_attendees=should_notify,
                )
                meet_link_recurrent = None

            recurrent_meeting = Reuniao(
                inicio=start_dt_recurrent,
                fim=end_dt_recurrent,
                assunto=form.subject.data,
                descricao=form.description.data,
                status=ReuniaoStatus.AGENDADA,
                meet_link=meet_link_recurrent,
                google_event_id=event_recurrent["id"],
                criador_id=user_id,
                course_id=course_id_value,
                recorrencia_tipo=recorrencia_tipo_value,
                recorrencia_fim=recorrencia_fim_value,
                recorrencia_grupo_id=group_id,
                recorrencia_dias_semana=recorrencia_dias_semana_value,
            )
            recurrent_meeting.meet_host_id = user_id
            recurrent_meeting.meet_settings = _normalize_meet_settings()
            db.session.add(recurrent_meeting)
            db.session.flush()

            for user in selected_users:
                db.session.add(
                    ReuniaoParticipante(
                        reuniao_id=recurrent_meeting.id,
                        id_usuario=user.id,
                        username_usuario=user.username,
                    )
                )

            if meet_link_recurrent:
                meetings_to_sync.append((recurrent_meeting.id, "create_meeting_recurrence"))

            if not meet_link and meet_link_recurrent and not additional_meet_link:
                additional_meet_link = meet_link_recurrent

            recurrence_count += 1

        db.session.commit()

        total_meetings = recurrence_count + 1
        total_requested = len(recurrence_dates) + 1

        # Show warnings about conflicts if any
        if recurrence_conflicts:
            skipped_count = len(recurrence_conflicts)
            conflict_details = "<ul>"
            for conflict in recurrence_conflicts[:5]:  # Show max 5 conflicts
                conflict_details += (
                    f"<li>{conflict['date']} às {conflict['original_time']} "
                    f"(sugerido: {conflict['suggested_time']})</li>"
                )
            if skipped_count > 5:
                conflict_details += f"<li>... e mais {skipped_count - 5} conflitos</li>"
            conflict_details += "</ul>"

            flash(
                Markup(
                    f"⚠️ {skipped_count} de {total_requested} reuniões recorrentes foram puladas "
                    f"devido a conflitos de horário:{conflict_details}"
                ),
                "warning",
            )

        if meet_link:
            flash(
                Markup(
                    f'Serie de {total_meetings} reunioes recorrentes criada com sucesso! '
                    f'<a href="{meet_link}" target="_blank">Link do Meet da primeira reuniao</a>'
                ),
                "success",
            )
        else:
            flash(f"Serie de {total_meetings} reunioes recorrentes criada com sucesso!", "success")
    else:
        if meet_link:
            flash(
                Markup(
                    f'Reuniao criada com sucesso! <a href="{meet_link}" target="_blank">Link do Meet</a>'
                ),
                "success",
            )
        else:
            flash("Reuniao criada com sucesso!", "success")

    invalidate_calendar_cache()

    for meeting_id, reason in meetings_to_sync:
        _queue_meet_preferences_sync(meeting_id, reason=reason)

    if form.create_meet.data and meetings_to_sync:
        flash(
            "Configuracoes do Google Meet serao aplicadas em segundo plano.",
            "info",
        )

    return True, MeetingOperationResult(meeting.id, meet_link or additional_meet_link)
def update_meeting(form, raw_events, now, meeting: Reuniao):
    """Update existing meeting adjusting for conflicts and syncing with Google Calendar.

    Returns a tuple ``(success, result)`` similar to
    :func:`create_meeting_and_event`, where ``result`` is a
    :class:`MeetingOperationResult`.
    """
    course_id_value = _parse_course_id(form)
    start_dt = datetime.combine(
        form.date.data, form.start_time.data, tzinfo=CALENDAR_TZ
    )
    end_dt = datetime.combine(
        form.date.data, form.end_time.data, tzinfo=CALENDAR_TZ
    )
    duration = end_dt - start_dt
    intervals: list[tuple[datetime, datetime]] = []
    for e in raw_events:
        if meeting.google_event_id and e.get("id") == meeting.google_event_id:
            continue
        existing_start = isoparse(
            e["start"].get("dateTime") or e["start"].get("date")
        ).astimezone(CALENDAR_TZ)
        existing_end = isoparse(
            e["end"].get("dateTime") or e["end"].get("date")
        ).astimezone(CALENDAR_TZ)
        intervals.append((existing_start, existing_end))
    for r in Reuniao.query.filter(Reuniao.id != meeting.id).all():
        existing_start = r.inicio.astimezone(CALENDAR_TZ)
        existing_end = r.fim.astimezone(CALENDAR_TZ)
        intervals.append((existing_start, existing_end))
    proposed_start = max(start_dt, now + MIN_GAP)
    messages: list[str] = []
    if proposed_start != start_dt:
        messages.append("Reuniões devem ser agendadas com pelo menos 2 minutos de antecedência.")
    adjusted_start = _next_available(proposed_start, duration, intervals)
    if adjusted_start != proposed_start:
        messages.append("Horário conflita com outra reunião.")
    if adjusted_start != start_dt:
        adjusted_end = adjusted_start + duration
        form.date.data = adjusted_start.date()
        form.start_time.data = adjusted_start.time()
        form.end_time.data = adjusted_end.time()
        flash(f"{' '.join(messages)} Horário ajustado para o próximo horário livre.", "warning")
        return False, None
    meeting.inicio = start_dt
    meeting.fim = end_dt
    meeting.assunto = form.subject.data
    meeting.descricao = form.description.data
    meeting.course_id = course_id_value
    meeting.meet_settings = _normalize_meet_settings(meeting.meet_settings)
    meeting.participantes.clear()
    selected_users = User.query.filter(User.id.in_(form.participants.data)).all()
    for u in selected_users:
        meeting.participantes.append(
            ReuniaoParticipante(id_usuario=u.id, username_usuario=u.username)
        )
    participant_emails = [u.email for u in selected_users if u.email]
    participant_usernames = [u.username for u in selected_users]
    description = _compose_calendar_description(
        form.description.data,
        participant_usernames,
        get_status_label(ReuniaoStatus.AGENDADA),
    )
    notify_field = getattr(form, "notify_attendees", None)
    should_notify = bool(notify_field.data) if notify_field else False
    if meeting.google_event_id:
        previous_meet_link = meeting.meet_link
        if form.create_meet.data:
            if previous_meet_link:
                create_meet_flag = None
            else:
                create_meet_flag = True
        else:
            create_meet_flag = False
        updated_event = update_event(
            meeting.google_event_id,
            form.subject.data,
            start_dt,
            end_dt,
            description,
            participant_emails,
            create_meet=create_meet_flag,
            notify_attendees=should_notify,
        )
        if form.create_meet.data:
            meeting.meet_link = updated_event.get("hangoutLink") or previous_meet_link
        else:
            meeting.meet_link = None
    else:
        if form.create_meet.data:
            updated_event = create_meet_event(
                form.subject.data,
                start_dt,
                end_dt,
                description,
                participant_emails,
                notify_attendees=should_notify,
            )
        else:
            updated_event = create_event(
                form.subject.data,
                start_dt,
                end_dt,
                description,
                participant_emails,
                notify_attendees=should_notify,
            )
        meeting.meet_link = updated_event.get("hangoutLink")
        meeting.google_event_id = updated_event.get("id")
    if meeting.meet_host_id is None:
        meeting.meet_host_id = meeting.criador_id
    db.session.commit()
    if meeting.meet_link:
        _queue_meet_preferences_sync(meeting.id, reason="update_meeting")

    flash("Reunião atualizada com sucesso!", "success")

    # Invalidar cache após atualizar reunião
    invalidate_calendar_cache()

    return True, MeetingOperationResult(meeting.id, meeting.meet_link)


def update_meeting_configuration(
    meeting: Reuniao,
    host_id: int | None,
    settings: dict[str, Any] | None,
) -> tuple[dict[str, bool], User | None, bool | None]:
    """Persist Meet configuration preferences for a meeting."""

    normalized_settings = _normalize_meet_settings(settings)
    meeting.meet_settings = normalized_settings
    host: User | None = None
    participant_ids = {p.id_usuario for p in meeting.participantes}
    if host_id is not None:
        if host_id not in participant_ids:
            raise ValueError("O proprietário da sala precisa ser um participante da reunião.")
        host = User.query.get(host_id)
        if host is None:
            raise ValueError("Participante selecionado não foi encontrado.")
        meeting.meet_host_id = host.id
    else:
        meeting.meet_host_id = None
    db.session.commit()
    if meeting.meet_link:
        _queue_meet_preferences_sync(meeting.id, reason="update_meeting_configuration")
        sync_result = None
    else:
        sync_result = True
    return normalized_settings, host, sync_result


def change_meeting_status(
    meeting: Reuniao,
    new_status: ReuniaoStatus,
    current_user_id: int,
    is_admin: bool,
    *,
    new_start: datetime | None = None,
    new_end: datetime | None = None,
    raw_events: list[dict[str, Any]] | None = None,
    notify_attendees: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Update a meeting status handling optional rescheduling logic."""

    now = now or datetime.now(CALENDAR_TZ)
    if new_status == ReuniaoStatus.ADIADA:
        if new_start is None or new_end is None:
            raise ValueError(
                "Nova data e horários são obrigatórios para marcar a reunião como adiada."
            )
        if new_end <= new_start:
            raise ValueError("O horário de término deve ser posterior ao horário de início.")
        duration = new_end - new_start
        raw_events = raw_events or fetch_raw_events()
        padding = max(duration, timedelta(hours=1))
        intervals = _collect_intervals(
            raw_events,
            exclude_meeting_id=meeting.id,
            exclude_event_id=meeting.google_event_id,
            window_start=new_start - padding,
            window_end=new_end + padding,
        )
        adjusted_start, messages = _calculate_adjusted_start(
            new_start, duration, intervals, now
        )
        if adjusted_start != new_start:
            raise MeetingStatusConflictError(messages)
        meeting.inicio = new_start
        meeting.fim = new_end
    elif new_status == ReuniaoStatus.EM_ANDAMENTO:
        if not (meeting.inicio <= now <= meeting.fim):
            raise ValueError("A reunião precisa estar dentro do horário previsto para ficar em andamento.")
    elif new_status == ReuniaoStatus.AGENDADA and now > meeting.fim:
        raise ValueError(
            "Não é possível marcar como agendada uma reunião cujo horário já foi concluído."
        )
    # Saga Pattern: Save original state for rollback in case of Google API failure
    original_status = meeting.status
    original_inicio = meeting.inicio
    original_fim = meeting.fim
    original_meet_link = meeting.meet_link

    meeting.status = new_status
    participant_meta = _meeting_participant_metadata(meeting)
    participant_emails = list(dict.fromkeys(participant_meta["emails"]))
    description = _compose_calendar_description(
        meeting.descricao,
        participant_meta["usernames"],
        get_status_label(meeting.status),
    )

    # Step 1: Commit to local DB first (source of truth)
    try:
        event_data, _ = serialize_meeting_event(
            meeting,
            now,
            current_user_id,
            is_admin,
            auto_progress=False,
        )
        db.session.commit()
        current_app.logger.debug(f"Meeting {meeting.id} status changed to {new_status} in DB")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Failed to update meeting {meeting.id} in DB: {e}")
        raise

    # Step 2: Sync to Google Calendar (best effort)
    google_sync_failed = False
    try:
        if meeting.google_event_id:
            create_meet_flag = False if new_status == ReuniaoStatus.CANCELADA else None
            notify_flag = notify_attendees if new_status == ReuniaoStatus.ADIADA else False
            updated_event = update_event(
                meeting.google_event_id,
                meeting.assunto,
                meeting.inicio.astimezone(CALENDAR_TZ),
                meeting.fim.astimezone(CALENDAR_TZ),
                description,
                participant_emails,
                create_meet=create_meet_flag,
                notify_attendees=notify_flag,
            )
            if new_status == ReuniaoStatus.CANCELADA or create_meet_flag is False:
                meeting.meet_link = None
            elif updated_event.get("hangoutLink"):
                meeting.meet_link = updated_event.get("hangoutLink")

            # Commit meet_link changes
            db.session.commit()
            current_app.logger.debug(f"Meeting {meeting.id} synced to Google Calendar")
        elif new_status == ReuniaoStatus.CANCELADA:
            meeting.meet_link = None
            db.session.commit()
    except (HttpError, RefreshError) as e:
        google_sync_failed = True
        current_app.logger.warning(
            f"Google Calendar sync failed for meeting {meeting.id}: {e}. "
            f"Local DB updated successfully. Will retry on next sync."
        )
        # Don't rollback DB - local state is source of truth
        # User can manually trigger sync or it will happen on next calendar load
    except Exception as e:
        google_sync_failed = True
        current_app.logger.error(
            f"Unexpected error syncing meeting {meeting.id} to Google: {e}"
        )
        # Don't rollback DB for non-critical sync errors

    # Step 3: Invalidate cache after successful DB update
    invalidate_calendar_cache()

    # Add warning to event_data if Google sync failed
    if google_sync_failed:
        event_data["sync_warning"] = (
            "Reunião atualizada localmente, mas sincronização com Google Calendar falhou. "
            "Será sincronizada automaticamente em breve."
        )

    return event_data


def delete_meeting(meeting: Reuniao) -> bool:
    """Remove meeting from DB and Google Calendar.

    Returns ``True`` when both the local record and the external calendar
    event are deleted successfully. If the Google Calendar removal fails,
    the meeting is left untouched in the database and ``False`` is
    returned so the caller can warn the user.
    """
    if meeting.google_event_id:
        try:
            delete_event(meeting.google_event_id)
        except Exception:
            return False
    db.session.delete(meeting)
    db.session.commit()

    # Invalidar cache após deletar reunião
    invalidate_calendar_cache()

    return True


def combine_events(raw_events, now, current_user_id: int, is_admin: bool):
    """Combine Google and local events updating their status.

    ``is_admin`` indicates if the requester has admin privileges, allowing
    them to delete meetings regardless of status.
    """
    # Cache de resultados processados para evitar reprocessamento
    # Cache separado por usuário e role (admin vs normal)
    cache_key = f"combined_events:{current_user_id}:{is_admin}"
    cached_data = calendar_cache.get(cache_key)

    # Verify cache version to ensure data is still valid after invalidation
    global _combined_cache_version
    with _combined_cache_version_lock:
        current_version = _combined_cache_version

    if cached_data is not None:
        # Check if cached data includes version and if it matches current version
        cached_version = cached_data.get("version") if isinstance(cached_data, dict) else None
        cached_events = cached_data.get("events") if isinstance(cached_data, dict) else cached_data

        # If version matches, return cached events
        if cached_version == current_version and cached_events is not None:
            return cached_events
        # Otherwise, cache is stale, proceed to regenerate

    events: list[dict] = []
    seen_keys: set[tuple[str, str, str]] = set()

    updated = False

    # Intervalo configurável para reduzir dados processados mantendo histórico relevante
    past_window_days = max(int(current_app.config.get("MEETING_CALENDAR_PAST_DAYS", 60)), 0)
    future_window_days = max(int(current_app.config.get("MEETING_CALENDAR_FUTURE_DAYS", 180)), 0)
    two_months_ago = now - timedelta(days=past_window_days)
    three_months_ahead = now + timedelta(days=future_window_days)

    # Use subqueryload para reduzir queries N+1 sem criar JOINs gigantes
    # subqueryload é mais eficiente que joinedload para relacionamentos 1:N
    from sqlalchemy.orm import subqueryload

    meetings = (
        Reuniao.query
        .options(
            subqueryload(Reuniao.participantes).subqueryload(ReuniaoParticipante.usuario),
            subqueryload(Reuniao.criador),
            subqueryload(Reuniao.meet_host)
        )
        .filter(
            Reuniao.inicio >= two_months_ago,
            Reuniao.inicio <= three_months_ahead
        )
        .all()
    )

    # Prioritize locally stored meetings so their metadata (including
    # edit permissions) is preserved. Google events are added later only
    # if they don't match an existing local meeting.

    # Coletar mudanças de status sem fazer commit aqui para evitar bloqueios
    status_updates = []
    for r in meetings:
        event_data, status_changed = serialize_meeting_event(
            r, now, current_user_id, is_admin
        )
        if status_changed:
            status_updates.append((r.id, r.status))
        key = (event_data["title"], event_data["start"], event_data["end"])
        events.append(event_data)
        seen_keys.add(key)

    # Commit apenas se houver mudanças de status, usando UPDATE individual para evitar
    # race conditions. Isso permite que múltiplas requisições atualizem diferentes reuniões
    # sem conflitos, e ignora atualizações que já foram feitas por outra thread.
    if status_updates:
        try:
            # Usar bulk update para melhor performance, mas sem expiration check
            # para evitar locks desnecessários
            for meeting_id, new_status in status_updates:
                # Update direto no BD sem carregar objeto ORM completo
                # Isso evita OptimisticLockException
                db.session.execute(
                    db.update(Reuniao)
                    .where(Reuniao.id == meeting_id)
                    .values(status=new_status)
                )
            db.session.commit()
            current_app.logger.debug(f"Auto-updated status for {len(status_updates)} meetings")
        except Exception as e:
            current_app.logger.warning(f"Failed to commit status updates: {e}")
            db.session.rollback()

    # Cache user emails para evitar múltiplas queries
    all_emails = set()
    for e in raw_events:
        attendee_objs = e.get("attendees", [])
        for a in attendee_objs:
            email = a.get("email")
            if email:
                all_emails.add(email)
        creator_info = e.get("creator") or e.get("organizer", {})
        creator_email = creator_info.get("email")
        if creator_email:
            all_emails.add(creator_email)

    # Buscar todos os usuários de uma vez usando cache
    user_map = get_users_by_email_cached(all_emails) if all_emails else {}

    # Processar eventos do Google
    for e in raw_events:
        start_str = e["start"].get("dateTime") or e["start"].get("date")
        end_str = e["end"].get("dateTime") or e["end"].get("date")
        start_dt = isoparse(start_str).astimezone(CALENDAR_TZ)
        end_dt = isoparse(end_str).astimezone(CALENDAR_TZ)
        key = (e.get("summary", "Sem título"), start_dt.isoformat(), end_dt.isoformat())
        if key in seen_keys:
            continue
        if now < start_dt:
            color = "#ffc107"
            status_label = "Agendada"
        elif start_dt <= now <= end_dt:
            color = "#198754"
            status_label = "Em Andamento"
        else:
            color = "#dc3545"
            status_label = "Realizada"
        attendee_objs = e.get("attendees", [])
        attendees: list[str] = []
        for a in attendee_objs:
            email = a.get("email")
            if email and email in user_map:
                attendees.append(user_map[email])
            elif a.get("displayName"):
                attendees.append(a.get("displayName"))
            elif email:
                attendees.append(email)
        creator_info = e.get("creator") or e.get("organizer", {})
        creator_email = creator_info.get("email")
        if creator_email and creator_email in user_map:
            creator_name = user_map[creator_email]
        elif creator_info.get("displayName"):
            creator_name = creator_info.get("displayName")
        elif creator_email:
            creator_name = creator_email
        else:
            creator_name = ""
        events.append(
            {
                "id": e.get("id"),
                "title": e.get("summary", "Sem título"),
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "description": e.get("description"),
                "pautas": "",
                "meet_link": e.get("hangoutLink"),
                "color": color,
                "status": status_label,
                "status_code": None,
                "participants": attendees,
                "creator": creator_name,
                "can_edit": False,
                "can_delete": False,
                "can_update_status": False,
                "can_configure": False,
                "can_edit_pautas": False,
            }
        )
        seen_keys.add(key)

    # Cachear resultado processado por 90 segundos com versão
    # TTL curto para balance entre performance e freshness
    # Include version to allow proper invalidation across all users
    cached_data_with_version = {
        "version": current_version,
        "events": events
    }
    calendar_cache.set(cache_key, cached_data_with_version, ttl=90)

    return events


