from datetime import date

import pytest

from imagetrace.audit import list_events, verify_chain
from imagetrace.cases import (
    CASE_ID_RE,
    CASE_SUBDIRS,
    MAX_TAGS,
    MAX_TITLE,
    CaseNotFoundError,
    CaseRepository,
    ValidationError,
    case_dir,
    new_case_id,
    normalize_tags,
    validate_text,
    validate_title,
)
from imagetrace.db import open_database

SCOPE = "Synthetic test scope: authorized examination of test fixtures only."


@pytest.fixture
def env(tmp_path):
    d = open_database(tmp_path)
    repo = CaseRepository(d, tmp_path / "cases", actor=lambda: "Tester")
    yield repo, tmp_path
    d.close()


def test_new_case_id_format():
    cid = new_case_id(date(2026, 11, 24))
    assert CASE_ID_RE.fullmatch(cid)
    assert cid.startswith("C-20261124-")


def test_create_case(env):
    repo, root = env
    case = repo.create("Test case one", SCOPE, notes="first", tags="alpha, Beta, alpha")
    assert CASE_ID_RE.fullmatch(case.id)
    assert case.title == "Test case one"
    assert case.status == "open"
    assert case.tags == ("alpha", "Beta")
    assert case.created_at == case.updated_at and case.created_at.endswith("Z")
    for sub in CASE_SUBDIRS:
        assert (root / "cases" / case.id / sub).is_dir()
    events = list_events(repo.db.con, case.id)
    assert [e.event_type for e in events] == ["case_created"]
    assert events[0].actor_label == "Tester"


@pytest.mark.parametrize(
    ("title", "scope", "message"),
    [
        ("", SCOPE, "Title is required"),
        ("   ", SCOPE, "Title is required"),
        ("line1\nline2", SCOPE, "single line"),
        ("x" * (MAX_TITLE + 1), SCOPE, "too long"),
        ("ok", "", "Scope / authorization declaration is required"),
        ("ok", "bad\x00scope", "control characters"),
    ],
)
def test_create_validation(env, title, scope, message):
    repo, _ = env
    with pytest.raises(ValidationError, match=message):
        repo.create(title, scope)
    assert repo.count() == 0
    assert list_events(repo.db.con) == []


def test_tag_rules():
    assert normalize_tags("  a ,, b  c ,A") == ("a", "b c")
    assert normalize_tags(["x", "X", " y "]) == ("x", "y")
    assert normalize_tags(None) == ()
    with pytest.raises(ValidationError):
        normalize_tags("x" * 41)
    with pytest.raises(ValidationError):
        normalize_tags([f"t{i}" for i in range(MAX_TAGS + 1)])
    with pytest.raises(ValidationError):
        normalize_tags("bad\x07tag")


def test_text_normalisation():
    assert validate_text(" a\r\nb ", "Notes") == "a\nb"
    assert validate_title("  Title  ") == "Title"


def test_update_records_field_names_only(env):
    repo, _ = env
    case = repo.create("Original", SCOPE, notes="secret detail")
    updated = repo.update(case.id, title="Renamed", notes="another secret", tags=["t1"])
    assert updated.title == "Renamed" and updated.tags == ("t1",)
    assert updated.updated_at >= case.updated_at
    ev = list_events(repo.db.con, case.id)[0]
    assert ev.event_type == "case_updated"
    assert ev.details == {"fields": ["notes", "tags", "title"]}
    assert "secret" not in str(ev.details)


def test_noop_update_creates_no_event(env):
    repo, _ = env
    case = repo.create("Same", SCOPE)
    repo.update(case.id, title="Same", scope_text=SCOPE)
    assert len(list_events(repo.db.con, case.id)) == 1


def test_update_validation_leaves_case_unchanged(env):
    repo, _ = env
    case = repo.create("Keep", SCOPE)
    with pytest.raises(ValidationError):
        repo.update(case.id, title="", notes="changed")
    assert repo.get(case.id).notes == ""


def test_archive_and_reopen(env):
    repo, _ = env
    case = repo.create("Arch", SCOPE)
    assert repo.archive(case.id).status == "archived"
    assert repo.list() == []
    assert [c.id for c in repo.list(include_archived=True)] == [case.id]
    assert repo.reopen(case.id).status == "open"
    types = [e.event_type for e in reversed(list_events(repo.db.con, case.id))]
    assert types == ["case_created", "case_archived", "case_reopened"]
    with pytest.raises(ValidationError):
        repo.set_status(case.id, "deleted")


def test_unknown_case(env):
    repo, _ = env
    with pytest.raises(CaseNotFoundError):
        repo.update("C-20260101-ABCDEF", title="x")


def test_search(env):
    repo, _ = env
    a = repo.create("Harbour photos", SCOPE, tags="batch-1")
    b = repo.create("Receipt scan", SCOPE, notes="100% match_test")
    assert [c.id for c in repo.list("harbour")] == [a.id]
    assert [c.id for c in repo.list("BATCH-1")] == [a.id]
    assert [c.id for c in repo.list(a.id[-6:])] == [a.id]
    # LIKE wildcards are matched literally
    assert [c.id for c in repo.list("100%")] == [b.id]
    assert [c.id for c in repo.list("h_test")] == [b.id]
    # unescaped, "%" / "_" would match every case; escaped, only b (which contains them) matches
    assert [c.id for c in repo.list("%")] == [b.id]
    assert [c.id for c in repo.list("_")] == [b.id]


def test_recent_orders_by_update(env):
    repo, _ = env
    a = repo.create("A", SCOPE)
    b = repo.create("B", SCOPE)
    repo.update(a.id, notes="touch")
    assert [c.id for c in repo.recent()] == [a.id, b.id]


def test_persistence_across_restart(tmp_path):
    d = open_database(tmp_path)
    repo = CaseRepository(d, tmp_path / "cases")
    case = repo.create("Persist me", SCOPE, tags="x")
    d.close()
    d2 = open_database(tmp_path)
    try:
        again = CaseRepository(d2, tmp_path / "cases").get(case.id)
        assert again == case
        assert verify_chain(d2.con).ok
    finally:
        d2.close()


def test_id_collision_retries(env):
    repo, _ = env
    ids = iter(["C-20260101-AAAAAA", "C-20260101-AAAAAA", "C-20260101-BBBBBB"])
    repo.id_factory = lambda: next(ids)
    first = repo.create("one", SCOPE)
    second = repo.create("two", SCOPE)
    assert (first.id, second.id) == ("C-20260101-AAAAAA", "C-20260101-BBBBBB")


def test_bad_generated_id_rejected(env):
    repo, _ = env
    repo.id_factory = lambda: "../../evil"
    with pytest.raises(ValidationError):
        repo.create("x", SCOPE)


@pytest.mark.parametrize("bad", ["../x", "C-20260101-ABCDEF/../..", "", "c-20260101-abcdef"])
def test_case_dir_rejects_unsafe_ids(tmp_path, bad):
    with pytest.raises(ValidationError):
        case_dir(tmp_path, bad)


def test_case_dir_ok(tmp_path):
    assert case_dir(tmp_path, "C-20260101-ABCDEF") == (tmp_path / "C-20260101-ABCDEF").resolve()
