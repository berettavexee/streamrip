"""Tests for streamrip/media/semaphore.py."""

import asyncio
from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

import streamrip.media.semaphore as sem_module
from streamrip.media.semaphore import global_download_semaphore


@pytest.fixture(autouse=True)
def _reset_global():
    """Reset module-level globals before and after each test."""
    sem_module._global_semaphore = None
    yield
    sem_module._global_semaphore = None


def _config(concurrency=True, max_connections=3):
    cfg = MagicMock()
    cfg.concurrency = concurrency
    cfg.max_connections = max_connections
    return cfg


# ---------------------------------------------------------------------------
# Unlimited (nullcontext) path
# ---------------------------------------------------------------------------


def test_returns_nullcontext_when_concurrency_disabled_and_unlimited():
    """concurrency=False → max_connections=1, not unlimited."""
    # A negative max_connections with concurrency=True → unlimited
    result = global_download_semaphore(_config(concurrency=True, max_connections=-1))
    assert isinstance(result, nullcontext)


def test_unlimited_returns_module_singleton():
    result = global_download_semaphore(_config(concurrency=True, max_connections=-1))
    assert result is sem_module._unlimited


# ---------------------------------------------------------------------------
# Semaphore creation
# ---------------------------------------------------------------------------


def test_creates_semaphore_with_correct_value():
    result = global_download_semaphore(_config(concurrency=True, max_connections=5))
    assert isinstance(result, asyncio.Semaphore)
    # Internal _value reflects the initial count
    assert result._value == 5


def test_concurrency_disabled_creates_semaphore_of_one():
    result = global_download_semaphore(_config(concurrency=False, max_connections=99))
    assert isinstance(result, asyncio.Semaphore)
    assert result._value == 1


def test_global_semaphore_is_reused_on_second_call():
    first = global_download_semaphore(_config(concurrency=True, max_connections=4))
    second = global_download_semaphore(_config(concurrency=True, max_connections=4))
    assert first is second


def test_global_semaphore_state_stored():
    global_download_semaphore(_config(concurrency=True, max_connections=6))
    assert sem_module._global_semaphore is not None
    count, sem = sem_module._global_semaphore
    assert count == 6
    assert isinstance(sem, asyncio.Semaphore)


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_raises_on_conflicting_max_connections():
    global_download_semaphore(_config(concurrency=True, max_connections=4))
    with pytest.raises(ValueError, match="Conflicting semaphore values"):
        global_download_semaphore(_config(concurrency=True, max_connections=8))


def test_semaphore_usable_as_async_context_manager():
    """The returned Semaphore can actually be used as an async context manager."""

    async def _use():
        sem = global_download_semaphore(_config(concurrency=True, max_connections=2))
        async with sem:
            pass

    asyncio.run(_use())
