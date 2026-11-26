import sqlite3

import pytest

from imagetrace import db as dbmod
from imagetrace.audit import list_events, verify_chain
from imagetrace.cases import CaseNotFoundError, CaseRepository, ValidationError
from imagetrace.db import SCHEMA_VERSION, Database, database_path, ensure_data_layout, open_database
from imagetrace.evidence import EvidenceNotFoundError, EvidenceRepository
from imagetrace.notes import (
    CONFIDENCE_LABELS,
    MAX_NOTE_TEXT,
    MAX_SOURCE_REF,
    NOTE_CATEGORIES,
    NoteNotFoundError,
    NotesRepository,
    validate_note_inputs,
)
from synthetic_images import make_valid_jpeg

SCOPE = "Synthetic analyst notes test scope."


@pytest.fixture
def env(tmp_path):
    d = open_database(tmp_path)
    cases = CaseRepository(d, tmp_path / "cases", actor=lambda: "Analyst Ada")
    case = cases.create("Notes test case", SCOPE)
    ev_repo = EvidenceRepository(d, tmp_path, actor=lambda: "Analyst Ada")
    ev = ev_repo.import_file(case.id, make_valid_jpeg(tmp_path / "sample.jpg"))
    notes = NotesRepository(d, actor=lambda: "Analyst Ada")
    yield d, cases, ev_repo, notes, case, ev, tmp_path
    d.close()


# --- validation ---------------------------------------------------------------------------
def test_validate_note_inputs_rules():
    # Confirmed from file & Corroborated require source_reference
    for strict_label in ("Confirmed from file", "Corroborated"):
        with pytest.raises(ValidationError, match="source / citation"):
            validate_note_inputs("Observed tag", NOTE_CATEGORIES[0], strict_label, "")
        text, cat, conf, src = validate_note_inputs(
            "  Observed tag  ", NOTE_CATEGORIES[0], strict_label, "  EXIF:Make  "
        )
        assert (text, cat, conf, src) == (
            "Observed tag",
            NOTE_CATEGORIES[0],
            strict_label,
            "EXIF:Make",
        )

    # Analyst observation & Unverified hypothesis allow blank source_reference
    for flex_label in ("Analyst observation", "Unverified hypothesis"):
        text, _cat, conf, src = validate_note_inputs(
            "Visible blue sign\r\nsecond line", NOTE_CATEGORIES[1], flex_label, ""
        )
        assert text == "Visible blue sign\nsecond line"
        assert conf == flex_label
        assert src == ""

    # Empty, control characters, length limits, bad enums
    with pytest.raises(ValidationError):
        validate_note_inputs("   ", NOTE_CATEGORIES[0], "Analyst observation", "")
    with pytest.raises(ValidationError, match="control"):
        validate_note_inputs("bad\x00text", NOTE_CATEGORIES[0], "Analyst observation", "")
    with pytest.raises(ValidationError, match="control"):
        validate_note_inputs("ok", NOTE_CATEGORIES[0], "Confirmed from file", "bad\x07src")
    with pytest.raises(ValidationError, match="too long"):
        validate_note_inputs(
            "x" * (MAX_NOTE_TEXT + 1), NOTE_CATEGORIES[0], "Analyst observation", ""
        )
    with pytest.raises(ValidationError, match="too long"):
        validate_note_inputs(
            "ok", NOTE_CATEGORIES[0], "Confirmed from file", "s" * (MAX_SOURCE_REF + 1)
        )
    with pytest.raises(ValidationError, match="category"):
        validate_note_inputs("ok", "Unknown category", "Analyst observation", "")
    with pytest.raises(ValidationError, match="confidence"):
        validate_note_inputs("ok", NOTE_CATEGORIES[0], "100% Certain", "")


