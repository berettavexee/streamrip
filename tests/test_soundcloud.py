from unittest.mock import AsyncMock, MagicMock

import pytest

from streamrip.client.soundcloud import SoundcloudClient, _find_client_id
from streamrip.config import Config

CLIENT_ID = "a" * 16 + "B" * 8 + "1234567z"


def _track(*transcodings, **extra):
    return {
        "id": 1,
        "streamable": True,
        "policy": "ALLOW",
        "downloadable": False,
        "has_downloads_left": False,
        "media": {"transcodings": list(transcodings)},
        **extra,
    }


def _tc(protocol, mime, url):
    return {"url": url, "format": {"protocol": protocol, "mime_type": mime}}


def test_client_id_found_in_bundle():
    assert _find_client_id(f'...,client_id:"{CLIENT_ID}",env:"production"') == CLIENT_ID
    assert _find_client_id(f"fetch('/x?client_id={CLIENT_ID}&a=1')") == CLIENT_ID
    assert _find_client_id("nothing to see here") is None


def test_progressive_mp3_preferred():
    track = _track(
        _tc("hls", "audio/mpeg", "https://x/stream/hls"),
        _tc("progressive", "audio/mpeg", "https://x/stream/progressive"),
    )
    assert SoundcloudClient._get_custom_id(track) == "1|https://x/stream/progressive"


def test_hls_mp3_used_when_no_progressive():
    track = _track(
        _tc("hls", 'audio/mp4; codecs="mp4a.40.2"', "https://x/aac"),
        _tc("hls", "audio/mpeg", "https://x/stream/hls"),
    )
    assert SoundcloudClient._get_custom_id(track) == "1|https://x/stream/hls"


def test_no_mp3_transcoding_is_non_streamable_instead_of_assertion():
    track = _track(_tc("hls", 'audio/ogg; codecs="opus"', "https://x/opus"))
    assert SoundcloudClient._get_custom_id(track).endswith("_non_streamable")


@pytest.mark.asyncio
async def test_refresh_tokens_scans_scripts_from_the_end():
    client = SoundcloudClient(Config.defaults())
    pages = {
        "https://soundcloud.com/": (
            '<script>window.__sc_version="1700000000"</script>'
            '<script src="https://a-v2.sndcdn.com/assets/0-a.js"></script>'
            '<script crossorigin src="https://a-v2.sndcdn.com/assets/49-b.js"></script>'
        ),
        "https://a-v2.sndcdn.com/assets/49-b.js": f'client_id:"{CLIENT_ID}"',
        "https://a-v2.sndcdn.com/assets/0-a.js": "no id here",
    }

    def get(url):
        resp = MagicMock()
        resp.text = AsyncMock(return_value=pages[url])
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    client.session = MagicMock()
    client.session.get = MagicMock(side_effect=get)
    assert await client._refresh_tokens() == (CLIENT_ID, "1700000000")


def test_snipped_preview_is_not_taken_for_the_track():
    """A SNIP-policy track only offers a 30 s preview; it is not the track."""
    track = _track(
        {**_tc("progressive", "audio/mpeg", "https://x/preview"), "snipped": True}
    )
    assert SoundcloudClient._get_custom_id(track).endswith("_non_streamable")


def _client_with_responses(responses):
    """A logged-in client whose _api_request/_request answer from a dict."""
    client = SoundcloudClient(Config.defaults())
    client.session = MagicMock()

    async def answer(path_or_url, params=None, headers=None):
        for key, value in responses.items():
            if path_or_url.endswith(key):
                return value
        raise AssertionError(f"unexpected request {path_or_url}")

    client._api_request = AsyncMock(side_effect=answer)
    client._request = AsyncMock(side_effect=answer)
    return client


async def test_original_refused_falls_back_to_the_mp3_stream():
    """Anonymously, tracks/<id>/download now answers 401 even for downloadable
    tracks, which made them fail outright (seen on soundcloud.com/forss/
    flickermood). The MP3 stream is used instead."""
    client = _client_with_responses(
        {
            "tracks/293/download": ({}, 401),
            "tracks/293": (
                _track(
                    _tc("progressive", "audio/mpeg", "https://x/stream/progressive")
                ),
                200,
            ),
            "/stream/progressive": ({"url": "https://cdn/signed.mp3"}, 200),
        }
    )

    d = await client.get_downloadable("293|_original_download", None)

    assert d.url == "https://cdn/signed.mp3"
    assert d.extension == "mp3"


async def test_original_refused_without_stream_is_non_streamable():
    from streamrip.exceptions import NonStreamableError

    client = _client_with_responses(
        {
            "tracks/293/download": ({}, 401),
            "tracks/293": (_track(), 200),
        }
    )
    with pytest.raises(NonStreamableError, match="no MP3 stream"):
        await client.get_downloadable("293|_original_download", None)


async def test_request_returns_status_for_a_non_json_error_body():
    """The 401 of the download endpoint has an empty, non-JSON body; decoding it
    used to raise before anyone looked at the status."""
    client = SoundcloudClient(Config.defaults())
    resp = MagicMock(status=401)
    resp.json = AsyncMock(side_effect=ValueError("not json"))
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    client.session = MagicMock(get=MagicMock(return_value=ctx))

    assert await client._request("https://api-v2.soundcloud.com/tracks/1/download") == (
        {},
        401,
    )
