import logging
import os

import mutagen

logger = logging.getLogger("streamrip")

# There is deliberately no bitrate floor here. One used to compare
# size / duration against a minimum per quality tier, and it was wrong both
# ways: a complete FLAC of digital silence (10 s ≈ 10 KB, ~8 kbps) failed it,
# so a valid track was recorded as failed on every run, while a truncated FLAC
# only fell under it once cut to about a tenth of its size. Truncation is
# caught upstream instead: on a short body, requests raises
# ChunkedEncodingError and aiohttp ClientPayloadError, and the download is
# retried.


def check_integrity(path: str) -> tuple[bool, str]:
    """Check that a downloaded file is audio streamrip can read.

    This catches what the transport cannot: an empty file, or a body that is
    not audio at all (an error page or a JSON message saved under an audio
    extension), which mutagen fails to parse or to give a duration for. It
    does not judge whether the audio is complete -- see the module comment.

    Args:
        path: Absolute path to the audio file on disk.

    Returns:
        A ``(ok, reason)`` tuple. ``ok`` is ``True`` when the file passes the
        check. When ``ok`` is ``False``, ``reason`` contains a human-readable
        explanation suitable for an ERROR log line.
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

    return True, ""
