"""Tests for the codec registry in streamrip/converter.py.

These don't run ffmpeg -- converter.get() only resolves a class, and the
ffmpeg availability check lives in Converter.__init__.
"""

import pytest

from streamrip import converter

# Codec name accepted by the CLI / config → (class, container, lossless)
EXPECTED = {
    "FLAC": (converter.FLAC, "flac", True),
    "ALAC": (converter.ALAC, "m4a", True),
    "AIFF": (converter.AIFF, "aiff", True),
    "MP3": (converter.LAME, "mp3", False),
    "OPUS": (converter.OPUS, "opus", False),
    "OGG": (converter.Vorbis, "ogg", False),
    "VORBIS": (converter.Vorbis, "ogg", False),
    "AAC": (converter.AAC, "m4a", False),
    "M4A": (converter.AAC, "m4a", False),
}


@pytest.mark.parametrize(("codec", "expected"), EXPECTED.items())
def test_get_resolves_codec(codec, expected):
    cls, container, lossless = expected
    assert converter.get(codec) is cls
    assert cls.container == container
    assert cls.lossless is lossless


@pytest.mark.parametrize("codec", ["aiff", "AiFf", "aif", "AIF"])
def test_get_aiff_is_case_insensitive_and_accepts_aif(codec):
    assert converter.get(codec) is converter.AIFF


def test_aiff_encodes_24_bit_pcm():
    """AIFF is written as big-endian 24-bit PCM regardless of the source.

    Note this ignores the configured conversion bit_depth: a 16-bit source
    converted to AIFF is upsampled to 24 bits by ffmpeg rather than staying
    16-bit. Lossless, but larger than necessary.
    """
    assert converter.AIFF.codec_lib == "pcm_s24be"


def test_get_unknown_codec_raises():
    with pytest.raises(KeyError):
        converter.get("WAV")
