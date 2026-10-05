#!/usr/bin/env bash
#
# bench.sh — compare this fork against upstream nathom/streamrip on the same
# download, and print the numbers that back benchmark.MD.
#
# Written in English, unlike run_realworld_tests.sh, because the report it
# produces (benchmark.MD) is public-facing and so is any figure quoted from it.
#
# Usage:
#   URL=https://www.deezer.com/album/302127 ./bench.sh
#   URL=<playlist url> LABEL=playlist N=3 ./bench.sh
#   URL=<url> INSTRUMENT=1 ./bench.sh      # + CPU, memory and write syscalls
#   URL=<url> INSTRUMENT_API=1 UPSTREAM_REF=stensel/dev \
#     UPSTREAM_PYTHON=/usr/bin/python3.14 UPSTREAM_BIN=streamrip ./bench.sh
#
# Environment:
#   URL          what to download (required)
#   LABEL        names the results file; default derived from the URL
#   N            number of PAIRS of runs; default 3
#   UPSTREAM_REF git ref of upstream to build; default upstream/dev
#   INSTRUMENT   1 = add one /usr/bin/time run and one strace run per side
#   INSTRUMENT_API 1 = log every HTTP request of every run (requests and aiohttp,
#                via an injected sitecustomize) and report per-side call counts
#   UPSTREAM_PYTHON interpreter for the other side's venv; default python3
#                (Stensel8/streamrip needs /usr/bin/python3.14)
#   UPSTREAM_BIN command the other side installs; default rip (Stensel8: streamrip)
#   KEEP_AUDIO   1 = keep the downloads (default 0: each run is ~0.4-1.8 GB)
#
# Outputs land in benchmark_runs/<timestamp>/ (gitignored): the CSV of every
# run, one log per run, and the instrumentation files.
#
# ── Why it is built this way ─────────────────────────────────────────────────
# Runs ALTERNATE fork, upstream, fork, upstream. Timing one side N times and
# then the other charges any network or CDN drift between the two blocks to the
# implementation; that drift is real and was measured at several seconds across
# a session.
#
# Each side gets a config generated from ITS OWN template with the same values
# injected. Handing upstream a copy of this fork's config does not work: the
# config versions differ and this fork has dropped fields upstream still reads.
#
# check_for_updates is forced off on both. Both projects otherwise make a
# version-check HTTP call unrelated to download performance, and it only fires
# when a newer release exists — so leaving it on penalises whichever side
# happens to be behind.
#
# Every run is checked for completeness (file count, bytes, exit code) before
# its timing counts. A run that failed, or that produced fewer files, is not a
# faster run — see the geoblocked track discussed in benchmark.MD.
#
# ⚠️ The generated configs carry the real ARL. They are created with mktemp at
# mode 0600, outside the project tree, and removed on exit including on Ctrl-C.
# Your own ~/.config/streamrip/config.toml is read but never modified.
set -uo pipefail
# The timings are formatted with printf %f; a French locale expects a comma and
# would reject the value produced by bc.
export LC_NUMERIC=C

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

URL="${URL:?set URL to the album/playlist to download}"
N="${N:-3}"
UPSTREAM_REF="${UPSTREAM_REF:-upstream/dev}"
INSTRUMENT="${INSTRUMENT:-}"
INSTRUMENT_API="${INSTRUMENT_API:-}"
UPSTREAM_PYTHON="${UPSTREAM_PYTHON:-python3}"
UPSTREAM_BIN="${UPSTREAM_BIN:-rip}"
KEEP_AUDIO="${KEEP_AUDIO:-0}"
LABEL="${LABEL:-$(sed -E 's#.*/([a-z]+)/([0-9]+).*#\1-\2#' <<<"$URL")}"

RUN_DIR="$SCRIPT_DIR/benchmark_runs/$(date +%Y%m%d-%H%M%S)-$LABEL"
RESULTS="$RUN_DIR/results.csv"
WORK="$SCRIPT_DIR/benchmark_runs/upstream-$(tr -c 'a-zA-Z0-9' '-' <<<"$UPSTREAM_REF")"

for dep in git python3 bc; do
    command -v "$dep" >/dev/null || { echo "missing dependency: $dep" >&2; exit 1; }
done

FORK_RIP="$SCRIPT_DIR/.venv/bin/rip"
if [[ ! -x "$FORK_RIP" ]]; then
    echo "not found: $FORK_RIP — run 'poetry sync' first" >&2
    exit 1
fi

