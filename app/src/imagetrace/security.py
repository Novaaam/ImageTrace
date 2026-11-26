"""Security helpers: passphrase hashing (scrypt), safe file wiping and filename hardening.

Rules (blueprint sections 7, 8, 9):
- Passphrases are NEVER stored in plaintext and NEVER logged.
- Session-lock verifiers use scrypt (``cryptography``) with a random 16-byte salt and
  constant-time verification (``hmac.compare_digest``).
- Temporary decrypted files are overwritten with zeros before unlinking (best-effort; note that
  SSD wear-leveling and OS pagefiles mean full-disk encryption such as BitLocker remains
  essential).
"""

from __future__ import annotations

import base64
import contextlib
import hmac
import os
import re
from pathlib import Path

from cryptography.exceptions import InvalidKey
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MIN_PASSPHRASE_LENGTH = 8
MAX_PASSPHRASE_LENGTH = 256

# scrypt parameters for interactive unlock / backup encryption (N=2^15=32768, r=8, p=1 -> ~32 MiB)
LOCK_SCRYPT_N_EXP = 15
LOCK_SCRYPT_R = 8
LOCK_SCRYPT_P = 1
LOCK_KEY_LEN = 32

_VERIFIER_RE = re.compile(
    r"^scrypt\$(?P<n_exp>\d{1,2})\$(?P<r>\d{1,2})\$(?P<p>\d{1,2})\$(?P<salt>[A-Za-z0-9+/=]+)\$(?P<dk>[A-Za-z0-9+/=]+)$"
)

WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


class PassphraseError(ValueError):
    """Passphrase failed validation (safe to display to user; never includes the secret)."""


def validate_new_passphrase(passphrase: str, confirm: str | None = None) -> str:
    """Validate a user-chosen passphrase without normalizing away meaningful characters."""
    if not isinstance(passphrase, str) or len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise PassphraseError(
            f"Passphrase must be at least {MIN_PASSPHRASE_LENGTH} characters long."
        )
    if len(passphrase) > MAX_PASSPHRASE_LENGTH:
        raise PassphraseError(
            f"Passphrase is too long (maximum {MAX_PASSPHRASE_LENGTH} characters)."
        )
    if confirm is not None and not hmac.compare_digest(passphrase, confirm):
        raise PassphraseError("Passphrase and confirmation do not match.")
    return passphrase


def hash_passphrase(passphrase: str) -> str:
    """Derive a salted scrypt verifier string for session lock (never reversible)."""
    validate_new_passphrase(passphrase)
    salt = os.urandom(16)
    kdf = Scrypt(
        salt=salt,
        length=LOCK_KEY_LEN,
        n=1 << LOCK_SCRYPT_N_EXP,
        r=LOCK_SCRYPT_R,
        p=LOCK_SCRYPT_P,
    )
    dk = kdf.derive(passphrase.encode("utf-8"))
    salt_b64 = base64.b64encode(salt).decode("ascii")
    dk_b64 = base64.b64encode(dk).decode("ascii")
    return f"scrypt${LOCK_SCRYPT_N_EXP}${LOCK_SCRYPT_R}${LOCK_SCRYPT_P}${salt_b64}${dk_b64}"


def is_valid_verifier(encoded: str | None) -> bool:
    return bool(isinstance(encoded, str) and _VERIFIER_RE.fullmatch(encoded))


def verify_passphrase(passphrase: str, encoded: str) -> bool:
    """Verify *passphrase* against a stored scrypt verifier in constant time."""
    if not isinstance(passphrase, str) or not isinstance(encoded, str):
        return False
    m = _VERIFIER_RE.fullmatch(encoded)
    if not m:
        return False
    n_exp = int(m.group("n_exp"))
    r = int(m.group("r"))
    p = int(m.group("p"))
    # Reject absurd/hostile work factors from a tampered settings row.
    if not (10 <= n_exp <= 20 and 1 <= r <= 16 and 1 <= p <= 8):
        return False
    try:
        salt = base64.b64decode(m.group("salt"), validate=True)
        expected = base64.b64decode(m.group("dk"), validate=True)
    except ValueError:
        return False
    if len(salt) != 16 or len(expected) != LOCK_KEY_LEN:
        return False
    kdf = Scrypt(salt=salt, length=LOCK_KEY_LEN, n=1 << n_exp, r=r, p=p)
    try:
        kdf.verify(passphrase.encode("utf-8"), expected)
    except InvalidKey:
        return False
    return True


def is_windows_reserved_stem(filename: str) -> bool:
    """Return True if *filename* uses a Windows reserved device name (e.g. CON, NUL.png)."""
    stem = Path(filename).stem.rstrip(" .").upper()
    return stem in WINDOWS_RESERVED_NAMES


def shred_and_unlink(path: Path | None) -> None:
    """Best-effort overwrite with zero bytes before deleting a temporary sensitive file."""
    if path is None or not path.exists():
        return
    try:
        size = path.stat().st_size
        if size > 0:
            chunk = b"\x00" * min(size, 65536)
            with path.open("r+b") as fh:
                remaining = size
                while remaining > 0:
                    n = min(remaining, len(chunk))
                    fh.write(chunk[:n])
                    remaining -= n
                fh.flush()
                os.fsync(fh.fileno())
    except OSError:
        pass
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)
