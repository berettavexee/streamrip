"""Tests for streamrip/metadata/album.py."""

from unittest.mock import MagicMock, patch

import pytest

from streamrip.metadata.album import AlbumInfo, AlbumMetadata
from streamrip.metadata.covers import Covers

# ---------------------------------------------------------------------------
# Helpers — minimal valid response dicts
# ---------------------------------------------------------------------------

_COVERS = Covers()


def _qobuz(**overrides):
    base = {
        "qobuz_id": "123",
        "title": "Album",
        "tracks_count": 10,
        "genre": {"name": "Rock"},
        "release_date_original": "2020-06-15",
        "artist": {"name": "Artist"},
        "tracks": {"items": [{"media_number": 1}]},
        "maximum_bit_depth": 24,
        "maximum_sampling_rate": 96,
    }
    base.update(overrides)
    return base


def _deezer(**overrides):
    base = {
        "id": "42",
        "title": "Album",
        "track_total": 10,
        "tracks": [{"disk_number": 1}],
        "genres": {"data": [{"name": "Rock"}]},
        "release_date": "2020-06-15",
        "artist": {"name": "Artist"},
        "contributors": [],
    }
    base.update(overrides)
    return base


def _soundcloud(**overrides):
    base = {
        "id": "sc-1",
        "user": {"username": "User"},
        "created_at": "2020-06-15T00:00:00Z",
        "publisher_metadata": {
            "artist": "Publisher Artist",
            "album_title": "SC Album",
            "explicit": False,
            "p_line": "(P) 2020 Label",
        },
    }
    base.update(overrides)
    return base


def _tidal(**overrides):
    base = {
        "id": 1,
        "title": "Album",
        "numberOfTracks": 10,
        "releaseDate": "2020-06-15",
        "copyright": "(P) 2020 Label",
        "artists": [{"name": "Artist"}],
        "numberOfVolumes": 1,
        "explicit": False,
        "audioQuality": "LOSSLESS",
        "allowStreaming": True,
    }
    base.update(overrides)
    return base


def _tidal_track(**overrides):
    base = {
        "id": "7",
        "album": {"id": "a1", "title": "Album"},
        "allowStreaming": True,
        "streamStartDate": "2020-06-15T00:00:00Z",
        "copyright": "(P) 2020 Label",
        "artists": [{"name": "Artist"}],
        "volumeNumber": 1,
        "explicit": False,
        "audioQuality": "LOSSLESS",
    }
    base.update(overrides)
    return base


def _deezer_track(**overrides):
    base = {
        "album": {
            "id": "a1",
            "title": "Album",
            "release_date": "2020-06-15",
        },
        "contributors": [{"name": "Artist"}],
        "explicit_lyrics": False,
    }
    base.update(overrides)
    return base


# Patch all Covers.from_* so tests don't depend on Covers internals
_PATCH_COVERS = {
    "qobuz": patch("streamrip.metadata.album.Covers.from_qobuz", return_value=_COVERS),
    "deezer": patch("streamrip.metadata.album.Covers.from_deezer", return_value=_COVERS),
    "soundcloud": patch("streamrip.metadata.album.Covers.from_soundcloud", return_value=_COVERS),
    "tidal": patch("streamrip.metadata.album.Covers.from_tidal", return_value=_COVERS),
}


# ---------------------------------------------------------------------------
# AlbumInfo dataclass
# ---------------------------------------------------------------------------


def test_albuminfo_defaults():
    info = AlbumInfo(id="1", quality=2, container="FLAC")
    assert info.label is None
    assert info.explicit is False
    assert info.sampling_rate is None
    assert info.bit_depth is None
    assert info.booklets is None


def test_albuminfo_explicit_fields():
    info = AlbumInfo(
        id="x", quality=3, container="FLAC",
        label="Warp", explicit=True, sampling_rate=96000,
        bit_depth=24, booklets=[{"url": "http://booklet.pdf"}],
    )
    assert info.label == "Warp"
    assert info.explicit is True
    assert info.sampling_rate == 96000
    assert info.bit_depth == 24
    assert info.booklets is not None


# ---------------------------------------------------------------------------
# AlbumMetadata.get_genres
# ---------------------------------------------------------------------------


def test_get_genres_multiple():
    meta = MagicMock(spec=AlbumMetadata)
    meta.genre = ["Rock", "Pop", "Indie"]
    assert AlbumMetadata.get_genres(meta) == "Rock, Pop, Indie"


