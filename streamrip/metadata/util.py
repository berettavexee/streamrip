import functools
from typing import Optional, Type, TypeVar


def get_album_track_ids(source: str, resp) -> list[str]:
    """Extract track IDs from an album metadata response.

    Args:
        source: Service name (e.g. ``"qobuz"``, ``"deezer"``); used to
            locate the track list inside the response dict (Qobuz wraps the
            list in an extra ``"items"`` key).
        resp: Raw API response for an album, containing a ``"tracks"`` key.

    Returns:
        An ordered list of track-ID strings as returned by the service.
    """
    tracklist = resp["tracks"]
    if source == "qobuz":
        tracklist = tracklist["items"]
    return [track["id"] for track in tracklist]


def safe_get(dictionary, *keys, default=None):
    """Traverse a nested dict without raising on missing keys.

    Equivalent to chained ``dict.get()`` calls but expressed as a single
    call.  Returns *default* instead of raising ``KeyError`` or ``TypeError``
    when any intermediate key is absent or a value is not a dict.

    Args:
        dictionary: The outermost dict to traverse.
        *keys: Sequence of keys to look up in order.
        default: Value returned when any key is missing.  Defaults to ``None``.

    Returns:
        The value at ``dictionary[keys[0]][keys[1]]…`` or *default* when any
        key is absent.
    """
    return functools.reduce(
        lambda d, key: d.get(key, default) if isinstance(d, dict) else default,
        keys,
        dictionary,
    )


T = TypeVar("T")


def typed(thing, expected_type: Type[T]) -> T:
    """Assert *thing* is an instance of *expected_type* and return it typed.

    Bridges ``Any``-typed API responses to fully typed variables without
    scattering ``isinstance`` checks across the codebase.

    Args:
        thing: The value to check.
        expected_type: The type to assert and cast to.

    Returns:
        *thing* unchanged, narrowed to ``expected_type`` for the type checker.

    Raises:
        AssertionError: When ``isinstance(thing, expected_type)`` is ``False``.
    """
    assert isinstance(thing, expected_type)
    return thing


def get_quality_id(
    bit_depth: Optional[int],
    sampling_rate: Optional[int | float],
) -> int:
    """Get the universal quality id from bit depth and sampling rate.

    :param bit_depth:
    :type bit_depth: Optional[int]
    :param sampling_rate: In kHz
    :type sampling_rate: Optional[int]
    """
    # XXX: Should `0` quality be supported?
    if bit_depth is None or sampling_rate is None:  # is lossy
        return 1

    if bit_depth == 16:
        return 2

    if bit_depth == 24:
        if sampling_rate <= 96:
            return 3

        return 4

    raise Exception(f"Invalid {bit_depth = }")
