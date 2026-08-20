"""Unit tests for streamrip.client.deezer_pipe (DeezerPipeClient + helpers)."""

import asyncio
import base64
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from streamrip.client.deezer_pipe import (
    _FALLBACK_TTL,
    _PIPE_URL,
    _REFRESH_MARGIN,
    _RENEW_URL,
    DeezerPipeClient,
    _jwt_exp_as_monotonic,
)

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_jwt(exp_unix: float | None) -> str:
    """Build a minimal fake JWT with the given exp claim."""
    header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=").decode()
    payload_data: dict = {}
    if exp_unix is not None:
        payload_data["exp"] = exp_unix
    payload = (
        base64.urlsafe_b64encode(json.dumps(payload_data).encode())
        .rstrip(b"=")
        .decode()
    )
    return f"{header}.{payload}.fakesig"


def _client(arl: str = "testarl") -> tuple[DeezerPipeClient, MagicMock]:
    """Return a DeezerPipeClient paired with a mock aiohttp session."""
    session = MagicMock()
    return DeezerPipeClient(arl, session), session


# ── _jwt_exp_as_monotonic ──────────────────────────────────────────────────────


class TestJwtExpAsMonotonic:
    def test_valid_future_exp(self):
        exp_unix = time.time() + 360.0
        token = _make_jwt(exp_unix)
        result = _jwt_exp_as_monotonic(token)
        assert result is not None
        assert abs(result - (time.monotonic() + 360.0)) < 2.0

    def test_already_expired(self):
        exp_unix = time.time() - 10.0
        token = _make_jwt(exp_unix)
        result = _jwt_exp_as_monotonic(token)
        assert result is not None
        assert result < time.monotonic()

    def test_no_exp_claim(self):
        token = _make_jwt(None)
        assert _jwt_exp_as_monotonic(token) is None

    def test_not_a_jwt(self):
        assert _jwt_exp_as_monotonic("not.a.real.jwt.at.all") is None
        assert _jwt_exp_as_monotonic("onlytwoparts.here") is None
        assert _jwt_exp_as_monotonic("") is None

    def test_malformed_base64_payload(self):
        assert _jwt_exp_as_monotonic("header.!!!notbase64!!!.sig") is None


# ── DeezerPipeClient._refresh_jwt ─────────────────────────────────────────────


class TestRefreshJwt:
    def test_stores_jwt_and_expiry_on_success(self):
        client, session = _client()
        exp_unix = time.time() + 360.0
        fake_jwt = _make_jwt(exp_unix)

        mock_resp = AsyncMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={"jwt": fake_jwt})
        session.post = MagicMock(return_value=mock_resp)

        asyncio.run(client._refresh_jwt())

        assert client._jwt == fake_jwt
        assert client._jwt_expires_at > time.monotonic()

    def test_uses_fallback_ttl_when_exp_undecidable(self):
        client, session = _client()
        # JWT with no exp claim → _jwt_exp_as_monotonic returns None
        fake_jwt = _make_jwt(None)

        mock_resp = AsyncMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={"jwt": fake_jwt})
        session.post = MagicMock(return_value=mock_resp)

        before = time.monotonic()
        asyncio.run(client._refresh_jwt())
        after = time.monotonic()

        expected = before + _FALLBACK_TTL
        assert abs(client._jwt_expires_at - expected) < (after - before) + 0.1

    def test_raises_when_no_jwt_in_response(self):
        client, session = _client()

        mock_resp = AsyncMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={"error": "bad_arl"})
        session.post = MagicMock(return_value=mock_resp)

        with pytest.raises(RuntimeError, match="no JWT"):
            asyncio.run(client._refresh_jwt())

    def test_posts_to_correct_url_with_arl_as_refresh_token(self):
        client, session = _client(arl="my_secret_arl")
        fake_jwt = _make_jwt(time.time() + 360)

        mock_resp = AsyncMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={"jwt": fake_jwt})
        session.post = MagicMock(return_value=mock_resp)

        asyncio.run(client._refresh_jwt())

        call_kwargs = session.post.call_args
        assert call_kwargs.args[0] == _RENEW_URL
        assert call_kwargs.kwargs["json"] == {"refresh_token": "my_secret_arl"}
        assert "i" in call_kwargs.kwargs["params"]


# ── DeezerPipeClient._get_jwt ──────────────────────────────────────────────────


