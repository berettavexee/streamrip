![streamrip logo](https://github.com/berettavexee/streamrip/blob/dev/demo/logo.svg?raw=true)

[![Downloads](https://pepy.tech/badge/streamrip)](https://pepy.tech/project/streamrip)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/python/black)
[![Coverage](https://img.shields.io/endpoint?url=https%3A%2F%2Fgist.githubusercontent.com%2Fberettavexee%2Ffd7a1415ef7a725932e26dc42554f754%2Fraw%2Fstreamrip_coverage.json)](https://github.com/berettavexee/streamrip/actions/workflows/pytest.yml)
[![Tests](https://github.com/berettavexee/streamrip/actions/workflows/pytest.yml/badge.svg)](https://github.com/berettavexee/streamrip/actions/workflows/pytest.yml)
[![Ruff](https://github.com/berettavexee/streamrip/actions/workflows/ruff.yml/badge.svg)](https://github.com/berettavexee/streamrip/actions/workflows/ruff.yml)

> **Note:** This is a personal fork of [nathom/streamrip](https://github.com/nathom/streamrip) used to test and integrate pending patches, primarily related to Deezer. It is not intended for general use — refer to the upstream project for stable releases. **The format of the configuration file config.toml has changed and is not compatible with the upstream version.**

## Changes from upstream

See [CHANGES.md](CHANGES.md) for the full list of fixes and improvements on top of [`nathom/streamrip:dev`](https://github.com/nathom/streamrip/tree/dev).

---

A scriptable stream downloader for Qobuz, Tidal, Deezer and SoundCloud.

![downloading an album](https://github.com/berettavexee/streamrip/blob/dev/demo/download_album.png?raw=true)

## Features

- Fast, concurrent downloads powered by `aiohttp`
- Downloads tracks, albums, playlists, discographies, and labels from Qobuz, Tidal, Deezer, and SoundCloud
- Downloads Deezer liked tracks from a user profile (`/profile/USER_ID/loved`)
- Supports downloads of Spotify and Apple Music playlists through [last.fm](https://www.last.fm)
- Automatically converts files to a preferred format
- Has a database that stores the downloaded tracks' IDs so that repeats are avoided
- Concurrency and rate limiting
- Interactive search for all sources
- Highly customizable through the config file

## Installation

First, ensure [Python](https://www.python.org/downloads/) (version 3.10 or greater) and [pip](https://pip.pypa.io/en/stable/installing/) are installed. Then install `ffmpeg`. You may choose not to install this, but some functionality will be limited.

Install this fork directly from the `dev` branch:

```bash
pip3 install git+https://github.com/berettavexee/streamrip.git@dev
```

> **Note:** `pip3 install streamrip --upgrade` installs the upstream release from PyPI, not this fork. Use the command above to get the patches listed below.

When you type

```bash
rip
```

it should show the main help page. If you have no idea what these mean, or are having other issues installing, check out the [detailed installation instructions](https://github.com/nathom/streamrip/wiki#detailed-installation-instructions).

## Example Usage

**For Tidal and Qobuz, you NEED a premium subscription.**

Download an album from Qobuz

```bash
rip url https://www.qobuz.com/us-en/album/rumours-fleetwood-mac/0603497941032
```

Download multiple albums from Qobuz

```bash
rip url https://www.qobuz.com/us-en/album/back-in-black-ac-dc/0886444889841 https://www.qobuz.com/us-en/album/blue-train-john-coltrane/0060253764852
```

Download the album and convert it to `mp3`

```bash
rip --codec mp3 url https://open.qobuz.com/album/0060253780968
```

To set the maximum quality, use the `--quality` option to `0, 1, 2, 3, 4`:

| Quality ID | Audio Quality         | Available Sources                            |
| ---------- | --------------------- | -------------------------------------------- |
| 0          | 128 kbps MP3 or AAC   | Deezer, Tidal, SoundCloud (most of the time) |
| 1          | 320 kbps MP3 or AAC   | Deezer, Tidal, Qobuz, SoundCloud (rarely)    |
| 2          | 16 bit, 44.1 kHz (CD) | Deezer, Tidal, Qobuz, SoundCloud (rarely)    |
| 3          | 24 bit, ≤ 96 kHz      | Tidal (MQA), Qobuz, SoundCloud (rarely)      |
| 4          | 24 bit, ≤ 192 kHz     | Qobuz                                        |

```bash
rip --quality 3 url https://tidal.com/browse/album/147569387
```

> Using `4` is generally a waste of space. It is impossible for humans to perceive the difference between sampling rates higher than 44.1 kHz. It may be useful if you're processing/slowing down the audio.

Search for playlists matching `rap` on Tidal

```bash
rip search tidal playlist 'bossa nova'
```

![streamrip interactive search](https://github.com/berettavexee/streamrip/blob/dev/demo/playlist_search.png?raw=true)

Search for *Rumours* on Tidal, and download it

```bash
rip search tidal album 'fleetwood mac rumours'
```

Download a last.fm playlist using the lastfm command

```
rip lastfm https://www.last.fm/user/nathan3895/playlists/12126195
```

Download your Deezer liked tracks

```bash
rip url https://www.deezer.com/fr/profile/USER_ID/loved
```

For more customization, see the config file

```
rip config open
```

If you're confused about anything, see the help pages. The main help pages can be accessed by typing `rip` by itself in the command line. The help pages for each command can be accessed with the `--help` flag. For example, to see the help page for the `url` command, type

```
rip url --help
```

![example_help_page.png](https://github.com/berettavexee/streamrip/blob/dev/demo/example_help_page.png?raw=true)

## Other information

For more in-depth information about `streamrip`, see the help pages and the [wiki](https://github.com/nathom/streamrip/wiki/).

## Contributions

All contributions are appreciated! You can help out the project by opening an issue
or by submitting code.

### Issues

If you're opening an issue **use the Feature Request or Bug Report templates properly**. This ensures
that I have all of the information necessary to debug the issue. If you do not follow the templates,
**I will silently close the issue** and you'll have to deal with it yourself.

### Code

If you're new to Git, follow these steps to open your first Pull Request (PR):

- Fork this repository
- Clone the new repository
- Commit your changes
- Open a pull request to the `dev` branch

Please document any functions or obscure lines of code.

### Testing

The CI runs `poetry install` followed by `poetry run pytest --cov`; use that exact
pair locally, since installing with `pip` leaves the virtualenv ahead of
`poetry.lock` and hides bugs specific to the locked versions.

`./run_realworld_tests.sh` runs `rip` against live Deezer/Last.fm URLs — tracks,
album, playlist, loved tracks, artist top tracks, Last.fm, AIFF/OPUS conversion
and `rip repair` — each case isolated in `realworld_runs/<timestamp>/` with its
own DEBUG log, and validated on container/extension coherence and on tags. Copy
`test_urls.env.example` to `test_urls.env` and fill in your own URLs; any case
left blank is skipped.

```bash
cp test_urls.env.example test_urls.env && chmod 600 test_urls.env
DRY_RUN=1 ./run_realworld_tests.sh      # resolve without downloading
KEEP_AUDIO=0 KEEP_RUNS=4 ./run_realworld_tests.sh   # validate, then reclaim disk
```

`test_urls.env` and `realworld_runs/` are gitignored and must stay that way: the
first holds account URLs and, optionally, a Deezer ARL — a session token
equivalent to a password — and the second holds session logs. Never add either,
not even with `git add -f`.

### The Wiki

To help out `streamrip` users that may be having trouble, consider contributing some information to the wiki.
Nothing is too obvious and everything is appreciated.

## Acknowledgements

Thanks to Vitiko98, Sorrow446, and DashLt for their contributions to this project, and the previous projects that made this one possible.

`streamrip` was inspired by:

- [qobuz-dl](https://github.com/vitiko98/qobuz-dl)
- [Qo-DL Reborn](https://github.com/badumbass/Qo-DL-Reborn)
- [Tidal-Media-Downloader](https://github.com/yaronzz/Tidal-Media-Downloader)
- [scdl](https://github.com/flyingrub/scdl)

## Disclaimer

I will not be responsible for how **you** use `streamrip`. By using `streamrip`, you agree to the terms and conditions of the Qobuz, Tidal, and Deezer APIs.

## Sponsorship

Consider becoming a Github sponsor for me if you enjoy my open source software.
