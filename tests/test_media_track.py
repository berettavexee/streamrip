"""Tests for streamrip/media/track.py."""

import asyncio
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from streamrip.exceptions import NonStreamableError
from streamrip.media.media import DownloadStats
from streamrip.media.track import PendingSingle, PendingTrack, Track

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _downloadable(ext="flac", size=1000, source="deezer"):
    d = MagicMock()
    d.extension = ext
    d.size = AsyncMock(return_value=size)
    d.download = AsyncMock()
    d.source = source
    return d


def _track_meta(title="Song", tracknumber=1, discnumber=1):
    m = MagicMock()
    m.title = title
    m.tracknumber = tracknumber
    m.discnumber = discnumber
    m.format_track_path = MagicMock(return_value=f"{tracknumber:02d} - {title}")
    m.info = MagicMock()
    m.info.id = "track-42"
    return m


def _config(
    restrict_characters=False,
    truncate_to=0,
    progress_bars=False,
    conversion_enabled=False,
    disc_subdirectories=False,
    source_subdirectories=False,
    add_singles_to_folder=True,
):
    cfg = MagicMock()
    cfg.session.filepaths.track_format = "{tracknumber} - {title}"
    cfg.session.filepaths.restrict_characters = restrict_characters
    cfg.session.filepaths.truncate_to = truncate_to
    cfg.session.filepaths.folder_format = "{albumartist}/{album}"
    cfg.session.filepaths.add_singles_to_folder = add_singles_to_folder
    cfg.session.cli.progress_bars = progress_bars
    cfg.session.cli.dry_run = False
    cfg.session.conversion.enabled = conversion_enabled
    cfg.session.downloads.disc_subdirectories = disc_subdirectories
    cfg.session.downloads.source_subdirectories = source_subdirectories
    cfg.session.downloads.folder = "/dl"
    cfg.session.get_source.return_value.quality = 2
    return cfg


def _db(downloaded=False):
    db = MagicMock()
    db.downloaded.return_value = downloaded
    return db


def _track(is_single=False, cover_path=None, cfg=None):
    return Track(
        meta=_track_meta(),
        downloadable=_downloadable(),
        config=cfg or _config(),
        folder="/dl/album",
        cover_path=cover_path,
        db=_db(),
        is_single=is_single,
    )


def _async_cm():
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=None)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _sync_cm():
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=MagicMock())
    cm.__exit__ = MagicMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# Track._set_download_path
# ---------------------------------------------------------------------------


def test_set_download_path_basic():
    t = _track()
    t._set_download_path()
    assert t.download_path == "/dl/album/01 - Song.flac"


def test_set_download_path_truncates():
    cfg = _config(truncate_to=5)
    t = _track(cfg=cfg)
    t._set_download_path()
    name_no_ext = os.path.basename(t.download_path).rsplit(".", 1)[0]
    assert len(name_no_ext) <= 5


def test_set_download_path_no_truncate_when_zero():
    t = _track(cfg=_config(truncate_to=0))
    t.meta.format_track_path = MagicMock(return_value="Long Title Track Name")
    t._set_download_path()
    assert "Long Title Track Name" in t.download_path


# ---------------------------------------------------------------------------
# Track.preprocess
# ---------------------------------------------------------------------------


async def test_preprocess_creates_folder(tmp_path):
    t = _track()
    t.folder = str(tmp_path / "new_dir")
    with patch("streamrip.media.track.add_title") as mock_add:
        await t.preprocess()
    assert (tmp_path / "new_dir").is_dir()
    mock_add.assert_not_called()


async def test_preprocess_adds_title_when_single(tmp_path):
    t = _track(is_single=True)
    t.folder = str(tmp_path)
    with patch("streamrip.media.track.add_title") as mock_add:
        await t.preprocess()
    mock_add.assert_called_once_with(t.meta.title)


# ---------------------------------------------------------------------------
# Track.download
# ---------------------------------------------------------------------------


async def test_download_success_first_attempt():
    t = _track()
    t.download_path = "/dl/album/01 - Song.flac"
    with (
        patch(
            "streamrip.media.track.global_download_semaphore", return_value=_async_cm()
        ),
        patch("streamrip.media.track.get_progress_callback", return_value=_sync_cm()),
    ):
        await t.download()
    t.downloadable.download.assert_awaited_once()