def test_get_genres_single():
    meta = MagicMock(spec=AlbumMetadata)
    meta.genre = ["Jazz"]
    assert AlbumMetadata.get_genres(meta) == "Jazz"


def test_get_genres_empty():
    meta = MagicMock(spec=AlbumMetadata)
    meta.genre = []
    assert AlbumMetadata.get_genres(meta) == ""


# ---------------------------------------------------------------------------
# AlbumMetadata.get_copyright
# ---------------------------------------------------------------------------


def test_get_copyright_none():
    meta = MagicMock(spec=AlbumMetadata)
    meta.copyright = None
    assert AlbumMetadata.get_copyright(meta) is None


def test_get_copyright_phonogram():
    meta = MagicMock(spec=AlbumMetadata)
    meta.copyright = "(P) 2020 Label"
    result = AlbumMetadata.get_copyright(meta)
    assert "℗" in result
    assert "(P)" not in result


def test_get_copyright_symbol():
    meta = MagicMock(spec=AlbumMetadata)
    meta.copyright = "(C) 2020 Artist"
    result = AlbumMetadata.get_copyright(meta)
    assert "©" in result
    assert "(C)" not in result


def test_get_copyright_both():
    meta = MagicMock(spec=AlbumMetadata)
    meta.copyright = "(P) 2020 Label (C) Artist"
    result = AlbumMetadata.get_copyright(meta)
    assert "℗" in result
    assert "©" in result


def test_get_copyright_case_insensitive():
    meta = MagicMock(spec=AlbumMetadata)
    meta.copyright = "(p) 2020 (c) Artist"
    result = AlbumMetadata.get_copyright(meta)
    assert "℗" in result
    assert "©" in result


# ---------------------------------------------------------------------------
# AlbumMetadata.format_folder_path
# ---------------------------------------------------------------------------


def _album_meta(quality=3, bit_depth=24, sampling_rate=96, container="FLAC",
                albumcomposer=None):
    info = AlbumInfo(id="1", quality=quality, container=container,
                     bit_depth=bit_depth, sampling_rate=sampling_rate)
    return AlbumMetadata(
        info=info, album="Discovery", albumartist="Daft Punk",
        year="2001", genre=["Electronic"], covers=_COVERS,
        tracktotal=14, albumcomposer=albumcomposer,
    )


def test_format_folder_path_basic():
    meta = _album_meta()
    result = meta.format_folder_path("{albumartist} - {title} ({year})")
    assert "Daft Punk" in result
    assert "Discovery" in result
    assert "2001" in result


def test_format_folder_path_no_effective_quality():
    meta = _album_meta(quality=3, bit_depth=24, sampling_rate=96, container="FLAC")
    result = meta.format_folder_path("{container} {bit_depth}bit {sampling_rate}kHz")
    assert "FLAC" in result
    assert "24" in result
    assert "96" in result


def test_format_folder_path_effective_quality_lower_uses_table():
    meta = _album_meta(quality=3)
    result = meta.format_folder_path("{container} {bit_depth}bit", effective_quality=2)
    assert "FLAC" in result
    assert "16" in result


def test_format_folder_path_effective_quality_equal_uses_album():
    meta = _album_meta(quality=2, bit_depth=16, sampling_rate=44100, container="FLAC")
    result = meta.format_folder_path("{bit_depth}bit", effective_quality=2)
    assert "16" in result


def test_format_folder_path_effective_quality_not_in_table_uses_album():
    meta = _album_meta(quality=3, bit_depth=24, sampling_rate=96)
    result = meta.format_folder_path("{bit_depth}bit", effective_quality=99)
    assert "24" in result


def test_format_folder_path_albumcomposer_fallback():
    meta = _album_meta(albumcomposer=None)
    result = meta.format_folder_path("{albumcomposer}")
    assert result == "Unknown"


def test_format_folder_path_albumcomposer_present():
    meta = _album_meta(albumcomposer="Bach")
    result = meta.format_folder_path("{albumcomposer}")
    assert "Bach" in result


def test_format_folder_path_bit_depth_none_fallback():
    meta = _album_meta(bit_depth=None, sampling_rate=None, quality=1, container="MP3")
    result = meta.format_folder_path("{bit_depth} {sampling_rate}")
    assert "Unknown" in result


