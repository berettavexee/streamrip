"""Tests for streamrip/media/artist.py (Artist and PendingArtist)."""

from unittest.mock import AsyncMock, MagicMock, patch

from streamrip.exceptions import NonStreamableError
from streamrip.media.artist import Artist, PendingArtist
from streamrip.media.media import DownloadStats

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_album(title="Album", albumartist="Artist", bit_depth=24, sampling_rate=96,
                explicit=False, n_tracks=2):
    a = MagicMock()
    a.meta.album = title
    a.meta.albumartist = albumartist
    a.meta.info.bit_depth = bit_depth
    a.meta.info.sampling_rate = sampling_rate
    a.meta.info.explicit = explicit
    a.tracks = [MagicMock()] * n_tracks
    a.rip = AsyncMock()
    return a


def _filter_conf(repeats=False, extras=False, features=False,
                 non_studio_albums=False, non_remaster=False):
    f = MagicMock()
    f.repeats = repeats
    f.extras = extras
    f.features = features
    f.non_studio_albums = non_studio_albums
    f.non_remaster = non_remaster
    return f


def _artist(albums=None, name="Artist"):
    config = MagicMock()
    config.session.qobuz_filters = _filter_conf()
    return Artist(
        name=name,
        albums=albums or [],
        client=MagicMock(),
        config=config,
    )


def _pending_album_mock(resolve_result=None):
    pa = MagicMock()
    pa.resolve = AsyncMock(return_value=resolve_result)
    return pa


def _pending_artist(source="deezer"):
    client = MagicMock()
    client.source = source
    client.get_metadata = AsyncMock(return_value={})
    return PendingArtist(id="7", client=client, config=MagicMock(), db=MagicMock())


# ---------------------------------------------------------------------------
# Artist.preprocess / postprocess
# ---------------------------------------------------------------------------


async def test_artist_preprocess_is_noop():
    await _artist().preprocess()


async def test_artist_postprocess_is_noop():
    await _artist().postprocess()


# ---------------------------------------------------------------------------
# Artist.download — dispatch
# ---------------------------------------------------------------------------


async def test_download_with_repeats_calls_resolve_then_download(mocker):
    artist = _artist()
    artist.config.session.qobuz_filters = _filter_conf(repeats=True)
    spy = mocker.patch.object(artist, "_resolve_then_download", new=AsyncMock())

    await artist.download()

    spy.assert_awaited_once()


async def test_download_without_repeats_calls_download_async(mocker):
    artist = _artist()
    artist.config.session.qobuz_filters = _filter_conf(repeats=False)
    spy = mocker.patch.object(artist, "_download_async", new=AsyncMock())

    await artist.download()

    spy.assert_awaited_once()


# ---------------------------------------------------------------------------
# Artist._resolve_then_download
# ---------------------------------------------------------------------------


async def test_resolve_then_download_rips_all_albums():
    album = _mock_album()
    pa = _pending_album_mock(album)
    artist = _artist([pa])
    filters = _filter_conf()

    await artist._resolve_then_download(filters)

    album.rip.assert_awaited_once_with(None)


async def test_resolve_then_download_skips_none_albums():
    pa = _pending_album_mock(None)
    artist = _artist([pa])
    await artist._resolve_then_download(_filter_conf())  # must not raise


async def test_resolve_then_download_passes_stats():
    album = _mock_album()
    stats = DownloadStats()
    artist = _artist([_pending_album_mock(album)])

    await artist._resolve_then_download(_filter_conf(), stats)

    album.rip.assert_awaited_once_with(stats)


# ---------------------------------------------------------------------------
# Artist._download_async
# ---------------------------------------------------------------------------


async def test_download_async_rips_passing_filter():
    album = _mock_album()
    pa = _pending_album_mock(album)
    artist = _artist([pa])

    await artist._download_async(_filter_conf())

    album.rip.assert_awaited_once_with(None)


async def test_download_async_skips_none_album():
    pa = _pending_album_mock(None)
    artist = _artist([pa])
    await artist._download_async(_filter_conf())  # must not raise


async def test_download_async_skips_filtered_out_album():
    # extras filter on — "Live" album must be filtered
    album = _mock_album(title="Tour Live")
    pa = _pending_album_mock(album)
    artist = _artist([pa])

    await artist._download_async(_filter_conf(extras=True))

    album.rip.assert_not_awaited()


