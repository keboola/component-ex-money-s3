import time

import pytest
from keboola.component.exceptions import UserException

from auth import TokenProvider


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def test_fetches_and_caches_token(mocker):
    post = mocker.patch(
        "auth.requests.post",
        return_value=_Resp(200, {"access_token": "TOK", "expires_in": 3600}),
    )
    tp = TokenProvider("http://h:28085", "APPID", "cid", "sec", "s3api")
    assert tp.get_token() == "TOK"
    assert tp.get_token() == "TOK"  # cached
    post.assert_called_once()
    _, kwargs = post.call_args
    assert kwargs["params"] == {"AppId": "APPID"}
    assert kwargs["data"]["grant_type"] == "client_credentials"
    assert kwargs["data"]["client_secret"] == "sec"


def test_refetches_when_expired(mocker):
    mocker.patch("auth.requests.post", return_value=_Resp(200, {"access_token": "T2", "expires_in": 1}))
    tp = TokenProvider("http://h", "A", "c", "s")
    tp.get_token()
    tp._expires_at = time.time() - 1  # force expiry
    assert tp.get_token() == "T2"
    assert tp._fetch_count == 2


def test_error_raises_userexception(mocker):
    mocker.patch("auth.requests.post", return_value=_Resp(400, {"error": "invalid_client"}))
    with pytest.raises(UserException, match="token"):
        TokenProvider("http://h", "A", "c", "s").get_token()
