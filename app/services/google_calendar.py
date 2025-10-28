"""Utilities for interacting with Google Calendar using a service account.

This module centralizes all access to the meeting room agenda. Instead of
relying on each user's OAuth credentials, a service account impersonates the
dedicated room e-mail and performs all calendar operations.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from datetime import datetime
from uuid import uuid4
from functools import lru_cache
from zoneinfo import ZoneInfo

from urllib.parse import urlparse

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import socket

_MEET_CODE_PATTERN = re.compile(r"[a-z0-9]{3,}(?:-[a-z0-9]{3,}){2}|[a-z0-9]{10,}")

# Scopes required to manage calendar events.
CALENDAR_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/calendar",
)

# Optional scopes used to update Google Meet spaces when available.
MEET_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/meetings.space.created",
    "https://www.googleapis.com/auth/meetings.space.readonly",
    "https://www.googleapis.com/auth/meetings.space.settings",
)

# Environment driven configuration for the meeting room.
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
MEETING_ROOM_EMAIL = os.getenv("GOOGLE_MEETING_ROOM_EMAIL")


def _build_delegated_credentials(scopes: Sequence[str]):
    """Return delegated service-account credentials for the given scopes."""

    if not SERVICE_ACCOUNT_FILE or not MEETING_ROOM_EMAIL:
        raise RuntimeError(
            "Service account file and meeting room e-mail must be configured"
        )

    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=list(scopes)
    )
    return creds.with_subject(MEETING_ROOM_EMAIL)


@lru_cache(maxsize=2)
def _build_service():
    """Create a Calendar API service authorized as the meeting room account.

    Cached to avoid recreating the service on every request.
    """
    delegated = _build_delegated_credentials(CALENDAR_SCOPES)
    # Set socket timeout to prevent hanging requests (reduced to 3s for faster failover)
    socket.setdefaulttimeout(3)
    return build("calendar", "v3", credentials=delegated)


@lru_cache(maxsize=2)
def _build_meet_service():
    """Create a Meet API service authorized as the meeting room account.

    Cached to avoid recreating the service on every request.
    """
    delegated = _build_delegated_credentials(MEET_SCOPES)
    socket.setdefaulttimeout(3)
    return build("meet", "v2", credentials=delegated)


@lru_cache(maxsize=1)
def get_calendar_timezone() -> ZoneInfo:
    """Return the calendar's configured timezone.

    The result is cached to avoid repeating the API call on every request.
    """
    try:
        service = _build_service()
        tz_name = (
            service.calendars().get(calendarId=MEETING_ROOM_EMAIL).execute().get(
                "timeZone", "UTC"
            )
        )
    except Exception:
        tz_name = "UTC"
    return ZoneInfo(tz_name)


def list_upcoming_events(
    *,
    max_results: int | None = 250,
    time_max: datetime | None = None,
) -> list[dict]:
    """Return upcoming events for the meeting room calendar.

    When ``max_results`` is ``None`` the function keeps paginating (250 per page)
    until it exhausts all pages or reaches ``time_max``. Pagination ensures that
    events muito distantes (ex.: anos à frente) sejam retornados quando o
    calendário já contém muitos compromissos futuros.
    """
    try:
        service = _build_service()
        calendar_tz = get_calendar_timezone()
        now_iso = datetime.now(calendar_tz).isoformat()
        base_params: dict[str, object] = {
            "calendarId": MEETING_ROOM_EMAIL,
            "timeMin": now_iso,
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if time_max is not None:
            base_params["timeMax"] = time_max.astimezone(calendar_tz).isoformat()

        per_page = 250 if max_results is None else max(1, min(max_results, 250))
        events: list[dict] = []
        remaining = max_results
        page_token: str | None = None

        while True:
            params = dict(base_params)
            params["maxResults"] = per_page if remaining is None else max(1, min(per_page, remaining))
            if page_token:
                params["pageToken"] = page_token

            response = service.events().list(**params).execute()
            items = response.get("items", [])
            events.extend(items)

            if remaining is not None:
                remaining -= len(items)
                if remaining <= 0:
                    break

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return events
    except (socket.timeout, socket.error) as e:
        # Return empty list on timeout to prevent blocking the application
        print(f"Google Calendar API timeout: {e}")
        return []
    except Exception as e:
        # Log error but don't crash the application
        print(f"Error fetching calendar events: {e}")
        return []


def _send_updates_flag(notify_attendees: bool | None) -> str:
    """Translate a boolean into the Google Calendar ``sendUpdates`` flag."""

    return "all" if notify_attendees else "none"


def create_meet_event(
    summary: str,
    start: datetime,
    end: datetime,
    description: str = "",
    attendees: list[str] | None = None,
    notify_attendees: bool | None = None,
):
    """Create a calendar event with a Google Meet link."""
    try:
        service = _build_service()
        tz_name = get_calendar_timezone().key
        event = {
            "summary": summary,
            "start": {"dateTime": start.isoformat(), "timeZone": tz_name},
            "end": {"dateTime": end.isoformat(), "timeZone": tz_name},
            "conferenceData": {
                "createRequest": {
                    "requestId": uuid4().hex,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }
        if description:
            event["description"] = description
        if attendees:
            event["attendees"] = [{"email": email} for email in attendees]
        created_event = (
            service.events()
            .insert(
                calendarId=MEETING_ROOM_EMAIL,
                body=event,
                conferenceDataVersion=1,
                sendUpdates=_send_updates_flag(notify_attendees),
            )
            .execute()
        )
        return created_event
    except (socket.timeout, socket.error) as e:
        print(f"Google Calendar API timeout on create_meet_event: {e}")
        raise RuntimeError("Timeout ao criar evento no Google Calendar. Tente novamente.")
    except HttpError as e:
        print(f"Google Calendar API error on create_meet_event: {e}")
        raise RuntimeError(f"Erro ao criar evento no Google Calendar: {e}")
    except Exception as e:
        print(f"Unexpected error on create_meet_event: {e}")
        raise


def create_event(
    summary: str,
    start: datetime,
    end: datetime,
    description: str = "",
    attendees: list[str] | None = None,
    notify_attendees: bool | None = None,
):
    """Create a calendar event without a Google Meet link."""
    try:
        service = _build_service()
        tz_name = get_calendar_timezone().key
        event = {
            "summary": summary,
            "start": {"dateTime": start.isoformat(), "timeZone": tz_name},
            "end": {"dateTime": end.isoformat(), "timeZone": tz_name},
        }
        if description:
            event["description"] = description
        if attendees:
            event["attendees"] = [{"email": email} for email in attendees]
        created_event = (
            service.events()
            .insert(
                calendarId=MEETING_ROOM_EMAIL,
                body=event,
                sendUpdates=_send_updates_flag(notify_attendees),
            )
            .execute()
        )
        return created_event
    except (socket.timeout, socket.error) as e:
        print(f"Google Calendar API timeout on create_event: {e}")
        raise RuntimeError("Timeout ao criar evento no Google Calendar. Tente novamente.")
    except HttpError as e:
        print(f"Google Calendar API error on create_event: {e}")
        raise RuntimeError(f"Erro ao criar evento no Google Calendar: {e}")
    except Exception as e:
        print(f"Unexpected error on create_event: {e}")
        raise


def update_event(
    event_id: str,
    summary: str,
    start: datetime,
    end: datetime,
    description: str = "",
    attendees: list[str] | None = None,
    create_meet: bool | None = None,
    notify_attendees: bool | None = None,
):
    """Update an existing calendar event.

    When ``create_meet`` is ``True``, a Google Meet conference is generated for
    the event. When ``False``, any existing conference data is removed. If
    ``None``, the conference configuration is left unchanged.
    """
    try:
        service = _build_service()
        tz_name = get_calendar_timezone().key
        event = {
            "summary": summary,
            "start": {"dateTime": start.isoformat(), "timeZone": tz_name},
            "end": {"dateTime": end.isoformat(), "timeZone": tz_name},
        }
        if description:
            event["description"] = description
        if attendees is not None:
            event["attendees"] = [{"email": email} for email in attendees]
        kwargs = {}
        if create_meet is True:
            event["conferenceData"] = {
                "createRequest": {
                    "requestId": uuid4().hex,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
            kwargs["conferenceDataVersion"] = 1
        elif create_meet is False:
            event["conferenceData"] = None
            kwargs["conferenceDataVersion"] = 1
        updated_event = (
            service.events()
            .patch(
                calendarId=MEETING_ROOM_EMAIL,
                eventId=event_id,
                body=event,
                sendUpdates=_send_updates_flag(notify_attendees),
                **kwargs,
            )
            .execute()
        )
        return updated_event
    except (socket.timeout, socket.error) as e:
        print(f"Google Calendar API timeout on update_event: {e}")
        raise RuntimeError("Timeout ao atualizar evento no Google Calendar. Tente novamente.")
    except HttpError as e:
        print(f"Google Calendar API error on update_event: {e}")
        raise RuntimeError(f"Erro ao atualizar evento no Google Calendar: {e}")
    except Exception as e:
        print(f"Unexpected error on update_event: {e}")
        raise


def delete_event(event_id: str):
    """Delete an event from the meeting room calendar."""
    service = _build_service()
    try:
        service.events().delete(calendarId=MEETING_ROOM_EMAIL, eventId=event_id).execute()
    except HttpError:
        # Ignore errors when the event has already been removed.
        pass


def _extract_meeting_code(meet_link: str | None) -> str | None:
    """Return the meeting code embedded in a Google Meet URL."""

    if not meet_link:
        return None
    candidate = meet_link.strip()
    if not candidate:
        return None
    if "//" not in candidate:
        candidate = f"https://{candidate}"
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None
    path = parsed.path.strip("/")
    if not path:
        return None
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return None
    if segments[0] == "lookup" and len(segments) > 1:
        code = segments[1]
    else:
        code = segments[-1]
    match = _MEET_CODE_PATTERN.fullmatch(code.lower())
    if not match:
        return None
    return match.group(0)


def _build_space_config_payload(settings: dict[str, bool]) -> tuple[dict, str]:
    """Translate internal Meet flags into the Meet API payload."""

    quick_access = bool(settings.get("quick_access_enabled", True))
    allow_chat = bool(settings.get("allow_chat", True))
    allow_screen_share = bool(settings.get("allow_screen_share", True))
    mute_on_join = bool(settings.get("mute_on_join", False))

    config: dict[str, object] = {}
    update_fields: list[str] = []

    config["accessType"] = "OPEN" if quick_access else "TRUSTED"
    update_fields.append("config.accessType")

    restrictions: dict[str, str] = {
        "chatRestriction": "NO_RESTRICTION" if allow_chat else "HOSTS_ONLY",
        "presentRestriction": "NO_RESTRICTION"
        if allow_screen_share
        else "HOSTS_ONLY",
        "reactionRestriction": "NO_RESTRICTION",
        "defaultJoinAsViewerType": "ON" if mute_on_join else "OFF",
    }
    config["moderationRestrictions"] = restrictions
    update_fields.append("config.moderationRestrictions")

    should_moderate = not allow_chat or not allow_screen_share or mute_on_join
    config["moderation"] = "ON" if should_moderate else "OFF"
    update_fields.append("config.moderation")

    update_mask = ",".join(update_fields)
    return {"config": config}, update_mask


def update_meet_space_preferences(
    meet_link: str, settings: dict[str, bool]
) -> dict | None:
    """Apply Meet configuration flags to the underlying meeting space.

    Returns the API response or None on failure. Logs detailed error information.

    Note: Google Meet API has timing restrictions - settings can only be applied
    BEFORE the first person joins the meeting. After someone joins, the settings
    become locked.
    """
    meeting_code = _extract_meeting_code(meet_link)
    if meeting_code is None:
        print(f"Invalid Google Meet link: {meet_link}")
        raise ValueError("Invalid Google Meet link")

    body, update_mask = _build_space_config_payload(settings)
    space_name = f"spaces/{meeting_code}"

    print(f"Applying Meet settings to {space_name}: {settings}")

    try:
        service = _build_meet_service()
        response = (
            service.spaces()
            .patch(name=space_name, updateMask=update_mask, body=body)
            .execute()
        )
        print(f"Successfully applied Meet settings to {space_name}")
        return response
    except HttpError as e:
        error_details = {
            "status_code": e.resp.status if hasattr(e, 'resp') else None,
            "reason": e.error_details if hasattr(e, 'error_details') else str(e),
            "space": space_name,
            "settings": settings
        }
        print(f"HttpError applying Meet settings: {error_details}")
        raise
    except (socket.timeout, socket.error) as e:
        print(f"Timeout applying Meet settings to {space_name}: {e}")
        raise
    except Exception as e:
        print(f"Unexpected error applying Meet settings to {space_name}: {type(e).__name__}: {e}")
        raise


