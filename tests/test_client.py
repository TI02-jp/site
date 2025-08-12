import requests
import requests
import requests
from integrations.acessorias.client import AcessoriasClient, paginate


class DummyResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}
        self.headers = {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


def test_get_company_handles_404(monkeypatch):
    client = AcessoriasClient('http://base', 'tok')
    resp = DummyResponse(404)
    monkeypatch.setattr(client.session, 'request', lambda *a, **k: resp)
    assert client.get_company('123') is None


def test_retry_on_429(monkeypatch):
    client = AcessoriasClient('http://base', 'tok')
    responses = [DummyResponse(429), DummyResponse(200, {'ok': True})]

    def req(*a, **k):
        return responses.pop(0)

    monkeypatch.setattr(client.session, 'request', req)
    assert client.get_company('1') == {'ok': True}
    assert responses == []


def test_paginate_helper():
    calls = []

    def getter(*, page):
        calls.append(page)
        return [] if page > 2 else [page]

    pages = list(paginate(getter))
    assert calls == [1, 2, 3]
    assert pages == [[1], [2]]
