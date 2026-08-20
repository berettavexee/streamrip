import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from streamrip.media.album import Album
from streamrip.media.playlist import Playlist


class TestErrorHandling:
    """Test error handling in playlist and album downloads."""

    @pytest.mark.asyncio
    async def test_playlist_handles_failed_track(self):
        """Test that a playlist download continues even if one track fails."""
        mock_config = MagicMock()
        mock_client = MagicMock()

        mock_track_success = MagicMock()
        mock_track_success.resolve = AsyncMock(return_value=MagicMock())
        mock_track_success.resolve.return_value.rip = AsyncMock()

        mock_track_failure = MagicMock()
        mock_track_failure.resolve = AsyncMock(
            side_effect=json.JSONDecodeError("Expecting value", "", 0)
        )

        playlist = Playlist(
            name="Test Playlist",
            config=mock_config,
            client=mock_client,
            tracks=[mock_track_success, mock_track_failure],
        )

        await playlist.download()

        mock_track_success.resolve.assert_called_once()
        mock_track_success.resolve.return_value.rip.assert_called_once()
        mock_track_failure.resolve.assert_called_once()

    @pytest.mark.asyncio
    async def test_album_handles_failed_track(self):
        """Test that an album download continues even if one track fails."""
        mock_config = MagicMock()
        mock_db = MagicMock()
        mock_meta = MagicMock()

        # Create a list of mock tracks - one will succeed, one will fail
        mock_track_success = MagicMock()
        mock_track_success.resolve = AsyncMock(return_value=MagicMock())
        mock_track_success.resolve.return_value.rip = AsyncMock()

        # This track will raise a JSONDecodeError when resolved
        mock_track_failure = MagicMock()
        mock_track_failure.resolve = AsyncMock(
            side_effect=json.JSONDecodeError("Expecting value", "", 0)
        )

        album = Album(
            meta=mock_meta,
            config=mock_config,
            tracks=[mock_track_success, mock_track_failure],
            folder="/test/folder",
            db=mock_db,
        )

        await album.download()

        mock_track_success.resolve.assert_called_once()
        mock_track_success.resolve.return_value.rip.assert_called_once()
        mock_track_failure.resolve.assert_called_once()

    @pytest.mark.asyncio
    async def test_main_rip_handles_failed_media(self):
        """Test that the Main.rip method handles failed media items."""
        from streamrip.rip.main import Main

        mock_config = MagicMock()

        mock_config.session.downloads.requests_per_minute = 0
        mock_config.session.database.downloads_enabled = False
        mock_config.session.database.failed_downloads_enabled = False

        with (
            patch("streamrip.rip.main.QobuzClient"),
            patch("streamrip.rip.main.TidalClient"),
            patch("streamrip.rip.main.DeezerClient"),
            patch("streamrip.rip.main.SoundcloudClient"),
        ):
            main = Main(mock_config)

            mock_media_success = MagicMock()
            mock_media_success.rip = AsyncMock()

            mock_media_failure = MagicMock()
            mock_media_failure.rip = AsyncMock(
                side_effect=Exception("Media download failed")
            )

            main.media = [mock_media_success, mock_media_failure]

            await main.rip()

            mock_media_success.rip.assert_called_once()
            mock_media_failure.rip.assert_called_once()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextmanager
def _patched_main():
    """Yield a Main instance with all client constructors mocked out."""
    from streamrip.rip.main import Main

    mock_config = MagicMock()
    mock_config.session.downloads.requests_per_minute = 0
    mock_config.session.database.downloads_enabled = False
    mock_config.session.database.failed_downloads_enabled = False

    with (
        patch("streamrip.rip.main.QobuzClient"),
        patch("streamrip.rip.main.TidalClient"),
        patch("streamrip.rip.main.DeezerClient"),
        patch("streamrip.rip.main.SoundcloudClient"),
    ):
        yield Main(mock_config)


def _mock_url(source: str = "deezer", pending=None, raises=None):
    """Return a mock URL object whose into_pending returns *pending* or raises *raises*."""
    url_obj = MagicMock()
    url_obj.source = source
    if raises is not None:
        url_obj.into_pending = AsyncMock(side_effect=raises)
    else:
        url_obj.into_pending = AsyncMock(return_value=pending or MagicMock())
    return url_obj


# ---------------------------------------------------------------------------
# Main.add — URL robustness
# ---------------------------------------------------------------------------