async def test_download_records_queue_wait_and_transfer_separately():
    """The two phase timers must measure disjoint spans, not one another.

    They exist to answer whether a slow session is bandwidth-bound or bound by
    ``max_connections``; a timer that started before the semaphore would fold
    contention into the transfer figure and answer neither.
    """
    t = _track()
    t.download_path = "/dl/album/01 - Song.flac"

    async def slow_acquire():
        await asyncio.sleep(0.05)

    slow_cm = _async_cm()
    slow_cm.__aenter__ = AsyncMock(side_effect=slow_acquire)

    async def slow_download(*_args, **_kwargs):
        await asyncio.sleep(0.05)

    t.downloadable.download = AsyncMock(side_effect=slow_download)
    with (
        patch("streamrip.media.track.global_download_semaphore", return_value=slow_cm),
        patch("streamrip.media.track.get_progress_callback", return_value=_sync_cm()),
    ):
        await t.download()

    assert t._queue_wait >= 0.04
    assert t._download_time >= 0.04
    # The transfer timer starts after the slot is acquired, so it cannot have
    # absorbed the wait.
    assert t._download_time < t._queue_wait + 0.04


async def test_rip_logs_the_phase_breakdown(caplog, tmp_path):
    """rip() emits one DEBUG line per track splitting wait / transfer / postprocess."""
    t = _track()
    t.folder = str(tmp_path)
    t.postprocess = AsyncMock()
    with (
        caplog.at_level(logging.DEBUG, logger="streamrip"),
        patch(
            "streamrip.media.track.global_download_semaphore", return_value=_async_cm()
        ),
        patch("streamrip.media.track.get_progress_callback", return_value=_sync_cm()),
        patch("streamrip.media.track.advance_overall"),
    ):
        await t.rip()

    assert "Phase timing for 'Song'" in caplog.text
    assert "queue wait" in caplog.text
    assert "postprocess" in caplog.text


async def test_download_retries_on_first_failure(caplog):
    t = _track()
    t.download_path = "/dl/album/01 - Song.flac"
    t.downloadable.download = AsyncMock(side_effect=[RuntimeError("network"), None])
    with (
        patch(
            "streamrip.media.track.global_download_semaphore", return_value=_async_cm()
        ),
        patch("streamrip.media.track.get_progress_callback", return_value=_sync_cm()),
    ):
        await t.download()
    assert t.downloadable.download.await_count == 2
    assert "retrying" in caplog.text


async def test_download_marks_failed_after_two_failures(caplog):
    t = _track()
    t.download_path = "/dl/album/01 - Song.flac"
    t.downloadable.download = AsyncMock(side_effect=RuntimeError("bad"))
    with (
        patch(
            "streamrip.media.track.global_download_semaphore", return_value=_async_cm()
        ),
        patch("streamrip.media.track.get_progress_callback", return_value=_sync_cm()),
        pytest.raises(NonStreamableError, match="after 2 attempts"),
    ):
        await t.download()
    assert t.downloadable.download.await_count == 2
    assert "skipping" in caplog.text
    t.db.set_failed.assert_called_once()


async def test_download_removes_partial_file_after_persistent_failure(tmp_path):
    """Track.download() clears the download path once both attempts fail.

    A backstop: Downloadable._download already discards its own partial, so in
    production this finds nothing. The downloadable is mocked here, which is
    what leaves a file for it to clean up.
    """
    partial = tmp_path / "01 - Song.flac"
    partial.write_bytes(b"truncated")

    t = _track()
    t.download_path = str(partial)
    t.downloadable.download = AsyncMock(side_effect=RuntimeError("boom"))
    with (
        patch(
            "streamrip.media.track.global_download_semaphore", return_value=_async_cm()
        ),
        patch("streamrip.media.track.get_progress_callback", return_value=_sync_cm()),
        pytest.raises(NonStreamableError),
    ):
        await t.download()

    assert not partial.exists()


async def test_download_keeps_going_when_partial_cannot_be_removed(tmp_path, caplog):
    """A failed cleanup must not mask the download error that caused it."""
    partial = tmp_path / "01 - Song.flac"
    partial.write_bytes(b"truncated")

    t = _track()
    t.download_path = str(partial)
    t.downloadable.download = AsyncMock(side_effect=RuntimeError("boom"))
    with (
        patch(
            "streamrip.media.track.global_download_semaphore", return_value=_async_cm()
        ),
        patch("streamrip.media.track.get_progress_callback", return_value=_sync_cm()),
        patch("streamrip.media.track.os.remove", side_effect=OSError("read-only fs")),
        pytest.raises(NonStreamableError, match="after 2 attempts"),
    ):
        await t.download()

    assert "Could not remove partial download" in caplog.text


