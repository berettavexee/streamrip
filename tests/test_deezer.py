import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import deezer
import pytest
import requests
import tomllib
from deezer.errors import DataException, GWAPIError
from util import arun

from streamrip.client.deezer import (
    DeezerClient,
    _HttpsUpgradeSession,
    _TaskCache,
)
from streamrip.config import Config
from streamrip.exceptions import NonStreamableError


def _get_arl() -> str:
    """Return ARL from DEEZER_ARL env var, falling back to ~/.config/streamrip/config.toml."""
    if arl := os.environ.get("DEEZER_ARL", ""):
        return arl
    cfg_path = os.path.expanduser("~/.config/streamrip/config.toml")
    try:
        with open(cfg_path, "rb") as f:
            return tomllib.load(f).get("deezer", {}).get("arl", "")
    except OSError:
        return ""


@pytest.fixture(scope="session")
def deezer_client():
    """Integration test fixture — requires DEEZER_ARL env var or ~/.config/streamrip/config.toml."""
    config = Config.defaults()
    config.session.deezer.arl = _get_arl()
    config.session.deezer.quality = 2
    config.session.deezer.lower_quality_if_not_available = True
    client = DeezerClient(config)
    arun(client.login())

    yield client

    arun(client.session.close())


@pytest.fixture
def mock_deezer_client():
    """Unit test fixture — mocked deezer.Deezer client for fast, offline testing."""
    config = Config.defaults()
    config.session.deezer.arl = "test_arl"
    config.session.deezer.quality = 2
    config.session.deezer.lower_quality_if_not_available = True

    client = DeezerClient(config)
    client.client = Mock()
    client.client.gw = Mock()
    client.session = Mock()

    return client


# ===== _fetch_lyrics =====

def test_fetch_lyrics_disabled_skips_gw_call(mock_deezer_client):
    """_fetch_lyrics returns None immediately when fetch_lyrics=False, without calling GW."""
    mock_deezer_client.config.fetch_lyrics = False
    result = arun(mock_deezer_client._fetch_lyrics("123"))
    assert result is None
    mock_deezer_client.client.gw.get_track_lyrics.assert_not_called()


def test_fetch_lyrics_enabled_calls_gw(mock_deezer_client):
    """_fetch_lyrics calls song.getLyrics and returns LYRICS_TEXT when fetch_lyrics=True."""
    mock_deezer_client.config.fetch_lyrics = True
    mock_deezer_client.client.gw.get_track_lyrics.return_value = {"LYRICS_TEXT": "La la la"}
    result = arun(mock_deezer_client._fetch_lyrics("123"))
    assert result == "La la la"
    mock_deezer_client.client.gw.get_track_lyrics.assert_called_once()


# ===== _TaskCache =====

def test_task_cache_set_if_absent_stores_new():
    """set_if_absent stores a value when the key is not yet cached."""
    cache: _TaskCache[str, str] = _TaskCache()
    cache.set_if_absent("k", "v1")
    assert cache.get("k") == "v1"


def test_task_cache_set_if_absent_does_not_overwrite():
    """set_if_absent does not overwrite a value that is already cached."""
    cache: _TaskCache[str, str] = _TaskCache()
    cache.set("k", "original")
    cache.set_if_absent("k", "replacement")
    assert cache.get("k") == "original"


# ===== get_downloadable — guard =====

def test_deezer_item_id_none(mock_deezer_client):
    """get_downloadable raises NonStreamableError immediately when item_id is None."""
    with pytest.raises(NonStreamableError):
        arun(mock_deezer_client.get_downloadable(None, quality=2))


# ===== get_downloadable — quality fallback =====

def test_deezer_fallback_logic_with_mock_data(mock_deezer_client):
    """WrongLicense on FLAC triggers fallback to MP3_320."""
    mock_track_info = {
        "FILESIZE_FLAC": 0,
        "FILESIZE_MP3_320": 5_000_000,
        "FILESIZE_MP3_128": 2_000_000,
        "TRACK_TOKEN": "test_token",
    }
    mock_deezer_client.client.gw.get_track.return_value = mock_track_info

    def url_side_effect(token, fmt):
        if fmt == "FLAC":
            raise deezer.WrongLicense("FLAC")
        return "https://test.mp3"

    mock_deezer_client.client.get_track_url.side_effect = url_side_effect

    downloadable = arun(mock_deezer_client.get_downloadable("123", quality=2))
    assert downloadable.quality == 1


def test_deezer_no_fallback_when_quality_available(mock_deezer_client):
    """Requested quality is returned unchanged when the API accepts it."""
    mock_track_info = {
        "FILESIZE_FLAC": 25_000_000,
        "FILESIZE_MP3_320": 5_000_000,
        "FILESIZE_MP3_128": 2_000_000,
        "TRACK_TOKEN": "test_token",
    }
    mock_deezer_client.client.gw.get_track.return_value = mock_track_info
    mock_deezer_client.client.get_track_url.return_value = "https://test.flac"

    downloadable = arun(mock_deezer_client.get_downloadable("123", quality=2))
    assert downloadable.quality == 2


def test_deezer_fallback_to_lowest_available_quality(mock_deezer_client):
    """WrongLicense on FLAC and MP3_320 falls back all the way to MP3_128."""
    mock_track_info = {
        "FILESIZE_FLAC": 0,
        "FILESIZE_MP3_320": 0,
        "FILESIZE_MP3_128": 2_000_000,
        "TRACK_TOKEN": "test_token",
    }
    mock_deezer_client.client.gw.get_track.return_value = mock_track_info

    def url_side_effect(token, fmt):
        if fmt in ("FLAC", "MP3_320"):
            raise deezer.WrongLicense(fmt)
        return "https://test.mp3"

    mock_deezer_client.client.get_track_url.side_effect = url_side_effect

    downloadable = arun(mock_deezer_client.get_downloadable("123", quality=2))
    assert downloadable.quality == 0


def test_deezer_no_fallback_when_disabled(mock_deezer_client):
    """WrongLicense raises NonStreamableError immediately when fallback is disabled."""
    mock_deezer_client.config.lower_quality_if_not_available = False

    mock_track_info = {
        "FILESIZE_FLAC": 25_000_000,
        "FILESIZE_MP3_320": 5_000_000,
        "FILESIZE_MP3_128": 2_000_000,
        "TRACK_TOKEN": "test_token",
    }
    mock_deezer_client.client.gw.get_track.return_value = mock_track_info
    mock_deezer_client.client.get_track_url.side_effect = deezer.WrongLicense("FLAC")

    with pytest.raises(NonStreamableError, match="fallback is disabled"):
        arun(mock_deezer_client.get_downloadable("123", quality=2))