# ---------------------------------------------------------------------------
# AlbumMetadata.from_qobuz
# ---------------------------------------------------------------------------


def test_from_qobuz_basic():
    with _PATCH_COVERS["qobuz"]:
        meta = AlbumMetadata.from_qobuz(_qobuz())
    assert meta.album == "Album"
    assert meta.year == "2020"
    assert meta.tracktotal == 10
    assert meta.info.quality == 3  # 24bit/96kHz


def test_from_qobuz_artists_list():
    resp = _qobuz(artists=[{"name": "A"}, {"name": "B"}])
    with _PATCH_COVERS["qobuz"]:
        meta = AlbumMetadata.from_qobuz(resp)
    assert meta.albumartist == "A, B"


def test_from_qobuz_artist_fallback():
    resp = _qobuz()  # no "artists" key, has "artist"
    with _PATCH_COVERS["qobuz"]:
        meta = AlbumMetadata.from_qobuz(resp)
    assert meta.albumartist == "Artist"


def test_from_qobuz_label_as_dict():
    resp = _qobuz(label={"name": "Warp Records"})
    with _PATCH_COVERS["qobuz"]:
        meta = AlbumMetadata.from_qobuz(resp)
    assert meta.info.label == "Warp Records"


def test_from_qobuz_label_as_string():
    resp = _qobuz(label="Warp Records")
    with _PATCH_COVERS["qobuz"]:
        meta = AlbumMetadata.from_qobuz(resp)
    assert meta.info.label == "Warp Records"


def test_from_qobuz_flac_container():
    with _PATCH_COVERS["qobuz"]:
        meta = AlbumMetadata.from_qobuz(_qobuz())
    assert meta.info.container == "FLAC"


def test_from_qobuz_date_fallback():
    resp = _qobuz()
    del resp["release_date_original"]
    resp["release_date"] = "2019-03-01"
    with _PATCH_COVERS["qobuz"]:
        meta = AlbumMetadata.from_qobuz(resp)
    assert meta.year == "2019"


def test_from_qobuz_no_date():
    resp = _qobuz()
    del resp["release_date_original"]
    with _PATCH_COVERS["qobuz"]:
        meta = AlbumMetadata.from_qobuz(resp)
    assert meta.year == "Unknown"


def test_from_qobuz_explicit():
    resp = _qobuz(parental_warning=True)
    with _PATCH_COVERS["qobuz"]:
        meta = AlbumMetadata.from_qobuz(resp)
    assert meta.info.explicit is True


def test_from_qobuz_disctotal_from_tracks():
    resp = _qobuz(tracks={"items": [{"media_number": 1}, {"media_number": 2}]})
    with _PATCH_COVERS["qobuz"]:
        meta = AlbumMetadata.from_qobuz(resp)
    assert meta.disctotal == 2


def test_from_qobuz_booklets():
    resp = _qobuz(goodies=[{"url": "http://booklet.pdf"}])
    with _PATCH_COVERS["qobuz"]:
        meta = AlbumMetadata.from_qobuz(resp)
    assert meta.info.booklets is not None


def test_from_qobuz_albumcomposer():
    resp = _qobuz(composer={"name": "Bach"})
    with _PATCH_COVERS["qobuz"]:
        meta = AlbumMetadata.from_qobuz(resp)
    assert meta.albumcomposer == "Bach"


# ---------------------------------------------------------------------------
# AlbumMetadata.from_deezer
# ---------------------------------------------------------------------------


def test_from_deezer_basic():
    with _PATCH_COVERS["deezer"]:
        meta = AlbumMetadata.from_deezer(_deezer())
    assert meta.album == "Album"
    assert meta.year == "2020"
    assert meta.info.quality == 2
    assert meta.info.container == "FLAC"


def test_from_deezer_contributors_joined():
    resp = _deezer(contributors=[
        {"type": "artist", "name": "A"},
        {"type": "composer", "name": "B"},
        {"type": "artist", "name": "C"},
    ])
    with _PATCH_COVERS["deezer"]:
        meta = AlbumMetadata.from_deezer(resp)
    assert meta.albumartist == "A, C"


def test_from_deezer_artist_fallback():
    resp = _deezer(contributors=[])
    with _PATCH_COVERS["deezer"]:
        meta = AlbumMetadata.from_deezer(resp)
    assert meta.albumartist == "Artist"


