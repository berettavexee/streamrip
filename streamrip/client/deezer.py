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
from .deezer_pipe import DeezerPipeClient
from .downloadable import DeezerDownloadable

logger = logging.getLogger("streamrip")
logging.captureWarnings(True)

# Suppress urllib3 per-request DEBUG lines (which expose GW session tokens in URLs).
# streamrip's own logger already records what is being fetched at a useful level.
logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)

# CD-quality audio parameters for all standard Deezer FLAC streams.
# The public REST API and GW API do not expose these values; they are fixed
# constants for Deezer's quality tier 2 (FLAC, CD quality).
_DEEZER_BIT_DEPTH: int = 16
_DEEZER_SAMPLING_RATE_KHZ: float = 44.1   # kHz — unit used by TrackMetadata.from_deezer
_DEEZER_SAMPLING_RATE_HZ: int = 44100     # Hz  — unit used by AlbumMetadata.from_deezer


def _gw_int(val: Any, default: int) -> int:
    """Convert a GW field value to int, returning default for None/empty/non-numeric."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


class _HttpsUpgradeSession(requests.Session):
    """requests.Session that rewrites http:// to https:// on every request.

    The rewrite happens in both request() and send():
    - request(): before prepare_request() selects cookies, so that Secure
      cookies from earlier HTTPS responses are included in subsequent requests.
    - send(): defensive fallback for PreparedRequests built and passed directly
      without going through request().
    """

    def request(self, method: str, url: str | bytes, **kwargs: Any) -> requests.Response:
        if isinstance(url, str) and url.startswith("http://"):
            url = "https://" + url[7:]
        return super().request(method, url, **kwargs)

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        if isinstance(request.url, str) and request.url.startswith("http://"):
            request.url = "https://" + request.url[7:]
        return super().send(request, **kwargs)

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
            self._tasks.pop(key, None)
            return result
        except BaseException:
            self._tasks.pop(key, None)
            raise

    def set(self, key: K, value: V) -> None:
        """Store a value directly, bypassing the task machinery."""
        self._results[key] = value

    def set_if_absent(self, key: K, value: V) -> None:
        """Store value only if key is not already cached; never overwrites."""
        if key not in self._results:
            self._results[key] = value


class DeezerClient(Client):
    """Deezer API client.

    All blocking deezer-py calls are offloaded to a thread pool via two
    rate-limited helpers:

    - ``_rest()`` — calls to ``api.deezer.com`` (REST, capped at 10 req/s)
    - ``_gw()``   — calls to ``gw-light.php`` (GW, no rate limit)

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
        self._gw_tracks: _TaskCache[str, dict] = _TaskCache()
        self._pipe: DeezerPipeClient | None = None
        self._url_results: dict[tuple[str, str], str] = {}
        self._url_batch_tasks: dict[str, asyncio.Task[None]] = {}
        self._url_batch_lock: asyncio.Lock = asyncio.Lock()

        # REST API (api.deezer.com) throttles beyond ~10 req/sec.
        self._rest_limiter = aiolimiter.AsyncLimiter(10, 1)

        max_conn = config.session.downloads.max_connections
        _pool_kwargs = dict(
            pool_connections=max_conn,
            pool_maxsize=max(max_conn * 4, 32),
            max_retries=0,
        )
        # Replace deezer-py's plain Session with one that upgrades http:// → https://.
        # All three objects share the same session reference.
        _session = _HttpsUpgradeSession()
        _session.mount("https://", requests.adapters.HTTPAdapter(**_pool_kwargs))
        self.client.session = _session
        self.client.api.session = _session
        self.client.gw.session = _session

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
        """Run a blocking deezer-py GW call in a thread pool.

        Note: Deezer does not raise on an expired session — the GW silently
        returns anonymous data (USER_ID=0), and the ``arl`` cookie transparently
        re-establishes the session server-side. A truly expired ARL fails at
        ``login()`` instead. So no session-renewal retry is needed here.

        Args:
            fn: Callable to invoke (e.g. ``self.client.gw.get_track``).
            *args: Positional arguments forwarded to fn.
            **kwargs: Keyword arguments forwarded to fn.

        Returns:
            Whatever fn returns.
        """
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def _get_gw_track(self, item_id: str) -> dict:
        """Return GW track info from cache, or fetch and cache it on a miss.

        Concurrent callers for the same ID share a single in-flight request via
        the ``_gw_tracks`` TaskCache; only one GW round-trip is ever made per ID.

        Args:
            item_id: Deezer track ID (coerced to str for consistent cache keying).

        Returns:
            GW track info dict containing TRACK_TOKEN, FILESIZE_*, GAIN, etc.

        Raises:
            NonStreamableError: If the GW request fails.
        """
        item_id = str(item_id)
        try:
            return await self._gw_tracks.get_or_create(
                item_id,
                lambda: self._gw(self.client.gw.get_track, item_id),
            )
        except NonStreamableError:
            raise
        except Exception as e:
            raise NonStreamableError(
                f"Could not fetch GW track info for {item_id}: {e}"
            )

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

        # Initialise the Pipe GraphQL client.  The first JWT is fetched lazily
        # on the first pipe_query() call, so login() itself stays fast.
        self._pipe = DeezerPipeClient(arl, self.session)

    async def pipe_query(
        self,
        query: str,
        variables: dict | None = None,
    ) -> dict:
        """Execute a GraphQL query against the Deezer Pipe API.

        The underlying JWT is acquired and renewed transparently by the
        :class:`~streamrip.client.deezer_pipe.DeezerPipeClient`.

        Args:
            query: GraphQL query or mutation string.
            variables: Optional variables dict.

        Returns:
            Parsed JSON response from pipe.deezer.com.

        Raises:
            RuntimeError: If :meth:`login` has not been called yet.
        """
        if self._pipe is None:
            raise RuntimeError("pipe_query called before login()")
        return await self._pipe.query(query, variables)

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
        logger.debug("Fetching Deezer %s %s", media_type, item_id)
        match media_type:
            case "track":
                return await self.get_track(item_id)
            case "album":
                return await self.get_album(item_id)
            case "playlist":
                return await self.get_playlist(item_id)
            case "artist":
                return await self.get_artist(item_id)
            case _:
                raise Exception(f"Media type {media_type} not available on deezer")

    async def _fetch_lyrics(self, item_id: str) -> str | None:
        """Fetch plain-text lyrics via the GW API.

        Args:
            item_id: Deezer track ID.

        Returns:
            Plain-text lyrics string, or None if unavailable or the request fails.
        """
        if not self.config.fetch_lyrics:
            return None
        try:
            data = await self._gw(self.client.gw.get_track_lyrics, item_id)
            return data.get("LYRICS_TEXT") or None
        except Exception:
            return None

    def _gw_to_track_dict(self, gw: dict, lyrics: str | None = None) -> dict:
        """Build a REST-track-shaped dict from cached GW data.

        Maps GW field names/formats to what TrackMetadata.from_deezer() expects,
        including the SNG_CONTRIBUTORS dict → contributors list conversion.
        """
        sng_contribs: dict = gw.get("SNG_CONTRIBUTORS", {})

        # SNG_CONTRIBUTORS: {"main_artist": ["Name"], "composer": [...], ...}
        # REST contributors: [{"name": "Name", "type": "artist"}, ...]
        _role_map = {"main_artist": "artist"}
        contributors_list: list[dict] = []
        for role, names in sng_contribs.items():
            rest_type = _role_map.get(role, role)
            for name in (names if isinstance(names, list) else [names]):
                contributors_list.append({"name": name, "type": rest_type})

        raw_bpm = gw.get("BPM")
        try:
            bpm_val = float(raw_bpm) if raw_bpm else None
            bpm: float | None = bpm_val if (bpm_val is not None and 0 < bpm_val < float("inf")) else None
        except (TypeError, ValueError):
            bpm = None

        alb_pic = gw.get("ALB_PICTURE", "")
        alb_cover_base = f"https://e-cdns-images.dzcdn.net/images/cover/{alb_pic}"

        return {
            "id": _gw_int(gw.get("SNG_ID"), 0),
            "title": gw.get("SNG_TITLE", ""),
            "isrc": str(gw.get("ISRC") or ""),
            "explicit_lyrics": bool(_gw_int(gw.get("EXPLICIT_LYRICS"), 0)),
            "track_position": _gw_int(gw.get("TRACK_NUMBER"), 0),
            "disk_number": _gw_int(gw.get("DISK_NUMBER"), 1),
            "bpm": bpm,
            "artist": {"name": gw.get("ART_NAME", "")},
            "contributors": contributors_list,
            # GW contributor sub-fields (injected identically to the slow path)
            "composer": sng_contribs.get("composer"),
            "author": sng_contribs.get("author"),
            "gain": gw.get("GAIN"),
            "lyrics": lyrics,
            "bit_depth": _DEEZER_BIT_DEPTH,
            "sampling_rate": _DEEZER_SAMPLING_RATE_KHZ,
            # Minimal album stub so get_track_for_playlist callers can build
            # AlbumMetadata without a REST round-trip (via from_incomplete_deezer_track_resp).
            # The full album dict is injected by the fetch_album=True path, overwriting this.
            "album": {
                "id": int(gw.get("ALB_ID", 0)),
                "title": gw.get("ALB_TITLE", ""),
                "release_date": gw.get("PHYSICAL_RELEASE_DATE", "0000-01-01"),
                "cover_xl": f"{alb_cover_base}/1000x1000-000000-80-0-0.jpg",
                "cover_big": f"{alb_cover_base}/500x500-000000-80-0-0.jpg",
                "cover_medium": f"{alb_cover_base}/250x250-000000-80-0-0.jpg",
                "cover_small": f"{alb_cover_base}/56x56-000000-80-0-0.jpg",
            },
        }

    async def get_track(self, item_id: str, fetch_album: bool = True) -> dict:
        """Fetch metadata for a track, optionally including its full album info.

        GW data (SNG_CONTRIBUTORS, GAIN) is fetched concurrently with the album
        call and cached in ``_gw_tracks`` for reuse by ``get_downloadable``.

        When the GW data is already cached from an album prefetch AND contains an
        ISRC field, the individual REST ``GET /track/{id}`` call is skipped and the
        response is synthesised from GW data only (fast path). This eliminates the
        per-track REST round-trip for all tracks in an album download.

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
        item_id = str(item_id)

        # Fast path: GW data already in cache (prefetched from the album batch call)
        # and complete enough to build the full track dict without a REST round-trip.
        # "ISRC" key presence (even if empty string) indicates a full GW record.
        cached_gw = self._gw_tracks._results.get(item_id)
        if cached_gw is not None and "ISRC" in cached_gw:
            lyrics = await self._fetch_lyrics(item_id)
            item = self._gw_to_track_dict(cached_gw, lyrics)
            if fetch_album:
                album_id = _gw_int(cached_gw.get("ALB_ID"), 0)
                if album_id:
                    try:
                        item["album"] = await self.get_album(str(album_id))
                    except Exception as e:
                        logger.error("Error fetching album %s for track %s: %s", album_id, item_id, e)
            return item

        # Slow path: no cached GW data or GW data lacks ISRC → REST call required.
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
        except NonStreamableError:
            raise
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

        try:
            gw_tracks = await self._gw(self.client.gw.get_album_tracks, item_id)
            for gw_track in gw_tracks:
                if tid := str(gw_track.get("SNG_ID", "")):
                    self._gw_tracks.set_if_absent(tid, gw_track)
        except Exception as e:
            logger.debug("GW album track prefetch failed for album %s: %s", item_id, e)

        album_metadata["tracks"] = album_tracks["data"]
        album_metadata["track_total"] = len(album_tracks["data"])
        album_metadata["bit_depth"] = _DEEZER_BIT_DEPTH
        album_metadata["sampling_rate"] = _DEEZER_SAMPLING_RATE_HZ
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
            item_id: The playlist ID, or "favorites:{user_id}" to fetch a user's
                loved tracks as a playlist, or "artist_top:{artist_id}" to fetch an
                artist's top tracks as a playlist.

        Returns:
            Dict with "title", "tracks" (list of {"id": ...}), and "track_total".
        """
        if item_id.startswith("favorites:"):
            return await self.get_user_favorites(item_id[len("favorites:"):])
        if item_id.startswith("artist_top:"):
            return await self.get_artist_top_tracks(item_id[len("artist_top:"):])

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
            "tracks": [{"id": str(t["SNG_ID"])} for t in tracks],
            "track_total": len(tracks),
        }

    async def get_user_favorites(self, user_id: str) -> dict:
        """Fetch the loved tracks for a Deezer user profile.

        ``song.getFavoriteIds`` is called with pagination because the server silently
        caps responses at ~25 entries per call regardless of the ``nb`` parameter.
        Track GW data is then batch-prefetched into ``_gw_tracks`` so downstream
        ``get_track`` calls hit the cache instead of issuing individual
        ``song.getData`` requests.

        ``song.getFavoriteIds`` carries no user_id parameter — it always returns the
        authenticated user's favorites.  Comparing ``uid`` against
        ``self.logged_in_user_id`` to detect "other user" is unreliable for family
        accounts: ``change_account()`` shifts ``current_user`` to a child profile
        whose id differs from the main account's USER_ID that authenticates the ARL.
        The ``user_id`` argument is accepted for API compatibility but is not used to
        route the GW call.

        Args:
            user_id: The Deezer user ID (numeric string). Accepted but not used to
                route the underlying GW call; favorites are always fetched for the
                authenticated account.

        Returns:
            Playlist-shaped dict with "title", "tracks", and "track_total".
        """
        # song.getFavoriteIds ignores nb= and caps at its own server page size (~25).
        # Advance start by the actual count returned to handle any server page size.
        page_size = 100
        all_entries: list[dict] = []
        start = 0
        while len(all_entries) < self.max_favorites:
            response = await self._gw(
                self.client.gw.get_user_favorite_ids, limit=page_size, start=start
            )
            entries: list[dict] = response.get("data", [])
            if not entries:
                break
            all_entries.extend(entries)
            start += len(entries)

        # Batch-prefetch GW track data in parallel and populate the cache.
        chunk_size = 50
        sng_ids = [int(entry["SNG_ID"]) for entry in all_entries]
        chunks = [sng_ids[i : i + chunk_size] for i in range(0, len(sng_ids), chunk_size)]
        results = await asyncio.gather(
            *[self._gw(self.client.gw.get_tracks, chunk) for chunk in chunks]
        )
        for gw_tracks in results:
            for gw_track in gw_tracks:
                if tid := str(gw_track.get("SNG_ID", "")):
                    self._gw_tracks.set_if_absent(tid, gw_track)

        return {
            "title": "Loved Tracks",
            "tracks": [{"id": str(entry["SNG_ID"])} for entry in all_entries],
            "track_total": len(all_entries),
        }

    async def get_artist_top_tracks(self, artist_id: str) -> dict:
        """Fetch the top tracks for a Deezer artist as a playlist-shaped dict.

        Args:
            artist_id: The Deezer artist ID.

        Returns:
            Playlist-shaped dict with "title", "tracks" (list of {"id": ...}),
            and "track_total".
        """
        artist, gw_tracks = await asyncio.gather(
            self._rest(self.client.api.get_artist, artist_id),
            self._gw(self.client.gw.get_artist_top_tracks, artist_id),
        )
        return {
            "title": f"{artist['name']} — Top Tracks",
            "tracks": [{"id": str(t["SNG_ID"])} for t in gw_tracks],
            "track_total": len(gw_tracks),
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
            artist = await self._rest(self.client.api.get_artist, item_id)
            albums = await self._rest(self.client.api.get_artist_albums, item_id)
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
                url: str | None = self._url_results.get((item_id, fmt))
                if url is None:
                    await self._ensure_url_batch(fmt)
                    url = self._url_results.get((item_id, fmt))
                if url is None:
                    url = await self._gw(self.client.get_track_url, token, fmt)
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

        md5 = track_info.get("MD5_ORIGIN")
        media_version = track_info.get("MEDIA_VERSION")
        if not md5 or not media_version:
            raise NonStreamableError(
                f"Deezer track {item_id}: token API failed and CDN fallback requires "
                "MD5_ORIGIN/MEDIA_VERSION which are missing"
            )
        return self._get_encrypted_file_url(item_id, md5, media_version), 2

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
        # CDN fallback always encodes FLAC; use the GW format ID from _QUALITY_MAP.
        gw_format_id = self._QUALITY_MAP[2][0]  # 1 = FLAC

        url_bytes = b"\xa4".join((
            track_hash.encode(),
            str(gw_format_id).encode(),
            meta_id.encode(),
            media_version.encode(),
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

    _BATCH_URL_CHUNK_SIZE: ClassVar[int] = 1000

    async def _batch_resolve_urls(self, fmt: str) -> None:
        """Fetch CDN URLs for all GW-cached tracks, chunked to avoid API limits.

        Collects every TRACK_TOKEN currently in ``_gw_tracks`` and calls
        ``get_tracks_url`` in chunks of at most ``_BATCH_URL_CHUNK_SIZE`` tokens.
        All failures are absorbed so the task always completes normally; individual
        ``get_track_url`` calls handle quality fallback and WrongLicense correctly.
        """
        tokens_by_id = {
            item_id: gw["TRACK_TOKEN"]
            for item_id, gw in self._gw_tracks._results.items()
            if "TRACK_TOKEN" in gw
        }
        if not tokens_by_id:
            return
        ids = list(tokens_by_id)
        tokens = [tokens_by_id[i] for i in ids]
        chunk = self._BATCH_URL_CHUNK_SIZE
        for offset in range(0, len(ids), chunk):
            chunk_ids = ids[offset : offset + chunk]
            chunk_tokens = tokens[offset : offset + chunk]
            try:
                results = await self._gw(self.client.get_tracks_url, chunk_tokens, fmt)
            except deezer.WrongLicense:
                # Format not licensed for this account — every chunk would fail the
                # same way, so stop; per-track get_track_url calls handle fallback.
                logger.debug("Batch URL prefetch: %s not licensed for this account", fmt)
                return
            except Exception as e:
                # Transient/per-chunk failure: skip this chunk but still try the rest.
                # Tracks left unresolved here fall back to individual get_track_url.
                logger.debug("Batch URL prefetch failed for %s chunk at %d: %s", fmt, offset, e)
                continue
            if not isinstance(results, list):
                logger.debug("Batch URL prefetch: unexpected result type for %s chunk at %d", fmt, offset)
                continue
            for item_id, result in zip(chunk_ids, results):
                # Only cache non-empty strings; empty string would suppress the
                # individual get_track_url fallback without providing a usable URL.
                if isinstance(result, str) and result:
                    self._url_results[(item_id, fmt)] = result

    async def _ensure_url_batch(self, fmt: str) -> None:
        """Start or await the batch URL-resolution task for *fmt*.

        The first caller for a given format creates the task; all concurrent
        callers await the same task so ``get_tracks_url`` is called exactly once
        per format per session.
        """
        async with self._url_batch_lock:
            if fmt not in self._url_batch_tasks:
                self._url_batch_tasks[fmt] = asyncio.create_task(
                    self._batch_resolve_urls(fmt)
                )
        await self._url_batch_tasks[fmt]
