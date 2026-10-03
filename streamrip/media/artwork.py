import asyncio
import logging
import os
import shutil
import tempfile
import uuid

import aiohttp
import requests
from PIL import Image

from ..client import BasicDownloadable
from ..config import ArtworkConfig
from ..metadata import Covers

_artwork_tempdirs: set[str] = set()

logger = logging.getLogger("streamrip")

# A cover is fetched once per album or playlist track; a single dropped
# connection used to leave the track tagged without art, silently recorded as
# downloaded. Retried like the audio, with a short backoff (1 s, then 2 s).
COVER_DOWNLOAD_ATTEMPTS = 3


def _worth_retrying(e: Exception) -> bool:
    """Tell whether a failed cover download might succeed on another try.

    A 404 or 403 will not change in two seconds; a dropped connection, a
    timeout, a truncated body, a 429 or a 5xx usually does.

    Args:
        e: The exception the download raised.

    Returns:
        True for a transient network or server failure, False otherwise.
    """
    if isinstance(e, requests.HTTPError):
        status = e.response.status_code if e.response is not None else 0
        return status == 429 or status >= 500
    return isinstance(
        e,
        requests.ConnectionError
        | requests.Timeout
        | requests.exceptions.ChunkedEncodingError
        | requests.exceptions.ContentDecodingError,
    )


async def _download_cover(session: aiohttp.ClientSession, url: str, path: str) -> bool:
    """Download one cover image, retrying transient failures.

    The image is written to a temporary file next to ``path`` and moved into
    place with ``os.replace``, which is atomic. Several tasks can therefore
    target the same path -- the singles of a folder all saving ``cover.jpg``,
    tracks of one album sharing an embed cover -- without one reading a
    half-written file, or a failing one deleting another's finished image
    (``BasicDownloadable`` removes its partial file on error).

    Args:
        session: The HTTP session to download with.
        url: The image URL.
        path: Where to write the image.

    Returns:
        True once the image is at ``path``, False when the download failed for
        good: on a non-transient error (a 404), or after
        ``COVER_DOWNLOAD_ATTEMPTS`` tries. The failure is logged with the URL.
    """
    tmp = f"{path}.{uuid.uuid4().hex}.part"
    logger.debug("Downloading artwork %s -> %s", url, path)
    for attempt in range(1, COVER_DOWNLOAD_ATTEMPTS + 1):
        try:
            await BasicDownloadable(session, url, "jpg").download(tmp, lambda _: None)
            os.replace(tmp, path)
            return True
        except Exception as e:
            retry = _worth_retrying(e)
            if attempt == COVER_DOWNLOAD_ATTEMPTS or not retry:
                logger.error(
                    "Error downloading artwork %s (%s): %s",
                    url,
                    f"after {attempt} attempts" if retry else "not retried",
                    e,
                )
                return False
            delay = 2 ** (attempt - 1)
            logger.warning(
                "Error downloading artwork %s, retrying in %ds: %s", url, delay, e
            )
            await asyncio.sleep(delay)
    return False  # unreachable: the loop returns on its last attempt


def remove_artwork_tempdirs():
    """Delete the temporary embed-artwork directories created this session.

    Only directories this process created with ``mkdtemp`` are listed, so a
    folder of the user's that happens to be called ``__artwork`` is never
    touched.
    """
    logger.debug("Removing dirs %s", _artwork_tempdirs)
    for path in _artwork_tempdirs.copy():
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            pass
        _artwork_tempdirs.discard(path)


