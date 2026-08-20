"""Tests for the liked-tracks pipeline (`streamrip/media/user_favorites.py`).

Downloading a user's favorites is one of this fork's own features — upstream has
no equivalent — and it was the least covered module the fork actually wrote.

Two behaviours are worth pinning down, because both fail *quietly*:

* ``PendingUserFavorites.resolve`` turns any client-side error into ``None``,
  which the caller reads as "nothing to download". A broken ARL, a profile the
  account may not read, or a plain API error all look exactly like an empty
  favorites list from the outside — only the log tells them apart, so the log is
  asserted here.
* ``UserFavorites.download`` swallows per-item exceptions on purpose, so that one
  unavailable track does not abort a 900-track library. The test therefore checks
  that the *other* items still run, which is the whole point of the ``try``.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from streamrip.media.album import PendingAlbum
from streamrip.media.artist import PendingArtist
from streamrip.media.media import DownloadStats
from streamrip.media.track import PendingSingle
from streamrip.media.user_favorites import (
    CHUNK_SIZE,
    PendingUserFavorites,
    UserFavorites,
)


def _pending(media_type="tracks", ids=None, side_effect=None):
    """Build a PendingUserFavorites over a client returning *ids*.

    Args:
        media_type: Plural media type passed to the constructor.
        ids: Favorite ids the client should return.
        side_effect: When set, the client raises this instead of returning ids.

    Returns:
        A (pending, client) pair.
    """
    client = MagicMock()
    client.source = "deezer"
    client.get_user_favorite_ids = AsyncMock(
        return_value=ids if ids is not None else [], side_effect=side_effect
    )
    pending = PendingUserFavorites(
        media_type=media_type,
        client=client,
        config=MagicMock(),
        db=MagicMock(),
    )
    return pending, client


def _item(media=None, side_effect=None):
    """Build a Pending whose resolve() yields *media* or raises *side_effect*."""
    item = MagicMock()
    item.resolve = AsyncMock(return_value=media, side_effect=side_effect)
    return item


def _media():
    """Build a resolved Media whose rip() records the stats it was handed."""
    media = MagicMock()
    media.rip = AsyncMock()
    return media


class TestPendingUserFavoritesResolve:
    async def test_source_without_favorites_support_raises(self):
        pending, client = _pending()
        del client.get_user_favorite_ids  # e.g. Qobuz, Tidal, SoundCloud
        with pytest.raises(NotImplementedError, match="deezer"):
            await pending.resolve()

    async def test_api_error_yields_none_and_is_logged(self, caplog):
        pending, _ = _pending(side_effect=RuntimeError("invalid ARL"))
        with caplog.at_level(logging.ERROR, logger="streamrip"):
            assert await pending.resolve() is None
        assert "invalid ARL" in caplog.text

    async def test_empty_favorites_yield_none_and_are_logged(self, caplog):
        # Deezer answers an unreadable profile with an empty list and no error,
        # so this path is reached in practice, not only on a genuinely empty
        # library. It must stay distinguishable from the error path above.
        pending, _ = _pending(ids=[])
        with caplog.at_level(logging.INFO, logger="streamrip"):
            assert await pending.resolve() is None
        assert "No tracks found" in caplog.text

    async def test_unknown_media_type_raises(self):
        pending, _ = _pending(media_type="podcasts", ids=["1"])
        with pytest.raises(NotImplementedError, match="podcasts"):
            await pending.resolve()

    @pytest.mark.parametrize(
        ("media_type", "expected_cls"),
        [
            ("tracks", PendingSingle),
            ("albums", PendingAlbum),
            ("artists", PendingArtist),
        ],
    )
    async def test_each_media_type_maps_to_its_pending_class(
        self, media_type, expected_cls
    ):
        pending, _ = _pending(media_type=media_type, ids=["1", "2"])
        favorites = await pending.resolve()
        assert isinstance(favorites, UserFavorites)
        assert favorites.media_type == media_type
        assert [type(p) for p in favorites.pending_items] == [expected_cls] * 2
        assert [p.id for p in favorites.pending_items] == ["1", "2"]

    async def test_the_client_is_asked_for_the_requested_media_type(self):
        pending, client = _pending(media_type="albums", ids=["1"])
        await pending.resolve()
        client.get_user_favorite_ids.assert_awaited_once_with("albums")


class TestUserFavoritesDownload:
    async def test_each_item_is_resolved_and_ripped_with_the_stats(self):
        medias = [_media(), _media()]
        favorites = UserFavorites(
            pending_items=[_item(m) for m in medias], media_type="tracks"
        )
        stats = DownloadStats()
        await favorites.download(stats)
        for media in medias:
            media.rip.assert_awaited_once_with(stats)

    async def test_unresolvable_item_is_skipped_without_ripping(self):
        # resolve() returning None is the normal "already downloaded" outcome,
        # not an error: it must not be treated as one.
        resolved = _media()
        favorites = UserFavorites(
            pending_items=[_item(None), _item(resolved)], media_type="tracks"
        )
        await favorites.download()
        resolved.rip.assert_awaited_once()

    async def test_one_failing_item_does_not_stop_the_others(self, caplog):
        survivor = _media()
        favorites = UserFavorites(
            pending_items=[
                _item(side_effect=RuntimeError("track unavailable")),
                _item(survivor),
            ],
            media_type="tracks",
        )
        with caplog.at_level(logging.ERROR, logger="streamrip"):
            await favorites.download()
        survivor.rip.assert_awaited_once()
        assert "track unavailable" in caplog.text

    async def test_a_failure_during_rip_is_caught_too(self, caplog):
        # Track.rip() records the failure itself before re-raising, so the point
        # here is only that the exception does not escape the batch.
        exploding = _media()
        exploding.rip = AsyncMock(side_effect=RuntimeError("boom"))
        survivor = _media()
        favorites = UserFavorites(
            pending_items=[_item(exploding), _item(survivor)], media_type="tracks"
        )
        with caplog.at_level(logging.ERROR, logger="streamrip"):
            await favorites.download()
        survivor.rip.assert_awaited_once()
        assert "boom" in caplog.text

    async def test_every_item_of_a_library_larger_than_one_batch_runs(self):
        # A favorites library is routinely in the hundreds, so it is split into
        # CHUNK_SIZE batches; nothing may be dropped at a batch boundary.
        count = CHUNK_SIZE * 2 + 3
        medias = [_media() for _ in range(count)]
        favorites = UserFavorites(
            pending_items=[_item(m) for m in medias], media_type="tracks"
        )
        await favorites.download()
        assert all(m.rip.await_count == 1 for m in medias)

    async def test_rip_runs_the_full_lifecycle(self):
        # preprocess/postprocess are no-ops here, unlike every other Media: the
        # per-item Pending objects do their own setup. Guard the contract so a
        # refactor that starts requiring them fails loudly.
        media = _media()
        favorites = UserFavorites(pending_items=[_item(media)], media_type="tracks")
        await favorites.rip()
        media.rip.assert_awaited_once()
