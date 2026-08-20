"""Tests for the session-level phase-timing debug lines of rip.main.Main.

These lines are the only way to tell, from a `-l` log, whether a slow session
went on logging in, on resolving metadata, or on the transfers themselves.
They are pure instrumentation, so nothing else fails when one is dropped in a
refactor — hence a test per line.

Main is instantiated via ``__new__`` to bypass the config/database setup in
``__init__``; only the attributes each method touches are supplied.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from streamrip.rip.main import Main


@pytest.fixture
def caplog_debug(caplog):
    caplog.set_level(logging.DEBUG, logger="streamrip")
    return caplog


def _bare_main() -> Main:
    main = Main.__new__(Main)
    main.config = MagicMock()
    main.database = MagicMock()
    main.clients = {}
    main.pending = []
    main.media = []
    return main


# ---------------------------------------------------------------------------
# add_all — "login … add N URL(s) …"
# ---------------------------------------------------------------------------


async def test_add_all_logs_login_and_add_timing(caplog_debug):
    main = _bare_main()
    main.get_logged_in_client = AsyncMock(return_value=MagicMock())

    parsed = MagicMock()
    parsed.source = "deezer"
    parsed.into_pending = AsyncMock(return_value=MagicMock())

    with patch("streamrip.rip.main.parse_url", return_value=parsed):
        await main.add_all(["https://www.deezer.com/track/1"])

    assert len(main.pending) == 1
    assert "Phase timing: login" in caplog_debug.text
    assert "add 1 URL(s)" in caplog_debug.text


async def test_add_all_logs_nothing_when_no_url_parses(caplog_debug):
    """The early return skips the timing line — there is no phase to report."""
    main = _bare_main()

    with patch("streamrip.rip.main.parse_url", return_value=None):
        await main.add_all(["not-a-url"])

    assert "Phase timing" not in caplog_debug.text


# ---------------------------------------------------------------------------
# resolve — "resolved N/M pending item(s) …"
# ---------------------------------------------------------------------------


async def test_resolve_logs_resolved_over_pending(caplog_debug):
    """The ratio is the point: it shows resolution failures a total would hide."""
    main = _bare_main()

    ok = MagicMock()
    ok.resolve = AsyncMock(return_value=MagicMock())
    boom = MagicMock()
    boom.resolve = AsyncMock(side_effect=RuntimeError("nope"))
    dropped = MagicMock()
    dropped.resolve = AsyncMock(return_value=None)
    main.pending = [ok, boom, dropped]

    await main.resolve()

    assert len(main.media) == 1
    assert "Phase timing: resolved 1/3 pending item(s)" in caplog_debug.text


# ---------------------------------------------------------------------------
# rip — "downloaded N media item(s) …"
# ---------------------------------------------------------------------------


async def test_rip_logs_download_phase_timing(caplog_debug):
    main = _bare_main()
    main.config.session.cli.dry_run = False

    item = MagicMock()
    item.rip = AsyncMock()
    main.media = [item, MagicMock(rip=AsyncMock())]

    with (
        patch("streamrip.rip.main.clear_progress"),
        patch("streamrip.rip.main.console"),
    ):
        await main.rip()

    assert "Phase timing: downloaded 2 media item(s)" in caplog_debug.text