# ── Upstream checkout, cached between invocations ────────────────────────────
UP_RIP="$WORK/.venv/bin/$UPSTREAM_BIN"
if [[ ! -x "$UP_RIP" ]]; then
    echo "==> building $UPSTREAM_REF (cached in $WORK)"
    git rev-parse --verify "$UPSTREAM_REF" >/dev/null 2>&1 || {
        echo "unknown ref $UPSTREAM_REF — add the remote first:" >&2
        echo "  git remote add upstream https://github.com/nathom/streamrip.git" >&2
        echo "  git fetch upstream" >&2
        exit 1
    }
    rm -rf "$WORK"; mkdir -p "$WORK"
    git archive "$UPSTREAM_REF" | tar -x -C "$WORK"
    "$UPSTREAM_PYTHON" -m venv "$WORK/.venv"
    "$WORK/.venv/bin/pip" install -q --upgrade pip
    "$WORK/.venv/bin/pip" install -q -e "$WORK" || {
        echo "failed to install upstream into its venv" >&2; exit 1; }
fi

# ── Configs, one per side, from each side's own template ─────────────────────
TMP_CFGS=()
cleanup() { local f; for f in ${TMP_CFGS+"${TMP_CFGS[@]}"}; do rm -f "$f"; done; return 0; }
# INT/TERM as well as EXIT: these files carry an ARL and must not survive a ^C.
trap cleanup EXIT INT TERM

SRC_CFG=$("$FORK_RIP" --help >/dev/null 2>&1; "$SCRIPT_DIR/.venv/bin/python" -c \
    'from streamrip.config import DEFAULT_CONFIG_PATH; print(DEFAULT_CONFIG_PATH)')
[[ -f "$SRC_CFG" ]] || { echo "no streamrip config at $SRC_CFG" >&2; exit 1; }

make_config() {
    # make_config <template> <db-prefix>; echoes nothing, sets CFG_OUT.
    local template="$1" prefix="$2" dst
    dst=$(mktemp -t streamrip-bench-XXXXXX.toml) || return 1
    chmod 600 "$dst"
    cp "$template" "$dst"
    TMP_CFGS+=("$dst")
    # The ARL travels through the environment, never on a command line, so it
    # cannot be read out of the process table.
    ARL_VALUE=$(sed -nE 's/^arl = "([^"]+)"$/\1/p' "$SRC_CFG") \
    CFG="$dst" PREFIX="$RUN_DIR/$prefix" OUT="$RUN_DIR/out" \
        "$SCRIPT_DIR/.venv/bin/python" - <<'PY'
import os, re, sys
cfg, prefix, out = os.environ["CFG"], os.environ["PREFIX"], os.environ["OUT"]
arl = os.environ.get("ARL_VALUE", "")
if not arl:
    sys.exit("no arl found in the streamrip config — log in first")
s = open(cfg, encoding="utf-8").read()
# Keys a side may have dropped (Stensel8 removed both): set when present.
optional = {
    r'^concurrency = .*$': "concurrency = true",
    r'^check_for_updates = .*$': "check_for_updates = false",
}
for pattern, replacement in optional.items():
    s = re.sub(pattern, replacement, s, flags=re.MULTILINE)
subs = {
    r'^arl = ".*"$': f'arl = "{arl}"',
    r'^max_connections = .*$': "max_connections = 6",
    r'^requests_per_minute = .*$': "requests_per_minute = 60",
    # Upstream ships these empty and its db.Failed asserts on a non-empty path,
    # so the run would die before downloading anything.
    r'^downloads_path = ".*"$': f'downloads_path = "{prefix}-downloads.db"',
    r'^failed_downloads_path = ".*"$': f'failed_downloads_path = "{prefix}-failed.db"',
    r'^folder = ".*"$': f'folder = "{out}"',
}
for pattern, replacement in subs.items():
    s, n = re.subn(pattern, replacement, s, flags=re.MULTILINE)
    if n != 1:
        sys.exit(f"expected exactly one {pattern!r} in {cfg}, found {n}")
# Deezer quality 2 = FLAC. The key exists under several sources, so scope the
# substitution to the [deezer] table.
s = re.sub(r'(\[deezer\](?:(?!\n\[).)*?^quality = )\d', r"\g<1>2", s,
           flags=re.MULTILINE | re.DOTALL)
open(cfg, "w", encoding="utf-8").write(s)
PY
    CFG_OUT="$dst"
}

mkdir -p "$RUN_DIR/logs"
make_config "$SCRIPT_DIR/streamrip/config.toml" fork || exit 1
FORK_CFG="$CFG_OUT"
make_config "$WORK/streamrip/config.toml" up || exit 1
UP_CFG="$CFG_OUT"

