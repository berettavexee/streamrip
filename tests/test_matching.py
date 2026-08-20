"""Unit tests for streamrip.utils.matching.

All score_similarity pairs are drawn from a real Last.fm rip log (2026-06-21).
The log recorded the winning candidate for each of 30 tracks; every pair scored
1.00 at runtime.  Tests assert score >= 0.90 to give the algorithm some slack
while still proving the real-world pairs are ranked as top results.
"""

import pytest

from streamrip.utils.matching import (
    duration_close,
    normalize,
    score_similarity,
    string_similarity,
    strip_collab,
)

# ---------------------------------------------------------------------------
# strip_collab
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title, expected",
    [
        # The canonical log example: query was sent as "The Nine Crusades Bloodbound"
        (
            "The Nine Crusades (feat. Unleash the Archers)",
            "The Nine Crusades",
        ),
        # Other common credit patterns
        ("Afterlife (feat. Brittney Slayes)", "Afterlife"),
        ("Song [with Guest Artist]", "Song"),
        ("Track (ft. Someone)", "Track"),
        ("Track (featuring Someone Else)", "Track"),
        # Titles with non-credit parentheticals must be left alone
        ("Symphony No. 5 (Remastered)", "Symphony No. 5 (Remastered)"),
        ("Enter The Cipher", "Enter The Cipher"),
        # Title already clean
        ("True Believer", "True Believer"),
    ],
)
def test_strip_collab(title: str, expected: str) -> None:
    assert strip_collab(title) == expected


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Enter The Cipher", "enter the cipher"),
        ("Beast In Black", "beast in black"),
        ("GALDERIA", "galderia"),
        ("ANGUS McSIX", "angus mcsix"),
        # Punctuation stripped
        ("Mr. White", "mr white"),
        ("Circe's Spell", "circe s spell"),
        ("Draugen's Maelstrom", "draugen s maelstrom"),
        # Parenthetical expressions removed
        ("The Nine Crusades (feat. Unleash the Archers)", "the nine crusades"),
        # Extra whitespace collapsed
        ("  multiple   spaces  ", "multiple spaces"),
    ],
)
def test_normalize(text: str, expected: str) -> None:
    assert normalize(text) == expected


# ---------------------------------------------------------------------------
# string_similarity
# ---------------------------------------------------------------------------


def test_string_similarity_identical() -> None:
    assert string_similarity("Temperance", "Temperance") == pytest.approx(1.0)


def test_string_similarity_case_insensitive() -> None:
    # "Beast in Black" vs "Beast In Black" — only case differs after normalize
    assert string_similarity("Beast in Black", "Beast In Black") == pytest.approx(1.0)


def test_string_similarity_all_caps() -> None:
    # "GALDERIA" vs "Galderia" — both reduce to "galderia"
    assert string_similarity("GALDERIA", "Galderia") == pytest.approx(1.0)


def test_string_similarity_empty_returns_zero() -> None:
    assert string_similarity("", "Temperance") == pytest.approx(0.0)
    assert string_similarity("Temperance", "") == pytest.approx(0.0)


def test_string_similarity_unrelated_is_low() -> None:
    assert string_similarity("Afterlife", "Freedom Call") < 0.4


# ---------------------------------------------------------------------------
# score_similarity — log pairs (all scored 1.00 at runtime)
# ---------------------------------------------------------------------------


# Format: (query_title, query_artist, result_title, result_artist, description)  # ruff: ignore[commented-out-code]
_LOG_PAIRS = [
    # Simple exact match with case variants in artist
    (
        "Enter The Cipher",
        "Follow the Cipher",
        "Enter the Cipher",
        "Follow The Cipher",
        "artist capitalisation variant",
    ),
    # Identical title, identical artist
    (
        "Litany of the Northern Lights",
        "Temperance",
        "Litany of the Northern Lights",
        "Temperance",
        "exact match",
    ),
    # Short title, exact match
    (
        "Hellfire",
        "Visions of Atlantis",
        "Hellfire",
        "Visions of Atlantis",
        "short title exact",
    ),
    # Title capitalisation differs
    (
        "Wake Up The World",
        "Galderia",
        "Wake up the World",
        "GALDERIA",
        "title+artist caps variants",
    ),
    # All-caps artist
    (
        "Master of the Universe",
        "Angus McSix",
        "Master of the Universe",
        "ANGUS McSIX",
        "stylised all-caps artist",
    ),
    # Artist uses 'In' capitalisation
    (
        "True Believer",
        "Beast in Black",
        "True Believer",
        "Beast In Black",
        "minor caps diff in artist",
    ),
    # Punctuation in title (apostrophe, dot)  # ruff: ignore[commented-out-code]
    ("Mr. White", "Temperance", "Mr. White", "Temperance", "dot in title"),
    ("Circe's Spell", "Kalidia", "Circe's Spell", "Kalidia", "apostrophe in title"),
    (
        "Draugen's Maelstrom",
        "Elvenking",
        "Draugen's Maelstrom",
        "Elvenking",
        "apostrophe in title 2",
    ),
    # Long title
    (
        "If It Bleeds We Can Kill It",
        "Dragony",
        "If It Bleeds We Can Kill It",
        "Dragony",
        "long title",
    ),
    (
        "Breaking the Rules of Heavy Metal",
        "Temperance",
        "Breaking the Rules of Heavy Metal",
        "Temperance",
        "very long title",
    ),
    # Title equals artist name
    ("Freedom Call", "Freedom Call", "Freedom Call", "Freedom Call", "title == artist"),
    # Title with article "The"
    (
        "Get Out Of My Head",
        "The Dark Element",
        "Get out of My Head",
        "The Dark Element",
        "mixed case title, 'The' artist",
    ),
    # Featured artist stays in RESULT title (but was stripped from the search query)
    (
        "The Nine Crusades (feat. Unleash the Archers)",
        "Bloodbound",
        "The Nine Crusades (feat. Unleash the Archers)",
        "Bloodbound",
        "feat. credit preserved in result title",
    ),
    # Multi-word artist with apostrophe already absent
    (
        "In The Arms Of A Devil",
        "Dynazty",
        "In the Arms of a Devil",
        "Dynazty",
        "preposition capitalisation in title",
    ),
]


