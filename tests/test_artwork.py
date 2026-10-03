"""Tests for streamrip/media/artwork.py."""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests
from PIL import Image

import streamrip.media.artwork as artwork_module
from streamrip.media.artwork import (
    download_artwork,
    download_embed_cover,
    downscale_image,
    remove_artwork_tempdirs,
)
from streamrip.metadata.covers import Covers

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_tempdirs():
    artwork_module._artwork_tempdirs.clear()
    yield
    artwork_module._artwork_tempdirs.clear()


def _config(
    save_artwork=True,
    embed=True,
    embed_size="large",
    saved_max_width=0,
    embed_max_width=0,
):
    cfg = MagicMock()
    cfg.save_artwork = save_artwork
    cfg.embed = embed
    cfg.embed_size = embed_size
    cfg.saved_max_width = saved_max_width
    cfg.embed_max_width = embed_max_width
    return cfg


def _covers(
    original_url="https://example.com/orig.jpg",
    large_url="https://example.com/large.jpg",
):
    c = Covers()
    if original_url:
        c.set_cover_url("original", original_url)
    if large_url:
        c.set_cover_url("large", large_url)
    return c


def _make_jpeg(path: str, width: int, height: int):
    Image.new("RGB", (width, height), color=(64, 64, 64)).save(path, "JPEG")


def _write_image(path, _callback):
    """What a successful BasicDownloadable.download leaves behind."""
    with open(path, "wb") as f:
        f.write(b"jpeg")


@pytest.fixture
def mock_downloadable():
    """Patch BasicDownloadable so no real HTTP calls are made."""
    with patch("streamrip.media.artwork.BasicDownloadable") as mock_cls:
        instance = MagicMock()
        instance.download = AsyncMock(side_effect=_write_image)
        mock_cls.return_value = instance
        yield mock_cls


# ---------------------------------------------------------------------------
# remove_artwork_tempdirs
# ---------------------------------------------------------------------------


def test_remove_tempdirs_deletes_existing(tmp_path):
    d = tempfile.mkdtemp(dir=tmp_path)
    artwork_module._artwork_tempdirs.add(d)
    assert os.path.isdir(d)

    remove_artwork_tempdirs()

    assert not os.path.exists(d)


def test_remove_tempdirs_ignores_missing():
    artwork_module._artwork_tempdirs.add("/nonexistent/path/xyz")
    remove_artwork_tempdirs()  # must not raise


def test_remove_tempdirs_empty_set():
    remove_artwork_tempdirs()  # no-op, must not raise


# ---------------------------------------------------------------------------
# downscale_image
# ---------------------------------------------------------------------------


def test_downscale_image_noop_when_within_limit(tmp_path):
    p = str(tmp_path / "cover.jpg")
    _make_jpeg(p, 100, 100)
    downscale_image(p, 200)
    assert Image.open(p).size == (100, 100)


def test_downscale_image_exact_dimension_is_noop(tmp_path):
    p = str(tmp_path / "cover.jpg")
    _make_jpeg(p, 200, 200)
    downscale_image(p, 200)
    assert Image.open(p).size == (200, 200)


def test_downscale_image_landscape(tmp_path):
    p = str(tmp_path / "cover.jpg")
    _make_jpeg(p, 400, 200)
    downscale_image(p, 200)
    w, h = Image.open(p).size
    assert w == 200
    assert h == 100


def test_downscale_image_portrait(tmp_path):
    p = str(tmp_path / "cover.jpg")
    _make_jpeg(p, 200, 400)
    downscale_image(p, 200)
    w, h = Image.open(p).size
    assert w == 100
    assert h == 200


# ---------------------------------------------------------------------------
# download_artwork — early-return branches
# ---------------------------------------------------------------------------


async def test_download_artwork_disabled_config(tmp_path):
    result = await download_artwork(
        MagicMock(),
        str(tmp_path),
        _covers(),
        _config(save_artwork=False, embed=False),
        False,
    )
    assert result == (None, None)


async def test_download_artwork_empty_covers(tmp_path):
    result = await download_artwork(
        MagicMock(), str(tmp_path), Covers(), _config(), False
    )
    assert result == (None, None)


async def test_download_artwork_already_cached_returns_early(tmp_path):
    """When both paths are already set in Covers, no download is triggered."""
    c = _covers()
    c.set_largest_path("/fake/saved.jpg")
    c.set_path("large", "/fake/embed.jpg")

    with patch("streamrip.media.artwork.BasicDownloadable") as mock_cls:
        embed_path, saved_path = await download_artwork(
            MagicMock(), str(tmp_path), c, _config(), False
        )
        mock_cls.assert_not_called()

    assert embed_path == "/fake/embed.jpg"
    assert saved_path == "/fake/saved.jpg"


# ---------------------------------------------------------------------------
# download_artwork — download paths
# ---------------------------------------------------------------------------


