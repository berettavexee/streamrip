"""Tests for client-layer logic that doesn't require real network access."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from streamrip.config import Config
from streamrip.exceptions import (
    AuthenticationError,
    MissingCredentialsError,
)

# ── Client.get_track_for_playlist default ────────────────────────────────────


class TestClientGetTrackForPlaylist:
    @pytest.mark.asyncio
    async def test_default_delegates_to_get_metadata(self):
        """The base Client.get_track_for_playlist must call get_metadata('track')."""
        from streamrip.client.client import Client
        from streamrip.client.downloadable import Downloadable

        class ConcreteClient(Client):
            source = "test"
            max_quality = 2
            logged_in = False

            async def login(self):
                pass

            async def get_metadata(self, item, media_type):
                return {"item": item, "media_type": media_type}

            async def search(self, media_type, query, limit=500):
                return []

            async def get_downloadable(self, item, quality) -> Downloadable:
                pass

        concrete = ConcreteClient()
        concrete._login_lock = asyncio.Lock()
        result = await concrete.get_track_for_playlist("99")
        assert result == {"item": "99", "media_type": "track"}


# ── DeezerClient.login error paths ───────────────────────────────────────────


class TestDeezerClientLoginErrors:
    def _client(self):
        from streamrip.client.deezer import DeezerClient

        config = Config.defaults()
        config.session.deezer.arl = ""
        return DeezerClient(config)

    @pytest.mark.asyncio
    async def test_missing_arl_raises(self):
        client = self._client()
        client.session = MagicMock()
        with patch.object(
            client, "get_session", new=AsyncMock(return_value=MagicMock())
        ):
            with pytest.raises(MissingCredentialsError):
                await client.login()

    @pytest.mark.asyncio
    async def test_failed_login_raises_auth_error(self):
        from streamrip.client.deezer import DeezerClient

        config = Config.defaults()
        config.session.deezer.arl = "fake-bad-arl"
        client = DeezerClient(config)
        client.client.login_via_arl = MagicMock(return_value=False)
        with patch.object(
            client, "get_session", new=AsyncMock(return_value=MagicMock())
        ):
            with pytest.raises(AuthenticationError):
                await client.login()

    @pytest.mark.asyncio
    async def test_successful_login_sets_logged_in(self):
        from streamrip.client.deezer import DeezerClient

        config = Config.defaults()
        config.session.deezer.arl = "valid-arl"
        client = DeezerClient(config)
        client.client.login_via_arl = MagicMock(return_value=True)
        client.client.current_user = {"id": 42}
        with patch.object(
            client, "get_session", new=AsyncMock(return_value=MagicMock())
        ):
            await client.login()
        assert client.logged_in is True
        assert client.logged_in_user_id == 42


# ── DeezerClient.get_metadata unknown type ───────────────────────────────────


class TestDeezerClientGetMetadata:
    def _logged_in_client(self):
        from streamrip.client.deezer import DeezerClient

        config = Config.defaults()
        client = DeezerClient(config)
        client.logged_in = True
        return client

    @pytest.mark.asyncio
    async def test_unknown_media_type_raises(self):
        client = self._logged_in_client()
        with pytest.raises(Exception, match="not available on deezer"):
            await client.get_metadata("123", "video")


# ── DeezerClient.get_album cache ─────────────────────────────────────────────


class TestDeezerClientAlbumCache:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_without_network(self):
        from streamrip.client.deezer import DeezerClient

        config = Config.defaults()
        client = DeezerClient(config)
        client._albums.set("999", {"id": "999", "title": "Cached Album"})

        result = await client.get_album("999")
        assert result["title"] == "Cached Album"


# ── DeezerClient.get_track_for_playlist ──────────────────────────────────────


class TestDeezerGetTrackForPlaylist:
    @pytest.mark.asyncio
    async def test_calls_get_track_without_album_fetch(self):
        from streamrip.client.deezer import DeezerClient

        config = Config.defaults()
        client = DeezerClient(config)
        client.get_track = AsyncMock(return_value={"id": "42"})

        await client.get_track_for_playlist("42")

        client.get_track.assert_called_once_with("42", fetch_album=False)


# ── Client constructors (soundcloud, qobuz, tidal) ───────────────────────────


class TestClientConstructors:
    def test_soundcloud_client_init(self):
        from streamrip.client.soundcloud import SoundcloudClient

        config = Config.defaults()
        config.session.downloads.requests_per_minute = 0
        client = SoundcloudClient(config)
        assert client.logged_in is False
        assert client.source == "soundcloud"

    def test_qobuz_client_init(self):
        from streamrip.client.qobuz import QobuzClient

        config = Config.defaults()
        config.session.downloads.requests_per_minute = 0
        client = QobuzClient(config)
        assert client.logged_in is False
        assert client.source == "qobuz"
        assert client.secret is None

    def test_tidal_client_init(self):
        from streamrip.client.tidal import TidalClient

        config = Config.defaults()
        config.session.downloads.requests_per_minute = 0
        client = TidalClient(config)
        assert client.logged_in is False
        assert client.source == "tidal"

    def test_deezer_client_init(self):
        from streamrip.client.deezer import DeezerClient

        config = Config.defaults()
        client = DeezerClient(config)
        assert client.logged_in is False
        assert client.source == "deezer"
        assert client.logged_in_user_id is None


# ── Client.get_rate_limiter ───────────────────────────────────────────────────


class TestGetRateLimiter:
    def test_zero_returns_nullcontext(self):
        import contextlib

        from streamrip.client.client import Client

        result = Client.get_rate_limiter(0)
        assert isinstance(result, contextlib.nullcontext)

    def test_nonzero_returns_limiter(self):
        import aiolimiter

        from streamrip.client.client import Client

        result = Client.get_rate_limiter(60)
        assert isinstance(result, aiolimiter.AsyncLimiter)


async def test_tidal_404_raises_non_streamable_not_a_logging_error(caplog):
    """The 404 warning had an argument but no placeholder: formatting the record
    raised TypeError from inside logging instead of the NonStreamableError."""
    from contextlib import asynccontextmanager

    from streamrip.client.tidal import TidalClient
    from streamrip.exceptions import NonStreamableError

    config = Config.defaults()
    config.session.downloads.requests_per_minute = 0
    client = TidalClient(config)

    resp = MagicMock(status=404, url="https://api.tidal.com/v1/tracks/1")

    @asynccontextmanager
    async def fake_get(*_a, **_kw):
        yield resp

    client.session = MagicMock(get=fake_get)
    with caplog.at_level("WARNING", logger="streamrip"):
        with pytest.raises(NonStreamableError):
            await client._api_request("tracks/1")
    assert "https://api.tidal.com/v1/tracks/1" in caplog.text


@pytest.mark.parametrize(
    ("media_type", "extra"),
    [("album", "track_ids"), ("playlist", "tracks,track_ids"), ("artist", "albums")],
)
async def test_qobuz_metadata_requests_track_ids(media_type, extra):
    """album/get and playlist/get must ask for track_ids (upstream #1012)."""
    from streamrip.client.qobuz import QobuzClient

    config = Config.defaults()
    config.session.downloads.requests_per_minute = 0
    client = QobuzClient(config)
    client._api_request = AsyncMock(return_value=(200, {}))

    await client.get_metadata("1", media_type)

    params = client._api_request.await_args.args[1]
    assert params["extra"] == extra


def _qobuz_client_ready_to_login():
    from streamrip.client.qobuz import QobuzClient

    config = Config.defaults()
    config.session.downloads.requests_per_minute = 0
    config.session.qobuz.email_or_userid = "user"
    config.session.qobuz.password_or_token = "token"
    config.session.qobuz.app_id = "old-id"
    config.session.qobuz.secrets = ["old-secret"]
    client = QobuzClient(config)
    session = MagicMock()
    session.headers = {}
    session.close = AsyncMock()
    client.get_session = AsyncMock(return_value=session)
    client._get_app_id_and_secrets = AsyncMock(return_value=("new-id", ["new-secret"]))
    return client


async def test_qobuz_stale_app_secret_is_refetched_and_login_retried():
    """Qobuz rotates its app secret; a pinned or cached pair must self-heal."""
    from streamrip.exceptions import InvalidAppSecretError

    client = _qobuz_client_ready_to_login()
    client._attempt_login = AsyncMock(side_effect=[InvalidAppSecretError("x"), None])

    await client.login()

    assert client.logged_in
    assert client._attempt_login.await_count == 2
    assert client.config.session.qobuz.app_id == "new-id"
    assert client.config.file.qobuz.secrets == ["new-secret"]  # persisted


async def test_qobuz_login_retries_only_once():
    from streamrip.exceptions import InvalidAppIdError

    client = _qobuz_client_ready_to_login()
    client._attempt_login = AsyncMock(side_effect=InvalidAppIdError("x"))

    with pytest.raises(InvalidAppIdError):
        await client.login()

    assert client._attempt_login.await_count == 2
    assert not client.logged_in
    client.session.close.assert_awaited_once()


async def test_qobuz_bad_user_credentials_are_not_retried():
    from streamrip.exceptions import AuthenticationError

    client = _qobuz_client_ready_to_login()
    client._attempt_login = AsyncMock(side_effect=AuthenticationError("x"))

    with pytest.raises(AuthenticationError):
        await client.login()

    client._get_app_id_and_secrets.assert_not_awaited()


async def test_sessions_go_through_the_environment_proxy(monkeypatch):
    """aiohttp ignores HTTP(S)_PROXY unless trust_env is set; requests (the
    audio downloads) always honoured it, so only some traffic was proxied."""
    import http.server
    import threading

    from streamrip.client.client import Client

    seen = []

    class Proxy(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen.append(self.path)  # a proxy receives the absolute URL
            body = b"via proxy"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for var in ("NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{srv.server_address[1]}")

    session = await Client.get_session()
    try:
        # The host does not exist: only the proxy can answer.
        async with session.get("http://example.invalid/track") as resp:
            assert await resp.text() == "via proxy"
    finally:
        await session.close()
        srv.shutdown()
    assert seen == ["http://example.invalid/track"]
