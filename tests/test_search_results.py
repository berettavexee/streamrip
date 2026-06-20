"""Tests for streamrip/metadata/search_results.py."""

from unittest.mock import MagicMock, patch

import pytest

from streamrip.metadata.search_results import (
    AlbumSummary,
    ArtistSummary,
    LabelSummary,
    PlaylistSummary,
    SearchResults,
    TrackSummary,
    clean,
)

# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------


def test_clean_removes_pipes():
    assert "|" not in clean("foo|bar")


def test_clean_removes_newlines():
    assert "\n" not in clean("foo\nbar")


def test_clean_truncates_at_50():
    s = "a" * 60
    assert len(clean(s)) == 50


def test_clean_no_truncation():
    s = "a" * 60
    assert len(clean(s, trunc=False)) == 60


def test_clean_short_string_unchanged():
    assert clean("Hello") == "Hello"


# ---------------------------------------------------------------------------
# ArtistSummary
# ---------------------------------------------------------------------------


def test_artist_media_type():
    assert ArtistSummary("1", "Daft Punk", "8").media_type() == "artist"


def test_artist_summarize():
    a = ArtistSummary("1", "Daft Punk", "8")
    assert a.summarize() == "Daft Punk"


def test_artist_preview():
    a = ArtistSummary("1", "Daft Punk", "8")
    assert "8" in a.preview()
    assert "1" in a.preview()


def test_artist_str():
    a = ArtistSummary("1", "Daft Punk", "8")
    assert str(a) == "Daft Punk"


def test_artist_from_item_name_field():
    a = ArtistSummary.from_item({"id": "1", "name": "Daft Punk", "albums_count": 8})
    assert a.name == "Daft Punk"
    assert a.num_albums == 8


def test_artist_from_item_performer_fallback():
    a = ArtistSummary.from_item({"id": "2", "performer": {"name": "Burial"}})
    assert a.name == "Burial"


def test_artist_from_item_artist_string_fallback():
    a = ArtistSummary.from_item({"id": "3", "artist": "Actress"})
    assert a.name == "Actress"


def test_artist_from_item_publisher_metadata_fallback():
    a = ArtistSummary.from_item({
        "id": "4",
        "publisher_metadata": {"artist": "Floating Points"},
    })
    assert a.name == "Floating Points"


def test_artist_from_item_unknown_fallback():
    a = ArtistSummary.from_item({"id": "5"})
    assert a.name == "Unknown"


def test_artist_from_item_albums_count_unknown():
    a = ArtistSummary.from_item({"id": "6", "name": "X"})
    assert a.num_albums == "Unknown"


# ---------------------------------------------------------------------------
# TrackSummary
# ---------------------------------------------------------------------------


def test_track_media_type():
    assert TrackSummary("1", "Song", "Artist", "2020").media_type() == "track"


def test_track_summarize():
    t = TrackSummary("1", "Song", "Artist", "2020")
    assert t.summarize() == "Song by Artist"


def test_track_preview():
    t = TrackSummary("1", "Song", "Artist", "2020")
    assert "2020" in t.preview()
    assert "1" in t.preview()


def test_track_from_item_title_field():
    t = TrackSummary.from_item({"id": "1", "title": "Song", "artist": "Artist"})
    assert t.name == "Song"


def test_track_from_item_name_fallback():
    t = TrackSummary.from_item({"id": "1", "name": "Song", "artist": "Artist"})
    assert t.name == "Song"


def test_track_from_item_strips_name():
    t = TrackSummary.from_item({"id": "1", "title": "  Song  ", "artist": "A"})
    assert t.name == "Song"


def test_track_from_item_performer_artist():
    t = TrackSummary.from_item({"id": "1", "title": "S",
                                 "performer": {"name": "The Artist"}})
    assert t.artist == "The Artist"


def test_track_from_item_artist_string():
    t = TrackSummary.from_item({"id": "1", "title": "S", "artist": "Solo"})
    assert t.artist == "Solo"


def test_track_from_item_artist_dict():
    t = TrackSummary.from_item({"id": "1", "title": "S",
                                 "artist": {"name": "Band"}})
    assert t.artist == "Band"