async def test_download_no_partial_to_remove_is_not_an_error():
    t = _track()
    t.download_path = "/dl/album/does-not-exist.flac"
    t.downloadable.download = AsyncMock(side_effect=RuntimeError("boom"))
    with (
        patch(
            "streamrip.media.track.global_download_semaphore", return_value=_async_cm()
        ),
        patch("streamrip.media.track.get_progress_callback", return_value=_sync_cm()),
        patch("streamrip.media.track.os.remove") as mock_remove,
        pytest.raises(NonStreamableError),
    ):
        await t.download()

    mock_remove.assert_not_called()


async def test_download_failure_clears_progress_title_when_single():
    """postprocess() normally clears it, but a failed download never gets there."""
    t = _track(is_single=True)
    t.download_path = "/dl/album/01 - Song.flac"
    t.downloadable.download = AsyncMock(side_effect=RuntimeError("boom"))
    with (
        patch(
            "streamrip.media.track.global_download_semaphore", return_value=_async_cm()
        ),
        patch("streamrip.media.track.get_progress_callback", return_value=_sync_cm()),
        patch("streamrip.media.track.remove_title") as mock_remove_title,
        pytest.raises(NonStreamableError),
    ):
        await t.download()

    mock_remove_title.assert_called_once_with(t.meta.title)


async def test_download_failure_does_not_clear_title_when_not_single():
    """Album tracks don't register a title of their own; the album owns it."""
    t = _track(is_single=False)
    t.download_path = "/dl/album/01 - Song.flac"
    t.downloadable.download = AsyncMock(side_effect=RuntimeError("boom"))
    with (
        patch(
            "streamrip.media.track.global_download_semaphore", return_value=_async_cm()
        ),
        patch("streamrip.media.track.get_progress_callback", return_value=_sync_cm()),
        patch("streamrip.media.track.remove_title") as mock_remove_title,
        pytest.raises(NonStreamableError),
    ):
        await t.download()

    mock_remove_title.assert_not_called()


async def test_rip_does_not_postprocess_when_download_fails():
    """A failed download must not reach tagging — postprocess() would otherwise
    tag a missing or truncated file and record it as downloaded."""
    t = _track()
    t.downloadable.download = AsyncMock(side_effect=RuntimeError("bad"))
    with (
        patch(
            "streamrip.media.track.global_download_semaphore", return_value=_async_cm()
        ),
        patch("streamrip.media.track.get_progress_callback", return_value=_sync_cm()),
        patch("streamrip.media.track.tag_file", new=AsyncMock()) as mock_tag,
        patch("streamrip.media.track.advance_overall"),
        patch("streamrip.media.track.os.makedirs"),
        pytest.raises(NonStreamableError),
    ):
        await t.rip()
    mock_tag.assert_not_awaited()
    t.db.set_downloaded.assert_not_called()


# ---------------------------------------------------------------------------
# Track.postprocess
# ---------------------------------------------------------------------------


def _integrity_ok():
    """Patch check_integrity to pass: postprocess() unit tests use a fake path."""
    return patch("streamrip.media.track.check_integrity", return_value=(True, ""))


async def test_postprocess_tags_file():
    t = _track()
    t.download_path = "/dl/album/01 - Song.flac"
    with (
        patch("streamrip.media.track.tag_file", new=AsyncMock()) as mock_tag,
        patch("streamrip.media.track.remove_title"),
        _integrity_ok(),
    ):
        await t.postprocess()
    mock_tag.assert_awaited_once_with(t.download_path, t.meta, t.cover_path)


async def test_postprocess_removes_title_when_single():
    t = _track(is_single=True)
    t.download_path = "/dl/album/01 - Song.flac"
    with (
        patch("streamrip.media.track.tag_file", new=AsyncMock()),
        patch("streamrip.media.track.remove_title") as mock_rm,
        _integrity_ok(),
    ):
        await t.postprocess()
    mock_rm.assert_called_once_with(t.meta.title)