def test_deezer_wrong_license_all_qualities(mock_deezer_client):
    """WrongLicense on every quality level falls through to the encrypted CDN URL."""
    mock_track_info = {
        "FILESIZE_FLAC": 25_000_000,
        "FILESIZE_MP3_320": 5_000_000,
        "FILESIZE_MP3_128": 2_000_000,
        "TRACK_TOKEN": "test_token",
        "MD5_ORIGIN": "abc123def456abc123def456abc12345",
        "MEDIA_VERSION": "1",
    }
    mock_deezer_client.client.gw.get_track.return_value = mock_track_info
    mock_deezer_client.client.get_track_url.side_effect = deezer.WrongLicense("any")

    with patch.object(
        mock_deezer_client,
        "_get_encrypted_file_url",
        return_value="https://e-cdns-proxy-a.dzcdn.net/mobile/1/deadbeef",
    ) as mock_encrypted:
        downloadable = arun(mock_deezer_client.get_downloadable("123", quality=2))

    mock_encrypted.assert_called_once_with(
        "123", "abc123def456abc123def456abc12345", "1"
    )
    assert downloadable.url == "https://e-cdns-proxy-a.dzcdn.net/mobile/1/deadbeef"


# ===== get_downloadable — geoblocking =====

def test_deezer_geoblocked_with_fallback(mock_deezer_client):
    """WrongGeolocation retries the download using the FALLBACK track ID."""
    def gw_get_track_side_effect(track_id):
        if track_id == "123":
            return {
                "FILESIZE_FLAC": 25_000_000,
                "FILESIZE_MP3_320": 5_000_000,
                "FILESIZE_MP3_128": 2_000_000,
                "TRACK_TOKEN": "token_123",
                "FALLBACK": {"SNG_ID": "456"},
            }
        return {
            "FILESIZE_FLAC": 25_000_000,
            "FILESIZE_MP3_320": 5_000_000,
            "FILESIZE_MP3_128": 2_000_000,
            "TRACK_TOKEN": "token_456",
        }

    mock_deezer_client.client.gw.get_track.side_effect = gw_get_track_side_effect

    def url_side_effect(token, fmt):
        if token == "token_123":
            raise deezer.WrongGeolocation("FR")
        return "https://test.flac"

    mock_deezer_client.client.get_track_url.side_effect = url_side_effect

    downloadable = arun(mock_deezer_client.get_downloadable("123", quality=2))
    assert downloadable.quality == 2
    assert mock_deezer_client.client.gw.get_track.call_count == 2


def test_deezer_geoblocked_no_fallback(mock_deezer_client):
    """WrongGeolocation raises NonStreamableError when no FALLBACK ID is available."""
    mock_track_info = {
        "FILESIZE_FLAC": 25_000_000,
        "FILESIZE_MP3_320": 5_000_000,
        "FILESIZE_MP3_128": 2_000_000,
        "TRACK_TOKEN": "test_token",
        # no FALLBACK key
    }
    mock_deezer_client.client.gw.get_track.return_value = mock_track_info
    mock_deezer_client.client.get_track_url.side_effect = deezer.WrongGeolocation("FR")

    with pytest.raises(NonStreamableError, match="geoblocked"):
        arun(mock_deezer_client.get_downloadable("123", quality=2))


# ===== get_downloadable — encrypted URL fallback =====

def test_deezer_encrypted_url_fallback(mock_deezer_client):
    """When get_track_url returns None for all qualities, falls back to the AES-encrypted CDN URL."""
    mock_track_info = {
        "FILESIZE_FLAC": 25_000_000,
        "FILESIZE_MP3_320": 5_000_000,
        "FILESIZE_MP3_128": 2_000_000,
        "TRACK_TOKEN": "test_token",
        "MD5_ORIGIN": "abc123def456abc123def456abc12345",
        "MEDIA_VERSION": "1",
    }
    mock_deezer_client.client.gw.get_track.return_value = mock_track_info
    mock_deezer_client.client.get_track_url.return_value = None

    with patch.object(
        mock_deezer_client,
        "_get_encrypted_file_url",
        return_value="https://e-cdns-proxy-a.dzcdn.net/mobile/1/deadbeef",
    ) as mock_encrypted:
        downloadable = arun(mock_deezer_client.get_downloadable("123", quality=2))

    mock_encrypted.assert_called_once_with(
        "123", "abc123def456abc123def456abc12345", "1"
    )
    assert downloadable.url == "https://e-cdns-proxy-a.dzcdn.net/mobile/1/deadbeef"


# ===== get_album =====

def test_deezer_album_cache(mock_deezer_client):
    """Repeated get_album calls for the same ID hit the API exactly once."""
    mock_deezer_client.client.api.get_album.return_value = {
        "id": "album_123",
        "title": "Test Album",
        "genres": {"data": []},
    }
    mock_deezer_client.client.api.get_album_tracks.return_value = {"data": []}

    res1 = arun(mock_deezer_client.get_album("album_123"))
    res2 = arun(mock_deezer_client.get_album("album_123"))

    assert res1 == res2
    assert res1["title"] == "Test Album"
    assert mock_deezer_client.client.api.get_album.call_count == 1
    assert mock_deezer_client.client.api.get_album_tracks.call_count == 1


def test_deezer_album_cache_concurrent(mock_deezer_client):
    """Concurrent get_album calls for the same ID make only one pair of API calls."""
    mock_deezer_client.client.api.get_album.return_value = {
        "id": "album_123",
        "title": "Test Album",
        "genres": {"data": []},
    }
    mock_deezer_client.client.api.get_album_tracks.return_value = {"data": []}

    async def run():
        return await asyncio.gather(
            mock_deezer_client.get_album("album_123"),
            mock_deezer_client.get_album("album_123"),
            mock_deezer_client.get_album("album_123"),
        )

    results = arun(run())
    assert all(r["title"] == "Test Album" for r in results)
    assert mock_deezer_client.client.api.get_album.call_count == 1
    assert mock_deezer_client.client.api.get_album_tracks.call_count == 1


def test_deezer_get_album_redirect(mock_deezer_client):
    """DataException on get_album triggers redirect resolution; original ID is cached."""
    def api_get_album_side_effect(item_id):
        if item_id == "old_id":
            raise DataException
        return {"id": item_id, "title": "Redirected Album"}

    def api_get_album_tracks_side_effect(item_id):
        if item_id == "old_id":
            raise DataException
        return {"data": []}

    mock_deezer_client.client.api.get_album.side_effect = api_get_album_side_effect
    mock_deezer_client.client.api.get_album_tracks.side_effect = api_get_album_tracks_side_effect

    with patch.object(
        mock_deezer_client,
        "_resolve_redirect",
        new=AsyncMock(return_value="new_id"),
    ):
        result = arun(mock_deezer_client.get_album("old_id"))

    assert result["title"] == "Redirected Album"
    # Both the original and canonical IDs should be in the cache.
    assert mock_deezer_client._albums.get("old_id") is not None
    assert mock_deezer_client._albums.get("new_id") is not None


# ===== get_track =====

