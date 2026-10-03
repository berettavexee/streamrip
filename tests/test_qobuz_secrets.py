"""Credentials must not reach Qobuz log lines or error messages."""

import logging
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from streamrip.client.qobuz import QobuzClient
from streamrip.config import Config
from streamrip.exceptions import AuthenticationError, InvalidAppSecretError

PASSWORD = "hunter2-PASSWORD-xyz"
TOKEN = "S3CRET-user-auth-token-0123456789"
APP_SECRET = "abcdef0123456789APPSECRETabcdef01"


def _client(use_auth_token=False):
    config = Config.defaults()
    config.session.downloads.requests_per_minute = 0
    q = config.session.qobuz
    q.use_auth_token = use_auth_token
    q.email_or_userid = "user@example.com"
    q.password_or_token = TOKEN if use_auth_token else PASSWORD
    q.app_id = "123456789"
    q.secrets = [APP_SECRET]
    client = QobuzClient(config)
    client.session = MagicMock()
    client.session.headers = {}
    return client


def _respond(client, status, json=None, json_error=None):
    """Make session.get answer every request with one response."""
    resp = MagicMock(status=status)
    resp.json = AsyncMock(return_value=json, side_effect=json_error)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    client.session.get = MagicMock(return_value=ctx)


def _assert_no_secret(text):
    for secret in (PASSWORD, TOKEN, APP_SECRET):
        assert secret not in text


@pytest.mark.parametrize("use_auth_token", [False, True])
async def test_rejected_login_leaks_nothing(caplog, use_auth_token):
    """The params dict (password or token) used to be interpolated into the
    exception, and logged along with the login response."""
    client = _client(use_auth_token)
    _respond(client, 401, {"message": "Invalid username/email and password"})

    with caplog.at_level(logging.DEBUG, logger="streamrip"):
        with pytest.raises(AuthenticationError) as excinfo:
            await client._attempt_login()

    _assert_no_secret(str(excinfo.value))
    _assert_no_secret(caplog.text)
    assert "***" in caplog.text  # the request was logged, masked


async def test_login_on_an_html_error_page_leaks_nothing(caplog):
    """aiohttp's ContentTypeError quotes the full URL; on user/login the
    password is in its query string."""
    client = _client()
    request_info = MagicMock(
        real_url=f"https://www.qobuz.com/api.json/0.2/user/login?password={PASSWORD}"
    )
    error = aiohttp.ContentTypeError(
        request_info, (), message=f"unexpected mimetype at ...password={PASSWORD}"
    )
    _respond(client, 502, json_error=error)

    with caplog.at_level(logging.DEBUG, logger="streamrip"):
        with pytest.raises(AuthenticationError, match="HTTP 502") as excinfo:
            await client._attempt_login()

    _assert_no_secret(str(excinfo.value))
    _assert_no_secret(caplog.text)


async def test_file_url_signature_does_not_log_the_app_secret(caplog):
    """The signature preimage ends with the app secret; it was logged raw."""
    client = _client()
    _respond(client, 200, {"url": "https://x"})

    with caplog.at_level(logging.DEBUG, logger="streamrip"):
        await client._request_file_url("19512574", 4, APP_SECRET)

    _assert_no_secret(caplog.text)


async def test_invalid_secrets_error_does_not_list_them():
    client = _client()
    client._test_secret = AsyncMock(return_value=None)

    with pytest.raises(InvalidAppSecretError) as excinfo:
        await client._get_valid_secret([APP_SECRET])

    _assert_no_secret(str(excinfo.value))