async def test_postprocess_no_remove_title_when_not_single():
    t = _track(is_single=False)
    t.download_path = "/dl/album/01 - Song.flac"
    with (
        patch("streamrip.media.track.tag_file", new=AsyncMock()),
        patch("streamrip.media.track.remove_title") as mock_rm,
        _integrity_ok(),
    ):
        await t.postprocess()
    mock_rm.assert_not_called()


async def test_postprocess_marks_downloaded():
    t = _track()
    t.download_path = "/dl/album/01 - Song.flac"
    with (
        patch("streamrip.media.track.tag_file", new=AsyncMock()),
        patch("streamrip.media.track.remove_title"),
        _integrity_ok(),
    ):
        await t.postprocess()
    t.db.set_downloaded.assert_called_once_with(t.meta.info.id)


async def test_postprocess_logs_error_when_integrity_fails(caplog):
    t = _track()
    t.download_path = "/dl/album/01 - Song.flac"
    import logging

    with (
        patch("streamrip.media.track.tag_file", new=AsyncMock()),
        patch("streamrip.media.track.remove_title"),
        patch(
            "streamrip.media.track.check_integrity",
            return_value=(False, "effective bitrate 10 kbps is below minimum"),
        ),
        caplog.at_level(logging.ERROR, logger="streamrip"),
        pytest.raises(NonStreamableError, match="Integrity check failed"),
    ):
        await t.postprocess()
    assert any("Integrity check failed" in r.message for r in caplog.records)
    assert any("Song" in r.message for r in caplog.records)


async def test_postprocess_no_warning_when_integrity_ok():
    t = _track()
    t.download_path = "/dl/album/01 - Song.flac"
    with (
        patch("streamrip.media.track.tag_file", new=AsyncMock()),
        patch("streamrip.media.track.remove_title"),
        patch("streamrip.media.track.check_integrity", return_value=(True, "")),
    ):
        await t.postprocess()
    # db.set_downloaded must still be called even when integrity passes
    t.db.set_downloaded.assert_called_once()


async def test_postprocess_marks_failed_when_integrity_fails():
    """A truncated file must never be recorded as downloaded: the database would
    skip it on every subsequent run, leaving it corrupt in the library forever."""
    t = _track()
    t.download_path = "/dl/album/01 - Song.flac"
    with (
        patch("streamrip.media.track.tag_file", new=AsyncMock()),
        patch("streamrip.media.track.remove_title"),
        patch(
            "streamrip.media.track.check_integrity", return_value=(False, "truncated")
        ),
        pytest.raises(NonStreamableError),
    ):
        await t.postprocess()
    t.db.set_downloaded.assert_not_called()
    t.db.set_failed.assert_called_once_with(
        t.downloadable.source, "track", t.meta.info.id
    )


async def test_postprocess_runs_conversion_when_enabled():
    t = _track(cfg=_config(conversion_enabled=True))
    t.download_path = "/dl/album/01 - Song.flac"
    with (
        patch("streamrip.media.track.tag_file", new=AsyncMock()),
        patch("streamrip.media.track.remove_title"),
        patch.object(t, "_convert", new=AsyncMock()) as mock_cv,
        _integrity_ok(),
    ):
        await t.postprocess()
    mock_cv.assert_awaited_once()


async def test_postprocess_skips_conversion_when_disabled():
    t = _track(cfg=_config(conversion_enabled=False))
    t.download_path = "/dl/album/01 - Song.flac"
    with (
        patch("streamrip.media.track.tag_file", new=AsyncMock()),
        patch("streamrip.media.track.remove_title"),
        patch.object(t, "_convert", new=AsyncMock()) as mock_cv,
        _integrity_ok(),
    ):
        await t.postprocess()
    mock_cv.assert_not_awaited()


# ---------------------------------------------------------------------------
# Track._convert
# ---------------------------------------------------------------------------


async def test_convert_calls_engine_and_updates_path():
    cfg = _config(conversion_enabled=True)
    cfg.session.conversion.codec = "MP3"
    cfg.session.conversion.sampling_rate = 44100
    cfg.session.conversion.bit_depth = 16
    t = _track(cfg=cfg)
    t.download_path = "/dl/album/01 - Song.flac"

    engine = MagicMock()
    engine.convert = AsyncMock()
    engine.final_fn = "/dl/album/01 - Song.mp3"

    with (
        patch(
            "streamrip.media.track.converter.get",
            return_value=MagicMock(return_value=engine),
        ),
        patch("streamrip.media.track.tag_file", new=AsyncMock()) as mock_tag,
    ):
        await t._convert()

    engine.convert.assert_awaited_once()
    assert t.download_path == "/dl/album/01 - Song.mp3"
    # ffmpeg drops fields across a container change (ISRC and lyrics, at
    # least), so the converted file is tagged again.
    mock_tag.assert_awaited_once_with("/dl/album/01 - Song.mp3", t.meta, t.cover_path)


