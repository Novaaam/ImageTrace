import os
import shutil
import stat
import threading
from collections import namedtuple

import pytest

from imagetrace import db as dbmod
from imagetrace.audit import list_events, verify_chain
from imagetrace.cases import CaseRepository, ValidationError
from imagetrace.db import SCHEMA_VERSION, open_database
from imagetrace.evidence import (
    EVIDENCE_ID_RE,
    DuplicateEvidenceError,
    EvidenceRepository,
    FileTooLargeError,
    ImportCancelledError,
    InsufficientSpaceError,
    UnsupportedFileError,
    sanitize_original_filename,
)
from imagetrace.hashing import sha256_file
from synthetic_images import (
    make_decompression_bomb_png,
    make_exe_stub,
    make_malformed_exif_jpeg,
    make_svg_file,
    make_truncated_jpeg,
    make_valid_format,
    make_valid_jpeg,
    make_valid_png,
    make_zip_file,
)

SCOPE = "Synthetic evidence test scope."


@pytest.fixture
def env(tmp_path):
    d = open_database(tmp_path)
    cases = CaseRepository(d, tmp_path / "cases", actor=lambda: "Tester")
    case = cases.create("Evidence test case", SCOPE)
    repo = EvidenceRepository(d, tmp_path, actor=lambda: "Tester")
    yield repo, cases, case, tmp_path
    d.close()


