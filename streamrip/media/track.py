import asyncio
import logging
import os
from dataclasses import dataclass

from .. import converter
from ..client import Client, Downloadable
from ..config import Config
from ..db import Database
from ..exceptions import NonStreamableError
from ..filepath_utils import clean_filename
from ..metadata import AlbumMetadata, TrackMetadata, tag_file
from ..progress import add_title, advance_overall, get_progress_callback, remove_title
from ..utils.integrity import check_integrity
from .artwork import download_embed_cover
from .media import DownloadStats, Media, Pending
from .semaphore import global_download_semaphore

logger = logging.getLogger("streamrip")


@dataclass(slots=True)
class Track(Media):
    """A single audio track ready for download and tagging.

    Created by :class:`PendingTrack` or :class:`PendingPlaylistTrack` after
    metadata resolution.  The :meth:`rip` method drives the full lifecycle:
    path computation → download (with one retry) → tagging → optional format
    conversion → integrity check → database recording.

    Attributes:
        meta: Track and album metadata used for tagging and path formatting.
        downloadable: Encapsulates the download URL and audio format.
        config: Session-wide configuration.
        folder: Directory where the audio file will be written.
        cover_path: Path to the embedded cover image, or ``None`` when no
            artwork is available.
        db: Session database for skip / failure tracking.
        download_path: Final on-disk path; set during :meth:`preprocess`.
        is_single: ``True`` when the track is downloaded standalone (not as
            part of an album), which affects the progress-bar title.
    """

    meta: TrackMetadata
    downloadable: Downloadable
    config: Config
    folder: str
    # Is None if a cover doesn't exist for the track
    cover_path: str | None
    db: Database
    # change?
    download_path: str = ""
    is_single: bool = False

    async def rip(self, stats: DownloadStats | None = None) -> None:
        try:
            if self.config.session.cli.dry_run:
                logger.info(
                    "[DRY RUN] Would download: '%s' by '%s'",
                    self.meta.title,
                    self.meta.artist,
                )
                if stats is not None:
                    stats.record_success("")
                return
            try:
                await self.preprocess()
                await self.download()
                await self.postprocess()
                if stats is not None:
                    stats.record_success(self.download_path)
            except Exception:
                if stats is not None:
                    stats.record_failure()
                raise
        finally:
            advance_overall()

    async def preprocess(self):
        """Compute the download path and create the destination directory.

        Also registers the track title in the progress-bar header when the
        track is downloaded as a standalone single.
        """
        self._set_download_path()
        os.makedirs(self.folder, exist_ok=True)
        if self.is_single:
            add_title(self.meta.title)

    async def download(self, stats: DownloadStats | None = None):
        """Download the audio file to :attr:`download_path`.

        Acquires the global download semaphore, then attempts the download up
        to twice (one initial attempt + one retry).  On a second consecutive
        failure the track is recorded as failed in the database and the method
        returns without raising.

        Args:
            stats: Unused (kept for interface symmetry with
                :meth:`Media.download`).
        """
        async with global_download_semaphore(self.config.session.downloads):
            for attempt in range(2):
                suffix = " (retry)" if attempt else ""
                label = f"Track {self.meta.tracknumber}{suffix}"
                with get_progress_callback(
                    self.config.session.cli.progress_bars,
                    await self.downloadable.size(),
                    label,
                ) as callback:
                    try:
                        await self.downloadable.download(self.download_path, callback)
                        return
                    except Exception as e:
                        if attempt == 0:
                            logger.error(
                                f"Error downloading track '{self.meta.title}', retrying: {e}"
                            )
                        else:
                            logger.error(
                                f"Persistent error downloading track '{self.meta.title}', skipping: {e}"
                            )
                            self.db.set_failed(
                                self.downloadable.source, "track", self.meta.info.id
                            )

    async def postprocess(self):
        """Tag, convert, and record the downloaded track.

        Steps in order:

        1. Run Mutagen tagging in a thread pool (non-blocking).
        2. If conversion is enabled, convert to the configured codec and
           update :attr:`download_path` to the new extension.
        3. Run the integrity check (effective bitrate vs. quality tier).
        4. Record the track as downloaded in the database.

        Raises:
            Exception: Propagated from :func:`tag_file` or :meth:`_convert`
                on tagging or conversion failure.
        """
        if self.is_single:
            remove_title(self.meta.title)

        await tag_file(self.download_path, self.meta, self.cover_path)
        if self.config.session.conversion.enabled:
            await self._convert()

        ok, reason = await asyncio.to_thread(
            check_integrity, self.download_path, self.meta.info.quality
        )
        if not ok:
            logger.warning(
                "Integrity check failed for '%s' by '%s': %s",
                self.meta.title,
                self.meta.artist,
                reason,
            )

        self.db.set_downloaded(self.meta.info.id)

    async def _convert(self):
        c = self.config.session.conversion
        engine_class = converter.get(c.codec)
        engine = engine_class(
            filename=self.download_path,
            sampling_rate=c.sampling_rate,
            bit_depth=c.bit_depth,
            remove_source=True,  # always going to delete the old file
        )
        await engine.convert()
        self.download_path = engine.final_fn  # because the extension changed

    def _set_download_path(self):
        c = self.config.session.filepaths
        formatter = c.track_format
        track_path = clean_filename(
            self.meta.format_track_path(formatter),
            restrict=c.restrict_characters,
        )
        if c.truncate_to > 0 and len(track_path) > c.truncate_to:
            track_path = track_path[: c.truncate_to]

        self.download_path = os.path.join(
            self.folder,
            f"{track_path}.{self.downloadable.extension}",
        )


