import time

import requests
from keboola.component.exceptions import UserException

_EXPIRY_SKEW_SECONDS = 60


class TokenProvider:
    """Obtains and caches an OAuth2 client-credentials Bearer token."""

    def __init__(self, base_url: str, app_id: str, client_id: str, client_secret: str, scope: str = "s3api"):
        self._token_url = f"{base_url.rstrip('/')}/connect/token"
        self._app_id = app_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._fetch_count = 0

    def get_token(self) -> str:
        if self._token and time.time() < self._expires_at - _EXPIRY_SKEW_SECONDS:
            return self._token
        self._fetch()
        return self._token  # type: ignore[return-value]

    def _fetch(self) -> None:
        resp = requests.post(
            self._token_url,
            params={"AppId": self._app_id},
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": self._scope,
            },
            timeout=30,
        )
        self._fetch_count += 1
        if resp.status_code != 200:
            raise UserException(f"Failed to obtain access token ({resp.status_code}): {resp.text}")
        body = resp.json()
        self._token = body["access_token"]
        self._expires_at = time.time() + float(body.get("expires_in", 3600))