def test_deezer_get_track(mock_deezer_client):
    """get_track returns a track dict with full album metadata embedded."""
    mock_deezer_client.client.api.get_track.return_value = {
        "id": "100",
        "title": "Test Track",
        "album": {"id": 200},
    }
    mock_deezer_client.client.api.get_album.return_value = {
        "id": "200",
        "title": "Test Album",
    }
    mock_deezer_client.client.api.get_album_tracks.return_value = {
        "data": [{"id": "100"}]
    }
    mock_deezer_client.client.gw.get_track.return_value = {
        "SNG_CONTRIBUTORS": {"composer": ["Bach", "Handel"]},
    }

    track = arun(mock_deezer_client.get_track("100"))

    assert track["title"] == "Test Track"
    assert track["album"]["title"] == "Test Album"
    assert track["album"]["track_total"] == 1
    assert track["composer"] == ["Bach", "Handel"]


def test_deezer_get_track_for_playlist(mock_deezer_client):
    """get_track_for_playlist skips get_album and keeps the REST stub album object."""
    mock_deezer_client.client.api.get_track.return_value = {
        "id": "100",
        "title": "Test Track",
        "album": {"id": 200, "title": "Stub Album"},
    }
    mock_deezer_client.client.gw.get_track.return_value = {
        "SNG_CONTRIBUTORS": {"author": ["Lennon"]},
        "GAIN": "-6.0",
    }

    track = arun(mock_deezer_client.get_track_for_playlist("100"))

    assert track["title"] == "Test Track"
    # album sub-object is the minimal REST stub, not the full album fetch
    assert track["album"] == {"id": 200, "title": "Stub Album"}
    assert track["author"] == ["Lennon"]
    assert track["gain"] == "-6.0"
    # get_album must NOT have been called
    mock_deezer_client.client.api.get_album.assert_not_called()
    mock_deezer_client.client.api.get_album_tracks.assert_not_called()


def test_deezer_gw_track_cache_reuse(mock_deezer_client):
    """gw.get_track is called only once when get_track populates the cache before get_downloadable."""
    mock_deezer_client.client.api.get_track.return_value = {
        "id": "100",
        "title": "Test Track",
        "album": {"id": 200},
    }
    mock_deezer_client.client.api.get_album.return_value = {
        "id": "200",
        "title": "Test Album",
    }
    mock_deezer_client.client.api.get_album_tracks.return_value = {"data": [{"id": "100"}]}
    gw_data = {
        "FILESIZE_FLAC": 25_000_000,
        "FILESIZE_MP3_320": 5_000_000,
        "FILESIZE_MP3_128": 2_000_000,
        "TRACK_TOKEN": "test_token",
        "SNG_CONTRIBUTORS": {},
    }
    mock_deezer_client.client.gw.get_track.return_value = gw_data
    mock_deezer_client.client.get_track_url.return_value = "https://test.flac"

    arun(mock_deezer_client.get_track("100"))
    assert mock_deezer_client.client.gw.get_track.call_count == 1

    # get_downloadable reuses the cached GW data — gw.get_track stays at 1 call total.
    arun(mock_deezer_client.get_downloadable("100", quality=2))
    assert mock_deezer_client.client.gw.get_track.call_count == 1


# ===== get_playlist =====

def test_deezer_get_playlist(mock_deezer_client):
    """get_playlist returns a normalized structure from the GW API response."""
    mock_deezer_client.client.gw.get_playlist.return_value = {
        "DATA": {"TITLE": "My Playlist"}
    }
    mock_deezer_client.client.gw.get_playlist_tracks.return_value = [
        {"SNG_ID": "1"},
        {"SNG_ID": "2"},
    ]

    result = arun(mock_deezer_client.get_playlist("playlist_123"))

    assert result["title"] == "My Playlist"
    assert result["track_total"] == 2
    assert result["tracks"] == [{"id": "1"}, {"id": "2"}]


def test_deezer_get_playlist_favorites_routing(mock_deezer_client):
    """get_playlist routes 'favorites:<user_id>' to get_user_favorites."""
    mock_deezer_client.logged_in_user_id = 42
    entries = [{"SNG_ID": "1"}, {"SNG_ID": "2"}, {"SNG_ID": "3"}]
    mock_deezer_client.client.gw.get_user_favorite_ids.side_effect = [
        {"data": entries},
        {"data": []},  # empty page signals end
    ]
    mock_deezer_client.client.gw.get_tracks.return_value = [
        {"SNG_ID": "1"},
        {"SNG_ID": "2"},
        {"SNG_ID": "3"},
    ]

    result = arun(mock_deezer_client.get_playlist("favorites:42"))

    assert result["title"] == "Loved Tracks"
    assert result["track_total"] == 3
    assert result["tracks"] == [{"id": "1"}, {"id": "2"}, {"id": "3"}]
    mock_deezer_client.client.gw.get_playlist.assert_not_called()


# ===== get_user_favorites =====

def test_deezer_get_user_favorites_own_profile(mock_deezer_client):
    """Own favorites are fetched via paginated get_user_favorite_ids, not get_my_favorite_tracks.

    The server silently caps song.getFavoriteIds responses; pagination via ``start``
    is required. This test verifies that get_user_favorite_ids is called (not the
    non-paginated get_my_favorite_tracks) and that results come from the paginated data.
    """
    mock_deezer_client.logged_in_user_id = 42
    mock_deezer_client.client.gw.get_user_favorite_ids.side_effect = [
        {"data": [{"SNG_ID": "10"}]},
        {"data": []},
    ]
    mock_deezer_client.client.gw.get_tracks.return_value = [{"SNG_ID": "10"}]

    result = arun(mock_deezer_client.get_user_favorites("42"))

    assert result["track_total"] == 1
    assert result["tracks"] == [{"id": "10"}]
    mock_deezer_client.client.gw.get_my_favorite_tracks.assert_not_called()
    mock_deezer_client.client.gw.get_user_tracks.assert_not_called()


def test_deezer_get_user_favorites_always_uses_own_account(mock_deezer_client):
    """get_user_favorites always fetches via get_user_favorite_ids (authenticated user).

    For family accounts, logged_in_user_id is the child profile id, which may differ
    from the uid in the favorites URL (main account id).  The uid comparison is
    therefore unreliable; get_user_favorite_ids carries no uid parameter and always
    returns the authenticated account's favorites, so we use it unconditionally.
    """
    mock_deezer_client.logged_in_user_id = 42
    mock_deezer_client.client.gw.get_user_favorite_ids.side_effect = [
        {"data": [{"SNG_ID": "99"}, {"SNG_ID": "100"}]},
        {"data": []},
    ]
    mock_deezer_client.client.gw.get_tracks.return_value = []

    # Pass a uid that differs from logged_in_user_id (simulates family account case)
    result = arun(mock_deezer_client.get_user_favorites("9999"))

    assert result["track_total"] == 2
    mock_deezer_client.client.gw.get_user_favorite_ids.assert_called()
    mock_deezer_client.client.gw.get_user_tracks.assert_not_called()
    mock_deezer_client.client.gw.get_my_favorite_tracks.assert_not_called()


