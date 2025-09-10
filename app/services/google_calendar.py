"""Utilities for interacting with the Google Calendar API."""

from datetime import datetime
from uuid import uuid4

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build


def _build_service(credentials_dict: dict):
    """Build a Google Calendar service instance from stored credentials."""
    creds = Credentials(**credentials_dict)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    service = build("calendar", "v3", credentials=creds)
    return service, creds


def list_upcoming_events(credentials_dict: dict, max_results: int = 10):
    """Return upcoming events and refreshed credentials."""
    service, creds = _build_service(credentials_dict)
    now = datetime.utcnow().isoformat() + "Z"
    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return events_result.get("items", []), creds


def create_meet_event(
    credentials_dict: dict,
    summary: str,
    start: datetime,
    end: datetime,
    description: str = "",
    attendees: list[str] | None = None,
):
    """Create a calendar event with a Google Meet link."""
    service, creds = _build_service(credentials_dict)
    event = {
        "summary": summary,
        "start": {
            "dateTime": start.isoformat(),
            "timeZone": "America/Sao_Paulo",
        },
        "end": {
            "dateTime": end.isoformat(),
            "timeZone": "America/Sao_Paulo",
        },
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
            calendarId="primary", body=event, conferenceDataVersion=1
        )
        .execute()
    )
    return created_event, creds

