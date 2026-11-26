"""Application settings, stored in the SQLite ``settings`` table (values are JSON).

When no database is available (for example, the storage is missing), settings live in memory
for the session only. Nothing is written anywhere else. QSettings is avoided on purpose: on
Windows it writes to the registry.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any

from imagetrace.db import Database
from imagetrace.security import is_valid_verifier
from imagetrace.timefmt import utc_now_iso

log = logging.getLogger(__name__)

THEMES = ("system", "light", "dark")
MAX_ANALYST_LABEL = 60
DEFAULT_MAX_IMPORT_MB = 50
MIN_IMPORT_MB = 1
MAX_IMPORT_MB = 500
DEFAULT_MAX_MEGAPIXELS = 50
MIN_MEGAPIXELS = 1
MAX_MEGAPIXELS = 200
DEFAULT_LOCK_TIMEOUT_MINUTES = 0  # 0 = manual lock only
MAX_LOCK_TIMEOUT_MINUTES = 240
DEFAULT_BACKUP_REMINDER_DAYS = 7
MAX_BACKUP_REMINDER_DAYS = 90


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    if value < low or value > high:
        return default
    return value


@dataclass
class Settings:
    theme: str = "system"
    # No optional network features enabled by default; external map requires explicit opt-in.
    allow_network_features: bool = False
    allow_external_map: bool = False
    active_case_id: str | None = None
    # Label recorded on audit events. Not necessarily a real name (data minimisation).
    analyst_label: str = "Analyst"
    # Conservative import safety limits (configurable by user).
    max_import_mb: int = DEFAULT_MAX_IMPORT_MB
    max_image_megapixels: int = DEFAULT_MAX_MEGAPIXELS
    # Optional custom paths to external tools (empty = auto-detect bundled or PATH).
    exiftool_path: str = ""
    tesseract_path: str = ""
    tessdata_dir: str = ""
    # Session lock & backup reminders (Phase 9 hardening). Never stores plaintext secrets!
    lock_timeout_minutes: int = DEFAULT_LOCK_TIMEOUT_MINUTES
    lock_passphrase_hash: str = ""
    backup_reminder_days: int = DEFAULT_BACKUP_REMINDER_DAYS

    def validated(self) -> Settings:
        theme = self.theme if self.theme in THEMES else "system"
        label = " ".join(str(self.analyst_label or "").split())[:MAX_ANALYST_LABEL] or "Analyst"
        active = self.active_case_id if isinstance(self.active_case_id, str) else None

        def _clean_path(val: Any) -> str:
            s = val.strip() if isinstance(val, str) else ""
            if len(s) > 500 or any(ord(ch) < 32 for ch in s):
                return ""
            return s

        lock_hash = (
            self.lock_passphrase_hash if is_valid_verifier(self.lock_passphrase_hash) else ""
        )

        return Settings(
            theme=theme,
            allow_network_features=self.allow_network_features is True,
            allow_external_map=self.allow_external_map is True,
            active_case_id=active or None,
            analyst_label=label,
            max_import_mb=_bounded_int(
                self.max_import_mb, DEFAULT_MAX_IMPORT_MB, MIN_IMPORT_MB, MAX_IMPORT_MB
            ),
            max_image_megapixels=_bounded_int(
                self.max_image_megapixels,
                DEFAULT_MAX_MEGAPIXELS,
                MIN_MEGAPIXELS,
                MAX_MEGAPIXELS,
            ),
            exiftool_path=_clean_path(self.exiftool_path),
            tesseract_path=_clean_path(self.tesseract_path),
            tessdata_dir=_clean_path(self.tessdata_dir),
            lock_timeout_minutes=_bounded_int(
                self.lock_timeout_minutes,
                DEFAULT_LOCK_TIMEOUT_MINUTES,
                0,
                MAX_LOCK_TIMEOUT_MINUTES,
            ),
            lock_passphrase_hash=lock_hash,
            backup_reminder_days=_bounded_int(
                self.backup_reminder_days,
                DEFAULT_BACKUP_REMINDER_DAYS,
                0,
                MAX_BACKUP_REMINDER_DAYS,
            ),
        )


def settings_from_dict(raw: dict[str, Any]) -> Settings:
    known = {f.name for f in fields(Settings)}
    return Settings(**{k: v for k, v in raw.items() if k in known}).validated()


class SettingsStore:
    def __init__(self, db: Database | None) -> None:
        self.db = db
        self._memory = Settings()

    @property
    def persistent(self) -> bool:
        return self.db is not None

    def is_empty(self) -> bool:
        if self.db is None:
            return True
        return self.db.con.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0

    def load(self) -> Settings:
        if self.db is None:
            return replace(self._memory)
        raw: dict[str, Any] = {}
        for row in self.db.con.execute("SELECT key, value FROM settings"):
            try:
                raw[row["key"]] = json.loads(row["value"])
            except ValueError:
                log.warning("Ignoring unreadable setting %r", row["key"])
        return settings_from_dict(raw)

    def save(self, settings: Settings) -> Settings:
        s = settings.validated()
        if self.db is None:
            self._memory = replace(s)
            return s
        now = utc_now_iso()
        with self.db.transaction() as con:
            for key, value in asdict(s).items():
                con.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                    "updated_at = excluded.updated_at WHERE settings.value <> excluded.value",
                    (key, json.dumps(value), now),
                )
        return s


# --- Phase 1 legacy JSON settings ------------------------------------------------------------
def legacy_json_path(data_root: Path) -> Path:
    return data_root / "config" / "settings.json"


def import_legacy_settings(store: SettingsStore, data_root: Path) -> bool:
    """Copy Phase 1 ``config/settings.json`` into the database once. The file is left in place."""
    path = legacy_json_path(data_root)
    if not store.persistent or not store.is_empty() or not path.is_file():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(raw, dict):
        return False
    store.save(settings_from_dict(raw))
    log.info("Imported legacy settings from %s", path.name)
    return True