@pytest.mark.parametrize("ext", ["flac", "m4a", "mp3", "aiff", "aif"])
async def test_convert_retags_taggable_containers(ext):
    cfg = _config(conversion_enabled=True)
    cfg.session.conversion.codec = ext.upper()
    cfg.session.conversion.sampling_rate = 44100
    cfg.session.conversion.bit_depth = 16
    t = _track(cfg=cfg)
    t.download_path = "/dl/album/01 - Song.flac"

    engine = MagicMock()
    engine.convert = AsyncMock()
    engine.final_fn = f"/dl/album/01 - Song.{ext}"

    with (
        patch(
            "streamrip.media.track.converter.get",
            return_value=MagicMock(return_value=engine),
        ),
        patch("streamrip.media.track.tag_file", new=AsyncMock()) as mock_tag,
    ):
        await t._convert()

    mock_tag.assert_awaited_once()


@pytest.mark.parametrize("ext", ["opus", "ogg"])
async def test_convert_skips_retag_for_untaggable_containers(ext):
    """tag_file() raises on these containers, so _convert must not call it.

    They keep whatever metadata ffmpeg copied during the conversion.
    """
    cfg = _config(conversion_enabled=True)
    cfg.session.conversion.codec = ext.upper()
    cfg.session.conversion.sampling_rate = 44100
    cfg.session.conversion.bit_depth = 16
    t = _track(cfg=cfg)
    t.download_path = "/dl/album/01 - Song.flac"

    engine = MagicMock()
    engine.convert = AsyncMock()
    engine.final_fn = f"/dl/album/01 - Song.{ext}"

    with (
        patch(
            "streamrip.media.track.converter.get",
            return_value=MagicMock(return_value=engine),
        ),
        patch("streamrip.media.track.tag_file", new=AsyncMock()) as mock_tag,
    ):
        await t._convert()

    mock_tag.assert_not_awaited()


async def test_convert_retag_matches_extension_case_insensitively():
    """An uppercase extension must still be recognised as taggable."""
    cfg = _config(conversion_enabled=True)
    cfg.session.conversion.codec = "AIFF"
    cfg.session.conversion.sampling_rate = 44100
    cfg.session.conversion.bit_depth = 24
    t = _track(cfg=cfg)
    t.download_path = "/dl/album/01 - Song.flac"

    engine = MagicMock()
    engine.convert = AsyncMock()
    engine.final_fn = "/dl/album/01 - Song.AIFF"

    with (
        patch(
            "streamrip.media.track.converter.get",
            return_value=MagicMock(return_value=engine),
        ),
        patch("streamrip.media.track.tag_file", new=AsyncMock()) as mock_tag,
    ):
        await t._convert()

    mock_tag.assert_awaited_once()


# ---------------------------------------------------------------------------
# Track.rip
# ---------------------------------------------------------------------------


async def test_rip_calls_lifecycle_methods():
    t = _track()
    with (
        patch.object(t, "preprocess", new=AsyncMock()) as mock_pre,
        patch.object(t, "download", new=AsyncMock()) as mock_dl,
        patch.object(t, "postprocess", new=AsyncMock()) as mock_post,
    ):
        await t.rip()
    mock_pre.assert_awaited_once()
    mock_dl.assert_awaited_once()
    mock_post.assert_awaited_once()


async def test_rip_records_success_in_stats():
    t = _track()
    t.download_path = "/nonexistent/path.flac"
    stats = DownloadStats()
    with (
        patch.object(t, "preprocess", new=AsyncMock()),
        patch.object(t, "download", new=AsyncMock()),
        patch.object(t, "postprocess", new=AsyncMock()),
    ):
        await t.rip(stats)
    assert stats.tracks_downloaded == 1


