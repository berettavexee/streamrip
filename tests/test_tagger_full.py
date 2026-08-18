"""Extended coverage for streamrip/metadata/tagger.py — MP3, AAC, edge cases."""

from unittest.mock import MagicMock, patch

import pytest
from mutagen.id3 import ID3, TIT2
from util import arun

from streamrip.metadata import (
    AlbumInfo,
    AlbumMetadata,
    Covers,
    TrackInfo,
    TrackMetadata,
    tag_file,
)
from streamrip.metadata.tagger import (
    FLAC_MAX_BLOCKSIZE,
    TAGGABLE_EXTENSIONS,
    Container,
)

TEST_COVER = "tests/1x1_pixel.jpg"


@pytest.fixture
def full_meta():
    album = AlbumMetadata(
        AlbumInfo("alb1", 3, "FLAC"),
        "Album",
        "Album Artist",
        "2020",
        ["Rock", "Electronic"],
        Covers(),
        10,  # tracktotal
        2,   # disctotal
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
        author="Lyricist",
        bpm=128,
        replaygain_track_gain="-9.50 dB",
        lyrics="La la la",
    )


# ---------------------------------------------------------------------------
# Container.get_mutagen_class
# ---------------------------------------------------------------------------


def test_get_mutagen_class_aac_calls_mp4():
    with patch("streamrip.metadata.tagger.MP4") as mock_mp4:
        result = Container.AAC.get_mutagen_class("dummy.m4a")
    mock_mp4.assert_called_once_with("dummy.m4a")
    assert result is mock_mp4.return_value


def test_get_mutagen_class_mp3_with_header(tmp_path):
    p = tmp_path / "tagged.mp3"
    audio = ID3()
    audio.add(TIT2(encoding=3, text=["test"]))
    audio.save(str(p))
    result = Container.MP3.get_mutagen_class(str(p))
    assert isinstance(result, ID3)


def test_get_mutagen_class_mp3_no_header(tmp_path):
    p = tmp_path / "bare.mp3"
    p.write_bytes(b"")
    result = Container.MP3.get_mutagen_class(str(p))
    assert isinstance(result, ID3)


# ---------------------------------------------------------------------------
# Container.get_tag_pairs dispatch
# ---------------------------------------------------------------------------


def test_get_tag_pairs_mp3_returns_list(full_meta):
    pairs = Container.MP3.get_tag_pairs(full_meta)
    assert isinstance(pairs, list)
    assert len(pairs) > 0


def test_get_tag_pairs_aac_returns_list(full_meta):
    pairs = Container.AAC.get_tag_pairs(full_meta)
    assert isinstance(pairs, list)
    assert len(pairs) > 0


# ---------------------------------------------------------------------------
# _tag_mp3
# ---------------------------------------------------------------------------


def test_tag_mp3_basic_fields(full_meta):
    keys = [k for k, _ in Container.MP3.get_tag_pairs(full_meta)]
    assert "TIT2" in keys
    assert "TPE1" in keys
    assert "TALB" in keys


def test_tag_mp3_tracknumber_slash_total(full_meta):
    pairs = dict(Container.MP3.get_tag_pairs(full_meta))
    assert "TRCK" in pairs
    text = str(pairs["TRCK"].text[0])
    assert "3" in text
    assert "10" in text


def test_tag_mp3_discnumber_slash_total(full_meta):
    pairs = dict(Container.MP3.get_tag_pairs(full_meta))
    assert "TPOS" in pairs
    text = str(pairs["TPOS"].text[0])
    assert "1" in text
    assert "2" in text


def test_tag_mp3_replaygain_present(full_meta):
    keys = [k for k, _ in Container.MP3.get_tag_pairs(full_meta)]
    assert "TXXX:replaygain_track_gain" in keys


def test_tag_mp3_replaygain_absent(full_meta):
    full_meta.replaygain_track_gain = None
    keys = [k for k, _ in Container.MP3.get_tag_pairs(full_meta)]
    assert "TXXX:replaygain_track_gain" not in keys


def test_tag_mp3_null_key_fields_not_in_output(full_meta):
    # tracktotal, disctotal, date have v=None in MP3_KEY and must be omitted
    for _k, v in Container.MP3.get_tag_pairs(full_meta):
        assert v is not None


