import ast
import pathlib
import tracemalloc
import urllib.request

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel

from imagetrace.audit import verify_chain
from imagetrace.cases import CaseRepository
from imagetrace.db import open_database
from imagetrace.evidence import (
    EvidenceRepository,
    FileTooLargeError,
    UnsupportedFileError,
    sanitize_original_filename,
)
from imagetrace.gps import GpsService
from imagetrace.metadata import MetadataService
from imagetrace.notes import NotesRepository
from imagetrace.ocr import OcrService
from imagetrace.reports import ReportOptions, ReportService
from imagetrace.security import (
    PassphraseError,
    hash_passphrase,
    validate_new_passphrase,
    verify_passphrase,
)
from imagetrace.ui.lock_ui import PassphraseDialog
from imagetrace.ui.main_window import MainWindow
from synthetic_images import (
    make_decompression_bomb_png,
    make_truncated_jpeg,
    make_valid_jpeg,
    make_valid_png,
)

SCOPE = "Synthetic Phase 9 security hardening test scope."


# --- passphrase hashing & validation ------------------------------------------------------
def test_scrypt_passphrase_hashing_and_verification():
    pw = "Strong-Examiner-Pass-2026!"
    h1 = hash_passphrase(pw)
    h2 = hash_passphrase(pw)
    assert h1 != h2  # random 16-byte salt every time
    assert pw not in h1
    assert h1.startswith("scrypt$")
    assert verify_passphrase(pw, h1) is True
    assert verify_passphrase("Wrong-Examiner-Pass-2026!", h1) is False
    assert verify_passphrase(pw, "not-a-valid-hash") is False
    # Reject hostile absurd scrypt work factor (N=2^30)
    assert verify_passphrase(pw, "scrypt$30$8$1$AAAAAAAAAAAAAAAAAAAAAA==$AAAA") is False

    with pytest.raises(PassphraseError):
        validate_new_passphrase("1234567", "1234567")
    with pytest.raises(PassphraseError):
        validate_new_passphrase("12345678", "87654321")


def test_passphrase_dialog_validation(qtbot):
    dlg = PassphraseDialog("Test", "Enter passphrase", confirm_mode=True)
    qtbot.addWidget(dlg)
    dlg.show()
    dlg.pw_edit.setText("short")
    dlg.confirm_edit.setText("short")
    dlg.accept()
    assert dlg.result() != QDialog.DialogCode.Accepted
    assert dlg.error_label.isVisible()

    dlg.pw_edit.setText("LongEnough123!")
    dlg.confirm_edit.setText("Mismatch123!")
    dlg.accept()
    assert dlg.result() != QDialog.DialogCode.Accepted

    dlg.confirm_edit.setText("LongEnough123!")
    dlg.accept()
    assert dlg.result() == QDialog.DialogCode.Accepted
    assert dlg.passphrase() == "LongEnough123!"


# --- session lock & idle timeout GUI tests ------------------------------------------------
def test_session_lock_privacy_passphrase_and_idle_timer(qtbot, tmp_path):
    win = MainWindow(data_root=tmp_path)
    qtbot.addWidget(win)
    win.show()
    case = win.cases.create("Sensitive Case Title", SCOPE)
    win.set_active_case(case.id)
    assert "Sensitive Case Title" in win.active_case_label.text()

    # 1. Privacy-screen lock (no passphrase)
    win.lock_session()
    assert win.is_locked is True
    assert win.root_stack.currentIndex() == 1
    assert "Sensitive Case Title" not in win.active_case_label.text()
    assert not win.search.isEnabled()
    win.lock_overlay.unlock_btn.click()
    assert win.is_locked is False
    assert win.root_stack.currentIndex() == 0
    assert "Sensitive Case Title" in win.active_case_label.text()

    # 2. Passphrase lock: never stored in plaintext in SQLite settings!
    pw = "Analyst-Lock-Pass-7788!"
    assert win.set_lock_passphrase(pw) is True
    raw_settings = str(
        [tuple(r) for r in win.db.con.execute("SELECT key, value FROM settings").fetchall()]
    )
    assert pw not in raw_settings
    assert "scrypt$" in raw_settings

    win.lock_session()
    assert win.lock_overlay.pw_edit.isVisible()
    # Wrong passphrase keeps session locked
    win.lock_overlay.pw_edit.setText("Wrong-Pass-7788!")
    win.lock_overlay.unlock_btn.click()
    assert win.is_locked is True
    assert win.lock_overlay.error_label.isVisible()

    # Correct passphrase unlocks
    win.lock_overlay.pw_edit.setText(pw)
    win.lock_overlay.unlock_btn.click()
    assert win.is_locked is False
    assert win.root_stack.currentIndex() == 0

    # 3. Idle timeout arms single-shot QTimer and locks when triggered
    assert win.set_lock_timeout(5) == 5
    assert win.idle_timer.isActive()
    assert win.idle_timer.interval() == 5 * 60 * 1000
    win.idle_timer.timeout.emit()
    assert win.is_locked is True
    assert win.unlock_session(pw) is True


