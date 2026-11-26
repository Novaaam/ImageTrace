import pytest

from imagetrace.audit import verify_chain
from imagetrace.backup import (
    ENCRYPTED_BACKUP_SUFFIX,
    ITBAK_HEADER_SIZE,
    BackupError,
    backup_database,
    decrypt_bytes,
    encrypt_bytes,
    is_encrypted_backup,
    list_backups,
    restore_corrupt_database_file,
    restore_database,
    sidecar_path,
    verify_backup,
)
from imagetrace.cases import CaseRepository
from imagetrace.db import DatabaseError, database_path, open_database
from imagetrace.evidence import EvidenceRepository
from imagetrace.logging_setup import setup_logging
from imagetrace.notes import NotesRepository
from imagetrace.ui.main_window import MainWindow
from synthetic_images import make_valid_jpeg

SCOPE = "Synthetic encrypted backup test scope."


def test_encrypt_decrypt_roundtrip_and_tamper_detection():
    payload = b"SQLite format 3\x00" + b"CONFIDENTIAL_CASE_PAYLOAD_9911" * 20
    pw = "CorrectHorse-Battery!99"
    blob = encrypt_bytes(payload, pw)
    assert len(blob) > ITBAK_HEADER_SIZE + len(payload)
    assert b"CONFIDENTIAL_CASE_PAYLOAD_9911" not in blob
    assert decrypt_bytes(blob, pw) == payload

    # Wrong passphrase rejected
    with pytest.raises(BackupError, match="wrong passphrase"):
        decrypt_bytes(blob, "WrongHorse-Battery!99")

    # Tampered header (GCM AAD) rejected
    bad_hdr = bytearray(blob)
    bad_hdr[12] ^= 0xFF  # flip salt byte
    with pytest.raises(BackupError):
        decrypt_bytes(bytes(bad_hdr), pw)

    # Tampered ciphertext byte rejected
    bad_ct = bytearray(blob)
    bad_ct[-1] ^= 0x01
    with pytest.raises(BackupError):
        decrypt_bytes(bytes(bad_ct), pw)

    # Short passphrase rejected
    with pytest.raises(BackupError, match="at least 8"):
        encrypt_bytes(payload, "short")


def test_encrypted_backup_verify_corrupt_recovery_and_privacy(tmp_path):
    log_path = setup_logging(tmp_path)
    d = open_database(tmp_path)
    cases = CaseRepository(d, tmp_path / "cases", actor=lambda: "Tester")
    ev_repo = EvidenceRepository(d, tmp_path, actor=lambda: "Tester")
    notes = NotesRepository(d, actor=lambda: "Tester")

    marker = "ENCRYPTED_CASE_MARKER_7331"
    test_pw = "Backup-Passphrase-Unique-8421!"
    wrong_pw = "WrongPassphrase123!"
    case = cases.create(f"Case {marker}", SCOPE)
    ev = ev_repo.import_file(case.id, make_valid_jpeg(tmp_path / "sample.jpg"))
    notes.create(
        case.id,
        ev.id,
        note_text=f"Note with {marker}",
        category="General observation",
        confidence_label="Analyst observation",
    )

    backups = tmp_path / "backups"
    res = backup_database(d, backups, label="encrypted", passphrase=test_pw)
    assert res.encrypted is True
    assert res.path.name.endswith(ENCRYPTED_BACKUP_SUFFIX)
    assert is_encrypted_backup(res.path)
    assert sidecar_path(res.path).is_file()
    assert res.path in list_backups(backups)

    # Ciphertext must NOT contain plaintext case/note text, and no temp plaintext left behind
    raw_bytes = res.path.read_bytes()
    assert marker.encode("utf-8") not in raw_bytes
    assert list(backups.glob(".tmp-plain-*")) == []

    # Verify without passphrase -> asks for passphrase
    chk_no_pw = verify_backup(res.path)
    assert chk_no_pw.ok is False
    assert chk_no_pw.requires_passphrase is True
    assert chk_no_pw.encrypted is True

    # Verify with wrong passphrase -> fails
    chk_wrong = verify_backup(res.path, passphrase=wrong_pw)
    assert chk_wrong.ok is False

    # Verify with correct passphrase -> passes
    chk_ok = verify_backup(res.path, passphrase=test_pw, temp_dir=tmp_path / "temp")
    assert chk_ok.ok is True
    assert chk_ok.encrypted is True
    assert "AES-256-GCM" in chk_ok.message
    assert list((tmp_path / "temp").glob(".tmp-verify-*")) == []

    # Live restore into modified DB
    cases.create("After backup case", SCOPE)
    assert len(cases.list()) == 2
    pre = restore_database(
        d, res.path, backups, "Tester", passphrase=test_pw, temp_dir=tmp_path / "temp"
    )
    assert pre.is_file()
    assert len(cases.list()) == 1
    assert cases.get(case.id) is not None
    assert verify_chain(d.con).ok
    d.close()

    # Corrupt the live database file completely and recover via restore_corrupt_database_file
    db_file = database_path(tmp_path)
    db_file.write_bytes(b"CORRUPTED_GARBAGE_BYTES" * 400)
    with pytest.raises((DatabaseError, Exception)):
        open_database(tmp_path)

    damaged_copy = restore_corrupt_database_file(
        db_file, res.path, backups, passphrase=test_pw, temp_dir=tmp_path / "temp"
    )
    assert damaged_copy is not None and damaged_copy.is_file()
    assert damaged_copy.read_bytes().startswith(b"CORRUPTED_GARBAGE_BYTES")

    d2 = open_database(tmp_path)
    try:
        assert CaseRepository(d2, tmp_path / "cases").get(case.id) is not None
        assert EvidenceRepository(d2, tmp_path).get(ev.id) is not None
        assert len(NotesRepository(d2).list_for_evidence(ev.id)) == 1
        assert verify_chain(d2.con).ok
        audit_dump = "\n".join(
            r[0] for r in d2.con.execute("SELECT details_json FROM audit_events").fetchall()
        )
        assert test_pw not in audit_dump
    finally:
        d2.close()

    # Passphrase must NEVER appear in imagetrace.log
    if log_path is not None and log_path.is_file():
        assert test_pw not in log_path.read_text(encoding="utf-8")


def test_gui_encrypted_backup_and_restore(qtbot, tmp_path, dialog_log):
    win = MainWindow(data_root=tmp_path)
    qtbot.addWidget(win)
    win.show()

    case = win.cases.create("Before encrypted backup", SCOPE)
    pw = "Gui-Backup-Passphrase-55!"
    dialog_log.new_passphrase = pw
    res = win.backup_encrypted_now()
    assert res is not None and res.encrypted is True

    # Create another case, then restore encrypted backup via GUI path
    win.cases.create("After encrypted backup", SCOPE)
    assert len(win.cases.list()) == 2
    dialog_log.open_file = res.path
    dialog_log.passphrase = pw
    dialog_log.confirm_answer = True
    assert win.restore_from_backup() is True
    assert [c.id for c in win.cases.list()] == [case.id]
    assert "encrypted" in win.pages["Settings"].backup_age_label.text().lower()