def test_deezer_get_user_favorites_prefetch_populates_cache(mock_deezer_client):
    """get_user_favorites pre-populates _gw_tracks so subsequent get_downloadable hits cache.

    After get_user_favorites, each track's GW data should be stored in _gw_tracks
    so that get_downloadable can skip the individual song.getData call entirely.
    """
    mock_deezer_client.logged_in_user_id = 42
    mock_deezer_client.client.gw.get_user_favorite_ids.side_effect = [
        {"data": [{"SNG_ID": "10"}, {"SNG_ID": "20"}]},
        {"data": []},
    ]
    gw_data = [
        {"SNG_ID": "10", "TRACK_TOKEN": "tok10", "FILESIZE_FLAC": 1000},
        {"SNG_ID": "20", "TRACK_TOKEN": "tok20", "FILESIZE_FLAC": 2000},
    ]
    mock_deezer_client.client.gw.get_tracks.return_value = gw_data

    arun(mock_deezer_client.get_user_favorites("42"))

    assert "10" in mock_deezer_client._gw_tracks
    assert "20" in mock_deezer_client._gw_tracks
    assert mock_deezer_client._gw_tracks.get("10")["TRACK_TOKEN"] == "tok10"


def test_deezer_get_user_favorites_prefetch_chunking(mock_deezer_client):
    """GW prefetch splits track IDs into chunks of 50 and issues one get_tracks call per chunk.

    With 75 favorites the IDs must be split into a chunk of 50 and a chunk of 25,
    triggering exactly 2 get_tracks calls in parallel.
    """
    mock_deezer_client.logged_in_user_id = 42
    entries = [{"SNG_ID": str(i)} for i in range(75)]
    mock_deezer_client.client.gw.get_user_favorite_ids.side_effect = [
        {"data": entries},
        {"data": []},
    ]
    mock_deezer_client.client.gw.get_tracks.return_value = []

    arun(mock_deezer_client.get_user_favorites("42"))

    assert mock_deezer_client.client.gw.get_tracks.call_count == 2
    first_ids = mock_deezer_client.client.gw.get_tracks.call_args_list[0].args[0]
    second_ids = mock_deezer_client.client.gw.get_tracks.call_args_list[1].args[0]
    assert len(first_ids) == 50
    assert len(second_ids) == 25


def test_deezer_get_user_favorites_pagination(mock_deezer_client):
    """Own favorites are fetched across multiple pages.

    The server ignores nb= and always returns its own page size (empirically ~25).
    start must advance by the actual count returned, not by the requested fetch_batch,
    otherwise the loop exits after one call thinking there are no more pages.
    """
    mock_deezer_client.logged_in_user_id = 42
    # Simulate server page size of 25 (smaller than fetch_batch=100 requested)
    server_page = 25
    page1 = [{"SNG_ID": str(i)} for i in range(server_page)]
    page2 = [{"SNG_ID": str(i)} for i in range(server_page, server_page + 6)]

    mock_deezer_client.client.gw.get_user_favorite_ids.side_effect = [
        {"data": page1},
        {"data": page2},
        {"data": []},  # empty page signals end
    ]
    mock_deezer_client.client.gw.get_tracks.return_value = []

    result = arun(mock_deezer_client.get_user_favorites("42"))

    assert result["track_total"] == server_page + 6
    calls = mock_deezer_client.client.gw.get_user_favorite_ids.call_args_list
    assert calls[0].kwargs["start"] == 0
    assert calls[1].kwargs["start"] == server_page        # advanced by actual count
    assert calls[2].kwargs["start"] == server_page + 6   # advanced again by actual count


# ===== get_metadata =====

def test_deezer_get_metadata_dispatch(mock_deezer_client):
    """get_metadata dispatches to the correct handler for each media type."""
    handlers = {
        "track": "get_track",
        "album": "get_album",
        "playlist": "get_playlist",
        "artist": "get_artist",
    }
    for media_type, method_name in handlers.items():
        expected = {"id": "1", "type": media_type}
        with patch.object(mock_deezer_client, method_name, return_value=expected) as mock_handler:
            result = arun(mock_deezer_client.get_metadata("1", media_type))
            mock_handler.assert_called_once_with("1")
            assert result == expected


def test_deezer_get_metadata_invalid_type(mock_deezer_client):
    """get_metadata raises for unsupported media types."""
    with pytest.raises(Exception, match="not available on deezer"):
        arun(mock_deezer_client.get_metadata("1", "label"))


# ===== search =====

def test_deezer_search_track(mock_deezer_client):
    """search returns a list containing the API response when results are found."""
    mock_deezer_client.client.api.search_track.return_value = {
        "total": 2,
        "data": [{"id": "1"}, {"id": "2"}],
    }

    results = arun(mock_deezer_client.search("track", "test query"))

    assert len(results) == 1
    assert results[0]["total"] == 2
    mock_deezer_client.client.api.search_track.assert_called_once_with(
        "test query", limit=200
    )


def test_deezer_search_no_results(mock_deezer_client):
    """search returns an empty list when the API reports zero results."""
    mock_deezer_client.client.api.search_track.return_value = {"total": 0, "data": []}

    results = arun(mock_deezer_client.search("track", "nonexistent"))

    assert results == []


def _setup_head_mock(mock_client, final_url):
    """Configure mock_client.session.head as an async context manager."""
    mock_resp = Mock()
    mock_resp.url = final_url
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_cm.__aexit__ = AsyncMock(return_value=None)
    mock_client.session.head.return_value = mock_cm


# ===== get_track — error paths =====

def test_get_track_api_failure(mock_deezer_client):
    """get_track wraps API errors in NonStreamableError."""
    mock_deezer_client.client.api.get_track.side_effect = Exception("API down")
    with pytest.raises(NonStreamableError):
        arun(mock_deezer_client.get_track("999"))


def test_get_track_error_dict_raises(mock_deezer_client):
    """When deezer-py returns an error dict instead of raising (e.g. quota 800),
    get_track raises NonStreamableError rather than passing a corrupt dict downstream."""
    mock_deezer_client.client.api.get_track.return_value = {
        "error": 800,
        "message": "Quota limit exceeded",
        "type": "DataException",
    }
    with pytest.raises(NonStreamableError, match="800"):
        arun(mock_deezer_client.get_track("42"))


def test_get_track_gw_fetch_error_raises(mock_deezer_client):
    """When GW data fetch fails with NonStreamableError, get_track propagates it."""
    mock_deezer_client.client.api.get_track.return_value = {
        "id": "100",
        "title": "Test Track",
        "album": {"id": 200},
    }
    mock_deezer_client.client.api.get_album.return_value = {"id": "200", "title": "Album"}
    mock_deezer_client.client.api.get_album_tracks.return_value = {"data": []}
    mock_deezer_client.client.gw.get_track.side_effect = Exception("GW down")

    with pytest.raises(NonStreamableError):
        arun(mock_deezer_client.get_track("100"))


