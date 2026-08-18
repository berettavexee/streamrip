import asyncio
import html
import logging
import os
import re
from contextlib import ExitStack
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import aiohttp
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn

from .. import progress
from ..client import Client
from ..config import Config
from ..console import console
from ..db import Database
from ..exceptions import NonStreamableError
from ..filepath_utils import clean_filename, clean_filepath
from ..metadata import (
    AlbumMetadata,
    PlaylistMetadata,
    SearchResults,
    TrackMetadata,
)
from ..metadata.search_results import TrackSummary
from ..utils.matching import score_similarity, strip_collab
from ..utils.ssl_utils import get_aiohttp_connector_kwargs
from .artwork import download_embed_cover
from .media import DownloadStats, Media, Pending
from .track import Track

logger = logging.getLogger("streamrip")


@dataclass(slots=True)
class PendingPlaylistTrack(Pending):
    id: str
    client: Client
    config: Config
    folder: str
    playlist_name: str
    position: int
    db: Database

    async def resolve(self) -> Track | None:
        if self.db.downloaded(self.id):
            logger.info(f"Track ({self.id}) already logged in database. Skipping.")
            return None
        try:
            resp = await self.client.get_track_for_playlist(self.id)
        except NonStreamableError as e:
            logger.error(f"Could not stream track {self.id}: {e}")
            return None

        album = AlbumMetadata.from_track_resp(resp, self.client.source)
        if album is None:
            logger.error(
                f"Track ({self.id}) not available for stream on {self.client.source}",
            )
            self.db.set_failed(self.client.source, "track", self.id)
            return None
        meta = TrackMetadata.from_resp(album, self.client.source, resp)
        if meta is None:
            logger.error(
                f"Track ({self.id}) not available for stream on {self.client.source}",
            )
            self.db.set_failed(self.client.source, "track", self.id)
            return None

        quality = self.config.session.get_source(self.client.source).quality
        try:
            embedded_cover_path, downloadable = await asyncio.gather(
                download_embed_cover(
                    self.client.session, self.folder, album.covers,
                    self.config.session.artwork, for_playlist=True,
                ),
                self.client.get_downloadable(self.id, quality),
            )
        except NonStreamableError as e:
            logger.error(f"Error fetching download info for track {self.id}: {e}")
            self.db.set_failed(self.client.source, "track", self.id)
            return None

        # A geoblocked track is served from an alternate ("fallback") track whose
        # id differs from the requested one. The requested track's metadata — most
        # visibly its cover — is then unreliable (Deezer serves a placeholder or a
        # missing image for the geoblocked release), so rebuild album/meta/cover
        # from the track that was actually served.
        served_id = getattr(downloadable, "id", None)
        if served_id is not None and str(served_id) != str(self.id):
            fb = await self._resolve_fallback_metadata(str(served_id))
            if fb is not None:
                album, meta, embedded_cover_path = fb

        c = self.config.session.metadata
        if c.renumber_playlist_tracks:
            meta.tracknumber = self.position
        if c.set_playlist_to_album:
            album.album = self.playlist_name

        return Track(
            meta,
            downloadable,
            self.config,
            self.folder,
            embedded_cover_path,
            self.db,
        )

    async def _resolve_fallback_metadata(
        self, served_id: str
    ) -> tuple[AlbumMetadata, TrackMetadata, str | None] | None:
        """Rebuild album, track metadata, and cover from the actually-served track.

        Used when a geoblock made ``get_downloadable`` fall back to an alternate
        track: the served track carries the real cover and album info, whereas the
        originally requested (geoblocked) track exposes only a placeholder cover.

        Args:
            served_id: The Deezer ID of the track the download URL points to.

        Returns:
            An ``(album, meta, embedded_cover_path)`` tuple, or None if the
            fallback metadata could not be fetched (caller keeps the originals).
        """
        try:
            fb_resp = await self.client.get_track_for_playlist(served_id)
            fb_album = AlbumMetadata.from_track_resp(fb_resp, self.client.source)
            if fb_album is None:
                return None
            fb_meta = TrackMetadata.from_resp(fb_album, self.client.source, fb_resp)
            if fb_meta is None:
                return None
            cover = await download_embed_cover(
                self.client.session, self.folder, fb_album.covers,
                self.config.session.artwork, for_playlist=True,
            )
            logger.debug(
                "Track %s geoblocked; using fallback %s for metadata and cover",
                self.id, served_id,
            )
            return fb_album, fb_meta, cover
        except Exception as e:
            logger.warning(
                "Could not fetch fallback metadata for geoblocked track %s "
                "(served %s): %s — keeping original metadata",
                self.id, served_id, e,
            )
            return None


