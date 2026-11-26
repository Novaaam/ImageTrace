import json

import pytest

from imagetrace.db import open_database
from imagetrace.settings import (
    Settings,
    SettingsStore,
    import_legacy_settings,
    legacy_json_path,
    settings_from_dict,
)


@pytest.fixture
def database(tmp_path):
    d = open_database(tmp_path)
    yield d
    d.close()


def test_defaults(database):
    s = SettingsStore(database).load()
    assert s == Settings()
    assert s.allow_network_features is False
    assert s.active_case_id is None


def test_roundtrip_persists(tmp_path):
    d = open_database(tmp_path)
    SettingsStore(d).save(Settings(theme="dark", analyst_label="  JD  ", active_case_id="C-1"))
    d.close()
    d2 = open_database(tmp_path)
    try:
        s = SettingsStore(d2).load()
        assert (s.theme, s.analyst_label, s.active_case_id) == ("dark", "JD", "C-1")
    finally:
        d2.close()


def test_memory_store_without_database():
    store = SettingsStore(None)
    assert not store.persistent
    store.save(Settings(theme="light"))
    assert store.load().theme == "light"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"theme": "neon"}, Settings()),
        ({"allow_network_features": 1}, Settings()),
        ({"allow_network_features": "yes"}, Settings()),
        ({"allow_external_map": "yes"}, Settings()),
        ({"allow_external_map": True}, Settings(allow_external_map=True)),
        ({"analyst_label": ""}, Settings()),
        ({"analyst_label": "x" * 100}, Settings(analyst_label="x" * 60)),
        ({"active_case_id": 5}, Settings()),
        ({"unknown": True}, Settings()),
        ({"max_import_mb": 0}, Settings()),
        ({"max_import_mb": 9999}, Settings()),
        ({"max_import_mb": True}, Settings()),
        ({"max_image_megapixels": -5}, Settings()),
        (
            {"max_import_mb": 25, "max_image_megapixels": 80},
            Settings(max_import_mb=25, max_image_megapixels=80),
        ),
    ],
)
def test_sanitising(raw, expected):
    assert settings_from_dict(raw) == expected


def test_unreadable_db_value_ignored(database):
    with database.transaction() as con:
        con.execute("INSERT INTO settings VALUES ('theme', '{not json', 'now')")
    assert SettingsStore(database).load().theme == "system"


def test_legacy_json_imported_once_and_kept(tmp_path, database):
    path = legacy_json_path(tmp_path)
    path.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    store = SettingsStore(database)
    assert import_legacy_settings(store, tmp_path) is True
    assert store.load().theme == "dark"
    assert path.is_file()  # never deleted
    path.write_text(json.dumps({"theme": "light"}), encoding="utf-8")
    assert import_legacy_settings(store, tmp_path) is False  # db already has settings
    assert store.load().theme == "dark"


def test_legacy_corrupt_json_ignored(tmp_path, database):
    legacy_json_path(tmp_path).write_text("{oops", encoding="utf-8")
    assert import_legacy_settings(SettingsStore(database), tmp_path) is False
