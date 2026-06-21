"""The clients that interact with the streaming service APIs."""

import asyncio
import contextlib
import logging
from abc import ABC, abstractmethod

import aiohttp
import aiolimiter

from ..utils.ssl_utils import get_aiohttp_connector_kwargs
from .downloadable import Downloadable

logger = logging.getLogger("streamrip")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:83.0) Gecko/20100101 Firefox/83.0"
)


class Client(ABC):
    """Abstract base for all streaming-service API clients.

    Each concrete subclass wraps one streaming platform (Deezer, Qobuz,
    Tidal, SoundCloud).  Callers use the interface below to authenticate,
    fetch metadata, search, and obtain download URLs without knowing which
    platform is being used.

    Class attributes that subclasses must set:
        source: Human-readable service name (e.g. ``"deezer"``), used as a
            routing key throughout the codebase.
        max_quality: Highest quality tier supported by this service (0-4).
        session: Shared :class:`aiohttp.ClientSession` created during login.
        logged_in: Whether :meth:`login` has completed successfully.
        _login_lock: An :class:`asyncio.Lock` that serialises concurrent
            login attempts so credentials are requested only once.
    """

    source: str
    max_quality: int
    session: aiohttp.ClientSession
    logged_in: bool
    _login_lock: asyncio.Lock

    @abstractmethod
    async def login(self):
        """Authenticate with the streaming service.

        Opens a shared :class:`aiohttp.ClientSession` and persists any
        credentials or tokens needed by subsequent API calls.

        Raises:
            AuthenticationError: When credentials are missing or invalid.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_metadata(self, item: str, media_type) -> dict:
        """Fetch raw metadata for a single item.

        Args:
            item: Platform-specific identifier (track ID, album ID, etc.).
            media_type: One of ``"track"``, ``"album"``, ``"artist"``,
                ``"playlist"``, ``"label"``.

        Returns:
            Raw API response dict that can be passed to the appropriate
            ``Metadata.from_resp()`` constructor.
        """
        raise NotImplementedError

    @abstractmethod
    async def search(self, media_type: str, query: str, limit: int = 500) -> list[dict]:
        """Search the service and return raw result pages.

        Args:
            media_type: Entity type to search for (e.g. ``"track"``).
            query: Search string.
            limit: Maximum number of results to fetch.

        Returns:
            A list of raw result dicts (one per page or one per result,
            depending on the platform).
        """
        raise NotImplementedError

    @abstractmethod
    async def get_downloadable(self, item: str, quality: int) -> Downloadable:
        """Resolve a download URL for the given track at the given quality.

        Args:
            item: Platform-specific track identifier.
            quality: Quality tier in [0, 4] (0 = lowest, 4 = highest).

        Returns:
            A :class:`Downloadable` that encapsulates the audio stream.

        Raises:
            NonStreamableError: When the track is unavailable at the requested
                (or any lower) quality tier.
        """
        raise NotImplementedError

    async def get_track_for_playlist(self, item_id: str) -> dict:
        """Fetch track metadata for use in a playlist context.

        Default implementation delegates to get_metadata. Override in subclasses
        to skip expensive sub-fetches that are unnecessary for playlist tracks
        (e.g. fetching the full album just to get TRACKTOTAL/GENRE tags).

        Args:
            item_id (str): The track ID on this service.

        Returns:
            dict: Track metadata dict suitable for building TrackMetadata and
                  AlbumMetadata from a track response.
        """
        return await self.get_metadata(item_id, "track")

    @staticmethod
    def get_rate_limiter(
        requests_per_min: int,
    ) -> aiolimiter.AsyncLimiter | contextlib.nullcontext:
        """Return an async rate limiter capped at *requests_per_min*.

        Args:
            requests_per_min: Maximum requests per minute.  Pass ``0`` or a
                negative value to disable rate limiting entirely.

        Returns:
            An :class:`aiolimiter.AsyncLimiter` when *requests_per_min* > 0,
            or a :class:`contextlib.nullcontext` (no-op) otherwise.
        """
        return (
            aiolimiter.AsyncLimiter(requests_per_min, 60)
            if requests_per_min > 0
            else contextlib.nullcontext()
        )

    @staticmethod
    async def get_session(
        headers: dict | None = None, verify_ssl: bool = True
    ) -> aiohttp.ClientSession:
        """Create a shared :class:`aiohttp.ClientSession` with sensible defaults.

        Uses a browser-like ``User-Agent`` header and respects the global SSL
        verification setting; if *certifi* is installed it is used as the
        certificate store.

        Args:
            headers: Additional HTTP headers merged on top of the default
                ``User-Agent``.  Pass ``None`` or ``{}`` for no extras.
            verify_ssl: Whether to verify SSL certificates.  Set to ``False``
                only when a self-signed or otherwise invalid certificate is
                expected.

        Returns:
            A new :class:`aiohttp.ClientSession` ready for use.
        """
        if headers is None:
            headers = {}

        # Get connector kwargs based on SSL verification setting
        connector_kwargs = get_aiohttp_connector_kwargs(verify_ssl=verify_ssl)
        connector = aiohttp.TCPConnector(**connector_kwargs)

        return aiohttp.ClientSession(
            headers={"User-Agent": DEFAULT_USER_AGENT} | headers,
            connector=connector,
        )
