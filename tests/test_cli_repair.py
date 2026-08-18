"""Tests for the `rip repair` command in streamrip/rip/cli.py."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from streamrip import db
from streamrip.rip.cli import repair


@pytest.fixture(autouse=True)
def _no_logging_during_invoke():
    """Silence logging while these tests run, or CliRunner loses its output.

    `log_cli` is enabled project-wide, so pytest's live-log handler suspends
    and resumes global capture around every record it emits. That reassigns
    sys.stdout, dropping the last reference to the TextIOWrapper CliRunner
    installed over its BytesIO; the wrapper is then garbage-collected, closing
    the buffer before invoke() reads it back, and the call dies with
    "I/O operation on closed file".

    Every test here trips it: asyncio.run(), inside the `coro` decorator every
    async command is wrapped in, logs "Using selector: ..." at DEBUG on each
    invocation. click 8.2 reworked CliRunner and no longer breaks this way, but
    poetry.lock pins 8.1, which is what CI installs.
    """
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)

FAILED_ROWS = [
    ("deezer", "track", "1"),
    ("deezer", "track", "2"),
    ("qobuz", "track", "3"),
]


@pytest.fixture
def cfg():
    """A Config stand-in usable as the `with ctx.obj["config"] as cfg` target."""
    c = MagicMock()
    c.__enter__ = MagicMock(return_value=c)
    c.__exit__ = MagicMock(return_value=False)
    c.session.database.failed_downloads_path = "/db/failed.db"
    c.session.database.downloads_path = "/db/downloads.db"
    c.session.filepaths.add_singles_to_folder = False
    return c


@pytest.fixture
def main():
    """An async-context-manager stand-in for Main."""
    instance = MagicMock()
    instance.add_all_by_id = AsyncMock()
    instance.resolve = AsyncMock()
    instance.rip = AsyncMock()
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    return instance


def _run(cfg, main, failed_rows, downloaded_ids, args=("--yes",)):
    """Invoke `rip repair` against fake databases.

    Args:
        cfg: The config fixture.
        main: The Main fixture.
        failed_rows: Rows the failed-downloads table starts with.
        downloaded_ids: Ids the downloads table reports after the rip, i.e.
            the items that succeeded.

    Returns:
        A ``(result, failed_db, downloads_db)`` tuple. ``result.printed`` holds
        everything the command sent to the rich console, which does not go
        through click's captured stdout.
    """
    failed_db = MagicMock()
    failed_db.all.return_value = list(failed_rows)
    downloads_db = MagicMock()
    downloads_db.contains.side_effect = lambda id: id in downloaded_ids
    console = MagicMock()

    with (
        patch("streamrip.rip.cli.db.Failed", return_value=failed_db),
        patch("streamrip.rip.cli.db.Downloads", return_value=downloads_db),
        patch("streamrip.rip.cli.Main", return_value=main),
        patch("streamrip.rip.cli.console", console),
    ):
        result = CliRunner().invoke(repair, list(args), obj={"config": cfg})

    result.printed = "\n".join(str(c.args[0]) for c in console.print.call_args_list)
    return result, failed_db, downloads_db


def test_repair_retries_every_failed_item(cfg, main):
    result, _, _ = _run(cfg, main, FAILED_ROWS, downloaded_ids=set())

    assert result.exit_code == 0
    main.add_all_by_id.assert_awaited_once_with(FAILED_ROWS)
    main.resolve.assert_awaited_once()
    main.rip.assert_awaited_once()


def test_repair_clears_only_the_items_that_succeeded(cfg, main):
    result, failed_db, _ = _run(cfg, main, FAILED_ROWS, downloaded_ids={"1", "3"})

    assert result.exit_code == 0
    assert [c.kwargs["id"] for c in failed_db.remove.call_args_list] == ["1", "3"]
    assert "Repaired 2/3" in result.printed
    assert "1 item(s) failed again" in result.printed


def test_repair_reports_full_success_without_a_warning(cfg, main):
    result, _, _ = _run(cfg, main, FAILED_ROWS, downloaded_ids={"1", "2", "3"})

    assert "Repaired 3/3" in result.printed
    assert "failed again" not in result.printed


def test_repair_clears_stale_downloads_rows_before_retrying(cfg, main):
    """An id marked both failed and downloaded would be skipped by resolve()."""
    _, _, downloads_db = _run(cfg, main, FAILED_ROWS, downloaded_ids=set())

    assert [c.kwargs["id"] for c in downloads_db.remove.call_args_list] == ["1", "2", "3"]


def test_repair_does_nothing_when_nothing_failed(cfg, main):
    result, failed_db, _ = _run(cfg, main, [], downloaded_ids=set())

    assert "No failed downloads to repair" in result.printed
    main.add_all_by_id.assert_not_awaited()
    failed_db.remove.assert_not_called()


def test_repair_puts_tracks_in_their_album_folder_by_default(cfg, main):
    """A repaired track is nearly always one missing from an otherwise
    complete album, so it has to rejoin that album's folder."""
    _run(cfg, main, FAILED_ROWS, downloaded_ids=set())

    assert cfg.session.filepaths.add_singles_to_folder is True