echo "url        : $URL"
echo "fork       : $("$FORK_RIP" --version) ($(git rev-parse --short HEAD))"
echo "upstream   : $("$UP_RIP" --version) ($(git rev-parse --short "$UPSTREAM_REF"))"
echo "pairs      : $N"
echo "results    : $RUN_DIR"
echo

rip_of() { [[ "$1" == fork ]] && echo "$FORK_RIP" || echo "$UP_RIP"; }
cfg_of() { [[ "$1" == fork ]] && echo "$FORK_CFG" || echo "$UP_CFG"; }

count_audio() {
    find "$1" -type f \( -iname '*.flac' -o -iname '*.mp3' -o -iname '*.m4a' \
        -o -iname '*.opus' -o -iname '*.ogg' \) 2>/dev/null | wc -l
}

echo "impl,run,seconds,files,bytes,rc" > "$RESULTS"

# ── Optional: count the HTTP requests each side makes ───────────────────────
# A sitecustomize on PYTHONPATH wraps requests.Session.send (deezer-py, the
# plain downloads) and aiohttp.ClientSession._request (CDN streams, covers)
# in both processes, without touching either codebase. One JSON line per
# request: host, path, and the GW method name, which is all a gw-light.php
# call is distinguished by. Query strings are dropped: they carry tokens.
API_DIR=""
if [[ -n "$INSTRUMENT_API" ]]; then
    API_DIR="$RUN_DIR/api"
    mkdir -p "$API_DIR/site"
    cat >"$API_DIR/site/sitecustomize.py" <<'PY'
import atexit, json, os, threading, urllib.parse

_log = os.environ.get("BENCH_API_LOG")
_lines, _lock = [], threading.Lock()


def _record(url, method):
    u = urllib.parse.urlsplit(str(url))
    q = urllib.parse.parse_qs(u.query)
    with _lock:
        _lines.append({"host": u.hostname, "path": u.path, "verb": method,
                       "gw": (q.get("method") or [None])[0]})


if _log:
    @atexit.register
    def _dump():
        with open(_log, "a", encoding="utf-8") as f:
            f.writelines(json.dumps(x) + "\n" for x in _lines)

    try:
        import requests

        _send = requests.Session.send

        def send(self, request, **kw):
            _record(request.url, request.method)
            return _send(self, request, **kw)

        requests.Session.send = send
    except ImportError:
        pass
    try:
        import aiohttp

        _req = aiohttp.ClientSession._request

        async def _request(self, method, str_or_url, **kw):
            _record(str_or_url, method)
            return await _req(self, method, str_or_url, **kw)

        aiohttp.ClientSession._request = _request
    except ImportError:
        pass
PY
fi

api_env() {
    # api_env <impl> <run>: env assignments for one run, nothing if disabled.
    [[ -n "$API_DIR" ]] || return 0
    echo "PYTHONPATH=$API_DIR/site BENCH_API_LOG=$API_DIR/$1-$2.jsonl"
}

run_one() {
    local impl="$1" i="$2"
    local out="$RUN_DIR/out/$impl-$i" log="$RUN_DIR/logs/$impl-$i.log"
    mkdir -p "$out"
    local start end rc files bytes
    start=$(date +%s.%N)
    env $(api_env "$impl" "$i") \
        "$(rip_of "$impl")" --config-path "$(cfg_of "$impl")" -ndb -f "$out" url "$URL" \
        >"$log" 2>&1
    rc=$?
    end=$(date +%s.%N)
    files=$(count_audio "$out")
    bytes=$(du -sb "$out" 2>/dev/null | cut -f1)
    printf '%s,%d,%.2f,%d,%d,%d\n' "$impl" "$i" "$(bc <<<"$end - $start")" \
        "$files" "${bytes:-0}" "$rc" | tee -a "$RESULTS"
    [[ "$KEEP_AUDIO" == "1" ]] || rm -rf "$out"
}

for ((i = 1; i <= N; i++)); do
    run_one fork "$i"
    run_one upstream "$i"
done

