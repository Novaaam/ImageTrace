import json
from datetime import UTC, datetime

import pytest

from imagetrace import db as dbmod
from imagetrace.audit import list_events, verify_chain
from imagetrace.cases import CaseRepository
from imagetrace.db import SCHEMA_VERSION, open_database
from imagetrace.evidence import EvidenceRepository
from imagetrace.hashing import sha256_file
from imagetrace.metadata import (
    ExifToolNotFoundError,
    MetadataService,
    analyze_timestamps,
    classify_key,
    parse_timestamp_value,
    query_exiftool_version,
)
from imagetrace.tools_status import find_exiftool
from synthetic_images import (
    make_exif_jpeg_with_exiftool,
    make_malformed_exif_jpeg,
    make_valid_jpeg,
    make_valid_png,
)

SCOPE = "Synthetic metadata test scope."


@pytest.fixture
def exiftool_exe():
    exe = find_exiftool()
    assert exe is not None and exe.is_file(), "Bundled ExifTool 13.64 expected on D-drive"
    return exe


@pytest.fixture
def env(tmp_path):
    d = open_database(tmp_path)
    cases = CaseRepository(d, tmp_path / "cases", actor=lambda: "Tester")
    case = cases.create("Metadata test case", SCOPE)
    ev_repo = EvidenceRepository(d, tmp_path, actor=lambda: "Tester")
    meta = MetadataService(d, ev_repo, actor=lambda: "Tester")
    yield meta, ev_repo, case, tmp_path
    d.close()


# --- pure unit tests (classification & timestamp normalization) ---------------------------
@pytest.mark.parametrize(
    ("key", "expected_cat", "expected_origin", "expected_field"),
    [
        ("EXIF:IFD0:Make", "EXIF", "embedded", "Make"),
        ("EXIF:ExifIFD:DateTimeOriginal", "EXIF", "embedded", "DateTimeOriginal"),
        ("EXIF:GPS:GPSLatitude", "GPS", "embedded", "GPSLatitude"),
        ("IPTC:Keywords", "IPTC", "embedded", "Keywords"),
        ("XMP:XMP-xmp:CreatorTool", "XMP", "embedded", "CreatorTool"),
        ("ICC_Profile:ProfileDescription", "ICC_Profile", "embedded", "ProfileDescription"),
        ("PNG:Software", "PNG", "embedded", "Software"),
        ("PNG:ImageWidth", "File/Container", "file_container", "ImageWidth"),
        ("JFIF:JFIFVersion", "File/Container", "file_container", "JFIFVersion"),
        ("File:FileType", "File/Container", "file_container", "FileType"),
        ("File:System:FileSize", "File/System", "application_system", "FileSize"),
        ("Composite:ImageSize", "Composite", "application_system", "ImageSize"),
        ("ExifTool:ExifToolVersion", "Tool", "application_system", "ExifToolVersion"),
    ],
)
def test_classify_key(key, expected_cat, expected_origin, expected_field):
    _group, cat, origin, field = classify_key(key)
    assert (cat, origin, field) == (expected_cat, expected_origin, expected_field)


def test_parse_timestamp_cases():
    # Valid with offset
    ts = parse_timestamp_value("EXIF:ExifIFD:DateTimeOriginal", "2026:05:10 14:30:00", "+03:00")
    assert ts.normalized == "2026-05-10T14:30:00+03:00"
    assert ts.has_timezone and ts.problem is None

    # Valid without timezone
    ts_notz = parse_timestamp_value("EXIF:ExifIFD:DateTimeOriginal", "2026:05:10 14:30:00")
    assert "timezone unknown" in ts_notz.normalized
    assert not ts_notz.has_timezone and ts_notz.problem is None

    # Zero placeholder
    ts_zero = parse_timestamp_value("EXIF:IFD0:ModifyDate", "0000:00:00 00:00:00")
    assert ts_zero.dt is None and ts_zero.problem and "zero/placeholder" in ts_zero.problem

    # Impossible calendar date
    ts_bad = parse_timestamp_value("EXIF:IFD0:ModifyDate", "2026:13:45 99:00:00")
    assert ts_bad.dt is None and ts_bad.problem and "impossible" in ts_bad.problem

    # Invalid timezone offset
    ts_badtz = parse_timestamp_value("EXIF:IFD0:ModifyDate", "2026:05:10 14:30:00", "+99:00")
    assert ts_badtz.dt is None and ts_badtz.problem and "timezone" in ts_badtz.problem


