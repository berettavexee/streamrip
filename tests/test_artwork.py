"""Tests for streamrip/media/artwork.py."""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
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


@pytest.fixture
def mock_downloadable():
    """Patch BasicDownloadable so no real HTTP calls are made."""
    with patch("streamrip.media.artwork.BasicDownloadable") as mock_cls:
        instance = MagicMock()
        instance.download = AsyncMock()
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
    """Embedding artwork creates an __artwork subdirectory and registers it."""
    await download_artwork(
        MagicMock(),
        str(tmp_path),
        _covers(),
        _config(save_artwork=False, embed=True),
        False,
    )
    expected_dir = os.path.join(str(tmp_path), "__artwork")
    assert (tmp_path / "__artwork").is_dir()
    assert expected_dir in artwork_module._artwork_tempdirs


async def test_download_artwork_both(mock_downloadable, tmp_path):
    embed_path, saved_path = await download_artwork(
        MagicMock(), str(tmp_path), _covers(), _config(), False
    )
    assert embed_path is not None
    assert saved_path == os.path.join(str(tmp_path), "cover.jpg")


async def test_download_artwork_gather_exception_returns_none(tmp_path):
    """asyncio.gather failure is caught and (None, None) is returned."""
    with patch("streamrip.media.artwork.BasicDownloadable") as mock_cls:
        instance = MagicMock()
        instance.download = AsyncMock(side_effect=RuntimeError("network error"))
        mock_cls.return_value = instance

        result = await download_artwork(
            MagicMock(), str(tmp_path), _covers(), _config(), False
        )

    assert result == (None, None)


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