def test_repair_flat_leaves_the_folder_setting_alone(cfg, main):
    _run(cfg, main, FAILED_ROWS, downloaded_ids=set(), args=("--yes", "--flat"))

    assert cfg.session.filepaths.add_singles_to_folder is False


def test_repair_aborts_when_confirmation_is_declined(cfg, main):
    with patch("streamrip.rip.cli.Confirm.ask", return_value=False):
        result, failed_db, _ = _run(cfg, main, FAILED_ROWS, downloaded_ids=set(), args=())

    assert "Repair aborted" in result.printed
    main.add_all_by_id.assert_not_awaited()
    failed_db.remove.assert_not_called()


def test_repair_proceeds_when_confirmation_is_accepted(cfg, main):
    with patch("streamrip.rip.cli.Confirm.ask", return_value=True):
        result, _, _ = _run(cfg, main, FAILED_ROWS, downloaded_ids=set(), args=())

    assert result.exit_code == 0
    main.add_all_by_id.assert_awaited_once()


def test_repair_returns_early_without_a_config(main):
    with patch("streamrip.rip.cli.Main", return_value=main):
        result = CliRunner().invoke(repair, ["--yes"], obj={"config": None})

    assert result.exit_code == 0
    main.add_all_by_id.assert_not_awaited()


# ---------------------------------------------------------------------------
# Against real sqlite tables
# ---------------------------------------------------------------------------


def test_repair_against_real_databases(tmp_path, cfg, main):
    """The full flow on real tables, including the stale-state case.

    Item "2" starts out recorded as both failed and downloaded -- the
    inconsistency older versions could produce. Repair must clear its downloads
    row so the retry isn't skipped, and, when it fails again, leave it in the
    failed table and out of the downloads table.
    """
    failed_path = str(tmp_path / "failed.db")
    downloads_path = str(tmp_path / "downloads.db")
    cfg.session.database.failed_downloads_path = failed_path
    cfg.session.database.downloads_path = downloads_path

    failed_db = db.Failed(failed_path)
    for row in FAILED_ROWS:
        failed_db.add(row)
    db.Downloads(downloads_path).add(("2",))

    async def fake_rip():
        """Only "1" and "3" download successfully this time."""
        for item_id in ("1", "3"):
            db.Downloads(downloads_path).add((item_id,))

    main.rip = fake_rip

    with (
        patch("streamrip.rip.cli.Main", return_value=main),
        patch("streamrip.rip.cli.console"),
    ):
        result = CliRunner().invoke(repair, ["--yes"], obj={"config": cfg})

    assert result.exit_code == 0
    assert [row[2] for row in db.Failed(failed_path).all()] == ["2"]
    assert sorted(row[0] for row in db.Downloads(downloads_path).all()) == ["1", "3"]
