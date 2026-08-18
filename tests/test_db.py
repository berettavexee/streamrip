import os
import tempfile

import pytest

import streamrip.db as db


@pytest.fixture
def tmp_path_str():
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


class TestDummy:
    def test_contains_always_false(self):
        d = db.Dummy()
        assert d.contains(id="abc") is False

    def test_add_noop(self):
        d = db.Dummy()
        d.add(("abc",))  # should not raise

    def test_remove_noop(self):
        d = db.Dummy()
        d.remove("abc")  # positional — should not raise

    def test_all_empty(self):
        d = db.Dummy()
        assert d.all() == []


class TestDownloads:
    def test_add_and_contains(self, tmp_path_str):
        path = os.path.join(tmp_path_str, "downloads.db")
        d = db.Downloads(path)
        d.add(("track-123",))
        assert d.contains(id="track-123") is True

    def test_contains_missing(self, tmp_path_str):
        path = os.path.join(tmp_path_str, "downloads.db")
        d = db.Downloads(path)
        assert d.contains(id="nonexistent") is False

    def test_all_returns_added(self, tmp_path_str):
        path = os.path.join(tmp_path_str, "downloads.db")
        d = db.Downloads(path)
        d.add(("id-a",))
        d.add(("id-b",))
        rows = d.all()
        ids = [r[0] for r in rows]
        assert "id-a" in ids
        assert "id-b" in ids

    def test_reset_deletes_file(self, tmp_path_str):
        path = os.path.join(tmp_path_str, "downloads.db")
        d = db.Downloads(path)
        d.add(("id-x",))
        assert os.path.exists(path)
        d.reset()
        assert not os.path.exists(path)

    def test_duplicate_add_idempotent(self, tmp_path_str):
        path = os.path.join(tmp_path_str, "downloads.db")
        d = db.Downloads(path)
        d.add(("same-id",))
        d.add(("same-id",))  # unique constraint — should not raise
        assert len(d.all()) == 1

    def test_persistent_across_instances(self, tmp_path_str):
        path = os.path.join(tmp_path_str, "downloads.db")
        d1 = db.Downloads(path)
        d1.add(("persist-me",))
        d2 = db.Downloads(path)
        assert d2.contains(id="persist-me") is True


class TestFailed:
    def test_add_and_all(self, tmp_path_str):
        path = os.path.join(tmp_path_str, "failed.db")
        f = db.Failed(path)
        f.add(("deezer", "track", "fail-1"))
        rows = f.all()
        assert len(rows) == 1
        assert rows[0] == ("deezer", "track", "fail-1")

    def test_contains_by_id(self, tmp_path_str):
        path = os.path.join(tmp_path_str, "failed.db")
        f = db.Failed(path)
        f.add(("qobuz", "album", "album-42"))
        assert f.contains(id="album-42") is True
        assert f.contains(id="unknown") is False

    def test_reset_deletes_file(self, tmp_path_str):
        path = os.path.join(tmp_path_str, "failed.db")
        f = db.Failed(path)
        f.add(("tidal", "track", "t1"))
        assert os.path.exists(path)
        f.reset()
        assert not os.path.exists(path)

    def test_remove_by_id_leaves_other_rows(self, tmp_path_str):
        """`rip repair` clears repaired items one id at a time."""
        path = os.path.join(tmp_path_str, "failed.db")
        f = db.Failed(path)
        f.add(("deezer", "track", "1"))
        f.add(("deezer", "track", "2"))
        f.add(("qobuz", "track", "3"))

        f.remove(id="2")

        assert [row[2] for row in f.all()] == ["1", "3"]

    def test_remove_unknown_id_is_a_noop(self, tmp_path_str):
        """Repair removes ids it believes succeeded; a stale one must not raise."""
        path = os.path.join(tmp_path_str, "failed.db")
        f = db.Failed(path)
        f.add(("deezer", "track", "1"))

        f.remove(id="not-there")

        assert len(f.all()) == 1


class TestDatabaseBaseValidation:
    def test_dummy_create_noop(self):
        d = db.Dummy()
        d.create()  # should not raise

    def test_empty_structure_raises(self, tmp_path_str):
        from typing import ClassVar

        class EmptyStructure(db.DatabaseBase):
            structure: ClassVar[dict] = {}
            name = "test"

        path = os.path.join(tmp_path_str, "es.db")
        with pytest.raises(ValueError, match="structure must not be empty"):
            EmptyStructure(path)

    def test_empty_name_raises(self, tmp_path_str):
        from typing import ClassVar

        class EmptyName(db.DatabaseBase):
            structure: ClassVar[dict] = {"id": ["text"]}
            name = ""

        path = os.path.join(tmp_path_str, "en.db")
        with pytest.raises(ValueError, match="name must not be empty"):
            EmptyName(path)

    def test_empty_path_raises(self):
        with pytest.raises(ValueError, match="path must not be empty"):
            db.Downloads("")

    def test_keys_returns_column_names(self, tmp_path_str):
        path = os.path.join(tmp_path_str, "d.db")
        d = db.Downloads(path)
        assert "id" in d.keys()

    def test_contains_invalid_key_raises(self, tmp_path_str):
        path = os.path.join(tmp_path_str, "d.db")
        d = db.Downloads(path)
        with pytest.raises(ValueError, match="Invalid key"):
            d.contains(bad_key="x")

    def test_add_wrong_count_raises(self, tmp_path_str):
        path = os.path.join(tmp_path_str, "d.db")
        d = db.Downloads(path)
        with pytest.raises(ValueError, match="Expected 1 values"):
            d.add(())

    def test_remove_deletes_row(self, tmp_path_str):
        path = os.path.join(tmp_path_str, "d.db")
        d = db.Downloads(path)
        d.add(("to-remove",))
        assert d.contains(id="to-remove")
        d.remove(id="to-remove")
        assert not d.contains(id="to-remove")

    def test_reset_when_file_missing_is_silent(self, tmp_path_str):
        from unittest.mock import patch

        path = os.path.join(tmp_path_str, "d.db")
        d = db.Downloads(path)
        with patch("streamrip.db.os.remove", side_effect=FileNotFoundError):
            d.reset()  # must not raise


class TestDatabase:
    def test_downloaded_and_set_downloaded(self, tmp_path_str):
        dl_path = os.path.join(tmp_path_str, "dl.db")
        fail_path = os.path.join(tmp_path_str, "fail.db")
        database = db.Database(db.Downloads(dl_path), db.Failed(fail_path))
        assert database.downloaded("abc") is False
        database.set_downloaded("abc")
        assert database.downloaded("abc") is True

    def test_set_failed_and_get(self, tmp_path_str):
        dl_path = os.path.join(tmp_path_str, "dl.db")
        fail_path = os.path.join(tmp_path_str, "fail.db")
        database = db.Database(db.Downloads(dl_path), db.Failed(fail_path))
        database.set_failed("deezer", "track", "bad-id")
        rows = database.get_failed_downloads()
        assert len(rows) == 1
        assert rows[0] == ("deezer", "track", "bad-id")