async def test_rip_records_failure_and_reraises():
    t = _track()
    stats = DownloadStats()
    with patch.object(
        t, "preprocess", new=AsyncMock(side_effect=RuntimeError("disk full"))
    ):
        with pytest.raises(RuntimeError, match="disk full"):
            await t.rip(stats)
    assert stats.tracks_failed == 1


async def test_rip_reraises_without_stats():
    t = _track()
    with patch.object(t, "preprocess", new=AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError):
            await t.rip()


async def test_rip_dry_run_skips_lifecycle():
    """dry_run=True records success but skips preprocess/download/postprocess."""
    t = _track()
    t.config.session.cli.dry_run = True
    stats = DownloadStats()
    with (
        patch.object(t, "preprocess", new=AsyncMock()) as mock_pre,
        patch.object(t, "download", new=AsyncMock()) as mock_dl,
        patch.object(t, "postprocess", new=AsyncMock()) as mock_post,
    ):
        await t.rip(stats)
    mock_pre.assert_not_awaited()
    mock_dl.assert_not_awaited()
    mock_post.assert_not_awaited()
    assert stats.tracks_downloaded == 1


async def test_rip_dry_run_no_stats():
    """dry_run=True with stats=None completes without error."""
    t = _track()
    t.config.session.cli.dry_run = True
    with (
        patch.object(t, "preprocess", new=AsyncMock()),
        patch.object(t, "download", new=AsyncMock()),
        patch.object(t, "postprocess", new=AsyncMock()),
    ):
        await t.rip()


# ---------------------------------------------------------------------------
# PendingTrack.resolve
# ---------------------------------------------------------------------------


def _pending_track(downloaded=False, disc_subdirectories=False):
    client = MagicMock()
    client.source = "deezer"
    client.get_metadata = AsyncMock(return_value={"id": "42", "title": "Song"})
    client.get_downloadable = AsyncMock(return_value=_downloadable())

    album = MagicMock()
    album.disctotal = 1

    return PendingTrack(
        id="42",
        album=album,
        client=client,
        config=_config(disc_subdirectories=disc_subdirectories),
        folder="/dl/album",
        db=_db(downloaded=downloaded),
        cover_path="/cover.jpg",
    )


async def test_pending_track_skips_if_downloaded():
    pt = _pending_track(downloaded=True)
    assert await pt.resolve() is None
    pt.client.get_metadata.assert_not_called()


async def test_pending_track_returns_none_on_get_metadata_non_streamable():
    pt = _pending_track()
    pt.client.get_metadata = AsyncMock(side_effect=NonStreamableError("geo"))
    assert await pt.resolve() is None


async def test_pending_track_returns_none_on_metadata_exception():
    pt = _pending_track()
    with patch(
        "streamrip.media.track.TrackMetadata.from_resp",
        side_effect=ValueError("bad resp"),
    ):
        assert await pt.resolve() is None


async def test_pending_track_records_failure_on_get_metadata_non_streamable():
    """A track that dies in resolve() leaves no file and no downloads row, so
    without a failed row it just goes missing with nothing left to retry."""
    pt = _pending_track()
    pt.client.get_metadata = AsyncMock(side_effect=NonStreamableError("geo"))
    await pt.resolve()
    pt.db.set_failed.assert_called_once_with("deezer", "track", "42")


async def test_pending_track_records_failure_on_metadata_exception():
    pt = _pending_track()
    with patch(
        "streamrip.media.track.TrackMetadata.from_resp", side_effect=ValueError("bad")
    ):
        await pt.resolve()
    pt.db.set_failed.assert_called_once_with("deezer", "track", "42")


async def test_pending_track_records_failure_on_downloadable_non_streamable():
    pt = _pending_track()
    pt.client.get_downloadable = AsyncMock(side_effect=NonStreamableError("no url"))
    await pt.resolve()
    pt.db.set_failed.assert_called_once_with("deezer", "track", "42")


async def test_pending_track_does_not_record_failure_when_already_downloaded():
    """Skipping an already-downloaded track is not a failure."""
    pt = _pending_track(downloaded=True)
    assert await pt.resolve() is None
    pt.db.set_failed.assert_not_called()


async def test_pending_track_returns_none_when_meta_is_none():
    pt = _pending_track()
    with patch("streamrip.media.track.TrackMetadata.from_resp", return_value=None):
        result = await pt.resolve()
    assert result is None
    pt.db.set_failed.assert_called_once()


