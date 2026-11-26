import json
import shutil
import sqlite3
import urllib.request
from collections import namedtuple

import pytest

from imagetrace import db as dbmod
from imagetrace.audit import list_events, verify_chain
from imagetrace.cases import CaseRepository
from imagetrace.db import SCHEMA_VERSION, Database, database_path, ensure_data_layout, open_database
from imagetrace.evidence import EvidenceRepository
from imagetrace.gps import GpsService
from imagetrace.hashing import sha256_file
from imagetrace.metadata import MetadataService
from imagetrace.notes import NotesRepository
from imagetrace.ocr import OcrService
from imagetrace.reports import (
    REPORT_LEGEND,
    REPORT_SCHEMA_VERSION,
    ReportError,
    ReportOptions,
    ReportService,
    validate_report_payload,
)
from imagetrace.tools_status import find_exiftool
from synthetic_images import make_valid_jpeg

SCOPE = "Synthetic Phase 8 report test scope."

TSV_WITH_UNTRUSTED = "\n".join(
    [
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
        "5\t1\t1\t1\t1\t1\t10\t12\t90\t20\t93.0\t<b>UNTRUSTED</b>",
        "5\t1\t1\t1\t1\t2\t105\t12\t80\t20\t88.5\tPIER&GATE",
        "5\t1\t1\t1\t2\t1\t10\t40\t120\t20\t91.0\tOCR_MARKER_8811",
    ]
)


def _make_jpeg_with_gps(path):
    """Create a synthetic JPEG with embedded EXIF + GPS tags via bundled ExifTool (if present)."""
    make_valid_jpeg(path)
    et = find_exiftool()
    if et is not None:
        import subprocess

        subprocess.run(  # noqa: S603 (test fixture helper)
            [
                str(et),
                "-overwrite_original",
                "-q",
                "-q",
                "-Make=SYNTH-Cam & Co <Test>",
                "-Model=SYNTH-M1",
                "-DateTimeOriginal=2026:05:10 14:30:00",
                "-GPSLatitude=51.476852",
                "-GPSLatitudeRef=N",
                "-GPSLongitude=0.000500",
                "-GPSLongitudeRef=W",
                "-GPSAltitude=47.0",
                "-GPSAltitudeRef=Above Sea Level",
                "--",
                str(path),
            ],
            check=True,
            timeout=20,
        )
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Hard-block all network calls: report generation must be 100% offline.
    def _no_net(*_a, **_k):
        raise AssertionError("Network call attempted during offline report generation!")

    monkeypatch.setattr(urllib.request, "urlopen", _no_net)

    d = open_database(tmp_path)
    cases = CaseRepository(d, tmp_path / "cases", actor=lambda: "Examiner Ray")
    ev_repo = EvidenceRepository(d, tmp_path, actor=lambda: "Examiner Ray")
    meta = MetadataService(d, ev_repo, actor=lambda: "Examiner Ray")
    gps = GpsService(d, ev_repo, meta, actor=lambda: "Examiner Ray")
    ocr = OcrService(
        d,
        ev_repo,
        actor=lambda: "Examiner Ray",
        runner=lambda *_: ("Tesseract", "5.4.0-synth", TSV_WITH_UNTRUSTED),
    )
    notes = NotesRepository(d, actor=lambda: "Examiner Ray")
    reports = ReportService(
        d, tmp_path, cases, ev_repo, meta, gps, ocr, notes, actor=lambda: "Examiner Ray"
    )
    yield d, cases, ev_repo, meta, gps, ocr, notes, reports, tmp_path
    d.close()