@dataclass(slots=True)
class PendingTrack(Pending):
    """A track awaiting resolution in the context of a known album.

    Used for tracks within an album download: the album metadata and cover art
    are already available, so resolution only needs to fetch track-level
    metadata and a download URL.

    Attributes:
        id: Platform-specific track identifier.
        album: Pre-resolved album metadata shared by all tracks in the album.
        client: API client for the source service.
        config: Session-wide configuration.
        folder: Destination directory (may be a per-disc sub-directory).
        db: Session database for skip / failure tracking.
        cover_path: Path to the shared embedded cover image, or ``None``.
    """

    id: str
    album: AlbumMetadata
    client: Client
    config: Config
    folder: str
    db: Database
    # cover_path is None <==> Artwork for this track doesn't exist in API
    cover_path: str | None

    async def resolve(self) -> Track | None:
        if self.db.downloaded(self.id):
            logger.info(
                f"Skipping track {self.id}. Marked as downloaded in the database.",
            )
            return None

        source = self.client.source
        try:
            resp = await self.client.get_metadata(self.id, "track")
        except NonStreamableError as e:
            logger.error(f"Track {self.id} not available for stream on {source}: {e}")
            return None

        try:
            meta = TrackMetadata.from_resp(self.album, source, resp)
        except Exception as e:
            logger.error(f"Error building track metadata for {self.id}: {e}")
            return None

        if meta is None:
            logger.error(f"Track {self.id} not available for stream on {source}")
            self.db.set_failed(source, "track", self.id)
            return None

        quality = self.config.session.get_source(source).quality
        try:
            downloadable = await self.client.get_downloadable(self.id, quality)
        except NonStreamableError as e:
            logger.error(
                f"Error getting downloadable data for track {meta.tracknumber} [{self.id}]: {e}"
            )
            return None

        downloads_config = self.config.session.downloads
        if downloads_config.disc_subdirectories and self.album.disctotal > 1:
            folder = os.path.join(self.folder, f"Disc {meta.discnumber}")
        else:
            folder = self.folder

        return Track(
            meta,
            downloadable,
            self.config,
            folder,
            self.cover_path,
            self.db,
        )


@dataclass(slots=True)
class PendingSingle(Pending):
    """Whereas PendingTrack is used in the context of an album, where the album metadata
    and cover have been resolved, PendingSingle is used when a single track is downloaded.

    This resolves the Album metadata and downloads the cover to pass to the Track class.
    """

    id: str
    client: Client
    config: Config
    db: Database

    async def resolve(self) -> Track | None:
        if self.db.downloaded(self.id):
            logger.info(
                f"Skipping track {self.id}. Marked as downloaded in the database.",
            )
            return None

        try:
            resp = await self.client.get_metadata(self.id, "track")
        except NonStreamableError as e:
            logger.error(f"Error fetching track {self.id}: {e}")
            return None
        # Patch for soundcloud
        try:
            album = AlbumMetadata.from_track_resp(resp, self.client.source)
        except Exception as e:
            logger.error(f"Error building album metadata for track {id=}: {e}")
            return None

        if album is None:
            self.db.set_failed(self.client.source, "track", self.id)
            logger.error(
                f"Cannot stream track (am) ({self.id}) on {self.client.source}",
            )
            return None

        try:
            meta = TrackMetadata.from_resp(album, self.client.source, resp)
        except Exception as e:
            logger.error(f"Error building track metadata for track {id=}: {e}")
            return None

        if meta is None:
            self.db.set_failed(self.client.source, "track", self.id)
            logger.error(
                f"Cannot stream track (tm) ({self.id}) on {self.client.source}",
            )
            return None

        config = self.config.session
        quality = getattr(config, self.client.source).quality
        assert isinstance(quality, int)
        parent = config.downloads.folder
        if config.filepaths.add_singles_to_folder:
            folder = self._format_folder(album)
        else:
            folder = parent

        os.makedirs(folder, exist_ok=True)

        embedded_cover_path, downloadable = await asyncio.gather(
            download_embed_cover(
                self.client.session, folder, album.covers,
                self.config.session.artwork, for_playlist=False,
            ),
            self.client.get_downloadable(self.id, quality),
        )
        return Track(
            meta,
            downloadable,
            self.config,
            folder,
            embedded_cover_path,
            self.db,
            is_single=True,
        )

    def _format_folder(self, meta: AlbumMetadata) -> str:
        c = self.config.session
        parent = c.downloads.folder
        formatter = c.filepaths.folder_format
        if c.downloads.source_subdirectories:
            parent = os.path.join(parent, self.client.source.capitalize())
        configured_quality = c.get_source(self.client.source).quality
        effective_quality = min(configured_quality, meta.info.quality)
        return os.path.join(parent, meta.format_folder_path(formatter, effective_quality))