# --- filename & malformed/adversarial input hardening -------------------------------------
@pytest.mark.parametrize(
    ("raw", "forbidden"),
    [
        ("../../etc/passwd.jpg", ".."),
        (r"C:\Users\victim\secret\photo.jpg", "victim"),
        ("CON.jpg", "CON.jpg"),
        ("NUL.png", "NUL.png"),
        ("COM1.tif", "COM1.tif"),
        ("LPT9.webp", "LPT9.webp"),
        ("photo.jpg...   ", "..."),
        ("bad\x00\x07\x1bname.jpg", "\x00"),
    ],
)
def test_sanitize_original_filename_blocks_traversal_and_reserved_names(raw, forbidden):
    clean = sanitize_original_filename(raw)
    assert clean
    assert "/" not in clean and "\\" not in clean
    assert clean != forbidden
    assert ".." not in clean or clean.startswith("file_")


def test_malformed_and_adversarial_file_matrix(tmp_path):
    d = open_database(tmp_path)
    try:
        cases = CaseRepository(d, tmp_path / "cases", actor=lambda: "Hardener")
        ev_repo = EvidenceRepository(d, tmp_path, actor=lambda: "Hardener")
        case = cases.create("Adversarial matrix", SCOPE)

        # Unsupported / spoofed payloads MUST be rejected before copying into originals/
        bad_payloads = {
            "empty.jpg": b"",
            "pe_spoof.jpg": b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 64,
            "elf_spoof.png": b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64,
            "pdf_spoof.jpg": b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n",
            "svg_spoof.png": b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
            "zip_spoof.jpg": b"PK\x03\x04\x14\x00\x00\x00\x08\x00" + b"\x00" * 32,
            "html_spoof.gif": b"<!DOCTYPE html><script>alert(1)</script>",
            "random.tif": b"NOT_AN_IMAGE_PAYLOAD_1234567890",
        }
        src_dir = tmp_path / "adversarial"
        src_dir.mkdir()
        for name, content in bad_payloads.items():
            p = src_dir / name
            p.write_bytes(content)
            with pytest.raises(UnsupportedFileError):
                ev_repo.import_file(case.id, p)

        # Decompression bomb rejected before pixel decode
        bomb = make_decompression_bomb_png(src_dir / "bomb.png", width=20_000, height=20_000)
        with pytest.raises(UnsupportedFileError, match="decompression bomb"):
            ev_repo.import_file(case.id, bomb, max_pixels=1_000_000)

        # Oversized file rejected before copy
        valid_p = make_valid_jpeg(src_dir / "tiny.jpg")
        with pytest.raises(FileTooLargeError):
            ev_repo.import_file(case.id, valid_p, max_size_bytes=10)

        # Truncated JPEG preserved safely with status='warning' & zero crashes
        trunc = make_truncated_jpeg(src_dir / "truncated.jpg")
        ev_trunc = ev_repo.import_file(case.id, trunc)
        assert ev_trunc.status == "warning"
        assert ev_repo.verify_integrity(ev_trunc.id).ok

        # Polyglot JPEG + trailing HTML/script bytes: imported safely, never executed
        poly_p = src_dir / "polyglot.jpg"
        poly_p.write_bytes(valid_p.read_bytes() + b"\n<script>alert('xss')</script>")
        ev_poly = ev_repo.import_file(case.id, poly_p)
        assert ev_repo.verify_integrity(ev_poly.id).ok

        # Zero rejected files leaked into originals/
        originals = list((tmp_path / "cases" / case.id / "originals").iterdir())
        assert len(originals) == 2
        assert verify_chain(d.con).ok
    finally:
        d.close()


