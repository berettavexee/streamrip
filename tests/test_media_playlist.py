"""Tests for streamrip/media/playlist.py."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from streamrip.exceptions import NonStreamableError
from streamrip.media.media import DownloadStats
from streamrip.media.playlist import (
    PendingLastfmPlaylist,
    PendingPlaylist,
    PendingPlaylistTrack,
    Playlist,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db(downloaded=False):
    db = MagicMock()
    db.downloaded.return_value = downloaded
    return db


def _client(source="deezer"):
    c = MagicMock()
    c.source = source
    c.session = MagicMock()
    c.get_track_for_playlist = AsyncMock(return_value={"id": "1", "title": "T"})

    # By default the served downloadable id matches the requested id (no geoblock
    # fallback), so resolve() doesn't take the metadata-refetch path.
    async def _mk_downloadable(item_id, _quality):
        d = MagicMock()
        d.id = str(item_id)
        return d

    c.get_downloadable = AsyncMock(side_effect=_mk_downloadable)
    c.get_metadata = AsyncMock(return_value={})
    c.search = AsyncMock(return_value=[])
    return c


def _config(renumber=False, set_to_album=False, progress_bars=False):
    cfg = MagicMock()
    cfg.session.metadata.renumber_playlist_tracks = renumber
    cfg.session.metadata.set_playlist_to_album = set_to_album
    cfg.session.cli.progress_bars = progress_bars
    cfg.session.downloads.folder = "/dl"
    cfg.session.artwork = MagicMock()
    cfg.session.downloads.verify_ssl = True
    cfg.session.get_source.return_value.quality = 2
    cfg.session.lastfm.min_score = 0.85
    cfg.session.lastfm.max_tracks = 50
    cfg.session.lastfm.api_key = ""
    return cfg


def _ppt(track_id="42", downloaded=False):
    return PendingPlaylistTrack(
        id=track_id,
        client=_client(),
        config=_config(),
        folder="/dl/playlist",
        playlist_name="My Playlist",
        position=1,
        db=_db(downloaded=downloaded),
    )


def _pending_track_mock(resolve_result=None):
    pt = MagicMock()
    pt.id = "x"
    pt.resolve = AsyncMock(return_value=resolve_result)
    return pt


def _track_mock():
    t = MagicMock()
    t.rip = AsyncMock()
    return t


def _playlist(tracks=None):
    return Playlist(
        name="Test Playlist",
        config=_config(),
        client=_client(),
        tracks=tracks or [],
    )


# ---------------------------------------------------------------------------
# Playlist.preprocess / postprocess
# ---------------------------------------------------------------------------


async def test_playlist_preprocess_adds_title(mocker):
    mock_add = mocker.patch("streamrip.media.playlist.progress.add_title")
    await _playlist().preprocess()
    mock_add.assert_called_once_with("Test Playlist")


async def test_playlist_postprocess_removes_title(mocker):
    mock_remove = mocker.patch("streamrip.media.playlist.progress.remove_title")
    await _playlist().postprocess()
    mock_remove.assert_called_once_with("Test Playlist")


# ---------------------------------------------------------------------------
# Playlist.download
# ---------------------------------------------------------------------------


async def test_playlist_download_empty_tracks():
    await _playlist([]).download()  # must not raise (early return)


async def test_playlist_download_rips_tracks():
    track = _track_mock()
    pt = _pending_track_mock(track)
    pl = _playlist([pt])
    await pl.download()
    track.rip.assert_awaited_once()


async def test_playlist_download_skips_none_resolved():
    pt = _pending_track_mock(None)
    pl = _playlist([pt])
    await pl.download()  # must not raise


async def test_playlist_download_catches_safe_resolve_exception(caplog):
    pt = MagicMock()
    pt.id = "bad"
    pt.resolve = AsyncMock(side_effect=RuntimeError("boom"))
    pl = _playlist([pt])
    await pl.download()
    assert "Error resolving track" in caplog.text


async def test_playlist_download_catches_rip_exception(caplog):
    track = _track_mock()
    track.rip = AsyncMock(side_effect=RuntimeError("disk full"))
    pt = _pending_track_mock(track)
    pl = _playlist([pt])
    await pl.download()
    assert "Error downloading track" in caplog.text


async def test_playlist_download_multiple_batches():
    """More than 20 tracks triggers multi-batch pipelining."""
    tracks = [_track_mock() for _ in range(25)]
    pts = [_pending_track_mock(t) for t in tracks]
    pl = _playlist(pts)
    await pl.download()
    for t in tracks:
        t.rip.assert_awaited_once()


async def test_playlist_download_passes_stats():
    track = _track_mock()
    stats = DownloadStats()
    pl = _playlist([_pending_track_mock(track)])
    await pl.download(stats)
    track.rip.assert_awaited_once_with(stats)


# ---------------------------------------------------------------------------
# PendingPlaylistTrack.resolve — skip if already downloaded
# ---------------------------------------------------------------------------


async def test_ppt_resolve_skips_if_downloaded():
    ppt = _ppt(downloaded=True)
    result = await ppt.resolve()
    assert result is None
    ppt.client.get_track_for_playlist.assert_not_called()


# ---------------------------------------------------------------------------
# PendingPlaylistTrack.resolve — error branches
# ---------------------------------------------------------------------------


async def test_ppt_resolve_returns_none_on_non_streamable():
    ppt = _ppt()
    ppt.client.get_track_for_playlist = AsyncMock(
        side_effect=NonStreamableError("geo")
    )
    result = await ppt.resolve()
    assert result is None


async def test_ppt_resolve_returns_none_when_album_is_none():
    ppt = _ppt()
    with patch("streamrip.media.playlist.AlbumMetadata.from_track_resp", return_value=None):
        result = await ppt.resolve()
    assert result is None
    ppt.db.set_failed.assert_called_once()


async def test_ppt_resolve_returns_none_when_track_meta_is_none():
    ppt = _ppt()
    with (
        patch("streamrip.media.playlist.AlbumMetadata.from_track_resp", return_value=MagicMock()),
        patch("streamrip.media.playlist.TrackMetadata.from_resp", return_value=None),
    ):
        result = await ppt.resolve()
    assert result is None
    ppt.db.set_failed.assert_called_once()


async def test_ppt_resolve_returns_none_on_download_non_streamable():
    ppt = _ppt()
    album_meta = MagicMock()
    track_meta = MagicMock()
    with (
        patch("streamrip.media.playlist.AlbumMetadata.from_track_resp", return_value=album_meta),
        patch("streamrip.media.playlist.TrackMetadata.from_resp", return_value=track_meta),
        patch(
            "streamrip.media.playlist.download_embed_cover",
            new=AsyncMock(side_effect=NonStreamableError("no download")),
        ),
    ):
        result = await ppt.resolve()
    assert result is None
    ppt.db.set_failed.assert_called_once()


# ---------------------------------------------------------------------------
# PendingPlaylistTrack.resolve — happy path + config flags
# ---------------------------------------------------------------------------


async def test_ppt_resolve_returns_track():
    ppt = _ppt()
    album_meta = MagicMock()
    track_meta = MagicMock()
    with (
        patch("streamrip.media.playlist.AlbumMetadata.from_track_resp", return_value=album_meta),
        patch("streamrip.media.playlist.TrackMetadata.from_resp", return_value=track_meta),
        patch("streamrip.media.playlist.download_embed_cover", new=AsyncMock(return_value="/cover.jpg")),
        patch("streamrip.media.playlist.Track") as mock_track,
    ):
        result = await ppt.resolve()

    assert result is mock_track.return_value


async def test_ppt_resolve_uses_fallback_metadata_on_geoblock():
    """When the served track id differs from the requested one (geoblock
    fallback), album/meta/cover are rebuilt from the served track — the requested
    track's cover is a placeholder for the geoblocked release."""
    client = _client()

    # get_downloadable returns a downloadable whose id is the fallback track.
    async def _served_fallback(_item_id, _quality):
        d = MagicMock()
        d.id = "999"  # differs from requested "42"
        return d
    client.get_downloadable = AsyncMock(side_effect=_served_fallback)

    orig_album, orig_meta = MagicMock(name="orig_album"), MagicMock(name="orig_meta")
    fb_album, fb_meta = MagicMock(name="fb_album"), MagicMock(name="fb_meta")
    album_by_resp = {"orig": orig_album, "fb": fb_album}

    # First get_track_for_playlist call = original ("42"), second = fallback ("999")
    client.get_track_for_playlist = AsyncMock(side_effect=[{"tag": "orig"}, {"tag": "fb"}])

    ppt = PendingPlaylistTrack(
        id="42", client=client, config=_config(),
        folder="/dl", playlist_name="PL", position=1, db=_db(),
    )

    with (
        patch("streamrip.media.playlist.AlbumMetadata.from_track_resp",
              side_effect=lambda resp, _src: album_by_resp[resp["tag"]]),
        patch("streamrip.media.playlist.TrackMetadata.from_resp",
              side_effect=lambda album, _src, resp: fb_meta if album is fb_album else orig_meta),
        patch("streamrip.media.playlist.download_embed_cover",
              new=AsyncMock(side_effect=["/orig_cover.jpg", "/fb_cover.jpg"])) as mock_cover,
        patch("streamrip.media.playlist.Track") as mock_track,
    ):
        await ppt.resolve()

    # The Track was built with the fallback metadata and fallback cover.
    args, _ = mock_track.call_args
    assert args[0] is fb_meta                 # meta
    assert args[4] == "/fb_cover.jpg"         # embedded_cover_path
    # Fallback metadata was fetched for the served id.
    client.get_track_for_playlist.assert_any_await("999")
    assert mock_cover.await_count == 2        # original + fallback cover