def test_analyze_timestamps_detects_missing_tz_contradictions_and_future():
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    raw = {
        "EXIF:ExifIFD:DateTimeOriginal": "2026:05:10 14:30:00",
        "EXIF:ExifIFD:OffsetTimeOriginal": "+00:00",
        "EXIF:ExifIFD:CreateDate": "2026:05:12 10:00:00",
        "EXIF:ExifIFD:OffsetTimeDigitized": "+00:00",
        "EXIF:IFD0:ModifyDate": "2026:05:09 08:00:00",
        "EXIF:ExifIFD:OffsetTime": "+00:00",
        "XMP:XMP-xmp:ModifyDate": "2027:01:01 00:00:00",  # naive + future!
    }
    overrides, warns = analyze_timestamps(raw, now_utc=now)
    assert overrides["EXIF:ExifIFD:DateTimeOriginal"] == "2026-05-10T14:30:00Z"
    joined = "\n".join(warns).lower()
    assert "earlier than" in joined
    assert "differs from" in joined
    assert "future" in joined
    assert "timezone" in joined
    # Must report discrepancies, never assert proven tampering
    assert "not a conclusion" in joined


# --- migration v2 -> v3 -------------------------------------------------------------------
def test_migration_v2_to_v3_preserves_evidence_and_backs_up(tmp_path):
    dbmod.ensure_data_layout(tmp_path)
    d2 = dbmod.Database(dbmod.database_path(tmp_path))
    dbmod.migrate(d2, tmp_path / "backups", target=2)
    assert d2.schema_version == 2
    case = CaseRepository(d2, tmp_path / "cases").create("Legacy v2 case", SCOPE)
    src = make_valid_jpeg(tmp_path / "legacy.jpg")
    ev = EvidenceRepository(d2, tmp_path).import_file(case.id, src)
    d2.close()

    d3 = open_database(tmp_path)
    try:
        assert d3.schema_version == SCHEMA_VERSION
        assert EvidenceRepository(d3, tmp_path).get(ev.id) == ev
        tables = {r[0] for r in d3.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"analysis_runs", "metadata_records"} <= tables
        assert len(list((tmp_path / "backups").glob("imagetrace-pre-migration-v2-*.sqlite"))) == 1
        assert d3.integrity_check() == []
    finally:
        d3.close()


# --- integration tests with real ExifTool -------------------------------------------------
def test_query_exiftool_version(exiftool_exe):
    ver = query_exiftool_version(exiftool_exe)
    assert ver.startswith("13.")


def test_extract_complete_exif_with_tz_is_reproducible(env, exiftool_exe, tmp_path):
    meta, ev_repo, case, root = env
    src = make_exif_jpeg_with_exiftool(
        tmp_path / "synth_full.jpg",
        exiftool_exe,
        {
            "IFD0:Make": "SynthCam",
            "IFD0:Model": "Model-400",
            "ExifIFD:LensModel": "Synth 35mm f/2",
            "IFD0:Orientation#": "6",
            "IFD0:Software": "SynthOS 2.1",
            "ExifIFD:DateTimeOriginal": "2026:05:10 14:30:00",
            "ExifIFD:OffsetTimeOriginal": "+03:00",
            "ExifIFD:CreateDate": "2026:05:10 14:30:00",
            "ExifIFD:OffsetTimeDigitized": "+03:00",
            "IFD0:ModifyDate": "2026:05:10 14:30:00",
            "ExifIFD:OffsetTime": "+03:00",
        },
    )
    ev = ev_repo.import_file(case.id, src)
    orig_sha = ev.sha256

    res1 = meta.extract(ev.id)
    assert res1.run.status == "ok"
    assert res1.run.warnings == ()
    assert res1.run.output["source_tool"] == "ExifTool"
    assert res1.run.output["source_version"].startswith("13.")
    assert res1.sidecar_path is not None and res1.sidecar_path.is_file()
    assert sha256_file(res1.sidecar_path) == res1.sidecar_sha256
    assert sha256_file(ev_repo.original_path(ev)) == orig_sha

    # Check raw vs normalized values (Orientation 6 -> Rotate 90 CW; DateTimeOriginal -> ISO-8601)
    by_field = {(r.group_name, r.field_name): r for r in res1.records}
    orient = by_field[("EXIF:IFD0", "Orientation")]
    assert orient.origin == "embedded"
    assert orient.raw_value == "6"
    assert "90" in orient.normalized_value

    dto = by_field[("EXIF:ExifIFD", "DateTimeOriginal")]
    assert dto.normalized_value == "2026-05-10T14:30:00+03:00"

    # Host directory paths must NEVER appear in DB records or sidecar JSON
    sidecar_text = res1.sidecar_path.read_text(encoding="utf-8")
    assert str(root.parent) not in sidecar_text
    assert "File:System:Directory" not in sidecar_text

    # Reproducibility: running a second extraction produces identical normalized/raw tag pairs
    res2 = meta.extract(ev.id)
    assert res2.run.id > res1.run.id
    pairs1 = [(r.group_name, r.field_name, r.raw_value, r.normalized_value) for r in res1.records]
    pairs2 = [(r.group_name, r.field_name, r.raw_value, r.normalized_value) for r in res2.records]
    assert pairs1 == pairs2
    assert verify_chain(meta.db.con).ok


