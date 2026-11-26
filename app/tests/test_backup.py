import shutil
import sqlite3
from collections import namedtuple

import pytest

from imagetrace import backup as backupmod
from imagetrace.audit import list_events, verify_chain
from imagetrace.backup import (
    BackupError,
    backup_database,
    list_backups,
    restore_database,
    sidecar_path,
    verify_backup,
)
from imagetrace.cases import CaseRepository
from imagetrace.db import open_database
from imagetrace.hashing import sha256_file

SCOPE = "Synthetic test scope."


@pytest.fixture
def env(tmp_path):
    d = open_database(tmp_path)
    repo = CaseRepository(d, tmp_path / "cases")
    yield d, repo, tmp_path / "backups"
    d.close()


def test_backup_creates_verified_file_and_sidecar(env):
    d, repo, backups = env
    repo.create("Backed up", SCOPE)
    result = backup_database(d, backups)
    assert result.path.is_file() and result.path.parent == backups
    assert result.path.name.startswith("imagetrace-manual-")
    assert sha256_file(result.path) == result.sha256
    assert sidecar_path(result.path).read_text().startswith(result.sha256)
    assert not list(backups.glob("*.partial"))
    check = verify_backup(result.path)
    assert check.ok, check.message
    assert "SHA-256 matches" in check.message
    assert list_backups(backups) == [result.path]


def test_backup_content_matches(env):
    d, repo, backups = env
    case = repo.create("Copy check", SCOPE)
    result = backup_database(d, backups)
    con = sqlite3.connect(result.path)
    try:
        assert con.execute("SELECT title FROM cases WHERE id = ?", (case.id,)).fetchone()[0] == (
            "Copy check"
        )
    finally:
        con.close()


def test_tampered_backup_detected(env):
    d, _repo, backups = env
    result = backup_database(d, backups)
    with result.path.open("r+b") as fh:
        fh.seek(200)
        fh.write(b"\x00garbage\x00")
    check = verify_backup(result.path)
    assert not check.ok
    assert "SHA-256 does not match" in check.message


def test_non_sqlite_and_foreign_db_rejected(tmp_path):
    junk = tmp_path / "junk.sqlite"
    junk.write_bytes(b"not a database" * 200)
    assert not verify_backup(junk).ok
    other = tmp_path / "other.sqlite"
    con = sqlite3.connect(other)
    con.execute("CREATE TABLE t (x)")
    con.close()
    check = verify_backup(other)
    assert not check.ok and "not an ImageTrace database" in check.message
    assert not verify_backup(tmp_path / "missing.sqlite").ok


def test_low_space_refuses_backup(env, monkeypatch):
    d, _repo, backups = env
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: usage(10**12, 0, 1024))
    with pytest.raises(BackupError, match="Not enough free space"):
        backup_database(d, backups)
    assert not list(backups.glob("*"))


def test_invalid_label(env):
    d, _repo, backups = env
    with pytest.raises(ValueError):
        backup_database(d, backups, label="../evil")


def test_restore_round_trip(env):
    d, repo, backups = env
    a = repo.create("Case A", SCOPE)
    snap = backup_database(d, backups)
    b = repo.create("Case B", SCOPE)
    pre = restore_database(d, snap.path, backups, actor="Tester")
    ids = {c.id for c in repo.list(include_archived=True)}
    assert ids == {a.id}
    assert pre.is_file() and "pre-restore" in pre.name
    con = sqlite3.connect(pre)  # the replaced state (with case B) was preserved
    try:
        assert con.execute("SELECT COUNT(*) FROM cases WHERE id = ?", (b.id,)).fetchone()[0] == 1
    finally:
        con.close()
    assert list_events(d.con)[0].event_type == "database_restored"
    assert verify_chain(d.con).ok
    assert d.integrity_check() == []


def test_restore_refuses_bad_backup(env, tmp_path):
    d, repo, backups = env
    repo.create("Keep", SCOPE)
    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"garbage" * 100)
    with pytest.raises(BackupError, match="not restored"):
        restore_database(d, bad, backups, actor="Tester")
    assert repo.count() == 1
    assert not list(backups.glob("imagetrace-pre-restore-*"))


def test_backup_without_sidecar_still_verifies(env):
    d, _repo, backups = env
    result = backup_database(d, backups)
    sidecar_path(result.path).unlink()
    check = verify_backup(result.path)
    assert check.ok and "not verified" in check.message


def test_partial_file_removed_on_failure(env, monkeypatch):
    d, _repo, backups = env
    monkeypatch.setattr(backupmod, "_inspect", lambda _p: (["corrupt page"], 1, True))
    with pytest.raises(BackupError, match="integrity"):
        backup_database(d, backups)
    assert not list(backups.glob("*"))