# ===== get_album — task exception =====

def test_get_album_task_exception_clears_task(mock_deezer_client):
    """A failed get_album task is evicted so it can be retried."""
    mock_deezer_client.client.api.get_album.side_effect = DataException

    with patch.object(mock_deezer_client, "_resolve_redirect", new=AsyncMock(return_value=None)):
        with pytest.raises(DataException):
            arun(mock_deezer_client.get_album("bad_album"))

    assert not mock_deezer_client._albums.has_pending("bad_album")


# ===== _resolve_redirect =====

def test_resolve_redirect_success(mock_deezer_client):
    """_resolve_redirect returns the new ID when the server redirects."""
    _setup_head_mock(mock_deezer_client, "https://www.deezer.com/album/99999")
    result = arun(mock_deezer_client._resolve_redirect("album", "old_id"))
    assert result == "99999"


def test_resolve_redirect_same_url_returns_none(mock_deezer_client):
    """_resolve_redirect returns None when there is no redirect."""
    _setup_head_mock(mock_deezer_client, "https://www.deezer.com/album/old_id")
    result = arun(mock_deezer_client._resolve_redirect("album", "old_id"))
    assert result is None


def test_resolve_redirect_network_error_returns_none(mock_deezer_client):
    """_resolve_redirect swallows network errors and returns None."""
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(side_effect=Exception("timeout"))
    mock_cm.__aexit__ = AsyncMock(return_value=None)
    mock_deezer_client.session.head.return_value = mock_cm

    result = arun(mock_deezer_client._resolve_redirect("album", "old_id"))
    assert result is None


def test_resolve_redirect_no_regex_match_returns_none(mock_deezer_client):
    """_resolve_redirect returns None when the final URL has no parsable ID."""
    _setup_head_mock(mock_deezer_client, "https://www.deezer.com/error")
    result = arun(mock_deezer_client._resolve_redirect("album", "old_id"))
    assert result is None


# ===== get_playlist — GWAPIError =====

def test_get_playlist_gw_error_redirect(mock_deezer_client):
    """GWAPIError on get_playlist triggers redirect resolution."""
    call_count = [0]

    def gw_get_playlist(item_id):
        call_count[0] += 1
        if call_count[0] == 1:
            raise GWAPIError("not found")
        return {"DATA": {"TITLE": "Redirected Playlist"}}

    mock_deezer_client.client.gw.get_playlist.side_effect = gw_get_playlist
    mock_deezer_client.client.gw.get_playlist_tracks.return_value = [{"SNG_ID": "1"}]

    with patch.object(mock_deezer_client, "_resolve_redirect", new=AsyncMock(return_value="new_id")):
        result = arun(mock_deezer_client.get_playlist("old_id"))

    assert result["title"] == "Redirected Playlist"


def test_get_playlist_gw_error_no_redirect_raises(mock_deezer_client):
    """GWAPIError re-raises when no redirect is available."""
    mock_deezer_client.client.gw.get_playlist.side_effect = GWAPIError("not found")

    with patch.object(mock_deezer_client, "_resolve_redirect", new=AsyncMock(return_value=None)):
        with pytest.raises(GWAPIError):
            arun(mock_deezer_client.get_playlist("bad_id"))


# ===== get_artist =====

def test_get_artist_redirect(mock_deezer_client):
    """DataException on get_artist triggers redirect resolution."""
    def api_get_artist(item_id):
        if item_id == "old_id":
            raise DataException
        return {"id": item_id, "name": "Artist X"}

    mock_deezer_client.client.api.get_artist.side_effect = api_get_artist
    mock_deezer_client.client.api.get_artist_albums.return_value = {"data": []}

    with patch.object(mock_deezer_client, "_resolve_redirect", new=AsyncMock(return_value="new_id")):
        result = arun(mock_deezer_client.get_artist("old_id"))

    assert result["name"] == "Artist X"


def test_get_artist_no_redirect_raises(mock_deezer_client):
    """DataException re-raises when no redirect is available for the artist."""
    mock_deezer_client.client.api.get_artist.side_effect = DataException

    with patch.object(mock_deezer_client, "_resolve_redirect", new=AsyncMock(return_value=None)):
        with pytest.raises(DataException):
            arun(mock_deezer_client.get_artist("bad_id"))


# ===== search — featured / invalid type =====

def test_deezer_search_featured_with_query(mock_deezer_client):
    """search('featured', 'releases') calls get_editorial_releases."""
    mock_deezer_client.client.api.get_editorial_releases.return_value = {
        "total": 2,
        "data": [{"id": "1"}, {"id": "2"}],
    }
    results = arun(mock_deezer_client.search("featured", "releases"))
    assert len(results) == 1
    mock_deezer_client.client.api.get_editorial_releases.assert_called_once()


def test_deezer_search_featured_no_query(mock_deezer_client):
    """search('featured', '') uses get_editorial_releases without a suffix."""
    mock_deezer_client.client.api.get_editorial_releases.return_value = {
        "total": 1,
        "data": [{"id": "1"}],
    }
    results = arun(mock_deezer_client.search("featured", ""))
    assert len(results) == 1


def test_deezer_search_featured_invalid_category(mock_deezer_client):
    """Unknown editorial category raises Exception."""
    mock_deezer_client.client.api = MagicMock(spec=["get_editorial_releases"])
    with pytest.raises(Exception, match="Invalid editorical selection"):
        arun(mock_deezer_client.search("featured", "nonexistent_xyz"))


def test_deezer_search_invalid_media_type(mock_deezer_client):
    """Unknown media type raises Exception."""
    mock_deezer_client.client.api = MagicMock(spec=["search_track"])
    with pytest.raises(Exception, match="Invalid media type"):
        arun(mock_deezer_client.search("nonexistent_type", "query"))


# ===== get_downloadable — additional error paths =====

def test_get_downloadable_gw_fetch_fails(mock_deezer_client):
    """NonStreamableError when GW track info can't be fetched (cache miss)."""
    mock_deezer_client.client.gw.get_track.side_effect = Exception("GW error")
    with pytest.raises(NonStreamableError, match="Could not fetch GW track info"):
        arun(mock_deezer_client.get_downloadable("no_cache_id", quality=2))


def test_get_downloadable_no_token(mock_deezer_client):
    """NonStreamableError when the track has no TRACK_TOKEN."""
    mock_deezer_client.client.gw.get_track.return_value = {
        "FILESIZE_FLAC": 25_000_000,
        "FILESIZE_MP3_320": 5_000_000,
        "FILESIZE_MP3_128": 2_000_000,
        # No TRACK_TOKEN
    }
    with pytest.raises(NonStreamableError, match="no TRACK_TOKEN"):
        arun(mock_deezer_client.get_downloadable("123", quality=2))


