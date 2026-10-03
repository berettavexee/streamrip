"""Wrapper classes over FFMPEG."""

import asyncio
import base64
import logging
import os
import shutil
import subprocess
import uuid
from tempfile import gettempdir
from typing import Final, Optional

from .exceptions import ConversionError

logger = logging.getLogger("streamrip")

SAMPLING_RATES = {44100, 48000, 88200, 96000, 176400, 192000}


def _check_libfdk_aac() -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-encoders"], capture_output=True, text=True, timeout=5
        )
        return "libfdk_aac" in result.stdout
    except Exception:
        return False


_LIBFDK_AAC_AVAILABLE = _check_libfdk_aac()


class Converter:
    """Base class for audio codecs."""

    codec_name: str
    codec_lib: str
    container: str
    lossless: bool = False
    default_ffmpeg_arg: str = ""
    # Subclasses set this to False when the muxer doesn't accept -c:v copy.
    # Art is then embedded post-conversion via mutagen instead.
    _ffmpeg_supports_art: bool = True

    def __init__(
        self,
        filename: str,
        ffmpeg_arg: Optional[str] = None,
        sampling_rate: Optional[int] = None,
        bit_depth: Optional[int] = None,
        copy_art: bool = True,
        remove_source: bool = False,
        show_progress: bool = False,
    ):
        """Create a Converter object.

        :param filename:
        :type filename: str
        :param ffmpeg_arg: The codec ffmpeg argument (defaults to an "optimal value")
        :type ffmpeg_arg: Optional[str]
        :param sampling_rate: This value is ignored if a lossy codec is detected
        :type sampling_rate: Optional[int]
        :param bit_depth: This value is ignored if a lossy codec is detected
        :type bit_depth: Optional[int]
        :param copy_art: Embed the cover art (if found) into the encoded file
        :type copy_art: bool
        :param remove_source: Remove the source file after conversion.
        :type remove_source: bool
        """
        if shutil.which("ffmpeg") is None:
            raise Exception(
                "Could not find FFMPEG executable. Install it to convert audio files.",
            )

        self.filename = filename
        self.final_fn = f"{os.path.splitext(filename)[0]}.{self.container}"
        # Unique per conversion: tracks of different albums often share a file
        # name ("01. Intro.flac"), and concurrent conversions would otherwise
        # write the same temp file and swap their audio.
        self.tempfile = os.path.join(
            gettempdir(),
            f"__streamrip_{uuid.uuid4().hex}_{os.path.basename(self.final_fn)}",
        )
        self.remove_source = remove_source
        self.sampling_rate = sampling_rate
        self.bit_depth = bit_depth
        self.copy_art = copy_art
        self.show_progress = show_progress

        if ffmpeg_arg is None:
            logger.debug("No arguments provided. Codec defaults will be used")
            self.ffmpeg_arg = self.default_ffmpeg_arg
        else:
            self.ffmpeg_arg = ffmpeg_arg
            self._is_command_valid()

        logger.debug("FFmpeg codec extra argument: %s", self.ffmpeg_arg)

    async def convert(self, custom_fn: Optional[str] = None):
        """Convert the file.

        :param custom_fn: Custom output filename (defaults to the original
        name with a replaced container)
        :type custom_fn: Optional[str]
        """
        if custom_fn:
            self.final_fn = custom_fn

        # Read cover art from the source before FFmpeg runs and before any
        # potential source deletion, so it's available for post-conversion
        # embedding even when remove_source=True.
        cover_data: tuple[bytes, str] | None = None
        if self.copy_art and not type(self)._ffmpeg_supports_art:
            cover_data = await asyncio.to_thread(self._read_source_cover)

        self.command = self._gen_command()
        logger.debug("Generated conversion command: %s", self.command)

        process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await process.communicate()
        except BaseException:
            # Cancelled (Ctrl-C): stop ffmpeg if the signal has not already,
            # and drop its partial output, which used to stay in the temp
            # directory. The source is untouched: it is only removed after a
            # successful run.
            if process.returncode is None:
                process.kill()
            if os.path.exists(self.tempfile):  # ruff: ignore[blocking-path-method-in-async-function]
                os.remove(self.tempfile)
            raise
        if process.returncode == 0 and os.path.isfile(self.tempfile):  # ruff: ignore[blocking-path-method-in-async-function]
            if self.remove_source:
                os.remove(self.filename)
                logger.debug("Source removed: %s", self.filename)

            shutil.move(self.tempfile, self.final_fn)
            logger.debug("Moved: %s -> %s", self.tempfile, self.final_fn)

            if cover_data is not None:
                await asyncio.to_thread(self._embed_cover_art, *cover_data)
        else:
            # ffmpeg can leave a partial output behind when it fails.
            if os.path.exists(self.tempfile):  # ruff: ignore[blocking-path-method-in-async-function]
                os.remove(self.tempfile)
            raise ConversionError(f"FFmpeg output:\n{out, err}")

    def _read_source_cover(self) -> tuple[bytes, str] | None:
        """Read cover art from the source file. Returns (data, mime) or None."""
        from mutagen.flac import FLAC
        from mutagen.id3 import ID3

        src_ext = os.path.splitext(self.filename)[1].lower()
        try:
            if src_ext == ".flac":
                src = FLAC(self.filename)
                if src.pictures:
                    p = src.pictures[0]
                    return p.data, p.mime
            elif src_ext == ".mp3":
                tags = ID3(self.filename)
                apic_list = tags.getall("APIC")
                if apic_list:
                    return apic_list[0].data, apic_list[0].mime
        except Exception as e:
            logger.debug("Could not read cover art from source: %s", e)
        return None

    def _embed_cover_art(self, cover_data: bytes, mime: str) -> None:
        """Embed cover art into the converted OGG/OPUS file via METADATA_BLOCK_PICTURE."""
        from mutagen.flac import Picture

        pic = Picture()
        pic.type = 3  # front cover
        pic.mime = mime
        pic.data = cover_data
        encoded = base64.b64encode(pic.write()).decode("ascii")

        out_ext = os.path.splitext(self.final_fn)[1].lower()
        try:
            if out_ext == ".ogg":
                from mutagen.oggvorbis import OggVorbis

                audio = OggVorbis(self.final_fn)
            elif out_ext == ".opus":
                from mutagen.oggopus import OggOpus

                audio = OggOpus(self.final_fn)  # type: ignore[assignment]
            else:
                return
            audio["metadata_block_picture"] = [encoded]
            audio.save()
            logger.debug("Embedded cover art via mutagen into %s", self.final_fn)
        except Exception as e:
            logger.warning("Could not embed cover art into %s: %s", self.final_fn, e)

    def _gen_command(self):
        command = [
            "ffmpeg",
            "-i",
            self.filename,
        ]

        if logger.getEffectiveLevel() != logging.DEBUG:
            command.extend(("-loglevel", "panic"))

        command.extend(("-c:a", self.codec_lib))

        if self.show_progress:
            command.append("-stats")

        supports_art = type(self)._ffmpeg_supports_art
        if self.copy_art and supports_art:
            command.extend(["-c:v", "copy"])
        elif not supports_art:
            command.append("-vn")

        if self.ffmpeg_arg:
            command.extend(self.ffmpeg_arg.split())

        if self.lossless:
            aformat = []

            if isinstance(self.sampling_rate, int):
                sample_rates = "|".join(
                    str(rate) for rate in SAMPLING_RATES if rate <= self.sampling_rate
                )
                aformat.append(f"sample_rates={sample_rates}")
            elif self.sampling_rate is not None:
                raise TypeError(
                    f"Sampling rate must be int, not {type(self.sampling_rate)}"
                )

            if isinstance(self.bit_depth, int):
                bit_depths = ["s16p", "s16"]

                if self.bit_depth in (24, 32):
                    bit_depths.extend(["s32p", "s32"])
                elif self.bit_depth != 16:
                    raise ValueError("Bit depth must be 16, 24, or 32")

                sample_fmts = "|".join(bit_depths)
                aformat.append(f"sample_fmts={sample_fmts}")
            elif self.bit_depth is not None:
                raise TypeError(f"Bit depth must be int, not {type(self.bit_depth)}")

            if aformat:
                aformat_params = ":".join(aformat)
                command.extend(["-af", f"aformat={aformat_params}"])

        # automatically overwrite
        command.extend(["-y", self.tempfile])

        logger.debug(command)

        return command

    def _is_command_valid(self):
        # TODO: add error handling for lossy codecs
        if self.ffmpeg_arg is not None and self.lossless:
            logger.debug(
                "Lossless codecs don't support extra arguments; "
                "the extra argument will be ignored",
            )
            self.ffmpeg_arg = self.default_ffmpeg_arg
            return

    @classmethod
    def get_quality_arg(cls, rate: int) -> str:
        """Translate ``[conversion] lossy_bitrate`` into this codec's ffmpeg argument.

        The base implementation ignores the rate and returns the codec default;
        lossy codecs override it.

        Args:
            rate: Target bitrate in kbps, as set in the config.

        Returns:
            The ffmpeg argument string selecting that quality.
        """
        return cls.default_ffmpeg_arg


