"""Evidence import, safe original storage, integrity verification and similarity lookup.

Rules (blueprint sections 4, 5D, 8, 9):
- Original evidence files are copied into ``cases/<case-id>/originals/<evidence-id><ext>``
  and marked read-only. They are never modified.
- Stored filenames are app-generated; user-supplied names never become filesystem paths.
- Source directory paths are NOT stored automatically (only the sanitised basename, plus an
  optional analyst-supplied note).
- D-drive / data-root free space is checked before copying.
- Duplicate SHA-256 hashes within a case raise ``DuplicateEvidenceError`` unless explicitly
  confirmed by the user.
- Perceptual similarity (dHash) is explicitly labelled as a heuristic, not proof of identity.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from imagetrace import APP_NAME, __version__
from imagetrace.audit import append_event
from imagetrace.cases import CASE_ID_RE, CaseNotFoundError, ValidationError, case_dir
from imagetrace.db import Database, DatabaseError
from imagetrace.hashing import CHUNK, sha256_file
from imagetrace.image_analysis import (
    DEFAULT_SIMILARITY_MAX_DISTANCE,
    DecompressionBombRejectedError,
    DerivedCopyResult,
    analyze_image,
    check_header_pixel_limit,
    create_derived_copy,
    detect_file_type,
    dhash_distance,
    extension_mismatch_warning,
    pillow_version,
)
from imagetrace.paths import LOW_SPACE_BYTES, check_storage, format_bytes
from imagetrace.security import is_windows_reserved_stem
from imagetrace.timefmt import utc_now_iso

log = logging.getLogger(__name__)

EVIDENCE_ID_RE = re.compile(r"^E-\d{8}-[0-9A-F]{6}$")
STORED_NAME_RE = re.compile(r"^E-\d{8}-[0-9A-F]{6}\.[a-z0-9]{2,5}$")
THUMB_NAME_RE = re.compile(r"^E-\d{8}-[0-9A-F]{6}-thumb\.png$")
IMPORT_SAFETY_MARGIN = 100 * 1024**2  # keep at least 100 MB headroom after import
MAX_SOURCE_NOTE = 500
MAX_ORIGINAL_NAME = 255
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_NOTE_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

ProgressCallback = Callable[[int, int, str], None]


class EvidenceError(Exception):
    """Base error for evidence operations (safe to show to the user)."""


class EvidenceNotFoundError(LookupError):
    pass


class UnsupportedFileError(EvidenceError):
    pass


class FileTooLargeError(EvidenceError):
    pass


class InsufficientSpaceError(EvidenceError):
    pass


class ImportCancelledError(EvidenceError):
    pass


class DuplicateEvidenceError(EvidenceError):
    def __init__(self, existing: list[Evidence], sha256: str) -> None:
        self.existing = existing
        self.sha256 = sha256
        names = ", ".join(f"{e.id} ({e.original_filename})" for e in existing[:3])
        super().__init__(f"A file with identical SHA-256 already exists in this case: {names}.")


@dataclass(frozen=True)
class Evidence:
    id: str
    case_id: str
    stored_filename: str
    original_filename: str
    file_size: int
    detected_format: str
    detected_mime: str
    sha256: str
    imported_at: str
    import_source_note: str
    status: str
    width: int | None
    height: int | None
    color_mode: str | None
    icc_profile_info: str | None
    dhash: str | None
    thumbnail_filename: str | None
    warnings: tuple[str, ...]
    analysis_tool_version: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Evidence:
        return cls(
            id=row["id"],
            case_id=row["case_id"],
            stored_filename=row["stored_filename"],
            original_filename=row["original_filename"],
            file_size=int(row["file_size"]),
            detected_format=row["detected_format"],
            detected_mime=row["detected_mime"],
            sha256=row["sha256"],
            imported_at=row["imported_at"],
            import_source_note=row["import_source_note"],
            status=row["status"],
            width=row["width"],
            height=row["height"],
            color_mode=row["color_mode"],
            icc_profile_info=row["icc_profile_info"],
            dhash=row["dhash"],
            thumbnail_filename=row["thumbnail_filename"],
            warnings=_load_warnings(row["warnings_json"]),
            analysis_tool_version=row["analysis_tool_version"],
        )


@dataclass(frozen=True)
class IntegrityResult:
    evidence_id: str
    ok: bool
    expected_sha256: str
    actual_sha256: str | None
    expected_size: int
    actual_size: int | None
    read_only: bool | None
    message: str


@dataclass(frozen=True)
class SimilarEvidence:
    evidence: Evidence
    hamming_distance: int
    exact_sha256_match: bool


def _load_warnings(raw: str) -> tuple[str, ...]:
    try:
        data = json.loads(raw)
    except ValueError:
        return ()
    return tuple(str(item) for item in data) if isinstance(data, list) else ()


def new_evidence_id(today: date | None = None) -> str:
    d = today or date.today()
    return f"E-{d:%Y%m%d}-{secrets.token_hex(3).upper()}"


def sanitize_original_filename(path_or_name: Path | str) -> str:
    """Keep only a safe single-line basename (never store source directory paths).

    Strips control characters and path separators, neutralizes Windows reserved device names
    (CON, PRN, AUX, NUL, COM1-9, LPT1-9), and removes trailing dots/spaces.
    """
    raw = str(path_or_name).replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _CONTROL.sub("", raw).strip().rstrip(" .")
    if cleaned in ("", ".", ".."):
        return "unnamed"
    if is_windows_reserved_stem(cleaned):
        cleaned = f"file_{cleaned}"
    if len(cleaned) > MAX_ORIGINAL_NAME:
        stem = Path(cleaned).stem[: MAX_ORIGINAL_NAME - 16]
        suffix = Path(cleaned).suffix[:16]
        cleaned = (stem + suffix) or cleaned[:MAX_ORIGINAL_NAME]
    return cleaned or "unnamed"


def validate_source_note(note: str | None) -> str:
    v = (note or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if _NOTE_CONTROL.search(v):
        raise ValidationError("Import note contains control characters.")
    if len(v) > MAX_SOURCE_NOTE:
        raise ValidationError(f"Import note is too long (maximum {MAX_SOURCE_NOTE} characters).")
    return v


def _safe_child(parent: Path, filename: str, pattern: re.Pattern[str]) -> Path:
    if not pattern.fullmatch(filename or ""):
        raise ValidationError(f"Unsafe stored filename: {filename!r}")
    root = parent.resolve()
    child = (root / filename).resolve()
    if child.parent != root:
        raise ValidationError("Resolved path escapes its evidence directory.")
    return child


def _make_readonly(path: Path) -> None:
    os.chmod(path, stat.S_IREAD)


def _unlink_force(path: Path | None) -> None:
    """Remove a newly-created file after a failed/cancelled import (clearing read-only first)."""
    if path is None or not path.exists():
        return
    with contextlib.suppress(OSError):
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("Could not clean up partial file: %s", path.name)


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ImportCancelledError("Import cancelled by user.")


def _hash_with_cancel(
    path: Path,
    total_size: int,
    cancel_event: threading.Event | None,
    on_progress: ProgressCallback | None,
) -> str:
    h = hashlib.sha256()
    done = 0
    with path.open("rb") as fh:
        while True:
            _check_cancel(cancel_event)
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done, total_size * 2, "Hashing original")
    return h.hexdigest()


def _copy_and_hash(
    source: Path,
    dest_partial: Path,
    total_size: int,
    cancel_event: threading.Event | None,
    on_progress: ProgressCallback | None,
) -> tuple[str, int]:
    h = hashlib.sha256()
    copied = 0
    dest_partial.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, dest_partial.open("wb") as dst:
        while True:
            _check_cancel(cancel_event)
            chunk = src.read(CHUNK)
            if not chunk:
                break
            dst.write(chunk)
            h.update(chunk)
            copied += len(chunk)
            if on_progress:
                on_progress(total_size + copied, total_size * 2, "Copying original")
        dst.flush()
        os.fsync(dst.fileno())
    return h.hexdigest(), copied


def _tool_version_string() -> str:
    return f"{APP_NAME} {__version__}; Pillow {pillow_version()}"


class EvidenceRepository:
    def __init__(
        self,
        db: Database,
        data_root: Path,
        actor: Callable[[], str] = lambda: "Analyst",
        id_factory: Callable[[], str] = new_evidence_id,
    ) -> None:
        self.db = db
        self.data_root = data_root
        self.cases_root = data_root / "cases"
        self.actor = actor
        self.id_factory = id_factory

    # --- path helpers -----------------------------------------------------------------
    def originals_dir(self, case_id: str) -> Path:
        return case_dir(self.cases_root, case_id) / "originals"

    def derived_dir(self, case_id: str) -> Path:
        return case_dir(self.cases_root, case_id) / "derived"

    def original_path(self, item: Evidence) -> Path:
        return _safe_child(self.originals_dir(item.case_id), item.stored_filename, STORED_NAME_RE)

    def thumbnail_path(self, item: Evidence) -> Path | None:
        if not item.thumbnail_filename:
            return None
        return _safe_child(self.derived_dir(item.case_id), item.thumbnail_filename, THUMB_NAME_RE)

    # --- queries ----------------------------------------------------------------------
    def get(self, evidence_id: str) -> Evidence | None:
        row = self.db.con.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
        return Evidence.from_row(row) if row else None

    def get_required(self, evidence_id: str) -> Evidence:
        item = self.get(evidence_id)
        if item is None:
            raise EvidenceNotFoundError(f"Evidence not found: {evidence_id}")
        return item

    def list_for_case(self, case_id: str, search: str = "") -> list[Evidence]:
        text = (search or "").strip()
        if not text:
            rows = self.db.con.execute(
                "SELECT * FROM evidence WHERE case_id = ? ORDER BY imported_at DESC, id",
                (case_id,),
            )
            return [Evidence.from_row(r) for r in rows]
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pat = f"%{escaped}%"
        rows = self.db.con.execute(
            "SELECT * FROM evidence WHERE case_id = ? AND ("
            "id LIKE ? ESCAPE '\\' OR original_filename LIKE ? ESCAPE '\\' "
            "OR sha256 LIKE ? ESCAPE '\\' OR detected_format LIKE ? ESCAPE '\\'"
            ") ORDER BY imported_at DESC, id",
            (case_id, pat, pat, pat, pat),
        )
        return [Evidence.from_row(r) for r in rows]

    def recent(self, limit: int = 5) -> list[Evidence]:
        rows = self.db.con.execute(
            "SELECT e.* FROM evidence e "
            "JOIN cases c ON c.id = e.case_id "
            "WHERE c.status = 'open' "
            "ORDER BY e.imported_at DESC, e.id LIMIT ?",
            (int(limit),),
        )
        return [Evidence.from_row(r) for r in rows]

    def count_for_case(self, case_id: str) -> int:
        return int(
            self.db.con.execute(
                "SELECT COUNT(*) FROM evidence WHERE case_id = ?", (case_id,)
            ).fetchone()[0]
        )

    def find_by_sha256(self, case_id: str, sha256: str) -> list[Evidence]:
        rows = self.db.con.execute(
            "SELECT * FROM evidence WHERE case_id = ? AND sha256 = ? ORDER BY imported_at",
            (case_id, sha256),
        )
        return [Evidence.from_row(r) for r in rows]

    def find_similar(
        self, evidence_id: str, max_distance: int = DEFAULT_SIMILARITY_MAX_DISTANCE
    ) -> list[SimilarEvidence]:
        target = self.get_required(evidence_id)
        if not target.dhash:
            return []
        matches: list[SimilarEvidence] = []
        for other in self.list_for_case(target.case_id):
            if other.id == target.id or not other.dhash:
                continue
            dist = dhash_distance(target.dhash, other.dhash)
            if dist is not None and dist <= max_distance:
                matches.append(
                    SimilarEvidence(
                        evidence=other,
                        hamming_distance=dist,
                        exact_sha256_match=(other.sha256 == target.sha256),
                    )
                )
        matches.sort(key=lambda m: (m.hamming_distance, m.evidence.id))
        return matches

    # --- commands ---------------------------------------------------------------------
    def _verify_open_case(self, case_id: str) -> None:
        if not CASE_ID_RE.fullmatch(case_id or ""):
            raise ValidationError(f"Invalid case ID: {case_id!r}")
        row = self.db.con.execute("SELECT status FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            raise CaseNotFoundError(f"Case not found: {case_id}")
        if row["status"] == "archived":
            raise ValidationError(
                "Cannot import evidence into an archived case. Reopen the case first."
            )

    def _next_unique_id(self) -> str:
        for _ in range(5):
            eid = self.id_factory()
            if not EVIDENCE_ID_RE.fullmatch(eid):
                raise ValidationError(f"Generated evidence ID has an invalid format: {eid!r}")
            if not self.db.con.execute("SELECT 1 FROM evidence WHERE id = ?", (eid,)).fetchone():
                return eid
        raise DatabaseError("Could not generate a unique evidence ID.")

    def import_file(
        self,
        case_id: str,
        source_path: Path,
        *,
        source_note: str = "",
        max_size_bytes: int = 50 * 1024**2,
        max_pixels: int = 50_000_000,
        allow_duplicate: bool = False,
        cancel_event: threading.Event | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> Evidence:
        """Validate, copy, hash, analyse and record one evidence image.

        Never modifies *source_path* or the stored original.
        """
        _check_cancel(cancel_event)
        self._verify_open_case(case_id)
        note = validate_source_note(source_note)
        src = Path(source_path)

        # 1. Basic source checks (refuse directories, missing files, symlinks to missing, empty)
        if not src.exists():
            raise EvidenceError(f"File not found: {src.name}")
        if not src.is_file():
            raise UnsupportedFileError(f"Not a regular file: {src.name}")
        file_size = src.stat().st_size
        if file_size <= 0:
            raise UnsupportedFileError(f"File is empty (0 bytes): {src.name}")
        if file_size > max_size_bytes:
            raise FileTooLargeError(
                f"File '{src.name}' is {format_bytes(file_size)}, which exceeds the configured "
                f"limit of {format_bytes(max_size_bytes)}. Adjust the limit in Settings if needed."
            )

        # 2. Storage availability & free-space check (no silent fallback)
        storage = check_storage(self.data_root)
        if not storage.available:
            raise InsufficientSpaceError(storage.message)
        needed = file_size * 2 + IMPORT_SAFETY_MARGIN
        free = shutil.disk_usage(self.data_root).free
        if free < needed:
            raise InsufficientSpaceError(
                f"Not enough free space in {self.data_root} ({format_bytes(free)} free; "
                f"need at least {format_bytes(needed)})."
            )

        # 3. File signature (magic bytes) & decompression-bomb header check
        detected = detect_file_type(src)
        if not detected.supported:
            raise UnsupportedFileError(
                f"Rejected '{src.name}' ({detected.format_name}): {detected.reason}"
            )
        try:
            check_header_pixel_limit(src, max_pixels)
        except DecompressionBombRejectedError as exc:
            raise UnsupportedFileError(str(exc)) from exc

        # 4. Pre-copy SHA-256 + duplicate check
        source_sha = _hash_with_cancel(src, file_size, cancel_event, on_progress)
        existing_dups = self.find_by_sha256(case_id, source_sha)
        if existing_dups and not allow_duplicate:
            raise DuplicateEvidenceError(existing_dups, source_sha)

        # 5. Copy to cases/<case_id>/originals/<evidence_id><ext> via .partial, verify hash
        evidence_id = self._next_unique_id()
        stored_filename = f"{evidence_id}{detected.canonical_ext}"
        orig_dir = self.originals_dir(case_id)
        drv_dir = self.derived_dir(case_id)
        orig_dir.mkdir(parents=True, exist_ok=True)
        drv_dir.mkdir(parents=True, exist_ok=True)
        final_path = _safe_child(orig_dir, stored_filename, STORED_NAME_RE)
        partial_path = orig_dir / f"{stored_filename}.partial"
        thumb_path: Path | None = None
        created_final = False

        try:
            copied_sha, copied_size = _copy_and_hash(
                src, partial_path, file_size, cancel_event, on_progress
            )
            _check_cancel(cancel_event)
            if copied_sha != source_sha or copied_size != file_size:
                raise EvidenceError(
                    "File changed or failed verification during copy (SHA-256 mismatch). "
                    "Import aborted."
                )
            os.replace(partial_path, final_path)
            created_final = True
            _make_readonly(final_path)

            # 6. Analyse the stored original (read-only) -> dimensions, thumbnail, dHash
            _check_cancel(cancel_event)
            orig_name = sanitize_original_filename(src)
            warnings_list: list[str] = []
            if free < LOW_SPACE_BYTES:
                warnings_list.append(
                    f"Low disk space on data root ({format_bytes(free)} remaining)."
                )
            ext_warn = extension_mismatch_warning(orig_name, detected)
            if ext_warn:
                warnings_list.append(ext_warn)
            if existing_dups:
                dup_ids = ", ".join(e.id for e in existing_dups[:5])
                warnings_list.append(
                    f"Imported as a user-confirmed duplicate of existing evidence: {dup_ids}."
                )

            try:
                props = analyze_image(final_path, drv_dir, evidence_id, detected, max_pixels)
            except DecompressionBombRejectedError as exc:
                raise UnsupportedFileError(str(exc)) from exc
            if props.thumbnail_filename:
                thumb_path = drv_dir / props.thumbnail_filename
            warnings_list.extend(props.warnings)
            status = "imported" if (props.decode_ok and not warnings_list) else "warning"

            # Confirm stored original was never altered by analysis
            if sha256_file(final_path) != source_sha:
                raise EvidenceError("Stored original failed post-analysis SHA-256 check.")

            _check_cancel(cancel_event)
            now = utc_now_iso()
            tool_ver = _tool_version_string()
            with self.db.transaction() as con:
                con.execute(
                    "INSERT INTO evidence ("
                    "id, case_id, stored_filename, original_filename, file_size, "
                    "detected_format, detected_mime, sha256, imported_at, import_source_note, "
                    "status, width, height, color_mode, icc_profile_info, dhash, "
                    "thumbnail_filename, warnings_json, analysis_tool_version"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        evidence_id,
                        case_id,
                        stored_filename,
                        orig_name,
                        file_size,
                        detected.format_name,
                        detected.mime,
                        source_sha,
                        now,
                        note,
                        status,
                        props.width,
                        props.height,
                        props.color_mode,
                        props.icc_profile_info,
                        props.dhash,
                        props.thumbnail_filename,
                        json.dumps(warnings_list, ensure_ascii=False),
                        tool_ver,
                    ),
                )
                append_event(
                    con,
                    "evidence_imported",
                    case_id=case_id,
                    evidence_id=evidence_id,
                    actor_label=self.actor(),
                    details={
                        "detected_format": detected.format_name,
                        "file_size": file_size,
                        "sha256": source_sha,
                        "status": status,
                        "duplicate": bool(existing_dups),
                        "warnings_count": len(warnings_list),
                        "tool_version": tool_ver,
                    },
                )
        except BaseException:
            _unlink_force(partial_path)
            if created_final:
                _unlink_force(final_path)
            _unlink_force(thumb_path)
            raise

        log.info(
            "Evidence imported: %s into %s (%s, %d bytes)",
            evidence_id,
            case_id,
            detected.format_name,
            file_size,
        )
        return self.get_required(evidence_id)

    def verify_integrity(self, evidence_id: str) -> IntegrityResult:
        """Recompute SHA-256 of the stored original and log the verification event."""
        item = self.get_required(evidence_id)
        path = self.original_path(item)
        if not path.is_file():
            result = IntegrityResult(
                evidence_id=item.id,
                ok=False,
                expected_sha256=item.sha256,
                actual_sha256=None,
                expected_size=item.file_size,
                actual_size=None,
                read_only=None,
                message="Stored original file is missing from disk.",
            )
        else:
            st = path.stat()
            actual_size = st.st_size
            actual_sha = sha256_file(path)
            read_only = not bool(st.st_mode & stat.S_IWRITE)
            ok = actual_sha == item.sha256 and actual_size == item.file_size
            if ok:
                msg = "Verified: SHA-256 and file size match the recorded import values."
                if not read_only:
                    msg += " (Note: file is not marked read-only on disk.)"
            else:
                msg = (
                    "INTEGRITY MISMATCH: the stored original no longer matches its recorded "
                    "SHA-256 or file size."
                )
            result = IntegrityResult(
                evidence_id=item.id,
                ok=ok,
                expected_sha256=item.sha256,
                actual_sha256=actual_sha,
                expected_size=item.file_size,
                actual_size=actual_size,
                read_only=read_only,
                message=msg,
            )

        with self.db.transaction() as con:
            append_event(
                con,
                "evidence_integrity_checked",
                case_id=item.case_id,
                evidence_id=item.id,
                actor_label=self.actor(),
                details={
                    "ok": result.ok,
                    "expected_sha256": result.expected_sha256,
                    "actual_sha256": result.actual_sha256,
                },
            )
        return result

    def create_derived_view(
        self,
        evidence_id: str,
        *,
        rotate_deg: int = 0,
        contrast: float = 1.0,
        max_edge: int | None = None,
        max_pixels: int = 50_000_000,
    ) -> DerivedCopyResult:
        """Create a derived processing copy without modifying the original."""
        item = self.get_required(evidence_id)
        orig = self.original_path(item)
        if not orig.is_file():
            raise EvidenceError("Stored original file is missing from disk.")
        storage = check_storage(self.data_root)
        if not storage.available:
            raise InsufficientSpaceError(storage.message)
        if shutil.disk_usage(self.data_root).free < IMPORT_SAFETY_MARGIN:
            raise InsufficientSpaceError("Not enough free space to write a derived copy.")

        try:
            result = create_derived_copy(
                orig,
                self.derived_dir(item.case_id),
                item.id,
                rotate_deg=rotate_deg,
                contrast=contrast,
                max_edge=max_edge,
                max_pixels=max_pixels,
            )
        except DecompressionBombRejectedError as exc:
            raise UnsupportedFileError(str(exc)) from exc

        if result.original_sha256_after != item.sha256:
            raise EvidenceError("Original file SHA-256 no longer matches the recorded value.")

        with self.db.transaction() as con:
            append_event(
                con,
                "derived_copy_created",
                case_id=item.case_id,
                evidence_id=item.id,
                actor_label=self.actor(),
                details={
                    "derived_filename": result.filename,
                    "sha256": result.sha256,
                    "rotate_deg": rotate_deg,
                    "contrast": round(contrast, 2),
                    "max_edge": max_edge,
                    "tool_version": _tool_version_string(),
                },
            )
        return result