class TestGetJwt:
    def _fresh_client_with_jwt(self) -> DeezerPipeClient:
        """Return a client that already holds a fresh JWT (no network call needed)."""
        client, _ = _client()
        client._jwt = _make_jwt(time.time() + 360)
        client._jwt_expires_at = time.monotonic() + 360.0
        return client

    def test_returns_existing_jwt_when_fresh(self):
        client = self._fresh_client_with_jwt()
        jwt = asyncio.run(client._get_jwt())
        assert jwt == client._jwt

    def test_refreshes_when_within_margin(self):
        client, session = _client()
        old_jwt = _make_jwt(time.time() + 10)
        new_jwt = _make_jwt(time.time() + 360)
        client._jwt = old_jwt
        # Expiry is within _REFRESH_MARGIN → should trigger refresh
        client._jwt_expires_at = time.monotonic() + _REFRESH_MARGIN - 1

        mock_resp = AsyncMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={"jwt": new_jwt})
        session.post = MagicMock(return_value=mock_resp)

        jwt = asyncio.run(client._get_jwt())
        assert jwt == new_jwt
        assert client._jwt == new_jwt

    def test_refreshes_when_no_jwt(self):
        client, session = _client()
        new_jwt = _make_jwt(time.time() + 360)

        mock_resp = AsyncMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={"jwt": new_jwt})
        session.post = MagicMock(return_value=mock_resp)

        jwt = asyncio.run(client._get_jwt())
        assert jwt == new_jwt

    def test_invalidate_forces_refresh(self):
        client = self._fresh_client_with_jwt()
        old_jwt = client._jwt
        new_jwt = _make_jwt(time.time() + 360)

        mock_resp = AsyncMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={"jwt": new_jwt})
        client._session.post = MagicMock(return_value=mock_resp)

        client.invalidate()
        jwt = asyncio.run(client._get_jwt())
        assert jwt != old_jwt
        assert jwt == new_jwt


# ── DeezerPipeClient.query ─────────────────────────────────────────────────────


class TestQuery:
    def _client_with_fresh_jwt(self) -> tuple[DeezerPipeClient, MagicMock]:
        client, session = _client()
        client._jwt = _make_jwt(time.time() + 360)
        client._jwt_expires_at = time.monotonic() + 360.0
        return client, session

    def _mock_post(self, session: MagicMock, status: int, body: dict) -> None:
        mock_resp = AsyncMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.status = status
        if status >= 400:
            import aiohttp

            mock_resp.raise_for_status = MagicMock(
                side_effect=aiohttp.ClientResponseError(
                    request_info=MagicMock(), history=(), status=status
                )
            )
        else:
            mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value=body)
        session.post = MagicMock(return_value=mock_resp)

    def test_posts_to_pipe_url_with_bearer(self):
        client, session = self._client_with_fresh_jwt()
        self._mock_post(session, 200, {"data": {"track": {"id": "123"}}})

        result = asyncio.run(client.query('query Q { track(trackId: "1") { id } }'))

        assert result == {"data": {"track": {"id": "123"}}}
        call = session.post.call_args
        assert call.args[0] == _PIPE_URL
        assert call.kwargs["headers"]["Authorization"].startswith("Bearer ")

    def test_passes_variables(self):
        client, session = self._client_with_fresh_jwt()
        self._mock_post(session, 200, {"data": {}})

        asyncio.run(
            client.query(
                "query Q($id: String!) { track(trackId: $id) { id } }", {"id": "42"}
            )
        )

        body = session.post.call_args.kwargs["json"]
        assert body["variables"] == {"id": "42"}

    def test_empty_variables_default(self):
        client, session = self._client_with_fresh_jwt()
        self._mock_post(session, 200, {"data": {}})

        asyncio.run(client.query("query Q { me { id } }"))

        body = session.post.call_args.kwargs["json"]
        assert body["variables"] == {}

    def test_retries_on_401_and_refreshes_jwt(self):
        client, session = self._client_with_fresh_jwt()
        first_jwt = client._jwt
        new_jwt = _make_jwt(time.time() + 360)

        responses = []

        # First POST → 401
        resp_401 = AsyncMock()
        resp_401.__aenter__ = AsyncMock(return_value=resp_401)
        resp_401.__aexit__ = AsyncMock(return_value=False)
        resp_401.status = 401
        resp_401.raise_for_status = MagicMock()
        resp_401.json = AsyncMock(return_value={})
        responses.append(resp_401)

        # Renewal POST → new JWT
        resp_renew = AsyncMock()
        resp_renew.__aenter__ = AsyncMock(return_value=resp_renew)
        resp_renew.__aexit__ = AsyncMock(return_value=False)
        resp_renew.status = 200
        resp_renew.raise_for_status = MagicMock()
        resp_renew.json = AsyncMock(return_value={"jwt": new_jwt})
        responses.append(resp_renew)

        # Second Pipe POST → 200
        resp_200 = AsyncMock()
        resp_200.__aenter__ = AsyncMock(return_value=resp_200)
        resp_200.__aexit__ = AsyncMock(return_value=False)
        resp_200.status = 200
        resp_200.raise_for_status = MagicMock()
        resp_200.json = AsyncMock(return_value={"data": {"me": {"id": "1"}}})
        responses.append(resp_200)

        session.post = MagicMock(side_effect=responses)

        result = asyncio.run(client.query("query Q { me { id } }"))

        assert result == {"data": {"me": {"id": "1"}}}
        assert client._jwt == new_jwt
        assert client._jwt != first_jwt

    def test_raises_runtime_error_on_double_401(self):
        """A persistent 401 (both attempts) raises RuntimeError, not an infinite loop."""
        client, session = self._client_with_fresh_jwt()

        # Every POST returns 401 (including the renewal call)
        def _401_resp():
            r = AsyncMock()
            r.__aenter__ = AsyncMock(return_value=r)
            r.__aexit__ = AsyncMock(return_value=False)
            r.status = 401
            r.raise_for_status = MagicMock()
            r.json = AsyncMock(return_value={})
            return r

        # Renewal returns no jwt → RuntimeError from _refresh_jwt
        resp_renew = AsyncMock()
        resp_renew.__aenter__ = AsyncMock(return_value=resp_renew)
        resp_renew.__aexit__ = AsyncMock(return_value=False)
        resp_renew.status = 200
        resp_renew.raise_for_status = MagicMock()
        resp_renew.json = AsyncMock(return_value={"error": "expired_arl"})

        session.post = MagicMock(side_effect=[_401_resp(), resp_renew])

        with pytest.raises(RuntimeError):
            asyncio.run(client.query("query Q { me { id } }"))


