"""Tests for streamrip/metadata/track.py."""

from unittest.mock import MagicMock

import pytest

from streamrip.metadata.track import TrackInfo, TrackMetadata

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _album(quality=2, albumartist="Artist", albumcomposer=None):
    a = MagicMock()
    a.info.quality = quality
    a.albumartist = albumartist
    a.albumcomposer = albumcomposer
    return a


def _qobuz_resp(**overrides):
    base = {
        "id": 1,
        "title": "Song",
        "isrc": "USRC12345678",
        "streamable": True,
        "track_number": 1,
        "media_number": 1,
        "performer": {"name": "Artist"},
    }
    base.update(overrides)
    return base


def _deezer_resp(**overrides):
    base = {
        "id": 42,
        "title": "Song",
        "isrc": "FR1234567890",
        "explicit_lyrics": False,
        "artist": {"name": "Artist"},
        "contributors": [],
        "track_position": 1,
        "disk_number": 1,
    }
    base.update(overrides)
    return base


def _tidal_resp(**overrides):
    base = {
        "id": 7,
        "title": "Song",
        "isrc": "US1234567890",
        "trackNumber": 1,
        "volumeNumber": 1,
        "artists": [{"name": "Artist"}],
        "audioQuality": "LOSSLESS",
    }
    base.update(overrides)
    return base