def test_migration_v1_to_v2_preserves_cases_and_backs_up(tmp_path):
    # Create a v1 database first
    dbmod.ensure_data_layout(tmp_path)
    d1 = dbmod.Database(dbmod.database_path(tmp_path))
    dbmod.migrate(d1, tmp_path / "backups", target=1)
    assert d1.schema_version == 1
    c = CaseRepository(d1, tmp_path / "cases").create("Legacy v1 case", SCOPE)
    d1.close()

    # Reopen with latest code -> migrates to v2 with a pre-migration backup
    d2 = open_database(tmp_path)
    try:
        assert d2.schema_version == SCHEMA_VERSION
        assert CaseRepository(d2, tmp_path / "cases").get(c.id).title == "Legacy v1 case"
        tables = {r[0] for r in d2.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "evidence" in tables
        assert len(list((tmp_path / "backups").glob("imagetrace-pre-migration-v1-*.sqlite"))) == 1
        assert d2.integrity_check() == []
    finally:
        d2.close()


def test_import_valid_image_preserves_original_and_logs_without_source_path(env, tmp_path):
    repo, _cases, case, root = env
    secret_dir = tmp_path / "PRIVATE_USER_NAME_FOLDER"
    secret_dir.mkdir()
    src = make_valid_jpeg(secret_dir / "harbour_01.jpg", (240, 160), with_icc=True)
    src_sha = sha256_file(src)

    ev = repo.import_file(case.id, src, source_note="Synthetic camera export")
    assert EVIDENCE_ID_RE.fullmatch(ev.id)
    assert ev.case_id == case.id
    assert ev.original_filename == "harbour_01.jpg"
    assert ev.stored_filename == f"{ev.id}.jpg"
    assert ev.sha256 == src_sha
    assert ev.detected_format == "JPEG"
    assert ev.detected_mime == "image/jpeg"
    assert ev.status == "imported"
    assert (ev.width, ev.height) == (240, 160)
    assert ev.import_source_note == "Synthetic camera export"
    assert ev.dhash and len(ev.dhash) == 16
    assert "Pillow" in ev.analysis_tool_version

    stored = repo.original_path(ev)
    assert stored.is_file()
    assert stored.parent == (root / "cases" / case.id / "originals").resolve()
    assert sha256_file(stored) == src_sha
    assert not bool(stored.stat().st_mode & stat.S_IWRITE)  # read-only
    assert repo.thumbnail_path(ev).is_file()

    # Source directory path must never be stored in DB or audit events
    events = list_events(repo.db.con, evidence_id=ev.id)
    assert [e.event_type for e in events] == ["evidence_imported"]
    assert "PRIVATE_USER_NAME_FOLDER" not in str(ev)
    assert "PRIVATE_USER_NAME_FOLDER" not in str(events[0].details)
    assert verify_chain(repo.db.con).ok


@pytest.mark.parametrize(
    "fmt,ext",
    [("PNG", ".png"), ("WEBP", ".webp"), ("TIFF", ".tif"), ("BMP", ".bmp"), ("GIF", ".gif")],
)
def test_import_multiple_supported_formats(env, tmp_path, fmt, ext):
    repo, _cases, case, _root = env
    src = make_valid_format(tmp_path / f"sample{ext}", fmt)
    ev = repo.import_file(case.id, src)
    assert ev.detected_format == fmt
    assert ev.stored_filename.endswith(ext)
    assert sha256_file(repo.original_path(ev)) == sha256_file(src)


def test_duplicate_requires_confirmation(env, tmp_path):
    repo, _cases, case, _root = env
    src = make_valid_png(tmp_path / "dup.png")
    first = repo.import_file(case.id, src)

    with pytest.raises(DuplicateEvidenceError) as info:
        repo.import_file(case.id, src, allow_duplicate=False)
    assert info.value.existing == [first]
    assert repo.count_for_case(case.id) == 1

    second = repo.import_file(case.id, src, allow_duplicate=True)
    assert second.id != first.id
    assert second.sha256 == first.sha256
    assert second.status == "warning"
    assert any("duplicate" in w.lower() for w in second.warnings)
    assert repo.count_for_case(case.id) == 2


def test_extension_mismatch_and_malformed_exif_and_truncated_image(env, tmp_path):
    repo, _cases, case, _root = env
    # Extension mismatch
    disguised = make_valid_jpeg(tmp_path / "actually_jpeg.png")
    ev1 = repo.import_file(case.id, disguised)
    assert ev1.detected_format == "JPEG"
    assert ev1.stored_filename.endswith(".jpg")  # canonical extension from detected format
    assert ev1.status == "warning"
    assert any("does not match" in w for w in ev1.warnings)

    # Malformed EXIF
    bad_exif = make_malformed_exif_jpeg(tmp_path / "bad_exif.jpg")
    ev2 = repo.import_file(case.id, bad_exif)
    assert sha256_file(repo.original_path(ev2)) == sha256_file(bad_exif)

    # Truncated JPEG (valid header, corrupt body): preserved safely with warning
    trunc = make_truncated_jpeg(tmp_path / "truncated.jpg")
    ev3 = repo.import_file(case.id, trunc)
    assert ev3.status == "warning"
    assert ev3.thumbnail_filename is None
    assert ev3.dhash is None
    assert sha256_file(repo.original_path(ev3)) == sha256_file(trunc)
    assert repo.verify_integrity(ev3.id).ok


def test_rejects_unsupported_oversized_and_bomb_without_leaving_files(env, tmp_path):
    repo, _cases, case, root = env
    txt = tmp_path / "note.txt"
    txt.write_text("plain text", encoding="utf-8")
    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")

    for bad in (
        txt,
        empty,
        make_zip_file(tmp_path / "archive.zip"),
        make_exe_stub(tmp_path / "prog.exe"),
        make_svg_file(tmp_path / "script.svg"),
        make_decompression_bomb_png(tmp_path / "bomb.png"),
        tmp_path,  # directory
    ):
        with pytest.raises(UnsupportedFileError):
            repo.import_file(case.id, bad)

    # Oversized file
    img = make_valid_png(tmp_path / "over.png", (200, 200))
    with pytest.raises(FileTooLargeError, match="exceeds the configured limit"):
        repo.import_file(case.id, img, max_size_bytes=50)

    assert repo.count_for_case(case.id) == 0
    assert list((root / "cases" / case.id / "originals").glob("*")) == []
    assert list((root / "cases" / case.id / "derived").glob("*")) == []


def test_insufficient_and_low_space_handling(env, tmp_path, monkeypatch):
    repo, _cases, case, _root = env
    src = make_valid_jpeg(tmp_path / "space.jpg")
    usage = namedtuple("usage", "total used free")

    # Critically low: refuse import
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: usage(10**12, 0, 1024))
    with pytest.raises(InsufficientSpaceError, match="Not enough free space"):
        repo.import_file(case.id, src)
    assert repo.count_for_case(case.id) == 0

    # Below 10 GB warning threshold but above minimum headroom: imports with warning
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: usage(10**12, 0, 2 * 1024**3))
    ev = repo.import_file(case.id, src)
    assert ev.status == "warning"
    assert any("low disk space" in w.lower() for w in ev.warnings)