async def test_download_artwork_for_playlist_disables_save(mock_downloadable, tmp_path):
    """for_playlist=True forces save_artwork=False."""
    embed_path, saved_path = await download_artwork(
        MagicMock(),
        str(tmp_path),
        _covers(),
        _config(save_artwork=True, embed=True),
        for_playlist=True,
    )
    assert saved_path is None
    assert embed_path is not None


async def test_download_artwork_only_save(mock_downloadable, tmp_path):
    c = _covers()
    embed_path, saved_path = await download_artwork(
        MagicMock(), str(tmp_path), c, _config(save_artwork=True, embed=False), False
    )
    assert saved_path == os.path.join(str(tmp_path), "cover.jpg")
    assert embed_path is None


async def test_download_artwork_only_embed(mock_downloadable, tmp_path):
    c = _covers()
    embed_path, saved_path = await download_artwork(
        MagicMock(), str(tmp_path), c, _config(save_artwork=False, embed=True), False
    )
    assert embed_path is not None
    assert saved_path is None


async def test_download_artwork_embed_creates_tempdir(mock_downloadable, tmp_path):
    """Each call embeds into a fresh __artwork_<random> directory it registers.

    A single shared "__artwork" directory let concurrent playlist tracks of one
    album write the same file, and its cleanup would have deleted a folder of
    that name belonging to the user.
    """
    for _ in range(2):
        await download_artwork(
            MagicMock(),
            str(tmp_path),
            _covers(),
            _config(save_artwork=False, embed=True),
            False,
        )
    dirs = sorted(p for p in tmp_path.iterdir() if p.name.startswith("__artwork_"))
    assert len(dirs) == 2
    assert {str(d) for d in dirs} == artwork_module._artwork_tempdirs


async def test_download_artwork_both(mock_downloadable, tmp_path):
    embed_path, saved_path = await download_artwork(
        MagicMock(), str(tmp_path), _covers(), _config(), False
    )
    assert embed_path is not None
    assert saved_path == os.path.join(str(tmp_path), "cover.jpg")


@pytest.fixture
def no_sleep():
    """Skip the retry backoff."""
    with patch("streamrip.media.artwork.asyncio.sleep", new=AsyncMock()) as sleep:
        yield sleep


async def test_download_artwork_gather_exception_returns_none(tmp_path, no_sleep):
    """When every attempt fails, both covers come back as None."""
    with patch("streamrip.media.artwork.BasicDownloadable") as mock_cls:
        instance = MagicMock()
        instance.download = AsyncMock(
            side_effect=requests.ConnectionError("network error")
        )
        mock_cls.return_value = instance

        result = await download_artwork(
            MagicMock(), str(tmp_path), _covers(), _config(), False
        )

    assert result == (None, None)
    # Both covers were tried COVER_DOWNLOAD_ATTEMPTS times each.
    assert instance.download.await_count == 2 * artwork_module.COVER_DOWNLOAD_ATTEMPTS


async def test_download_artwork_retries_a_dropped_connection(tmp_path, no_sleep):
    """A transient failure is retried instead of costing the track its cover.

    Seen on a real playlist run: one 'Connection reset by peer' left a track
    tagged without art, with nothing to retry it later.
    """
    with patch("streamrip.media.artwork.BasicDownloadable") as mock_cls:
        instance = MagicMock()
        calls = []

        def drop_then_succeed(path, cb):
            calls.append(path)
            if len(calls) == 1:
                raise requests.ConnectionError(
                    "('Connection aborted.', "
                    "ConnectionResetError(104, 'Connection reset by peer'))"
                )
            _write_image(path, cb)

        instance.download = AsyncMock(side_effect=drop_then_succeed)
        mock_cls.return_value = instance

        embed_path, saved_path = await download_artwork(
            MagicMock(), str(tmp_path), _covers(), _config(save_artwork=False), False
        )

    assert embed_path is not None
    assert saved_path is None
    assert instance.download.await_count == 2
    no_sleep.assert_awaited_once_with(1)


async def test_download_artwork_one_failed_cover_keeps_the_other(tmp_path, no_sleep):
    """A cover.jpg that cannot be fetched no longer discards the embedded art."""

    def make(session, url, ext):
        dl = MagicMock()
        if url.endswith("orig.jpg"):  # the hi-res, saved cover
            dl.download = AsyncMock(side_effect=OSError("gone"))
        else:
            dl.download = AsyncMock(side_effect=_write_image)
        return dl

    with patch("streamrip.media.artwork.BasicDownloadable", side_effect=make):
        embed_path, saved_path = await download_artwork(
            MagicMock(), str(tmp_path), _covers(), _config(), False
        )

    assert saved_path is None
    assert embed_path is not None


async def test_download_artwork_logs_the_url_on_final_failure(
    tmp_path, no_sleep, caplog
):
    with patch("streamrip.media.artwork.BasicDownloadable") as mock_cls:
        instance = MagicMock()
        instance.download = AsyncMock(side_effect=requests.ConnectionError("boom"))
        mock_cls.return_value = instance
        with caplog.at_level("ERROR", logger="streamrip"):
            await download_artwork(
                MagicMock(),
                str(tmp_path),
                _covers(),
                _config(save_artwork=False),
                False,
            )

    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    assert "https://example.com/large.jpg" in errors[0]
    assert "after 3 attempts" in errors[0]


