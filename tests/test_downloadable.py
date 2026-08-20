"""Tests for client/downloadable.py — constructors, quality clipping, crypto helpers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from streamrip.client.downloadable import (
    BasicDownloadable,
    DeezerDownloadable,
    SoundcloudDownloadable,
    TidalDownloadable,
    discard_partial_file,
    generate_temp_path,
)
from streamrip.exceptions import NonStreamableError

# ── generate_temp_path ───────────────────────────────────────────────────────


class TestGenerateTempPath:
    def test_returns_string(self):
        path = generate_temp_path("https://example.com/track.mp3")
        assert isinstance(path, str)

    def test_contains_streamrip_prefix(self):
        path = generate_temp_path("https://example.com/track.mp3")
        assert "__streamrip_" in path

    def test_ends_with_download_extension(self):
        path = generate_temp_path("https://example.com/track.mp3")
        assert path.endswith(".download")

    def test_different_urls_give_different_paths(self):
        p1 = generate_temp_path("https://example.com/a.mp3")
        p2 = generate_temp_path("https://example.com/b.mp3")
        # hash part differs
        assert p1 != p2


# ── BasicDownloadable ────────────────────────────────────────────────────────


class TestBasicDownloadable:
    def _make(self, url="https://ex.com/a.flac", ext="flac", source="qobuz"):
        return BasicDownloadable(MagicMock(), url, ext, source)

    def test_attributes_set(self):
        d = self._make()
        assert d.url == "https://ex.com/a.flac"
        assert d.extension == "flac"
        assert d.source == "qobuz"
        assert d._size is None

    def test_default_source_unknown(self):
        d = BasicDownloadable(MagicMock(), "https://ex.com/a.mp3", "mp3")
        assert d.source == "Unknown"


# ── DeezerDownloadable ───────────────────────────────────────────────────────


def _deezer_info(quality=2, quality_to_size=None, url=None):
    if quality_to_size is None:
        quality_to_size = [3_000_000, 8_000_000, 20_000_000]  # mp3-128, mp3-320, flac
    if url is None:
        # A realistic CDN URL whose extension matches the resolved quality.
        ext = "flac" if quality >= 2 else "mp3"
        url = (
            f"https://cdnt-stream.dzcdn.net/media/1/9/a/b/c/12345/"
            f"{'0' * 32}.{ext}?hdnea=exp=1~acl=/media/*"
        )
    return {
        "quality": quality,
        "id": "12345",
        "quality_to_size": quality_to_size,
        "url": url,
    }


class TestDeezerDownloadable:
    def test_flac_extension(self):
        d = DeezerDownloadable(MagicMock(), _deezer_info(quality=2))
        assert d.extension == "flac"
        assert d.quality == 2

    def test_mp3_extension_quality_1(self):
        d = DeezerDownloadable(MagicMock(), _deezer_info(quality=1))
        assert d.extension == "mp3"

    def test_mp3_extension_quality_0(self):
        d = DeezerDownloadable(MagicMock(), _deezer_info(quality=0))
        assert d.extension == "mp3"

    def test_filesize_flac_zero_keeps_flac_extension(self):
        # FILESIZE_FLAC=0 is a missing-metadata artefact, NOT "FLAC unavailable".
        # The resolved quality is FLAC and the URL serves .flac, so the file must
        # stay .flac — the old FILESIZE clipping wrongly relabelled it .mp3,
        # producing a FLAC stream inside an MP3 container.
        info = _deezer_info(quality=2, quality_to_size=[3_000_000, 8_000_000, 0])
        d = DeezerDownloadable(MagicMock(), info)
        assert d.quality == 2
        assert d.extension == "flac"
        assert d._size is None  # unknown → read from Content-Length

    def test_extension_follows_url_over_filesize(self):
        # Even with only MP3_128 sized, a resolved FLAC quality on a .flac URL
        # stays FLAC.
        info = _deezer_info(quality=2, quality_to_size=[5_000_000, 0, 0])
        d = DeezerDownloadable(MagicMock(), info)
        assert d.quality == 2
        assert d.extension == "flac"

    def test_mp3_url_gives_mp3_extension(self):
        # When the URL actually serves MP3, the extension follows it.
        info = _deezer_info(quality=1)  # helper builds a .mp3 URL for quality 1
        d = DeezerDownloadable(MagicMock(), info)
        assert d.extension == "mp3"

    def test_legacy_mobile_url_without_extension_uses_quality(self):
        # The legacy /mobile/ CDN URL carries no extension; fall back to quality.
        info = _deezer_info(
            quality=2, url="https://e-cdns-proxy-a.dzcdn.net/mobile/1/abc"
        )
        d = DeezerDownloadable(MagicMock(), info)
        assert d.extension == "flac"

    def test_all_zero_quality_to_size_proceeds_with_none_size(self):
        # Old-catalog tracks have FILESIZE_* = 0 in GW metadata but a valid CDN URL.
        # __init__ must not raise; _size=None lets _download() read Content-Length.
        info = _deezer_info(quality=2, quality_to_size=[0, 0, 0])
        d = DeezerDownloadable(MagicMock(), info)
        assert d._size is None
        assert d.quality == 2

    def test_size_set_from_quality_to_size(self):
        info = _deezer_info(
            quality=2, quality_to_size=[3_000_000, 8_000_000, 20_000_000]
        )
        d = DeezerDownloadable(MagicMock(), info)
        assert d._size == 20_000_000

    def test_id_stored_as_str(self):
        d = DeezerDownloadable(MagicMock(), _deezer_info())
        assert d.id == "12345"


class TestDiscardPartialFile:
    def test_removes_existing_file(self, tmp_path):
        f = tmp_path / "partial.flac"
        f.write_bytes(b"truncated")
        discard_partial_file(str(f))
        assert not f.exists()

    def test_missing_file_is_not_an_error(self, tmp_path):
        discard_partial_file(str(tmp_path / "never-created.flac"))


class TestDeezerDownloadCleanup:
    """A failed download must not leave a truncated file behind: postprocess()
    would tag it and record it as downloaded, hiding the corruption forever."""

    def _response(self, chunks_side_effect):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.headers = {"Content-Length": "20000000"}
        resp.content.iter_chunks = MagicMock(side_effect=chunks_side_effect)
        resp.content.iter_chunked = MagicMock(side_effect=chunks_side_effect)
        return resp

    def _session(self, resp):
        session = MagicMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=resp)
        cm.__aexit__ = AsyncMock(return_value=False)
        session.get = MagicMock(return_value=cm)
        return session

    async def _run(self, url, path):
        def _boom(*_args, **_kwargs):
            raise RuntimeError("connection reset mid-stream")

        resp = self._response(_boom)
        d = DeezerDownloadable(self._session(resp), _deezer_info(url=url))
        # Simulate a partial file left by an interrupted write.
        path.write_bytes(b"\x00" * 1024)
        with pytest.raises(RuntimeError, match="connection reset"):
            await d._download(str(path), lambda _n: None)

    async def test_encrypted_stream_failure_removes_partial_file(self, tmp_path):
        path = tmp_path / "track.flac"
        # "/mobile/" in the path is what marks a Deezer URL as encrypted.
        await self._run("https://e-cdns-proxy-a.dzcdn.net/mobile/1/abc", path)
        assert not path.exists()

    async def test_plain_stream_failure_removes_partial_file(self, tmp_path):
        path = tmp_path / "track.flac"
        await self._run("https://cdns-proxy.dzcdn.net/stream/abc", path)
        assert not path.exists()


class TestDeezerBlowfishKey:
    def test_returns_bytes(self):
        key = DeezerDownloadable._generate_blowfish_key("77874822")
        assert isinstance(key, bytes)

    def test_deterministic(self):
        k1 = DeezerDownloadable._generate_blowfish_key("12345")
        k2 = DeezerDownloadable._generate_blowfish_key("12345")
        assert k1 == k2

    def test_different_ids_different_keys(self):
        k1 = DeezerDownloadable._generate_blowfish_key("11111")
        k2 = DeezerDownloadable._generate_blowfish_key("22222")
        assert k1 != k2

    def test_key_length_16(self):
        key = DeezerDownloadable._generate_blowfish_key("77874822")
        assert len(key) == 16


class TestDeezerDecryptChunk:
    def test_decrypt_encrypt_roundtrip(self):
        key = DeezerDownloadable._generate_blowfish_key("77874822")
        from Cryptodome.Cipher import Blowfish

        data = b"A" * 2048  # Blowfish requires multiple of 8 bytes
        encrypted = Blowfish.new(
            key, Blowfish.MODE_CBC, b"\x00\x01\x02\x03\x04\x05\x06\x07"
        ).encrypt(data)
        decrypted = DeezerDownloadable._decrypt_chunk(key, encrypted)
        assert decrypted == data


def _deezer_encrypt_reference(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt *plaintext* the way Deezer's CDN does (inverse of _download).

    Each 6144-byte (3 x 2048) segment has only its first 2048-byte block
    Blowfish-encrypted; the rest is plaintext. A trailing segment shorter than
    2048 bytes is left entirely in the clear.
    """
    from Cryptodome.Cipher import Blowfish

    segment = 3 * 2048
    out = bytearray()
    for i in range(0, len(plaintext), segment):
        block = plaintext[i : i + segment]
        if len(block) >= 2048:
            enc = Blowfish.new(
                key, Blowfish.MODE_CBC, b"\x00\x01\x02\x03\x04\x05\x06\x07"
            ).encrypt(block[:2048])
            out += enc + block[2048:]
        else:
            out += block
    return bytes(out)


