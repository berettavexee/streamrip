# Changes from upstream

All fixes and improvements present in this fork on top of [`nathom/streamrip:dev`](https://github.com/nathom/streamrip/tree/dev).

## Deezer

- Support for `link.deezer.com/s/` short URLs ([#887](https://github.com/nathom/streamrip/pull/887))
- Download liked tracks from a user profile URL (`/profile/USER_ID/loved`); all loved tracks are fetched regardless of library size — the `song.getFavoriteIds` API silently caps each page at ~100 entries and the client now paginates until the server returns an empty page; family/child accounts are handled correctly (the UID comparison that previously blocked family profiles was removed)
- Lyrics embedded in track files (FLAC `LYRICS`, MP3 `USLT`, M4A custom atom) via the GW lyrics API; disabled with `fetch_lyrics = false` in `[deezer]`
- Use the GW API for playlist fetching to avoid breakage on the public API ([#973](https://github.com/nathom/streamrip/pull/973))
- Include all artists from the `contributors` array in track and album metadata ([#907](https://github.com/nathom/streamrip/pull/907))
- Automatic redirect resolution for albums, playlists and artists (handles moved/deleted IDs)
- In-memory album metadata cache — avoids redundant API calls when the same album is fetched multiple times in one session ([#1000](https://github.com/nathom/streamrip/pull/1000))
- In-memory GW track data cache — `get_track()` stores the GW response so that `get_downloadable()` can reuse it without a second `song.getData` call, halving GW API requests per album download
- Connection pool sized to `max(max_connections × 4, 32)` to prevent urllib3 "pool full" warnings under concurrent downloads ([#997](https://github.com/nathom/streamrip/pull/997))
- REST API rate limiter capped at 10 req/sec — Deezer's public API returns errors above this threshold; the limiter prevents throttling under concurrent downloads
- Fix `search()` bypassing the rate limiter — it was the only REST entry point called without `async with self._api_rate_limiter`, so a Last.fm playlist of N tracks would fire N concurrent search requests via `asyncio.gather`, ignoring the 10 req/s cap
- Playlist tracks skip the per-track album fetch (saves 2 REST calls per track); GENRE, TRACKTOTAL, and DISCTOTAL tags are omitted for playlist tracks as a result. Reduces download time by 10 seconds for 100 tracks.
- Quality fallback: if the requested quality is unavailable, silently falls back to a lower tier instead of crashing
- Composer tag sourced from `SNG_CONTRIBUTORS` via the GW API (absent from the public REST API)
- Lyricist/author tag sourced from `SNG_CONTRIBUTORS` → `LYRICIST` (FLAC), `TEXT` (MP3), iTunes freeform atom (MP4)
- BPM tag written when available
- ReplayGain track gain (`GAIN` field from GW API) written as `REPLAYGAIN_TRACK_GAIN` to FLAC, `TXXX:replaygain_track_gain` to MP3, and iTunes freeform atom to MP4
- Fix `KeyError` when `disk_number` is absent from the last track in a Deezer album response ([#994](https://github.com/nathom/streamrip/pull/994))

## Tidal

- MPEG-DASH manifest (`application/dash+xml`) support for `HI_RES_LOSSLESS` streams — required since Tidal dropped MQA ([#974](https://github.com/nathom/streamrip/issues/974), [#981](https://github.com/nathom/streamrip/issues/981))
- Updated quality tier name `HI_RES` → `HI_RES_LOSSLESS` throughout the quality maps ([#974](https://github.com/nathom/streamrip/issues/974))
- Composer tag populated from the `/contributors` endpoint (fetched concurrently with lyrics)
- Fix `AttributeError` when the DASH `SegmentTemplate` has no `media` attribute
- Fix infinite recursion / `KeyError` when BTS manifest decode fails at quality 0 ([#892](https://github.com/nathom/streamrip/issues/892))
- Fix `TypeError: len(None)` when the `artists` field is absent from a track response
- Fix `KeyError` when `audioQuality` returns an unknown value (e.g. new tiers)

## Qobuz

- Fix unclosed `aiohttp.ClientSession` when `login()` raises (bad credentials, missing credentials, ineligible account, network error) — the session is now closed before the exception propagates ([#1005](https://github.com/nathom/streamrip/pull/1005))
- Fall back to `album.artist.name` when the track-level `performer` field is absent — fixes `AssertionError` on compilation albums ([#610](https://github.com/nathom/streamrip/issues/610))
- Replace `assert status == 200` guards with proper `NonStreamableError` exceptions (asserts are silently disabled by Python's `-O` flag) ([#780](https://github.com/nathom/streamrip/issues/780))
- Fix silent wrong-quality bug in `get_quality()`: passing `quality=0` would return the 24-bit format via Python's negative index instead of raising an error

## SoundCloud

- Replace `assert url is not None` with a graceful `NON_STREAMABLE` return when no HLS stream is found for a track
- Replace all remaining `assert status == 200` guards in `search`, `resolve_url`, `_get_track`, `_get_playlist`, and `get_downloadable` with `NonStreamableError` exceptions

## All clients

- `asyncio.Lock` on each client prevents concurrent login races when multiple URLs from the same source are resolved in parallel

## Integrity

- Post-download integrity check: after tagging and conversion, each track's effective bitrate (`file_size * 8 / duration`) is compared against a conservative minimum for the requested quality tier (50 kbps for MP3 128, 100 kbps for MP3 320 and FLAC, 200 kbps for Hi-Res FLAC). Files that fall below the threshold — empty files, unreadable formats, or obviously truncated downloads — trigger a WARNING log with file size, duration, and effective bitrate. The download is still recorded in the database (no hard failure); the check is non-blocking (`asyncio.to_thread`). Tracks shorter than 5 seconds are skipped to avoid false positives from header overhead.

## Downloads

- Playlist downloads pipeline the resolve and download phases: while one batch of tracks is downloading, the next batch's metadata and URL resolution runs concurrently, eliminating the idle API time between batches (~2 s saved per additional batch of 20 tracks)
- Playlist batch download boundary eliminated: each resolved track now starts downloading immediately via `asyncio.create_task` instead of waiting for all tracks in the current batch to finish. Previously the last few tracks in a batch left up to 5 download slots idle; the fix reduces the inter-batch gap from ~4 s to ~1 s (the time for the first new download to complete)
- `fast_async_download` runs the `requests` HTTP call inside `asyncio.to_thread` so it no longer blocks the event loop during concurrent downloads ([#982](https://github.com/nathom/streamrip/pull/982))
- `fast_async_download` now calls `raise_for_status()` so HTTP errors (4xx/5xx) surface as exceptions instead of silently writing the error body to disk; the partial file is removed on failure
- Fix `truncate_str` to explicitly use UTF-8 encoding and skip the encode/decode round-trip when the filename is already within the 255-byte limit
- Mutagen file I/O (tag read + write) runs in a thread pool via `asyncio.to_thread`, releasing the download slot before tagging completes — the next track's download starts immediately while the previous one is being tagged

## Converter

- OGG/OPUS: cover art is embedded post-conversion via `mutagen` (`METADATA_BLOCK_PICTURE`), and `-vn` prevents an unwanted Theora video stream ([#992](https://github.com/nathom/streamrip/pull/992))
- AAC: uses `libfdk_aac` when available, falls back to the native FFmpeg `aac` encoder ([#990](https://github.com/nathom/streamrip/pull/990))
- `OPUS` exposed as a `-c`/`--codec` option in the CLI ([#989](https://github.com/nathom/streamrip/pull/989))
- FFmpeg `stdin` redirected to `/dev/null` to prevent terminal echo/raw-mode corruption after a rip ([#996](https://github.com/nathom/streamrip/pull/996))

## Last.fm

- Score-based track matching: instead of blindly accepting the first search result, up to 10 candidates are fetched and ranked by a weighted composite score (60 % title + 40 % artist similarity via `SequenceMatcher`). The best match is returned and its score is logged at DEBUG level for post-mortem analysis. A configurable `min_score` threshold (0–1, default 0.85) rejects candidates that fall below it and logs a WARNING — raises to reduce false positives, lower if too many tracks are skipped.
- Collaboration credits (`feat. X`, `with Y`, etc.) are stripped from the search query before hitting the API, improving hit rate for tracks whose title includes a featured artist — the scorer still compares against the full original title.
- Support user library URLs (`/user/{username}/library/tracks`), loved-track URLs (`/user/{username}/loved`), and artist top-track URLs (`/music/{Artist}/+tracks`) via the Last.fm public API; requires setting `api_key` in `[lastfm]` (free key at https://www.last.fm/api/account/create). The optional `date_preset` query parameter is honoured for user library URLs; it is not supported by the public API for artist URLs (all-time results are returned with a warning). A configurable `max_tracks` (default 50) caps the number of tracks fetched to prevent silent multi-minute pagination on accounts with thousands of loved or scrobbled tracks.
- Bare artist page URLs (`/music/Artist`) now raise a descriptive error suggesting the correct `/music/Artist/+tracks` form instead of failing with a cryptic "Could not find playlist title" message
- Missing `api_key` for API-backed URLs (`/loved`, `/library/tracks`, `/+tracks`) now logs a clear actionable error ("A Last.fm API key is required…") instead of the generic "Error occurred while fetching" prefix that made it indistinguishable from a network failure
- HTTP errors (404, 503, etc.) now surface the status code and URL in the error message instead of failing silently with a cryptic parse error; this includes non-standard codes such as `600` that Last.fm's CDN occasionally returns as a transient response — a plain retry is sufficient
- Distinguish three failure modes: HTTP error from Last.fm, page structure changed/unrecognised, playlist parsed successfully but zero tracks extracted
- Fix `Exception("msg: %s", page)` bug where the page body was silently dropped from the exception args and never shown in the log
- Warning when track not found on any source now shows title and artist separately instead of printing the raw Python tuple repr
- Fix logging bug in fallback search: the fallback source name was logged as the primary source name
- "No result" log message now names both the primary and fallback source when both fail
- Fix typo "occured" → "occurred" in error log

## CLI / misc

- `-l`/`--log-file` option writes all log messages at DEBUG level to a file for post-mortem analysis ([#81](https://github.com/nathom/streamrip/issues/81)); fix: DEBUG messages from the `streamrip` logger now correctly reach the log file in non-verbose mode; fix: the `RichHandler` is explicitly held at INFO when the root logger is lowered to DEBUG for file output, preventing DEBUG messages from bleeding into the terminal alongside normal output
- Fix double "Downloading…" banner printed to the terminal at the end of a session — `ProgressManager.cleanup()` now syncs the Rich `Live` display to the cleared state before stopping it, so the last rendered frame no longer re-appears after the progress bars close
- Global overall progress bar displayed above the per-track download bars: shows `N/total • ETA` in white so the batch completion percentage is visible at a glance. Initialised by album and playlist downloads; advances after each track regardless of outcome (success, failure, already-in-DB skip)
- Last.fm track matching now shows a Rich progress bar (magenta, distinct from the cyan download bars) that advances per track and displays live found/failed counts; replaces the moon-phase spinner
- Unmatched tracks (not found on any source, or rejected by `min_score`) are written to `unmatched.txt` in the playlist folder after each run — one `Title — Artist` line per track, no file created when all tracks match
- Version check is resilient to network errors and non-JSON responses (e.g. GitHub 504) ([#995](https://github.com/nathom/streamrip/pull/995))
- Version comparison is numeric (`1.10 > 1.9`) rather than lexicographic
- Download summary printed at end of session (tracks downloaded, failed, total size)
- `-n`/`--dry-run` flag resolves and matches tracks without downloading anything — each would-be download is logged at INFO level and the end-of-session summary is labelled `[DRY RUN]`; useful for validating Last.fm matching and generating clean debug logs

## Tests

- Unit test suite added: `metadata/album.py` (99%), `metadata/track.py` (100%), `metadata/playlist.py` (100%), `metadata/covers.py` (100%), `metadata/tagger.py` (99%), `metadata/search_results.py` (98%), `media/track.py` (100%), `media/playlist.py` (100%)
- Fix `LabelSummary.summarize()` / `preview()` infinite recursion: both returned `str(self)` which called `Summary.__str__` which called `summarize()` in a loop
- Fix `AlbumSummary` bug where `item.get("artist", {}).get("name")` raised `AttributeError` when the `artist` field was a plain string rather than a dict
- Dynamic coverage badge wired to CI via GitHub Actions + Gist + shields.io
