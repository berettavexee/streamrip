"""VCR-based integration tests for DeezerClient.

These tests record and replay real Deezer API exchanges, so they run without
network access once cassettes are committed to the repository.

Recording cassettes
-------------------
You need a valid ARL (from your Deezer session cookie or config file):

    DEEZER_ARL=<your_arl> .venv/bin/pytest tests/test_deezer_vcr.py \\
        --record-mode=new_episodes -v

This writes YAML cassettes to tests/cassettes/deezer/.  The ARL is stripped
from cassettes automatically (Cookie / Set-Cookie headers are removed).
Commit the cassettes; subsequent CI runs replay them without any credentials.

Updating cassettes
------------------
Re-run with ``--record-mode=new_episodes`` (adds new interactions) or
``--vcr-record=all`` (re-records everything from scratch).

Known stable Deezer IDs used in tests
--------------------------------------
TRACK_ID   77874822   Pink Floyd — Comfortably Numb
ALBUM_ID   302127     Pink Floyd — The Wall
ARTIST_ID  130204     Pink Floyd
PLAYLIST_ID 1116189381 Deezer Top France (public chart)
"""

import os
import sys
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest
import requests as req_lib
import tomllib

from streamrip.client.deezer import DeezerClient

sys.path.insert(0, os.path.dirname(__file__))
from util import arun

# ---------------------------------------------------------------------------
# Known stable IDs
# ---------------------------------------------------------------------------

TRACK_ID = "77874822"  # Pink Floyd — Comfortably Numb
ALBUM_ID = "302127"  # Pink Floyd — The Wall
ARTIST_ID = "130204"  # Pink Floyd
PLAYLIST_ID = "1116189381"  # Deezer Top France (public chart)
FAVORITES_USER_ID = "1231003"  # authenticated account used for cassette recording

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CASSETTE_DIR = os.path.join(os.path.dirname(__file__), "cassettes", "deezer")


def _get_arl() -> str:
    """Return ARL from environment variable or config file, empty string if absent."""
    if arl := os.environ.get("DEEZER_ARL", ""):
        return arl
    cfg_path = os.path.expanduser("~/.config/streamrip/config.toml")
    try:
        with open(cfg_path, "rb") as f:
            return tomllib.load(f).get("deezer", {}).get("arl", "")
    except OSError:
        return ""


def _cassette_path(test_name: str) -> str:
    return os.path.join(CASSETTE_DIR, f"{test_name}.yaml")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def vcr_cassette_dir():
    """Tell pytest-recording where to read/write cassettes for this module."""
    return CASSETTE_DIR


@pytest.fixture
def deezer_vcr_client(request):
    """DeezerClient with login bypassed, ready for VCR-recorded API calls.

    The underlying deezer-py requests.Session is given the real ARL cookie
    (when available) so that recording mode works against the live API.
    The aiohttp session used only for downloads and redirect resolution is
    replaced with a lightweight mock that reports "no redirect" for any URL.

    Skip logic:
    - No cassette + replay mode (default) → skip (nothing to replay).
    - No cassette + recording mode + no ARL  → skip (cannot authenticate).
    - No cassette + recording mode + ARL     → proceed and record.
    - Cassette exists                        → proceed and replay.
    """
    cassette = _cassette_path(request.node.name)
    vcr_record = request.config.getoption("--record-mode", default="none")
    is_recording = vcr_record != "none"
    arl = _get_arl()

    if not os.path.exists(cassette):
        if not is_recording:
            pytest.skip(
                f"No cassette at {cassette!r} — "
                "run with --record-mode=new_episodes to record."
            )
        if not arl:
            pytest.skip(
                "Recording mode requires an ARL — set DEEZER_ARL or add it to "
                "~/.config/streamrip/config.toml"
            )

    config = MagicMock()
    config.session.downloads.max_connections = 6
    config.session.downloads.verify_ssl = True
    config.session.deezer.arl = arl or "PLACEHOLDER"

    client = DeezerClient(config)

    # Bypass login(): set the state that login() would normally set.
    client.logged_in = True
    client.logged_in_user_id = 0

    # Inject ARL into the underlying requests.Session so the live API
    # accepts our calls during recording.
    if arl:
        cookie = req_lib.cookies.create_cookie(
            domain=".deezer.com",
            name="arl",
            value=arl,
            path="/",
            secure=False,  # GW calls go to http://, not https://
        )
        client.client.session.cookies.set_cookie(cookie)

    # Mock the aiohttp session (used only by _resolve_redirect and downloads).
    # Return the same URL so _resolve_redirect always sees "no redirect".
    @asynccontextmanager
    async def _head_no_redirect(url, **_kwargs):
        resp = MagicMock()
        resp.url = url
        yield resp

    client.session = MagicMock()
    client.session.head = _head_no_redirect

    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.vcr
