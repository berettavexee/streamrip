import asyncio
import itertools
import logging
import random
import re

from ..config import Config
from ..exceptions import NonStreamableError
from .client import Client
from .downloadable import SoundcloudDownloadable

# e.g. 123456-293847-121314-209849
USER_ID = "-".join(str(random.randint(111111, 999999)) for _ in range(4))
BASE = "https://api-v2.soundcloud.com"
STOCK_URL = "https://soundcloud.com/"

# for playlists
MAX_BATCH_SIZE = 50

logger = logging.getLogger("streamrip")


class SoundcloudClient(Client):
    source = "soundcloud"
    logged_in = False

    NON_STREAMABLE = "_non_streamable"
    ORIGINAL_DOWNLOAD = "_original_download"
    NOT_RESOLVED = "_not_resolved"

    def __init__(self, config: Config):
        self.logged_in = False
        self._login_lock = asyncio.Lock()
        self.global_config = config
        self.config = config.session.soundcloud
        self.rate_limiter = self.get_rate_limiter(
            config.session.downloads.requests_per_minute,
        )

    async def login(self):
        self.session = await self.get_session(
            verify_ssl=self.global_config.session.downloads.verify_ssl
        )
        client_id, app_version = self.config.client_id, self.config.app_version
        # app_version is optional: SoundCloud does not always expose it, and
        # requiring it made every run re-scrape the website.
        if not client_id or not (await self._announce_success()):
            client_id, app_version = await self._refresh_tokens()
            # update file and session configs and save to disk
            cf = self.global_config.file.soundcloud
            cs = self.global_config.session.soundcloud
            cs.client_id = client_id
            cs.app_version = app_version
            cf.client_id = client_id
            cf.app_version = app_version
            self.global_config.file.set_modified()

        logger.debug(f"Current valid {client_id=} {app_version=}")
        self.logged_in = True

    async def get_metadata(self, item_id: str, media_type: str) -> dict:
        """Fetch metadata for an item in Soundcloud API.

        Args:

            item_id (str): Plain soundcloud item ID (e.g 1633786176)
            media_type (str): track or playlist

        Returns:

            API response. The item IDs for the tracks in the playlist are modified to
            include resolution status.
        """
        if media_type == "track":
            # parse custom id that we injected
            _item_id, _ = item_id.split("|")
            return await self._get_track(_item_id)
        elif media_type == "playlist":
            return await self._get_playlist(item_id)
        else:
            raise Exception(f"{media_type} not supported")

    async def search(
        self,
        media_type: str,
        query: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        # TODO: implement pagination
        assert media_type in ("track", "playlist"), f"Cannot search for {media_type}"
        params = {
            "q": query,
            "facet": "genre",
            "user_id": USER_ID,
            "limit": limit,
            "offset": offset,
            "linked_partitioning": "1",
        }
        resp, status = await self._api_request(f"search/{media_type}s", params=params)
        if status != 200:
            raise NonStreamableError(f"SoundCloud search failed with status {status}")
        if media_type == "track":
            for item in resp["collection"]:
                item["id"] = self._get_custom_id(item)
        return [resp]

    async def get_downloadable(self, item_info: str, _) -> SoundcloudDownloadable:
        # We have `get_metadata` overwrite the "id" field so that it contains
        # some extra information we need to download soundcloud tracks

        # item_id is the soundcloud ID of the track
        # download_url is either the url that points to an mp3 download or ""
        # if download_url == '_non_streamable' then we raise an exception

        infos: list[str] = item_info.split("|")
        logger.debug(f"{infos=}")
        if len(infos) != 2:
            raise NonStreamableError(f"Malformed SoundCloud item ID: {item_info!r}")
        item_id, download_info = infos
        if re.match(r"\d+", item_id) is None:
            raise NonStreamableError(f"SoundCloud item ID is not numeric: {item_id!r}")

        if download_info == self.NON_STREAMABLE:
            raise NonStreamableError(item_info)

        if download_info == self.ORIGINAL_DOWNLOAD:
            resp_json, status = await self._api_request(f"tracks/{item_id}/download")
            if status == 200:
                return SoundcloudDownloadable(
                    self.session,
                    {"url": resp_json["redirectUri"], "type": "original"},
                )
            # The original file needs a signed-in user now: anonymously this
            # endpoint answers 401 even for tracks marked downloadable, which
            # made them fail outright. Fall back to the MP3 stream.
            track, track_status = await self._api_request(f"tracks/{item_id}")
            stream = self._pick_mp3_transcoding(track) if track_status == 200 else None
            if stream is None:
                raise NonStreamableError(
                    f"SoundCloud original download unavailable for track {item_id} "
                    f"(status {status}), and no MP3 stream to fall back to"
                )
            logger.info(
                "SoundCloud original of track %s unavailable (status %s); "
                "downloading the MP3 stream instead",
                item_id,
                status,
            )
            download_info = stream

        if download_info == self.NOT_RESOLVED:
            raise NotImplementedError(item_info)

        # download_info contains a transcoding url, which resolves to the
        # actual (signed) media url
        resp_json, status = await self._request(download_info)
        if status != 200 or "url" not in resp_json:
            raise NonStreamableError(f"Could not resolve stream for track {item_id}")
        file_type = "progressive" if "/progressive" in download_info else "mp3"
        return SoundcloudDownloadable(
            self.session,
            {"url": resp_json["url"], "type": file_type},
        )

    async def resolve_url(self, url: str) -> dict:
        """Get metadata of the item pointed to by a soundcloud url.

        This is necessary only for soundcloud because they don't store
        the item IDs in their url. See SoundcloudURL.into_pending for example
        usage.

        Args:
            url (str): Url to resolve.

        Returns:
            API response for item.
        """
        resp, status = await self._api_request("resolve", params={"url": url})
        if status != 200:
            raise NonStreamableError(
                f"SoundCloud URL resolution failed with status {status}"
            )
        if resp["kind"] == "track":
            resp["id"] = self._get_custom_id(resp)

        return resp

    async def _get_track(self, item_id: str):
        resp, status = await self._api_request(f"tracks/{item_id}")
        if status != 200:
            raise NonStreamableError(
                f"SoundCloud track {item_id} not found (status {status})"
            )
        return resp

    async def _get_playlist(self, item_id: str):
        original_resp, status = await self._api_request(f"playlists/{item_id}")
        if status != 200:
            raise NonStreamableError(
                f"SoundCloud playlist {item_id} not found (status {status})"
            )

        unresolved_tracks = [
            track["id"] for track in original_resp["tracks"] if "media" not in track
        ]

        if len(unresolved_tracks) == 0:
            return original_resp

        batches = batched(unresolved_tracks, MAX_BATCH_SIZE)
        requests = [
            self._api_request(
                "tracks",
                params={"ids": ",".join(str(id) for id in filter_none(batch))},
            )
            for batch in batches
        ]

        # (list of track metadata, status code)
        responses: list[tuple[list, int]] = await asyncio.gather(*requests)

        failed = [status for _, status in responses if status != 200]
        if failed:
            raise NonStreamableError(
                f"SoundCloud batch track fetch failed (statuses: {failed})"
            )

        remaining_tracks = list(itertools.chain(*[resp for resp, _ in responses]))

        # Insert the new metadata into the original response
        track_map: dict[str, dict] = {track["id"]: track for track in remaining_tracks}
        for i, track in enumerate(original_resp["tracks"]):
            if "media" in track:  # track already has metadata
                continue
            this_track = track_map.get(track["id"])
            if this_track is None:
                raise Exception(f"Requested {track['id']} but got no response")
            original_resp["tracks"][i] = this_track

        # Overwrite all ids in playlist
        for track in original_resp["tracks"]:
            track["id"] = self._get_custom_id(track)

        return original_resp

    @classmethod
    def _get_custom_id(cls, resp: dict) -> str:
        item_id = resp["id"]
        if "media" not in resp:
            return f"{resp['id']}|{cls.NON_STREAMABLE}"

        if not resp["streamable"] or resp["policy"] == "BLOCK":
            return f"{item_id}|{cls.NON_STREAMABLE}"

        if resp["downloadable"] and resp["has_downloads_left"]:
            return f"{item_id}|{cls.ORIGINAL_DOWNLOAD}"

        url = cls._pick_mp3_transcoding(resp)
        if url is None:
            return f"{item_id}|{cls.NON_STREAMABLE}"
        return f"{item_id}|{url}"

    @staticmethod
    def _pick_mp3_transcoding(resp: dict) -> str | None:
        """Choose the MP3 stream of a track: progressive first, then HLS.

        A progressive stream is a single file; HLS is segments. SoundCloud is
        phasing out MP3 HLS for many tracks, and a track with no MP3 stream at
        all used to fail an assertion.

        Args:
            resp: A SoundCloud track resource, with its ``media`` field.

        Returns:
            The transcoding URL to resolve, or None when the track has no
            full-length MP3 stream (``snipped`` previews are skipped).
        """
        transcodings = (resp.get("media") or {}).get("transcodings") or []
        for protocol in ("progressive", "hls"):
            for tc in transcodings:
                fmt = tc.get("format") or {}
                if (
                    fmt.get("protocol") == protocol
                    and fmt.get("mime_type") == "audio/mpeg"
                    and not tc.get("snipped")
                ):
                    return tc["url"]
        return None

    def _auth_params(self) -> dict:
        """Query parameters every API request carries.

        Returns:
            ``client_id`` and ``app_locale``, plus ``app_version`` when known --
            SoundCloud does not always expose it.
        """
        c = self.config
        params = {"client_id": c.client_id, "app_locale": "en"}
        if c.app_version:
            params["app_version"] = c.app_version
        return params

    async def _api_request(self, path, params=None, headers=None):
        url = f"{BASE}/{path}"
        return await self._request(url, params=params, headers=headers)

    async def _request(self, url, params=None, headers=None) -> tuple[dict, int]:
        """GET a SoundCloud API URL with the client's auth parameters.

        Args:
            url: The full URL.
            params: Extra query parameters.
            headers: Extra request headers.

        Returns:
            ``(json, status)``. An error answer whose body is not JSON -- the
            empty 401 of the download endpoint, an HTML error page -- yields
            ``({}, status)`` so the caller can act on the status instead of
            crashing on the decoding.
        """
        _params = self._auth_params()
        if params is not None:
            _params.update(params)

        logger.debug(f"Requesting {url} with {_params=}, {headers=}")
        async with self.session.get(url, params=_params, headers=headers) as resp:
            try:
                return await resp.json(content_type=None), resp.status
            except ValueError:
                return {}, resp.status

    async def _request_body(self, url, params=None, headers=None):
        _params = self._auth_params()
        if params is not None:
            _params.update(params)

        async with self.session.get(url, params=_params, headers=headers) as resp:
            return await resp.content.read(), resp.status

    async def _announce_success(self):
        url = f"{BASE}/announcements"
        _, status = await self._request_body(url)
        return status == 200

    async def _refresh_tokens(self) -> tuple[str, str]:
        """Return a valid client_id, app_version pair.

        The client id is embedded in one of the JavaScript bundles loaded by
        soundcloud.com. Rather than relying on the exact markup of the script
        tags (which changed and broke this, upstream #1038), try every bundle,
        newest last, the way yt-dlp does.
        """
        async with self.session.get(STOCK_URL) as resp:
            page_text = await resp.text(encoding="utf-8")

        app_version_match = re.search(r'__sc_version\s*=\s*"(\d+)"', page_text)
        app_version = app_version_match.group(1) if app_version_match else ""

        script_urls = re.findall(r'<script[^>]+src="([^"]+)"', page_text)
        for script_url in reversed(script_urls):
            if script_url.startswith("//"):
                script_url = "https:" + script_url
            elif script_url.startswith("/"):
                script_url = STOCK_URL.rstrip("/") + script_url
            try:
                async with self.session.get(script_url) as resp:
                    script = await resp.text(encoding="utf-8")
            except Exception as e:
                logger.debug("Could not fetch %s: %s", script_url, e)
                continue
            client_id = _find_client_id(script)
            if client_id is not None:
                logger.debug(
                    f"Refreshed soundcloud tokens as {client_id=} {app_version=}"
                )
                return client_id, app_version

        raise Exception(
            f"Could not find a SoundCloud client id in {len(script_urls)} scripts "
            f"on {STOCK_URL}. SoundCloud may have changed its website."
        )


CLIENT_ID_REGEXES = (
    re.compile(r'client_id\s*:\s*"([0-9a-zA-Z]{32})"'),
    re.compile(r"client_id=([0-9a-zA-Z]{32})"),
)


def _find_client_id(script: str) -> str | None:
    """Find a SoundCloud client id in a JavaScript bundle.

    Args:
        script: The bundle's source.

    Returns:
        The 32-character client id, or None when the bundle has none.
    """
    for regex in CLIENT_ID_REGEXES:
        match = regex.search(script)
        if match is not None:
            return match.group(1)
    return None


def batched(iterable, n, fillvalue=None):
    args = [iter(iterable)] * n
    return list(itertools.zip_longest(*args, fillvalue=fillvalue))


def filter_none(iterable):
    return (x for x in iterable if x is not None)
