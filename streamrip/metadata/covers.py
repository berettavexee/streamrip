TIDAL_COVER_URL = "https://resources.tidal.com/images/{uuid}/{width}x{height}.jpg"


class Covers:
    """Stores cover-art URLs and local file paths at up to four sizes.

    Sizes are ordered from largest to smallest: ``"original"``, ``"large"``,
    ``"small"``, ``"thumbnail"``.  Each slot is a 3-tuple
    ``(size_name, url, local_path)`` where *url* and *local_path* are ``None``
    until populated.

    Class methods :meth:`from_qobuz`, :meth:`from_deezer`,
    :meth:`from_soundcloud`, and :meth:`from_tidal` parse service-specific API
    responses into a normalised :class:`Covers` instance.
    """

    COVER_SIZES = ("thumbnail", "small", "large", "original")
    CoverEntry = tuple[str, str | None, str | None]
    _covers: list[CoverEntry]

    def __init__(self):
        # ordered from largest to smallest
        self._covers = [
            ("original", None, None),
            ("large", None, None),
            ("small", None, None),
            ("thumbnail", None, None),
        ]

    def set_cover(self, size: str, url: str | None, path: str | None):
        """Set the URL and local path for a given size.

        Args:
            size: One of ``"original"``, ``"large"``, ``"small"``,
                ``"thumbnail"``.
            url: Remote cover URL, or ``None`` if not available.
            path: Local file path after download, or ``None`` if not yet
                saved to disk.
        """
        i = self._indexof(size)
        self._covers[i] = (size, url, path)

    def set_cover_url(self, size: str, url: str):
        """Set only the remote URL for a given size (local path stays ``None``).

        Args:
            size: One of ``"original"``, ``"large"``, ``"small"``,
                ``"thumbnail"``.
            url: Remote cover URL.
        """
        self.set_cover(size, url, None)

    @staticmethod
    def _indexof(size: str) -> int:
        if size == "original":
            return 0
        if size == "large":
            return 1
        if size == "small":
            return 2
        if size == "thumbnail":
            return 3
        raise Exception(f"Invalid {size = }")

    def empty(self) -> bool:
        """Return ``True`` when no cover URL has been set at any size.

        Returns:
            ``True`` if every slot has ``url=None``.
        """
        return all(url is None for _, url, _ in self._covers)

    def set_largest_path(self, path: str):
        """Set the local path for the largest available cover URL.

        Finds the first slot (from largest to smallest) that has a URL and
        updates its local path.

        Args:
            path: Local file path where the cover image has been saved.

        Raises:
            Exception: When no cover URL is available at any size.
        """
        for size, url, _ in self._covers:
            if url is not None:
                self.set_cover(size, url, path)
                return
        raise Exception(f"No covers found in {self}")

    def set_path(self, size: str, path: str):
        """Set the local path for a specific size without changing its URL.

        Args:
            size: One of ``"original"``, ``"large"``, ``"small"``,
                ``"thumbnail"``.
            path: Local file path where the cover image has been saved.
        """
        i = self._indexof(size)
        size, url, _ = self._covers[i]
        self._covers[i] = (size, url, path)

    def largest(self) -> CoverEntry:
        """Return the entry for the largest available cover URL.

        Iterates from ``"original"`` downward and returns the first slot that
        has a URL set.

        Returns:
            A ``(size_name, url, local_path)`` 3-tuple.

        Raises:
            Exception: When no cover URL is available at any size.
        """
        for s, u, p in self._covers:
            if u is not None:
                return (s, u, p)

        raise Exception(f"No covers found in {self}")

    @classmethod
    def from_qobuz(cls, resp: dict) -> "Covers":
        """Parse a Qobuz album or track API response into a :class:`Covers`.

        Args:
            resp: Raw Qobuz API response dict containing an ``"image"`` key
                with ``"large"``, ``"small"``, and ``"thumbnail"`` sub-keys.

        Returns:
            A :class:`Covers` with ``original``, ``large``, ``small``, and
            ``thumbnail`` URLs populated.
        """
        img = resp["image"]

        c = cls()
        c.set_cover_url("original", "org".join(img["large"].rsplit("600", 1)))
        c.set_cover_url("large", img["large"])
        c.set_cover_url("small", img["small"])
        c.set_cover_url("thumbnail", img["thumbnail"])
        return c

    @classmethod
    def from_deezer(cls, resp: dict) -> "Covers":
        """Parse a Deezer album API response into a :class:`Covers`.

        Args:
            resp: Raw Deezer API response dict containing ``cover_xl``,
                ``cover_big``, ``cover_medium``, and ``cover_small`` keys.

        Returns:
            A :class:`Covers` with all four sizes populated.
        """
        c = cls()
        c.set_cover_url("original", resp["cover_xl"])
        c.set_cover_url("large", resp["cover_big"])
        c.set_cover_url("small", resp["cover_medium"])
        c.set_cover_url("thumbnail", resp["cover_small"])
        return c

    @classmethod
    def from_soundcloud(cls, resp: dict) -> "Covers":
        """Parse a SoundCloud track API response into a :class:`Covers`.

        Uses ``artwork_url`` when available and falls back to the user's
        avatar URL.  Requests the 500x500 variant of the image.

        Args:
            resp: Raw SoundCloud API response dict.

        Returns:
            A :class:`Covers` with only the ``"large"`` size populated.
        """
        c = cls()
        cover_url = (resp["artwork_url"] or resp["user"].get("avatar_url")).replace(
            "large",
            "t500x500",
        )
        c.set_cover_url("large", cover_url)
        return c

    @classmethod
    def from_tidal(cls, resp: dict) -> "Covers | None":
        """Parse a Tidal track or album API response into a :class:`Covers`.

        Args:
            resp: Raw Tidal API response dict containing a ``"cover"`` UUID
                string.

        Returns:
            A :class:`Covers` with four sizes populated (160 / 320 / 640 /
            1280 px), or ``None`` when the response carries no cover UUID.
        """
        uuid = resp["cover"]
        if not uuid:
            return None

        c = cls()
        for size_name, dimension in zip(cls.COVER_SIZES, (160, 320, 640, 1280)):
            c.set_cover_url(size_name, cls._get_tidal_cover_url(uuid, dimension))
        return c

    def get_size(self, size: str) -> CoverEntry:
        """Return the entry for *size*, falling back to smaller sizes if needed.

        Args:
            size: Requested size — one of ``"original"``, ``"large"``,
                ``"small"``, ``"thumbnail"``.

        Returns:
            The best available ``(size_name, url, local_path)`` 3-tuple at or
            below the requested size.

        Raises:
            Exception: When no URL is available at *size* or at any smaller
                size.
        """
        i = self._indexof(size)
        size, url, path = self._covers[i]
        if url is not None:
            return (size, url, path)
        if i + 1 < len(self._covers):
            for s, u, p in self._covers[i + 1 :]:
                if u is not None:
                    return (s, u, p)
        raise Exception(f"Cover not found for {size = }. Available: {self}")

    @staticmethod
    def _get_tidal_cover_url(uuid, size):
        """Generate a tidal cover url.

        :param uuid: VALID uuid string
        :param size:
        """
        possibles = (80, 160, 320, 640, 1280)
        assert size in possibles, f"size must be in {possibles}"
        return TIDAL_COVER_URL.format(
            uuid=uuid.replace("-", "/"),
            height=size,
            width=size,
        )

    def __repr__(self):
        covers = "\n".join(map(repr, self._covers))
        return f"Covers({covers})"
