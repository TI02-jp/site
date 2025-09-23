"""Helpers for interacting with Google Drive video folders."""

from __future__ import annotations

import io
import os
import re
from datetime import datetime
from functools import lru_cache
from typing import Iterable
from urllib.parse import parse_qs, urlparse

from flask import current_app
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Precompiled regex patterns for common Google Drive URL structures.
_FOLDER_PATTERN = re.compile(r"/folders/([a-zA-Z0-9_-]+)")
_FILE_PATTERN = re.compile(r"/file/d/([a-zA-Z0-9_-]+)")


class GoogleDriveVideoError(RuntimeError):
    """Base exception raised for video related Drive failures."""


@lru_cache(maxsize=1)
def _get_drive_service():
    """Return an authenticated Google Drive service instance."""

    service_account_file = current_app.config.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not service_account_file or not os.path.exists(service_account_file):
        raise GoogleDriveVideoError(
            "Credenciais do Google Drive não configuradas. "
            "Defina GOOGLE_SERVICE_ACCOUNT_FILE para habilitar a biblioteca de vídeos."
        )
    credentials = service_account.Credentials.from_service_account_file(
        service_account_file, scopes=DRIVE_SCOPES
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _extract_from_query(url: str, key: str) -> str | None:
    """Return the value of ``key`` from the URL query string, if present."""

    parsed = urlparse(url)
    if not parsed.query:
        return None
    values = parse_qs(parsed.query).get(key)
    if not values:
        return None
    return values[0]


def extract_drive_folder_id(value: str) -> str:
    """Normalize a Drive folder link or identifier into its raw ID."""

    candidate = (value or "").strip()
    if not candidate:
        raise GoogleDriveVideoError("Informe um link ou ID de pasta do Google Drive.")
    if "drive.google.com" not in candidate:
        return candidate
    match = _FOLDER_PATTERN.search(candidate)
    if match:
        return match.group(1)
    query_id = _extract_from_query(candidate, "id")
    if query_id:
        return query_id
    raise GoogleDriveVideoError("Não foi possível identificar o ID da pasta do Google Drive informado.")


def extract_drive_file_id(value: str) -> str:
    """Normalize a Drive file link or identifier into its raw ID."""

    candidate = (value or "").strip()
    if not candidate:
        raise GoogleDriveVideoError("Informe um link ou ID de arquivo do Google Drive.")
    if "drive.google.com" not in candidate:
        return candidate
    match = _FILE_PATTERN.search(candidate)
    if match:
        return match.group(1)
    query_id = _extract_from_query(candidate, "id")
    if query_id:
        return query_id
    raise GoogleDriveVideoError("Não foi possível identificar o ID do arquivo do Google Drive informado.")


def ensure_drive_folder(folder_id: str) -> dict:
    """Validate that ``folder_id`` exists and corresponds to a Drive folder."""

    service = _get_drive_service()
    try:
        metadata = (
            service.files()
            .get(
                fileId=folder_id,
                fields="id, name, mimeType, webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
    except HttpError as exc:
        raise GoogleDriveVideoError("Não foi possível acessar a pasta no Google Drive.") from exc
    if metadata.get("mimeType") != "application/vnd.google-apps.folder":
        raise GoogleDriveVideoError("O link informado não corresponde a uma pasta do Google Drive.")
    return metadata


def list_drive_videos(folder_id: str) -> list[dict]:
    """Return metadata for all video files stored inside ``folder_id``."""

    service = _get_drive_service()
    files: list[dict] = []
    page_token: str | None = None
    query = f"'{folder_id}' in parents and mimeType contains 'video/' and trashed = false"
    fields = (
        "nextPageToken, files(id, name, mimeType, size, modifiedTime, "
        "thumbnailLink, description, videoMediaMetadata(durationMillis, width, height))"
    )
    while True:
        try:
            response = (
                service.files()
                .list(
                    q=query,
                    pageToken=page_token,
                    fields=fields,
                    orderBy="name",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
        except HttpError as exc:
            raise GoogleDriveVideoError(
                "Não foi possível listar os vídeos desta pasta no Google Drive."
            ) from exc
        files.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return files


def download_drive_file(file_id: str) -> io.BytesIO:
    """Download a Drive file into memory and return a ``BytesIO`` handle."""

    service = _get_drive_service()
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request, chunksize=2 * 1024 * 1024)
    done = False
    try:
        while not done:
            _, done = downloader.next_chunk()
    except HttpError as exc:
        raise GoogleDriveVideoError("Falha ao baixar o arquivo de vídeo do Google Drive.") from exc
    buffer.seek(0)
    return buffer


def parse_google_datetime(payload: str | None) -> datetime | None:
    """Convert an ISO datetime string returned by Drive into ``datetime``."""

    if not payload:
        return None
    try:
        return datetime.fromisoformat(payload.replace("Z", "+00:00"))
    except ValueError:
        return None


def coerce_int(value: str | int | None) -> int | None:
    """Convert Drive numeric metadata into ``int`` when possible."""

    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def batch_extract_file_ids(values: Iterable[str]) -> list[str]:
    """Helper to normalize multiple Drive URLs or IDs."""

    result: list[str] = []
    for value in values:
        try:
            result.append(extract_drive_file_id(value))
        except GoogleDriveVideoError:
            continue
    return result