async def test_ppt_resolve_renumbers_track():
    cfg = _config(renumber=True)
    ppt = PendingPlaylistTrack(
        id="1", client=_client(), config=cfg,
        folder="/dl", playlist_name="PL", position=7, db=_db()
    )
    track_meta = MagicMock()
    with (
        patch("streamrip.media.playlist.AlbumMetadata.from_track_resp", return_value=MagicMock()),
        patch("streamrip.media.playlist.TrackMetadata.from_resp", return_value=track_meta),
        patch("streamrip.media.playlist.download_embed_cover", new=AsyncMock(return_value=None)),
        patch("streamrip.media.playlist.Track"),
    ):
        await ppt.resolve()

    assert track_meta.tracknumber == 7


async def test_ppt_resolve_sets_playlist_to_album():
    cfg = _config(set_to_album=True)
    ppt = PendingPlaylistTrack(
        id="1", client=_client(), config=cfg,
        folder="/dl", playlist_name="Best Of", position=1, db=_db()
    )
    album_meta = MagicMock()
    with (
        patch("streamrip.media.playlist.AlbumMetadata.from_track_resp", return_value=album_meta),
        patch("streamrip.media.playlist.TrackMetadata.from_resp", return_value=MagicMock()),
        patch("streamrip.media.playlist.download_embed_cover", new=AsyncMock(return_value=None)),
        patch("streamrip.media.playlist.Track"),
    ):
        await ppt.resolve()

    assert album_meta.album == "Best Of"


# ---------------------------------------------------------------------------
# PendingPlaylist.resolve
# ---------------------------------------------------------------------------


async def test_pending_playlist_resolve_returns_none_on_non_streamable():
    pp = PendingPlaylist(id="1", client=_client(), config=_config(), db=_db())
    pp.client.get_metadata = AsyncMock(side_effect=NonStreamableError("geo"))
    assert await pp.resolve() is None


