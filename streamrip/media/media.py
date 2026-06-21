import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class DownloadStats:
    """Accumulates per-session download metrics.

    Attributes:
        tracks_downloaded: Number of tracks successfully downloaded and tagged.
        tracks_failed: Number of tracks that raised an exception during rip().
        bytes_downloaded: Total on-disk size of successfully downloaded files.
    """

    tracks_downloaded: int = field(default=0)
    tracks_failed: int = field(default=0)
    bytes_downloaded: int = field(default=0)

    def record_success(self, path: str) -> None:
        """Record a successful track download.

        Args:
            path: Absolute path to the final downloaded (and possibly converted) file.
        """
        self.tracks_downloaded += 1
        try:
            self.bytes_downloaded += os.path.getsize(path)
        except OSError:
            pass

    def record_failure(self) -> None:
        """Record a failed track download."""
        self.tracks_failed += 1


class Media(ABC):
    """Abstract base for all downloadable media objects.

    Concrete subclasses (``Track``, ``Album``, ``Playlist``, ``Artist``,
    ``Label``) implement three lifecycle phases:

    1. :meth:`preprocess` — create directories, download cover art, etc.
    2. :meth:`download` — fetch and tag audio files.
    3. :meth:`postprocess` — update the database, run format conversion, etc.

    The template method :meth:`rip` calls them in order.  ``Track`` overrides
    ``rip`` to add dry-run support and per-track progress tracking.
    """

    async def rip(self, stats: DownloadStats | None = None) -> None:
        """Execute the full preprocess → download → postprocess lifecycle.

        Args:
            stats: Optional accumulator; when provided, each phase records
                success or failure via :class:`DownloadStats`.
        """
        await self.preprocess()
        await self.download(stats)
        await self.postprocess()

    @abstractmethod
    async def preprocess(self):
        """Create directories, download cover art, etc."""
        raise NotImplementedError

    @abstractmethod
    async def download(self, stats: DownloadStats | None = None):
        """Download and tag the actual audio files in the correct directories."""
        raise NotImplementedError

    @abstractmethod
    async def postprocess(self):
        """Update database, run conversion, delete garbage files etc."""
        raise NotImplementedError

    @staticmethod
    def batch(iterable, n=1):
        """Split *iterable* into consecutive chunks of at most *n* items.

        Args:
            iterable: Any sized iterable to partition.
            n: Maximum chunk size.

        Yields:
            Successive slices of *iterable*, each of length ≤ *n*.
        """
        total = len(iterable)
        for ndx in range(0, total, n):
            yield iterable[ndx : min(ndx + n, total)]


class Pending(ABC):
    """A deferred media request whose metadata has not yet been fetched.

    Each concrete subclass (``PendingTrack``, ``PendingAlbum``, etc.) wraps
    an item ID and the credentials needed to resolve it into a downloadable
    :class:`Media` object.  Resolution is intentionally deferred to allow
    batching of API calls: the next batch can be resolved while the current
    batch is downloading.
    """

    @abstractmethod
    async def resolve(self) -> Media | None:
        """Fetch metadata and resolve into a ready-to-download :class:`Media`.

        Returns:
            A fully initialised :class:`Media` instance, or ``None`` when the
            item is already in the database, unavailable, or its metadata
            cannot be fetched.
        """
        raise NotImplementedError