class TestDeezerStreamingDecrypt:
    """The encrypted download path decrypts on the fly (bounded memory) and must
    stay bit-for-bit correct no matter how the network fragments the stream."""

    # Deliberately non-6144-aligned fragment sizes, cycled over the ciphertext,
    # so segment boundaries fall mid-chunk and exercise the carry logic.
    _WEIRD_SIZES = (1, 2047, 6143, 5, 6145, 4096, 3, 8192)

    def _fragment(self, data: bytes) -> list[bytes]:
        chunks, i, si = [], 0, 0
        while i < len(data):
            n = self._WEIRD_SIZES[si % len(self._WEIRD_SIZES)]
            chunks.append(data[i : i + n])
            i += n
            si += 1
        return chunks

    def _session_serving(self, chunks: list[bytes], size: int):
        def _factory(*_a, **_k):
            async def _agen():
                for c in chunks:
                    yield c, True

            return _agen()

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.headers = {"Content-Length": str(size)}
        resp.content.iter_chunks = MagicMock(side_effect=_factory)

        session = MagicMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=resp)
        cm.__aexit__ = AsyncMock(return_value=False)
        session.get = MagicMock(return_value=cm)
        return session

    @pytest.mark.parametrize(
        "size",
        [
            4 * 6144,  # exact multiple, no trailing segment
            3 * 6144 + 3000,  # trailing segment >= 2048 (first block encrypted)
            4 * 6144 + 1000,  # trailing segment < 2048 (entirely plaintext)
            4 * 6144 + 100,  # tiny trailing segment
            5 * 6144 + 2048,  # trailing segment exactly 2048
        ],
    )
    async def test_streamed_decrypt_matches_plaintext(self, tmp_path, size):
        import os

        plaintext = os.urandom(size)
        key = DeezerDownloadable._generate_blowfish_key("12345")
        ciphertext = _deezer_encrypt_reference(plaintext, key)

        # "/mobile/" marks the URL as encrypted; Content-Length >= 20000 avoids
        # the short-body JSON-error branch.
        url = "https://e-cdns-proxy-a.dzcdn.net/mobile/1/abc"
        session = self._session_serving(self._fragment(ciphertext), len(ciphertext))
        d = DeezerDownloadable(session, _deezer_info(url=url))

        received = 0

        def _cb(n):
            nonlocal received
            received += n

        path = tmp_path / "track.flac"
        await d._download(str(path), _cb)

        assert path.read_bytes() == plaintext
        # Progress callback still counts the raw (encrypted) bytes off the wire.
        assert received == len(ciphertext)


