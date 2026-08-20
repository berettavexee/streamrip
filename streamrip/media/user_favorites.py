import asyncio
import logging
from dataclasses import dataclass

from ..client import Client
from ..config import Config
from ..db import Database
from .album import PendingAlbum
from .artist import PendingArtist
from .media import DownloadStats, Media, Pending
from .track import PendingSingle

logger = logging.getLogger("streamrip")

CHUNK_SIZE = 10

_PENDING_CLS = {
    "track": PendingSingle,
    "album": PendingAlbum,
    "artist": PendingArtist,
}


@dataclass(slots=True)
class PendingUserFavorites(Pending):
    """Deferred request for a user's favorited tracks, albums, or artists.

    Attributes:
        media_type: Plural form — "tracks", "albums", or "artists".
        client: API client for the source service.
        config: Session-wide configuration.
        db: Session database for skip / failure tracking.
    """

    media_type: str
    client: Client
    config: Config
    db: Database

    async def resolve(self) -> Media | None:
        """Fetch the list of favorited IDs and build a :class:`UserFavorites`.

        Returns:
            A :class:`UserFavorites` ready to rip, or ``None`` on error or empty list.

        Raises:
            NotImplementedError: If the client source does not support favorites.
        """
        source = self.client.source
        if not hasattr(self.client, "get_user_favorite_ids"):
            raise NotImplementedError(
                f"User favorites not supported for source {source!r}"
            )

        try:
            ids = await self.client.get_user_favorite_ids(self.media_type)
        except Exception as e:
            logger.error(
                "Failed to fetch %s favorites from %s: %s", self.media_type, source, e
            )
            return None

        if not ids:
            logger.info("No %s found in %s user favorites", self.media_type, source)
            return None

        singular = self.media_type.rstrip("s")  # "tracks" → "track"
        cls = _PENDING_CLS.get(singular)
        if cls is None:
            raise NotImplementedError(f"No Pending type for {self.media_type!r}")

        pending = [cls(item_id, self.client, self.config, self.db) for item_id in ids]
        logger.info(
            "Found %d favorited %s on %s", len(pending), self.media_type, source
        )
        return UserFavorites(pending_items=pending, media_type=self.media_type)


@dataclass(slots=True)
class UserFavorites(Media):
    """A collection of favorited items downloaded one by one.

    Attributes:
        pending_items: Resolved :class:`Pending` objects for each favorited item.
        media_type: Plural form — "tracks", "albums", or "artists".
    """

    pending_items: list[Pending]
    media_type: str

    async def preprocess(self):
        pass

    async def download(self, stats: DownloadStats | None = None):
        """Resolve and rip each item in batches.

        Args:
            stats: Optional accumulator for download metrics.
        """

        async def _rip(item: Pending):
            try:
                media = await item.resolve()
                if media is not None:
                    await media.rip(stats)
            except Exception as e:
                logger.error("Error downloading favorited %s: %s", self.media_type, e)

        batches = self.batch([_rip(item) for item in self.pending_items], CHUNK_SIZE)
        for batch in batches:
            await asyncio.gather(*batch)

    async def postprocess(self):
        pass
