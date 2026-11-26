import urllib.request

import pytest

from imagetrace import db as dbmod
from imagetrace.audit import list_events, verify_chain
from imagetrace.cases import CaseRepository
from imagetrace.db import SCHEMA_VERSION, open_database
from imagetrace.evidence import EvidenceRepository
from imagetrace.gps import (
    GPS_PARSER_VERSION,
    NO_GPS_MESSAGE,
    GpsError,
    GpsService,
    fetch_osm_tile,
    format_dms,
    lat_lon_to_tile,
    parse_altitude,
    parse_coordinate,
    parse_embedded_gps_records,
    parse_gps_timestamp,
)
from imagetrace.metadata import MetadataRecord, MetadataService
from imagetrace.tools_status import find_exiftool
from synthetic_images import make_exif_jpeg_with_exiftool, make_valid_jpeg

SCOPE = "Synthetic GPS test scope."


@pytest.fixture
def exiftool_exe():
    exe = find_exiftool()
    assert exe is not None and exe.is_file()
    return exe


@pytest.fixture
def env(tmp_path):
    d = open_database(tmp_path)
    cases = CaseRepository(d, tmp_path / "cases", actor=lambda: "Tester")
    case = cases.create("GPS test case", SCOPE)
    ev_repo = EvidenceRepository(d, tmp_path, actor=lambda: "Tester")
    meta = MetadataService(d, ev_repo, actor=lambda: "Tester")
    gps = GpsService(d, ev_repo, meta, actor=lambda: "Tester")
    yield gps, meta, ev_repo, case, tmp_path
    d.close()


def _rec(group: str, category: str, field: str, raw: str, norm: str = "", origin: str = "embedded"):
    return MetadataRecord(
        id=1,
        analysis_run_id=1,
        evidence_id="E-20261124-AAAAAA",
        group_name=group,
        category=category,
        origin=origin,
        field_name=field,
        raw_value=raw,
        normalized_value=norm or raw,
        source_tool="ExifTool",
        source_version="13.64",
        extracted_at="2026-11-24T00:00:00Z",
    )


# --- unit tests: coordinate, altitude, timestamp & tile math ------------------------------
def test_parse_coordinate_valid_formats():
    lat, w = parse_coordinate("40.712776", "N", axis="lat")
    assert lat == pytest.approx(40.712776) and w == []

    lat_s, ws = parse_coordinate("33.86882", "South", axis="lat")
    assert lat_s == pytest.approx(-33.86882) and ws == []

    lon_w, ww = parse_coordinate("151.20929", "W", axis="lon")
    assert lon_w == pytest.approx(-151.20929) and ww == []

    # DMS string
    dms_lat, wd = parse_coordinate("33 deg 52' 7.75\" S", None, axis="lat")
    assert dms_lat == pytest.approx(-33.8688194, abs=1e-5) and wd == []

    # XMP DDM string
    xmp_lat, wx = parse_coordinate("40,42.7666N", None, axis="lat")
    assert xmp_lat == pytest.approx(40.7127767, abs=1e-5) and wx == []


@pytest.mark.parametrize(
    ("raw", "ref", "axis", "word"),
    [
        ("95.0", "N", "lat", "outside the valid range"),
        ("90.0001", "S", "lat", "outside the valid range"),
        ("181.0", "E", "lon", "outside the valid range"),
        ("NaN", "N", "lat", "could not parse"),
        ("inf", "E", "lon", "could not parse"),
        ("not-a-coord", "N", "lat", "could not parse"),
        ("40 deg 75' 0\" N", None, "lat", "invalid gps latitude minutes"),
        ("", "N", "lat", "empty"),
    ],
)
def test_parse_coordinate_rejects_invalid_and_out_of_range(raw, ref, axis, word):
    val, warns = parse_coordinate(raw, ref, axis=axis)
    assert val is None
    assert any(word in w.lower() for w in warns)


