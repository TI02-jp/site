import logging
import time
from collections import deque
from datetime import date, datetime
from typing import Any, Dict, Generator, Iterable, Optional
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)


def to_query_date(d: Optional[Any]) -> Optional[str]:
    """Serialize a date/datetime/string to YYYY-MM-DD."""
    if d is None:
        return None
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y-%m-%d")
    return str(d)


def paginate(getter, *args, **kwargs) -> Generator:
    """Paginate over API responses using the `Pagina` parameter."""
    page = 1
    while True:
        kwargs['page'] = page
        data = getter(*args, **kwargs)
        if not data:
            break
        yield data
        page += 1


class AcessoriasClient:
    """HTTP client for the Acessorias API."""

    def __init__(self, base_url: str, token: str, *, timeout: int = 15) -> None:
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        self.timeout = timeout
        # rate limiting
        self._requests: deque = deque()
        self._max_per_minute = 90

    # ------------------------------------------------------------------
    # internal helpers
    def _respect_rate_limit(self) -> None:
        now = time.time()
        while self._requests and now - self._requests[0] > 60:
            self._requests.popleft()
        if len(self._requests) >= self._max_per_minute:
            sleep = 60 - (now - self._requests[0]) + 0.01
            time.sleep(max(sleep, 0))
        self._requests.append(time.time())

    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        url = urljoin(self.base_url + '/', endpoint.lstrip('/'))
        retries = 3
        backoff = 0.5
        for attempt in range(retries):
            self._respect_rate_limit()
            start = time.time()
            resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
            latency = time.time() - start
            request_id = resp.headers.get('X-Request-Id')
            logger.info(
                "acessorias_request",
                extra={
                    "request_id": request_id,
                    "endpoint": endpoint,
                    "status": resp.status_code,
                    "latency": latency,
                },
            )
            if resp.status_code in {429} | set(range(500, 600)):
                if attempt < retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
            return resp
        return resp

    # ------------------------------------------------------------------
    # public API
    def get_company(self, identifier: str, *, include_obligations: bool = True, page: int = 1) -> Optional[Dict]:
        params = {"Pagina": page}
        if include_obligations:
            params["obligations"] = ""
        resp = self._request("GET", f"/companies/{identifier}/", params=params)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def list_invoices(
        self,
        identifier: str,
        *,
        page: int = 1,
        vo_ini: Optional[Any] = None,
        vo_fim: Optional[Any] = None,
        v_ini: Optional[Any] = None,
        v_fim: Optional[Any] = None,
        pgto_ini: Optional[Any] = None,
        pgto_fim: Optional[Any] = None,
        dtcria_ini: Optional[Any] = None,
        dtcria_fim: Optional[Any] = None,
        dtlast: Optional[Any] = None,
        status: Optional[str] = None,
        with_items: bool = False,
        boleto_id: Optional[str] = None,
    ) -> Any:
        params: Dict[str, Any] = {"Pagina": page}
        mapping = {
            "vo_ini": vo_ini,
            "vo_fim": vo_fim,
            "v_ini": v_ini,
            "v_fim": v_fim,
            "pgto_ini": pgto_ini,
            "pgto_fim": pgto_fim,
            "dtcria_ini": dtcria_ini,
            "dtcria_fim": dtcria_fim,
            "dtlast": dtlast,
        }
        for key, value in mapping.items():
            if value is not None:
                params[key] = to_query_date(value)
        if status:
            params['status'] = status
        if with_items:
            params['bltFat'] = 'S'
        if boleto_id:
            params['boleto_id'] = boleto_id
        resp = self._request("GET", f"/invoices/{identifier}/", params=params)
        resp.raise_for_status()
        return resp.json()

    def upsert_company(self, payload: Dict) -> Dict:
        resp = self._request("POST", "/companies", json=payload)
        resp.raise_for_status()
        return resp.json()

    def upsert_contact(self, identifier: str, payload: Dict) -> Dict:
        resp = self._request("POST", f"/contacts/{identifier}", json=payload)
        resp.raise_for_status()
        return resp.json()