# ── DeezerClient integration ───────────────────────────────────────────────────


class TestDeezerClientPipeIntegration:
    def _make_deezer_client(self):
        # Use the packaged default config so the fixture stays hermetic and does
        # not depend on the user's ~/.config/streamrip/config.toml (which would
        # break on every config-version bump).
        from streamrip.config import Config

        cfg = Config.defaults()

        from streamrip.client.deezer import DeezerClient

        return DeezerClient(cfg)

    def test_pipe_is_none_before_login(self):
        client = self._make_deezer_client()
        assert client._pipe is None

    def test_pipe_query_raises_before_login(self):
        client = self._make_deezer_client()
        with pytest.raises(RuntimeError, match="before login"):
            asyncio.run(client.pipe_query("query Q { me { id } }"))

    def test_pipe_initialised_after_login(self):
        from streamrip.client.deezer import DeezerClient

        client = self._make_deezer_client()
        client.config.arl = "testarl"

        mock_session = MagicMock()
        mock_pipe = MagicMock()
        # current_user is a plain instance attribute set by login_via_arl
        client.client.current_user = {"id": 42}

        with (
            patch.object(
                DeezerClient, "get_session", new=AsyncMock(return_value=mock_session)
            ),
            patch.object(client.client, "login_via_arl", return_value=True),
            patch(
                "streamrip.client.deezer.DeezerPipeClient", return_value=mock_pipe
            ) as mock_pipe_cls,
        ):
            asyncio.run(client.login())

        assert client._pipe is mock_pipe
        mock_pipe_cls.assert_called_once_with(client.config.arl, mock_session)

    def test_pipe_query_delegates_to_pipe_client(self):

        client = self._make_deezer_client()
        mock_pipe = AsyncMock()
        mock_pipe.query = AsyncMock(return_value={"data": {"me": {"id": "1"}}})
        client._pipe = mock_pipe

        result = asyncio.run(client.pipe_query("query Q { me { id } }", {"x": 1}))

        mock_pipe.query.assert_called_once_with("query Q { me { id } }", {"x": 1})
        assert result == {"data": {"me": {"id": "1"}}}
