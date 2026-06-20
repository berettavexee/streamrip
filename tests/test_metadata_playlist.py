"""Tests for streamrip/metadata/playlist.py."""

from unittest.mock import MagicMock, patch

import pytest

from streamrip.metadata.playlist import (
    NON_STREAMABLE,
    NOT_RESOLVED,
    ORIGINAL_DOWNLOAD,
    PlaylistMetadata,
    get_soundcloud_id,
    parse_soundcloud_id,
)

# ---------------------------------------------------------------------------
# get_soundcloud_id
# ---------------------------------------------------------------------------

_BASE = {
    "id": 42,
    "streamable": True,
    "policy": "ALLOW",
    "downloadable": False,
    "has_downloads_left": False,
    "media": {
        "transcodings": [
            {"format": {"protocol": "hls", "mime_type": "audio/mpeg"}, "url": "https://hls.example.com/stream"},
        ]
    },
}


def _sc(overrides=None):
    resp = dict(_BASE)
    if overrides:
        resp.update(overrides)
    return resp


def test_get_soundcloud_id_no_media():
    resp = {"id": 1}
    assert get_soundcloud_id(resp) == f"1|{NOT_RESOLVED}"


def test_get_soundcloud_id_not_streamable():
    assert get_soundcloud_id(_sc({"streamable": False})) == f"42|{NON_STREAMABLE}"


def test_get_soundcloud_id_blocked_policy():
    assert get_soundcloud_id(_sc({"policy": "BLOCK"})) == f"42|{NON_STREAMABLE}"


def test_get_soundcloud_id_original_download():
    resp = _sc({"downloadable": True, "has_downloads_left": True})
    assert get_soundcloud_id(resp) == f"42|{ORIGINAL_DOWNLOAD}"


def test_get_soundcloud_id_hls_url():
    result = get_soundcloud_id(_sc())
    assert result == "42|https://hls.example.com/stream"


def test_get_soundcloud_id_no_hls_match():
    resp = _sc({
        "media": {
            "transcodings": [
                {"format": {"protocol": "progressive", "mime_type": "audio/mpeg"}, "url": "https://x.com"},
            ]
        }
    })
    assert get_soundcloud_id(resp) == f"42|{NON_STREAMABLE}"


def test_get_soundcloud_id_picks_first_hls_match():
    resp = _sc({
        "media": {
            "transcodings": [
                {"format": {"protocol": "progressive", "mime_type": "audio/mpeg"}, "url": "bad"},
                {"format": {"protocol": "hls", "mime_type": "audio/mpeg"}, "url": "https://first.com"},
                {"format": {"protocol": "hls", "mime_type": "audio/mpeg"}, "url": "https://second.com"},
            ]
        }
    })
    assert get_soundcloud_id(resp) == "42|https://first.com"


# ---------------------------------------------------------------------------
# parse_soundcloud_id
# ---------------------------------------------------------------------------


def test_parse_soundcloud_id_splits_correctly():
    assert parse_soundcloud_id("42|https://hls.example.com") == ("42", "https://hls.example.com")


def test_parse_soundcloud_id_non_streamable():
    assert parse_soundcloud_id(f"99|{NON_STREAMABLE}") == ("99", NON_STREAMABLE)


def test_parse_soundcloud_id_original_download():
    assert parse_soundcloud_id(f"7|{ORIGINAL_DOWNLOAD}") == ("7", ORIGINAL_DOWNLOAD)


def test_parse_soundcloud_id_invalid_raises():
    with pytest.raises(AssertionError):
        parse_soundcloud_id("no_pipe_here")


# ---------------------------------------------------------------------------
# PlaylistMetadata.from_deezer
# ---------------------------------------------------------------------------


def test_from_deezer_name_and_ids():
    resp = {
        "title": "Deezer Hits",
        "tracks": [{"id": 111}, {"id": 222}, {"id": 333}],
    }
    meta = PlaylistMetadata.from_deezer(resp)
    assert meta.name == "Deezer Hits"
    assert meta.tracks == ["111", "222", "333"]


def test_from_deezer_empty_tracks():
    resp = {"title": "Empty", "tracks": []}
    meta = PlaylistMetadata.from_deezer(resp)
    assert meta.name == "Empty"
    assert meta.tracks == []


# ---------------------------------------------------------------------------
# PlaylistMetadata.from_tidal
# ---------------------------------------------------------------------------


def test_from_tidal_name_and_ids():
    resp = {
        "title": "Tidal Mix",
        "tracks": [{"id": 10}, {"id": 20}],
    }
    meta = PlaylistMetadata.from_tidal(resp)
    assert meta.name == "Tidal Mix"
    assert meta.tracks == ["10", "20"]


def test_from_tidal_empty_tracks():
    resp = {"title": "Empty", "tracks": []}
    meta = PlaylistMetadata.from_tidal(resp)
    assert meta.tracks == []