async def test_pending_track_returns_none_on_downloadable_non_streamable():
    pt = _pending_track()
    pt.client.get_downloadable = AsyncMock(side_effect=NonStreamableError("no dl"))
    with patch(
        "streamrip.media.track.TrackMetadata.from_resp",
        return_value=MagicMock(tracknumber=1),
    ):
        assert await pt.resolve() is None


async def test_pending_track_returns_track():
    pt = _pending_track()
    with patch(
        "streamrip.media.track.TrackMetadata.from_resp", return_value=MagicMock()
    ):
        result = await pt.resolve()
    assert isinstance(result, Track)
    assert result.folder == "/dl/album"


async def test_pending_track_disc_subdirectory():
    pt = _pending_track(disc_subdirectories=True)
    pt.album.disctotal = 3
    meta = MagicMock()
    meta.discnumber = 2
    with patch("streamrip.media.track.TrackMetadata.from_resp", return_value=meta):
        result = await pt.resolve()
    assert result.folder == "/dl/album/Disc 2"


async def test_pending_track_no_disc_folder_when_single_disc():
    pt = _pending_track(disc_subdirectories=True)
    pt.album.disctotal = 1
    with patch(
        "streamrip.media.track.TrackMetadata.from_resp", return_value=MagicMock()
    ):
        result = await pt.resolve()
    assert result.folder == "/dl/album"


# ---------------------------------------------------------------------------
# PendingSingle.resolve
# ---------------------------------------------------------------------------


def _pending_single(downloaded=False, add_singles_to_folder=True, source="deezer"):
    client = MagicMock()
    client.source = source
    client.session = MagicMock()
    client.get_metadata = AsyncMock(return_value={"id": "1", "title": "Song"})
    client.get_downloadable = AsyncMock(return_value=_downloadable())

    cfg = _config(add_singles_to_folder=add_singles_to_folder)
    setattr(cfg.session, source, MagicMock(quality=2))

    return PendingSingle(
        id="1", client=client, config=cfg, db=_db(downloaded=downloaded)
    )


async def test_pending_single_skips_if_downloaded():
    ps = _pending_single(downloaded=True)
    assert await ps.resolve() is None
    ps.client.get_metadata.assert_not_called()


async def test_pending_single_records_failure_on_get_metadata_non_streamable():
    ps = _pending_single()
    ps.client.get_metadata = AsyncMock(side_effect=NonStreamableError("geo"))
    await ps.resolve()
    ps.db.set_failed.assert_called_once_with("deezer", "track", "1")


async def test_pending_single_records_failure_on_album_metadata_exception():
    ps = _pending_single()
    with patch(
        "streamrip.media.track.AlbumMetadata.from_track_resp",
        side_effect=ValueError("bad"),
    ):
        await ps.resolve()
    ps.db.set_failed.assert_called_once_with("deezer", "track", "1")


async def test_pending_single_records_failure_on_track_metadata_exception():
    ps = _pending_single()
    with (
        patch(
            "streamrip.media.track.AlbumMetadata.from_track_resp",
            return_value=MagicMock(),
        ),
        patch(
            "streamrip.media.track.TrackMetadata.from_resp",
            side_effect=ValueError("bad"),
        ),
    ):
        await ps.resolve()
    ps.db.set_failed.assert_called_once_with("deezer", "track", "1")


async def test_pending_single_does_not_record_failure_when_already_downloaded():
    ps = _pending_single(downloaded=True)
    assert await ps.resolve() is None
    ps.db.set_failed.assert_not_called()


async def test_pending_single_returns_none_on_metadata_non_streamable():
    ps = _pending_single()
    ps.client.get_metadata = AsyncMock(side_effect=NonStreamableError("geo"))
    assert await ps.resolve() is None


async def test_pending_single_returns_none_on_album_exception():
    ps = _pending_single()
    with patch(
        "streamrip.media.track.AlbumMetadata.from_track_resp",
        side_effect=ValueError("bad"),
    ):
        assert await ps.resolve() is None


async def test_pending_single_returns_none_when_album_is_none():
    ps = _pending_single()
    with patch(
        "streamrip.media.track.AlbumMetadata.from_track_resp", return_value=None
    ):
        result = await ps.resolve()
    assert result is None
    ps.db.set_failed.assert_called_once()


