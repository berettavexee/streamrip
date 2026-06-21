"""Tests for streamrip/utils/integrity.py."""

from unittest.mock import MagicMock, patch

import pytest

from streamrip.utils.integrity import _MIN_DURATION_S, _MIN_KBPS, check_integrity


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
        ok, reason = check_integrity("/fake/track.flac", quality=2)
    assert not ok
    assert "empty" in reason


def test_stat_error_fails():
    with patch("streamrip.utils.integrity.os.path.getsize",
               side_effect=OSError("no such file")):
        ok, reason = check_integrity("/fake/track.flac", quality=2)
    assert not ok
    assert "cannot stat" in reason


def test_mutagen_error_fails():
    with (
        patch("streamrip.utils.integrity.os.path.getsize", return_value=5_000_000),
        patch("streamrip.utils.integrity.mutagen.File", side_effect=Exception("bad header")),
    ):
        ok, reason = check_integrity("/fake/track.flac", quality=2)
    assert not ok
    assert "mutagen" in reason


def test_mutagen_returns_none_fails():
    with (
        patch("streamrip.utils.integrity.os.path.getsize", return_value=5_000_000),
        patch("streamrip.utils.integrity.mutagen.File", return_value=None),
    ):
        ok, reason = check_integrity("/fake/track.flac", quality=2)
    assert not ok
    assert "unknown audio format" in reason


def test_missing_duration_fails():
    audio = MagicMock()
    audio.info.length = None
    with (
        patch("streamrip.utils.integrity.os.path.getsize", return_value=5_000_000),
        patch("streamrip.utils.integrity.mutagen.File", return_value=audio),
    ):
        ok, reason = check_integrity("/fake/track.flac", quality=2)
    assert not ok
    assert "duration" in reason


def test_zero_duration_fails():
    audio = _mock_audio(length=0.0)
    with (
        patch("streamrip.utils.integrity.os.path.getsize", return_value=5_000_000),
        patch("streamrip.utils.integrity.mutagen.File", return_value=audio),
    ):
        ok, _reason = check_integrity("/fake/track.flac", quality=2)
    assert not ok


# ---------------------------------------------------------------------------
# Short-track bypass
# ---------------------------------------------------------------------------


def test_short_track_skips_check():
    """Tracks shorter than _MIN_DURATION_S pass without bitrate check."""
    audio = _mock_audio(length=_MIN_DURATION_S - 0.1)
    with (
        patch("streamrip.utils.integrity.os.path.getsize", return_value=100),
        patch("streamrip.utils.integrity.mutagen.File", return_value=audio),
    ):
        ok, reason = check_integrity("/fake/jingle.mp3", quality=0)
    assert ok
    assert reason == ""


# ---------------------------------------------------------------------------
# Happy paths — one per quality level
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("quality", [0, 1, 2, 3])
def test_healthy_file_passes(quality):
    """A file whose effective kbps is well above the minimum should pass."""
    min_kbps = _MIN_KBPS[quality]
    duration = 200.0
    size = int(min_kbps * 2 * 1000 / 8 * duration)
    audio = _mock_audio(length=duration)
    with (
        patch("streamrip.utils.integrity.os.path.getsize", return_value=size),
        patch("streamrip.utils.integrity.mutagen.File", return_value=audio),
    ):
        ok, _reason = check_integrity("/fake/track.flac", quality=quality)
    assert ok


# ---------------------------------------------------------------------------
# Truncation detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("quality,min_kbps", _MIN_KBPS.items())
def test_truncated_file_fails(quality, min_kbps):
    """Effective kbps below the minimum triggers a failure."""
    duration = 200.0
    size = int(min_kbps * 0.4 * 1000 / 8 * duration)
    audio = _mock_audio(length=duration)
    with (
        patch("streamrip.utils.integrity.os.path.getsize", return_value=size),
        patch("streamrip.utils.integrity.mutagen.File", return_value=audio),
    ):
        ok, reason = check_integrity("/fake/track.flac", quality=quality)
    assert not ok
    assert "truncated" in reason or "corrupt" in reason
    assert str(min_kbps) in reason


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_unknown_quality_uses_fallback_threshold():
    """Quality values outside [0, 3] fall back to the default minimum (50 kbps)."""
    duration = 120.0
    size = int(500 * 1000 / 8 * duration)  # ~500 kbps effective
    audio = _mock_audio(length=duration)
    with (
        patch("streamrip.utils.integrity.os.path.getsize", return_value=size),
        patch("streamrip.utils.integrity.mutagen.File", return_value=audio),
    ):
        ok, _ = check_integrity("/fake/track.flac", quality=99)
    assert ok


def test_reason_contains_size_and_duration_on_failure():
    """Failure reason must include file size and duration for diagnosis."""
    duration = 300.0
    size = 10_000  # obviously too small for any quality
    audio = _mock_audio(length=duration)
    with (
        patch("streamrip.utils.integrity.os.path.getsize", return_value=size),
        patch("streamrip.utils.integrity.mutagen.File", return_value=audio),
    ):
        ok, reason = check_integrity("/fake/track.mp3", quality=1)
    assert not ok
    assert "KB" in reason
    assert "300.0" in reason
