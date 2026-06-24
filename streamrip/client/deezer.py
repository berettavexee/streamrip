import asyncio
import binascii
import hashlib
import logging
import re
from typing import Any, Callable, ClassVar, Coroutine, Generic, TypeVar

import aiolimiter
import deezer
import requests
from Cryptodome.Cipher import AES
from deezer.errors import DataException, GWAPIError

from ..config import Config
from ..exceptions import (
    AuthenticationError,
    MissingCredentialsError,
    NonStreamableError,
)
from .client import Client
from .downloadable import DeezerDownloadable

logger = logging.getLogger("streamrip")
logging.captureWarnings(True)

K = TypeVar("K")
V = TypeVar("V")


class _TaskCache(Generic[K, V]):
    """Cache with in-flight deduplication for async fetch-once operations.

    Concurrent awaiters on the same key share a single Task; the result is
    stored and returned immediately on all subsequent calls. A failed task is
    evicted so it can be retried.
    """

    def __init__(self) -> None:
        self._results: dict[K, V] = {}
        self._tasks: dict[K, asyncio.Task[V]] = {}

    async def get_or_create(
        self,
        key: K,
        factory: Callable[[], Coroutine[Any, Any, V]],
    ) -> V:
        """Return cached value for key, or run factory() once and cache its result.

        Args:
            key: Cache key.
            factory: Zero-argument coroutine factory, called at most once per key.

        Returns:
            The cached or freshly-fetched value.

        Raises:
            Exception: Whatever factory() raises; the failed task is evicted so the
                next caller retries from scratch.
        """
        if key in self._results:
            return self._results[key]
        if key not in self._tasks:
            self._tasks[key] = asyncio.create_task(factory())
        try:
            result = await self._tasks[key]
            self._results[key] = result
            return result
        except Exception:
            self._tasks.pop(key, None)
            raise

    def set(self, key: K, value: V) -> None:
        """Store a value directly, bypassing the task machinery."""
        self._results[key] = value