async def test_pending_playlist_resolve_returns_none_on_meta_error():
    pp = PendingPlaylist(id="1", client=_client(), config=_config(), db=_db())
    with patch("streamrip.media.playlist.PlaylistMetadata.from_resp", side_effect=ValueError("bad")):
        assert await pp.resolve() is None


async def test_pending_playlist_resolve_returns_playlist():
    pp = PendingPlaylist(id="1", client=_client(), config=_config(), db=_db())
    meta = MagicMock()
    meta.name = "Hot Tracks"
    meta.ids.return_value = ["t1", "t2"]
    with (
        patch("streamrip.media.playlist.PlaylistMetadata.from_resp", return_value=meta),
        patch("streamrip.media.playlist.clean_filepath", side_effect=lambda x: x),
        patch("streamrip.media.playlist.clean_filename", side_effect=lambda x: x),
    ):
        result = await pp.resolve()

    assert isinstance(result, Playlist)
    assert result.name == "Hot Tracks"
    assert len(result.tracks) == 2


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# PendingLastfmPlaylist._make_query
# ---------------------------------------------------------------------------


def _lastfm_playlist(fallback=None):
    return PendingLastfmPlaylist(
        lastfm_url="https://www.last.fm/user/x/playlists/1",
        client=_client(),
        fallback_client=fallback,
        config=_config(),
        db=_db(),
    )


async def test_make_query_found_on_primary():
    pl = _lastfm_playlist()
    page = {"data": [{"id": "track-42", "title": "Song"}]}
    pl.client.search = AsyncMock(return_value=[page])

    result_id = MagicMock()
    result_id.id = "track-42"
    result_id.name = "Song"
    result_id.artist = "Artist"
    result_id.duration = None
    s = PendingLastfmPlaylist.Status(0, 0, 1)

    with patch("streamrip.media.playlist.SearchResults.from_pages") as mock_sr:
        mock_sr.return_value.results = [result_id]
        track_id, from_fallback = await pl._make_query("Song", "Artist", None, s, lambda: None)

    assert track_id == "track-42"
    assert from_fallback is False
    assert s.found == 1


async def test_make_query_not_found_no_fallback():
    pl = _lastfm_playlist(fallback=None)
    pl.client.search = AsyncMock(return_value=[])
    s = PendingLastfmPlaylist.Status(0, 0, 1)

    track_id, _from_fallback = await pl._make_query("Unknown Song", "Artist", None, s, lambda: None)

    assert track_id is None
    assert s.failed == 1


async def test_make_query_found_on_fallback():
    fallback = _client(source="qobuz")
    page = {"data": [{"id": "fb-99"}]}
    fallback.search = AsyncMock(return_value=[page])

    pl = _lastfm_playlist(fallback=fallback)
    pl.client.search = AsyncMock(return_value=[])

    result_id = MagicMock()
    result_id.id = "fb-99"
    result_id.name = "Song"
    result_id.artist = "Artist"
    result_id.duration = None
    s = PendingLastfmPlaylist.Status(0, 0, 1)

    with patch("streamrip.media.playlist.SearchResults.from_pages") as mock_sr:
        mock_sr.return_value.results = [result_id]
        track_id, from_fallback = await pl._make_query("Song", "Artist", None, s, lambda: None)

    assert track_id == "fb-99"
    assert from_fallback is True
    assert s.found == 1


async def test_make_query_not_found_with_fallback():
    fallback = _client(source="qobuz")
    fallback.search = AsyncMock(return_value=[])

    pl = _lastfm_playlist(fallback=fallback)
    pl.client.search = AsyncMock(return_value=[])

    s = PendingLastfmPlaylist.Status(0, 0, 1)
    track_id, _from_fallback = await pl._make_query("Unknown", "Whoever", None, s, lambda: None)

    assert track_id is None
    assert s.failed == 1


async def test_make_query_callback_always_called():
    pl = _lastfm_playlist()
    pl.client.search = AsyncMock(return_value=[])
    called = []
    s = PendingLastfmPlaylist.Status(0, 0, 1)

    await pl._make_query("anything", "Whoever", None, s, lambda: called.append(True))

    assert called == [True]


# ---------------------------------------------------------------------------
# PendingLastfmPlaylist.resolve — parse error
# ---------------------------------------------------------------------------


async def test_lastfm_resolve_returns_none_on_parse_error():
    pl = _lastfm_playlist()
    with patch.object(pl, "_parse_lastfm_playlist", side_effect=Exception("bad html")):
        result = await pl.resolve()
    assert result is None


# ---------------------------------------------------------------------------
# PendingLastfmPlaylist.resolve — happy path (no progress bars)
# ---------------------------------------------------------------------------


async def test_lastfm_resolve_returns_playlist():
    pl = _lastfm_playlist()
    titles_artists = [("Song A", "Artist A", None), ("Song B", "Artist B", None)]

    async def fake_parse(_url):
        return "Top Tracks", titles_artists

    async def fake_query(title, artist, duration, s, cb):
        s.found += 1
        cb()
        return "track-1", False

    with (
        patch.object(pl, "_parse_lastfm_playlist", side_effect=fake_parse),
        patch.object(pl, "_make_query", side_effect=fake_query),
        patch("streamrip.media.playlist.clean_filepath", side_effect=lambda x: x),
        patch("streamrip.media.playlist.clean_filename", side_effect=lambda x: x),
    ):
        result = await pl.resolve()

    assert isinstance(result, Playlist)
    assert result.name == "Top Tracks"
    assert len(result.tracks) == 2


async def test_lastfm_resolve_skips_none_results():
    pl = _lastfm_playlist()

    async def fake_parse(_url):
        return "Playlist", [("A", "X", None), ("B", "Y", None)]

    async def fake_query(title, artist, duration, s, cb):
        cb()
        return None, False  # not found

    with (
        patch.object(pl, "_parse_lastfm_playlist", side_effect=fake_parse),
        patch.object(pl, "_make_query", side_effect=fake_query),
        patch("streamrip.media.playlist.clean_filepath", side_effect=lambda x: x),
        patch("streamrip.media.playlist.clean_filename", side_effect=lambda x: x),
        patch("streamrip.media.playlist.os.makedirs"),
        patch("streamrip.media.playlist.asyncio.to_thread", new=AsyncMock()),
    ):
        result = await pl.resolve()

    assert isinstance(result, Playlist)
    assert len(result.tracks) == 0