async def test_pending_single_returns_none_on_track_meta_exception():
    ps = _pending_single()
    album = MagicMock()
    with (
        patch(
            "streamrip.media.track.AlbumMetadata.from_track_resp", return_value=album
        ),
        patch(
            "streamrip.media.track.TrackMetadata.from_resp",
            side_effect=ValueError("bad"),
        ),
    ):
        assert await ps.resolve() is None


async def test_pending_single_returns_none_when_track_meta_is_none():
    ps = _pending_single()
    album = MagicMock()
    with (
        patch(
            "streamrip.media.track.AlbumMetadata.from_track_resp", return_value=album
        ),
        patch("streamrip.media.track.TrackMetadata.from_resp", return_value=None),
    ):
        result = await ps.resolve()
    assert result is None
    ps.db.set_failed.assert_called_once()


async def test_pending_single_returns_track_with_is_single():
    ps = _pending_single()
    album = MagicMock()
    album.info.quality = 2
    with (
        patch(
            "streamrip.media.track.AlbumMetadata.from_track_resp", return_value=album
        ),
        patch(
            "streamrip.media.track.TrackMetadata.from_resp", return_value=MagicMock()
        ),
        patch(
            "streamrip.media.track.download_embed_cover",
            new=AsyncMock(return_value="/cover.jpg"),
        ),
        patch("streamrip.media.track.os.makedirs"),
    ):
        result = await ps.resolve()
    assert isinstance(result, Track)
    assert result.is_single is True


async def test_pending_single_uses_parent_folder_when_add_singles_to_folder_false():
    ps = _pending_single(add_singles_to_folder=False)
    album = MagicMock()
    with (
        patch(
            "streamrip.media.track.AlbumMetadata.from_track_resp", return_value=album
        ),
        patch(
            "streamrip.media.track.TrackMetadata.from_resp", return_value=MagicMock()
        ),
        patch(
            "streamrip.media.track.download_embed_cover",
            new=AsyncMock(return_value=None),
        ),
        patch("streamrip.media.track.os.makedirs"),
    ):
        result = await ps.resolve()
    assert result.folder == "/dl"


async def test_pending_single_uses_format_folder_when_add_singles_to_folder():
    ps = _pending_single(add_singles_to_folder=True)
    album = MagicMock()
    with (
        patch(
            "streamrip.media.track.AlbumMetadata.from_track_resp", return_value=album
        ),
        patch(
            "streamrip.media.track.TrackMetadata.from_resp", return_value=MagicMock()
        ),
        patch(
            "streamrip.media.track.download_embed_cover",
            new=AsyncMock(return_value=None),
        ),
        patch("streamrip.media.track.os.makedirs"),
        patch.object(ps, "_format_folder", return_value="/dl/Artist/Album") as mock_ff,
    ):
        result = await ps.resolve()
    mock_ff.assert_called_once_with(album)
    assert result.folder == "/dl/Artist/Album"


# ---------------------------------------------------------------------------
# PendingSingle._format_folder
# ---------------------------------------------------------------------------


def test_format_folder_no_source_subdirectory():
    ps = _pending_single()
    ps.config.session.downloads.folder = "/dl"
    ps.config.session.downloads.source_subdirectories = False
    ps.config.session.get_source.return_value.quality = 2

    meta = MagicMock()
    meta.info.quality = 2
    meta.format_folder_path = MagicMock(return_value="Artist/Album")

    result = ps._format_folder(meta)
    assert result == "/dl/Artist/Album"


def test_format_folder_with_source_subdirectory():
    ps = _pending_single(source="qobuz")
    ps.config.session.downloads.folder = "/dl"
    ps.config.session.downloads.source_subdirectories = True
    ps.config.session.get_source.return_value.quality = 3

    meta = MagicMock()
    meta.info.quality = 2
    meta.format_folder_path = MagicMock(return_value="Artist/Album")

    result = ps._format_folder(meta)
    assert result == "/dl/Qobuz/Artist/Album"


def test_format_folder_clips_quality_to_meta():
    ps = _pending_single()
    ps.config.session.downloads.folder = "/dl"
    ps.config.session.downloads.source_subdirectories = False
    ps.config.session.get_source.return_value.quality = 5

    meta = MagicMock()
    meta.info.quality = 1
    meta.format_folder_path = MagicMock(return_value="Artist/Album")

    ps._format_folder(meta)
    meta.format_folder_path.assert_called_once_with(
        ps.config.session.filepaths.folder_format, 1
    )
