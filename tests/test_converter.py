"""Tests for the codec registry in streamrip/converter.py.

These don't run ffmpeg -- converter.get() only resolves a class, and the
ffmpeg availability check lives in Converter.__init__.
"""

import os

import pytest
from util import arun

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


@pytest.mark.parametrize(
    ("cls", "rate", "arg"),
    [
        # A rate matching a LAME VBR preset uses it; any other is CBR.
        (converter.LAME, 245, "-q:a 0"),
        (converter.LAME, 190, "-q:a 2"),
        (converter.LAME, 320, "-b:a 320k"),
        (converter.LAME, 192, "-b:a 192k"),
        # libvorbis quality scale: q4 ≈ 128, q5 ≈ 160, q8 ≈ 256, q9 ≈ 320.
        (converter.Vorbis, 128, "-q:a 4"),
        (converter.Vorbis, 160, "-q:a 5"),
        (converter.Vorbis, 256, "-q:a 8"),
        (converter.Vorbis, 320, "-q:a 9"),
        (converter.Vorbis, 32, "-q:a -1"),
        (converter.OPUS, 96, "-b:a 96k"),
        (converter.AAC, 192, "-b:a 192k"),
    ],
)
def test_lossy_quality_arg_follows_lossy_bitrate(cls, rate, arg):
    assert cls.get_quality_arg(rate) == arg


@pytest.mark.parametrize("cls", [converter.FLAC, converter.ALAC, converter.AIFF])
def test_lossless_quality_arg_is_the_default(cls):
    assert cls.get_quality_arg(128) == cls.default_ffmpeg_arg


@pytest.fixture
def no_ffmpeg_check(monkeypatch):
    """Converter.__init__ only checks that ffmpeg is on PATH."""
    monkeypatch.setattr(converter.shutil, "which", lambda _: "/usr/bin/ffmpeg")


def test_same_file_name_gets_distinct_temp_files(no_ffmpeg_check):
    """Tracks of different albums share names ("01. Intro"); their concurrent
    conversions used to write the same temp file."""
    a = converter.LAME("/music/Album A/01. Intro.flac")
    b = converter.LAME("/music/Album B/01. Intro.flac")
    assert a.tempfile != b.tempfile
    assert a.tempfile.endswith("01. Intro.mp3")


def test_failed_conversion_removes_its_temp_file(
    no_ffmpeg_check, monkeypatch, tmp_path
):
    src = tmp_path / "01. Song.flac"
    src.write_bytes(b"flac")
    conv = converter.LAME(str(src))
    # What ffmpeg had written of its output when it failed.
    with open(conv.tempfile, "wb") as f:
        f.write(b"partial")

    class FailedProcess:
        returncode = 1

        async def communicate(self):
            return None, b"error"

    async def fake_exec(*_args, **_kwargs):
        return FailedProcess()

    monkeypatch.setattr(converter.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(converter.ConversionError):
        arun(conv.convert())

    assert not os.path.exists(conv.tempfile)
    assert src.exists()  # the source is only removed after a success


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.mp3", "MP3"),
        ("a.MP3", "MP3"),
        ("a.ogg", "Vorbis"),
        ("a.opus", "Opus"),
        ("a.flac", None),
        ("a.aiff", None),
    ],
)
def test_lossy_source_codec_by_extension(name, expected):
    assert converter.lossy_source_codec(name) == expected


@pytest.mark.parametrize(("codec", "expected"), [("mp4a.40.2", "AAC"), ("alac", None)])
def test_lossy_source_codec_reads_m4a(monkeypatch, codec, expected):
    """An .m4a holds AAC or ALAC; only the file can tell which."""
    import mutagen.mp4

    fake = type("FakeMP4", (), {"info": type("Info", (), {"codec": codec})()})
    monkeypatch.setattr(mutagen.mp4, "MP4", lambda _path: fake)
    assert converter.lossy_source_codec("a.m4a") == expected


def test_lossy_source_codec_unreadable_m4a_is_not_called_lossy(tmp_path):
    path = tmp_path / "broken.m4a"
    path.write_bytes(b"not an mp4")
    assert converter.lossy_source_codec(str(path)) is None