def test_extract_conflicting_exif_iptc_xmp_timestamps(env, exiftool_exe, tmp_path):
    meta, ev_repo, case, _root = env
    src = make_exif_jpeg_with_exiftool(
        tmp_path / "synth_conflict.jpg",
        exiftool_exe,
        {
            "ExifIFD:DateTimeOriginal": "2026:05:10 14:30:00",
            "ExifIFD:CreateDate": "2026:05:12 09:00:00",
            "IFD0:ModifyDate": "2026:05:09 07:00:00",
            "IPTC:DateCreated": "2026:05:15",
            "IPTC:Keywords": "synthetic-tag",
            "XMP-xmp:CreatorTool": "SyntheticEditor 1.0",
        },
    )
    ev = ev_repo.import_file(case.id, src)
    res = meta.extract(ev.id)
    assert res.run.status == "warning"
    categories = {r.category for r in res.records}
    assert {"EXIF", "IPTC", "XMP"} <= categories
    joined = "\n".join(res.run.warnings).lower()
    assert "timezone" in joined
    assert "earlier than" in joined
    assert "differs from" in joined
    assert "iptc:datecreated" in joined


def test_extract_no_exif_jpeg_and_png(env, tmp_path):
    meta, ev_repo, case, _root = env
    for path in (make_valid_jpeg(tmp_path / "plain.jpg"), make_valid_png(tmp_path / "plain.png")):
        ev = ev_repo.import_file(case.id, path)
        res = meta.extract(ev.id)
        assert res.run.status == "warning"
        assert any("no embedded exif, iptc or xmp" in w.lower() for w in res.run.warnings)


def test_extract_malformed_exif_surfaces_warning_and_preserves_original(env, tmp_path):
    meta, ev_repo, case, _root = env
    src = make_malformed_exif_jpeg(tmp_path / "corrupt_exif.jpg")
    ev = ev_repo.import_file(case.id, src)
    res = meta.extract(ev.id)
    assert res.run.status == "warning"
    assert any("exiftool" in w.lower() or "warning" in w.lower() for w in res.run.warnings)
    assert ev_repo.verify_integrity(ev.id).ok


def test_export_json_contains_provenance_legend_and_hash(env, exiftool_exe, tmp_path):
    meta, ev_repo, case, _root = env
    src = make_exif_jpeg_with_exiftool(
        tmp_path / "export_me.jpg",
        exiftool_exe,
        {"IFD0:Make": "SynthCam", "ExifIFD:DateTimeOriginal": "2026:05:10 14:30:00"},
    )
    ev = ev_repo.import_file(case.id, src)
    meta.extract(ev.id)

    dest = tmp_path / "exported_metadata.json"
    exp = meta.export_json(ev.id, dest)
    assert exp.path.is_file()
    assert sha256_file(exp.path) == exp.sha256

    data = json.loads(exp.path.read_text(encoding="utf-8"))
    assert data["evidence_id"] == ev.id
    assert data["evidence_sha256"] == ev.sha256
    assert data["source_tool"] == "ExifTool"
    assert data["source_version"].startswith("13.")
    assert "embedded" in data["legend"] and "application_system" in data["legend"]
    assert len(data["records_by_origin"]["embedded"]) >= 2
    assert len(data["records_by_origin"]["application_system"]) >= 1

    events = [e.event_type for e in list_events(meta.db.con, evidence_id=ev.id)]
    assert "metadata_exported" in events
    assert verify_chain(meta.db.con).ok


def test_missing_exiftool_raises_clean_error(tmp_path):
    d = open_database(tmp_path)
    try:
        case = CaseRepository(d, tmp_path / "cases").create("No tool", SCOPE)
        ev_repo = EvidenceRepository(d, tmp_path)
        ev = ev_repo.import_file(case.id, make_valid_jpeg(tmp_path / "a.jpg"))
        meta = MetadataService(
            d, ev_repo, exiftool_path_getter=lambda: str(tmp_path / "nonexistent.exe")
        )
        with pytest.raises(ExifToolNotFoundError):
            meta.extract(ev.id)
    finally:
        d.close()