def test_get_downloadable_missing_cdn_fields(mock_deezer_client):
    """NonStreamableError when all qualities fail and MD5/MEDIA_VERSION are absent."""
    mock_deezer_client.client.gw.get_track.return_value = {
        "FILESIZE_FLAC": 25_000_000,
        "FILESIZE_MP3_320": 5_000_000,
        "FILESIZE_MP3_128": 2_000_000,
        "TRACK_TOKEN": "test_token",
        # No MD5_ORIGIN or MEDIA_VERSION
    }
    mock_deezer_client.client.get_track_url.side_effect = deezer.WrongLicense("any")

    with pytest.raises(NonStreamableError, match="MD5_ORIGIN/MEDIA_VERSION"):
        arun(mock_deezer_client.get_downloadable("123", quality=2))


def test_get_downloadable_encrypted_url_none(mock_deezer_client):
    """NonStreamableError when the CDN fallback returns an empty URL."""
    mock_deezer_client.client.gw.get_track.return_value = {
        "FILESIZE_FLAC": 25_000_000,
        "FILESIZE_MP3_320": 5_000_000,
        "FILESIZE_MP3_128": 2_000_000,
        "TRACK_TOKEN": "test_token",
        "MD5_ORIGIN": "abc123",
        "MEDIA_VERSION": "1",
    }
    mock_deezer_client.client.get_track_url.side_effect = deezer.WrongLicense("any")

    with patch.object(mock_deezer_client, "_get_encrypted_file_url", return_value=None):
        with pytest.raises(NonStreamableError, match="Could not retrieve"):
            arun(mock_deezer_client.get_downloadable("123", quality=2))


# ===== _get_encrypted_file_url =====

def test_get_encrypted_file_url_url_structure(mock_deezer_client):
    """_get_encrypted_file_url generates a CDN URL whose proxy prefix matches the hash's first char."""
    url = mock_deezer_client._get_encrypted_file_url(
        meta_id="12345",
        track_hash="abc123def456abc123def456abc12345",
        media_version="1",
    )
    # Proxy subdomain is derived from track_hash[0] = 'a'.
    assert url.startswith("https://e-cdns-proxy-a.dzcdn.net/mobile/1/")
    # Path is AES-ECB output encoded as hex — always a non-trivial string.
    path = url.split("/mobile/1/")[1]
    assert len(path) > 32
    assert all(c in "0123456789abcdef" for c in path)


def test_get_encrypted_file_url_format_id_from_quality_map(mock_deezer_client):
    """_get_encrypted_file_url uses _QUALITY_MAP[2][0] as the format ID, not a hardcoded literal.

    _QUALITY_MAP[2] is the FLAC entry; its GW format ID is 1. The encrypted CDN
    always serves FLAC, so changing the map entry must change the format byte in
    the URL-hash input.
    """
    # Patch _QUALITY_MAP to replace the FLAC GW format ID (index 2) with a sentinel.
    original_map = mock_deezer_client._QUALITY_MAP
    patched_map = [original_map[0], original_map[1], (99, original_map[2][1])]
    with patch.object(DeezerClient, "_QUALITY_MAP", patched_map):
        url_patched = mock_deezer_client._get_encrypted_file_url(
            meta_id="12345",
            track_hash="abc123def456abc123def456abc12345",
            media_version="1",
        )
    url_original = mock_deezer_client._get_encrypted_file_url(
        meta_id="12345",
        track_hash="abc123def456abc123def456abc12345",
        media_version="1",
    )
    # Different format IDs must produce different encrypted paths.
    assert url_patched != url_original


# ===== _HttpsUpgradeSession =====

def test_https_upgrade_session_rewrites_http():
    """_HttpsUpgradeSession rewrites http:// to https:// before prepare_request."""
    session = _HttpsUpgradeSession()
    with patch.object(requests.Session, "request", return_value=Mock()) as mock_request:
        session.request("GET", "http://www.deezer.com/ajax/gw-light.php?method=foo")

    actual_url = mock_request.call_args[0][1]
    assert actual_url.startswith("https://")
    assert "http://" not in actual_url


def test_https_upgrade_session_leaves_https_unchanged():
    """_HttpsUpgradeSession does not alter URLs that are already HTTPS."""
    session = _HttpsUpgradeSession()
    with patch.object(requests.Session, "request", return_value=Mock()) as mock_request:
        session.request("GET", "https://api.deezer.com/track/123")

    actual_url = mock_request.call_args[0][1]
    assert actual_url == "https://api.deezer.com/track/123"


def test_https_upgrade_session_installed_on_gw():
    """DeezerClient replaces the deezer-py GW session with _HttpsUpgradeSession."""
    config = Config.defaults()
    config.session.deezer.arl = "test_arl"
    client = DeezerClient(config)
    assert isinstance(client.client.gw.session, _HttpsUpgradeSession)
    assert isinstance(client.client.api.session, _HttpsUpgradeSession)
    assert isinstance(client.client.session, _HttpsUpgradeSession)


# ===== _gw_to_track_dict =====

def _make_gw_track(**overrides) -> dict:
    base = {
        "SNG_ID": "42",
        "SNG_TITLE": "Test Track",
        "ISRC": "GBAWA0900090",
        "EXPLICIT_LYRICS": "0",
        "TRACK_NUMBER": "3",
        "DISK_NUMBER": "1",
        "BPM": "120.0",
        "GAIN": "-6.5",
        "ART_NAME": "Test Artist",
        "ALB_ID": "99",
        "ALB_TITLE": "Test Album",
        "ALB_PICTURE": "abc123hash",
        "PHYSICAL_RELEASE_DATE": "2023-06-15",
        "SNG_CONTRIBUTORS": {
            "main_artist": ["Test Artist"],
            "composer": ["J. Bach"],
            "author": ["G. Handel"],
        },
    }
    base.update(overrides)
    return base


def test_gw_to_track_dict_basic_fields(mock_deezer_client):
    gw = _make_gw_track()
    result = mock_deezer_client._gw_to_track_dict(gw)

    assert result["id"] == 42
    assert result["title"] == "Test Track"
    assert result["isrc"] == "GBAWA0900090"
    assert result["explicit_lyrics"] is False
    assert result["track_position"] == 3
    assert result["disk_number"] == 1
    assert result["gain"] == "-6.5"


def test_gw_to_track_dict_contributor_mapping(mock_deezer_client):
    gw = _make_gw_track()
    result = mock_deezer_client._gw_to_track_dict(gw)

    artist_entries = [c for c in result["contributors"] if c["type"] == "artist"]
    assert any(c["name"] == "Test Artist" for c in artist_entries)
    assert result["composer"] == ["J. Bach"]
    assert result["author"] == ["G. Handel"]