class FLAC(Converter):
    """Class for FLAC converter."""

    codec_name = "flac"
    codec_lib = "flac"
    container = "flac"
    lossless = True


class AIFF(Converter):
    """Class for AIFF converter (lossless PCM).

    ffmpeg's AIFF muxer accepts an attached picture stream but silently drops
    it, so the cover is embedded post-conversion by :func:`tag_file` instead.
    """

    codec_name = "aiff"
    codec_lib = "pcm_s24be"
    container = "aiff"
    lossless = True


class LAME(Converter):
    """Class for libmp3lame converter.

    Default ffmpeg_arg: `-q:a 0`.

    See available options:
    https://trac.ffmpeg.org/wiki/Encode/MP3
    """

    codec_name = "lame"
    codec_lib = "libmp3lame"
    container = "mp3"
    default_ffmpeg_arg = "-q:a 0"  # V0

    # LAME's VBR presets, keyed by their average bitrate; any other rate is
    # encoded as CBR.
    _bitrate_map: Final[dict[int, str]] = {
        320: "-b:a 320k",
        245: "-q:a 0",
        225: "-q:a 1",
        190: "-q:a 2",
        175: "-q:a 3",
        165: "-q:a 4",
        130: "-q:a 5",
        115: "-q:a 6",
        100: "-q:a 7",
        85: "-q:a 8",
        65: "-q:a 9",
    }

    @classmethod
    def get_quality_arg(cls, rate: int) -> str:
        """Pick the LAME VBR preset for ``rate``, or CBR when there is none.

        Args:
            rate: Target bitrate in kbps.

        Returns:
            ``-q:a N`` for a rate matching a VBR preset, ``-b:a <rate>k`` otherwise.
        """
        return cls._bitrate_map.get(rate, f"-b:a {rate}k")


