import sqlite3

import pytest

from imagetrace import db as dbmod
from imagetrace.db import (
    SCHEMA_VERSION,
    Database,
    DatabaseError,
    MigrationError,
    NewerSchemaError,
    database_path,
    ensure_data_layout,
    migrate,
    open_database,
)


@pytest.fixture
def database(tmp_path):
    d = open_database(tmp_path)
    yield d
    d.close()


def test_open_creates_layout_and_schema(tmp_path):
    d = open_database(tmp_path)
    try:
        for sub in dbmod.DATA_SUBDIRS:
            assert (tmp_path / sub).is_dir()
        assert database_path(tmp_path).is_file()
        assert d.schema_version == SCHEMA_VERSION
        tables = {r[0] for r in d.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "cases",
            "evidence",
            "analysis_runs",
            "metadata_records",
            "gps_records",
            "ocr_runs",
            "ocr_items",
            "notes",
            "exports",
            "audit_events",
            "settings",
        } <= tables
        assert d.con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert d.integrity_check() == []
    finally:
        d.close()


def test_missing_root_is_not_created(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(DatabaseError):
        ensure_data_layout(missing)
    assert not missing.exists()


def test_data_persists_across_reopen(tmp_path):
    d = open_database(tmp_path)
    with d.transaction() as con:
        con.execute("INSERT INTO settings VALUES ('k', '1', 'now')")
    d.close()
    d2 = open_database(tmp_path)
    try:
        assert d2.con.execute("SELECT value FROM settings WHERE key='k'").fetchone()[0] == "1"
    finally:
        d2.close()


def test_transaction_rolls_back_on_error(database):
    with pytest.raises(RuntimeError), database.transaction() as con:
        con.execute("INSERT INTO settings VALUES ('a', '1', 'now')")
        raise RuntimeError("boom")
    assert database.con.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0
    assert not database.con.in_transaction


def test_nested_transaction_rejected(database):
    with database.transaction(), pytest.raises(DatabaseError), database.transaction():
        pass


def test_foreign_keys_enforced(database):
    with pytest.raises(sqlite3.IntegrityError), database.transaction() as con:
        con.execute(
            "INSERT INTO audit_events (case_id, event_type, event_time, actor_label, event_hash) "
            "VALUES ('C-20260101-ABCDEF', 'x', 'now', 'a', 'h')"
        )


def test_newer_schema_refused_and_untouched(tmp_path):
    ensure_data_layout(tmp_path)
    raw = sqlite3.connect(database_path(tmp_path))
    raw.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
    raw.close()
    with pytest.raises(NewerSchemaError):
        open_database(tmp_path)
    raw = sqlite3.connect(database_path(tmp_path))
    assert raw.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION + 5
    raw.close()


def test_failed_migration_rolls_back_and_backs_up_first(tmp_path, monkeypatch):
    d = open_database(tmp_path)
    with d.transaction() as con:
        con.execute("INSERT INTO settings VALUES ('keep', '\"me\"', 'now')")
    bad = dict(dbmod.MIGRATIONS)
    bad[SCHEMA_VERSION + 1] = ("CREATE TABLE ok_table (x INTEGER)", "THIS IS NOT SQL")
    monkeypatch.setattr(dbmod, "MIGRATIONS", bad)
    with pytest.raises(MigrationError) as info:
        migrate(d, tmp_path / "backups", target=SCHEMA_VERSION + 1)
    assert "nothing was discarded" in str(info.value)
    assert d.schema_version == SCHEMA_VERSION
    tables = {r[0] for r in d.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ok_table" not in tables  # partial step rolled back
    assert d.con.execute("SELECT value FROM settings WHERE key='keep'").fetchone()[0] == '"me"'
    backups = list((tmp_path / "backups").glob("imagetrace-pre-migration-v*.sqlite"))
    assert len(backups) == 1
    d.close()


def test_successful_future_migration(tmp_path, monkeypatch):
    d = open_database(tmp_path)
    extra = dict(dbmod.MIGRATIONS)
    extra[SCHEMA_VERSION + 1] = ("CREATE TABLE future_table (x INTEGER)",)
    monkeypatch.setattr(dbmod, "MIGRATIONS", extra)
    result = migrate(d, tmp_path / "backups", target=SCHEMA_VERSION + 1)
    assert (result.from_version, result.to_version) == (SCHEMA_VERSION, SCHEMA_VERSION + 1)
    assert result.backup_path is not None and result.backup_path.is_file()
    assert d.schema_version == SCHEMA_VERSION + 1
    d.close()


def test_corrupt_database_file_raises(tmp_path):
    ensure_data_layout(tmp_path)
    database_path(tmp_path).write_bytes(b"this is not a sqlite database" * 100)
    with pytest.raises((DatabaseError, sqlite3.DatabaseError)):
        open_database(tmp_path)
    # the corrupt file is left in place for the user to inspect
    assert database_path(tmp_path).read_bytes().startswith(b"this is not")


def test_close_is_idempotent(tmp_path):
    d = Database(tmp_path / "x.sqlite")
    d.close()
    d.close()
    assert d.closed
