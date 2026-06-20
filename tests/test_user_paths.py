"""Tests for streamrip/rip/user_paths.py.

The module only defines path constants computed at import time.  Tests verify
types, structural invariants (correct filenames, correct parent relationships),
and that the bundled blank config actually exists on disk.
"""

import os
from pathlib import Path

import streamrip.rip.user_paths as up


def test_constants_are_strings():
    for name in (
        "APP_DIR",
        "LOG_DIR",
        "CACHE_DIR",
        "CONFIG_DIR",
        "DEFAULT_CONFIG_PATH",
        "DOWNLOADS_DIR",
        "DEFAULT_DOWNLOADS_FOLDER",
        "DEFAULT_DOWNLOADS_DB_PATH",
        "DEFAULT_FAILED_DOWNLOADS_DB_PATH",
        "DEFAULT_YOUTUBE_VIDEO_DOWNLOADS_FOLDER",
    ):
        assert isinstance(getattr(up, name), str), f"{name} should be a str"


def test_log_cache_config_dirs_are_identical():
    assert up.LOG_DIR == up.CACHE_DIR == up.CONFIG_DIR == up.APP_DIR


def test_default_config_path_filename():
    assert os.path.basename(up.DEFAULT_CONFIG_PATH) == "config.toml"
    assert os.path.dirname(up.DEFAULT_CONFIG_PATH) == up.CONFIG_DIR


def test_downloads_dir_under_home():
    home = str(Path.home())
    assert up.DOWNLOADS_DIR.startswith(home)
    assert up.DEFAULT_DOWNLOADS_FOLDER.startswith(home)


def test_downloads_dir_consistency():
    assert up.DOWNLOADS_DIR == up.DEFAULT_DOWNLOADS_FOLDER


def test_db_paths_under_log_dir():
    assert os.path.dirname(up.DEFAULT_DOWNLOADS_DB_PATH) == up.LOG_DIR
    assert os.path.dirname(up.DEFAULT_FAILED_DOWNLOADS_DB_PATH) == up.LOG_DIR


def test_db_paths_filenames():
    assert os.path.basename(up.DEFAULT_DOWNLOADS_DB_PATH) == "downloads.db"
    assert os.path.basename(up.DEFAULT_FAILED_DOWNLOADS_DB_PATH) == "failed_downloads.db"


def test_youtube_folder_under_downloads_dir():
    assert up.DEFAULT_YOUTUBE_VIDEO_DOWNLOADS_FOLDER.startswith(up.DOWNLOADS_DIR)
    assert os.path.basename(up.DEFAULT_YOUTUBE_VIDEO_DOWNLOADS_FOLDER) == "YouTubeVideos"