# --- migration v6 -> v7 -------------------------------------------------------------------
def test_schema_v6_migrates_cleanly_to_v7(tmp_path):
    ensure_data_layout(tmp_path)
    db_file = database_path(tmp_path)
    d6 = Database(db_file)
    for ver in (1, 2, 3, 4, 5, 6):
        with d6.transaction() as con:
            for stmt in dbmod.MIGRATIONS[ver]:
                con.execute(stmt)
            con.execute(f"PRAGMA user_version = {ver}")
    cases6 = CaseRepository(d6, tmp_path / "cases", actor=lambda: "Tester")
    case = cases6.create("Pre-v7 case", SCOPE)
    ev6 = EvidenceRepository(d6, tmp_path, actor=lambda: "Tester").import_file(
        case.id, make_valid_jpeg(tmp_path / "legacy.jpg")
    )
    NotesRepository(d6, actor=lambda: "Tester").create(
        case.id,
        ev6.id,
        note_text="Legacy note",
        category="General observation",
        confidence_label="Analyst observation",
    )
    assert d6.schema_version == 6
    d6.close()

    d7 = open_database(tmp_path)
    try:
        assert d7.schema_version == SCHEMA_VERSION
        assert d7.schema_version >= 7
        backups = list((tmp_path / "backups").glob("imagetrace-pre-migration-v6-*.sqlite"))
        assert len(backups) == 1
        tables = {r[0] for r in d7.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "exports" in tables
        triggers = {
            r[0] for r in d7.con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
        assert "exports_no_delete" in triggers
        assert CaseRepository(d7, tmp_path / "cases").get(case.id) is not None
        assert EvidenceRepository(d7, tmp_path).get(ev6.id) is not None
        assert len(NotesRepository(d7).list_for_case(case.id)) == 1
        assert verify_chain(d7.con).ok
    finally:
        d7.close()


# --- payload validation -------------------------------------------------------------------
def test_validate_report_payload_rejects_incomplete_or_invalid_structures():
    problems = validate_report_payload({})
    assert len(problems) >= 6

    bad_payload = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": "2026-11-24T12:00:00Z",
        "app_version": "ImageTrace 0.1.0",
        "legend": dict(REPORT_LEGEND),
        "limitations": ["Limit 1"],
        "tools": [{"name": "ImageTrace", "version": "0.1.0"}],
        "case": {"id": "C-20261124-AAAAAA", "title": "Case"},
        "evidence": [
            {
                "id": "E-20261124-BBBBBB",
                "original_filename": "x.jpg",
                "detected_format": "JPEG",
                "imported_at": "2026-11-24T11:00:00Z",
                "sha256": "not-a-sha256",
                "file_size": -1,
                "gps": {"normalized": {}},  # missing raw_fields
                "ocr": {"has_run": True, "engine": "Tesseract", "classification": "Fact"},
                "notes": [{"confidence_label": "Proven", "author_label": "", "created_at": ""}],
            }
        ],
        "audit_summary": {"chain_ok": True, "events": []},
    }
    errs = validate_report_payload(bad_payload)
    joined = "\n".join(errs).lower()
    assert "sha-256" in joined
    assert "file_size" in joined
    assert "raw_fields" in joined
    assert "automated" in joined
    assert "confidence_label" in joined


# --- full JSON & PDF pipeline, escaping, GPS-only-when-present & verification -------------
def test_json_and_pdf_reports_provenance_escaping_and_verification(env, tmp_path):
    d, cases, ev_repo, meta, gps, ocr, notes, reports, _root = env
    case = cases.create(
        "Harbour & Pier <Test> Case",
        "Authorized synthetic examination <scope & notes>.",
        tags="synthetic, report",
    )
    # Evidence 1: EXIF + GPS + OCR (with hostile XML/HTML) + revised & active analyst notes
    ev1 = ev_repo.import_file(
        case.id,
        _make_jpeg_with_gps(tmp_path / "harbour_01.jpg"),
        source_note="Synthetic <test> & provenance",
    )
    has_exiftool = find_exiftool() is not None
    if has_exiftool:
        meta.extract(ev1.id)
        gps.parse(ev1.id, extract_if_missing=False)
    ocr.run_ocr(ev1.id, language="eng")
    n1 = notes.create(
        case.id,
        ev1.id,
        note_text="Initial note <tag> & detail NOTE_MARKER_4491",
        category="Visible landmark",
        confidence_label="Confirmed from file",
        source_reference="EXIF:IFD0:Make & top-left sign",
    )
    notes.revise(
        n1.id,
        note_text="Revised active note: <b>untrusted</b> & verified NOTE_MARKER_4492",
        category="Signage / visible text",
        confidence_label="Corroborated",
        source_reference="OCR word #1 & visual inspection",
    )
    notes.create(
        case.id,
        None,  # case-level note
        note_text="Case-level hypothesis <unverified> & pending",
        category="General observation",
        confidence_label="Unverified hypothesis",
    )

    # Evidence 2: plain image with NO metadata extraction, NO GPS, NO OCR
    ev2 = ev_repo.import_file(case.id, make_valid_jpeg(tmp_path / "plain_02.jpg"))

    # 1. Generate JSON report (entire case, full metadata + OCR text)
    json_rec = reports.generate_json(
        case.id,
        options=ReportOptions(
            include_ocr_text=True, include_full_metadata=True, include_note_history=False
        ),
    )
    json_path = reports.resolve_path(json_rec)
    assert json_path.is_file()
    assert json_rec.path.startswith(f"cases/{case.id}/reports/")
    assert sha256_file(json_path) == json_rec.sha256
    sidecar = json_path.with_name(json_path.name + ".sha256")
    assert sidecar.is_file()
    assert sidecar.read_text(encoding="utf-8").startswith(json_rec.sha256)

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert validate_report_payload(data) == []
    assert data["report_schema_version"] == REPORT_SCHEMA_VERSION
    assert set(data["legend"]) == set(REPORT_LEGEND)
    assert len(data["limitations"]) >= 5
    assert data["case"]["id"] == case.id
    assert len(data["case_notes"]) == 1
    assert data["audit_summary"]["chain_ok"] is True

    by_id = {e["id"]: e for e in data["evidence"]}
    assert set(by_id) == {ev1.id, ev2.id}
    e1 = by_id[ev1.id]
    e2 = by_id[ev2.id]
    assert e1["sha256"] == ev1.sha256
    assert e1["integrity_check"]["status"] == "matched"
    if has_exiftool:
        assert e1["metadata"]["has_run"] is True
        assert "embedded_records" in e1["metadata"]
        assert "gps" in e1
        assert e1["gps"]["normalized"]["latitude"] == pytest.approx(51.476852, abs=1e-4)
        assert e1["gps"]["normalized"]["longitude"] == pytest.approx(-0.0005, abs=1e-4)
        assert e1["gps"]["raw_fields"]
    assert e1["ocr"]["has_run"] is True
    assert "automated" in e1["ocr"]["classification"].lower()
    assert "<b>UNTRUSTED</b>" in e1["ocr"]["full_text_untrusted"]
    assert len(e1["notes"]) == 1  # active revision only
    assert e1["notes"][0]["supersedes_note_id"] == n1.id

    # GPS section MUST be omitted when no GPS exists
    assert "gps" not in e2
    assert e2["metadata"]["has_run"] is False
    assert e2["ocr"]["has_run"] is False

    # 2. Generate PDF report (must handle all <script>/<b>/& strings without XML crash!)
    pdf_rec = reports.generate_pdf(
        case.id,
        options=ReportOptions(
            include_ocr_text=True, include_full_metadata=True, include_note_history=True
        ),
    )
    pdf_path = reports.resolve_path(pdf_rec)
    assert pdf_path.is_file()
    pdf_bytes = pdf_path.read_bytes()
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 2000
    assert sha256_file(pdf_path) == pdf_rec.sha256

    # 3. Single-evidence report with OCR text omitted & note history included
    single_rec = reports.generate_json(
        case.id,
        evidence_id=ev1.id,
        options=ReportOptions(
            include_ocr_text=False, include_full_metadata=False, include_note_history=True
        ),
    )
    single_data = json.loads(reports.resolve_path(single_rec).read_text(encoding="utf-8"))
    assert len(single_data["evidence"]) == 1
    assert single_data["evidence"][0]["ocr"]["full_text_included"] is False
    assert "full_text_untrusted" not in single_data["evidence"][0]["ocr"]
    assert len(single_data["evidence"][0]["notes"]) == 2  # active + superseded

    # 4. Verify exports (OK, modified file mismatch, corrupted sidecar, missing file)
    v_ok = reports.verify_export(pdf_rec.id)
    assert v_ok.ok is True
    assert v_ok.sidecar_ok is True

    # Tamper with JSON file bytes
    json_path.write_bytes(json_path.read_bytes() + b" ")
    v_tampered = reports.verify_export(json_rec.id)
    assert v_tampered.ok is False
    assert "MISMATCH" in v_tampered.message

    # Corrupt PDF sidecar
    pdf_sidecar = pdf_path.with_name(pdf_path.name + ".sha256")
    pdf_sidecar.write_text("0" * 64 + f"  {pdf_path.name}\n", encoding="utf-8")
    v_bad_sc = reports.verify_export(pdf_rec.id)
    assert v_bad_sc.ok is False
    assert v_bad_sc.sidecar_ok is False

    # Missing file
    reports.resolve_path(single_rec).unlink()
    v_missing = reports.verify_export(single_rec.id)
    assert v_missing.ok is False
    assert "not found" in v_missing.message.lower()

    # 5. Append-only exports trigger & privacy (no raw OCR or note text in audit_events)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"), d.transaction() as con:
        con.execute("DELETE FROM exports WHERE id = ?", (pdf_rec.id,))

    all_audit = str(d.con.execute("SELECT details_json FROM audit_events").fetchall())
    assert "OCR_MARKER_8811" not in all_audit
    assert "NOTE_MARKER_4491" not in all_audit
    assert "NOTE_MARKER_4492" not in all_audit
    ev_types = [e.event_type for e in list_events(d.con, case.id)]
    assert "report_exported" in ev_types
    assert "export_verified" in ev_types
    assert verify_chain(d.con).ok


# --- low-space guard ----------------------------------------------------------------------
def test_export_rejects_low_free_space(env, monkeypatch):
    _d, cases, _ev_repo, _meta, _gps, _ocr, _notes, reports, _root = env
    case = cases.create("Low space case", SCOPE)
    usage = namedtuple("usage", ["total", "used", "free"])
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: usage(10**9, 10**9 - 1024, 1024))
    with pytest.raises(ReportError, match="Insufficient free space"):
        reports.generate_json(case.id)
    assert reports.list_exports(case.id) == []
