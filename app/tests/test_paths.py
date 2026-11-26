import shutil
from collections import namedtuple
from pathlib import Path

import pytest

from imagetrace import paths


def test_project_root_contains_blueprint():
    assert (paths.project_root() / "BLUEPRINT.md").is_file()


def test_default_data_root_is_project_data():
    assert paths.default_data_root() == paths.project_root() / "data"


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.ENV_DATA_ROOT, str(tmp_path))
    assert paths.default_data_root() == tmp_path


def test_remembered_location(tmp_path):
    chosen = tmp_path / "elsewhere"
    paths.save_configured_data_root(chosen)
    assert paths.configured_data_root() == chosen
    assert paths.default_data_root() == chosen


def test_env_beats_remembered_location(monkeypatch, tmp_path):
    paths.save_configured_data_root(tmp_path / "remembered")
    monkeypatch.setenv(paths.ENV_DATA_ROOT, str(tmp_path / "env"))
    assert paths.default_data_root() == tmp_path / "env"


@pytest.mark.parametrize("content", ["{bad json", "[]", '{"data_root": 5}', '{"data_root": " "}'])
def test_bad_local_config_ignored(content):
    paths.local_config_path().write_text(content, encoding="utf-8")
    assert paths.configured_data_root() is None


def test_missing_root_is_unavailable_and_not_created(tmp_path):
    missing = tmp_path / "nope"
    st = paths.check_storage(missing)
    assert not st.available
    assert "not found" in st.message
    assert not missing.exists()  # never created as a side effect


def test_file_as_root_is_unavailable(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    assert not paths.check_storage(f).available


def test_existing_root_reports_space(tmp_path):
    st = paths.check_storage(tmp_path)
    assert st.available
    assert st.free_bytes is not None and st.free_bytes > 0


def test_low_space_flag(monkeypatch, tmp_path):
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: usage(100 * 1024**3, 0, 1024**3))
    st = paths.check_storage(tmp_path)
    assert st.available and st.low_space
    assert "Low free space" in st.message


@pytest.mark.parametrize(
    ("n", "expected"),
    [(None, "unknown"), (0, "0 B"), (1023, "1023 B"), (1024, "1.0 KB"), (5 * 1024**3, "5.0 GB")],
)
def test_format_bytes(n, expected):
    assert paths.format_bytes(n) == expected


def test_tests_temp_is_on_project_drive(tmp_path: Path):
    # pyproject sets --basetemp under data/temp so tests don't write to C:\...\Temp
    assert tmp_path.resolve().drive.upper() == paths.project_root().drive.upper()