class TestMainAdd:
    @pytest.mark.asyncio
    async def test_add_unrecognised_url_skips_silently(self):
        """An URL that parse_url cannot match is skipped without raising."""
        with _patched_main() as main:
            with patch("streamrip.rip.main.parse_url", return_value=None):
                await main.add("https://example.com/not-supported")
        assert main.pending == []

    @pytest.mark.asyncio
    async def test_add_into_pending_error_skips_silently(self):
        """An URL whose into_pending raises is skipped without raising."""
        with _patched_main() as main:
            url_obj = _mock_url(raises=Exception("API unavailable"))
            with patch("streamrip.rip.main.parse_url", return_value=url_obj):
                await main.add("https://www.deezer.com/fr/album/1")
        assert main.pending == []

    @pytest.mark.asyncio
    async def test_add_valid_url_appended_to_pending(self):
        """A well-formed URL whose into_pending succeeds is added to pending."""
        mock_pending = MagicMock()
        with _patched_main() as main:
            url_obj = _mock_url(pending=mock_pending)
            with patch("streamrip.rip.main.parse_url", return_value=url_obj):
                await main.add("https://www.deezer.com/fr/album/1")
        assert main.pending == [mock_pending]


# ---------------------------------------------------------------------------
# Main.add_all — URL robustness
# ---------------------------------------------------------------------------


class TestMainAddAll:
    @pytest.mark.asyncio
    async def test_add_all_invalid_url_skipped(self):
        """parse_url returning None for one URL does not prevent others from being added."""
        mock_pending = MagicMock()
        good = _mock_url(pending=mock_pending)

        def _parse(url):
            return None if "bad" in url else good

        with _patched_main() as main:
            with patch("streamrip.rip.main.parse_url", side_effect=_parse):
                await main.add_all(
                    ["https://bad.example.com", "https://www.deezer.com/fr/album/1"]
                )

        assert main.pending == [mock_pending]

    @pytest.mark.asyncio
    async def test_add_all_all_invalid_leaves_pending_empty(self):
        """All invalid URLs → pending stays empty, no exception."""
        with _patched_main() as main:
            with patch("streamrip.rip.main.parse_url", return_value=None):
                await main.add_all(
                    ["https://bad1.example.com", "https://bad2.example.com"]
                )
        assert main.pending == []

    @pytest.mark.asyncio
    async def test_add_all_into_pending_error_skips_one_keeps_others(self):
        """into_pending failure for one URL does not prevent the other from being added."""
        mock_pending = MagicMock()
        good = _mock_url(pending=mock_pending)
        bad = _mock_url(raises=Exception("network error"))
        # Both parse successfully — into_pending differs
        bad.source = "deezer"

        call_count = 0

        def _parse(_url):
            nonlocal call_count
            call_count += 1
            return bad if call_count == 1 else good

        with _patched_main() as main:
            with patch("streamrip.rip.main.parse_url", side_effect=_parse):
                await main.add_all(
                    [
                        "https://www.deezer.com/fr/album/1",
                        "https://www.deezer.com/fr/album/2",
                    ]
                )

        assert main.pending == [mock_pending]

    @pytest.mark.asyncio
    async def test_add_all_success_adds_all(self):
        """All valid URLs with successful into_pending are all added to pending."""
        items = [MagicMock(), MagicMock()]
        urls_objs = [_mock_url(pending=p) for p in items]
        idx = 0

        def _parse(_url):
            nonlocal idx
            obj = urls_objs[idx]
            idx += 1
            return obj

        with _patched_main() as main:
            with patch("streamrip.rip.main.parse_url", side_effect=_parse):
                await main.add_all(
                    [
                        "https://www.deezer.com/fr/album/1",
                        "https://www.deezer.com/fr/album/2",
                    ]
                )

        assert main.pending == items


# ---------------------------------------------------------------------------
# Main.resolve — robustness
# ---------------------------------------------------------------------------


class TestMainResolve:
    @pytest.mark.asyncio
    async def test_resolve_one_failure_does_not_drop_others(self):
        """A pending item whose resolve() raises does not prevent others from reaching media."""
        mock_media = MagicMock()
        good_pending = MagicMock()
        good_pending.resolve = AsyncMock(return_value=mock_media)
        bad_pending = MagicMock()
        bad_pending.resolve = AsyncMock(side_effect=Exception("resolve failed"))

        with _patched_main() as main:
            main.pending = [bad_pending, good_pending]
            await main.resolve()

        assert mock_media in main.media
        assert len(main.media) == 1

    @pytest.mark.asyncio
    async def test_resolve_none_result_excluded_from_media(self):
        """A pending item whose resolve() returns None is not added to media."""
        pending = MagicMock()
        pending.resolve = AsyncMock(return_value=None)

        with _patched_main() as main:
            main.pending = [pending]
            await main.resolve()

        assert main.media == []

    @pytest.mark.asyncio
    async def test_resolve_clears_pending_on_completion(self):
        """pending list is always cleared after resolve(), even when all items fail."""
        bad = MagicMock()
        bad.resolve = AsyncMock(side_effect=Exception("fail"))

        with _patched_main() as main:
            main.pending = [bad]
            await main.resolve()

        assert main.pending == []