def test_track_from_item_publisher_metadata_artist():
    t = TrackSummary.from_item({
        "id": "1", "title": "S",
        "publisher_metadata": {"artist": "SC Artist"},
    })
    assert t.artist == "SC Artist"


def test_track_from_item_artist_unknown():
    t = TrackSummary.from_item({"id": "1", "title": "S"})
    assert t.artist == "Unknown"


def test_track_from_item_date_release_date():
    t = TrackSummary.from_item({"id": "1", "title": "S", "artist": "A",
                                 "release_date": "2021-01-01"})
    assert t.date_released == "2021-01-01"


def test_track_from_item_date_stream_start():
    t = TrackSummary.from_item({"id": "1", "title": "S", "artist": "A",
                                 "streamStartDate": "2022-05-10"})
    assert t.date_released == "2022-05-10"


def test_track_from_item_date_album_original():
    t = TrackSummary.from_item({"id": "1", "title": "S", "artist": "A",
                                 "album": {"release_date_original": "2019-03-01"}})
    assert t.date_released == "2019-03-01"


def test_track_from_item_date_display_date():
    t = TrackSummary.from_item({"id": "1", "title": "S", "artist": "A",
                                 "display_date": "2018"})
    assert t.date_released == "2018"


def test_track_from_item_date_unknown():
    t = TrackSummary.from_item({"id": "1", "title": "S", "artist": "A"})
    assert t.date_released == "Unknown"


# ---------------------------------------------------------------------------
# AlbumSummary
# ---------------------------------------------------------------------------


def test_album_media_type():
    assert AlbumSummary("1", "Album", "Artist", "10", "2020").media_type() == "album"


def test_album_summarize():
    a = AlbumSummary("1", "Album", "Artist", "10", "2020")
    assert a.summarize() == "Album by Artist"


def test_album_preview():
    a = AlbumSummary("1", "Album", "Artist", "10", "2020")
    p = a.preview()
    assert "2020" in p
    assert "10" in p
    assert "1" in p


def test_album_from_item_title_only():
    a = AlbumSummary.from_item({"id": "1", "title": "Discovery",
                                  "artist": {"name": "Daft Punk"}})
    assert a.name == "Discovery"


def test_album_from_item_title_with_version():
    a = AlbumSummary.from_item({"id": "1", "title": "Discovery",
                                  "version": "Remastered",
                                  "artist": {"name": "Daft Punk"}})
    assert a.name == "Discovery (Remastered)"


def test_album_from_item_artist_performer():
    a = AlbumSummary.from_item({"id": "1", "title": "A",
                                  "performer": {"name": "Performer"}})
    assert a.artist == "Performer"


def test_album_from_item_artist_dict():
    a = AlbumSummary.from_item({"id": "1", "title": "A",
                                  "artist": {"name": "Band"}})
    assert a.artist == "Band"


def test_album_from_item_artist_publisher_metadata():
    a = AlbumSummary.from_item({"id": "1", "title": "A",
                                  "publisher_metadata": {"artist": "SC"}})
    assert a.artist == "SC"


def test_album_from_item_artist_unknown():
    a = AlbumSummary.from_item({"id": "1", "title": "A"})
    assert a.artist == "Unknown"


def test_album_from_item_num_tracks_count():
    a = AlbumSummary.from_item({"id": "1", "title": "A", "tracks_count": 12})
    assert a.num_tracks == "12"


def test_album_from_item_num_tracks_number_of_tracks():
    a = AlbumSummary.from_item({"id": "1", "title": "A", "numberOfTracks": 8})
    assert a.num_tracks == "8"


def test_album_from_item_num_tracks_from_list():
    a = AlbumSummary.from_item({"id": "1", "title": "A",
                                  "tracks": [{}, {}, {}]})
    assert a.num_tracks == "3"


def test_album_from_item_num_tracks_from_items():
    a = AlbumSummary.from_item({"id": "1", "title": "A",
                                  "items": [{}, {}]})
    assert a.num_tracks == "2"


def test_album_from_item_date_release_date_original():
    a = AlbumSummary.from_item({"id": "1", "title": "A",
                                  "release_date_original": "2020-01-01"})
    assert a.date_released == "2020-01-01"