def test_large_file_import_cancellation_cleans_up(env, tmp_path):
    repo, _cases, case, root = env
    # ~4.5 MB uncompressed BMP (fast to generate, spans multiple 1 MB chunks)
    big = make_valid_format(tmp_path / "large.bmp", "BMP", size=(1500, 1000))
    assert big.stat().st_size > 4 * 1024**2

    cancel = threading.Event()
    stages: list[str] = []

    def on_progress(_done, _total, stage):
        stages.append(stage)
        if stage == "Copying original":
            cancel.set()

    with pytest.raises(ImportCancelledError):
        repo.import_file(case.id, big, cancel_event=cancel, on_progress=on_progress)

    assert "Copying original" in stages
    assert repo.count_for_case(case.id) == 0
    assert list((root / "cases" / case.id / "originals").glob("*")) == []
    assert list((root / "cases" / case.id / "derived").glob("*")) == []


def test_archived_case_refuses_import(env, tmp_path):
    repo, cases, case, _root = env
    cases.archive(case.id)
    src = make_valid_jpeg(tmp_path / "a.jpg")
    with pytest.raises(ValidationError, match="archived"):
        repo.import_file(case.id, src)


def test_integrity_verification_detects_tampering_and_missing_file(env, tmp_path):
    repo, _cases, case, _root = env
    src = make_valid_jpeg(tmp_path / "check.jpg")
    ev = repo.import_file(case.id, src)

    ok_res = repo.verify_integrity(ev.id)
    assert ok_res.ok and ok_res.read_only is True

    # Simulate external tampering on disk
    stored = repo.original_path(ev)
    os.chmod(stored, stat.S_IWRITE | stat.S_IREAD)
    data = bytearray(stored.read_bytes())
    data[-1] ^= 0xFF
    stored.write_bytes(bytes(data))

    bad_res = repo.verify_integrity(ev.id)
    assert not bad_res.ok
    assert "MISMATCH" in bad_res.message
    assert bad_res.actual_sha256 != bad_res.expected_sha256

    # Simulate missing file
    stored.unlink()
    miss_res = repo.verify_integrity(ev.id)
    assert not miss_res.ok and "missing" in miss_res.message.lower()
    assert verify_chain(repo.db.con).ok


def test_visual_similarity_and_derived_view(env, tmp_path):
    repo, _cases, case, _root = env
    jpg_hi = make_valid_jpeg(tmp_path / "hi.jpg", (180, 120), variant=0, quality=95)
    jpg_lo = make_valid_jpeg(tmp_path / "lo.jpg", (180, 120), variant=0, quality=50)
    different = make_valid_jpeg(tmp_path / "diff.jpg", (180, 120), variant=1, quality=90)

    ev_hi = repo.import_file(case.id, jpg_hi)
    ev_lo = repo.import_file(case.id, jpg_lo)
    ev_diff = repo.import_file(case.id, different)

    similar = repo.find_similar(ev_hi.id)
    ids = [m.evidence.id for m in similar]
    assert ev_lo.id in ids
    assert ev_diff.id not in ids
    match = next(m for m in similar if m.evidence.id == ev_lo.id)
    assert not match.exact_sha256_match

    # Derived copy leaves original untouched and records audit event
    res = repo.create_derived_view(ev_hi.id, rotate_deg=90, contrast=1.2)
    assert res.path.is_file()
    assert repo.verify_integrity(ev_hi.id).ok
    types = [e.event_type for e in list_events(repo.db.con, evidence_id=ev_hi.id)]
    assert "derived_copy_created" in types


def test_sanitize_original_filename():
    assert sanitize_original_filename(r"C:\Users\Secret\Desktop\photo.jpg") == "photo.jpg"
    assert sanitize_original_filename("bad\x00\nname.png") == "badname.png"
    assert sanitize_original_filename("   ") == "unnamed"
    long_name = "a" * 300 + ".jpg"
    cleaned = sanitize_original_filename(long_name)
    assert len(cleaned) <= 255 and cleaned.endswith(".jpg")
