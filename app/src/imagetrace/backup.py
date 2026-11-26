"""Database backup, optional encryption (AES-256-GCM + scrypt), verification and restore.

Rules (blueprint sections 7, 8, 9, 11):
- Backups use SQLite's online backup API (consistent copy of a live database).
- Each backup is written to a ``.partial`` file, integrity-checked, then atomically renamed.
  A ``<name>.sha256`` sidecar records its SHA-256 digest.
- Optional encrypted backups (``.sqlite.itbak``) use authenticated AES-256-GCM with an scrypt
  key derived from a user passphrase (``>= 8`` chars). Header bytes are bound as GCM AAD so any
  tampering with magic, KDF parameters, salt, nonce or ciphertext is detected cleanly.
- Passphrases are NEVER stored or logged. Temporary plaintext copies used during encryption or
  decryption are overwritten with zeros and deleted in ``finally:``.
- Backups are NEVER deleted automatically.
- Restore verifies the backup first and always creates a ``pre-restore`` backup of the current
  database before replacing it.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import sqlite3
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from imagetrace.audit import append_event
from imagetrace.db import SCHEMA_VERSION, Database, migrate
from imagetrace.hashing import sha256_file
from imagetrace.security import PassphraseError, shred_and_unlink, validate_new_passphrase

log = logging.getLogger(__name__)

BACKUP_PREFIX = "imagetrace-"
BACKUP_SUFFIX = ".sqlite"
ENCRYPTED_BACKUP_SUFFIX = ".sqlite.itbak"
SAFETY_MARGIN = 50 * 1024**2
_LABEL_RE = re.compile(r"^[a-z0-9-]{1,40}$")

# Self-describing encrypted backup container format (see docs/user-guide/backup-recovery.md)
# Header (40 bytes):
#   8 bytes : magic = b"ITBAK1\x00\x01"
#   4 bytes : uint8 n_exp (16 -> N=65536), uint8 r (8), uint8 p (1), uint8 reserved (0)
#   16 bytes: random scrypt salt
#   12 bytes: random AES-GCM nonce
# Body:
#   AES-256-GCM ciphertext + 16-byte authentication tag (AAD = full 40-byte header)
ITBAK_MAGIC = b"ITBAK1\x00\x01"
ITBAK_HEADER_FMT = ">8sBBBB16s12s"
ITBAK_HEADER_SIZE = struct.calcsize(ITBAK_HEADER_FMT)
ITBAK_SCRYPT_N_EXP = 16
ITBAK_SCRYPT_R = 8
ITBAK_SCRYPT_P = 1


class BackupError(Exception):
    pass


@dataclass(frozen=True)
class BackupResult:
    path: Path
    sha256: str
    size: int
    encrypted: bool = False


@dataclass(frozen=True)
class BackupCheck:
    path: Path
    ok: bool
    message: str
    schema_version: int | None = None
    encrypted: bool = False
    requires_passphrase: bool = False


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def sidecar_path(path: Path) -> Path:
    return path.with_name(path.name + ".sha256")


def is_encrypted_backup(path: Path) -> bool:
    """Return True if *path* starts with the ImageTrace encrypted backup header."""
    p = Path(path)
    if not p.is_file():
        return False
    try:
        with p.open("rb") as fh:
            return fh.read(len(ITBAK_MAGIC)) == ITBAK_MAGIC
    except OSError:
        return False


def _derive_backup_key(passphrase: str, salt: bytes, n_exp: int, r: int, p: int) -> bytes:
    if not (10 <= n_exp <= 20 and 1 <= r <= 16 and 1 <= p <= 8):
        raise BackupError("Unsupported encryption parameters in backup header.")
    kdf = Scrypt(salt=salt, length=32, n=1 << n_exp, r=r, p=p)
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_bytes(plaintext: bytes, passphrase: str) -> bytes:
    """Encrypt *plaintext* into the self-describing ITBAK1 container (AES-256-GCM + scrypt)."""
    try:
        validate_new_passphrase(passphrase)
    except PassphraseError as exc:
        raise BackupError(str(exc)) from exc
    salt = os.urandom(16)
    nonce = os.urandom(12)
    header = struct.pack(
        ITBAK_HEADER_FMT,
        ITBAK_MAGIC,
        ITBAK_SCRYPT_N_EXP,
        ITBAK_SCRYPT_R,
        ITBAK_SCRYPT_P,
        0,
        salt,
        nonce,
    )
    key = _derive_backup_key(passphrase, salt, ITBAK_SCRYPT_N_EXP, ITBAK_SCRYPT_R, ITBAK_SCRYPT_P)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, header)
    return header + ciphertext


def decrypt_bytes(blob: bytes, passphrase: str) -> bytes:
    """Decrypt and authenticate an ITBAK1 encrypted backup container."""
    if len(blob) < ITBAK_HEADER_SIZE + 16:
        raise BackupError("Encrypted backup file is truncated or corrupt.")
    header = blob[:ITBAK_HEADER_SIZE]
    magic, n_exp, r, p, reserved, salt, nonce = struct.unpack(ITBAK_HEADER_FMT, header)
    if magic != ITBAK_MAGIC or reserved != 0:
        raise BackupError("Not a valid ImageTrace encrypted backup file.")
    if not passphrase:
        raise BackupError("A passphrase is required to decrypt this backup.")
    key = _derive_backup_key(passphrase, salt, n_exp, r, p)
    try:
        return AESGCM(key).decrypt(nonce, blob[ITBAK_HEADER_SIZE:], header)
    except InvalidTag as exc:
        raise BackupError(
            "Decryption failed: wrong passphrase or the encrypted backup file was modified."
        ) from exc


def _inspect(path: Path) -> tuple[list[str], int, bool]:
    """(integrity problems, user_version, has ImageTrace tables) of a database file, read-only."""
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        problems = [r[0] for r in con.execute("PRAGMA integrity_check") if r[0] != "ok"]
        version = int(con.execute("PRAGMA user_version").fetchone()[0])
        has_cases = (
            con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cases'"
            ).fetchone()
            is not None
        )
    finally:
        con.close()
    return problems, version, has_cases


def _check_space(backups_dir: Path, required_bytes: int) -> None:
    backups_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(backups_dir).free
    if free < required_bytes:
        raise BackupError(
            f"Not enough free space in {backups_dir} for a backup "
            f"({free // 1024**2} MB free, need about {required_bytes // 1024**2} MB)."
        )


def backup_database(
    db: Database,
    backups_dir: Path,
    label: str = "manual",
    *,
    passphrase: str | None = None,
) -> BackupResult:
    """Create a verified online backup (encrypted ``.sqlite.itbak`` when *passphrase* is given)."""
    if not _LABEL_RE.fullmatch(label):
        raise ValueError(f"Invalid backup label: {label!r}")
    encrypted = passphrase is not None
    if encrypted:
        try:
            validate_new_passphrase(passphrase)
        except PassphraseError as exc:
            raise BackupError(str(exc)) from exc

    db_size = db.path.stat().st_size if db.path.exists() else 0
    _check_space(backups_dir, db_size * (2 if encrypted else 1) + SAFETY_MARGIN)

    suffix = ENCRYPTED_BACKUP_SUFFIX if encrypted else BACKUP_SUFFIX
    final = backups_dir / f"{BACKUP_PREFIX}{label}-{_timestamp()}{suffix}"
    partial = final.with_name(final.name + ".partial")
    plain_tmp = backups_dir / f".tmp-plain-{secrets.token_hex(6)}.sqlite" if encrypted else partial
    try:
        dest = sqlite3.connect(plain_tmp)
        try:
            db.con.backup(dest)
        finally:
            dest.close()
        problems, _version, _has = _inspect(plain_tmp)
        if problems:
            raise BackupError(f"Backup failed integrity check: {problems[:3]}")

        if encrypted:
            assert passphrase is not None  # noqa: S101 (guarded by encrypted check)
            blob = encrypt_bytes(plain_tmp.read_bytes(), passphrase)
            partial.write_bytes(blob)
            # Round-trip authentication check before publishing
            if len(decrypt_bytes(partial.read_bytes(), passphrase)) < 100:
                raise BackupError("Encrypted backup verification produced an empty payload.")
        os.replace(partial, final)
    except (sqlite3.Error, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise BackupError(f"Backup failed: {exc}") from exc
    except BackupError:
        partial.unlink(missing_ok=True)
        raise
    finally:
        if encrypted:
            shred_and_unlink(plain_tmp)

    digest = sha256_file(final)
    sidecar_path(final).write_text(f"{digest}  {final.name}\n", encoding="ascii")
    log.info("Database backup created: %s (encrypted=%s)", final.name, encrypted)
    return BackupResult(final, digest, final.stat().st_size, encrypted=encrypted)


def verify_backup(
    path: Path,
    *,
    passphrase: str | None = None,
    temp_dir: Path | None = None,
) -> BackupCheck:
    path = Path(path)
    if not path.is_file():
        return BackupCheck(path, False, "Backup file not found.")
    encrypted = is_encrypted_backup(path) or path.name.lower().endswith(ENCRYPTED_BACKUP_SUFFIX)

    notes: list[str] = []
    sidecar = sidecar_path(path)
    if sidecar.is_file():
        try:
            expected = sidecar.read_text(encoding="ascii").split()[0].lower()
        except (OSError, IndexError, UnicodeDecodeError):
            return BackupCheck(
                path, False, "The .sha256 sidecar file is unreadable.", encrypted=encrypted
            )
        if sha256_file(path) != expected:
            return BackupCheck(
                path,
                False,
                "SHA-256 does not match the .sha256 sidecar file.",
                encrypted=encrypted,
            )
        notes.append("SHA-256 matches the sidecar")
    else:
        notes.append("no .sha256 sidecar, so the hash was not verified")

    if encrypted:
        if not is_encrypted_backup(path):
            return BackupCheck(
                path, False, "Encrypted backup header is invalid or corrupt.", encrypted=True
            )
        if not passphrase:
            return BackupCheck(
                path,
                False,
                "Encrypted backup: passphrase required to decrypt and verify contents.",
                encrypted=True,
                requires_passphrase=True,
            )
        try:
            plaintext = decrypt_bytes(path.read_bytes(), passphrase)
        except (BackupError, OSError) as exc:
            return BackupCheck(path, False, str(exc), encrypted=True)

        work_dir = temp_dir if (temp_dir is not None and temp_dir.is_dir()) else path.parent
        tmp_db = work_dir / f".tmp-verify-{secrets.token_hex(6)}.sqlite"
        try:
            tmp_db.write_bytes(plaintext)
            problems, version, has_cases = _inspect(tmp_db)
        except (sqlite3.DatabaseError, OSError) as exc:
            return BackupCheck(
                path,
                False,
                f"Decrypted payload is not a valid SQLite database: {exc}",
                encrypted=True,
            )
        finally:
            shred_and_unlink(tmp_db)
        notes.append("AES-256-GCM decryption OK")
    else:
        try:
            problems, version, has_cases = _inspect(path)
        except sqlite3.DatabaseError as exc:
            return BackupCheck(path, False, f"Not a readable SQLite database: {exc}")

    if problems:
        return BackupCheck(
            path, False, f"Integrity check failed: {problems[:3]}", version, encrypted=encrypted
        )
    if not has_cases or version < 1:
        return BackupCheck(
            path, False, "This is not an ImageTrace database.", version, encrypted=encrypted
        )
    if version > SCHEMA_VERSION:
        return BackupCheck(
            path,
            False,
            f"Backup schema v{version} is newer than this app (v{SCHEMA_VERSION}).",
            version,
            encrypted=encrypted,
        )
    notes.append(f"integrity OK, schema v{version}")
    text = "; ".join(notes)
    return BackupCheck(path, True, text[:1].upper() + text[1:] + ".", version, encrypted=encrypted)


def restore_database(
    db: Database,
    backup_path: Path,
    backups_dir: Path,
    actor: str,
    *,
    passphrase: str | None = None,
    temp_dir: Path | None = None,
) -> Path:
    """Replace the live database contents with *backup_path* (plain or encrypted).

    Returns the path to the pre-restore backup of the previous database.
    """
    backup_path = Path(backup_path)
    check = verify_backup(backup_path, passphrase=passphrase, temp_dir=temp_dir)
    if not check.ok:
        raise BackupError(f"Backup not restored: {check.message}")
    pre = backup_database(db, backups_dir, label="pre-restore")

    tmp_plain: Path | None = None
    src_path = backup_path
    try:
        if check.encrypted:
            assert passphrase is not None  # noqa: S101 (verified above)
            work_dir = temp_dir if (temp_dir is not None and temp_dir.is_dir()) else backups_dir
            tmp_plain = work_dir / f".tmp-restore-{secrets.token_hex(6)}.sqlite"
            tmp_plain.write_bytes(decrypt_bytes(backup_path.read_bytes(), passphrase))
            src_path = tmp_plain
        src = sqlite3.connect(f"{src_path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            src.backup(db.con)
        finally:
            src.close()
    except (sqlite3.Error, OSError, BackupError) as exc:
        raise BackupError(
            f"Restore failed: {exc}. The previous database was saved as {pre.path}"
        ) from exc
    finally:
        shred_and_unlink(tmp_plain)

    migrate(db, backups_dir)
    with db.transaction() as con:
        append_event(
            con,
            "database_restored",
            actor_label=actor,
            details={
                "backup_file": backup_path.name,
                "pre_restore_backup": pre.path.name,
                "encrypted": check.encrypted,
            },
        )
    log.info(
        "Database restored from %s (encrypted=%s, previous saved as %s)",
        backup_path.name,
        check.encrypted,
        pre.path.name,
    )
    return pre.path


def restore_corrupt_database_file(
    db_path: Path,
    backup_path: Path,
    backups_dir: Path,
    *,
    passphrase: str | None = None,
    temp_dir: Path | None = None,
) -> Path | None:
    """Offline recovery helper when the live SQLite file is too corrupt to open.

    Preserves the damaged file in *backups_dir* (never discards it) and writes the verified
    backup in its place. Returns the preserved damaged-file copy path (if any existed).
    """
    db_path = Path(db_path)
    backup_path = Path(backup_path)
    check = verify_backup(backup_path, passphrase=passphrase, temp_dir=temp_dir)
    if not check.ok:
        raise BackupError(f"Backup cannot be restored: {check.message}")
    backups_dir.mkdir(parents=True, exist_ok=True)
    preserved: Path | None = None
    if db_path.is_file():
        preserved = backups_dir / f"imagetrace-damaged-{_timestamp()}.sqlite"
        shutil.copy2(db_path, preserved)

    partial = db_path.with_name(db_path.name + ".restore-partial")
    try:
        if check.encrypted:
            assert passphrase is not None  # noqa: S101 (verified above)
            partial.write_bytes(decrypt_bytes(backup_path.read_bytes(), passphrase))
        else:
            shutil.copy2(backup_path, partial)
        problems, _ver, has_cases = _inspect(partial)
        if problems or not has_cases:
            raise BackupError("Restored copy failed final SQLite integrity check.")
        os.replace(partial, db_path)
    except BaseException:
        shred_and_unlink(partial)
        raise
    log.info(
        "Recovered database file %s from %s (damaged copy: %s)",
        db_path.name,
        backup_path.name,
        preserved.name if preserved else "none",
    )
    return preserved


def list_backups(backups_dir: Path) -> list[Path]:
    if not backups_dir.is_dir():
        return []
    files = [
        p
        for p in backups_dir.iterdir()
        if p.is_file()
        and p.name.startswith(BACKUP_PREFIX)
        and (p.name.endswith(BACKUP_SUFFIX) or p.name.endswith(ENCRYPTED_BACKUP_SUFFIX))
    ]
    return sorted(files, key=lambda p: p.name, reverse=True)


def latest_backup_age_days(backups_dir: Path) -> float | None:
    """Return age in days of the newest backup in *backups_dir*, or None if no backups exist."""
    backups = list_backups(backups_dir)
    if not backups:
        return None
    newest_mtime = max(p.stat().st_mtime for p in backups)
    delta_sec = max(0.0, datetime.now(UTC).timestamp() - newest_mtime)
    return delta_sec / 86400.0
