"""Tests for streamrip/media/label.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from streamrip.exceptions import NonStreamableError
from streamrip.media.label import Label, PendingLabel
from streamrip.media.media import DownloadStats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pending_album(resolve_result=None):
    """Return a PendingAlbum mock whose resolve() returns resolve_result."""
    pa = MagicMock()
    pa.resolve = AsyncMock(return_value=resolve_result)
    return pa


def _album_mock():
    a = MagicMock()
    a.rip = AsyncMock()
    return a


def _label(albums=None):
    return Label(
        name="Test Label",
        albums=albums or [],
        client=MagicMock(),
        config=MagicMock(),
    )


def _pending_label(meta_resp=None, source="deezer"):
    client = MagicMock()
    client.source = source
    client.get_metadata = AsyncMock(return_value=meta_resp or {})
    return PendingLabel(
        id="42",
        client=client,
        config=MagicMock(),
        db=MagicMock(),
    )


# ---------------------------------------------------------------------------
# Label.preprocess / postprocess
# ---------------------------------------------------------------------------


async def test_label_preprocess_is_noop():
    await _label().preprocess()  # must not raise


async def test_label_postprocess_is_noop():
    await _label().postprocess()  # must not raise


# ---------------------------------------------------------------------------
# Label.batch
# ---------------------------------------------------------------------------


def test_batch_empty():
    assert list(Label.batch([], 3)) == []


def test_batch_exact_multiple():
    result = list(Label.batch([1, 2, 3, 4], 2))
    assert result == [[1, 2], [3, 4]]


def test_batch_remainder():
    result = list(Label.batch([1, 2, 3, 4, 5], 2))
    assert result == [[1, 2], [3, 4], [5]]


def test_batch_larger_than_list():
    result = list(Label.batch([1, 2], 10))
    assert result == [[1, 2]]


# ---------------------------------------------------------------------------
# Label.download
# ---------------------------------------------------------------------------


async def test_download_resolves_and_rips_all_albums():
    album1 = _album_mock()
    album2 = _album_mock()
    label = _label([_pending_album(album1), _pending_album(album2)])

    await label.download()

    album1.rip.assert_awaited_once()
    album2.rip.assert_awaited_once()


async def test_download_passes_stats_to_rip():
    album = _album_mock()
    stats = DownloadStats()
    label = _label([_pending_album(album)])

    await label.download(stats=stats)

    album.rip.assert_awaited_once_with(stats)


async def test_download_skips_none_resolved_album():
    """When PendingAlbum.resolve() returns None, rip() is not called."""
    label = _label([_pending_album(None)])
    await label.download()  # must not raise


async def test_download_processes_in_batches_of_ten():
    """More than 10 albums must still all be processed."""
    albums = [_pending_album(_album_mock()) for _ in range(15)]
    label = _label(albums)

    await label.download()

    for pa in albums:
        pa.resolve.assert_awaited_once()


async def test_download_empty_albums():
    await _label([]).download()  # must not raise


# ---------------------------------------------------------------------------
# PendingLabel.resolve — happy path
# ---------------------------------------------------------------------------


async def test_pending_label_resolve_returns_label():
    resp = {"name": "Warp Records", "albums": [{"id": "a1"}, {"id": "a2"}]}
    pl = _pending_label(meta_resp=resp, source="deezer")

    with patch("streamrip.media.label.PendingAlbum"):
        result = await pl.resolve()

    assert isinstance(result, Label)
    assert result.name == "Warp Records"
    assert len(result.albums) == 2


async def test_pending_label_resolve_creates_pending_albums_with_correct_ids():
    resp = {"name": "XL", "albums": [{"id": "id1"}, {"id": "id2"}]}
    pl = _pending_label(meta_resp=resp, source="deezer")

    with patch("streamrip.media.label.PendingAlbum") as mock_pa:
        mock_pa.side_effect = lambda *a, **kw: MagicMock()
        await pl.resolve()

    calls = [c.args[0] for c in mock_pa.call_args_list]
    assert calls == ["id1", "id2"]


# ---------------------------------------------------------------------------
# PendingLabel.resolve — error paths
# ---------------------------------------------------------------------------


async def test_pending_label_resolve_returns_none_on_non_streamable():
    pl = _pending_label()
    pl.client.get_metadata = AsyncMock(side_effect=NonStreamableError("gone"))

    result = await pl.resolve()

    assert result is None


async def test_pending_label_resolve_returns_none_on_metadata_error():
    """LabelMetadata.from_resp raising returns None gracefully."""
    resp = {"name": "Bad", "albums": [{"id": "x"}]}
    pl = _pending_label(meta_resp=resp, source="unknown_source")

    result = await pl.resolve()

    assert result is None
