import asyncio
import logging
import os
import time
from dataclasses import dataclass

from .. import converter
from ..client import Client, Downloadable
from ..config import Config
from ..db import Database
from ..exceptions import NonStreamableError
from ..filepath_utils import clean_filename
from ..metadata import (
    TAGGABLE_EXTENSIONS,
    AlbumMetadata,
    TrackMetadata,
    tag_file,
)
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
        _queue_wait: Seconds spent waiting for a download-semaphore slot; set
            by :meth:`download` and reported in the phase-timing debug log.
        _download_time: Seconds spent actually transferring the file (all
            attempts, excluding queue wait); set by :meth:`download`.
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
    _queue_wait: float = 0.0
    _download_time: float = 0.0

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
                t0 = time.monotonic()
                await self.postprocess()
                logger.debug(
                    "Phase timing for '%s': queue wait %.2fs, download %.2fs, "
                    "postprocess %.2fs",
                    self.meta.title,
                    self._queue_wait,
                    self._download_time,
                    time.monotonic() - t0,
                )
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
        failure the track is recorded as failed in the database and the error
        is raised, so that :meth:`postprocess` never runs on a missing or
        truncated file.

        Sets :attr:`_queue_wait` (time blocked on the semaphore) and, on
        success, :attr:`_download_time` (transfer time across all attempts),
        so the phase-timing debug log can separate contention from bandwidth.

        Args:
            stats: Unused (kept for interface symmetry with
                :meth:`Media.download`).

        Raises:
            NonStreamableError: When both attempts fail.
        """
        t0 = time.monotonic()
        async with global_download_semaphore(self.config.session.downloads):
            self._queue_wait = time.monotonic() - t0
            t0 = time.monotonic()
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
                        self._download_time = time.monotonic() - t0
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
                            self._discard_partial_download()
                            # postprocess() normally clears the header, but the
                            # raise below skips it — the title would otherwise
                            # sit in the progress display for the whole run.
                            if self.is_single:
                                remove_title(self.meta.title)
                            raise NonStreamableError(
                                f"Failed to download '{self.meta.title}' after 2 attempts: {e}"
                            ) from e

    def _discard_partial_download(self) -> None:
        """Delete any file left at the download path after a failed download.

        A backstop, not the primary cleanup: every ``Downloadable._download``
        already calls ``discard_partial_file`` on error, so this should find
        nothing. It guards against a future download path forgetting to —
        the file sits at its final location in the library, where the tagging
        and integrity stages would happily pick it up. Unlike an integrity
        failure, where the file is kept for inspection because it looks
        complete, there is nothing to inspect here.

        Failure to delete is logged and swallowed: the download error that
        triggered this is the one worth propagating.
        """
        try:
            if os.path.isfile(self.download_path):
                os.remove(self.download_path)
                logger.debug("Removed partial download: %s", self.download_path)
        except OSError as e:
            logger.warning(
                "Could not remove partial download %s: %s", self.download_path, e
            )

    async def postprocess(self):
        """Tag, convert, and record the downloaded track.

        Steps in order:

        1. Run Mutagen tagging in a thread pool (non-blocking).
        2. If conversion is enabled, convert to the configured codec and
           update :attr:`download_path` to the new extension.
        3. Run the integrity check (effective bitrate vs. quality tier).
        4. Record the track as downloaded in the database.

        A file that fails the integrity check is recorded as *failed*, not as
        downloaded: marking it as downloaded would make the database skip it on
        every subsequent run, leaving a truncated file in the library forever.
        The file itself is kept on disk so it can be inspected; the next run
        overwrites it.

        Raises:
            NonStreamableError: When the downloaded file fails the integrity check.
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
            logger.error(
                "Integrity check failed for '%s' by '%s': %s",
                self.meta.title,
                self.meta.artist,
                reason,
            )
            self.db.set_failed(self.downloadable.source, "track", self.meta.info.id)
            raise NonStreamableError(
                f"Integrity check failed for '{self.meta.title}': {reason}"
            )

        self.db.set_downloaded(self.meta.info.id)

    async def _convert(self):
        """Convert the downloaded file to the configured codec and re-tag it.

        :attr:`download_path` is updated to the converted file, whose
        extension differs from the source's.

        The file is tagged again afterwards because ffmpeg does not reliably
        carry every field across a container change, and writes no tags at all
        for some containers (AIFF among them). Only the containers
        :func:`tag_file` knows how to write are re-tagged; the others (opus,
        ogg, ...) keep whatever ffmpeg copied over, since calling
        :func:`tag_file` on them would raise.

        Raises:
            Exception: Propagated from the converter when ffmpeg fails, or
                from :func:`tag_file` when re-tagging fails.
        """
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

        ext = os.path.splitext(self.download_path)[1].lstrip(".").lower()
        if ext in TAGGABLE_EXTENSIONS:
            await tag_file(self.download_path, self.meta, self.cover_path)

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
        # Every failure below is recorded, not merely logged. A track that dies
        # here produces no file and no downloads row, so without a failed row
        # it leaves no trace at all: it simply goes missing from the album, and
        # `rip repair` has nothing to retry.
        try:
            resp = await self.client.get_metadata(self.id, "track")
        except NonStreamableError as e:
            logger.error(f"Track {self.id} not available for stream on {source}: {e}")
            self.db.set_failed(source, "track", self.id)
            return None

        try:
            meta = TrackMetadata.from_resp(self.album, source, resp)
        except Exception as e:
            logger.error(f"Error building track metadata for {self.id}: {e}")
            self.db.set_failed(source, "track", self.id)
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
            self.db.set_failed(source, "track", self.id)
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

        # As in PendingTrack.resolve: record every failure so a track that dies
        # here stays retryable by `rip repair` instead of vanishing.
        try:
            resp = await self.client.get_metadata(self.id, "track")
        except NonStreamableError as e:
            logger.error(f"Error fetching track {self.id}: {e}")
            self.db.set_failed(self.client.source, "track", self.id)
            return None
        # Patch for soundcloud
        try:
            album = AlbumMetadata.from_track_resp(resp, self.client.source)
        except Exception as e:
            logger.error(f"Error building album metadata for track {id=}: {e}")
            self.db.set_failed(self.client.source, "track", self.id)
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
            self.db.set_failed(self.client.source, "track", self.id)
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
                self.client.session,
                folder,
                album.covers,
                self.config.session.artwork,
                for_playlist=False,
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
        return os.path.join(
            parent, meta.format_folder_path(formatter, effective_quality)
        )