async def test_lastfm_resolve_writes_unmatched_file():
    pl = _lastfm_playlist()

    async def fake_parse(_url):
        return "Playlist", [("Found Song", "Artist A", None), ("Lost Song", "Artist B", None)]

    async def fake_query(title, artist, duration, s, cb):
        cb()
        if title == "Found Song":
            s.found += 1
            return "id-1", False
        s.failed += 1
        return None, False

    async def fake_to_thread(fn, *args, **kwargs):
        # Execute the write function synchronously so we can inspect the result
        fn(*args, **kwargs)

    from unittest.mock import mock_open
    m = mock_open()

    with (
        patch.object(pl, "_parse_lastfm_playlist", side_effect=fake_parse),
        patch.object(pl, "_make_query", side_effect=fake_query),
        patch("streamrip.media.playlist.clean_filepath", side_effect=lambda x: x),
        patch("streamrip.media.playlist.clean_filename", side_effect=lambda x: x),
        patch("streamrip.media.playlist.os.makedirs"),
        patch("streamrip.media.playlist.asyncio.to_thread", side_effect=fake_to_thread),
        patch("builtins.open", m),
    ):
        result = await pl.resolve()

    assert isinstance(result, Playlist)
    assert len(result.tracks) == 1
    # Verify the file was opened for writing
    m.assert_called_once()
    open_path = m.call_args[0][0]
    assert open_path.endswith("unmatched.txt")
    # Verify the content written contains the unmatched track
    written_content = "".join(
        call.args[0] for call in m().write.call_args_list
    )
    assert "Lost Song" in written_content
    assert "Artist B" in written_content
    assert "Found Song" not in written_content


async def test_lastfm_resolve_uses_fallback_client():
    fallback = _client(source="qobuz")
    pl = _lastfm_playlist(fallback=fallback)

    async def fake_parse(_url):
        return "PL", [("Song", "Artist", None)]

    async def fake_query(title, artist, duration, s, cb):
        cb()
        return "id-1", True  # from_fallback=True

    with (
        patch.object(pl, "_parse_lastfm_playlist", side_effect=fake_parse),
        patch.object(pl, "_make_query", side_effect=fake_query),
        patch("streamrip.media.playlist.clean_filepath", side_effect=lambda x: x),
        patch("streamrip.media.playlist.clean_filename", side_effect=lambda x: x),
    ):
        result = await pl.resolve()

    assert result.tracks[0].client is fallback


# ---------------------------------------------------------------------------
# PendingLastfmPlaylist._parse_lastfm_playlist
# ---------------------------------------------------------------------------

_LASTFM_HTML_SINGLE_PAGE = """
<html>
<h1 class="playlisting-playlist-header-title">My Playlist</h1>
<a href="/track/1" title="Song One">Song One</a>
<a href="/artist/1" title="Artist One">Artist One</a>
<a href="/track/2" title="Song Two">Song Two</a>
<a href="/artist/2" title="Artist Two">Artist Two</a>
data-playlisting-entry-count="2"
</html>
"""


async def test_parse_lastfm_single_page(mocker):
    pl = _lastfm_playlist()

    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value=_LASTFM_HTML_SINGLE_PAGE)

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = MagicMock(return_value=mock_resp)

    with patch("streamrip.media.playlist.aiohttp.ClientSession", return_value=mock_session):
        with patch("streamrip.media.playlist.aiohttp.TCPConnector"):
            title, pairs = await pl._parse_lastfm_playlist("https://last.fm/x")

    assert title == "My Playlist"
    assert len(pairs) == 2
    assert pairs[0] == ("Song One", "Artist One", None)


async def test_parse_lastfm_raises_when_title_missing(mocker):
    pl = _lastfm_playlist()

    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value="<html>no title here</html>")

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = MagicMock(return_value=mock_resp)

    with patch("streamrip.media.playlist.aiohttp.ClientSession", return_value=mock_session):
        with patch("streamrip.media.playlist.aiohttp.TCPConnector"):
            with pytest.raises(Exception, match="Could not find playlist title"):
                await pl._parse_lastfm_playlist("https://last.fm/x")


_LASTFM_HTML_NO_COUNT = """
<html>
<h1 class="playlisting-playlist-header-title">No Count Playlist</h1>
<a href="/track/1" title="A Track">A Track</a>
<a href="/artist/1" title="An Artist">An Artist</a>
</html>
"""


async def test_parse_lastfm_raises_when_track_count_missing():
    pl = _lastfm_playlist()

    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value=_LASTFM_HTML_NO_COUNT)

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = MagicMock(return_value=mock_resp)

    with patch("streamrip.media.playlist.aiohttp.ClientSession", return_value=mock_session):
        with patch("streamrip.media.playlist.aiohttp.TCPConnector"):
            with pytest.raises(Exception, match="Could not find track count"):
                await pl._parse_lastfm_playlist("https://last.fm/x")


_LASTFM_HTML_MULTIPAGE = """
<html>
<h1 class="playlisting-playlist-header-title">Big Playlist</h1>
<a href="/track/1" title="Song One">Song One</a>
<a href="/artist/1" title="Artist One">Artist One</a>
data-playlisting-entry-count="60"
</html>
"""

_LASTFM_HTML_PAGE2 = """
<html>
<a href="/track/2" title="Song Two">Song Two</a>
<a href="/artist/2" title="Artist Two">Artist Two</a>
</html>
"""