def test_album_from_item_date_release_date():
    a = AlbumSummary.from_item({"id": "1", "title": "A",
                                  "release_date": "2019-01-01"})
    assert a.date_released == "2019-01-01"


def test_album_from_item_date_tidal():
    a = AlbumSummary.from_item({"id": "1", "title": "A",
                                  "releaseDate": "2018-06-01"})
    assert a.date_released == "2018-06-01"


def test_album_from_item_date_unknown():
    a = AlbumSummary.from_item({"id": "1", "title": "A"})
    assert a.date_released == "Unknown"


# ---------------------------------------------------------------------------
# LabelSummary
# ---------------------------------------------------------------------------


def test_label_media_type():
    assert LabelSummary("1", "Warp").media_type() == "label"


def test_label_summarize():
    assert LabelSummary("1", "Warp").summarize() == "Warp"


def test_label_preview():
    p = LabelSummary("42", "Warp").preview()
    assert "42" in p


def test_label_from_item():
    lab = LabelSummary.from_item({"id": "7", "name": "Ninja Tune"})
    assert lab.id == "7"
    assert lab.name == "Ninja Tune"


# ---------------------------------------------------------------------------
# PlaylistSummary
# ---------------------------------------------------------------------------


def _terminal(columns=80):
    m = MagicMock()
    m.columns = columns
    return m


def test_playlist_media_type():
    pl = PlaylistSummary("1", "Hits", "User", 20, "Great playlist")
    assert pl.media_type() == "playlist"


def test_playlist_summarize():
    pl = PlaylistSummary("1", "Hits", "User", 20, "desc")
    assert pl.summarize() == "Hits by User"


def test_playlist_preview():
    pl = PlaylistSummary("1", "Hits", "User", 20, "Great desc")
    with patch("streamrip.metadata.search_results.os.get_terminal_size",
               return_value=_terminal(80)):
        p = pl.preview()
    assert "20" in p
    assert "Great desc" in p
    assert "1" in p


def test_playlist_from_item_id_uuid_fallback():
    pl = PlaylistSummary.from_item({"uuid": "abc-123", "title": "PL",
                                    "user": {"username": "u"}})
    assert pl.id == "abc-123"


def test_playlist_from_item_name_title_fallback():
    pl = PlaylistSummary.from_item({"id": "1", "title": "My PL",
                                    "user": {"username": "u"}})
    assert pl.name == "My PL"


def test_playlist_from_item_creator_publisher_metadata():
    pl = PlaylistSummary.from_item({
        "id": "1", "name": "PL",
        "publisher_metadata": {"artist": "SC Artist"},
    })
    assert pl.creator == "SC Artist"


def test_playlist_from_item_creator_owner():
    pl = PlaylistSummary.from_item({"id": "1", "name": "PL",
                                    "owner": {"name": "Qobuz User"}})
    assert pl.creator == "Qobuz User"


def test_playlist_from_item_creator_user_username():
    pl = PlaylistSummary.from_item({"id": "1", "name": "PL",
                                    "user": {"username": "sc_user"}})
    assert pl.creator == "sc_user"


def test_playlist_from_item_creator_user_name():
    pl = PlaylistSummary.from_item({"id": "1", "name": "PL",
                                    "user": {"name": "tidal_user"}})
    assert pl.creator == "tidal_user"


def test_playlist_from_item_creator_unknown():
    pl = PlaylistSummary.from_item({"id": "1", "name": "PL"})
    assert pl.creator == "Unknown"


def test_playlist_from_item_num_tracks_count():
    pl = PlaylistSummary.from_item({"id": "1", "name": "PL",
                                    "tracks_count": 15})
    assert pl.num_tracks == 15


def test_playlist_from_item_num_tracks_nb_tracks():
    pl = PlaylistSummary.from_item({"id": "1", "name": "PL",
                                    "nb_tracks": 12})
    assert pl.num_tracks == 12


def test_playlist_from_item_num_tracks_number_of_tracks():
    pl = PlaylistSummary.from_item({"id": "1", "name": "PL",
                                    "numberOfTracks": 30})
    assert pl.num_tracks == 30


def test_playlist_from_item_num_tracks_list():
    pl = PlaylistSummary.from_item({"id": "1", "name": "PL",
                                    "tracks": [{}, {}, {}]})
    assert pl.num_tracks == 3