async def test_download_async_passes_stats():
    album = _mock_album()
    stats = DownloadStats()
    artist = _artist([_pending_album_mock(album)])

    await artist._download_async(_filter_conf(), stats)

    album.rip.assert_awaited_once_with(stats)


# ---------------------------------------------------------------------------
# Artist._apply_filters
# ---------------------------------------------------------------------------


def test_apply_filters_no_filters_returns_all():
    artist = _artist()
    albums = [_mock_album(), _mock_album()]
    result = artist._apply_filters(albums, _filter_conf())
    assert result == albums


def test_apply_filters_repeats():
    artist = _artist()
    a1 = _mock_album("Discovery", bit_depth=24, sampling_rate=96)
    a2 = _mock_album("Discovery", bit_depth=16, sampling_rate=44)
    result = artist._apply_filters([a1, a2], _filter_conf(repeats=True))
    assert len(result) == 1
    assert result[0] is a1  # highest quality kept


def test_apply_filters_extras():
    artist = _artist()
    regular = _mock_album("Kind of Blue")
    deluxe = _mock_album("Kind of Blue (Deluxe)")
    result = artist._apply_filters([regular, deluxe], _filter_conf(extras=True))
    assert regular in result
    assert deluxe not in result


def test_apply_filters_features():
    artist = _artist(name="Miles Davis")
    own = _mock_album("Kind of Blue", albumartist="Miles Davis")
    feat = _mock_album("Walkin'", albumartist="Ahmad Jamal")
    result = artist._apply_filters([own, feat], _filter_conf(features=True))
    assert own in result
    assert feat not in result


def test_apply_filters_non_studio_albums():
    artist = _artist(name="Artist")
    studio = _mock_album("Studio Album", albumartist="Artist")
    various = _mock_album("Compilation", albumartist="Various Artists")
    result = artist._apply_filters([studio, various], _filter_conf(non_studio_albums=True))
    assert studio in result
    assert various not in result


def test_apply_filters_non_remaster():
    artist = _artist()
    remaster = _mock_album("Album (Remastered 2021)")
    original = _mock_album("Album")
    result = artist._apply_filters([remaster, original], _filter_conf(non_remaster=True))
    assert remaster in result
    assert original not in result


# ---------------------------------------------------------------------------
# Artist._non_albums
# ---------------------------------------------------------------------------


def test_non_albums_single_track_returns_false():
    artist = _artist()
    a = _mock_album(n_tracks=1)
    assert artist._non_albums(a) is False


def test_non_albums_multi_track_returns_true():
    artist = _artist()
    a = _mock_album(n_tracks=3)
    assert artist._non_albums(a) is True


# ---------------------------------------------------------------------------
# PendingArtist.resolve — happy path
# ---------------------------------------------------------------------------


async def test_pending_artist_resolve_returns_artist():
    resp = {"name": "Miles Davis", "albums": [{"id": "a1"}, {"id": "a2"}]}
    pa = _pending_artist()
    pa.client.get_metadata = AsyncMock(return_value=resp)

    with patch("streamrip.media.artist.PendingAlbum"):
        result = await pa.resolve()

    assert isinstance(result, Artist)
    assert result.name == "Miles Davis"
    assert len(result.albums) == 2


async def test_pending_artist_resolve_creates_pending_albums():
    resp = {"name": "Artist", "albums": [{"id": "x1"}, {"id": "x2"}]}
    pa = _pending_artist()
    pa.client.get_metadata = AsyncMock(return_value=resp)

    with patch("streamrip.media.artist.PendingAlbum") as mock_pa:
        mock_pa.side_effect = lambda *a, **kw: MagicMock()
        await pa.resolve()

    ids = [c.args[0] for c in mock_pa.call_args_list]
    assert ids == ["x1", "x2"]


# ---------------------------------------------------------------------------
# PendingArtist.resolve — error branches
# ---------------------------------------------------------------------------


async def test_pending_artist_resolve_returns_none_on_non_streamable():
    pa = _pending_artist()
    pa.client.get_metadata = AsyncMock(side_effect=NonStreamableError("geo"))

    result = await pa.resolve()

    assert result is None


async def test_pending_artist_resolve_returns_none_on_metadata_error():
    pa = _pending_artist(source="unknown")
    pa.client.get_metadata = AsyncMock(return_value={"name": "X", "albums": []})

    result = await pa.resolve()

    assert result is None
