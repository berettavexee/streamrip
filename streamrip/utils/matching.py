import re
from difflib import SequenceMatcher

_COLLAB_CREDIT_RE = re.compile(
    r"\s*[\(\[](with|feat\.?|ft\.?|featuring)\s+[^\)\]]+[\)\]]",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    """Lowercase, strip parenthetical expressions, punctuation, and extra spaces."""
    text = text.lower()
    text = re.sub(r"\(.*?\)|\[.*?\]", "", text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def strip_collab(title: str) -> str:
    """Remove collaboration credits like '(feat. X)' or '[with Y]' from a title."""
    return _COLLAB_CREDIT_RE.sub("", title).strip()


def string_similarity(a: str, b: str) -> float:
    """Normalized edit-distance similarity between two strings after normalization."""
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def score_similarity(
    query_title: str,
    query_artists: list[str],
    result_title: str,
    result_artist: str,
) -> float:
    """Return a composite [0, 1] score for how well a search result matches a query.

    Args:
        query_title: Title from the Last.fm playlist entry.
        query_artists: Artist names from the Last.fm playlist entry (usually one).
        result_title: Title of the candidate result from the streaming API.
        result_artist: Primary artist of the candidate result.

    Returns:
        A float in [0, 1] where higher means a better match.
    """
    title_score = string_similarity(query_title, result_title)
    artist_score = max(
        (string_similarity(a, result_artist) for a in query_artists), default=0.0
    )
    title_artist_score = max(
        (string_similarity(a, result_title) for a in query_artists), default=0.0
    )
    best_artist_score = max(artist_score, title_artist_score * 0.8)
    composite = 0.60 * title_score + 0.40 * best_artist_score

    if title_score >= 0.55 and composite < 0.35:
        return title_score * 0.75

    if best_artist_score < 0.45 and result_artist.strip():
        if title_score >= 0.90:
            pass
        elif title_score >= 0.75:
            return min(composite, 0.55)
        else:
            return min(composite, 0.50)

    return composite


def duration_close(expected_s: float, actual_s: float, tolerance: int = 10) -> bool:
    """Return True when two durations are close enough to be the same track.

    Args:
        expected_s: Expected duration in seconds.
        actual_s: Actual duration in seconds.
        tolerance: Minimum allowed difference in seconds (default 10).

    Returns:
        True if the absolute difference is within an adaptive tolerance derived
        from track length (4.5 % of the longer duration, at least `tolerance`
        seconds and at most 30 seconds).
    """
    try:
        expected = float(expected_s)
        actual = float(actual_s)
    except (TypeError, ValueError):
        return False
    longer = max(expected, actual)
    adaptive_tolerance = min(30.0, longer * 0.045)
    effective_tolerance = max(float(tolerance), adaptive_tolerance)
    return abs(expected - actual) <= effective_tolerance