async def download_artwork(
    session: aiohttp.ClientSession,
    folder: str,
    covers: Covers,
    config: ArtworkConfig,
    for_playlist: bool,
) -> tuple[str | None, str | None]:
    """Download artwork and update passed Covers object with filepaths.

    If paths for the selected sizes already exist in `covers`, nothing will
    be downloaded.

    If `for_playlist` is set, it will not download hires cover art regardless
    of the config setting.

    Embedded artworks are put in a temporary directory of their own under
    `folder` ("__artwork_<random>"), removed by remove_artwork_tempdirs() once
    the download session is done.

    Hi-res (saved) artworks are kept in `folder` as "cover.jpg".

    Args:
    ----
        session (aiohttp.ClientSession):
        folder (str):
        covers (Covers):
        config (ArtworkConfig):
        for_playlist (bool): Set to disable saved hires covers.

    Returns:
    -------
        (path to embedded artwork, path to hires artwork). Either is None
        when that cover is disabled, unavailable, or could not be downloaded
        after COVER_DOWNLOAD_ATTEMPTS tries.
    """
    save_artwork, embed = config.save_artwork, config.embed
    if for_playlist:
        save_artwork = False

    if not (save_artwork or embed) or covers.empty():
        # No need to download anything
        return None, None

    downloadables = []

    _, l_url, saved_cover_path = covers.largest()
    if saved_cover_path is None and save_artwork:
        saved_cover_path = os.path.join(folder, "cover.jpg")
        if l_url is None:
            logger.warning("No cover URL available for saving artwork; skipping")
            saved_cover_path = None
        else:
            downloadables.append(
                ("saved", _download_cover(session, l_url, saved_cover_path))
            )

    _, embed_url, embed_cover_path = covers.get_size(config.embed_size)
    if embed_cover_path is None and embed:
        if embed_url is None:
            logger.warning("No embed cover URL available; skipping artwork embedding")
        else:
            # A directory of its own per call: playlist tracks of one album
            # used to write the same "__artwork/cover<hash>.jpg" concurrently.
            os.makedirs(folder, exist_ok=True)
            embed_dir = tempfile.mkdtemp(prefix="__artwork_", dir=folder)
            _artwork_tempdirs.add(embed_dir)
            embed_cover_path = os.path.join(embed_dir, f"cover{hash(embed_url)}.jpg")
            downloadables.append(
                ("embed", _download_cover(session, embed_url, embed_cover_path))
            )

    if len(downloadables) == 0:
        return embed_cover_path, saved_cover_path

    # Each cover succeeds or fails on its own: a hi-res cover.jpg that could
    # not be fetched no longer costs the track its embedded art, nor the other
    # way round.
    results = await asyncio.gather(*(coro for _, coro in downloadables))
    for (kind, _), ok in zip(downloadables, results, strict=True):
        if ok:
            continue
        if kind == "saved":
            saved_cover_path = None
        else:
            embed_cover_path = None

    # Update `covers` to reflect the current download state
    if save_artwork and saved_cover_path is not None:
        covers.set_largest_path(saved_cover_path)
        if config.saved_max_width > 0:
            downscale_image(saved_cover_path, config.saved_max_width)

    if embed and embed_cover_path is not None:
        covers.set_path(config.embed_size, embed_cover_path)
        if config.embed_max_width > 0:
            downscale_image(embed_cover_path, config.embed_max_width)

    return embed_cover_path, saved_cover_path


async def download_embed_cover(
    session: aiohttp.ClientSession,
    folder: str,
    covers: Covers,
    config: ArtworkConfig,
    for_playlist: bool,
) -> str | None:
    """Download and return the path of the cover to embed, discarding the hi-res path."""
    embed_path, _ = await download_artwork(
        session, folder, covers, config, for_playlist
    )
    return embed_path


def downscale_image(input_image_path: str, max_dimension: int):
    """Downscale an image in place given a maximum allowed dimension.

    Args:
    ----
        input_image_path (str): Path to image
        max_dimension (int): Maximum dimension allowed

    Returns:
    -------


    """
    # Open the image
    image = Image.open(input_image_path)

    # Get the original width and height
    width, height = image.size

    if max_dimension >= max(width, height):
        return

    # Calculate the new dimensions while maintaining the aspect ratio
    if width > height:
        new_width = max_dimension
        new_height = int(height * (max_dimension / width))
    else:
        new_height = max_dimension
        new_width = int(width * (max_dimension / height))

    # Resize the image with the new dimensions
    resized_image = image.resize((new_width, new_height))

    # Save the resized image
    resized_image.save(input_image_path)