def test_from_deezer_tracktotal_from_track_total():
    resp = _deezer(track_total=12)
    with _PATCH_COVERS["deezer"]:
        meta = AlbumMetadata.from_deezer(resp)
    assert meta.tracktotal == 12


def test_from_deezer_tracktotal_from_nb_tracks():
    resp = _deezer()
    del resp["track_total"]
    resp["nb_tracks"] = 8
    with _PATCH_COVERS["deezer"]:
        meta = AlbumMetadata.from_deezer(resp)
    assert meta.tracktotal == 8


def test_from_deezer_disctotal_from_last_track():
    resp = _deezer(tracks=[{"disk_number": 1}, {"disk_number": 2}])
    with _PATCH_COVERS["deezer"]:
        meta = AlbumMetadata.from_deezer(resp)
    assert meta.disctotal == 2


def test_from_deezer_empty_tracks_disctotal_one():
    resp = _deezer(tracks=[])
    with _PATCH_COVERS["deezer"]:
        meta = AlbumMetadata.from_deezer(resp)
    assert meta.disctotal == 1


def test_from_deezer_explicit_from_parental_warning():
    resp = _deezer(parental_warning=True)
    with _PATCH_COVERS["deezer"]:
        meta = AlbumMetadata.from_deezer(resp)
    assert meta.info.explicit is True


def test_from_deezer_explicit_from_explicit_lyrics():
    resp = _deezer(explicit_lyrics=True)
    with _PATCH_COVERS["deezer"]:
        meta = AlbumMetadata.from_deezer(resp)
    assert meta.info.explicit is True


def test_from_deezer_genres():
    resp = _deezer(genres={"data": [{"name": "Rock"}, {"name": "Indie"}]})
    with _PATCH_COVERS["deezer"]:
        meta = AlbumMetadata.from_deezer(resp)
    assert "Rock" in meta.genre
    assert "Indie" in meta.genre


# ---------------------------------------------------------------------------
# AlbumMetadata.from_soundcloud
# ---------------------------------------------------------------------------


def test_from_soundcloud_basic():
    with _PATCH_COVERS["soundcloud"]:
        meta = AlbumMetadata.from_soundcloud(_soundcloud())
    assert meta.album == "SC Album"
    assert meta.albumartist == "Publisher Artist"
    assert meta.year == "2020"
    assert meta.info.quality == 0
    assert meta.info.container == "MP3"


def test_from_soundcloud_artist_fallback_to_username():
    resp = _soundcloud(publisher_metadata={"album_title": "Album"})
    with _PATCH_COVERS["soundcloud"]:
        meta = AlbumMetadata.from_soundcloud(resp)
    assert meta.albumartist == "User"


def test_from_soundcloud_no_publisher_metadata():
    resp = _soundcloud()
    del resp["publisher_metadata"]
    with _PATCH_COVERS["soundcloud"]:
        meta = AlbumMetadata.from_soundcloud(resp)
    assert meta.albumartist == "User"
    assert meta.album == "Unknown album"


def test_from_soundcloud_genre():
    resp = _soundcloud(genre="Electronic")
    with _PATCH_COVERS["soundcloud"]:
        meta = AlbumMetadata.from_soundcloud(resp)
    assert meta.genre == ["Electronic"]


def test_from_soundcloud_no_genre():
    with _PATCH_COVERS["soundcloud"]:
        meta = AlbumMetadata.from_soundcloud(_soundcloud())
    assert meta.genre == []


def test_from_soundcloud_explicit():
    resp = _soundcloud(publisher_metadata={
        "artist": "A", "album_title": "B", "explicit": True,
    })
    with _PATCH_COVERS["soundcloud"]:
        meta = AlbumMetadata.from_soundcloud(resp)
    assert meta.info.explicit is True


# ---------------------------------------------------------------------------
# AlbumMetadata.from_tidal
# ---------------------------------------------------------------------------


def test_from_tidal_not_streamable_returns_none():
    with _PATCH_COVERS["tidal"]:
        assert AlbumMetadata.from_tidal(_tidal(allowStreaming=False)) is None


def test_from_tidal_basic():
    with _PATCH_COVERS["tidal"]:
        meta = AlbumMetadata.from_tidal(_tidal())
    assert meta.album == "Album"
    assert meta.year == "2020"
    assert meta.info.container == "MP4"