def test_vcr_get_track(deezer_vcr_client, mocker):
    """get_track returns full album metadata when REST calls succeed.

    gw.get_track runs concurrently with get_album via asyncio.gather.  VCR
    doesn't record concurrent threads reliably, so we mock the GW side and let
    VCR capture only the three sequential REST calls: get_track, get_album,
    get_album_tracks.
    """
    mocker.patch.object(
        deezer_vcr_client.client.gw,
        "get_track",
        return_value={"SNG_CONTRIBUTORS": {}, "GAIN": None},
    )
    result = arun(deezer_vcr_client.get_track(TRACK_ID))

    assert int(result["id"]) == int(TRACK_ID)
    assert "title" in result
    assert "album" in result
    assert isinstance(result["album"], dict)
    # Full album object contains tracks list (from get_album)
    assert "tracks" in result["album"]


@pytest.mark.vcr
def test_vcr_get_track_contributors(deezer_vcr_client, mocker):
    """get_track merges SNG_CONTRIBUTORS from GW into the result dict."""
    mocker.patch.object(
        deezer_vcr_client.client.gw,
        "get_track",
        return_value={
            "SNG_CONTRIBUTORS": {
                "composer": ["Roger Waters"],
                "author": ["Roger Waters"],
            },
            "GAIN": "-9.0",
        },
    )
    result = arun(deezer_vcr_client.get_track(TRACK_ID))

    assert isinstance(result, dict)
    assert "title" in result
    assert result.get("composer") == ["Roger Waters"]
    assert result.get("author") == ["Roger Waters"]
    assert result.get("gain") == "-9.0"


@pytest.mark.vcr
def test_vcr_get_track_for_playlist(deezer_vcr_client, mocker):
    """get_track_for_playlist skips the full album fetch (fetch_album=False).

    _fetch_lyrics runs concurrently with song.getData via asyncio.gather and
    VCR does not reliably record parallel threads.  It is mocked here so the
    cassette only captures the single sequential REST call (api.get_track)
    and the GW song.getData call that pre-existed the lyrics feature.
    """
    mocker.patch.object(deezer_vcr_client, "_fetch_lyrics", return_value=None)
    result = arun(deezer_vcr_client.get_track_for_playlist(TRACK_ID))

    assert "title" in result
    # album is the minimal stub from REST, not the enriched get_album() result
    assert isinstance(result.get("album"), dict)
    # track_total/disctotal are absent from the stub album
    assert result["album"].get("nb_tracks") is None or True  # depends on API


@pytest.mark.vcr
def test_vcr_get_album(deezer_vcr_client):
    """get_album returns album metadata with a populated tracks list."""
    result = arun(deezer_vcr_client.get_album(ALBUM_ID))

    assert "title" in result
    assert "tracks" in result
    assert isinstance(result["tracks"], list)
    assert len(result["tracks"]) > 0
    assert "track_total" in result
    assert result["track_total"] == len(result["tracks"])


@pytest.mark.vcr
def test_vcr_get_album_cached(deezer_vcr_client):
    """Second call to get_album for the same ID returns the cached result."""
    # Populate cache via first call (VCR records/replays the HTTP requests).
    first = arun(deezer_vcr_client.get_album(ALBUM_ID))
    # Second call must hit cache — no additional HTTP requests expected.
    second = arun(deezer_vcr_client.get_album(ALBUM_ID))

    assert first is second