# ---------------------------------------------------------------------------
# PlaylistMetadata.from_soundcloud
# ---------------------------------------------------------------------------


def test_from_soundcloud_builds_track_list():
    sc_track = {"id": 1, "title": "Track"}
    resp = {"title": "SC Playlist", "tracks": [sc_track, sc_track]}

    album_meta = MagicMock()
    track_meta = MagicMock()

    with (
        patch("streamrip.metadata.playlist.AlbumMetadata.from_soundcloud", return_value=album_meta),
        patch("streamrip.metadata.playlist.TrackMetadata.from_soundcloud", return_value=track_meta),
    ):
        meta = PlaylistMetadata.from_soundcloud(resp)

    assert meta.name == "SC Playlist"
    assert meta.tracks == [track_meta, track_meta]


def test_from_soundcloud_empty():
    resp = {"title": "Empty SC", "tracks": []}
    with (
        patch("streamrip.metadata.playlist.AlbumMetadata.from_soundcloud"),
        patch("streamrip.metadata.playlist.TrackMetadata.from_soundcloud"),
    ):
        meta = PlaylistMetadata.from_soundcloud(resp)
    assert meta.tracks == []


# ---------------------------------------------------------------------------
# PlaylistMetadata.from_qobuz
# ---------------------------------------------------------------------------


def test_from_qobuz_builds_track_list():
    qobuz_track = {"album": {"id": "a1"}, "id": "t1"}
    resp = {"name": "Qobuz Picks", "tracks": {"items": [qobuz_track, qobuz_track]}}

    album_meta = MagicMock()
    track_meta = MagicMock()

    with (
        patch("streamrip.metadata.playlist.AlbumMetadata.from_qobuz", return_value=album_meta),
        patch("streamrip.metadata.playlist.TrackMetadata.from_qobuz", return_value=track_meta),
    ):
        meta = PlaylistMetadata.from_qobuz(resp)

    assert meta.name == "Qobuz Picks"
    assert meta.tracks == [track_meta, track_meta]


def test_from_qobuz_skips_unavailable_tracks(caplog):
    qobuz_track = {"album": {}, "id": "t1"}
    resp = {"name": "PL", "tracks": {"items": [qobuz_track]}}

    with (
        patch("streamrip.metadata.playlist.AlbumMetadata.from_qobuz", return_value=MagicMock()),
        patch("streamrip.metadata.playlist.TrackMetadata.from_qobuz", return_value=None),
    ):
        meta = PlaylistMetadata.from_qobuz(resp)

    assert meta.tracks == []
    assert "not available for stream" in caplog.text


def test_from_qobuz_empty_items():
    resp = {"name": "Empty", "tracks": {"items": []}}
    with (
        patch("streamrip.metadata.playlist.AlbumMetadata.from_qobuz"),
        patch("streamrip.metadata.playlist.TrackMetadata.from_qobuz"),
    ):
        meta = PlaylistMetadata.from_qobuz(resp)
    assert meta.tracks == []


# ---------------------------------------------------------------------------
# PlaylistMetadata.from_resp dispatch
# ---------------------------------------------------------------------------


def test_from_resp_dispatches_deezer():
    resp = {"title": "D", "tracks": [{"id": 1}]}
    meta = PlaylistMetadata.from_resp(resp, "deezer")
    assert meta.name == "D"


def test_from_resp_dispatches_tidal():
    resp = {"title": "T", "tracks": [{"id": 2}]}
    meta = PlaylistMetadata.from_resp(resp, "tidal")
    assert meta.name == "T"


def test_from_resp_dispatches_qobuz():
    resp = {"name": "Q", "tracks": {"items": []}}
    with (
        patch("streamrip.metadata.playlist.AlbumMetadata.from_qobuz"),
        patch("streamrip.metadata.playlist.TrackMetadata.from_qobuz"),
    ):
        meta = PlaylistMetadata.from_resp(resp, "qobuz")
    assert meta.name == "Q"


def test_from_resp_dispatches_soundcloud():
    resp = {"title": "SC", "tracks": []}
    meta = PlaylistMetadata.from_resp(resp, "soundcloud")
    assert meta.name == "SC"


def test_from_resp_raises_on_unknown_source():
    with pytest.raises(NotImplementedError):
        PlaylistMetadata.from_resp({}, "unknown")


# ---------------------------------------------------------------------------
# PlaylistMetadata.ids
# ---------------------------------------------------------------------------


def test_ids_empty():
    assert PlaylistMetadata("PL", []).ids() == []


def test_ids_string_tracks():
    meta = PlaylistMetadata("PL", ["1", "2", "3"])
    assert meta.ids() == ["1", "2", "3"]


def test_ids_track_metadata_objects():
    t1, t2 = MagicMock(), MagicMock()
    t1.info.id = "aaa"
    t2.info.id = "bbb"
    meta = PlaylistMetadata("PL", [t1, t2])
    assert meta.ids() == ["aaa", "bbb"]
