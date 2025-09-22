"""Utilities to fetch Reforma Tributária videos from Google Drive."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from flask import current_app
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


@dataclass(frozen=True, slots=True)
class DriveVideo:
    """Representation of a Google Drive video file."""

    id: str
    name: str
    mime_type: str
    thumbnail_link: str | None = None
    duration_millis: int | None = None

    @property
    def preview_url(self) -> str:
        """Return the embeddable preview URL for the file."""

        return f"https://drive.google.com/file/d/{self.id}/preview"

    @property
    def download_url(self) -> str:
        """Return a direct download URL for the file."""

        return f"https://drive.google.com/uc?id={self.id}&export=download"

    @property
    def formatted_duration(self) -> str | None:
        """Return the duration formatted as ``Hh MMmin`` or ``MMmin SSs``."""

        if not self.duration_millis:
            return None
        total_seconds = int(self.duration_millis // 1000)
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h {minutes:02d}min"
        if minutes:
            return f"{minutes}min {seconds:02d}s"
        return f"{seconds}s"


def _load_service_account_credentials():
    """Return service account credentials configured for Drive access."""

    info_raw = current_app.config.get("GOOGLE_SERVICE_ACCOUNT_INFO")
    file_path = current_app.config.get("GOOGLE_SERVICE_ACCOUNT_FILE")

    credentials = None
    if info_raw:
        try:
            info = json.loads(info_raw) if isinstance(info_raw, str) else info_raw
            credentials = service_account.Credentials.from_service_account_info(
                info, scopes=SCOPES
            )
        except (json.JSONDecodeError, ValueError) as exc:
            current_app.logger.error(
                "Falha ao carregar credenciais do serviço a partir da variável de ambiente: %s",
                exc,
            )
            return None
    elif file_path:
        try:
            credentials = service_account.Credentials.from_service_account_file(
                file_path, scopes=SCOPES
            )
        except Exception as exc:  # pragma: no cover - defensive branch
            current_app.logger.error(
                "Não foi possível abrir o arquivo de credenciais %s: %s", file_path, exc
            )
            return None
    else:
        return None

    subject = current_app.config.get("GOOGLE_SERVICE_ACCOUNT_SUBJECT")
    if subject:
        credentials = credentials.with_subject(subject)

    return credentials


def _build_drive_service():
    """Return an authenticated Google Drive service client."""

    credentials = _load_service_account_credentials()
    if not credentials:
        return None
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def fetch_drive_videos(folder_id: str) -> tuple[list[DriveVideo], str | None]:
    """Return the list of videos for ``folder_id`` and an optional error message."""

    if not folder_id:
        return [], "A pasta de vídeos não está configurada."

    drive_service = _build_drive_service()
    if not drive_service:
        return [], "Integração com o Google Drive não está configurada."

    query = (
        f"'{folder_id}' in parents and trashed = false and "
        "mimeType contains 'video/'"
    )

    try:
        response = (
            drive_service.files()
            .list(
                q=query,
                orderBy="name",
                fields="files(id,name,mimeType,thumbnailLink,videoMediaMetadata(durationMillis))",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                pageSize=1000,
            )
            .execute()
        )
    except HttpError as exc:
        current_app.logger.error("Erro ao consultar vídeos no Google Drive: %s", exc)
        return [], "Não foi possível carregar os vídeos do Google Drive no momento."

    files: Iterable[dict] = response.get("files", [])
    videos = [
        DriveVideo(
            id=file_data["id"],
            name=file_data.get("name", "Vídeo sem título"),
            mime_type=file_data.get("mimeType", ""),
            thumbnail_link=file_data.get("thumbnailLink"),
            duration_millis=(
                (file_data.get("videoMediaMetadata") or {}).get("durationMillis")
            ),
        )
        for file_data in files
    ]

    videos.sort(key=lambda item: item.name.lower())
    return videos, None
