"""Tests for streamrip/progress.py."""

from unittest.mock import MagicMock

import pytest

import streamrip.progress as progress_module
from streamrip.progress import Handle, ProgressManager, get_progress_callback

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pm():
    """ProgressManager with its Live session mocked out (no terminal I/O)."""
    manager = ProgressManager()
    manager.live = MagicMock()
    manager.progress = MagicMock()
    manager.progress.add_task.return_value = 42  # fake task id
    return manager


@pytest.fixture(autouse=True)
def _reset_global_p(pm):
    """Replace the module-level _p with a mocked one for each test."""
    original = progress_module._p
    progress_module._p = pm
    yield
    progress_module._p = original


# ---------------------------------------------------------------------------
# Handle
# ---------------------------------------------------------------------------


def test_handle_enter_returns_update():
    updates = []
    h = Handle(update=updates.append, done=lambda: None)
    with h as cb:
        cb(10)
        cb(20)
    assert updates == [10, 20]


def test_handle_exit_calls_done():
    called = []
    h = Handle(update=lambda _: None, done=lambda: called.append(True))
    with h:
        pass
    assert called == [True]


def test_handle_exit_called_even_on_exception():
    called = []
    h = Handle(update=lambda _: None, done=lambda: called.append(True))
    with pytest.raises(RuntimeError):
        with h:
            raise RuntimeError("oops")
    assert called == [True]


# ---------------------------------------------------------------------------
# ProgressManager.get_callback
# ---------------------------------------------------------------------------


def test_get_callback_starts_live_on_first_call(pm):
    pm.get_callback(total=100, desc="track")
    pm.live.start.assert_called_once()
    assert pm.started is True


def test_get_callback_does_not_restart_live(pm):
    pm.get_callback(total=100, desc="first")
    pm.get_callback(total=200, desc="second")
    pm.live.start.assert_called_once()


def test_get_callback_returns_handle(pm):
    result = pm.get_callback(total=100, desc="track")
    assert isinstance(result, Handle)


def test_get_callback_update_advances_task(pm):
    handle = pm.get_callback(total=100, desc="track")
    handle.update(50)
    pm.progress.update.assert_any_call(42, advance=50)


def test_get_callback_done_hides_task(pm):
    handle = pm.get_callback(total=100, desc="track")
    handle.done()
    pm.progress.update.assert_any_call(42, visible=False)


def test_get_callback_live_update_called_on_advance(pm):
    handle = pm.get_callback(total=100, desc="track")
    handle.update(1)
    pm.live.update.assert_called()


# ---------------------------------------------------------------------------
# ProgressManager.cleanup
# ---------------------------------------------------------------------------


def test_cleanup_stops_live_when_started(pm):
    pm.started = True
    pm.cleanup()
    pm.live.stop.assert_called_once()


def test_cleanup_noop_when_not_started(pm):
    pm.started = False
    pm.cleanup()
    pm.live.stop.assert_not_called()


# ---------------------------------------------------------------------------
# ProgressManager.add_title / remove_title
# ---------------------------------------------------------------------------


def test_add_title_strips_and_appends(pm):
    pm.add_title("  hello  ")
    assert "hello" in pm.task_titles


def test_remove_title_removes_existing(pm):
    pm.task_titles = ["alpha", "beta"]
    pm.remove_title("alpha")
    assert "alpha" not in pm.task_titles
    assert "beta" in pm.task_titles


def test_remove_title_strips_whitespace(pm):
    pm.task_titles = ["gamma"]
    pm.remove_title("  gamma  ")
    assert pm.task_titles == []


def test_remove_title_ignores_missing(pm):
    pm.task_titles = ["alpha"]
    pm.remove_title("nonexistent")  # must not raise
    assert pm.task_titles == ["alpha"]


# ---------------------------------------------------------------------------
# ProgressManager.gen_title_text / get_title_text
# ---------------------------------------------------------------------------


def test_gen_title_text_returns_rule(pm):
    from rich.rule import Rule

    assert isinstance(pm.gen_title_text(), Rule)


def test_gen_title_text_truncates_beyond_three(pm):
    for title in ["a", "b", "c", "d"]:
        pm.task_titles.append(title)
    rule = pm.gen_title_text()
    # The ellipsis suffix is included in the rendered text
    assert "..." in str(rule.title)


def test_get_title_text_returns_cached_rule(pm):
    result = pm.get_title_text()
    assert result is pm._text_cache


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def test_get_progress_callback_disabled_returns_noop_handle():
    handle = get_progress_callback(enabled=False, total=100, desc="x")
    assert isinstance(handle, Handle)
    handle.update(99)  # must not raise
    handle.done()  # must not raise


def test_get_progress_callback_enabled_delegates_to_p(pm):
    handle = get_progress_callback(enabled=True, total=100, desc="track")
    assert isinstance(handle, Handle)
    pm.live.start.assert_called_once()


def test_add_title_delegates_to_p(pm):
    progress_module.add_title("Album X")
    assert "Album X" in pm.task_titles


def test_remove_title_delegates_to_p(pm):
    pm.task_titles = ["Album X"]
    progress_module.remove_title("Album X")
    assert "Album X" not in pm.task_titles


def test_clear_progress_stops_live(pm):
    pm.started = True
    progress_module.clear_progress()
    pm.live.stop.assert_called_once()
