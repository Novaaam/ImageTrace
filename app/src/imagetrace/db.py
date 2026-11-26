"""SQLite database: connection, transactions and schema migrations.

- Foreign keys are enforced on every connection.
- Writes go through ``Database.transaction()`` (BEGIN IMMEDIATE ... COMMIT / ROLLBACK).
- The schema version lives in ``PRAGMA user_version``. Each migration step runs in its own
  transaction together with the version bump, so a failed step leaves the database at the
  previous version, unchanged.
- An existing database is backed up before it is migrated. A database newer than this app
  is refused (never downgraded).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

DB_FILENAME = "imagetrace.sqlite"
DATA_SUBDIRS = ("cases", "database", "exports", "logs", "backups", "temp", "config")

# Each entry: version -> statements. Statements run one at a time (trigger bodies may contain ';').
MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        """
        CREATE TABLE cases (
            id          TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 64),
            title       TEXT NOT NULL CHECK (length(trim(title)) > 0),
            notes       TEXT NOT NULL DEFAULT '',
            scope_text  TEXT NOT NULL DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'archived')),
            tags_json   TEXT NOT NULL DEFAULT '[]',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_cases_status_updated ON cases (status, updated_at)",
        """
        CREATE TABLE audit_events (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id             TEXT REFERENCES cases (id) ON DELETE RESTRICT,
            evidence_id         TEXT,
            event_type          TEXT NOT NULL,
            event_time          TEXT NOT NULL,
            actor_label         TEXT NOT NULL,
            details_json        TEXT NOT NULL DEFAULT '{}',
            previous_event_hash TEXT,
            event_hash          TEXT NOT NULL UNIQUE
        )
        """,
        "CREATE INDEX idx_audit_case ON audit_events (case_id, id)",
        """
        CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events
        BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END
        """,
        """
        CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events
        BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END
        """,
        """
        CREATE TABLE settings (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
        """,
    ),
    2: (
        """
        CREATE TABLE evidence (
            id                    TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 64),
            case_id               TEXT NOT NULL REFERENCES cases (id) ON DELETE RESTRICT,
            stored_filename       TEXT NOT NULL CHECK (length(trim(stored_filename)) > 0),
            original_filename     TEXT NOT NULL CHECK (length(trim(original_filename)) > 0),
            file_size             INTEGER NOT NULL CHECK (file_size >= 0),
            detected_format       TEXT NOT NULL,
            detected_mime         TEXT NOT NULL,
            sha256                TEXT NOT NULL CHECK (length(sha256) = 64),
            imported_at           TEXT NOT NULL,
            import_source_note    TEXT NOT NULL DEFAULT '',
            status                TEXT NOT NULL DEFAULT 'imported'
                                      CHECK (status IN ('imported', 'warning', 'error')),
            width                 INTEGER,
            height                INTEGER,
            color_mode            TEXT,
            icc_profile_info      TEXT,
            dhash                 TEXT,
            thumbnail_filename    TEXT,
            warnings_json         TEXT NOT NULL DEFAULT '[]',
            analysis_tool_version TEXT NOT NULL DEFAULT ''
        )
        """,
        "CREATE INDEX idx_evidence_case_imported ON evidence (case_id, imported_at DESC, id)",
        "CREATE INDEX idx_evidence_case_sha256 ON evidence (case_id, sha256)",
        "CREATE INDEX idx_audit_evidence ON audit_events (evidence_id, id)",
    ),
    3: (
        """
        CREATE TABLE analysis_runs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            evidence_id   TEXT NOT NULL REFERENCES evidence (id) ON DELETE RESTRICT,
            module        TEXT NOT NULL,
            version       TEXT NOT NULL,
            started_at    TEXT NOT NULL,
            finished_at   TEXT NOT NULL,
            status        TEXT NOT NULL CHECK (status IN ('ok', 'warning', 'error')),
            output_json   TEXT NOT NULL DEFAULT '{}',
            warnings_json TEXT NOT NULL DEFAULT '[]'
        )
        """,
        "CREATE INDEX idx_analysis_runs_evidence ON analysis_runs (evidence_id, module, id DESC)",
        """
        CREATE TABLE metadata_records (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_run_id  INTEGER NOT NULL REFERENCES analysis_runs (id) ON DELETE RESTRICT,
            evidence_id      TEXT NOT NULL REFERENCES evidence (id) ON DELETE RESTRICT,
            group_name       TEXT NOT NULL,
            category         TEXT NOT NULL,
            origin           TEXT NOT NULL CHECK (
                                 origin IN ('embedded', 'file_container', 'application_system')
                             ),
            field_name       TEXT NOT NULL,
            raw_value        TEXT NOT NULL,
            normalized_value TEXT NOT NULL,
            source_tool      TEXT NOT NULL,
            source_version   TEXT NOT NULL,
            extracted_at     TEXT NOT NULL
        )
        """,
        (
            "CREATE INDEX idx_metadata_evidence_run ON metadata_records "
            "(evidence_id, analysis_run_id, category)"
        ),
    ),
    4: (
        """
        CREATE TABLE gps_records (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_run_id INTEGER NOT NULL REFERENCES analysis_runs (id) ON DELETE RESTRICT,
            evidence_id     TEXT NOT NULL REFERENCES evidence (id) ON DELETE RESTRICT,
            raw_fields_json TEXT NOT NULL,
            latitude        REAL,
            longitude       REAL,
            altitude        REAL,
            gps_timestamp   TEXT,
            source_field    TEXT NOT NULL,
            parser_version  TEXT NOT NULL,
            is_valid        INTEGER NOT NULL CHECK (is_valid IN (0, 1)),
            warnings_json   TEXT NOT NULL DEFAULT '[]',
            extracted_at    TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_gps_evidence_run ON gps_records (evidence_id, analysis_run_id DESC)",
    ),
    5: (
        """
        CREATE TABLE ocr_runs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            evidence_id    TEXT NOT NULL REFERENCES evidence (id) ON DELETE RESTRICT,
            engine         TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            language       TEXT NOT NULL,
            run_at         TEXT NOT NULL,
            status         TEXT NOT NULL CHECK (status IN ('ok', 'warning', 'error')),
            full_text      TEXT NOT NULL DEFAULT '',
            warnings_json  TEXT NOT NULL DEFAULT '[]'
        )
        """,
        "CREATE INDEX idx_ocr_runs_evidence ON ocr_runs (evidence_id, id DESC)",
        """
        CREATE TABLE ocr_items (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            ocr_run_id        INTEGER NOT NULL REFERENCES ocr_runs (id) ON DELETE RESTRICT,
            text              TEXT NOT NULL,
            confidence        REAL,
            bounding_box_json TEXT NOT NULL DEFAULT '{}'
        )
        """,
        "CREATE INDEX idx_ocr_items_run ON ocr_items (ocr_run_id, id)",
    ),
    6: (
        """
        CREATE TABLE notes (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id            TEXT NOT NULL REFERENCES cases (id) ON DELETE RESTRICT,
            evidence_id        TEXT REFERENCES evidence (id) ON DELETE RESTRICT,
            note_text          TEXT NOT NULL CHECK (length(trim(note_text)) > 0),
            category           TEXT NOT NULL,
            confidence_label   TEXT NOT NULL CHECK (
                                   confidence_label IN (
                                       'Confirmed from file',
                                       'Corroborated',
                                       'Analyst observation',
                                       'Unverified hypothesis'
                                   )
                               ),
            source_reference   TEXT NOT NULL DEFAULT '',
            author_label       TEXT NOT NULL,
            status             TEXT NOT NULL DEFAULT 'active'
                                   CHECK (status IN ('active', 'superseded', 'retracted')),
            supersedes_note_id INTEGER REFERENCES notes (id) ON DELETE RESTRICT,
            created_at         TEXT NOT NULL,
            updated_at         TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_notes_case_evidence ON notes (case_id, evidence_id, status, id DESC)",
        """
        CREATE TRIGGER notes_no_delete BEFORE DELETE ON notes
        BEGIN SELECT RAISE(ABORT, 'notes are append-only and cannot be deleted'); END
        """,
    ),
    7: (
        """
        CREATE TABLE exports (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id      TEXT NOT NULL REFERENCES cases (id) ON DELETE RESTRICT,
            evidence_id  TEXT REFERENCES evidence (id) ON DELETE RESTRICT,
            export_type  TEXT NOT NULL CHECK (export_type IN ('json_report', 'pdf_report')),
            path         TEXT NOT NULL CHECK (length(trim(path)) > 0),
            sha256       TEXT NOT NULL CHECK (length(sha256) = 64),
            actor_label  TEXT NOT NULL,
            options_json TEXT NOT NULL DEFAULT '{}',
            created_at   TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_exports_case ON exports (case_id, id DESC)",
        """
        CREATE TRIGGER exports_no_delete BEFORE DELETE ON exports
        BEGIN SELECT RAISE(ABORT, 'exports history is append-only and cannot be deleted'); END
        """,
    ),
}
SCHEMA_VERSION = max(MIGRATIONS)


class DatabaseError(Exception):
    """Database could not be opened or used."""


class MigrationError(DatabaseError):
    """A schema migration failed; the database was left at its previous version."""


class NewerSchemaError(DatabaseError):
    """The database was created by a newer ImageTrace version."""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        try:
            # isolation_level=None: autocommit; transactions are explicit (see transaction()).
            self.con = sqlite3.connect(self.path, isolation_level=None, timeout=5.0)
        except sqlite3.Error as exc:
            raise DatabaseError(f"Cannot open database {self.path}: {exc}") from exc
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.execute("PRAGMA busy_timeout = 5000")
        self._closed = False

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self.con.in_transaction:
            raise DatabaseError("Nested transactions are not supported.")
        self.con.execute("BEGIN IMMEDIATE")
        try:
            yield self.con
            self.con.execute("COMMIT")
        except BaseException:
            if self.con.in_transaction:
                self.con.execute("ROLLBACK")
            raise

    @property
    def schema_version(self) -> int:
        return int(self.con.execute("PRAGMA user_version").fetchone()[0])

    def integrity_check(self) -> list[str]:
        """Return a list of problems (empty list = OK)."""
        problems = [r[0] for r in self.con.execute("PRAGMA integrity_check") if r[0] != "ok"]
        problems += [
            f"Foreign key violation in {r[0]} (rowid {r[1]})"
            for r in self.con.execute("PRAGMA foreign_key_check")
        ]
        return problems

    def close(self) -> None:
        if not self._closed:
            self.con.close()
            self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


@dataclass(frozen=True)
class MigrationResult:
    from_version: int
    to_version: int
    backup_path: Path | None


def migrate(db: Database, backups_dir: Path, target: int | None = None) -> MigrationResult:
    target = SCHEMA_VERSION if target is None else target
    current = db.schema_version
    if current > target:
        raise NewerSchemaError(
            f"Database schema v{current} is newer than this ImageTrace version supports "
            f"(v{target}). It was not modified."
        )
    if current == target:
        return MigrationResult(current, current, None)

    backup_path = None
    if current > 0:
        from imagetrace.backup import backup_database  # local import: backup imports db

        backup_path = backup_database(db, backups_dir, label=f"pre-migration-v{current}").path
        log.info("Backed up database before migration: %s", backup_path.name)

    for version in range(current + 1, target + 1):
        try:
            with db.transaction() as con:
                for stmt in MIGRATIONS[version]:
                    con.execute(stmt)
                con.execute(f"PRAGMA user_version = {int(version)}")
        except (sqlite3.Error, KeyError) as exc:
            raise MigrationError(
                f"Migration to schema v{version} failed: {exc}. The database was left at "
                f"v{version - 1}; nothing was discarded."
                + (f" Pre-migration backup: {backup_path}" if backup_path else "")
            ) from exc
        log.info("Database migrated to schema v%d", version)
    return MigrationResult(current, target, backup_path)


def ensure_data_layout(data_root: Path) -> None:
    """Create the standard sub-folders inside an existing data root (never the root itself)."""
    if not data_root.is_dir():
        raise DatabaseError(f"Data root not found: {data_root}")
    for name in DATA_SUBDIRS:
        (data_root / name).mkdir(exist_ok=True)


def database_path(data_root: Path) -> Path:
    return data_root / "database" / DB_FILENAME


def open_database(data_root: Path) -> Database:
    ensure_data_layout(data_root)
    db = Database(database_path(data_root))
    try:
        migrate(db, data_root / "backups")
    except BaseException:
        db.close()
        raise
    return db
