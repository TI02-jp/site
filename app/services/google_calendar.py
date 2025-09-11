"""Utilities for interacting with Google Calendar using a service account.

This module centralizes all access to the meeting room agenda. Instead of
relying on each user's OAuth credentials, a service account impersonates the
dedicated room e-mail and performs all calendar operations.
"""

from __future__ import annotations

import os
from datetime import datetime
from uuid import uuid4

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.models.tables import BRASILIA_TZ

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


def list_upcoming_events(max_results: int = 10):
    """Return upcoming events for the meeting room calendar."""
    service = _build_service()
    now = datetime.now(BRASILIA_TZ).isoformat()
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


def create_meet_event(
    summary: str,
    start: datetime,
    end: datetime,
    description: str = "",
    attendees: list[str] | None = None,
):
    """Create a calendar event with a Google Meet link."""
    service = _build_service()
    event = {
        "summary": summary,
        "start": {"dateTime": start.isoformat(), "timeZone": "America/Brasilia"},
        "end": {"dateTime": end.isoformat(), "timeZone": "America/Brasilia"},
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
            calendarId=MEETING_ROOM_EMAIL, body=event, conferenceDataVersion=1
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
):
    """Create a calendar event without a Google Meet link."""
    service = _build_service()
    event = {
        "summary": summary,
        "start": {"dateTime": start.isoformat(), "timeZone": "America/Brasilia"},
        "end": {"dateTime": end.isoformat(), "timeZone": "America/Brasilia"},
    }
    if description:
        event["description"] = description
    if attendees:
        event["attendees"] = [{"email": email} for email in attendees]
    created_event = (
        service.events().insert(calendarId=MEETING_ROOM_EMAIL, body=event).execute()
    )
    return created_event


def update_event(
    event_id: str,
    summary: str,
    start: datetime,
    end: datetime,
    description: str = "",
    attendees: list[str] | None = None,
):
    """Update an existing calendar event."""
    service = _build_service()
    event = {
        "summary": summary,
        "start": {"dateTime": start.isoformat(), "timeZone": "America/Brasilia"},
        "end": {"dateTime": end.isoformat(), "timeZone": "America/Brasilia"},
    }
    if description:
        event["description"] = description
    if attendees is not None:
        event["attendees"] = [{"email": email} for email in attendees]
    updated_event = (
        service.events()
        .patch(calendarId=MEETING_ROOM_EMAIL, eventId=event_id, body=event)
        .execute()
    )
    return updated_event


def delete_event(event_id: str):
    """Delete an event from the meeting room calendar."""
    service = _build_service()
    try:
        service.events().delete(calendarId=MEETING_ROOM_EMAIL, eventId=event_id).execute()
    except HttpError:
        # Ignore errors when the event has already been removed.
        pass