async def test_parse_lastfm_multi_page():
    pl = _lastfm_playlist()

    call_count = 0

    async def fake_text(_encoding=None):
        nonlocal call_count
        call_count += 1
        return _LASTFM_HTML_MULTIPAGE if call_count == 1 else _LASTFM_HTML_PAGE2

    mock_resp = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_resp.status = 200
    mock_resp.text = fake_text

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = MagicMock(return_value=mock_resp)

    with patch("streamrip.media.playlist.aiohttp.ClientSession", return_value=mock_session):
        with patch("streamrip.media.playlist.aiohttp.TCPConnector"):
            title, pairs = await pl._parse_lastfm_playlist("https://last.fm/x")

    assert title == "Big Playlist"
    assert ("Song One", "Artist One", None) in pairs
    assert ("Song Two", "Artist Two", None) in pairs


def _mock_session_with_status(status: int):
    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_resp.status = status
    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = MagicMock(return_value=mock_resp)
    return mock_session


async def test_parse_lastfm_raises_on_404():
    pl = _lastfm_playlist()
    with patch("streamrip.media.playlist.aiohttp.ClientSession",
               return_value=_mock_session_with_status(404)):
        with patch("streamrip.media.playlist.aiohttp.TCPConnector"):
            with pytest.raises(Exception, match="HTTP 404"):
                await pl._parse_lastfm_playlist("https://last.fm/x")


async def test_parse_lastfm_raises_on_5xx():
    pl = _lastfm_playlist()
    with patch("streamrip.media.playlist.aiohttp.ClientSession",
               return_value=_mock_session_with_status(503)):
        with patch("streamrip.media.playlist.aiohttp.TCPConnector"):
            with pytest.raises(Exception, match="HTTP 503"):
                await pl._parse_lastfm_playlist("https://last.fm/x")


async def test_parse_lastfm_raises_on_bare_artist_page():
    """Bare artist page URL triggers a descriptive error suggesting /+tracks."""
    pl = _lastfm_playlist()
    for url in (
        "https://www.last.fm/music/And+One",
        "https://www.last.fm/music/And+One/",
    ):
        with pytest.raises(ValueError, match=r"/\+tracks"):
            await pl._parse_lastfm_playlist(url)


async def test_parse_lastfm_artist_tracks_url_not_caught_as_bare_page():
    """/+tracks URL must NOT trigger the bare-page error."""
    pl = _lastfm_playlist()
    with patch.object(
        pl,
        "_parse_lastfm_artist_top_tracks",
        new=AsyncMock(return_value=("Artist Top", [("Song", "Artist")])),
    ):
        title, _pairs = await pl._parse_lastfm_playlist(
            "https://www.last.fm/music/And+One/+tracks"
        )
    assert title == "Artist Top"


# ---------------------------------------------------------------------------
# PendingLastfmPlaylist._parse_lastfm_loved_tracks
# ---------------------------------------------------------------------------

_LASTFM_LOVED_RESPONSE_P1 = {
    "lovedtracks": {
        "track": [
            {"name": "Song A", "artist": {"name": "Artist A"}},
            {"name": "Song B", "artist": {"name": "Artist B"}},
        ],
        "@attr": {"user": "bob", "page": "1", "totalPages": "2", "perPage": "2", "total": "3"},
    }
}
_LASTFM_LOVED_RESPONSE_P2 = {
    "lovedtracks": {
        "track": [
            {"name": "Song C", "artist": {"name": "Artist C"}},
        ],
        "@attr": {"user": "bob", "page": "2", "totalPages": "2", "perPage": "2", "total": "3"},
    }
}


def _mock_loved_session(pages):
    """Return a mock aiohttp session that serves successive Last.fm API pages."""
    call_count = 0

    async def fake_json():
        nonlocal call_count
        result = pages[min(call_count, len(pages) - 1)]
        call_count += 1
        return result

    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_resp.status = 200
    mock_resp.json = fake_json

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = MagicMock(return_value=mock_resp)
    return mock_session


async def test_parse_lastfm_loved_tracks_dispatcher():
    """Loved-tracks URL routes to _parse_lastfm_loved_tracks."""
    pl = _lastfm_playlist()
    with patch.object(
        pl,
        "_parse_lastfm_loved_tracks",
        new=AsyncMock(return_value=("bob's Loved Tracks", [("S", "A", None)])),
    ) as mock_loved:
        title, pairs = await pl._parse_lastfm_playlist(
            "https://www.last.fm/user/bob/loved"
        )
    mock_loved.assert_awaited_once()
    assert title == "bob's Loved Tracks"
    assert pairs == [("S", "A", None)]


async def test_parse_lastfm_loved_tracks_single_page():
    pl = _lastfm_playlist()
    pl.config.session.lastfm.api_key = "key"
    pl.config.session.lastfm.max_tracks = 50

    single_page = {
        "lovedtracks": {
            "track": [
                {"name": "Song A", "artist": {"name": "Artist A"}},
            ],
            "@attr": {"user": "bob", "page": "1", "totalPages": "1", "perPage": "50", "total": "1"},
        }
    }

    with (
        patch("streamrip.media.playlist.aiohttp.ClientSession",
              return_value=_mock_loved_session([single_page])),
        patch("streamrip.media.playlist.aiohttp.TCPConnector"),
    ):
        title, tracks = await pl._parse_lastfm_loved_tracks(
            "https://www.last.fm/user/bob/loved"
        )

    assert title == "bob's Loved Tracks"
    assert tracks == [("Song A", "Artist A", None)]


