"""Tests for the `rip database clear` command in streamrip/rip/cli.py."""

import logging
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from streamrip import db
from streamrip.rip.cli import database_clear


@pytest.fixture(autouse=True)
def _no_logging_during_invoke():
    """Silence logging while these tests run, or CliRunner loses its output.

    See the identical fixture in test_cli_repair.py for the full explanation:
    project-wide ``log_cli`` reassigns ``sys.stdout`` around each record, which
    closes the buffer CliRunner installed before ``invoke()`` reads it back.
    """
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


@pytest.fixture
def cfg(tmp_path):
    """A Config stand-in whose database paths point at real temp files."""
    c = MagicMock()
    c.session.database.failed_downloads_path = str(tmp_path / "failed.db")
    c.session.database.downloads_path = str(tmp_path / "downloads.db")
    return c


def _seed(cfg):
    """Populate both tables and return them."""
    failed = db.Failed(cfg.session.database.failed_downloads_path)
    failed.add(("deezer", "track", "1"))
    failed.add(("deezer", "track", "2"))
    downloads = db.Downloads(cfg.session.database.downloads_path)
    downloads.add(("a",))
    downloads.add(("b",))
    downloads.add(("c",))
    return failed, downloads


def _invoke(cfg, args, obj_config=...):
    """Invoke `database clear` with the rich console captured."""
    console = MagicMock()
    obj = {"config": cfg if obj_config is ... else obj_config}
    with patch("streamrip.rip.cli.console", console):
        result = CliRunner().invoke(database_clear, list(args), obj=obj)
    result.printed = "\n".join(str(c.args[0]) for c in console.print.call_args_list)
    return result


def _failed(cfg):
    return db.Failed(cfg.session.database.failed_downloads_path).all()


def _downloads(cfg):
    return db.Downloads(cfg.session.database.downloads_path).all()


def test_clear_failed_empties_only_the_failed_table(cfg):
    _seed(cfg)
    result = _invoke(cfg, ("failed", "--yes"))

    assert result.exit_code == 0
    assert _failed(cfg) == []
    assert len(_downloads(cfg)) == 3
    assert "failed download(s)" in result.printed


def test_clear_downloads_empties_only_the_downloads_table(cfg):
    _seed(cfg)
    result = _invoke(cfg, ("downloads", "--yes"))

    assert result.exit_code == 0
    assert _downloads(cfg) == []
    assert len(_failed(cfg)) == 2
    assert "downloaded track(s)" in result.printed


def test_clear_all_empties_both_tables(cfg):
    _seed(cfg)
    result = _invoke(cfg, ("all", "--yes"))

    assert result.exit_code == 0
    assert _failed(cfg) == []
    assert _downloads(cfg) == []


def test_clear_reports_nothing_to_clear_when_empty(cfg):
    # No seeding: both tables are empty.
    result = _invoke(cfg, ("all", "--yes"))

    assert "Nothing to clear" in result.printed


def test_clear_aborts_when_confirmation_declined(cfg):
    _seed(cfg)
    with patch("streamrip.rip.cli.Confirm.ask", return_value=False):
        result = _invoke(cfg, ("failed",))

    assert "Clear aborted" in result.printed
    assert len(_failed(cfg)) == 2  # untouched


def test_clear_proceeds_when_confirmation_accepted(cfg):
    _seed(cfg)
    with patch("streamrip.rip.cli.Confirm.ask", return_value=True):
        result = _invoke(cfg, ("failed",))

    assert result.exit_code == 0
    assert _failed(cfg) == []


def test_clear_rejects_unknown_table(cfg):
    result = _invoke(cfg, ("bogus", "--yes"))

    assert result.exit_code != 0  # click.Choice rejects it


def test_clear_returns_early_without_a_config():
    result = _invoke(None, ("all", "--yes"), obj_config=None)

    assert result.exit_code == 0