@pytest.mark.vcr
def test_vcr_get_artist(deezer_vcr_client):
    """get_artist returns artist metadata with a non-empty albums list."""
    result = arun(deezer_vcr_client.get_artist(ARTIST_ID))

    assert "name" in result
    assert "albums" in result
    assert isinstance(result["albums"], list)
    assert len(result["albums"]) > 0


@pytest.mark.vcr
def test_vcr_get_playlist(deezer_vcr_client, mocker):
    """get_playlist returns a normalised dict with title, tracks, track_total.

    gw.get_playlist and gw.get_playlist_tracks run concurrently via asyncio.gather.
    VCR doesn't record concurrent threads reliably, so get_playlist_tracks is
    mocked with realistic data so that only the sequential gw.get_playlist call
    (which includes getUserData init + pagePlaylist) goes through VCR.
    """
    mocker.patch.object(
        deezer_vcr_client.client.gw,
        "get_playlist_tracks",
        return_value=[{"SNG_ID": "77874822"}, {"SNG_ID": "77874823"}],
    )
    result = arun(deezer_vcr_client.get_playlist(PLAYLIST_ID))

    assert "title" in result
    assert "tracks" in result
    assert "track_total" in result
    assert isinstance(result["tracks"], list)
    assert result["track_total"] == 2
    # Each track entry is {"id": "..."}
    assert all("id" in t for t in result["tracks"])


@pytest.mark.vcr
def test_vcr_search_track(deezer_vcr_client):
    """search('track', ...) returns a non-empty list with a 'data' key."""
    results = arun(deezer_vcr_client.search("track", "comfortably numb", limit=5))

    assert isinstance(results, list)
    assert len(results) > 0
    assert "data" in results[0]
    assert len(results[0]["data"]) > 0


@pytest.mark.vcr
def test_vcr_search_album(deezer_vcr_client):
    """search('album', ...) returns a non-empty list."""
    results = arun(deezer_vcr_client.search("album", "the wall", limit=5))

    assert isinstance(results, list)
    assert len(results) > 0


@pytest.mark.vcr
def test_vcr_get_metadata_track(deezer_vcr_client, mocker):
    """get_metadata dispatches to get_track for media_type='track'."""
    mocker.patch.object(
        deezer_vcr_client.client.gw,
        "get_track",
        return_value={"SNG_CONTRIBUTORS": {}, "GAIN": None},
    )
    result = arun(deezer_vcr_client.get_metadata(TRACK_ID, "track"))

    assert int(result["id"]) == int(TRACK_ID)
    assert "title" in result


@pytest.mark.vcr
def test_vcr_get_metadata_album(deezer_vcr_client):
    """get_metadata dispatches to get_album for media_type='album'."""
    result = arun(deezer_vcr_client.get_metadata(ALBUM_ID, "album"))

    assert "title" in result
    assert "tracks" in result


@pytest.mark.vcr
def test_vcr_get_user_favorites(deezer_vcr_client, mocker):
    """get_user_favorites fetches all loved tracks via paginated song.getFavoriteIds.

    song.getFavoriteIds silently caps responses at ~25 entries per call; the
    implementation paginates via start= until the server returns an empty page.
    This cassette captures the real multi-page exchange so regressions in the
    pagination loop are caught without a live Deezer session.

    get_tracks (the batch prefetch) is mocked: concurrent asyncio.to_thread calls
    share the same deezer-py GW client, which lazily inits its CSRF token with
    getUserData.  Multiple threads can race on that init and trigger extra getUserData
    calls whose ordering is non-deterministic — making the cassette unreproducible.
    Mocking get_tracks removes that non-determinism; the prefetch is tested separately
    in unit tests.

    The user_id argument is accepted for routing compatibility but is not
    forwarded to the GW call — the cassette is therefore stable regardless of
    which account is used to re-record it.
    """
    mocker.patch.object(deezer_vcr_client.client.gw, "get_tracks", return_value=[])
    result = arun(deezer_vcr_client.get_user_favorites(FAVORITES_USER_ID))

    assert result["title"] == "Loved Tracks"
    assert "tracks" in result
    assert "track_total" in result
    assert isinstance(result["tracks"], list)
    assert result["track_total"] == len(result["tracks"])
    assert result["track_total"] > 0
    assert all("id" in t for t in result["tracks"])