async def test_parse_lastfm_loved_tracks_multi_page():
    pl = _lastfm_playlist()
    pl.config.session.lastfm.api_key = "key"
    pl.config.session.lastfm.max_tracks = 0  # no limit

    with (
        patch("streamrip.media.playlist.aiohttp.ClientSession",
              return_value=_mock_loved_session(
                  [_LASTFM_LOVED_RESPONSE_P1, _LASTFM_LOVED_RESPONSE_P2]
              )),
        patch("streamrip.media.playlist.aiohttp.TCPConnector"),
    ):
        title, tracks = await pl._parse_lastfm_loved_tracks(
            "https://www.last.fm/user/bob/loved"
        )

    assert title == "bob's Loved Tracks"
    assert len(tracks) == 3
    assert tracks[0] == ("Song A", "Artist A", None)
    assert tracks[2] == ("Song C", "Artist C", None)
    # Duration is always None for loved tracks
    assert all(dur is None for _, _, dur in tracks)


async def test_parse_lastfm_loved_tracks_respects_max_tracks():
    pl = _lastfm_playlist()
    pl.config.session.lastfm.api_key = "key"
    pl.config.session.lastfm.max_tracks = 1

    with (
        patch("streamrip.media.playlist.aiohttp.ClientSession",
              return_value=_mock_loved_session(
                  [_LASTFM_LOVED_RESPONSE_P1, _LASTFM_LOVED_RESPONSE_P2]
              )),
        patch("streamrip.media.playlist.aiohttp.TCPConnector"),
    ):
        _title, tracks = await pl._parse_lastfm_loved_tracks(
            "https://www.last.fm/user/bob/loved"
        )

    assert len(tracks) == 1


async def test_parse_lastfm_loved_tracks_requires_api_key():
    pl = _lastfm_playlist()
    pl.config.session.lastfm.api_key = ""

    with pytest.raises(Exception, match="API key"):
        await pl._parse_lastfm_loved_tracks("https://www.last.fm/user/bob/loved")


# ---------------------------------------------------------------------------
# PendingLastfmPlaylist.resolve — missing API key (integration)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.last.fm/user/bob/loved",
        "https://www.last.fm/user/bob/library/tracks",
        "https://www.last.fm/music/Some+Artist/+tracks",
    ],
)
async def test_resolve_returns_none_and_logs_error_when_api_key_missing(url, caplog):
    """resolve() must return None (not raise) and log a clear error when api_key is absent."""
    pl = PendingLastfmPlaylist(
        lastfm_url=url,
        client=_client(),
        fallback_client=None,
        config=_config(),  # api_key="" by default in _config()
        db=_db(),
    )
    import logging
    with caplog.at_level(logging.ERROR, logger="streamrip"):
        result = await pl.resolve()

    assert result is None
    assert any("API key" in r.message for r in caplog.records), (
        f"Expected 'API key' in error log, got: {[r.message for r in caplog.records]}"
    )


async def test_lastfm_resolve_with_progress_bars():
    pl = _lastfm_playlist()
    pl.config.session.cli.progress_bars = True

    async def fake_parse(_url):
        return "PL", [("Song", "Artist", None)]

    async def fake_query(title, artist, duration, s, cb):
        s.found += 1
        cb()
        return "id-1", False

    mock_prog = MagicMock()
    mock_prog.__enter__ = MagicMock(return_value=mock_prog)
    mock_prog.__exit__ = MagicMock(return_value=False)
    mock_prog.add_task = MagicMock(return_value=0)

    with (
        patch.object(pl, "_parse_lastfm_playlist", side_effect=fake_parse),
        patch.object(pl, "_make_query", side_effect=fake_query),
        patch("streamrip.media.playlist.clean_filepath", side_effect=lambda x: x),
        patch("streamrip.media.playlist.clean_filename", side_effect=lambda x: x),
        patch("streamrip.media.playlist.Progress", return_value=mock_prog),
    ):
        result = await pl.resolve()

    assert isinstance(result, Playlist)
    assert result.name == "PL"


# ---------------------------------------------------------------------------
# PendingLastfmPlaylist._make_query — _best_id edge cases
# ---------------------------------------------------------------------------


async def test_make_query_pages_returned_but_no_results():
    """_best_id returns None when SearchResults yields an empty list (line 392)."""
    pl = _lastfm_playlist(fallback=None)
    page = {"data": [{"id": "track-1"}]}
    pl.client.search = AsyncMock(return_value=[page])
    s = PendingLastfmPlaylist.Status(0, 0, 1)

    with patch("streamrip.media.playlist.SearchResults.from_pages") as mock_sr:
        mock_sr.return_value.results = []
        track_id, _from_fallback = await pl._make_query("Song", "Artist", None, s, lambda: None)

    assert track_id is None
    assert s.failed == 1


async def test_make_query_score_below_threshold(caplog):
    """_best_id rejects a low-scoring candidate and logs a WARNING (lines 408-413)."""
    pl = _lastfm_playlist(fallback=None)
    pl.config.session.lastfm.min_score = 0.95

    page = {"data": [{"id": "id-1"}]}
    pl.client.search = AsyncMock(return_value=[page])

    result = MagicMock()
    result.id = "id-1"
    result.name = "Completely Different Song"
    result.artist = "Wrong Artist"
    result.duration = None
    s = PendingLastfmPlaylist.Status(0, 0, 1)

    with patch("streamrip.media.playlist.SearchResults.from_pages") as mock_sr:
        mock_sr.return_value.results = [result]
        with patch("streamrip.media.playlist.score_similarity", return_value=0.20):
            with caplog.at_level(logging.WARNING, logger="streamrip"):
                track_id, _from_fallback = await pl._make_query("Song", "Artist", None, s, lambda: None)

    assert track_id is None
    assert s.failed == 1
    assert any("Rejecting match" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# PendingLastfmPlaylist._fetch_lastfm_api
# ---------------------------------------------------------------------------


async def test_fetch_lastfm_api_raises_on_http_error():
    """Non-200 HTTP response raises an Exception (line 519)."""
    pl = _lastfm_playlist()

    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_resp.status = 503

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)

    with pytest.raises(Exception, match="HTTP 503"):
        await pl._fetch_lastfm_api(mock_session, {"method": "user.getTopTracks"})