async def test_download_artwork_downscale_saved(mock_downloadable, tmp_path, mocker):
    mock_ds = mocker.patch("streamrip.media.artwork.downscale_image")
    config = _config(save_artwork=True, embed=False, saved_max_width=500)

    await download_artwork(MagicMock(), str(tmp_path), _covers(), config, False)

    mock_ds.assert_called_once_with(os.path.join(str(tmp_path), "cover.jpg"), 500)


async def test_download_artwork_downscale_embed(mock_downloadable, tmp_path, mocker):
    mock_ds = mocker.patch("streamrip.media.artwork.downscale_image")
    config = _config(
        save_artwork=False, embed=True, embed_size="large", embed_max_width=300
    )

    embed_path, _ = await download_artwork(
        MagicMock(), str(tmp_path), _covers(), config, False
    )

    assert embed_path is not None
    mock_ds.assert_called_once_with(embed_path, 300)


async def test_download_artwork_no_downscale_when_zero(
    mock_downloadable, tmp_path, mocker
):
    mock_ds = mocker.patch("streamrip.media.artwork.downscale_image")
    config = _config(saved_max_width=0, embed_max_width=0)

    await download_artwork(MagicMock(), str(tmp_path), _covers(), config, False)

    mock_ds.assert_not_called()


# ---------------------------------------------------------------------------
# download_embed_cover
# ---------------------------------------------------------------------------


async def test_download_embed_cover_returns_embed_path(mock_downloadable, tmp_path):
    result = await download_embed_cover(
        MagicMock(),
        str(tmp_path),
        _covers(),
        _config(save_artwork=False, embed=True),
        False,
    )
    assert result is not None


async def test_download_embed_cover_returns_none_when_disabled(tmp_path):
    result = await download_embed_cover(
        MagicMock(),
        str(tmp_path),
        _covers(),
        _config(save_artwork=False, embed=False),
        False,
    )
    assert result is None


def _http_error(status):
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(f"{status} error", response=resp)


@pytest.mark.parametrize(
    ("error", "retried"),
    [
        (_http_error(404), False),
        (_http_error(403), False),
        (_http_error(429), True),
        (_http_error(503), True),
        (requests.ConnectionError("reset"), True),
        (requests.Timeout("slow"), True),
        (requests.exceptions.ChunkedEncodingError("cut"), True),
        (OSError("disk full"), False),
    ],
)
async def test_only_transient_errors_are_retried(tmp_path, no_sleep, error, retried):
    """A missing cover (404) fails at once instead of burning three attempts."""
    with patch("streamrip.media.artwork.BasicDownloadable") as mock_cls:
        instance = MagicMock()
        instance.download = AsyncMock(side_effect=error)
        mock_cls.return_value = instance
        await download_artwork(
            MagicMock(), str(tmp_path), _covers(), _config(save_artwork=False), False
        )
    expected = artwork_module.COVER_DOWNLOAD_ATTEMPTS if retried else 1
    assert instance.download.await_count == expected


async def test_a_failing_writer_does_not_delete_a_finished_cover(tmp_path):
    """Concurrent writers of one path: the loser cannot remove the winner's file.

    Singles saved to the same folder all write cover.jpg. BasicDownloadable
    deletes its partial file on failure, which used to be the shared path.
    """
    target = str(tmp_path / "cover.jpg")

    def make(session, url, ext):
        dl = MagicMock()
        if url == "ok":
            dl.download = AsyncMock(side_effect=_write_image)
        else:

            def fail(path, _cb):
                with open(path, "wb") as f:
                    f.write(b"partial")
                os.remove(path)  # what BasicDownloadable does on error
                raise OSError("gone")

            dl.download = AsyncMock(side_effect=fail)
        return dl

    with patch("streamrip.media.artwork.BasicDownloadable", side_effect=make):
        assert await artwork_module._download_cover(MagicMock(), "ok", target)
        assert not await artwork_module._download_cover(MagicMock(), "bad", target)

    assert (tmp_path / "cover.jpg").read_bytes() == b"jpeg"
    assert [p.name for p in tmp_path.iterdir()] == ["cover.jpg"]  # no .part left


def test_cleanup_leaves_a_users_artwork_folder_alone(tmp_path):
    user_dir = tmp_path / "__artwork"
    user_dir.mkdir()
    (user_dir / "mine.jpg").write_bytes(b"x")
    ours = tempfile.mkdtemp(prefix="__artwork_", dir=tmp_path)
    artwork_module._artwork_tempdirs.add(ours)

    remove_artwork_tempdirs()

    assert (user_dir / "mine.jpg").exists()
    assert not os.path.exists(ours)
    assert artwork_module._artwork_tempdirs == set()