# ---------------------------------------------------------------------------
# _tag_mp4
# ---------------------------------------------------------------------------


def test_tag_mp4_title(full_meta):
    keys = [k for k, _ in Container.AAC.get_tag_pairs(full_meta)]
    assert "\xa9nam" in keys


def test_tag_mp4_tracknumber_tuple(full_meta):
    pairs = dict(Container.AAC.get_tag_pairs(full_meta))
    assert pairs["trkn"] == [(3, 10)]


def test_tag_mp4_discnumber_tuple(full_meta):
    pairs = dict(Container.AAC.get_tag_pairs(full_meta))
    assert pairs["disk"] == [(1, 2)]


def test_tag_mp4_isrc_bytes(full_meta):
    pairs = dict(Container.AAC.get_tag_pairs(full_meta))
    assert pairs["----:com.apple.iTunes:ISRC"] == b"USRC12345678"


def test_tag_mp4_isrc_none_skipped(full_meta):
    full_meta.isrc = None
    keys = [k for k, _ in Container.AAC.get_tag_pairs(full_meta)]
    assert "----:com.apple.iTunes:ISRC" not in keys


def test_tag_mp4_author_bytes(full_meta):
    pairs = dict(Container.AAC.get_tag_pairs(full_meta))
    assert pairs["----:com.apple.iTunes:LYRICIST"] == b"Lyricist"


def test_tag_mp4_author_none_skipped(full_meta):
    full_meta.author = None
    keys = [k for k, _ in Container.AAC.get_tag_pairs(full_meta)]
    assert "----:com.apple.iTunes:LYRICIST" not in keys


def test_tag_mp4_replaygain_bytes(full_meta):
    pairs = dict(Container.AAC.get_tag_pairs(full_meta))
    assert pairs["----:com.apple.iTunes:REPLAYGAIN_TRACK_GAIN"] == b"-9.50 dB"


def test_tag_mp4_replaygain_none_skipped(full_meta):
    full_meta.replaygain_track_gain = None
    keys = [k for k, _ in Container.AAC.get_tag_pairs(full_meta)]
    assert "----:com.apple.iTunes:REPLAYGAIN_TRACK_GAIN" not in keys


def test_tag_mp4_bpm_int_list(full_meta):
    pairs = dict(Container.AAC.get_tag_pairs(full_meta))
    assert pairs["tmpo"] == [128]


def test_tag_mp4_bpm_none_skipped(full_meta):
    full_meta.bpm = None
    keys = [k for k, _ in Container.AAC.get_tag_pairs(full_meta)]
    assert "tmpo" not in keys


# ---------------------------------------------------------------------------
# embed_cover
# ---------------------------------------------------------------------------


async def test_embed_cover_flac_too_big():
    with patch(
        "streamrip.metadata.tagger.os.path.getsize",
        return_value=FLAC_MAX_BLOCKSIZE + 1,
    ):
        with pytest.raises(Exception, match="too big"):
            await Container.FLAC.embed_cover(MagicMock(), "dummy.jpg")


async def test_embed_cover_mp3():
    audio = MagicMock()
    await Container.MP3.embed_cover(audio, TEST_COVER)
    audio.add.assert_called_once()
    apic = audio.add.call_args[0][0]
    assert apic.type == 3
    assert apic.mime == "image/jpeg"


async def test_embed_cover_aac():
    audio = MagicMock()
    await Container.AAC.embed_cover(audio, TEST_COVER)
    audio.__setitem__.assert_called_once()
    key = audio.__setitem__.call_args[0][0]
    assert key == "covr"


# ---------------------------------------------------------------------------
# save_audio
# ---------------------------------------------------------------------------


def test_save_audio_aac():
    audio = MagicMock()
    Container.AAC.save_audio(audio, "dummy.m4a")
    audio.save.assert_called_once_with()


def test_save_audio_mp3():
    audio = MagicMock()
    Container.MP3.save_audio(audio, "dummy.mp3")
    audio.save.assert_called_once_with("dummy.mp3", "v2_version=3")


# ---------------------------------------------------------------------------
# tag_file dispatch
# ---------------------------------------------------------------------------