def test_gw_to_track_dict_zero_isrc_becomes_empty_string(mock_deezer_client):
    gw = _make_gw_track(ISRC=0)
    result = mock_deezer_client._gw_to_track_dict(gw)
    assert result["isrc"] == ""


def test_gw_to_track_dict_zero_bpm_becomes_none(mock_deezer_client):
    gw = _make_gw_track(BPM="0")
    result = mock_deezer_client._gw_to_track_dict(gw)
    assert result["bpm"] is None


def test_gw_to_track_dict_lyrics_forwarded(mock_deezer_client):
    gw = _make_gw_track()
    result = mock_deezer_client._gw_to_track_dict(gw, lyrics="La la la")
    assert result["lyrics"] == "La la la"


# ===== get_track — GW fast path =====

def test_get_track_fast_path_skips_rest_call(mock_deezer_client):
    """When GW data is cached with ISRC, get_track uses it without calling REST GET /track."""
    gw = _make_gw_track()
    mock_deezer_client._gw_tracks.set("42", gw)

    mock_deezer_client.client.api.get_album.return_value = {"id": "99", "title": "Album"}
    mock_deezer_client.client.api.get_album_tracks.return_value = {"data": []}

    result = arun(mock_deezer_client.get_track("42"))

    assert result["title"] == "Test Track"
    mock_deezer_client.client.api.get_track.assert_not_called()


def test_get_track_fast_path_fetches_album(mock_deezer_client):
    """Fast path resolves album via get_album (which may hit cache)."""
    gw = _make_gw_track()
    mock_deezer_client._gw_tracks.set("42", gw)
    mock_deezer_client.client.api.get_album.return_value = {"id": "99", "title": "Test Album"}
    mock_deezer_client.client.api.get_album_tracks.return_value = {"data": []}

    result = arun(mock_deezer_client.get_track("42", fetch_album=True))

    assert result["album"]["title"] == "Test Album"


def test_get_track_fast_path_skips_album_when_fetch_album_false(mock_deezer_client):
    """Fast path with fetch_album=False does not call get_album."""
    gw = _make_gw_track()
    mock_deezer_client._gw_tracks.set("42", gw)

    result = arun(mock_deezer_client.get_track("42", fetch_album=False))

    assert result["title"] == "Test Track"
    mock_deezer_client.client.api.get_album.assert_not_called()


def test_get_track_slow_path_when_no_gw_cache(mock_deezer_client):
    """When GW cache is empty, get_track falls back to the REST GET /track call."""
    mock_deezer_client.client.api.get_track.return_value = {
        "id": "100",
        "title": "Slow Track",
        "album": {"id": 200},
    }
    mock_deezer_client.client.api.get_album.return_value = {"id": "200", "title": "Slow Album"}
    mock_deezer_client.client.api.get_album_tracks.return_value = {"data": []}
    mock_deezer_client.client.gw.get_track.return_value = {"SNG_CONTRIBUTORS": {}}

    result = arun(mock_deezer_client.get_track("100"))

    assert result["title"] == "Slow Track"
    mock_deezer_client.client.api.get_track.assert_called_once()


def test_get_track_slow_path_when_gw_cache_lacks_isrc(mock_deezer_client):
    """GW cache entries without ISRC (e.g. minimal prefetch) use the REST slow path."""
    gw_without_isrc = {"SNG_ID": "42", "SNG_TITLE": "Partial", "TRACK_TOKEN": "tok"}
    mock_deezer_client._gw_tracks.set("42", gw_without_isrc)

    mock_deezer_client.client.api.get_track.return_value = {
        "id": "42",
        "title": "Full Track",
        "album": {"id": 200},
    }
    mock_deezer_client.client.api.get_album.return_value = {"id": "200", "title": "Album"}
    mock_deezer_client.client.api.get_album_tracks.return_value = {"data": []}
    mock_deezer_client.client.gw.get_track.return_value = {"SNG_CONTRIBUTORS": {}}

    result = arun(mock_deezer_client.get_track("42"))

    assert result["title"] == "Full Track"
    mock_deezer_client.client.api.get_track.assert_called_once()


# ===== batch URL resolution =====

def test_batch_url_single_call_for_multiple_tracks(mock_deezer_client):
    """get_tracks_url is called once for N tracks; individual get_track_url is never used."""
    track_infos = {
        "1": {"TRACK_TOKEN": "tok1", "FILESIZE_FLAC": 1000, "ISRC": "A"},
        "2": {"TRACK_TOKEN": "tok2", "FILESIZE_FLAC": 1000, "ISRC": "B"},
        "3": {"TRACK_TOKEN": "tok3", "FILESIZE_FLAC": 1000, "ISRC": "C"},
    }
    for tid, info in track_infos.items():
        mock_deezer_client._gw_tracks.set_if_absent(tid, info)
    mock_deezer_client.client.get_tracks_url.return_value = [
        "https://cdn1.flac",
        "https://cdn2.flac",
        "https://cdn3.flac",
    ]

    for tid in ["1", "2", "3"]:
        dl = arun(mock_deezer_client.get_downloadable(tid, quality=2))
        assert dl.quality == 2

    assert mock_deezer_client.client.get_tracks_url.call_count == 1
    mock_deezer_client.client.get_track_url.assert_not_called()


def test_batch_url_transient_chunk_failure_still_resolves_later_chunks(mock_deezer_client):
    """A transient failure on one chunk must not abandon the remaining chunks."""
    for tid in ("1", "2", "3", "4"):
        mock_deezer_client._gw_tracks.set_if_absent(tid, {"TRACK_TOKEN": f"tok{tid}"})

    # First chunk raises a transient error; second chunk resolves normally.
    mock_deezer_client.client.get_tracks_url.side_effect = [
        RuntimeError("transient network blip"),
        ["https://cdn3.flac", "https://cdn4.flac"],
    ]

    with patch.object(DeezerClient, "_BATCH_URL_CHUNK_SIZE", 2):
        arun(mock_deezer_client._batch_resolve_urls("FLAC"))

    # Chunk 1 (tracks 1,2) failed → not cached; chunk 2 (tracks 3,4) still resolved.
    assert ("1", "FLAC") not in mock_deezer_client._url_results
    assert ("2", "FLAC") not in mock_deezer_client._url_results
    assert mock_deezer_client._url_results[("3", "FLAC")] == "https://cdn3.flac"
    assert mock_deezer_client._url_results[("4", "FLAC")] == "https://cdn4.flac"
    assert mock_deezer_client.client.get_tracks_url.call_count == 2


def test_batch_url_wrong_license_stops_remaining_chunks(mock_deezer_client):
    """WrongLicense aborts the whole batch — every chunk would fail the same way."""
    for tid in ("1", "2", "3", "4"):
        mock_deezer_client._gw_tracks.set_if_absent(tid, {"TRACK_TOKEN": f"tok{tid}"})

    mock_deezer_client.client.get_tracks_url.side_effect = deezer.WrongLicense("FLAC")

    with patch.object(DeezerClient, "_BATCH_URL_CHUNK_SIZE", 2):
        arun(mock_deezer_client._batch_resolve_urls("FLAC"))

    # Only the first chunk was attempted before bailing out.
    assert mock_deezer_client.client.get_tracks_url.call_count == 1
    assert mock_deezer_client._url_results == {}