class DeezerClient(Client):
    """Deezer API client.

    All blocking deezer-py calls are offloaded to a thread pool via two
    rate-limited helpers:

    - ``_rest()`` — calls to ``api.deezer.com`` (REST, capped at 10 req/s)
    - ``_gw()``   — calls to ``gw-light.php`` (GW, capped at 5 req/s)

    GW track data is cached in ``_gw_tracks`` so each track incurs at most
    one GW round-trip across the lifetime of a session (prefetch from album,
    per-track enrichment, and download URL resolution all share the same entry).

    Album metadata uses ``_albums`` (a ``_TaskCache``) which deduplicates
    concurrent fetches and caches results for the session.

    Attributes:
        global_config: Entire config object.
        client: deezer-py Deezer instance used for REST and GW API requests.
        logged_in: True if login has completed successfully.
        config: Deezer-specific sub-config.
        session: aiohttp.ClientSession used only for track byte-stream downloads.
    """

    source = "deezer"
    max_quality = 2
    max_favorites = 10_000

    # quality index → (gw format id, API format string)
    _QUALITY_MAP: ClassVar[list[tuple[int, str]]] = [
        (9, "MP3_128"),  # quality 0
        (3, "MP3_320"),  # quality 1
        (1, "FLAC"),     # quality 2
    ]

    def __init__(self, config: Config):
        """Initialize the DeezerClient.

        Args:
            config: The application configuration object.
        """
        self.global_config = config
        self.client = deezer.Deezer()
        self.logged_in = False
        self._login_lock = asyncio.Lock()
        self.config = config.session.deezer
        self.logged_in_user_id: int | None = None

        self._albums: _TaskCache[str, dict] = _TaskCache()
        self._gw_tracks: dict[str, dict] = {}

        # REST API (api.deezer.com) throttles beyond ~10 req/sec.
        self._rest_limiter = aiolimiter.AsyncLimiter(10, 1)
        # GW endpoint (gw-light.php) is more conservative.
        self._gw_limiter = aiolimiter.AsyncLimiter(5, 1)

        max_conn = config.session.downloads.max_connections
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=max_conn,
            pool_maxsize=max(max_conn * 4, 32),
            max_retries=0,
        )
        self.client.session.mount("https://", adapter)
        self.client.session.mount("http://", adapter)

    # ── backward-compatible aliases (used by tests) ──────────────────────────

    @property
    def _album_cache(self) -> dict[str, dict]:
        return self._albums._results

    @property
    def _album_tasks(self) -> dict[str, asyncio.Task]:
        return self._albums._tasks

    # ── low-level API helpers ─────────────────────────────────────────────────

    async def _rest(self, fn: Callable, /, *args: Any, **kwargs: Any) -> Any:
        """Run a blocking deezer-py REST call in a thread, rate-limited to 10 req/s.

        Args:
            fn: Callable to invoke (e.g. ``self.client.api.get_track``).
            *args: Positional arguments forwarded to fn.
            **kwargs: Keyword arguments forwarded to fn.

        Returns:
            Whatever fn returns.
        """
        async with self._rest_limiter:
            return await asyncio.to_thread(fn, *args, **kwargs)

    async def _gw(self, fn: Callable, /, *args: Any, **kwargs: Any) -> Any:
        """Run a blocking deezer-py GW call in a thread, rate-limited to 5 req/s.

        Args:
            fn: Callable to invoke (e.g. ``self.client.gw.get_track``).
            *args: Positional arguments forwarded to fn.
            **kwargs: Keyword arguments forwarded to fn.

        Returns:
            Whatever fn returns.
        """
        async with self._gw_limiter:
            return await asyncio.to_thread(fn, *args, **kwargs)

    async def _get_gw_track(self, item_id: str) -> dict:
        """Return GW track info from cache, or fetch and cache it on a miss.

        Args:
            item_id: Deezer track ID.

        Returns:
            GW track info dict containing TRACK_TOKEN, FILESIZE_*, GAIN, etc.

        Raises:
            NonStreamableError: If the GW request fails.
        """
        if item_id in self._gw_tracks:
            return self._gw_tracks[item_id]
        try:
            info = await self._gw(self.client.gw.get_track, item_id)
        except Exception as e:
            raise NonStreamableError(
                f"Could not fetch GW track info for {item_id}: {e}"
            )
        self._gw_tracks[item_id] = info
        return info

    # ── public interface ──────────────────────────────────────────────────────

    async def login(self):
        """Log in via ARL token. Creates the aiohttp session for track downloads.

        Raises:
            MissingCredentialsError: If the ARL is missing from the config.
            AuthenticationError: If login fails.
        """
        # self.session (aiohttp) is used only for track byte-stream downloads;
        # API calls go through self.client.session (requests).
        self.session = await self.get_session(
            verify_ssl=self.global_config.session.downloads.verify_ssl
        )
        arl = self.config.arl
        if not arl:
            raise MissingCredentialsError
        logger.debug("Logging into Deezer")
        success = self.client.login_via_arl(arl)
        if not success:
            raise AuthenticationError
        # login_via_arl already called getUserData internally; read the cached result
        # instead of making a second network round-trip.
        self.logged_in_user_id = self.client.current_user["id"]
        self.logged_in = True
        logger.debug("Deezer login successful (user ID: %s)", self.logged_in_user_id)

    async def get_metadata(self, item_id: str, media_type: str) -> dict:
        """Fetch metadata for a given item, dispatching by media type.

        Args:
            item_id: The ID of the item to fetch.
            media_type: One of "track", "album", "playlist", or "artist".

        Returns:
            The metadata of the requested item.

        Raises:
            Exception: If the media type is not supported.
        """
        handlers = {
            "track": self.get_track,
            "album": self.get_album,
            "playlist": self.get_playlist,
            "artist": self.get_artist,
        }
        handler = handlers.get(media_type)
        if handler is None:
            raise Exception(f"Media type {media_type} not available on deezer")
        logger.debug("Fetching Deezer %s %s", media_type, item_id)
        return await handler(item_id)

    async def _fetch_lyrics(self, item_id: str) -> str | None:
        """Fetch plain-text lyrics via GW song.getLyrics. Returns None when unavailable."""
        try:
            data = await self._gw(self.client.gw.get_track_lyrics, item_id)
            return data.get("LYRICS_TEXT") or None
        except Exception:
            return None

    async def get_track(self, item_id: str, fetch_album: bool = True) -> dict:
        """Fetch metadata for a track, optionally including its full album info.

        GW data (SNG_CONTRIBUTORS, GAIN) is fetched concurrently with the album
        call and cached in ``_gw_tracks`` for reuse by ``get_downloadable``.

        When fetch_album is True (the default), the album sub-object in the
        returned dict is replaced with the full album metadata (genres, tracktotal,
        disctotal, albumartist). When False, the minimal album stub from the REST
        track response is kept.

        Args:
            item_id: The Deezer track ID.
            fetch_album: Whether to fetch the full album metadata. Set to False
                for playlist tracks to save two REST API calls per track.

        Returns:
            Track metadata dict, enriched with "composer", "author", "gain",
            and "lyrics" when available from GW data.

        Raises:
            NonStreamableError: If the track cannot be fetched from the REST API.
        """
        try:
            item = await self._rest(self.client.api.get_track, item_id)
        except Exception as e:
            raise NonStreamableError(e)

        # deezer-py may return an error dict (e.g. {"error": 800, "message":
        # "Quota limit exceeded"}) instead of raising when the HTTP status is
        # 200 but the body is a short JSON error. Detect this early.
        if "error" in item:
            raise NonStreamableError(
                f"Deezer API error {item.get('error')} for track {item_id}: "
                f"{item.get('message', 'unknown error')}"
            )

        try:
            if fetch_album:
                album_id = str(item["album"]["id"])
                album_metadata, gw_info, lyrics = await asyncio.gather(
                    self.get_album(album_id),
                    self._get_gw_track(item_id),
                    self._fetch_lyrics(item_id),
                )
                item["album"] = album_metadata
            else:
                gw_info, lyrics = await asyncio.gather(
                    self._get_gw_track(item_id),
                    self._fetch_lyrics(item_id),
                )
        except Exception as e:
            label = "album/GW data" if fetch_album else "GW data"
            logger.error("Error fetching %s for track %s: %s", label, item_id, e)
            return item

        contributors = gw_info.get("SNG_CONTRIBUTORS", {})
        if "composer" in contributors:
            item["composer"] = contributors["composer"]
        if "author" in contributors:
            item["author"] = contributors["author"]
        gain = gw_info.get("GAIN")
        if gain is not None:
            item["gain"] = gain
        if lyrics:
            item["lyrics"] = lyrics

        return item

    async def get_track_for_playlist(self, item_id: str) -> dict:
        """Fetch track metadata for a playlist context, skipping the album fetch.

        Saves two REST API calls per track (get_album + get_album_tracks) at the
        cost of missing GENRE, TRACKTOTAL, and DISCTOTAL tags on the downloaded
        file.

        Args:
            item_id: The Deezer track ID.

        Returns:
            Track metadata dict without full album sub-object.
        """
        return await self.get_track(item_id, fetch_album=False)

    async def get_album(self, item_id: str) -> dict:
        """Fetch metadata for an album, including its full track list.

        Concurrent calls for the same ID share a single in-flight Task so only
        one pair of REST requests is ever made per album. Results are cached
        for the session lifetime.

        Args:
            item_id: The Deezer album ID.

        Returns:
            Album metadata dict with "tracks" and "track_total" keys.

        Raises:
            DataException: If the album is not found and no redirect resolves.
        """
        return await self._albums.get_or_create(
            item_id, lambda: self._fetch_album(item_id)
        )

    async def _fetch_album(self, item_id: str) -> dict:
        """Fetch album metadata and track list; also batch-prefetches GW track data.

        Runs the two REST calls (album metadata + track list) in parallel.
        Then prefetches GW data for all tracks via song.getListByAlbum so
        subsequent per-track ``get_track()`` calls can skip the individual
        song.getData round-trip (one GW call replaces N).

        Called exclusively through ``get_album``'s ``_TaskCache``.

        Args:
            item_id: The Deezer album ID.

        Returns:
            Album metadata dict with "tracks" and "track_total" populated.

        Raises:
            DataException: If the album is not found and no redirect resolves.
        """
        try:
            album_metadata, album_tracks = await asyncio.gather(
                self._rest(self.client.api.get_album, item_id),
                self._rest(self.client.api.get_album_tracks, item_id),
            )
        except DataException:
            new_id = await self._resolve_redirect("album", item_id)
            if new_id:
                metadata = await self.get_album(new_id)
                self._albums.set(item_id, metadata)
                return metadata
            raise

        # Best-effort: batch-prefetch GW track data for the whole album at once.
        try:
            gw_tracks = await self._gw(self.client.gw.get_album_tracks, item_id)
            for gw_track in gw_tracks:
                tid = str(gw_track.get("SNG_ID", ""))
                if tid and tid not in self._gw_tracks:
                    self._gw_tracks[tid] = gw_track
        except Exception as e:
            logger.debug("GW album track prefetch failed for album %s: %s", item_id, e)

        album_metadata["tracks"] = album_tracks["data"]
        album_metadata["track_total"] = len(album_tracks["data"])
        return album_metadata

    async def _resolve_redirect(self, media_type: str, item_id: str) -> str | None:
        """Follow HTTP redirects to find the canonical ID for a moved/aliased item.

        Args:
            media_type: "album", "playlist", or "artist".
            item_id: The original Deezer item ID.

        Returns:
            The new ID if a redirect occurred, otherwise None.
        """
        url = f"https://www.deezer.com/{media_type}/{item_id}"
        try:
            async with self.session.head(url, allow_redirects=True) as response:
                final_url = str(response.url)
        except Exception as e:
            logger.warning("Failed to resolve redirect for %s: %s", item_id, e)
            return None

        if final_url == url:
            return None

        match = re.search(rf"/{media_type}/(\d+)", final_url)
        if match and (new_id := match.group(1)) != item_id:
            logger.debug("Resolved redirect for %s %s -> %s", media_type, item_id, new_id)
            return new_id

        return None

    async def get_playlist(self, item_id: str) -> dict:
        """Fetch metadata for a playlist.

        Args:
            item_id: The playlist ID, or "favorites:{user_id}" to fetch
                     a user's loved tracks as a playlist.

        Returns:
            Dict with "title", "tracks" (list of {"id": ...}), and "track_total".
        """
        if item_id.startswith("favorites:"):
            return await self.get_user_favorites(item_id[len("favorites:"):])

        try:
            pl_metadata, pl_tracks = await asyncio.gather(
                self._gw(self.client.gw.get_playlist, item_id),
                self._gw(self.client.gw.get_playlist_tracks, item_id),
            )
        except GWAPIError:
            new_id = await self._resolve_redirect("playlist", item_id)
            if new_id:
                return await self.get_playlist(new_id)
            raise

        tracks = pl_tracks if isinstance(pl_tracks, list) else pl_tracks["data"]
        return {
            "title": pl_metadata["DATA"]["TITLE"],
            "tracks": [{"id": t["SNG_ID"]} for t in tracks],
            "track_total": len(tracks),
        }

    async def get_user_favorites(self, user_id: str) -> dict:
        """Fetch the loved tracks for a Deezer user profile.

        Args:
            user_id: The Deezer user ID (numeric string).

        Returns:
            Playlist-shaped dict with "title", "tracks", and "track_total".
        """
        # deezer-py silently drops the limit arg in get_user_tracks() when it detects
        # the own profile and re-routes to get_my_favorite_tracks(). Call the latter
        # directly so the limit is always honoured.
        uid = int(user_id)
        if uid == self.logged_in_user_id:
            tracks = await self._gw(
                self.client.gw.get_my_favorite_tracks, self.max_favorites
            )
        else:
            tracks = await self._gw(
                self.client.gw.get_user_tracks, uid, self.max_favorites
            )
        return {
            "title": "Loved Tracks",
            "tracks": [{"id": str(t["id"])} for t in tracks],
            "track_total": len(tracks),
        }

    async def get_artist(self, item_id: str) -> dict:
        """Fetch metadata for an artist, including their album list.

        Args:
            item_id: The Deezer artist ID.

        Returns:
            Artist metadata dict with an "albums" key containing the album list.

        Raises:
            DataException: If the artist is not found and no redirect resolves.
        """
        try:
            artist, albums = await asyncio.gather(
                self._rest(self.client.api.get_artist, item_id),
                self._rest(self.client.api.get_artist_albums, item_id),
            )
        except DataException:
            new_id = await self._resolve_redirect("artist", item_id)
            if new_id:
                return await self.get_artist(new_id)
            raise

        artist["albums"] = albums["data"]
        return artist

    async def search(self, media_type: str, query: str, limit: int = 200) -> list[dict]:
        """Search for items on Deezer.

        Args:
            media_type: Entity type to search for (e.g. "track", "album",
                "artist"). Pass "featured" for editorial content.
            query: Search string. For "featured", matches an editorial category
                name (e.g. "releases").
            limit: Maximum number of results to return.

        Returns:
            A list containing the raw API response dict(s).
        """
        if media_type == "featured":
            try:
                fn = (
                    getattr(self.client.api, f"get_editorial_{query}")
                    if query
                    else self.client.api.get_editorial_releases
                )
            except AttributeError:
                raise Exception(f'Invalid editorical selection "{query}"')
        else:
            try:
                fn = getattr(self.client.api, f"search_{media_type}")
            except AttributeError:
                raise Exception(f"Invalid media type {media_type}")

        response = await self._rest(fn, query, limit=limit)  # type: ignore[arg-type]
        return [response] if response["total"] > 0 else []

    async def get_downloadable(
        self,
        item_id: str | None,
        quality: int = 2,
    ) -> DeezerDownloadable:
        """Resolve the download URL for a track and return a DeezerDownloadable.

        GW track info is taken from ``_gw_tracks`` when available (populated by
        ``get_track`` or the album prefetch), avoiding a redundant round-trip.

        Args:
            item_id: The Deezer track ID. None raises NonStreamableError immediately.
            quality: Desired quality level (0=MP3_128, 1=MP3_320, 2=FLAC).

        Returns:
            Ready-to-use DeezerDownloadable for the track.

        Raises:
            NonStreamableError: If no download URL can be obtained for the track.
        """
        if item_id is None:
            raise NonStreamableError(
                "No item id provided. This can happen when searching for fallback songs.",
            )

        quality = max(0, min(quality, 2))
        track_info = await self._get_gw_track(item_id)
        url, final_quality = await self._resolve_quality(track_info, quality, item_id)

        if not url:
            raise NonStreamableError(
                "Could not retrieve a download URL for track %s" % item_id
            )

        dl_info = {
            "id": item_id,
            "url": url,
            "quality": final_quality,
            "quality_to_size": [
                int(track_info.get(f"FILESIZE_{fmt}", 0))
                for _, fmt in self._QUALITY_MAP
            ],
        }
        _, format_str = self._QUALITY_MAP[final_quality]
        logger.debug(
            "Deezer track %s resolved at quality %d (%s)", item_id, final_quality, format_str
        )
        return DeezerDownloadable(self.session, dl_info)

    async def _resolve_quality(
        self,
        track_info: dict,
        quality: int,
        item_id: str,
        is_retry: bool = False,
    ) -> tuple[str | None, int]:
        """Try qualities from requested down to 0; fall back to AES CDN on exhaustion.

        Handles WrongLicense (tries next lower quality) and WrongGeolocation
        (retries once with the FALLBACK track ID if available).

        Args:
            track_info: GW track info dict (must contain TRACK_TOKEN).
            quality: Starting quality level (0-2, clamped by caller).
            item_id: Track ID, used for geoblocking retry and CDN URL construction.
            is_retry: True when already handling a geoblocking fallback to prevent loops.

        Returns:
            ``(url, effective_quality)`` — url is None only if the CDN fallback URL
            itself is empty or missing (triggers NonStreamableError in the caller).

        Raises:
            NonStreamableError: On WrongGeolocation with no fallback, on missing
                TRACK_TOKEN, or when CDN fallback fields are absent.
        """
        token = track_info.get("TRACK_TOKEN")
        if token is None:
            raise NonStreamableError(
                f"Deezer track {item_id} has no TRACK_TOKEN "
                "(possibly unavailable in your region or account)"
            )

        fallback_id = track_info.get("FALLBACK", {}).get("SNG_ID")

        for q in range(quality, -1, -1):
            _, fmt = self._QUALITY_MAP[q]
            try:
                logger.debug("Attempting quality %d (%s)", q, fmt)
                url = await asyncio.to_thread(self.client.get_track_url, token, fmt)
                if url:
                    return url, q
            except deezer.WrongLicense:
                if not self.config.lower_quality_if_not_available:
                    raise NonStreamableError(
                        f"Quality {q} is not available with your subscription "
                        "and fallback is disabled."
                    )
                logger.warning(
                    "Quality %d not available for this account, trying lower", q
                )
            except deezer.WrongGeolocation:
                if not is_retry and fallback_id:
                    logger.debug("Geoblocked; retrying with fallback ID %s", fallback_id)
                    fallback_info = await self._get_gw_track(fallback_id)
                    return await self._resolve_quality(
                        fallback_info, quality, fallback_id, is_retry=True
                    )
                raise NonStreamableError("Track geoblocked and no fallback available.")

        # All token-based attempts exhausted — try the legacy AES-encrypted CDN URL.
        md5 = track_info.get("MD5_ORIGIN")
        media_version = track_info.get("MEDIA_VERSION")
        if not md5 or not media_version:
            raise NonStreamableError(
                f"Deezer track {item_id}: token API failed and CDN fallback requires "
                "MD5_ORIGIN/MEDIA_VERSION which are missing"
            )
        return self._get_encrypted_file_url(item_id, md5, media_version), quality

    def _get_encrypted_file_url(
        self,
        meta_id: str,
        track_hash: str,
        media_version: str,
    ) -> str:
        """Build the legacy AES-ECB CDN URL used when the token API returns nothing.

        Args:
            meta_id: The track metadata ID.
            track_hash: The MD5 hash of the track origin URL (MD5_ORIGIN).
            media_version: The media version string from GW track info.

        Returns:
            Signed CDN URL pointing to the AES-encrypted audio file.
        """
        logger.debug("Falling back to encrypted file URL for track %s", meta_id)
        format_number = 1

        url_bytes = b"\xa4".join((
            track_hash.encode(),
            str(format_number).encode(),
            str(meta_id).encode(),
            str(media_version).encode(),
        ))
        url_hash = hashlib.md5(url_bytes).hexdigest()
        info_bytes = bytearray(url_hash.encode())
        info_bytes.extend(b"\xa4")
        info_bytes.extend(url_bytes)
        info_bytes.extend(b"\xa4")
        padding_len = 16 - (len(info_bytes) % 16)
        info_bytes.extend(b"." * padding_len)

        path = binascii.hexlify(
            AES.new(b"jo6aey6haid2Teih", AES.MODE_ECB).encrypt(info_bytes),
        ).decode("utf-8")
        url = f"https://e-cdns-proxy-{track_hash[0]}.dzcdn.net/mobile/1/{path}"
        logger.debug("Encrypted file path %s", url)
        return url