def test_parse_coordinate_missing_and_conflicting_ref_warns():
    val, warns = parse_coordinate("40.71", None, axis="lat")
    assert val == pytest.approx(40.71)
    assert any("missing" in w.lower() for w in warns)

    val2, warns2 = parse_coordinate("40,42.0N", "S", axis="lat")
    assert val2 == pytest.approx(40.7)
    assert any("conflicting" in w.lower() for w in warns2)


def test_parse_altitude_and_timestamp_and_dms():
    assert parse_altitude("12.5", "0") == (12.5, [])
    assert parse_altitude("12.5", "1") == (-12.5, [])
    assert parse_altitude("12.5 m", "Below Sea Level") == (-12.5, [])
    val, warns = parse_altitude("200000", "0")
    assert val == 200000.0 and any("outside typical" in w.lower() for w in warns)
    assert parse_altitude("garbage", "0")[0] is None

    ts, tw = parse_gps_timestamp("2026:05:10", "14:30:05")
    assert ts == "2026-05-10T14:30:05Z" and tw == []
    ts_bad, tw_bad = parse_gps_timestamp("2026:99:99", "00:00:00")
    assert ts_bad is None and tw_bad

    assert format_dms(-33.86882, "lat").endswith('" S')
    assert format_dms(151.20929, "lon").endswith('" E')


def test_parse_embedded_gps_records_edge_cases():
    # Composite-only tags must be ignored (not embedded!)
    has, _ = parse_embedded_gps_records(
        [_rec("Composite", "Composite", "GPSLatitude", "40.7", origin="application_system")]
    )
    assert not has

    # Null Island warning
    has, parsed = parse_embedded_gps_records(
        [
            _rec("EXIF:GPS", "GPS", "GPSLatitude", "0.0"),
            _rec("EXIF:GPS", "GPS", "GPSLatitudeRef", "N"),
            _rec("EXIF:GPS", "GPS", "GPSLongitude", "0.0"),
            _rec("EXIF:GPS", "GPS", "GPSLongitudeRef", "E"),
        ]
    )
    assert has and parsed["is_valid"]
    assert any("null island" in w.lower() for w in parsed["warnings"])

    # Incomplete (latitude without longitude)
    has, parsed = parse_embedded_gps_records([_rec("EXIF:GPS", "GPS", "GPSLatitude", "40.0")])
    assert has and not parsed["is_valid"]
    assert any("incomplete" in w.lower() for w in parsed["warnings"])

    # Out of range embedded GPS
    has, parsed = parse_embedded_gps_records(
        [
            _rec("EXIF:GPS", "GPS", "GPSLatitude", "123.45"),
            _rec("EXIF:GPS", "GPS", "GPSLatitudeRef", "N"),
            _rec("EXIF:GPS", "GPS", "GPSLongitude", "10.0"),
            _rec("EXIF:GPS", "GPS", "GPSLongitudeRef", "E"),
        ]
    )
    assert has and not parsed["is_valid"] and parsed["latitude"] is None
    assert any("outside the valid range" in w.lower() for w in parsed["warnings"])

    # EXIF + conflicting XMP GPS
    has, parsed = parse_embedded_gps_records(
        [
            _rec("EXIF:GPS", "GPS", "GPSLatitude", "40.7128"),
            _rec("EXIF:GPS", "GPS", "GPSLatitudeRef", "N"),
            _rec("EXIF:GPS", "GPS", "GPSLongitude", "74.0060"),
            _rec("EXIF:GPS", "GPS", "GPSLongitudeRef", "W"),
            _rec("XMP:XMP-exif", "XMP", "GPSLatitude", "51,30.0N"),
            _rec("XMP:XMP-exif", "XMP", "GPSLongitude", "0,7.0W"),
        ]
    )
    assert has and parsed["is_valid"]
    assert any("discrepancy" in w.lower() for w in parsed["warnings"])


