import logging
import os

import mutagen

logger = logging.getLogger("streamrip")

# Minimum effective kbps required for each quality level.
# Thresholds are deliberately conservative to catch only obvious truncations
# or catastrophic failures without producing false positives on legitimately
# compressed audio.
_MIN_KBPS: dict[int, int] = {
    0: 50,  # MP3 128 kbps requested
    1: 100,  # MP3 320 kbps requested
    2: 100,  # FLAC 16-bit (compression reduces bitrate well below uncompressed)
    3: 200,  # Hi-Res FLAC (24-bit / high sample rate)
}

# Tracks shorter than this threshold are skipped: header overhead makes the
# effective-kbps metric unreliable for very short clips.
_MIN_DURATION_S = 5.0


def check_integrity(path: str, quality: int) -> tuple[bool, str]:
    """Check that a downloaded audio file's size is coherent with its duration.

    Computes an effective bitrate (``file_size * 8 / duration``) and compares
    it against a conservative minimum for the requested quality tier.  Only
    obvious anomalies — truncated downloads, empty files, unreadable formats —
    are flagged; the tolerance is intentionally wide to avoid false positives
    from variable-bitrate encoding or heavily compressed lossless audio.

    Args:
        path: Absolute path to the audio file on disk.
        quality: Quality level in [0, 3] as used throughout streamrip
            (0 = MP3 128, 1 = MP3 320, 2 = FLAC, 3 = Hi-Res FLAC).

    Returns:
        A ``(ok, reason)`` tuple.  ``ok`` is ``True`` when the file passes
        the check.  When ``ok`` is ``False``, ``reason`` contains a
        human-readable explanation suitable for a WARNING log line.
    """
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return False, f"cannot stat file: {e}"

    if size == 0:
        return False, "file is empty (0 bytes)"

    try:
        audio = mutagen.File(path)
    except Exception as e:
        return False, f"mutagen cannot open file: {e}"

    if audio is None:
        return False, "unknown audio format (mutagen returned None)"

    duration: float | None = getattr(audio.info, "length", None)
    if not duration or duration <= 0:
        return False, "cannot determine track duration"

    if duration < _MIN_DURATION_S:
        return True, ""  # too short for reliable effective-kbps check

    effective_kbps = size * 8 / 1000 / duration
    min_kbps = _MIN_KBPS.get(quality, 50)

    if effective_kbps < min_kbps:
        return False, (
            f"effective bitrate {effective_kbps:.0f} kbps is below the "
            f"{min_kbps} kbps minimum for quality={quality} "
            f"(size={size // 1024} KB, duration={duration:.1f}s) — "
            "file may be truncated or corrupt"
        )

    return True, ""