@dataclass(slots=True)
class Playlist(Media):
    name: str
    config: Config
    client: Client
    tracks: list[PendingPlaylistTrack]

    async def preprocess(self):
        progress.add_title(self.name)

    async def postprocess(self):
        progress.remove_title(self.name)

    async def download(self, stats: DownloadStats | None = None):
        track_resolve_chunk_size = 20
        batches = list(self.batch(self.tracks, track_resolve_chunk_size))

        async def safe_resolve(item: PendingPlaylistTrack) -> Track | None:
            try:
                track = await item.resolve()
                if track is None:
                    progress.advance_overall()  # already in DB or skipped
                return track
            except Exception as e:
                logger.error(f"Error resolving track {item.id}: {e}")
                progress.advance_overall()  # resolve failed
                return None

        async def resolve_batch(batch: list) -> list[Track]:
            results = await asyncio.gather(*[safe_resolve(t) for t in batch])
            return [r for r in results if r is not None]

        async def safe_rip(track: Track) -> None:
            try:
                await track.rip(stats)
            except Exception as e:
                logger.error(f"Error downloading track: {e}")

        if not batches:
            return

        if self.config.session.cli.progress_bars:
            progress.set_overall(len(self.tracks))

        resolved = await resolve_batch(batches[0])
        rip_tasks: list[asyncio.Task] = []

        for i in range(len(batches)):
            # Kick off next batch resolution concurrently with current downloads.
            next_task = (
                asyncio.create_task(resolve_batch(batches[i + 1]))
                if i + 1 < len(batches)
                else None
            )
            # Start each resolved track's download immediately — don't wait for
            # the whole batch to finish before moving on.  The global semaphore
            # caps concurrency; batch-N+1 slots can start filling as batch-N
            # tracks complete, eliminating idle time at batch boundaries.
            for track in resolved:
                rip_tasks.append(asyncio.create_task(safe_rip(track)))
            resolved = await next_task if next_task is not None else []

        await asyncio.gather(*rip_tasks)


@dataclass(slots=True)
class PendingPlaylist(Pending):
    id: str
    client: Client
    config: Config
    db: Database

    async def resolve(self) -> Playlist | None:
        try:
            resp = await self.client.get_metadata(self.id, "playlist")
        except NonStreamableError as e:
            logger.error(
                f"Playlist {self.id} not available to stream on {self.client.source} ({e})",
            )
            return None

        try:
            meta = PlaylistMetadata.from_resp(resp, self.client.source)
        except Exception as e:
            logger.error(f"Error creating playlist: {e}")
            return None
        name = meta.name
        parent = self.config.session.downloads.folder
        folder = os.path.join(parent, clean_filepath(clean_filename(name)))
        ids = meta.ids()
        # A user's loved tracks and an artist's top tracks are both resolved as
        # playlists, so capping here covers all three. Truncation happens before
        # any track is resolved, so nothing is fetched only to be discarded.
        max_tracks = self.config.session.cli.max_tracks
        if max_tracks > 0 and len(ids) > max_tracks:
            logger.info(
                "Limiting '%s' to the first %d of %d tracks (--max-tracks).",
                name, max_tracks, len(ids),
            )
            ids = ids[:max_tracks]

        tracks = [
            PendingPlaylistTrack(
                id,
                self.client,
                self.config,
                folder,
                name,
                position + 1,
                self.db,
            )
            for position, id in enumerate(ids)
        ]
        return Playlist(name, self.config, self.client, tracks)


# Last.fm inserts a two-letter locale segment when the site is browsed in a
# language other than English (https://www.last.fm/fr/user/…). Copying a URL
# from the browser therefore yields a path these patterns must tolerate, or the
# URL falls through to the HTML playlist scraper and fails on a page that is
# not a playlist. Non-capturing, so the group numbers below stay put.
_LASTFM_LOCALE = r"https://www\.last\.fm/(?:[a-z]{2}/)?"