class ALAC(Converter):
    """Class for ALAC converter."""

    codec_name = "alac"
    codec_lib = "alac"
    container = "m4a"
    lossless = True


class Vorbis(Converter):
    """Class for libvorbis converter.

    Default ffmpeg_arg: `-q:a 6`.

    See available options:
    https://trac.ffmpeg.org/wiki/TheoraVorbisEncodingGuide
    """

    codec_name = "vorbis"
    codec_lib = "libvorbis"
    container = "ogg"
    # The OGG muxer doesn't support -c:v copy; art is embedded via mutagen instead.
    _ffmpeg_supports_art = False
    default_ffmpeg_arg = "-q:a 6"  # 160, aka the "high" quality profile from Spotify

    @classmethod
    def get_quality_arg(cls, rate: int) -> str:
        """Map ``rate`` onto libvorbis' quality scale (-1 to 10).

        Args:
            rate: Target bitrate in kbps.

        Returns:
            A ``-q:a`` argument whose nominal bitrate is close to ``rate``
            (q4 ≈ 128, q5 ≈ 160, q8 ≈ 256, q9 ≈ 320 kbps).
        """
        if rate <= 128:
            q = rate / 16 - 4
        elif rate <= 256:
            q = rate / 32
        else:
            q = rate / 64 + 4
        return f"-q:a {max(-1, min(10, round(q)))}"


