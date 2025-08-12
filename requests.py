class HTTPError(Exception):
    pass


class Response:
    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data or {}
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPError(f"HTTP {self.status_code}")


class Session:
    def __init__(self):
        self.headers = {}

    def request(self, method, url, timeout=None, **kwargs):
        raise HTTPError("Network not implemented in stub")