_LASTFM_USER_LIBRARY_RE = re.compile(
    _LASTFM_LOCALE + r"user/(\w+)/library/tracks"
)
_LASTFM_LOVED_TRACKS_RE = re.compile(
    _LASTFM_LOCALE + r"user/(\w+)/loved"
)
_LASTFM_ARTIST_TRACKS_RE = re.compile(
    _LASTFM_LOCALE + r"music/([^/]+)/\+tracks"
)
_LASTFM_ARTIST_PAGE_RE = re.compile(
    _LASTFM_LOCALE + r"music/([^/]+)/?$"
)
_LASTFM_API = "https://ws.audioscrobbler.com/2.0/"


class _LastfmConfigError(Exception):
    """Raised when required Last.fm configuration (e.g. api_key) is missing."""
_LASTFM_PERIOD_MAP = {
    "LAST_7_DAYS": "7day",
    "LAST_30_DAYS": "1month",
    "LAST_90_DAYS": "3month",
    "LAST_180_DAYS": "6month",
    "LAST_365_DAYS": "12month",
    "ALL": "overall",
}
_LASTFM_PERIOD_LABEL = {
    "7day": "Last 7 Days",
    "1month": "Last Month",
    "3month": "Last 3 Months",
    "6month": "Last 6 Months",
    "12month": "Last Year",
    "overall": "All Time",
}


