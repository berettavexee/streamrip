"""Tests for streamrip/media/album.py (Album and PendingAlbum)."""

from unittest.mock import AsyncMock, MagicMock, patch

from streamrip.exceptions import NonStreamableError
from streamrip.media.album import Album, PendingAlbum
from streamrip.media.media import DownloadStats

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pending_track(resolve_result=None):
    pt = MagicMock()
    pt.resolve = AsyncMock(return_value=resolve_result)
    return pt


def _track_mock():
    t = MagicMock()
    t.rip = AsyncMock()
    return t


def _album(tracks=None):
    meta = MagicMock()
    meta.album = "Test Album"
    return Album(
        meta=meta,
        tracks=tracks or [],
        config=MagicMock(),
        folder="/tmp/album",
        db=MagicMock(),
    )


def _pending_album(source="deezer"):
    client = MagicMock()
    client.source = source
    client.get_metadata = AsyncMock(return_value={"title": "Test"})
    client.session = MagicMock()
    config = MagicMock()
    config.session.downloads.folder = "/downloads"
    config.session.downloads.source_subdirectories = False
    config.session.filepaths.folder_format = "{albumartist}/{album}"
    config.session.filepaths.restrict_characters = False
    config.session.artwork = MagicMock()
    config.session.get_source.return_value.quality = 2
    return PendingAlbum(id="42", client=client, config=config, db=MagicMock())


# ---------------------------------------------------------------------------
# Album.preprocess / postprocess
# ---------------------------------------------------------------------------


async def test_album_preprocess_adds_title(mocker):
    mock_add = mocker.patch("streamrip.media.album.progress.add_title")
    album = _album()
    await album.preprocess()
    mock_add.assert_called_once_with("Test Album")


async def test_album_postprocess_removes_title(mocker):
    mock_remove = mocker.patch("streamrip.media.album.progress.remove_title")
    album = _album()
    await album.postprocess()
    mock_remove.assert_called_once_with("Test Album")


# ---------------------------------------------------------------------------
# Album.download
# ---------------------------------------------------------------------------


async def test_album_download_rips_resolved_tracks():
    track = _track_mock()
    album = _album([_pending_track(track)])
    stats = DownloadStats()

    await album.download(stats)

    track.rip.assert_awaited_once_with(stats)


async def test_album_download_skips_none_resolved_track():
    album = _album([_pending_track(None)])
    await album.download()  # must not raise


async def test_album_download_catches_rip_exception(caplog):
    track = _track_mock()
    track.rip = AsyncMock(side_effect=RuntimeError("disk full"))
    album = _album([_pending_track(track)])

    await album.download()  # must not raise

    assert "Error downloading track" in caplog.text


async def test_album_download_processes_all_tracks():
    tracks = [_track_mock() for _ in range(5)]
    album = _album([_pending_track(t) for t in tracks])

    await album.download()

    for t in tracks:
        t.rip.assert_awaited_once()


async def test_album_download_empty_tracklist():
    await _album([]).download()  # must not raise


# ---------------------------------------------------------------------------
# PendingAlbum.resolve — happy path
# ---------------------------------------------------------------------------


async def test_pending_album_resolve_returns_album():
    pa = _pending_album()
    meta = MagicMock()
    meta.covers = MagicMock()
    meta.info.quality = 2
    meta.format_folder_path.return_value = "Artist/Album"

    with (
        patch("streamrip.media.album.AlbumMetadata.from_album_resp", return_value=meta),
        patch("streamrip.media.album.get_album_track_ids", return_value=["t1", "t2"]),
        patch(
            "streamrip.media.album.download_artwork",
            new=AsyncMock(return_value=("/cover.jpg", None)),
        ),
        patch("streamrip.media.album.os.makedirs"),
        patch("streamrip.media.album.PendingTrack") as mock_pt,
        patch("streamrip.media.album.clean_filepath", side_effect=lambda p, _: p),
    ):
        result = await pa.resolve()

    assert isinstance(result, Album)
    assert mock_pt.call_count == 2