def test_tile_math_and_opt_in_guard(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("Network must not be called when disabled")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    x, y = lat_lon_to_tile(0.0, 0.0, 2)
    assert (x, y) == pytest.approx((2.0, 2.0))
    with pytest.raises(GpsError, match="disabled"):
        fetch_osm_tile(10, 500, 500, allow_external_map=False)


# --- migration v3 -> v4 -------------------------------------------------------------------
def test_migration_v3_to_v4_preserves_metadata_and_backs_up(tmp_path):
    dbmod.ensure_data_layout(tmp_path)
    d3 = dbmod.Database(dbmod.database_path(tmp_path))
    dbmod.migrate(d3, tmp_path / "backups", target=3)
    assert d3.schema_version == 3
    case = CaseRepository(d3, tmp_path / "cases").create("Legacy v3 case", SCOPE)
    ev_repo = EvidenceRepository(d3, tmp_path)
    ev = ev_repo.import_file(case.id, make_valid_jpeg(tmp_path / "legacy.jpg"))
    MetadataService(d3, ev_repo).extract(ev.id)
    d3.close()

    d4 = open_database(tmp_path)
    try:
        assert d4.schema_version == SCHEMA_VERSION
        tables = {r[0] for r in d4.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "gps_records" in tables
        assert MetadataService(d4, EvidenceRepository(d4, tmp_path)).latest_run(ev.id) is not None
        assert len(list((tmp_path / "backups").glob("imagetrace-pre-migration-v3-*.sqlite"))) == 1
        assert d4.integrity_check() == []
    finally:
        d4.close()


# --- integration with real ExifTool & strict zero-network guard ---------------------------
def test_valid_gps_fixture_offline(env, exiftool_exe, tmp_path, monkeypatch):
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("No network allowed by default")),
    )
    gps, _meta, ev_repo, case, _root = env
    src = make_exif_jpeg_with_exiftool(
        tmp_path / "synth_gps_sw.jpg",
        exiftool_exe,
        {
            "GPSLatitude": "33.86882",
            "GPSLatitudeRef": "S",
            "GPSLongitude": "151.20929",
            "GPSLongitudeRef": "W",
            "GPSAltitude": "12.5",
            "GPSAltitudeRef": "Below Sea Level",
            "GPSDateStamp": "2026:05:10",
            "GPSTimeStamp": "14:30:05",
            "GPSDOP": "1.8",
        },
    )
    ev = ev_repo.import_file(case.id, src)
    outcome = gps.parse(ev.id, extract_if_missing=True)

    assert outcome.has_gps
    assert outcome.record is not None
    rec = outcome.record
    assert rec.is_valid
    assert rec.latitude == pytest.approx(-33.86882, abs=1e-5)
    assert rec.longitude == pytest.approx(-151.20929, abs=1e-5)
    assert rec.altitude == pytest.approx(-12.5)
    assert rec.gps_timestamp == "2026-05-10T14:30:05Z"
    assert rec.source_field == "EXIF:GPS:GPSLatitude, EXIF:GPS:GPSLongitude"
    assert rec.parser_version == GPS_PARSER_VERSION
    assert "EXIF:GPS:GPSLatitude" in rec.raw_fields
    assert "EXIF:GPS:GPSDOP" in rec.raw_fields

    events = [e.event_type for e in list_events(gps.db.con, evidence_id=ev.id)]
    assert "gps_parsed" in events
    assert verify_chain(gps.db.con).ok


def test_missing_gps_fixture_reports_absent_cleanly(env, tmp_path, monkeypatch):
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("No network allowed")),
    )
    gps, _meta, ev_repo, case, _root = env
    ev = ev_repo.import_file(case.id, make_valid_jpeg(tmp_path / "no_gps.jpg"))
    outcome = gps.parse(ev.id, extract_if_missing=True)
    assert not outcome.has_gps
    assert outcome.record is None
    assert outcome.run.output["message"] == NO_GPS_MESSAGE
    count = gps.db.con.execute("SELECT COUNT(*) FROM gps_records").fetchone()[0]
    assert count == 0