async def test_fetch_lastfm_api_raises_on_api_error():
    """API-level error field in JSON response raises an Exception (line 524)."""
    pl = _lastfm_playlist()

    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"error": 6, "message": "User not found"})

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)

    with pytest.raises(Exception, match=r"Last\.fm API error 6"):
        await pl._fetch_lastfm_api(mock_session, {"method": "user.getTopTracks"})


# ---------------------------------------------------------------------------
# PendingLastfmPlaylist._parse_lastfm_user_top_tracks
# ---------------------------------------------------------------------------

_LASTFM_USER_TOP_RESPONSE = {
    "toptracks": {
        "track": [
            {"name": "Song A", "artist": {"name": "Artist A"}, "duration": "245"},
            {"name": "Song B", "artist": {"name": "Artist B"}, "duration": "0"},
        ],
        "@attr": {"user": "alice", "page": "1", "totalPages": "1", "total": "2"},
    }
}


async def test_parse_lastfm_user_top_tracks_single_page():
    pl = _lastfm_playlist()
    pl.config.session.lastfm.api_key = "key"
    pl.config.session.lastfm.max_tracks = 50

    with (
        patch("streamrip.media.playlist.aiohttp.ClientSession",
              return_value=_mock_loved_session([_LASTFM_USER_TOP_RESPONSE])),
        patch("streamrip.media.playlist.aiohttp.TCPConnector"),
    ):
        title, tracks = await pl._parse_lastfm_user_top_tracks(
            "https://www.last.fm/user/alice/library/tracks"
        )

    assert title == "alice's Top Tracks (All Time)"
    assert len(tracks) == 2
    assert tracks[0] == ("Song A", "Artist A", 245)
    assert tracks[1] == ("Song B", "Artist B", None)  # "0" duration maps to None


async def test_parse_lastfm_user_top_tracks_date_preset():
    """date_preset query parameter maps to the correct period label in the title."""
    pl = _lastfm_playlist()
    pl.config.session.lastfm.api_key = "key"
    pl.config.session.lastfm.max_tracks = 50

    with (
        patch("streamrip.media.playlist.aiohttp.ClientSession",
              return_value=_mock_loved_session([_LASTFM_USER_TOP_RESPONSE])),
        patch("streamrip.media.playlist.aiohttp.TCPConnector"),
    ):
        title, _tracks = await pl._parse_lastfm_user_top_tracks(
            "https://www.last.fm/user/alice/library/tracks?date_preset=LAST_7_DAYS"
        )

    assert "Last 7 Days" in title


async def test_parse_lastfm_user_top_tracks_respects_max_tracks():
    pl = _lastfm_playlist()
    pl.config.session.lastfm.api_key = "key"
    pl.config.session.lastfm.max_tracks = 1

    with (
        patch("streamrip.media.playlist.aiohttp.ClientSession",
              return_value=_mock_loved_session([_LASTFM_USER_TOP_RESPONSE])),
        patch("streamrip.media.playlist.aiohttp.TCPConnector"),
    ):
        _title, tracks = await pl._parse_lastfm_user_top_tracks(
            "https://www.last.fm/user/alice/library/tracks"
        )

    assert len(tracks) == 1


async def test_parse_lastfm_user_top_tracks_invalid_url():
    """Defensive guard raises when called with a URL the regex can't match (line 549)."""
    pl = _lastfm_playlist()
    pl.config.session.lastfm.api_key = "key"

    with pytest.raises(Exception, match="Could not parse user library URL"):
        await pl._parse_lastfm_user_top_tracks("https://www.last.fm/music/Artist/+tracks")


_LASTFM_USER_TOP_P1 = {
    "toptracks": {
        "track": [
            {"name": "Song A", "artist": {"name": "Artist A"}, "duration": "bad_dur"},
        ],
        "@attr": {"page": "1", "totalPages": "2", "total": "2"},
    }
}
_LASTFM_USER_TOP_P2 = {
    "toptracks": {
        "track": [
            {"name": "Song B", "artist": {"name": "Artist B"}, "duration": "200"},
        ],
        "@attr": {"page": "2", "totalPages": "2", "total": "2"},
    }
}


async def test_parse_lastfm_user_top_tracks_multi_page_and_bad_duration():
    """Covers pagination (line 593) and unparseable duration falling back to None (lines 586-587)."""
    pl = _lastfm_playlist()
    pl.config.session.lastfm.api_key = "key"
    pl.config.session.lastfm.max_tracks = 0  # no limit

    with (
        patch("streamrip.media.playlist.aiohttp.ClientSession",
              return_value=_mock_loved_session([_LASTFM_USER_TOP_P1, _LASTFM_USER_TOP_P2])),
        patch("streamrip.media.playlist.aiohttp.TCPConnector"),
    ):
        _title, tracks = await pl._parse_lastfm_user_top_tracks(
            "https://www.last.fm/user/alice/library/tracks"
        )

    assert len(tracks) == 2
    assert tracks[0] == ("Song A", "Artist A", None)   # "bad_dur" → None
    assert tracks[1] == ("Song B", "Artist B", 200)


# ---------------------------------------------------------------------------
# PendingLastfmPlaylist._parse_lastfm_loved_tracks — invalid URL guard
# ---------------------------------------------------------------------------


async def test_parse_lastfm_loved_tracks_invalid_url():
    """Defensive guard raises when called with a URL the regex can't match (line 623)."""
    pl = _lastfm_playlist()
    pl.config.session.lastfm.api_key = "key"

    with pytest.raises(Exception, match="Could not parse loved tracks URL"):
        await pl._parse_lastfm_loved_tracks("https://www.last.fm/music/Artist/+tracks")


# ---------------------------------------------------------------------------
# PendingLastfmPlaylist._parse_lastfm_artist_top_tracks
# ---------------------------------------------------------------------------

