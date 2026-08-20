# Benchmark — this fork vs upstream

Time, CPU and memory for a full download, this fork against
[`nathom/streamrip`](https://github.com/nathom/streamrip), on identical inputs
and matched settings.

Measured on 2026-08-20. Fork at `57d363a` (2.2.1), upstream at `e329161` (2.2.0,
the head of `nathom/streamrip:dev`). Both on Python 3.12.3, 12 cores, 15 GB RAM,
same machine, same network, same Deezer account, FLAC (`quality = 2`) requested
throughout.

Summary: the fork finishes in **73 % of upstream's time** on an album and
**64 %** on a playlist, using **a third of the peak memory**, and it brings back
one geoblocked track in FLAC that upstream degrades to MP3.

## Results

**Album — Daft Punk, *Discovery*, 14 tracks, 420 MB** (6 runs each):

| | mean | median | min – max | σ | effective throughput |
|---|---|---|---|---|---|
| **This fork** | **17.09 s** | 16.98 s | 16.73 – 17.67 | 0.32 | 24.5 MB/s |
| Upstream | 23.47 s | 23.31 s | 22.17 – 25.28 | 0.98 | 17.9 MB/s |

The fork is **6.38 s faster, 27 % of upstream's time (1.37×)**.

**Playlist — 59 tracks, 1.8 GB** (3 runs each):

| | mean | median | min – max | σ | effective throughput |
|---|---|---|---|---|---|
| **This fork** | **65.04 s** | 65.56 s | 63.16 – 66.39 | 1.37 | 28.1 MB/s |
| Upstream | 101.88 s | 101.47 s | 100.82 – 103.36 | 1.08 | 17.7 MB/s |

The fork is **36.85 s faster, 36 % of upstream's time (1.57×)**.

In both workloads the two ranges do not overlap: the fork's slowest run beats
upstream's fastest. The fork is also the steadier of the two on the album, at a
third of upstream's standard deviation.

**Resources**, from one instrumented playlist run per side:

| | wall | user CPU | sys CPU | total CPU | peak RSS |
|---|---|---|---|---|---|
| **This fork** | 67.2 s | 54.4 s | 17.6 s | **72.0 s** | **139 MB** |
| Upstream | 102.7 s | 71.1 s | 43.8 s | **114.9 s** | **381 MB** |

## The playlist run is not the same work

Upstream produced 59 files and 1804 MB; the fork produced 59 files and 1827 MB.
The whole 23 MB difference is a single track:

| | file | size |
|---|---|---|
| This fork | `31. Children Of Bodom - Oops!...I Did It Again.flac` | 25 MB |
| Upstream | `31. Children Of Bodom - Oops!...I Did It Again.mp3` | 3.2 MB |

That track is geoblocked for this account. The fork follows Deezer's own
`FALLBACK.SNG_ID` redirect and gets the lossless stream of the release that
superseded it — `flac -t` confirms the file decodes clean. Upstream has no
fallback path, so it degrades the track to MP3. Upstream is therefore doing
*slightly less work* in the runs above, which makes its deficit a floor rather
than a ceiling.

## Where the time goes: the decryption path

Upstream burns **42.9 s more CPU** than the fork for a 35.5 s wall gap. Both run
a single asyncio event loop, so that extra CPU is not absorbed by the eleven idle
cores — it is time during which no download is progressing. The gap is compute,
not network: the two move identical bytes, and neither is bandwidth bound at
24.5 MB/s against 17.9.

It comes from how each one decrypts. Deezer encrypts only the first 2048 bytes
of every 6144-byte segment, and the segments are independent, so the stream can
be decrypted as it arrives. Upstream does not do that — `DeezerDownloadable._download`
accumulates the **entire track in memory** first:

```python
buf = bytearray()
async for data, _ in resp.content.iter_chunks():
    buf += data                                  # whole track buffered
...                                              # only once the transfer ends:
async with aiofiles.open(path, "wb") as audio:
    for i in range(0, buflen, encrypt_chunk_size):
        ...
        await audio.write(decrypted_chunk)       # one write per 6 KB segment
```

This fork keeps a small carry instead, decrypts whole segments as chunks land,
and writes them in batches — so the work overlaps the transfer rather than
following it.

Two consequences, both measured:

- **Write granularity.** Over the same album — 14 tracks, 420 MB — upstream
  issues **68,750 `write` syscalls against this fork's 2,662** (`strace -c`):
  6 KB per write against 158 KB. Since `aiofiles` dispatches every write to a
  thread pool, that is also 26× the thread-pool round trips. Time inside `write`
  alone: 3.05 s against 0.80 s.
- **Memory.** Buffering whole tracks with six concurrent downloads puts peak RSS
  at **381 MB against 139 MB**. It scales with track size rather than staying
  flat, so hi-res material would widen it further.

Isolated from the network, decrypting and writing one 25 MB track costs 0.92 s
the upstream way against 0.44 s streaming — and upstream pays that bill *after*
the transfer, where this fork has already spent it while bytes were still
arriving.

Metadata resolution is a smaller, separate effect. From process start to the
first byte of audio there is only about a second between them, and the fork's own
phase timing on the album puts resolution at a rounding error next to the
transfer:

```
Phase timing: login 0.24s, add 1 URL(s) 0.00s
Phase timing: resolved 1/1 pending item(s) 0.37s
Phase timing: downloaded 1 media item(s) 15.89s
```

Batched CDN URL resolution replaces one HTTP round trip per track with one per
batch, which is what makes the margin widen from 27 % on 14 tracks to 36 % on
59 — but it is not what creates the margin in the first place.

## Method

Both sides ran the same command shape against a fresh output directory:

```
rip --config-path <bench config> -ndb -f <fresh dir> url <URL>
```

- **Runs alternate** fork, upstream, fork, upstream… Measuring one side three
  times and then the other would charge any network or CDN drift between the two
  blocks to the implementation — and there was drift: upstream's album runs slid
  from 22.2 s to 25.3 s across the session.
- **Each side gets a config built from its own template**, with the same values
  injected: `quality = 2`, `max_connections = 6`, `concurrency = true`,
  `requests_per_minute = 60`. Copying the fork's config to upstream would not
  work — the config version differs and the fork has dropped dead fields.
- **`check_for_updates = false` on both.** Both projects otherwise make a
  version-check HTTP call that has nothing to do with download performance, and
  it fires only when a newer release exists, so leaving it on would penalise
  whichever side happens to be behind.
- **`-ndb` and a fresh directory per run**, so nothing is skipped as already
  downloaded and nothing is resumed.
- **Every run is checked for completeness** — file count, total bytes, exit
  code — before its timing is kept. A run that failed or produced fewer files is
  not a faster run.
- The user's real `~/.config/streamrip/config.toml` is **read** — that is where
  the ARL comes from — but never modified. The ARL is copied into throwaway
  configs at mode 0600, outside the project tree, deleted on exit.

`use_deezloader`, which upstream still has and this fork removed, does not
affect the comparison: upstream's own code never uses it when an ARL is present.

## Reproducing

`./bench.sh` in this repository does all of the above:

```bash
git remote add upstream https://github.com/nathom/streamrip.git && git fetch upstream
URL=https://www.deezer.com/album/302127 N=6 ./bench.sh
URL=<playlist url> N=3 INSTRUMENT=1 ./bench.sh
```

It builds the upstream ref into its own cached virtualenv, generates one config
per side from that side's own template, alternates the runs, checks each one for
completeness, deletes the audio as it goes, and prints the summary. With
`INSTRUMENT=1` it adds one `/usr/bin/time` run and one `strace -c -e trace=write`
run per side, which is where the CPU, memory and syscall figures above come
from. Results land in `benchmark_runs/` (gitignored).

Two traps it handles, worth knowing if you reimplement it: upstream's shipped
`config.toml` leaves `downloads_path` and `failed_downloads_path` empty while
its `db.Failed.__init__` asserts on a non-empty path, so a run dies before
downloading anything; and the generated configs carry the real ARL, so they are
created with `mktemp` at mode 0600 outside the project tree and removed on exit,
including on Ctrl-C.

## Limits

- One machine, one network, one account, one evening. These numbers describe a
  ratio between two implementations under identical conditions, not absolute
  throughput anyone else should expect.
- Three runs per side on the playlist, six on the album. Enough for
  non-overlapping ranges at this effect size, not enough for a confidence
  interval worth quoting.
- The CPU, syscall and memory figures come from one instrumented run per side,
  not from repeated trials. Their ratios are large enough to read qualitatively —
  26× the syscalls, 2.7× the memory — but the exact numbers are not averages.
- Only the Deezer path is covered. Qobuz, Tidal and SoundCloud are untested here,
  and most of this fork's divergence is Deezer-specific anyway.
- Downloads land on a warm CDN. A cold cache would likely move both numbers, and
  might not move them equally.