def test_batch_url_wrong_license_falls_back_to_lower_quality(mock_deezer_client):
    """WrongLicense from batch is absorbed; individual get_track_url also raises it, triggering fallback."""
    mock_deezer_client._gw_tracks.set_if_absent(
        "1", {"TRACK_TOKEN": "tok", "FILESIZE_FLAC": 0, "FILESIZE_MP3_320": 5000}
    )

    def tracks_url_side_effect(tokens, fmt):
        if fmt == "FLAC":
            raise deezer.WrongLicense("FLAC")
        return ["https://cdn.mp3"]

    def track_url_side_effect(token, fmt):
        if fmt == "FLAC":
            raise deezer.WrongLicense("FLAC")
        return "https://cdn.mp3"

    mock_deezer_client.client.get_tracks_url.side_effect = tracks_url_side_effect
    mock_deezer_client.client.get_track_url.side_effect = track_url_side_effect

    dl = arun(mock_deezer_client.get_downloadable("1", quality=2))
    assert dl.quality == 1
    # Individual get_track_url must have been called for FLAC (batch WrongLicense is now absorbed)
    mock_deezer_client.client.get_track_url.assert_called()


def test_batch_url_geoblocked_track_falls_back_to_individual(mock_deezer_client):
    """A geoblocked entry in the batch result triggers the individual get_track_url fallback."""
    mock_deezer_client._gw_tracks.set_if_absent(
        "1", {"TRACK_TOKEN": "tok", "FILESIZE_FLAC": 5000}
    )
    # Batch returns a WrongGeolocation object (not a string) for this track
    mock_deezer_client.client.get_tracks_url.return_value = [
        deezer.WrongGeolocation("FR")
    ]
    mock_deezer_client.client.get_track_url.side_effect = deezer.WrongGeolocation("FR")

    with pytest.raises(NonStreamableError, match="geoblocked"):
        arun(mock_deezer_client.get_downloadable("1", quality=2))

    mock_deezer_client.client.get_track_url.assert_called()


# ===== _gw_to_track_dict — audio params =====

def test_gw_to_track_dict_audio_params(mock_deezer_client):
    """_gw_to_track_dict includes bit_depth and sampling_rate derived from Deezer constants."""
    from streamrip.client.deezer import _DEEZER_BIT_DEPTH, _DEEZER_SAMPLING_RATE_KHZ

    result = mock_deezer_client._gw_to_track_dict(_make_gw_track())
    assert result["bit_depth"] == _DEEZER_BIT_DEPTH
    assert result["sampling_rate"] == _DEEZER_SAMPLING_RATE_KHZ


def test_gw_to_track_dict_album_stub(mock_deezer_client):
    """_gw_to_track_dict includes a minimal album stub so playlist tracks don't KeyError."""
    result = mock_deezer_client._gw_to_track_dict(_make_gw_track())
    album = result["album"]

    assert album["id"] == 99
    assert album["title"] == "Test Album"
    assert album["release_date"] == "2023-06-15"
    assert "abc123hash" in album["cover_xl"]
    assert "abc123hash" in album["cover_small"]
    # Must NOT contain a "tracks" key — triggers from_incomplete_deezer_track_resp path
    assert "tracks" not in album


def test_gw_to_track_dict_album_stub_overwritten_on_fetch_album(mock_deezer_client):
    """When fetch_album=True the stub is replaced by the full album dict."""
    gw = _make_gw_track(TRACK_TOKEN="tok", FILESIZE_FLAC="1000")
    mock_deezer_client._gw_tracks.set("42", gw)

    full_album = {
        "id": 99,
        "title": "Full Album",
        "tracks": [{"id": "42", "disk_number": 1}],
        "track_total": 1,
        "genres": {"data": []},
        "release_date": "2023-06-15",
        "contributors": [{"name": "Test Artist", "type": "artist"}],
        "bit_depth": 16,
        "sampling_rate": 44100,
    }
    mock_deezer_client.client.api.get_album.return_value = full_album
    mock_deezer_client.client.api.get_album_tracks.return_value = {
        "data": [{"id": "42", "disk_number": 1}]
    }
    mock_deezer_client.client.gw.get_album_tracks.return_value = []

    track = arun(mock_deezer_client.get_track("42", fetch_album=True))
    # Full album metadata replaces the stub
    assert track["album"]["title"] == "Full Album"
    assert "tracks" in track["album"]


# ===== _fetch_album — audio params =====

def test_fetch_album_injects_audio_params(mock_deezer_client):
    """_fetch_album adds bit_depth and sampling_rate so AlbumMetadata.from_deezer reads them."""
    from streamrip.client.deezer import _DEEZER_BIT_DEPTH, _DEEZER_SAMPLING_RATE_HZ

    mock_deezer_client.client.api.get_album.return_value = {
        "id": "10",
        "title": "Test Album",
        "genres": {"data": []},
    }
    mock_deezer_client.client.api.get_album_tracks.return_value = {"data": []}
    mock_deezer_client.client.gw.get_album_tracks.return_value = []

    meta = arun(mock_deezer_client._fetch_album("10"))
    assert meta["bit_depth"] == _DEEZER_BIT_DEPTH
    assert meta["sampling_rate"] == _DEEZER_SAMPLING_RATE_HZ


# ===== Integration test =====

@pytest.mark.skipif(not _get_arl(), reason="Deezer ARL not found in env or config.")
def test_deezer_fallback_actually_occurred(deezer_client):
    """Integration: track 77874822 has no FLAC — verify fallback to MP3_320."""
    downloadable = arun(deezer_client.get_downloadable("77874822", quality=2))

    assert downloadable.quality == 1, "Should have fallen back to MP3_320 when FLAC unavailable"
    assert downloadable.url.startswith("https://")
    assert downloadable._size > 0, "Downloadable should have a valid file size"
    assert downloadable.extension == "mp3", "MP3_320 should have .mp3 extension"


def test_deezer_downloadable_size_never_issues_head():
    """DeezerDownloadable.size() returns _size (or 0) without a HEAD request."""
    from streamrip.client.downloadable import DeezerDownloadable

    session = Mock()
    session.head = Mock(side_effect=AssertionError("size() must not issue a HEAD"))
    info = {
        "quality": 2,
        "id": "12345",
        "quality_to_size": [0, 0, 0],  # old-catalog: _size becomes None
        "url": "https://cdnt-stream.dzcdn.net/media/1/track.flac",
    }
    dl = DeezerDownloadable(session, info)
    assert dl._size is None
    assert arun(dl.size()) == 0
    session.head.assert_not_called()
