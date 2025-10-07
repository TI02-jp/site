"""Utilities for interacting with Google Calendar using a service account.

This module centralizes all access to the meeting room agenda. Instead of
relying on each user's OAuth credentials, a service account impersonates the
dedicated room e-mail and performs all calendar operations.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime
from uuid import uuid4
from functools import lru_cache
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from urllib.parse import urlencode

# Scopes required to manage calendar events.
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Environment driven configuration for the meeting room.
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
MEETING_ROOM_EMAIL = os.getenv("GOOGLE_MEETING_ROOM_EMAIL")


def _build_service():
    """Create a Calendar API service authorized as the meeting room account."""
    if not SERVICE_ACCOUNT_FILE or not MEETING_ROOM_EMAIL:
        raise RuntimeError(
            "Service account file and meeting room e-mail must be configured"
        )
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    delegated = creds.with_subject(MEETING_ROOM_EMAIL)
    return build("calendar", "v3", credentials=delegated)


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


def list_upcoming_events(max_results: int = 10):
    """Return upcoming events for the meeting room calendar."""
    service = _build_service()
    now = datetime.now(get_calendar_timezone()).isoformat()
    events_result = (
        service.events()
        .list(
            calendarId=MEETING_ROOM_EMAIL,
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return events_result.get("items", [])


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


def create_event(
    summary: str,
    start: datetime,
    end: datetime,
    description: str = "",
    attendees: list[str] | None = None,
    notify_attendees: bool | None = None,
):
    """Create a calendar event without a Google Meet link."""
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


def build_event_edit_link(
    event_id: str, calendar_id: str, timezone: str | None = None
) -> str:
    """Return the Google Calendar URL used to edit an event.

    Google Calendar expects the ``eventedit`` route to receive the event id and
    calendar id encoded together using URL-safe base64. The encoded identifier
    is placed in the path segment and optional query parameters (such as the
    calendar timezone) are appended afterwards.

    Args:
        event_id: Identifier returned by the Calendar API.
        calendar_id: Calendar that owns the event (usually an e-mail).
        timezone: Optional timezone name to preselect in the Calendar UI.

    Returns:
        A fully qualified URL pointing to the Calendar event configuration
        screen.
    """

    if not event_id or not calendar_id:
        raise ValueError("event_id and calendar_id are required")

    token = base64.urlsafe_b64encode(f"{event_id} {calendar_id}".encode("utf-8")).decode(
        "ascii"
    ).rstrip("=")
    query = {"pli": "1"}
    if timezone:
        query["ctz"] = timezone
    return "https://calendar.google.com/calendar/u/0/r/eventedit/{}{}".format(
        token, f"?{urlencode(query)}" if query else ""
    )


def delete_event(event_id: str):
    """Delete an event from the meeting room calendar."""
    service = _build_service()
    try:
        service.events().delete(calendarId=MEETING_ROOM_EMAIL, eventId=event_id).execute()
    except HttpError:
        # Ignore errors when the event has already been removed.
        pass