def test_unicode_and_hostile_strings_end_to_end_pdf_and_json(tmp_path, monkeypatch):
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("No network allowed!")),
    )
    d = open_database(tmp_path)
    try:
        cases = CaseRepository(d, tmp_path / "cases", actor=lambda: "Analyst <A&B>")
        ev_repo = EvidenceRepository(d, tmp_path, actor=lambda: "Analyst <A&B>")
        meta = MetadataService(d, ev_repo, actor=lambda: "Analyst <A&B>")
        gps = GpsService(d, ev_repo, meta, actor=lambda: "Analyst <A&B>")
        hostile_tsv = "\n".join(
            [
                "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
                "5\t1\t1\t1\t1\t1\t10\t10\t80\t20\t91.0\t';DROP_TABLE_cases;--",
                "5\t1\t1\t1\t1\t2\t95\t10\t80\t20\t89.0\t<script><b>&amp;</b>",
                "5\t1\t1\t1\t2\t1\t10\t36\t80\t20\t85.0\tCafé_Москва_شارع",
            ]
        )
        ocr = OcrService(
            d,
            ev_repo,
            actor=lambda: "Analyst",
            runner=lambda *_: ("Tesseract", "5.4.0", hostile_tsv),
        )
        notes = NotesRepository(d, actor=lambda: "Analyst <A&B>")
        reports = ReportService(d, tmp_path, cases, ev_repo, meta, gps, ocr, notes)

        case = cases.create("SQL '; DROP TABLE cases;-- & <tag>", "Scope Café & <review>")
        ev = ev_repo.import_file(
            case.id, make_valid_png(tmp_path / "img.png"), source_note="Note <img src=x> & Café"
        )
        ocr.run_ocr(ev.id, language="eng")
        notes.create(
            case.id,
            ev.id,
            note_text="Unicode Café Москва & <b>HTML</b> '; DROP TABLE notes;--",
            category="Signage / visible text",
            confidence_label="Analyst observation",
        )
        jrec = reports.generate_json(case.id, options=ReportOptions(include_ocr_text=True))
        prec = reports.generate_pdf(case.id, options=ReportOptions(include_ocr_text=True))
        assert reports.verify_export(jrec.id).ok
        assert reports.verify_export(prec.id).ok
        assert reports.resolve_path(prec).read_bytes().startswith(b"%PDF-")
        # Tables still exist and audit chain is intact!
        assert cases.get(case.id) is not None
        assert verify_chain(d.con).ok
    finally:
        d.close()


# --- static & runtime security invariants -------------------------------------------------
def test_static_security_invariants_across_source_tree():
    src_root = pathlib.Path(__file__).resolve().parents[1] / "src" / "imagetrace"
    for py_file in sorted(src_root.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "pickle", f"Forbidden pickle import in {py_file}"
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "pickle", f"Forbidden pickle import in {py_file}"
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec"):
                    raise AssertionError(f"Forbidden builtin call {node.func.id}() in {py_file}")
                if isinstance(node.func, ast.Attribute) and node.func.attr in (
                    "setHtml",
                    "system",
                ):
                    raise AssertionError(f"Forbidden method call .{node.func.attr}() in {py_file}")
                for kw in node.keywords:
                    if (
                        kw.arg == "shell"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        raise AssertionError(f"Forbidden shell=True call in {py_file}")


def test_every_qlabel_in_main_window_uses_plain_text(qtbot, tmp_path):
    win = MainWindow(data_root=tmp_path)
    qtbot.addWidget(win)
    non_plain = [
        (lbl.objectName(), lbl.text()[:40])
        for lbl in win.findChildren(QLabel)
        if lbl.textFormat() != Qt.TextFormat.PlainText
    ]
    assert non_plain == [], f"Found QLabel(s) not using PlainText: {non_plain}"


# --- performance, memory ceiling & temp-file cleanliness ----------------------------------
def test_batch_performance_memory_and_zero_temp_leaks(tmp_path, monkeypatch):
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("No network allowed!")),
    )
    d = open_database(tmp_path)
    try:
        cases = CaseRepository(d, tmp_path / "cases", actor=lambda: "Bench")
        ev_repo = EvidenceRepository(d, tmp_path, actor=lambda: "Bench")
        meta = MetadataService(d, ev_repo, actor=lambda: "Bench")
        gps = GpsService(d, ev_repo, meta, actor=lambda: "Bench")
        ocr = OcrService(d, ev_repo, actor=lambda: "Bench")
        notes = NotesRepository(d, actor=lambda: "Bench")
        reports = ReportService(d, tmp_path, cases, ev_repo, meta, gps, ocr, notes)

        case = cases.create("Batch stress case", SCOPE)
        src_dir = tmp_path / "batch_src"
        src_dir.mkdir()
        files = [
            make_valid_jpeg(src_dir / f"img_{i:02d}.jpg", size=(240 + i * 2, 180))
            if i % 2 == 0
            else make_valid_png(src_dir / f"img_{i:02d}.png", size=(220 + i * 2, 160))
            for i in range(20)
        ]

        tracemalloc.start()
        try:
            for idx, p in enumerate(files):
                ev = ev_repo.import_file(case.id, p)
                if idx < 5:
                    notes.create(
                        case.id,
                        ev.id,
                        note_text=f"Batch note #{idx}",
                        category="General observation",
                        confidence_label="Analyst observation",
                    )
            jrec = reports.generate_json(case.id)
            prec = reports.generate_pdf(case.id)
            assert reports.verify_export(jrec.id).ok
            assert reports.verify_export(prec.id).ok
            _cur, peak_bytes = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        # Peak traced Python heap must remain well below 128 MiB for 20-image batch + PDF/JSON
        assert peak_bytes < 128 * 1024 * 1024, (
            f"Peak memory too high: {peak_bytes / 1024**2:.1f} MiB"
        )
        assert len(ev_repo.list_for_case(case.id)) == 20
        assert list((tmp_path / "temp").iterdir()) == []
        assert list((tmp_path / "cases" / case.id / "originals").glob("*.partial")) == []
        assert verify_chain(d.con).ok
    finally:
        d.close()