async def test_tag_file_mp3_end_to_end(full_meta, tmp_path):
    p = tmp_path / "track.mp3"
    p.write_bytes(b"")  # empty → ID3NoHeaderError → ID3() fallback
    await tag_file(str(p), full_meta, None)
    result = ID3(str(p))
    assert "TIT2" in result


async def test_tag_file_m4a_dispatches_to_aac(full_meta, tmp_path):
    p = tmp_path / "track.m4a"
    mock_audio = MagicMock()
    with patch("streamrip.metadata.tagger.MP4", return_value=mock_audio):
        await tag_file(str(p), full_meta, None)
    mock_audio.save.assert_called_once()


async def test_tag_file_invalid_extension_raises(full_meta, tmp_path):
    p = tmp_path / "track.ogg"
    with pytest.raises(Exception, match="Invalid extension"):
        await tag_file(str(p), full_meta, None)


# ---------------------------------------------------------------------------
# Container.AIFF
# ---------------------------------------------------------------------------


def _mock_aiff() -> MagicMock:
    """An AIFF with no ID3 chunk, whose add_tags() creates one like mutagen's."""
    audio = MagicMock()
    audio.tags = None

    def add_tags():
        audio.tags = MagicMock()

    audio.add_tags.side_effect = add_tags
    return audio


def test_get_mutagen_class_aiff_adds_tags_when_missing():
    """A freshly converted AIFF has no ID3 chunk; one is created."""
    audio = _mock_aiff()
    with patch("streamrip.metadata.tagger.AIFF", return_value=audio):
        result = Container.AIFF.get_mutagen_class("dummy.aiff")
    audio.add_tags.assert_called_once_with()
    assert result is audio.tags


def test_get_mutagen_class_aiff_keeps_existing_tags():
    """add_tags() raises when a chunk already exists, so it must not be called."""
    audio = MagicMock()
    audio.tags = MagicMock()
    with patch("streamrip.metadata.tagger.AIFF", return_value=audio):
        result = Container.AIFF.get_mutagen_class("dummy.aiff")
    audio.add_tags.assert_not_called()
    assert result is audio.tags


def test_get_tag_pairs_aiff_matches_mp3(full_meta):
    """AIFF carries ID3 frames, so it reuses the MP3 tag pairs verbatim."""
    assert Container.AIFF.get_tag_pairs(full_meta) == Container.MP3.get_tag_pairs(
        full_meta
    )


async def test_embed_cover_aiff_uses_apic():
    audio = MagicMock()
    await Container.AIFF.embed_cover(audio, TEST_COVER)
    audio.add.assert_called_once()
    cover = audio.add.call_args[0][0]
    assert cover.mime == "image/jpeg"
    assert cover.type == 3


def test_save_audio_aiff_passes_path_without_v2_version():
    """_IFFID3 carries no filename of its own and takes no v2_version arg."""
    audio = MagicMock()
    Container.AIFF.save_audio(audio, "dummy.aiff")
    audio.save.assert_called_once_with("dummy.aiff")


@pytest.mark.parametrize("ext", ["aiff", "aif", "AIFF", "Aif"])
async def test_tag_file_dispatches_aiff_extensions(full_meta, tmp_path, ext):
    p = tmp_path / f"track.{ext}"
    audio = _mock_aiff()
    with patch("streamrip.metadata.tagger.AIFF", return_value=audio):
        await tag_file(str(p), full_meta, None)
    audio.tags.save.assert_called_once_with(str(p))


def test_taggable_extensions_matches_tag_file_dispatch(full_meta, tmp_path):
    """TAGGABLE_EXTENSIONS is what Track._convert gates on; keep it truthful.

    Every listed extension must be accepted by tag_file, and anything outside
    the list must be rejected -- otherwise _convert either skips a container it
    could have tagged, or calls tag_file on one that raises.
    """
    for ext in TAGGABLE_EXTENSIONS:
        p = tmp_path / f"track.{ext}"
        try:
            arun(tag_file(str(p), full_meta, None))
        except Exception as e:
            # The empty file makes mutagen unhappy for most containers; only
            # the extension check itself is under test here.
            assert "Invalid extension" not in str(e), ext

    for ext in ("ogg", "opus", "wav"):
        p = tmp_path / f"track.{ext}"
        with pytest.raises(Exception, match="Invalid extension"):
            arun(tag_file(str(p), full_meta, None))
