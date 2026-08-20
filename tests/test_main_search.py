"""Tests for the non-interactive search flows of rip.main.Main.

``search_interactive`` drives a terminal menu and is left to manual testing, but
``search_take_first`` (feeling-lucky dispatch) and ``search_output_file`` (dump
results as JSON) are plain, mockable orchestration and were previously uncovered.

Main is instantiated via ``__new__`` to bypass the config/database setup in
``__init__``; only the attributes each method touches are supplied.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from streamrip.rip.main import Main


def _main_with_client(pages):
    """Return a Main whose logged-in client returns *pages* from search()."""
    main = Main.__new__(Main)
    client = MagicMock()
    client.search = AsyncMock(return_value=pages)
    main.get_logged_in_client = AsyncMock(return_value=client)
    main.add_by_id = AsyncMock()
    return main, client


def _search_results(results=None, as_list=None):
    fake = MagicMock()
    fake.results = results if results is not None else []
    fake.as_list = MagicMock(return_value=as_list if as_list is not None else [])
    return fake


class TestSearchTakeFirst:
    async def test_no_pages_does_not_dispatch(self):
        main, client = _main_with_client(pages=[])
        await main.search_take_first("deezer", "track", "query")
        client.search.assert_awaited_once_with("track", "query", limit=1)
        main.add_by_id.assert_not_called()

    async def test_empty_results_does_not_dispatch(self):
        main, _ = _main_with_client(pages=[{"data": []}])
        with patch("streamrip.rip.main.SearchResults") as sr:
            sr.from_pages.return_value = _search_results(results=[])
            await main.search_take_first("deezer", "track", "query")
        main.add_by_id.assert_not_called()

    async def test_dispatches_first_result_by_id(self):
        main, _ = _main_with_client(pages=[{"data": [{"id": 42}]}])
        first = MagicMock()
        first.id = "42"
        first.media_type = MagicMock(return_value="track")
        with patch("streamrip.rip.main.SearchResults") as sr:
            sr.from_pages.return_value = _search_results(results=[first])
            await main.search_take_first("deezer", "track", "query")
        main.add_by_id.assert_awaited_once_with("deezer", "track", "42")


class TestSearchOutputFile:
    async def test_no_pages_writes_nothing(self, tmp_path):
        main, _ = _main_with_client(pages=[])
        out = tmp_path / "results.json"
        await main.search_output_file("deezer", "track", "query", str(out), 100)
        assert not out.exists()

    async def test_writes_results_as_json(self, tmp_path):
        main, client = _main_with_client(pages=[{"data": [{"id": 1}]}])
        payload = [{"id": "1", "title": "Song"}]
        out = tmp_path / "results.json"
        with patch("streamrip.rip.main.SearchResults") as sr:
            sr.from_pages.return_value = _search_results(results=[MagicMock()], as_list=payload)
            await main.search_output_file("deezer", "album", "query", str(out), 25)
        client.search.assert_awaited_once_with("album", "query", limit=25)
        assert json.loads(out.read_text()) == payload