_LASTFM_ARTIST_TOP_RESPONSE = {
    "toptracks": {
        "track": [
            {"name": "Top Song", "artist": {"name": "Metal Band"}, "duration": "300"},
        ],
        "@attr": {"artist": "Metal Band", "page": "1", "totalPages": "1", "total": "1"},
    }
}


async def test_parse_lastfm_artist_top_tracks_single_page():
    pl = _lastfm_playlist()
    pl.config.session.lastfm.api_key = "key"
    pl.config.session.lastfm.max_tracks = 50

    with (
        patch("streamrip.media.playlist.aiohttp.ClientSession",
              return_value=_mock_loved_session([_LASTFM_ARTIST_TOP_RESPONSE])),
        patch("streamrip.media.playlist.aiohttp.TCPConnector"),
    ):
        title, tracks = await pl._parse_lastfm_artist_top_tracks(
            "https://www.last.fm/music/Metal+Band/+tracks"
        )

    assert title == "Metal Band — Top Tracks"
    assert tracks == [("Top Song", "Metal Band", 300)]


async def test_parse_lastfm_artist_top_tracks_invalid_url():
    """Defensive guard raises when called with a URL the regex can't match (line 692)."""
    pl = _lastfm_playlist()
    pl.config.session.lastfm.api_key = "key"

    with pytest.raises(Exception, match="Could not parse artist tracks URL"):
        await pl._parse_lastfm_artist_top_tracks("https://www.last.fm/user/alice/library/tracks")


_LASTFM_ARTIST_MULTI_TRACKS = {
    "toptracks": {
        "track": [
            {"name": "Song 1", "artist": {"name": "Band"}, "duration": "300"},
            {"name": "Song 2", "artist": {"name": "Band"}, "duration": "300"},
        ],
        "@attr": {"page": "1", "totalPages": "1", "total": "2"},
    }
}


async def test_parse_lastfm_artist_top_tracks_max_tracks_inner_break():
    """max_tracks cap triggers the inner loop break (line 732)."""
    pl = _lastfm_playlist()
    pl.config.session.lastfm.api_key = "key"
    pl.config.session.lastfm.max_tracks = 1

    with (
        patch("streamrip.media.playlist.aiohttp.ClientSession",
              return_value=_mock_loved_session([_LASTFM_ARTIST_MULTI_TRACKS])),
        patch("streamrip.media.playlist.aiohttp.TCPConnector"),
    ):
        _title, tracks = await pl._parse_lastfm_artist_top_tracks(
            "https://www.last.fm/music/Band/+tracks"
        )

    assert len(tracks) == 1


_LASTFM_ARTIST_TOP_P1 = {
    "toptracks": {
        "track": [
            {"name": "Song 1", "artist": {"name": "Band"}, "duration": "not-a-number"},
        ],
        "@attr": {"page": "1", "totalPages": "2", "total": "2"},
    }
}
_LASTFM_ARTIST_TOP_P2 = {
    "toptracks": {
        "track": [
            {"name": "Song 2", "artist": {"name": "Band"}, "duration": "200"},
        ],
        "@attr": {"page": "2", "totalPages": "2", "total": "2"},
    }
}


async def test_parse_lastfm_artist_top_tracks_multi_page_and_bad_duration():
    """Covers pagination (line 735) and unparseable duration falling back to None (lines 728-729)."""
    pl = _lastfm_playlist()
    pl.config.session.lastfm.api_key = "key"
    pl.config.session.lastfm.max_tracks = 0  # no limit

    with (
        patch("streamrip.media.playlist.aiohttp.ClientSession",
              return_value=_mock_loved_session([_LASTFM_ARTIST_TOP_P1, _LASTFM_ARTIST_TOP_P2])),
        patch("streamrip.media.playlist.aiohttp.TCPConnector"),
    ):
        _title, tracks = await pl._parse_lastfm_artist_top_tracks(
            "https://www.last.fm/music/Band/+tracks"
        )

    assert len(tracks) == 2
    assert tracks[0] == ("Song 1", "Band", None)  # "not-a-number" → None
    assert tracks[1] == ("Song 2", "Band", 200)


async def test_parse_lastfm_artist_top_tracks_date_preset_warning(caplog):
    """date_preset on artist URL logs a warning (lines 696-700)."""
    pl = _lastfm_playlist()
    pl.config.session.lastfm.api_key = "key"
    pl.config.session.lastfm.max_tracks = 50

    with (
        patch("streamrip.media.playlist.aiohttp.ClientSession",
              return_value=_mock_loved_session([_LASTFM_ARTIST_TOP_RESPONSE])),
        patch("streamrip.media.playlist.aiohttp.TCPConnector"),
    ):
        with caplog.at_level(logging.WARNING, logger="streamrip"):
            await pl._parse_lastfm_artist_top_tracks(
                "https://www.last.fm/music/Metal+Band/+tracks?date_preset=LAST_7_DAYS"
            )

    assert any("date_preset" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# PendingLastfmPlaylist._parse_lastfm_playlist_html — empty track list warning
# ---------------------------------------------------------------------------

_LASTFM_HTML_EMPTY_TRACKS = """
<html>
<h1 class="playlisting-playlist-header-title">Empty Playlist</h1>
data-playlisting-entry-count="0"
</html>
"""


async def test_parse_lastfm_html_warns_when_no_tracks(caplog):
    """Warning logged when HTML parses but yields no track pairs (line 810)."""
    pl = _lastfm_playlist()

    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value=_LASTFM_HTML_EMPTY_TRACKS)

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = MagicMock(return_value=mock_resp)

    with patch("streamrip.media.playlist.aiohttp.ClientSession", return_value=mock_session):
        with patch("streamrip.media.playlist.aiohttp.TCPConnector"):
            with caplog.at_level(logging.WARNING, logger="streamrip"):
                title, pairs = await pl._parse_lastfm_playlist("https://last.fm/x")

    assert title == "Empty Playlist"
    assert pairs == []
    assert any("no tracks" in r.message for r in caplog.records)