def _soundcloud_resp(**overrides):
    base = {
        "id": "sc-1",
        "title": "  Song  ",
        "user": {"username": "DJ Artist"},
        "publisher_metadata": {"isrc": "US1234567890", "explicit": False},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# TrackInfo dataclass
# ---------------------------------------------------------------------------


def test_trackinfo_defaults():
    ti = TrackInfo(id="1", quality=2)
    assert ti.bit_depth is None
    assert ti.explicit is False
    assert ti.sampling_rate is None
    assert ti.work is None


def test_trackinfo_explicit_fields():
    ti = TrackInfo(id="x", quality=3, bit_depth=24, explicit=True, sampling_rate=96000, work="Suite")
    assert ti.bit_depth == 24
    assert ti.explicit is True
    assert ti.sampling_rate == 96000
    assert ti.work == "Suite"


# ---------------------------------------------------------------------------
# TrackMetadata.from_qobuz
# ---------------------------------------------------------------------------


def test_from_qobuz_returns_none_when_not_streamable():
    assert TrackMetadata.from_qobuz(_album(), _qobuz_resp(streamable=False)) is None


def test_from_qobuz_basic():
    meta = TrackMetadata.from_qobuz(_album(), _qobuz_resp())
    assert meta is not None
    assert meta.title == "Song"
    assert meta.isrc == "USRC12345678"
    assert meta.tracknumber == 1
    assert meta.discnumber == 1
    assert meta.artist == "Artist"


def test_from_qobuz_version_appended():
    resp = _qobuz_resp(version="Remastered")
    meta = TrackMetadata.from_qobuz(_album(), resp)
    assert meta.title == "Song (Remastered)"


def test_from_qobuz_version_not_duplicated():
    resp = _qobuz_resp(title="Song (Remastered)", version="Remastered")
    meta = TrackMetadata.from_qobuz(_album(), resp)
    assert meta.title == "Song (Remastered)"


def test_from_qobuz_work_prepended():
    resp = _qobuz_resp(work="Symphony No. 5")
    meta = TrackMetadata.from_qobuz(_album(), resp)
    assert meta.title == "Symphony No. 5: Song"


def test_from_qobuz_work_not_duplicated():
    resp = _qobuz_resp(title="Symphony No. 5: Song", work="Symphony No. 5")
    meta = TrackMetadata.from_qobuz(_album(), resp)
    assert meta.title == "Symphony No. 5: Song"


def test_from_qobuz_composer():
    resp = _qobuz_resp(composer={"name": "Bach"})
    meta = TrackMetadata.from_qobuz(_album(), resp)
    assert meta.composer == "Bach"


def test_from_qobuz_artist_fallback_to_album():
    resp = _qobuz_resp()
    del resp["performer"]
    resp["album"] = {"artist": {"name": "Album Artist"}}
    meta = TrackMetadata.from_qobuz(_album(), resp)
    assert meta.artist == "Album Artist"


def test_from_qobuz_bit_depth_and_sampling_rate():
    resp = _qobuz_resp(maximum_bit_depth=24, maximum_sampling_rate=96)
    meta = TrackMetadata.from_qobuz(_album(), resp)
    assert meta.info.bit_depth == 24
    assert meta.info.sampling_rate == 96


def test_from_qobuz_work_stored_in_info():
    resp = _qobuz_resp(work="Requiem")
    meta = TrackMetadata.from_qobuz(_album(), resp)
    assert meta.info.work == "Requiem"


# ---------------------------------------------------------------------------
# TrackMetadata.from_deezer
# ---------------------------------------------------------------------------


def test_from_deezer_basic():
    meta = TrackMetadata.from_deezer(_album(), _deezer_resp())
    assert meta.title == "Song"
    assert meta.isrc == "FR1234567890"
    assert meta.tracknumber == 1
    assert meta.discnumber == 1
    assert meta.info.bit_depth == 16
    assert meta.info.sampling_rate == 44.1


def test_from_deezer_contributors_joined():
    resp = _deezer_resp(contributors=[
        {"type": "artist", "name": "A"},
        {"type": "composer", "name": "B"},
        {"type": "artist", "name": "C"},
    ])
    meta = TrackMetadata.from_deezer(_album(), resp)
    assert meta.artist == "A, C"


def test_from_deezer_artist_fallback_when_no_contributors():
    meta = TrackMetadata.from_deezer(_album(), _deezer_resp(contributors=[]))
    assert meta.artist == "Artist"


def test_from_deezer_composer_as_list():
    resp = _deezer_resp(composer=["Bach", "Handel"])
    meta = TrackMetadata.from_deezer(_album(), resp)
    assert meta.composer == "Bach, Handel"


def test_from_deezer_composer_as_str():
    resp = _deezer_resp(composer="Mozart")
    meta = TrackMetadata.from_deezer(_album(), resp)
    assert meta.composer == "Mozart"


def test_from_deezer_composer_empty_list():
    resp = _deezer_resp(composer=[])
    meta = TrackMetadata.from_deezer(_album(), resp)
    assert meta.composer is None


def test_from_deezer_composer_absent():
    meta = TrackMetadata.from_deezer(_album(), _deezer_resp())
    assert meta.composer is None


def test_from_deezer_author_as_list():
    resp = _deezer_resp(author=["Voltaire", "Hugo"])
    meta = TrackMetadata.from_deezer(_album(), resp)
    assert meta.author == "Voltaire, Hugo"


def test_from_deezer_author_as_str():
    resp = _deezer_resp(author="Rimbaud")
    meta = TrackMetadata.from_deezer(_album(), resp)
    assert meta.author == "Rimbaud"


def test_from_deezer_author_absent():
    meta = TrackMetadata.from_deezer(_album(), _deezer_resp())
    assert meta.author is None


def test_from_deezer_bpm():
    meta = TrackMetadata.from_deezer(_album(), _deezer_resp(bpm=128))
    assert meta.bpm == 128


def test_from_deezer_bpm_absent():
    meta = TrackMetadata.from_deezer(_album(), _deezer_resp())
    assert meta.bpm is None


def test_from_deezer_gain_valid():
    meta = TrackMetadata.from_deezer(_album(), _deezer_resp(gain="-9.5"))
    assert meta.replaygain_track_gain == "-9.50 dB"


def test_from_deezer_gain_invalid():
    meta = TrackMetadata.from_deezer(_album(), _deezer_resp(gain="bad"))
    assert meta.replaygain_track_gain is None


def test_from_deezer_gain_absent():
    meta = TrackMetadata.from_deezer(_album(), _deezer_resp())
    assert meta.replaygain_track_gain is None


def test_from_deezer_explicit():
    meta = TrackMetadata.from_deezer(_album(), _deezer_resp(explicit_lyrics=True))
    assert meta.info.explicit is True


# ---------------------------------------------------------------------------
# TrackMetadata.from_soundcloud
# ---------------------------------------------------------------------------


def test_from_soundcloud_basic():
    meta = TrackMetadata.from_soundcloud(_album(), _soundcloud_resp())
    assert meta.title == "Song"
    assert meta.artist == "DJ Artist"
    assert meta.tracknumber == 1
    assert meta.discnumber == 0
    assert meta.isrc == "US1234567890"


def test_from_soundcloud_explicit():
    resp = _soundcloud_resp(publisher_metadata={"isrc": "X", "explicit": True})
    meta = TrackMetadata.from_soundcloud(_album(), resp)
    assert meta.info.explicit is True


def test_from_soundcloud_strips_title_whitespace():
    resp = _soundcloud_resp(title="  Spaces  ")
    meta = TrackMetadata.from_soundcloud(_album(), resp)
    assert meta.title == "Spaces"


def test_from_soundcloud_no_publisher_metadata():
    resp = _soundcloud_resp()
    del resp["publisher_metadata"]
    meta = TrackMetadata.from_soundcloud(_album(), resp)
    assert meta.isrc is None
    assert meta.info.explicit is False


# ---------------------------------------------------------------------------
# TrackMetadata.from_tidal
# ---------------------------------------------------------------------------


def test_from_tidal_basic():
    meta = TrackMetadata.from_tidal(_album(), _tidal_resp())
    assert meta.title == "Song"
    assert meta.isrc == "US1234567890"
    assert meta.tracknumber == 1
    assert meta.discnumber == 1
    assert meta.artist == "Artist"


def test_from_tidal_version_appended():
    meta = TrackMetadata.from_tidal(_album(), _tidal_resp(version="Live"))
    assert meta.title == "Song (Live)"


def test_from_tidal_no_version():
    meta = TrackMetadata.from_tidal(_album(), _tidal_resp())
    assert meta.title == "Song"


def test_from_tidal_multiple_artists():
    resp = _tidal_resp(artists=[{"name": "A"}, {"name": "B"}])
    meta = TrackMetadata.from_tidal(_album(), resp)
    assert meta.artist == "A, B"


def test_from_tidal_artist_fallback_when_no_artists():
    resp = _tidal_resp(artists=None)
    resp["artist"] = {"name": "Solo"}
    meta = TrackMetadata.from_tidal(_album(), resp)
    assert meta.artist == "Solo"


def test_from_tidal_quality_low():
    meta = TrackMetadata.from_tidal(_album(), _tidal_resp(audioQuality="LOW"))
    assert meta.info.quality == 0
    assert meta.info.bit_depth is None
    assert meta.info.sampling_rate is None


def test_from_tidal_quality_high():
    meta = TrackMetadata.from_tidal(_album(), _tidal_resp(audioQuality="HIGH"))
    assert meta.info.quality == 1
    assert meta.info.bit_depth is None


def test_from_tidal_quality_lossless():
    meta = TrackMetadata.from_tidal(_album(), _tidal_resp(audioQuality="LOSSLESS"))
    assert meta.info.quality == 2
    assert meta.info.bit_depth == 16
    assert meta.info.sampling_rate == 44100


def test_from_tidal_quality_hi_res():
    meta = TrackMetadata.from_tidal(_album(), _tidal_resp(audioQuality="HI_RES"))
    assert meta.info.quality == 3
    assert meta.info.bit_depth == 24
    assert meta.info.sampling_rate == 44100


def test_from_tidal_quality_hi_res_lossless():
    meta = TrackMetadata.from_tidal(_album(), _tidal_resp(audioQuality="HI_RES_LOSSLESS"))
    assert meta.info.quality == 3
    assert meta.info.bit_depth == 24


def test_from_tidal_quality_unknown_defaults_to_zero():
    meta = TrackMetadata.from_tidal(_album(), _tidal_resp(audioQuality="FUTURE_FORMAT"))
    assert meta.info.quality == 0


def test_from_tidal_quality_none_defaults_to_zero():
    resp = _tidal_resp()
    del resp["audioQuality"]
    meta = TrackMetadata.from_tidal(_album(), resp)
    assert meta.info.quality == 0


def test_from_tidal_explicit():
    meta = TrackMetadata.from_tidal(_album(), _tidal_resp(explicit=True))
    assert meta.info.explicit is True


def test_from_tidal_composers_from_contributors():
    resp = _tidal_resp(contributors=[
        {"name": "Bach", "role": "Composer"},
        {"name": "Smith", "role": "Producer"},
        {"name": "Handel", "role": "Composer"},
    ])
    meta = TrackMetadata.from_tidal(_album(), resp)
    assert meta.composer == "Bach, Handel"


def test_from_tidal_no_composers():
    resp = _tidal_resp(contributors=[{"name": "Smith", "role": "Producer"}])
    meta = TrackMetadata.from_tidal(_album(), resp)
    assert meta.composer is None


def test_from_tidal_lyrics():
    meta = TrackMetadata.from_tidal(_album(), _tidal_resp(lyrics="La la la"))
    assert meta.lyrics == "La la la"


def test_from_tidal_strips_title():
    meta = TrackMetadata.from_tidal(_album(), _tidal_resp(title="  Trim  "))
    assert meta.title == "Trim"


# ---------------------------------------------------------------------------
# TrackMetadata.from_resp dispatch
# ---------------------------------------------------------------------------


def test_from_resp_dispatches_qobuz():
    meta = TrackMetadata.from_resp(_album(), "qobuz", _qobuz_resp())
    assert meta.title == "Song"


def test_from_resp_dispatches_tidal():
    meta = TrackMetadata.from_resp(_album(), "tidal", _tidal_resp())
    assert meta.title == "Song"


def test_from_resp_dispatches_soundcloud():
    meta = TrackMetadata.from_resp(_album(), "soundcloud", _soundcloud_resp())
    assert meta.title == "Song"


def test_from_resp_dispatches_deezer():
    meta = TrackMetadata.from_resp(_album(), "deezer", _deezer_resp())
    assert meta.title == "Song"


def test_from_resp_unknown_source_raises():
    with pytest.raises(Exception):
        TrackMetadata.from_resp(_album(), "napster", {})


# ---------------------------------------------------------------------------
# TrackMetadata.format_track_path
# ---------------------------------------------------------------------------


def _track_meta(**overrides):
    album = _album(albumartist="Album Artist", albumcomposer="Composer")
    defaults = dict(
        info=TrackInfo(id="1", quality=2),
        title="Song",
        album=album,
        artist="Track Artist",
        tracknumber=3,
        discnumber=1,
        composer="Bach",
    )
    defaults.update(overrides)
    return TrackMetadata(**defaults)


def test_format_track_path_basic():
    meta = _track_meta()
    result = meta.format_track_path("{tracknumber} - {title}")
    assert result == "3 - Song"


def test_format_track_path_all_keys():
    meta = _track_meta()
    result = meta.format_track_path("{artist} - {title} ({albumartist})")
    assert result == "Track Artist - Song (Album Artist)"


def test_format_track_path_composer_fallback():
    meta = _track_meta(composer=None)
    result = meta.format_track_path("{composer}")
    assert result == "Unknown"


def test_format_track_path_albumcomposer_fallback():
    album = _album(albumcomposer=None)
    meta = _track_meta(album=album)
    result = meta.format_track_path("{albumcomposer}")
    assert result == "Unknown"


def test_format_track_path_explicit_tag():
    meta = _track_meta(info=TrackInfo(id="1", quality=2, explicit=True))
    result = meta.format_track_path("{explicit}{title}")
    assert "(Explicit)" in result


def test_format_track_path_not_explicit():
    meta = _track_meta()
    result = meta.format_track_path("{explicit}{title}")
    assert result == "Song"