@dataclass(slots=True)
class PendingLastfmPlaylist(Pending):
    lastfm_url: str
    client: Client
    fallback_client: Client | None
    config: Config
    db: Database

    @dataclass(slots=True)
    class Status:
        found: int
        failed: int
        total: int

    async def resolve(self) -> Playlist | None:
        try:
            playlist_title, titles_artists = await self._parse_lastfm_playlist(
                self.lastfm_url,
            )
        except _LastfmConfigError as e:
            logger.error("%s", e)
            return None
        except Exception as e:
            logger.error("Error occurred while fetching Last.fm playlist %s: %s", self.lastfm_url, e)
            return None

        requests = []

        s = self.Status(0, 0, len(titles_artists))
        if self.config.session.cli.progress_bars:
            with Progress(
                TextColumn("{task.description}"),
                BarColumn(
                    bar_width=None,
                    style="dim magenta",
                    complete_style="magenta",
                    finished_style="magenta",
                ),
                MofNCompleteColumn(),
                console=console,
            ) as prog:
                task = prog.add_task(
                    "[magenta]Matching Last.fm tracks[/magenta]",
                    total=len(titles_artists),
                )

                def callback():
                    prog.advance(task)
                    prog.update(
                        task,
                        description=(
                            f"[magenta]Matching Last.fm tracks[/magenta]"
                            f"  [green]{s.found} found[/green]"
                            f"  [red]{s.failed} failed[/red]"
                        ),
                    )

                for title, artist, duration in titles_artists:
                    requests.append(self._make_query(title, artist, duration, s, callback))
                results: list[tuple[str | None, bool]] = await asyncio.gather(*requests)
        else:

            def callback():
                pass

            for title, artist, duration in titles_artists:
                requests.append(self._make_query(title, artist, duration, s, callback))
            results = await asyncio.gather(*requests)

        parent = self.config.session.downloads.folder
        folder = os.path.join(parent, clean_filepath(clean_filename(playlist_title)))

        pending_tracks = []
        unmatched: list[tuple[str, str]] = []
        for pos, (id, from_fallback) in enumerate(results, start=1):
            title, artist, _ = titles_artists[pos - 1]
            if id is None:
                logger.warning(
                    "Track not found on any source: '%s' by '%s'", title, artist
                )
                unmatched.append((title, artist))
                continue

            if from_fallback:
                assert self.fallback_client is not None
                client = self.fallback_client
            else:
                client = self.client

            pending_tracks.append(
                PendingPlaylistTrack(
                    id,
                    client,
                    self.config,
                    folder,
                    playlist_title,
                    pos,
                    self.db,
                ),
            )

        if unmatched:
            os.makedirs(folder, exist_ok=True)
            unmatched_path = os.path.join(folder, "unmatched.txt")
            content = "".join(f"{t} — {a}\n" for t, a in unmatched)

            def _write_unmatched():
                with open(unmatched_path, "w", encoding="utf-8") as f:
                    f.write(content)

            await asyncio.to_thread(_write_unmatched)
            logger.info(
                "Wrote %d unmatched track(s) to %s", len(unmatched), unmatched_path
            )

        return Playlist(playlist_title, self.config, self.client, pending_tracks)

    async def _make_query(
        self,
        title: str,
        artist: str,
        duration: int | None,
        search_status: Status,
        callback,
    ) -> tuple[str | None, bool]:
        """Search for a track with the main source, and use fallback source if that fails.

        The search query strips collaboration credits (e.g. "feat. X") from the
        title to improve API hit rate. Candidates are ranked by a weighted
        title+artist similarity score and the best match is returned.

        Args:
            title: Track title from the Last.fm playlist entry.
            artist: Artist name from the Last.fm playlist entry.
            duration: Expected track duration in seconds from the Last.fm API,
                or ``None`` when unavailable (e.g. HTML-scraped playlists).
            search_status: Mutable counter updated with found/failed totals.
            callback: Callable invoked after each query completes (updates the
                progress display).

        Returns:
            A 2-tuple ``(id, from_fallback)`` where ``id`` is the best-matching
            track ID (or ``None`` when nothing was found on any source) and
            ``from_fallback`` is ``True`` when the fallback client was used.
        """
        query = f"{strip_collab(title)} {artist}".strip()

        min_score = self.config.session.lastfm.min_score

        def _best_id(pages: list[dict], source: str) -> str | None:
            results: list[TrackSummary] = SearchResults.from_pages(source, "track", pages).results  # type: ignore[assignment]
            if not results:
                return None
            scored = [
                (score_similarity(title, [artist], r.name, r.artist, duration, r.duration), r)
                for r in results
            ]
            best_score, best = max(scored, key=lambda x: x[0])
            dur_info = (
                f", dur: {duration}s vs {best.duration}s"
                if duration and best.duration
                else ""
            )
            logger.debug(
                "Best match for '%s' by '%s' on %s: '%s' by '%s' (score=%.2f%s)",
                title, artist, source, best.name, best.artist, best_score, dur_info,
            )
            if best_score < min_score:
                logger.warning(
                    "Rejecting match for '%s' by '%s' on %s: "
                    "'%s' by '%s' scored %.2f (min_score=%.2f)",
                    title, artist, source, best.name, best.artist, best_score, min_score,
                )
                return None
            return best.id

        with ExitStack() as stack:
            stack.callback(callback)
            pages = await self.client.search("track", query, limit=10)
            if len(pages) > 0:
                best = _best_id(pages, self.client.source)
                if best is not None:
                    search_status.found += 1
                    return best, False

            if self.fallback_client is None:
                logger.debug(
                    "No result found for '%s' by '%s' on %s",
                    title, artist, self.client.source,
                )
                search_status.failed += 1
                return None, False

            pages = await self.fallback_client.search("track", query, limit=10)
            if len(pages) > 0:
                best = _best_id(pages, self.fallback_client.source)
                if best is not None:
                    logger.debug(
                        "Found result for '%s' by '%s' on fallback source %s",
                        title, artist, self.fallback_client.source,
                    )
                    search_status.found += 1
                    return best, True

            logger.debug(
                "No result found for '%s' by '%s' on primary source %s or fallback source %s",
                title, artist, self.client.source, self.fallback_client.source,
            )
            search_status.failed += 1
        return None, False

    async def _parse_lastfm_playlist(
        self, playlist_url: str
    ) -> tuple[str, list[tuple[str, str, int | None]]]:
        """Dispatch to the appropriate parser based on the Last.fm URL type.

        Args:
            playlist_url: A Last.fm URL — playlist, user library, or artist tracks.

        Returns:
            A 2-tuple of (playlist_title, [(track_title, artist_name, duration_s), ...])
            where ``duration_s`` is the track duration in seconds, or ``None`` when
            unavailable (e.g. HTML-scraped playlists).
        """
        if _LASTFM_USER_LIBRARY_RE.match(playlist_url):
            return await self._parse_lastfm_user_top_tracks(playlist_url)
        if _LASTFM_LOVED_TRACKS_RE.match(playlist_url):
            return await self._parse_lastfm_loved_tracks(playlist_url)
        if _LASTFM_ARTIST_TRACKS_RE.match(playlist_url):
            return await self._parse_lastfm_artist_top_tracks(playlist_url)
        m = _LASTFM_ARTIST_PAGE_RE.match(playlist_url)
        if m:
            artist = m.group(1)
            raise ValueError(
                f"'{playlist_url}' is an artist page, not a track list. "
                f"Use '{playlist_url}/+tracks' to download {artist}'s top tracks."
            )
        return await self._parse_lastfm_playlist_html(playlist_url)

    def _require_api_key(self, url: str) -> str:
        """Return the configured Last.fm API key, or raise a descriptive error.

        Args:
            url: The URL being processed (included in the error message).

        Returns:
            The non-empty API key string.

        Raises:
            Exception: When api_key is not set in the [lastfm] config section.
        """
        key = self.config.session.lastfm.api_key
        if not key:
            raise _LastfmConfigError(
                f"A Last.fm API key is required to use {url}\n"
                "Register a free key at https://www.last.fm/api/account/create "
                "and set api_key in the [lastfm] section of your config."
            )
        return key

    async def _fetch_lastfm_api(
        self,
        session: aiohttp.ClientSession,
        params: dict,
    ) -> dict:
        """Perform a single Last.fm API call and return the parsed JSON.

        Args:
            session: An active aiohttp session to reuse.
            params: Query parameters for the API call (method, api_key, etc.).

        Returns:
            Parsed JSON response as a dict.

        Raises:
            Exception: On non-200 HTTP status or an API-level error response.
        """
        async with session.get(_LASTFM_API, params=params) as resp:
            if resp.status != 200:
                raise Exception(
                    f"Last.fm API returned HTTP {resp.status} for method={params.get('method')}"
                )
            data = await resp.json()
        if "error" in data:
            raise Exception(
                f"Last.fm API error {data['error']}: {data.get('message', '')}"
            )
        return data

    async def _fetch_lastfm_paginated(
        self,
        *,
        method: str,
        root_key: str,
        params: dict,
        page_size: int,
        extract_duration: bool = True,
    ) -> list[tuple[str, str, int | None]]:
        """Run a paginated Last.fm API query and collect track title/artist/duration.

        Shared pagination loop for the user-top-tracks, loved-tracks, and
        artist-top-tracks parsers: it opens a dedicated session, walks pages
        until ``totalPages`` is reached, and stops early once the configured
        ``max_tracks`` limit is hit.

        Args:
            method: Last.fm API method name (e.g. ``"user.getTopTracks"``).
            root_key: Top-level key wrapping the results in the JSON response
                (``"toptracks"`` or ``"lovedtracks"``).
            params: Method-specific query parameters (e.g. ``user``/``artist``,
                ``api_key``, ``period``). The ``method``, ``format``, ``limit``,
                and ``page`` parameters are added automatically; ``api_key`` must
                already be validated by the caller.
            page_size: Number of entries to request per page.
            extract_duration: When True, read each track's ``duration`` field
                (seconds); when False, every duration is ``None`` (the loved
                tracks endpoint does not expose durations).

        Returns:
            A list of ``(track_title, artist_name, duration_s)`` tuples, where
            ``duration_s`` is ``None`` when unavailable.

        Raises:
            Exception: If the Last.fm API returns an error (via
                :meth:`_fetch_lastfm_api`).
        """
        max_tracks = self.config.session.lastfm.max_tracks
        verify_ssl = getattr(self.config.session.downloads, "verify_ssl", True)
        connector = aiohttp.TCPConnector(**get_aiohttp_connector_kwargs(verify_ssl=verify_ssl))
        tracks: list[tuple[str, str, int | None]] = []
        page = 1

        async with aiohttp.ClientSession(connector=connector) as session:
            while True:
                data = await self._fetch_lastfm_api(session, {
                    "method": method,
                    "format": "json",
                    "limit": page_size,
                    "page": page,
                    **params,
                })
                root = data[root_key]
                total_pages = int(root["@attr"]["totalPages"])
                for track in root.get("track", []):
                    dur: int | None = None
                    if extract_duration:
                        try:
                            dur = int(track.get("duration") or 0) or None
                        except (TypeError, ValueError):
                            dur = None
                    tracks.append((track["name"], track["artist"]["name"], dur))
                    if max_tracks > 0 and len(tracks) >= max_tracks:
                        break
                if page >= total_pages or (max_tracks > 0 and len(tracks) >= max_tracks):
                    break
                page += 1

        return tracks

    async def _parse_lastfm_user_top_tracks(
        self, url: str
    ) -> tuple[str, list[tuple[str, str, int | None]]]:
        """Fetch a user's top tracks from the Last.fm API.

        Args:
            url: A URL of the form
                ``https://www.last.fm/user/{username}/library/tracks``
                with an optional ``?date_preset=LAST_7_DAYS`` query parameter.

        Returns:
            A 2-tuple of (playlist_title, [(track_title, artist_name), ...]).

        Raises:
            Exception: If the URL cannot be parsed, the API key is missing, or
                the Last.fm API returns an error.
        """
        api_key = self._require_api_key(url)
        match = _LASTFM_USER_LIBRARY_RE.match(url)
        if match is None:
            raise Exception(f"Could not parse user library URL: {url}")
        username = match.group(1)

        date_preset = parse_qs(urlparse(url).query).get("date_preset", ["ALL"])[0]
        period = _LASTFM_PERIOD_MAP.get(date_preset, "overall")
        label = _LASTFM_PERIOD_LABEL.get(period, "All Time")
        playlist_title = f"{username}'s Top Tracks ({label})"

        max_tracks = self.config.session.lastfm.max_tracks
        page_size = min(max_tracks, 200) if max_tracks > 0 else 200
        limit_str = str(max_tracks) if max_tracks > 0 else "all"
        logger.info(
            "Last.fm user library: %s — period: %s — limit: %s tracks",
            username, label, limit_str,
        )

        tracks = await self._fetch_lastfm_paginated(
            method="user.getTopTracks",
            root_key="toptracks",
            params={"user": username, "api_key": api_key, "period": period},
            page_size=page_size,
        )

        logger.debug(
            "Fetched %d tracks for user '%s' (%s) from Last.fm API",
            len(tracks), username, period,
        )
        return playlist_title, tracks

    async def _parse_lastfm_loved_tracks(
        self, url: str
    ) -> tuple[str, list[tuple[str, str, int | None]]]:
        """Fetch a user's loved tracks from the Last.fm API.

        Loved tracks are returned in reverse chronological order (most recently
        loved first).  The ``user.getLovedTracks`` endpoint does not expose
        track duration, so every entry carries ``duration=None``.

        Args:
            url: A URL of the form ``https://www.last.fm/user/{username}/loved``.

        Returns:
            A 2-tuple of (playlist_title, [(track_title, artist_name, None), ...]).

        Raises:
            Exception: If the URL cannot be parsed, the API key is missing, or
                the Last.fm API returns an error.
        """
        api_key = self._require_api_key(url)
        match = _LASTFM_LOVED_TRACKS_RE.match(url)
        if match is None:
            raise Exception(f"Could not parse loved tracks URL: {url}")
        username = match.group(1)

        playlist_title = f"{username}'s Loved Tracks"

        max_tracks = self.config.session.lastfm.max_tracks
        page_size = min(max_tracks, 200) if max_tracks > 0 else 200
        limit_str = str(max_tracks) if max_tracks > 0 else "all"
        logger.info(
            "Last.fm loved tracks: %s — limit: %s tracks",
            username, limit_str,
        )

        tracks = await self._fetch_lastfm_paginated(
            method="user.getLovedTracks",
            root_key="lovedtracks",
            params={"user": username, "api_key": api_key},
            page_size=page_size,
            extract_duration=False,
        )

        logger.debug(
            "Fetched %d loved tracks for user '%s' from Last.fm API",
            len(tracks), username,
        )
        return playlist_title, tracks

    async def _parse_lastfm_artist_top_tracks(
        self, url: str
    ) -> tuple[str, list[tuple[str, str, int | None]]]:
        """Fetch an artist's all-time top tracks from the Last.fm API.

        The ``date_preset`` query parameter present on the website URL is not
        supported by the public ``artist.getTopTracks`` API endpoint; when it
        is present and not ``ALL``, a warning is logged and all-time results
        are returned instead.

        Args:
            url: A URL of the form
                ``https://www.last.fm/music/{Artist}/+tracks``
                with an optional ``?date_preset=…`` query parameter.

        Returns:
            A 2-tuple of (playlist_title, [(track_title, artist_name), ...]).

        Raises:
            Exception: If the URL cannot be parsed, the API key is missing, or
                the Last.fm API returns an error.
        """
        api_key = self._require_api_key(url)
        match = _LASTFM_ARTIST_TRACKS_RE.match(url)
        if match is None:
            raise Exception(f"Could not parse artist tracks URL: {url}")
        artist_name = match.group(1).replace("+", " ")

        date_preset = parse_qs(urlparse(url).query).get("date_preset", ["ALL"])[0]
        if date_preset not in ("ALL", ""):
            logger.warning(
                "Last.fm artist.getTopTracks does not support date_preset=%s; "
                "returning all-time top tracks instead.",
                date_preset,
            )

        playlist_title = f"{artist_name} — Top Tracks"

        max_tracks = self.config.session.lastfm.max_tracks
        page_size = min(max_tracks, 50) if max_tracks > 0 else 50

        tracks = await self._fetch_lastfm_paginated(
            method="artist.getTopTracks",
            root_key="toptracks",
            params={"artist": artist_name, "api_key": api_key},
            page_size=page_size,
        )

        logger.debug(
            "Fetched %d tracks for artist '%s' from Last.fm API",
            len(tracks), artist_name,
        )
        return playlist_title, tracks

    async def _parse_lastfm_playlist_html(
        self,
        playlist_url: str,
    ) -> tuple[str, list[tuple[str, str, int | None]]]:
        """From a last.fm url, return the playlist title, and a list of
        track titles, artist names, and durations.

        Duration is not available from the HTML page and is always ``None``.
        Each page contains 50 results, so `num_tracks // 50 + 1` requests
        are sent per playlist.

        :param url:
        :type url: str
        :rtype: tuple[str, list[tuple[str, str, int | None]]]
        """
        logger.debug("Fetching lastfm playlist")

        title_tags = re.compile(r'<a\s+href="[^"]+"\s+title="([^"]+)"')
        re_total_tracks = re.compile(r'data-playlisting-entry-count="(\d+)"')
        re_playlist_title_match = re.compile(
            r'<h1 class="playlisting-playlist-header-title">([^<]+)</h1>',
        )

        def find_title_artist_pairs(page_text):
            info: list[tuple[str, str, int | None]] = []
            titles = title_tags.findall(page_text)  # [2:]
            for i in range(0, len(titles) - 1, 2):
                info.append((html.unescape(titles[i]), html.unescape(titles[i + 1]), None))
            return info

        async def fetch(session: aiohttp.ClientSession, url, **kwargs):
            async with session.get(url, **kwargs) as resp:
                if resp.status == 404:
                    raise Exception(f"Last.fm playlist not found (HTTP 404): {url}")
                if resp.status != 200:
                    raise Exception(
                        f"Last.fm returned HTTP {resp.status} for {url}"
                    )
                return await resp.text("utf-8")

        # Create new session so we're not bound by rate limit
        verify_ssl = getattr(self.config.session.downloads, "verify_ssl", True)
        connector_kwargs = get_aiohttp_connector_kwargs(verify_ssl=verify_ssl)
        connector = aiohttp.TCPConnector(**connector_kwargs)

        async with aiohttp.ClientSession(connector=connector) as session:
            page = await fetch(session, playlist_url)
            playlist_title_match = re_playlist_title_match.search(page)
            if playlist_title_match is None:
                raise Exception(
                    f"Could not find playlist title in Last.fm response for {playlist_url}. "
                    "The page may be an error page or Last.fm's HTML structure may have changed."
                )

            playlist_title: str = html.unescape(playlist_title_match.group(1))

            title_artist_pairs: list[tuple[str, str, int | None]] = find_title_artist_pairs(page)

            total_tracks_match = re_total_tracks.search(page)
            if total_tracks_match is None:
                raise Exception(
                    f"Could not find track count in Last.fm response for {playlist_url}. "
                    "The page structure may have changed."
                )
            total_tracks = int(total_tracks_match.group(1))

            if not title_artist_pairs:
                logger.warning(
                    "Last.fm playlist '%s' was parsed successfully but contains no tracks "
                    "(total declared: %d). The page structure may have changed.",
                    playlist_title, total_tracks,
                )

            remaining_tracks = total_tracks - 50  # already got 50 from 1st page
            if remaining_tracks <= 0:
                return playlist_title, title_artist_pairs

            last_page = (
                1 + int(remaining_tracks // 50) + int(remaining_tracks % 50 != 0)
            )
            requests = []
            for page in range(2, last_page + 1):
                requests.append(fetch(session, playlist_url, params={"page": page}))
            results = await asyncio.gather(*requests)

        for page in results:
            title_artist_pairs.extend(find_title_artist_pairs(page))

        return playlist_title, title_artist_pairs