# --- schema v5 -> v6 migration ------------------------------------------------------------
def test_schema_v5_migrates_cleanly_to_v6(tmp_path):
    ensure_data_layout(tmp_path)
    db_file = database_path(tmp_path)
    d5 = Database(db_file)
    for ver in (1, 2, 3, 4, 5):
        with d5.transaction() as con:
            for stmt in dbmod.MIGRATIONS[ver]:
                con.execute(stmt)
            con.execute(f"PRAGMA user_version = {ver}")
    # Populate a case + evidence item on schema v5
    cases5 = CaseRepository(d5, tmp_path / "cases", actor=lambda: "Tester")
    case = cases5.create("Pre-v6 case", SCOPE)
    ev5 = EvidenceRepository(d5, tmp_path, actor=lambda: "Tester").import_file(
        case.id, make_valid_jpeg(tmp_path / "legacy.jpg")
    )
    assert d5.schema_version == 5
    d5.close()

    # Reopen with open_database -> should backup v5 and migrate to latest schema (>= v6)
    d6 = open_database(tmp_path)
    try:
        assert d6.schema_version == SCHEMA_VERSION
        assert d6.schema_version >= 6
        backups = list((tmp_path / "backups").glob("imagetrace-pre-migration-v5-*.sqlite"))
        assert len(backups) == 1
        tables = {r[0] for r in d6.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "notes" in tables
        triggers = {
            r[0] for r in d6.con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
        assert "notes_no_delete" in triggers
        # Existing case & evidence preserved
        assert CaseRepository(d6, tmp_path / "cases").get(case.id) is not None
        assert EvidenceRepository(d6, tmp_path).get(ev5.id) is not None
        # Notes work immediately after migration
        notes = NotesRepository(d6, actor=lambda: "Tester")
        created = notes.create(
            case.id,
            ev5.id,
            note_text="Post-migration note",
            category=NOTE_CATEGORIES[0],
            confidence_label="Analyst observation",
        )
        assert created.id == 1
        assert verify_chain(d6.con).ok
    finally:
        d6.close()


# --- create, revise, retract, append-only & privacy ---------------------------------------
def test_create_revise_retract_and_append_only_protection(env):
    d, _cases, _ev_repo, notes, case, ev, _root = env

    marker_text = "SENSITIVE_OBSERVATION_MARKER_7781"
    marker_src = "SENSITIVE_CITATION_MARKER_8892"
    n1 = notes.create(
        case.id,
        ev.id,
        note_text=marker_text,
        category="Visible landmark",
        confidence_label="Confirmed from file",
        source_reference=marker_src,
    )
    assert n1.author_label == "Analyst Ada"
    assert n1.status == "active"
    assert n1.supersedes_note_id is None
    assert n1.created_at.endswith("Z")
    assert n1.note_text == marker_text
    assert n1.source_reference == marker_src

    # Audit log must record creation WITHOUT leaking raw note text or citation
    events = list_events(d.con, evidence_id=ev.id)
    assert events[0].event_type == "note_created"
    assert events[0].actor_label == "Analyst Ada"
    assert events[0].details["note_id"] == n1.id
    assert events[0].details["confidence_label"] == "Confirmed from file"
    assert events[0].details["has_source_reference"] is True
    raw_audit = str(d.con.execute("SELECT details_json FROM audit_events").fetchall())
    assert marker_text not in raw_audit
    assert marker_src not in raw_audit

    # No-op revise returns same note without creating an extra row or event
    ev_count_before = len(list_events(d.con, evidence_id=ev.id))
    same = notes.revise(
        n1.id,
        note_text=marker_text,
        category="Visible landmark",
        confidence_label="Confirmed from file",
        source_reference=marker_src,
    )
    assert same.id == n1.id
    assert len(list_events(d.con, evidence_id=ev.id)) == ev_count_before

    # Real revise supersedes n1 and inserts linked n2
    n2 = notes.revise(
        n1.id,
        note_text="Revised wording: structure appears to be a coastal beacon.",
        category="Object / structure",
        confidence_label="Unverified hypothesis",
        source_reference="",
    )
    assert n2.id != n1.id
    assert n2.status == "active"
    assert n2.supersedes_note_id == n1.id
    old_reloaded = notes.get_required(n1.id)
    assert old_reloaded.status == "superseded"
    assert old_reloaded.note_text == marker_text  # original text preserved untouched

    assert [n.id for n in notes.list_for_evidence(ev.id)] == [n2.id]
    assert [n.id for n in notes.list_for_evidence(ev.id, include_history=True)] == [n2.id, n1.id]

    # Superseded note cannot be revised again
    with pytest.raises(ValidationError, match="Only active notes"):
        notes.revise(
            n1.id,
            note_text="Attempt",
            category=NOTE_CATEGORIES[0],
            confidence_label="Analyst observation",
        )

    # Retract active revision (marks retracted, never deletes)
    retracted = notes.retract(n2.id)
    assert retracted.status == "retracted"
    assert notes.list_for_evidence(ev.id) == []
    assert [n.id for n in notes.list_for_evidence(ev.id, include_history=True)] == [n2.id, n1.id]
    # Retracting again is idempotent
    ev_count_after_retract = len(list_events(d.con, evidence_id=ev.id))
    assert notes.retract(n2.id).status == "retracted"
    assert len(list_events(d.con, evidence_id=ev.id)) == ev_count_after_retract

    # SQLite trigger blocks DELETE FROM notes
    with pytest.raises(sqlite3.IntegrityError, match="append-only"), d.transaction() as con:
        con.execute("DELETE FROM notes WHERE id = ?", (n1.id,))

    # No note text leaked anywhere in audit_events
    all_details = str(d.con.execute("SELECT details_json FROM audit_events").fetchall())
    assert "coastal beacon" not in all_details
    assert marker_text not in all_details
    assert verify_chain(d.con).ok


# --- target checks & case archiving -------------------------------------------------------
def test_archived_case_and_invalid_targets_rejected(env, tmp_path):
    _d, cases, ev_repo, notes, case, ev, _root = env
    other_case = cases.create("Second case", SCOPE)
    other_ev = ev_repo.import_file(other_case.id, make_valid_jpeg(tmp_path / "other.jpg"))

    with pytest.raises(ValidationError, match="does not belong"):
        notes.create(
            case.id,
            other_ev.id,
            note_text="Wrong pair",
            category=NOTE_CATEGORIES[0],
            confidence_label="Analyst observation",
        )
    with pytest.raises(CaseNotFoundError):
        notes.create(
            "C-20260101-NOPE00",
            None,
            note_text="Missing case",
            category=NOTE_CATEGORIES[0],
            confidence_label="Analyst observation",
        )
    with pytest.raises(EvidenceNotFoundError):
        notes.create(
            case.id,
            "E-20260101-NOPE00",
            note_text="Missing ev",
            category=NOTE_CATEGORIES[0],
            confidence_label="Analyst observation",
        )
    with pytest.raises(NoteNotFoundError):
        notes.get_required(99999)

    active = notes.create(
        case.id,
        ev.id,
        note_text="Before archive",
        category=NOTE_CATEGORIES[0],
        confidence_label="Analyst observation",
    )
    cases.archive(case.id)

    with pytest.raises(ValidationError, match="archived"):
        notes.create(
            case.id,
            ev.id,
            note_text="Blocked",
            category=NOTE_CATEGORIES[0],
            confidence_label="Analyst observation",
        )
    with pytest.raises(ValidationError, match="archived"):
        notes.revise(
            active.id,
            note_text="Blocked revision",
            category=NOTE_CATEGORIES[0],
            confidence_label="Analyst observation",
        )
    with pytest.raises(ValidationError, match="archived"):
        notes.retract(active.id)


# --- counts by confidence & case-level notes ----------------------------------------------
def test_counts_by_confidence_only_count_active_notes(env):
    _d, _cases, _ev_repo, notes, case, ev, _root = env

    n_conf = notes.create(
        case.id,
        ev.id,
        note_text="File tag confirmed",
        category="Metadata / technical finding",
        confidence_label="Confirmed from file",
        source_reference="EXIF:DateTimeOriginal",
    )
    n_corr = notes.create(
        case.id,
        None,  # case-level note without specific evidence item
        note_text="Corroborated across two case photos",
        category="General observation",
        confidence_label="Corroborated",
        source_reference="Cross-check of two imported files",
    )
    n_obs = notes.create(
        case.id,
        ev.id,
        note_text="Visible street bollard pattern",
        category="Object / structure",
        confidence_label="Analyst observation",
    )
    n_hyp = notes.create(
        case.id,
        ev.id,
        note_text="Could be a municipal ferry pier",
        category="Visible landmark",
        confidence_label="Unverified hypothesis",
    )
    # Revise n_obs into a second Corroborated note, and retract n_hyp
    notes.revise(
        n_obs.id,
        note_text="Bollard design matches harbour spec sheet",
        category="Object / structure",
        confidence_label="Corroborated",
        source_reference="Public harbour design manual p. 12",
    )
    notes.retract(n_hyp.id)

    counts = notes.counts_by_confidence(case.id)
    assert list(counts.keys()) == list(CONFIDENCE_LABELS)
    assert counts == {
        "Confirmed from file": 1,
        "Corroborated": 2,
        "Analyst observation": 0,
        "Unverified hypothesis": 0,
    }
    assert notes.count_for_case(case.id) == 3
    assert len(notes.list_for_case(case.id)) == 3
    assert len(notes.list_for_case(case.id, include_history=True)) == 5
    assert n_conf.id and n_corr.evidence_id is None
