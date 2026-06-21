from string import printable

from pathvalidate import sanitize_filename, sanitize_filepath  # type: ignore

ALLOWED_CHARS = set(printable)


# TODO: remove this when new pathvalidate release arrives with https://github.com/thombashi/pathvalidate/pull/48
def truncate_str(text: str) -> str:
    """Truncate a string to at most 255 bytes when encoded as UTF-8.

    Splits on a byte boundary and decodes with ``errors="ignore"`` so that
    incomplete multi-byte sequences at the cut point are silently dropped;
    the result is always valid UTF-8 and ≤ 255 bytes when re-encoded.

    Args:
        text: The string to truncate.

    Returns:
        The original string if it already fits within 255 UTF-8 bytes,
        otherwise a truncated version that is ≤ 255 bytes when re-encoded.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= 255:
        return text
    # Cut at 255 bytes and decode back. errors="ignore" silently drops any
    # incomplete multi-byte sequence that straddles the boundary (e.g. a CJK
    # character whose lead byte is at position 254). The result is always
    # valid UTF-8 and ≤ 255 bytes when re-encoded.
    return encoded[:255].decode("utf-8", errors="ignore")


def clean_filename(fn: str, restrict: bool = False) -> str:
    """Sanitize a string for use as a single file-name component.

    Replaces ``/`` with ``-`` so path separators become dashes rather than
    being treated as directory separators, then strips characters illegal in
    file names on Windows / macOS / Linux, and finally truncates to 255 UTF-8
    bytes.

    Args:
        fn: The raw filename string (e.g. a track title).
        restrict: When ``True``, additionally restrict to ASCII printable
            characters, removing accented letters and non-Latin scripts.

    Returns:
        A sanitized filename safe to use as a path component.
    """
    fn = fn.replace("/", "-")
    path = truncate_str(str(sanitize_filename(fn)))
    if restrict:
        path = "".join(c for c in path if c in ALLOWED_CHARS)

    return path


def clean_filepath(fn: str, restrict: bool = False) -> str:
    """Sanitize a string for use as a multi-component file path.

    Unlike :func:`clean_filename`, directory separators in *fn* are preserved
    so ``"Artist/Album"`` stays ``"Artist/Album"``; only characters illegal in
    path names are removed.

    Args:
        fn: The raw file path string, possibly containing ``/`` separators
            (e.g. ``"Artist Name/Album Title"``).
        restrict: When ``True``, additionally restrict to ASCII printable
            characters.

    Returns:
        A sanitized path string safe to use as a relative file path.
    """
    path = str(sanitize_filepath(fn))
    if restrict:
        path = "".join(c for c in path if c in ALLOWED_CHARS)

    return path
