import sqlite3
from itertools import pairwise

import pytest

from imagetrace.audit import append_event, compute_event_hash, list_events, verify_chain
from imagetrace.db import open_database


@pytest.fixture
def database(tmp_path):
    d = open_database(tmp_path)
    yield d
    d.close()


def _add(d, n=3):
    for i in range(n):
        with d.transaction() as con:
            append_event(con, "test_event", actor_label="tester", details={"i": i})


def test_append_requires_transaction(database):
    with pytest.raises(RuntimeError):
        append_event(database.con, "x", actor_label="a")


def test_chain_links_and_verifies(database):
    _add(database, 4)
    events = list(reversed(list_events(database.con)))
    assert len(events) == 4
    assert events[0].previous_event_hash is None
    for prev, cur in pairwise(events):
        assert cur.previous_event_hash == prev.event_hash
    report = verify_chain(database.con)
    assert report.ok and report.checked == 4


def test_empty_chain_ok(database):
    assert verify_chain(database.con).ok


def test_update_and_delete_blocked(database):
    _add(database, 1)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"), database.transaction() as c:
        c.execute("UPDATE audit_events SET actor_label = 'someone else'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"), database.transaction() as c:
        c.execute("DELETE FROM audit_events")


def test_tampering_detected_when_triggers_bypassed(database):
    _add(database, 3)
    with database.transaction() as con:
        con.execute("DROP TRIGGER audit_events_no_update")
        con.execute("UPDATE audit_events SET details_json = '{\"i\": 99}' WHERE id = 2")
    report = verify_chain(database.con)
    assert not report.ok
    assert report.first_bad_id == 2


def test_deleted_event_detected(database):
    _add(database, 3)
    with database.transaction() as con:
        con.execute("DROP TRIGGER audit_events_no_delete")
        con.execute("DELETE FROM audit_events WHERE id = 2")
    report = verify_chain(database.con)
    assert not report.ok and report.first_bad_id == 3


def test_hash_is_deterministic():
    fields = {
        "previous_event_hash": None,
        "case_id": None,
        "evidence_id": None,
        "event_type": "x",
        "event_time": "2026-01-01T00:00:00.000000Z",
        "actor_label": "a",
        "details_json": "{}",
    }
    assert compute_event_hash(fields) == compute_event_hash(dict(fields))
    assert compute_event_hash(fields) != compute_event_hash({**fields, "actor_label": "b"})
