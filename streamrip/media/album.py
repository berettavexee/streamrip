import asyncio
import logging
import os
from dataclasses import dataclass

from .. import progress
from ..client import Client
from ..config import Config
from ..db import Database
from ..exceptions import NonStreamableError
from ..filepath_utils import clean_filepath
from ..metadata import AlbumMetadata
from ..metadata.util import get_album_track_ids
from .artwork import download_artwork
from .media import DownloadStats, Media, Pending
from .track import PendingTrack

logger = logging.getLogger("streamrip")


@dataclass(slots=True)
class Album(Media):
    """An album: an ordered list of :class:`PendingTrack` objects sharing metadata.

    Attributes:
        meta: Album-level metadata used for the progress-bar title and folder
            naming.
        tracks: Pending track objects to be resolved and downloaded
            concurrently.
        config: Session-wide configuration.
        folder: Root directory where track files will be written.
        db: Session database (passed through to each track).
    """

    meta: AlbumMetadata
    tracks: list[PendingTrack]
    config: Config
    # folder where the tracks will be downloaded
    folder: str
    db: Database

    async def preprocess(self):
        """Register the album title in the progress-bar header."""
        progress.add_title(self.meta.album)

    async def download(self, stats: DownloadStats | None = None):
        """Resolve and download all tracks concurrently.

        Advances the overall progress bar for tracks that are skipped (already
        in the database) or fail to resolve, and calls :meth:`Track.rip` for
        successfully resolved tracks.

        Args:
            stats: Optional accumulator updated with per-track success /
                failure counts and total bytes downloaded.
        """
        if self.config.session.cli.progress_bars:
            progress.set_overall(len(self.tracks))

        async def _resolve_and_download(pending: Pending):
            track = None
            try:
                track = await pending.resolve()
                if track is None:
                    progress.advance_overall()  # already in DB or skipped
                    return
                await track.rip(stats)  # advance_overall() called in rip()'s finally
            except Exception as e:
                logger.error(f"Error downloading track: {e}")
                if track is None:
                    # resolve() raised before rip() could advance
                    progress.advance_overall()

        await asyncio.gather(*[_resolve_and_download(p) for p in self.tracks])

    async def postprocess(self):
        """Remove the album title from the progress-bar header."""
        progress.remove_title(self.meta.album)


@dataclass(slots=True)
class PendingAlbum(Pending):
    """An album awaiting metadata resolution.

    Fetches the album API response, builds :class:`AlbumMetadata`, downloads
    artwork, and constructs a list of :class:`PendingTrack` objects.

    Attributes:
        id: Platform-specific album identifier.
        client: API client for the source service.
        config: Session-wide configuration.
        db: Session database for skip / failure tracking.
    """

    id: str
    client: Client
    config: Config
    db: Database

    async def resolve(self) -> Album | None:
        """Fetch album metadata and artwork, returning a ready-to-download :class:`Album`.

        Returns:
            A fully initialised :class:`Album`, or ``None`` when the album is
            unavailable or its metadata cannot be parsed.
        """
        try:
            resp = await self.client.get_metadata(self.id, "album")
        except NonStreamableError as e:
            logger.error(
                f"Album {self.id} not available to stream on {self.client.source} ({e})",
            )
            return None

        try:
            meta = AlbumMetadata.from_album_resp(resp, self.client.source)
        except Exception as e:
            logger.error(f"Error building album metadata for {id=}: {e}")
            return None

        if meta is None:
            logger.error(
                f"Album {self.id} not available to stream on {self.client.source}",
            )
            return None

        tracklist = get_album_track_ids(self.client.source, resp)
        folder = self.config.session.downloads.folder
        album_folder = self._album_folder(folder, meta)
        os.makedirs(album_folder, exist_ok=True)
        embed_cover, _ = await download_artwork(
            self.client.session,
            album_folder,
            meta.covers,
            self.config.session.artwork,
            for_playlist=False,
        )
        pending_tracks = [
            PendingTrack(
                id,
                album=meta,
                client=self.client,
                config=self.config,
                folder=album_folder,
                db=self.db,
                cover_path=embed_cover,
            )
            for id in tracklist
        ]
        logger.debug(
            "Resolved %d pending tracks for album '%s'", len(pending_tracks), meta.album
        )
        return Album(meta, pending_tracks, self.config, album_folder, self.db)

    def _album_folder(self, parent: str, meta: AlbumMetadata) -> str:
        config = self.config.session
        if config.downloads.source_subdirectories:
            parent = os.path.join(parent, self.client.source.capitalize())
        formatter = config.filepaths.folder_format
        configured_quality = config.get_source(self.client.source).quality
        effective_quality = min(configured_quality, meta.info.quality)
        folder = clean_filepath(
            meta.format_folder_path(formatter, effective_quality),
            config.filepaths.restrict_characters,
        )

        return os.path.join(parent, folder)