def test_playlist_from_item_num_tracks_none():
    pl = PlaylistSummary.from_item({"id": "1", "name": "PL"})
    assert pl.num_tracks == -1


def test_playlist_from_item_description_absent():
    pl = PlaylistSummary.from_item({"id": "1", "name": "PL"})
    assert pl.description == "No description"


# ---------------------------------------------------------------------------
# SearchResults.from_pages
# ---------------------------------------------------------------------------


_TRACK_ITEM = {"id": "1", "title": "Song", "artist": "Artist"}
_ALBUM_ITEM = {"id": "2", "title": "Album", "artist": {"name": "Artist"}}
_ARTIST_ITEM = {"id": "3", "name": "Artist"}
_LABEL_ITEM = {"id": "4", "name": "Warp"}
_PLAYLIST_ITEM = {"id": "5", "name": "PL", "user": {"username": "u"}}


def test_from_pages_deezer_track():
    pages = [{"data": [_TRACK_ITEM]}]
    sr = SearchResults.from_pages("deezer", "track", pages)
    assert len(sr.results) == 1
    assert isinstance(sr.results[0], TrackSummary)


def test_from_pages_qobuz_album():
    pages = [{"albums": {"items": [_ALBUM_ITEM]}}]
    sr = SearchResults.from_pages("qobuz", "album", pages)
    assert len(sr.results) == 1
    assert isinstance(sr.results[0], AlbumSummary)


def test_from_pages_soundcloud_artist():
    pages = [{"collection": [_ARTIST_ITEM]}]
    sr = SearchResults.from_pages("soundcloud", "artist", pages)
    assert len(sr.results) == 1
    assert isinstance(sr.results[0], ArtistSummary)


def test_from_pages_tidal_playlist():
    pages = [{"items": [_PLAYLIST_ITEM]}]
    sr = SearchResults.from_pages("tidal", "playlist", pages)
    assert len(sr.results) == 1
    assert isinstance(sr.results[0], PlaylistSummary)


def test_from_pages_deezer_label():
    pages = [{"data": [_LABEL_ITEM]}]
    sr = SearchResults.from_pages("deezer", "label", pages)
    assert isinstance(sr.results[0], LabelSummary)


def test_from_pages_multiple_pages():
    pages = [{"data": [_TRACK_ITEM]}, {"data": [_TRACK_ITEM, _TRACK_ITEM]}]
    sr = SearchResults.from_pages("deezer", "track", pages)
    assert len(sr.results) == 3


def test_from_pages_invalid_media_type():
    with pytest.raises(Exception, match="invalid media type"):
        SearchResults.from_pages("deezer", "podcast", [])


def test_from_pages_invalid_source():
    with pytest.raises(NotImplementedError):
        SearchResults.from_pages("napster", "track",
                                  [{"data": [_TRACK_ITEM]}])


# ---------------------------------------------------------------------------
# SearchResults methods
# ---------------------------------------------------------------------------


@pytest.fixture
def search_results():
    return SearchResults([
        TrackSummary("1", "Song A", "Artist", "2020"),
        TrackSummary("2", "Song B", "Artist", "2021"),
        TrackSummary("3", "Song C", "Artist", "2022"),
    ])


def test_summaries(search_results):
    s = search_results.summaries()
    assert len(s) == 3
    assert s[0].startswith("1.")
    assert "Song A" in s[0]


def test_get_choices_int(search_results):
    choices = search_results.get_choices(0)
    assert len(choices) == 1
    assert choices[0].name == "Song A"


def test_get_choices_tuple(search_results):
    choices = search_results.get_choices((0, 2))
    assert len(choices) == 2
    assert choices[0].name == "Song A"
    assert choices[1].name == "Song C"


def test_preview_by_number(search_results):
    p = search_results.preview("2. Song B by Artist")
    assert "2021" in p


def test_as_list(search_results):
    lst = search_results.as_list("deezer")
    assert len(lst) == 3
    assert lst[0]["source"] == "deezer"
    assert lst[0]["media_type"] == "track"
    assert lst[0]["id"] == "1"
    assert "Song A" in lst[0]["desc"]