def test_from_tidal_artists_joined():
    resp = _tidal(artists=[{"name": "A"}, {"name": "B"}])
    with _PATCH_COVERS["tidal"]:
        meta = AlbumMetadata.from_tidal(resp)
    assert meta.albumartist == "A, B"


def test_from_tidal_artist_fallback():
    resp = _tidal(artists=[])
    resp["artist"] = {"name": "Solo"}
    with _PATCH_COVERS["tidal"]:
        meta = AlbumMetadata.from_tidal(resp)
    assert meta.albumartist == "Solo"


def test_from_tidal_covers_none_fallback():
    with patch("streamrip.metadata.album.Covers.from_tidal", return_value=None):
        meta = AlbumMetadata.from_tidal(_tidal())
    assert isinstance(meta.covers, Covers)


def test_from_tidal_quality_low():
    with _PATCH_COVERS["tidal"]:
        meta = AlbumMetadata.from_tidal(_tidal(audioQuality="LOW"))
    assert meta.info.quality == 0
    assert meta.info.bit_depth is None


def test_from_tidal_quality_high():
    with _PATCH_COVERS["tidal"]:
        meta = AlbumMetadata.from_tidal(_tidal(audioQuality="HIGH"))
    assert meta.info.quality == 1
    assert meta.info.bit_depth is None


def test_from_tidal_quality_lossless():
    with _PATCH_COVERS["tidal"]:
        meta = AlbumMetadata.from_tidal(_tidal(audioQuality="LOSSLESS"))
    assert meta.info.quality == 2
    assert meta.info.bit_depth == 16
    assert meta.info.sampling_rate == 44100


def test_from_tidal_quality_hi_res():
    with _PATCH_COVERS["tidal"]:
        meta = AlbumMetadata.from_tidal(_tidal(audioQuality="HI_RES"))
    assert meta.info.quality == 3
    assert meta.info.bit_depth == 24


def test_from_tidal_quality_hi_res_lossless():
    with _PATCH_COVERS["tidal"]:
        meta = AlbumMetadata.from_tidal(_tidal(audioQuality="HI_RES_LOSSLESS"))
    assert meta.info.quality == 3
    assert meta.info.bit_depth == 24


def test_from_tidal_quality_unknown_defaults_zero():
    with _PATCH_COVERS["tidal"]:
        meta = AlbumMetadata.from_tidal(_tidal(audioQuality="FUTURE"))
    assert meta.info.quality == 0


def test_from_tidal_explicit():
    with _PATCH_COVERS["tidal"]:
        meta = AlbumMetadata.from_tidal(_tidal(explicit=True))
    assert meta.info.explicit is True


def test_from_tidal_disctotal():
    with _PATCH_COVERS["tidal"]:
        meta = AlbumMetadata.from_tidal(_tidal(numberOfVolumes=3))
    assert meta.disctotal == 3


# ---------------------------------------------------------------------------
# AlbumMetadata.from_tidal_playlist_track_resp
# ---------------------------------------------------------------------------


def test_from_tidal_playlist_track_not_streamable():
    with _PATCH_COVERS["tidal"]:
        assert AlbumMetadata.from_tidal_playlist_track_resp(
            _tidal_track(allowStreaming=False)
        ) is None


def test_from_tidal_playlist_track_basic():
    with _PATCH_COVERS["tidal"]:
        meta = AlbumMetadata.from_tidal_playlist_track_resp(_tidal_track())
    assert meta.album == "Album"
    assert meta.year == "2020"
    assert meta.tracktotal == 1


def test_from_tidal_playlist_track_no_date():
    resp = _tidal_track()
    del resp["streamStartDate"]
    with _PATCH_COVERS["tidal"]:
        meta = AlbumMetadata.from_tidal_playlist_track_resp(resp)
    assert meta.year == "Unknown Year"


def test_from_tidal_playlist_track_artist_fallback():
    resp = _tidal_track(artists=[])
    resp["artist"] = {"name": "Solo"}
    with _PATCH_COVERS["tidal"]:
        meta = AlbumMetadata.from_tidal_playlist_track_resp(resp)
    assert meta.albumartist == "Solo"


def test_from_tidal_playlist_track_covers_none_fallback():
    with patch("streamrip.metadata.album.Covers.from_tidal", return_value=None):
        meta = AlbumMetadata.from_tidal_playlist_track_resp(_tidal_track())
    assert isinstance(meta.covers, Covers)


