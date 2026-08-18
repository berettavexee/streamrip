"""End-to-end checks that converted files keep their tags and cover art.

These exercise the real ffmpeg + mutagen round trip rather than mocks, because
the failures they guard against only show up in the actual file: ffmpeg silently
drops fields across a container change, writes no tags at all into AIFF, and
carries the cover into some containers but not others.

Skipped when ffmpeg is unavailable.
"""

import base64
import os
import shutil
import subprocess

import mutagen
import pytest
from mutagen.flac import Picture
from util import arun

from streamrip import converter
from streamrip.metadata import (
    AlbumInfo,
    AlbumMetadata,
    Covers,
    TrackInfo,
    TrackMetadata,
    tag_file,
)

TEST_COVER = "tests/1x1_pixel.jpg"

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg is required for conversion tests",
)


@pytest.fixture
def meta() -> TrackMetadata:
    album = AlbumMetadata(
        AlbumInfo("alb1", 3, "FLAC"),
        "Album",
        "Album Artist",
        "2020",
        ["Rock"],
        Covers(),
        10,  # tracktotal
        1,  # disctotal
    )
    return TrackMetadata(
        info=TrackInfo(id="t1", quality=2, bit_depth=16, sampling_rate=44100),
        title="Title",
        album=album,
        artist="Artist",
        tracknumber=3,
        discnumber=1,
        composer="Composer",
        isrc="USRC12345678",
        lyrics="La la la",
    )


@pytest.fixture
def tagged_flac(tmp_path, meta) -> str:
    """A short FLAC carrying a full tag set and exactly one cover picture."""
    path = str(tmp_path / "source.flac")
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-sample_fmt", "s16", "-c:a", "flac", path,
        ],
        check=True,
    )
    arun(tag_file(path, meta, TEST_COVER))
    return path


def _convert(path: str, codec: str) -> str:
    engine = converter.get(codec)(
        filename=path, sampling_rate=48000, bit_depth=24, remove_source=False
    )
    arun(engine.convert())
    return engine.final_fn


def _cover_count(path: str) -> int:
    """Count embedded cover images, whichever way the container stores them."""
    audio = mutagen.File(path)
    if getattr(audio, "pictures", None):
        return len(audio.pictures)
    if audio.tags is None:
        return 0
    if "metadata_block_picture" in audio.tags:  # ogg / opus
        return len(audio.tags["metadata_block_picture"])
    if hasattr(audio.tags, "getall"):
        return len(audio.tags.getall("APIC"))
    return len(audio.tags.get("covr", []))


def test_ffmpeg_writes_no_tags_into_aiff(tagged_flac):
    """The premise of the re-tagging in Track._convert.

    If this ever starts failing because ffmpeg learned to write AIFF tags, the
    re-tag becomes redundant rather than wrong -- but it is worth knowing.
    """
    out = _convert(tagged_flac, "AIFF")
    assert mutagen.File(out).tags is None


def test_retagging_aiff_writes_tags_and_cover(tagged_flac, meta):
    out = _convert(tagged_flac, "AIFF")
    arun(tag_file(out, meta, TEST_COVER))

    tags = mutagen.File(out).tags
    assert tags["TIT2"].text == ["Title"]
    assert tags["TALB"].text == ["Album"]
    assert tags["TPE1"].text == ["Artist"]
    assert tags["TSRC"].text == ["USRC12345678"]
    assert _cover_count(out) == 1


@pytest.mark.parametrize(
    ("codec", "dropped"),
    [("MP3", "TSRC"), ("ALAC", "----:com.apple.iTunes:ISRC")],
)
def test_ffmpeg_drops_isrc_and_retag_restores_it(tagged_flac, meta, codec, dropped):
    """ffmpeg does not carry ISRC across a container change; the re-tag does."""
    out = _convert(tagged_flac, codec)
    assert dropped not in mutagen.File(out).tags

    arun(tag_file(out, meta, TEST_COVER))
    assert dropped in mutagen.File(out).tags


@pytest.mark.parametrize("codec", ["FLAC", "MP3", "ALAC", "AIFF"])
def test_retagging_never_duplicates_the_cover(tagged_flac, meta, codec):
    """Re-tagging a file that already has art must replace it, not append.

    FLAC is the one that bites: ffmpeg carries the picture over, and
    Picture.add_picture() appends where the ID3 and MP4 paths replace.
    """
    out = _convert(tagged_flac, codec)
    arun(tag_file(out, meta, TEST_COVER))
    assert _cover_count(out) == 1

    arun(tag_file(out, meta, TEST_COVER))  # tagging is idempotent
    assert _cover_count(out) == 1


@pytest.mark.parametrize("codec", ["OPUS", "OGG"])
def test_opus_and_ogg_keep_their_cover_through_conversion(tagged_flac, codec):
    """These containers get no re-tag -- tag_file() would raise on them.

    Their cover is embedded by the converter itself (_embed_cover_art, via
    METADATA_BLOCK_PICTURE), reading the art off the source file before ffmpeg
    runs. This guards that path against changes to how the source is tagged.
    """
    out = _convert(tagged_flac, codec)

    assert _cover_count(out) == 1
    picture = Picture(
        base64.b64decode(mutagen.File(out).tags["metadata_block_picture"][0])
    )
    with open(TEST_COVER, "rb") as img:
        assert picture.data == img.read()
    assert picture.mime == "image/jpeg"


@pytest.mark.parametrize("codec", ["OPUS", "OGG"])
def test_tag_file_rejects_opus_and_ogg(tmp_path, tagged_flac, meta, codec):
    """Why Track._convert gates the re-tag on the extension."""
    out = _convert(tagged_flac, codec)
    with pytest.raises(Exception, match="Invalid extension"):
        arun(tag_file(out, meta, TEST_COVER))


def test_aiff_is_readable_and_lossless_after_conversion(tagged_flac):
    out = _convert(tagged_flac, "AIFF")
    audio = mutagen.File(out)

    assert os.path.splitext(out)[1] == ".aiff"
    assert audio.info.bits_per_sample == 24
    assert round(audio.info.length) == 6
