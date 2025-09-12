"""Simple integration with Banco do Brasil's account statement API."""

import os
from typing import Any, Dict

import requests

BB_BASE_URL = os.getenv("BB_BASE_URL", "https://api.bb.com.br")
BB_CLIENT_ID = os.getenv("BB_CLIENT_ID")
BB_CLIENT_SECRET = os.getenv("BB_CLIENT_SECRET")


def get_access_token() -> str | None:
    """Retrieve OAuth2 token for Banco do Brasil API.

    Returns ``None`` if credentials are missing or the request fails.
    """
    if not BB_CLIENT_ID or not BB_CLIENT_SECRET:
        return None
    token_url = f"{BB_BASE_URL}/oauth/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": BB_CLIENT_ID,
        "client_secret": BB_CLIENT_SECRET,
    }
    try:
        resp = requests.post(
            token_url,
            data=data,
            timeout=20,
            proxies={"http": None, "https": None},
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except Exception:
        return None


def get_statement(
    account: str,
    agency: str,
    start_date: str,
    end_date: str,
) -> Dict[str, Any] | None:
    """Fetch account statements for the given parameters.

    ``start_date`` and ``end_date`` must be in ``YYYY-MM-DD`` format.
    Returns a dict with the API response or ``None`` if the request fails.
    """
    token = get_access_token()
    if not token:
        return None
    url = f"{BB_BASE_URL}/conta/v2/contas/{agency}/{account}/extratos"
    params = {"dataInicio": start_date, "dataFim": end_date}
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        resp = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=20,
            proxies={"http": None, "https": None},
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        return None
    return None

