"""Tests for streamrip/utils/integrity.py."""

from unittest.mock import MagicMock, patch

from streamrip.utils.integrity import check_integrity


def _mock_audio(length: float) -> MagicMock:
    audio = MagicMock()
    audio.info.length = length
    return audio


def _patches(size: int, audio):
    """Return the two patches needed by most tests as a tuple for use with 'with'."""
    return (
        patch("streamrip.utils.integrity.os.path.getsize", return_value=size),
        patch("streamrip.utils.integrity.mutagen.File", return_value=audio),
    )


# ---------------------------------------------------------------------------
# File-level failures
# ---------------------------------------------------------------------------


def test_empty_file_fails():
    with patch("streamrip.utils.integrity.os.path.getsize", return_value=0):
        ok, reason = check_integrity("/fake/track.flac")
    assert not ok
    assert "empty" in reason


def test_stat_error_fails():
    with patch(
        "streamrip.utils.integrity.os.path.getsize", side_effect=OSError("no such file")
    ):
        ok, reason = check_integrity("/fake/track.flac")
    assert not ok
    assert "cannot stat" in reason


def test_mutagen_error_fails():
    with (
        patch("streamrip.utils.integrity.os.path.getsize", return_value=5_000_000),
        patch(
            "streamrip.utils.integrity.mutagen.File",
            side_effect=Exception("bad header"),
        ),
    ):
        ok, reason = check_integrity("/fake/track.flac")
    assert not ok
    assert "mutagen" in reason


def test_mutagen_returns_none_fails():
    with (
        patch("streamrip.utils.integrity.os.path.getsize", return_value=5_000_000),
        patch("streamrip.utils.integrity.mutagen.File", return_value=None),
    ):
        ok, reason = check_integrity("/fake/track.flac")
    assert not ok
    assert "unknown audio format" in reason


def test_missing_duration_fails():
    audio = MagicMock()
    audio.info.length = None
    with (
        patch("streamrip.utils.integrity.os.path.getsize", return_value=5_000_000),
        patch("streamrip.utils.integrity.mutagen.File", return_value=audio),
    ):
        ok, reason = check_integrity("/fake/track.flac")
    assert not ok
    assert "duration" in reason


def test_zero_duration_fails():
    audio = _mock_audio(length=0.0)
    with (
        patch("streamrip.utils.integrity.os.path.getsize", return_value=5_000_000),
        patch("streamrip.utils.integrity.mutagen.File", return_value=audio),
    ):
        ok, _reason = check_integrity("/fake/track.flac")
    assert not ok


# ---------------------------------------------------------------------------
# No bitrate judgement
# ---------------------------------------------------------------------------


def test_complete_silent_flac_passes():
    """A complete FLAC of digital silence is valid audio.

    10 s of silence is under 10 KB (~8 kbps). The bitrate floor this check
    used to apply rejected it, so the track was recorded as failed on every
    run.
    """
    ok, reason = check_integrity("tests/silence_10s.flac")
    assert ok, reason


def test_low_bitrate_is_not_a_failure():
    """Size over duration says nothing about completeness: 1 kbps passes."""
    duration = 300.0
    audio = _mock_audio(length=duration)
    p_size, p_file = _patches(int(1000 / 8 * duration), audio)
    with p_size, p_file:
        ok, reason = check_integrity("/fake/track.flac")
    assert ok, reason