class OPUS(Converter):
    """Class for libopus.

    Default ffmpeg_arg: `-b:a 128k`.

    See more:
    http://ffmpeg.org/ffmpeg-codecs.html#libopus-1
    """

    codec_name = "opus"
    codec_lib = "libopus"
    container = "opus"
    # The Opus muxer doesn't support -c:v copy; art is embedded via mutagen instead.
    _ffmpeg_supports_art = False
    default_ffmpeg_arg = "-b:a 128k"  # Transparent

    @classmethod
    def get_quality_arg(cls, rate: int) -> str:
        """Encode at a constant ``rate``.

        Args:
            rate: Target bitrate in kbps.

        Returns:
            ``-b:a <rate>k``.
        """
        return f"-b:a {rate}k"


class AAC(Converter):
    """Class for AAC converter.

    Uses libfdk_aac if available, falls back to the native FFmpeg aac encoder.
    Default ffmpeg_arg: `-b:a 256k`.

    See available options:
    https://trac.ffmpeg.org/wiki/Encode/AAC
    """

    codec_name = "aac"
    codec_lib = "libfdk_aac" if _LIBFDK_AAC_AVAILABLE else "aac"
    container = "m4a"
    default_ffmpeg_arg = "-b:a 256k"

    @classmethod
    def get_quality_arg(cls, rate: int) -> str:
        """Encode at a constant ``rate``.

        Args:
            rate: Target bitrate in kbps.

        Returns:
            ``-b:a <rate>k``.
        """
        return f"-b:a {rate}k"


# Containers whose audio is always lossy, by extension.
_LOSSY_EXTENSIONS: Final[dict[str, str]] = {
    "mp3": "MP3",
    "ogg": "Vorbis",
    "opus": "Opus",
}


def lossy_source_codec(path: str) -> str | None:
    """Name the lossy codec of an audio file, if it uses one.

    The extension decides for MP3, Ogg and Opus. An ``.m4a`` can hold either
    AAC (lossy) or ALAC (lossless), so its codec is read from the file.

    Args:
        path: The audio file to inspect.

    Returns:
        ``"MP3"``, ``"AAC"``, ``"Vorbis"`` or ``"Opus"`` for a lossy file;
        None for a lossless one, or when the format cannot be determined.
    """
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    if ext in _LOSSY_EXTENSIONS:
        return _LOSSY_EXTENSIONS[ext]
    if ext in ("m4a", "mp4"):
        from mutagen.mp4 import MP4

        try:
            codec = MP4(path).info.codec or ""
        except Exception as e:
            logger.debug("Could not read the codec of %s: %s", path, e)
            return None
        return None if codec.lower().startswith("alac") else "AAC"
    return None


def get(codec: str) -> type[Converter]:
    converter_classes = {
        "FLAC": FLAC,
        "ALAC": ALAC,
        "MP3": LAME,
        "OPUS": OPUS,
        "OGG": Vorbis,
        "VORBIS": Vorbis,
        "AAC": AAC,
        "M4A": AAC,
        "AIFF": AIFF,
        "AIF": AIFF,
    }
    return converter_classes[codec.upper()]