def test_from_tidal_playlist_track_quality_hi_res():
    with _PATCH_COVERS["tidal"]:
        meta = AlbumMetadata.from_tidal_playlist_track_resp(
            _tidal_track(audioQuality="HI_RES")
        )
    assert meta.info.quality == 3
    assert meta.info.bit_depth == 24


def test_from_tidal_playlist_track_quality_low():
    with _PATCH_COVERS["tidal"]:
        meta = AlbumMetadata.from_tidal_playlist_track_resp(
            _tidal_track(audioQuality="LOW")
        )
    assert meta.info.bit_depth is None


# ---------------------------------------------------------------------------
# AlbumMetadata.from_incomplete_deezer_track_resp
# ---------------------------------------------------------------------------


def test_from_incomplete_deezer_track_resp_basic():
    with _PATCH_COVERS["deezer"]:
        meta = AlbumMetadata.from_incomplete_deezer_track_resp(_deezer_track())
    assert meta.album == "Album"
    assert meta.year == "2020"
    assert meta.albumartist == "Artist"
    assert meta.tracktotal == 1
    assert meta.disctotal == 1


def test_from_incomplete_deezer_track_resp_multiple_contributors():
    resp = _deezer_track(contributors=[{"name": "A"}, {"name": "B"}])
    with _PATCH_COVERS["deezer"]:
        meta = AlbumMetadata.from_incomplete_deezer_track_resp(resp)
    assert meta.albumartist == "A, B"


def test_from_incomplete_deezer_track_resp_explicit():
    resp = _deezer_track(explicit_lyrics=True)
    with _PATCH_COVERS["deezer"]:
        meta = AlbumMetadata.from_incomplete_deezer_track_resp(resp)
    assert meta.info.explicit is True


# ---------------------------------------------------------------------------
# AlbumMetadata.from_track_resp dispatch
# ---------------------------------------------------------------------------


def test_from_track_resp_qobuz():
    resp = {"album": _qobuz()}
    with _PATCH_COVERS["qobuz"]:
        meta = AlbumMetadata.from_track_resp(resp, "qobuz")
    assert meta.album == "Album"


def test_from_track_resp_tidal():
    with _PATCH_COVERS["tidal"]:
        meta = AlbumMetadata.from_track_resp(_tidal_track(), "tidal")
    assert meta.album == "Album"


def test_from_track_resp_soundcloud():
    with _PATCH_COVERS["soundcloud"]:
        meta = AlbumMetadata.from_track_resp(_soundcloud(), "soundcloud")
    assert meta.album == "SC Album"


def test_from_track_resp_deezer_full_album():
    resp = {"album": _deezer()}
    with _PATCH_COVERS["deezer"]:
        meta = AlbumMetadata.from_track_resp(resp, "deezer")
    assert meta.album == "Album"


def test_from_track_resp_deezer_incomplete():
    resp = _deezer_track()
    with _PATCH_COVERS["deezer"]:
        meta = AlbumMetadata.from_track_resp(resp, "deezer")
    assert meta.album == "Album"


def test_from_track_resp_invalid_source():
    with pytest.raises(Exception, match="Invalid source"):
        AlbumMetadata.from_track_resp({}, "napster")


# ---------------------------------------------------------------------------
# AlbumMetadata.from_album_resp dispatch
# ---------------------------------------------------------------------------


def test_from_album_resp_qobuz():
    with _PATCH_COVERS["qobuz"]:
        meta = AlbumMetadata.from_album_resp(_qobuz(), "qobuz")
    assert meta.album == "Album"


def test_from_album_resp_tidal():
    with _PATCH_COVERS["tidal"]:
        meta = AlbumMetadata.from_album_resp(_tidal(), "tidal")
    assert meta.album == "Album"


def test_from_album_resp_soundcloud():
    with _PATCH_COVERS["soundcloud"]:
        meta = AlbumMetadata.from_album_resp(_soundcloud(), "soundcloud")
    assert meta.album == "SC Album"


def test_from_album_resp_deezer():
    with _PATCH_COVERS["deezer"]:
        meta = AlbumMetadata.from_album_resp(_deezer(), "deezer")
    assert meta.album == "Album"


def test_from_album_resp_invalid_source():
    with pytest.raises(Exception, match="Invalid source"):
        AlbumMetadata.from_album_resp({}, "napster")