@pytest.mark.parametrize(
    "query_title, query_artist, result_title, result_artist, description",
    _LOG_PAIRS,
    ids=[p[4] for p in _LOG_PAIRS],
)
def test_score_similarity_log_pairs(
    query_title: str,
    query_artist: str,
    result_title: str,
    result_artist: str,
    description: str,
) -> None:
    score = score_similarity(query_title, [query_artist], result_title, result_artist)
    assert score >= 0.90, (
        f"[{description}] score={score:.3f} < 0.90 for "
        f"'{query_title}'/'{query_artist}' → '{result_title}'/'{result_artist}'"
    )


# ---------------------------------------------------------------------------
# score_similarity — negative cases (wrong matches should score low)
# ---------------------------------------------------------------------------


def test_score_wrong_title_scores_low() -> None:
    score = score_similarity(
        "Afterlife", ["Unleash the Archers"], "Hellfire", "Visions of Atlantis"
    )
    assert score < 0.55


def test_score_wrong_artist_with_common_title_penalised() -> None:
    # "Strangers" is a generic title; wrong artist should push score down
    score = score_similarity(
        "Strangers", ["Nocturna"], "Strangers", "Completely Different Band"
    )
    correct = score_similarity("Strangers", ["Nocturna"], "Strangers", "Nocturna")
    assert correct > score


def test_score_complete_mismatch_is_low() -> None:
    score = score_similarity(
        "Afterlife", ["Unleash the Archers"], "Freedom Call", "Freedom Call"
    )
    assert score < 0.40


def test_score_title_partial_no_artist_returns_partial_credit() -> None:
    """Line 60: title barely above 0.55 but composite < 0.35 → returns title_score * 0.75.

    "afterlife" vs "afterburn" share only the prefix "after" (5 chars); the
    suffixes {l,i,f,e} and {b,u,r,n} have no characters in common, giving
    SequenceMatcher a ratio of 10/18 ≈ 0.556 (just above the 0.55 threshold).

    "Kiss" (k, i, s, s) shares no characters with "afterburn" or "Bloodbound",
    so best_artist_score = 0 and composite = 0.60 x 0.556 ≈ 0.333 < 0.35.
    The branch returns title_score x 0.75 ≈ 0.417 instead of the raw 0.333.
    """
    score = score_similarity("Afterlife", ["Kiss"], "Afterburn", "Bloodbound")
    expected = (10 / 18) * 0.75  # title_score * 0.75
    assert score == pytest.approx(expected, abs=1e-6)


def test_score_title_close_wrong_artist_capped_at_055() -> None:
    """Line 66: title_score ∈ [0.75, 0.90) with best_artist_score < 0.45 → min(composite, 0.55).

    "freedom call" (12 chars) is a strict prefix of "freedom calling" (15 chars),
    giving SequenceMatcher a ratio of 24/27 = 8/9 ≈ 0.889.

    "Kiss" shares only the single char 'i' with "freedom calling"; the resulting
    best_artist_score ≈ 0.084 keeps it below the 0.45 threshold.  composite ≈ 0.567
    is capped at 0.55 by the branch.
    """
    score = score_similarity("Freedom Call", ["Kiss"], "Freedom Calling", "Bloodbound")
    assert score <= 0.55
    assert score > 0.45  # title proximity still contributes meaningfully


# ---------------------------------------------------------------------------
# duration_close
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expected, actual, close",
    [
        (210, 210, True),  # identical
        (210, 218, True),  # within 10 s default
        (210, 221, False),  # just outside 10 s
        (600, 622, True),  # adaptive: 4.5% of 622 ≈ 28 s
        (600, 632, False),  # 32 s > 30 s cap
        (0, 5, True),  # zero-length edge
        ("210", "218", True),  # string inputs coerced
        (None, 210, False),  # bad input → False
    ],
)
def test_duration_close(expected, actual, close: bool) -> None:
    assert duration_close(expected, actual) is close


def test_score_duration_bonus_applied() -> None:
    """Line 80: close durations give an 8% boost to the composite score."""
    # exact title, mismatched artist → base well below 1.0 so the bonus isn't clamped away
    base = score_similarity("Afterlife", ["Kiss"], "Afterlife", "Bloodbound")
    with_dur = score_similarity(
        "Afterlife",
        ["Kiss"],
        "Afterlife",
        "Bloodbound",
        query_duration=210,
        result_duration=215,
    )
    assert with_dur > base
    assert with_dur == pytest.approx(base * 1.08, rel=1e-5)