async def test_pending_album_resolve_passes_embed_cover_to_tracks():
    pa = _pending_album()
    meta = MagicMock()
    meta.covers = MagicMock()
    meta.info.quality = 2
    meta.format_folder_path.return_value = "Artist/Album"

    with (
        patch("streamrip.media.album.AlbumMetadata.from_album_resp", return_value=meta),
        patch("streamrip.media.album.get_album_track_ids", return_value=["t1"]),
        patch(
            "streamrip.media.album.download_artwork",
            new=AsyncMock(return_value=("/embed.jpg", None)),
        ),
        patch("streamrip.media.album.os.makedirs"),
        patch("streamrip.media.album.PendingTrack") as mock_pt,
        patch("streamrip.media.album.clean_filepath", side_effect=lambda p, _: p),
    ):
        await pa.resolve()

    _, kwargs = mock_pt.call_args
    assert kwargs["cover_path"] == "/embed.jpg"


# ---------------------------------------------------------------------------
# PendingAlbum.resolve — error branches
# ---------------------------------------------------------------------------


async def test_pending_album_resolve_returns_none_on_non_streamable():
    pa = _pending_album()
    pa.client.get_metadata = AsyncMock(side_effect=NonStreamableError("geo"))

    result = await pa.resolve()

    assert result is None


async def test_pending_album_resolve_returns_none_on_metadata_exception():
    pa = _pending_album()

    with patch(
        "streamrip.media.album.AlbumMetadata.from_album_resp",
        side_effect=ValueError("bad resp"),
    ):
        result = await pa.resolve()

    assert result is None


async def test_pending_album_resolve_returns_none_when_meta_is_none():
    pa = _pending_album()

    with patch(
        "streamrip.media.album.AlbumMetadata.from_album_resp", return_value=None
    ):
        result = await pa.resolve()

    assert result is None


# ---------------------------------------------------------------------------
# PendingAlbum._album_folder
# ---------------------------------------------------------------------------


def test_album_folder_no_subdirectory():
    pa = _pending_album()
    pa.config.session.downloads.source_subdirectories = False
    pa.config.session.filepaths.folder_format = "{albumartist}/{album}"
    pa.config.session.filepaths.restrict_characters = False
    pa.config.session.get_source.return_value.quality = 2

    meta = MagicMock()
    meta.info.quality = 2
    meta.format_folder_path.return_value = "Daft Punk/Discovery"

    with patch("streamrip.media.album.clean_filepath", side_effect=lambda p, _: p):
        result = pa._album_folder("/downloads", meta)

    assert result == "/downloads/Daft Punk/Discovery"


def test_album_folder_with_source_subdirectory():
    pa = _pending_album(source="qobuz")
    pa.config.session.downloads.source_subdirectories = True
    pa.config.session.filepaths.folder_format = "{albumartist}/{album}"
    pa.config.session.filepaths.restrict_characters = False
    pa.config.session.get_source.return_value.quality = 2

    meta = MagicMock()
    meta.info.quality = 2
    meta.format_folder_path.return_value = "Daft Punk/Discovery"

    with patch("streamrip.media.album.clean_filepath", side_effect=lambda p, _: p):
        result = pa._album_folder("/downloads", meta)

    assert result == "/downloads/Qobuz/Daft Punk/Discovery"


def test_album_folder_clips_quality_to_meta():
    """effective_quality = min(configured, meta.info.quality)."""
    pa = _pending_album()
    pa.config.session.downloads.source_subdirectories = False
    pa.config.session.filepaths.restrict_characters = False
    pa.config.session.get_source.return_value.quality = 5  # higher than available

    meta = MagicMock()
    meta.info.quality = 1  # only 320k available
    meta.format_folder_path.return_value = "Artist/Album"

    with patch("streamrip.media.album.clean_filepath", side_effect=lambda p, _: p):
        pa._album_folder("/dl", meta)

    # effective_quality passed to format_folder_path must be min(5, 1) = 1
    meta.format_folder_path.assert_called_once_with(
        pa.config.session.filepaths.folder_format, 1
    )


# ---------------------------------------------------------------------------
# Media.rip (inherited by Album)
# ---------------------------------------------------------------------------


async def test_album_rip_calls_lifecycle():
    """album.rip() goes through Media.rip() → preprocess → download → postprocess."""
    album = _album()
    with (
        patch.object(album, "preprocess", new=AsyncMock()) as mock_pre,
        patch.object(album, "download", new=AsyncMock()) as mock_dl,
        patch.object(album, "postprocess", new=AsyncMock()) as mock_post,
    ):
        await album.rip()
    mock_pre.assert_awaited_once()
    mock_dl.assert_awaited_once()
    mock_post.assert_awaited_once()