# ── Optional: where the time actually goes ───────────────────────────────────
if [[ -n "$INSTRUMENT" ]]; then
    echo
    echo "==> instrumented runs (one per side, not averaged)"
    mkdir -p "$RUN_DIR/instr"
    for impl in fork upstream; do
        out="$RUN_DIR/out/instr-$impl"; mkdir -p "$out"
        /usr/bin/time -f "%e wall  %U user  %S sys  %M KB peak RSS" \
            -o "$RUN_DIR/instr/$impl.time" \
            "$(rip_of "$impl")" --config-path "$(cfg_of "$impl")" -ndb -f "$out" \
            url "$URL" >/dev/null 2>&1
        printf '  %-9s %s\n' "$impl" "$(cat "$RUN_DIR/instr/$impl.time")"
        rm -rf "$out"
        if command -v strace >/dev/null; then
            out="$RUN_DIR/out/strace-$impl"; mkdir -p "$out"
            strace -f -c -e trace=write -o "$RUN_DIR/instr/$impl.strace" \
                "$(rip_of "$impl")" --config-path "$(cfg_of "$impl")" -ndb -f "$out" \
                url "$URL" >/dev/null 2>&1
            printf '  %-9s writes: %s\n' "$impl" \
                "$(awk '/ write$/ {print $4" calls, "$2"s"}' "$RUN_DIR/instr/$impl.strace")"
            rm -rf "$out"
        fi
    done
fi

rmdir "$RUN_DIR/out" 2>/dev/null

# ── Summary ──────────────────────────────────────────────────────────────────
echo
RESULTS="$RESULTS" python3 - <<'PY'
import csv, os, statistics as st
rows = list(csv.DictReader(open(os.environ["RESULTS"])))
data = {}
for r in rows:
    data.setdefault(r["impl"], []).append(r)
bad = [r for r in rows if r["rc"] != "0"]
if bad:
    print(f"WARNING: {len(bad)} run(s) exited non-zero; timings below are not comparable")
files = {r["files"] for r in rows}
if len(files) > 1:
    print(f"WARNING: runs produced different file counts {sorted(files)} — "
          "the two sides did not do the same work, read the logs before quoting a ratio")
means = {}
for impl, rs in data.items():
    s = [float(r["seconds"]) for r in rs]
    means[impl] = st.mean(s)
    mb = st.mean(int(r["bytes"]) for r in rs) / 1e6
    print(f"{impl:9} n={len(s)}  mean={st.mean(s):7.2f}s  median={st.median(s):7.2f}s  "
          f"min={min(s):7.2f}  max={max(s):7.2f}  sd={st.pstdev(s):5.2f}  "
          f"files={rs[0]['files']}  {mb:.0f} MB  {mb/st.mean(s):.1f} MB/s")
if len(means) == 2:
    f, u = means["fork"], means["upstream"]
    fast = [float(r["seconds"]) for r in data["fork"]]
    slow = [float(r["seconds"]) for r in data["upstream"]]
    print(f"\nfork is {u - f:.2f}s faster, {f / u * 100:.0f}% of upstream's time "
          f"({u / f:.2f}x)")
    print("ranges overlap:", "no" if max(fast) < min(slow) else "YES — treat the ratio with care")
PY

# ── API call counts ──────────────────────────────────────────────────────────
if [[ -n "$API_DIR" ]]; then
    echo
    API_DIR="$API_DIR" python3 - <<'PY'
import collections, glob, json, os, re, statistics as st

def kind(r):
    h, p = r["host"] or "", r["path"] or ""
    if r["gw"]:
        return "GW " + r["gw"]
    if h == "api.deezer.com":
        return "REST /" + re.sub(r"/\d+", "/<id>", p.strip("/"))
    if h == "media.deezer.com":
        return "media " + p
    if "dzcdn.net" in h and "/images/" in p:
        return "cover image"
    if "dzcdn.net" in h:
        return "audio stream"
    return "other " + h

runs = collections.defaultdict(list)
for f in sorted(glob.glob(os.path.join(os.environ["API_DIR"], "*.jsonl"))):
    impl = os.path.basename(f).rsplit("-", 1)[0]
    runs[impl].append(collections.Counter(kind(json.loads(l)) for l in open(f)))
if not runs:
    print("API: no request logged")
    raise SystemExit
kinds = sorted({k for rs in runs.values() for c in rs for k in c})
impls = sorted(runs)
print("HTTP requests per run (mean over runs)")
print(f"{'':40}" + "".join(f"{i:>12}" for i in impls))
for k in kinds:
    print(f"{k[:40]:40}" + "".join(f"{st.mean(c[k] for c in runs[i]):12.1f}" for i in impls))
tot = {i: st.mean(sum(c.values()) for c in runs[i]) for i in impls}
api = {i: st.mean(sum(v for k, v in c.items() if k.split()[0] in ("GW", "REST", "media"))
                   for c in runs[i]) for i in impls}
print(f"{'TOTAL':40}" + "".join(f"{tot[i]:12.1f}" for i in impls))
print(f"{'API calls (GW + REST + media)':40}" + "".join(f"{api[i]:12.1f}" for i in impls))
PY
fi