# ── TidalDownloadable ────────────────────────────────────────────────────────


class TestTidalDownloadable:
    def test_flac_codec_gives_flac_extension(self):
        d = TidalDownloadable(
            MagicMock(), "https://tidal.com/track.flac", "flac", None, None
        )
        assert d.extension == "flac"

    def test_mqa_codec_gives_flac_extension(self):
        d = TidalDownloadable(
            MagicMock(), "https://tidal.com/track.mqa", "MQA", None, None
        )
        assert d.extension == "flac"

    def test_aac_codec_gives_m4a_extension(self):
        d = TidalDownloadable(
            MagicMock(), "https://tidal.com/track.m4a", "AAC", None, None
        )
        assert d.extension == "m4a"

    def test_url_none_with_restrictions_raises(self):
        restrictions = [{"code": "PremiumRequired"}]
        with pytest.raises(NonStreamableError, match=r"(?i)premium"):
            TidalDownloadable(MagicMock(), None, "flac", None, restrictions)

    def test_url_none_without_restrictions_raises(self):
        with pytest.raises(NonStreamableError):
            TidalDownloadable(MagicMock(), None, "flac", None, None)

    def test_url_none_empty_restrictions_raises(self):
        with pytest.raises(NonStreamableError):
            TidalDownloadable(MagicMock(), None, "flac", None, [])

    def test_enc_key_stored(self):
        d = TidalDownloadable(
            MagicMock(), "https://tidal.com/t.flac", "FLAC", "abc123=", None
        )
        assert d.enc_key == "abc123="

    def test_no_enc_key_stored_as_none(self):
        d = TidalDownloadable(
            MagicMock(), "https://tidal.com/t.flac", "FLAC", None, None
        )
        assert d.enc_key is None


# ── SoundcloudDownloadable ───────────────────────────────────────────────────


class TestSoundcloudDownloadable:
    def test_mp3_type(self):
        d = SoundcloudDownloadable(
            MagicMock(), {"type": "mp3", "url": "https://sc.com/t.mp3"}
        )
        assert d.extension == "mp3"
        assert d.file_type == "mp3"

    def test_original_type_gives_flac(self):
        d = SoundcloudDownloadable(
            MagicMock(), {"type": "original", "url": "https://sc.com/t.flac"}
        )
        assert d.extension == "flac"

    def test_invalid_type_raises(self):
        with pytest.raises(Exception, match="Invalid file type"):
            SoundcloudDownloadable(
                MagicMock(), {"type": "wav", "url": "https://sc.com/t.wav"}
            )

    def test_source_is_soundcloud(self):
        d = SoundcloudDownloadable(
            MagicMock(), {"type": "mp3", "url": "https://sc.com/t.mp3"}
        )
        assert d.source == "soundcloud"

    def test_url_stored(self):
        url = "https://sc.com/track.mp3"
        d = SoundcloudDownloadable(MagicMock(), {"type": "mp3", "url": url})
        assert d.url == url
