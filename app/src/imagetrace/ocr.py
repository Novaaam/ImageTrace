"""Offline, user-triggered OCR via Tesseract CLI (Phase 6).

Rules (blueprint sections 3, 4, 5C, 7, 9, 11):
- User-invoked only; never runs automatically on import or tab switch.
- Offline only; never silently downloads language packs or uploads evidence.
- Calls Tesseract via subprocess with argument lists (never ``shell=True``) and timeouts.
- Feeds Tesseract a safe, Pillow-decoded, EXIF-transposed PNG processing copy in
  ``<data_root>/temp/`` (deleted in ``finally:``) so originals are never modified and
  decompression bombs / corrupt containers are caught safely before invoking Tesseract.
- Labels all results as **Automated result** and treats extracted text strictly as untrusted
  data (never interpreted as HTML, shell commands, app control or AI prompts).
- Never logs raw OCR text to application logs or audit event details.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import sqlite3
import subprocess
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from imagetrace import APP_NAME, __version__
from imagetrace.audit import append_event
from imagetrace.cases import ValidationError, case_dir
from imagetrace.db import Database
from imagetrace.evidence import EvidenceRepository
from imagetrace.hashing import sha256_file
from imagetrace.image_analysis import _PIL_LOCK, DecompressionBombRejectedError
from imagetrace.timefmt import utc_now_iso
from imagetrace.tools_status import find_tesseract

log = logging.getLogger(__name__)

OCR_ENGINE_NAME = "Tesseract"
OCR_TIMEOUT_SECONDS = 60
VERSION_TIMEOUT_SECONDS = 10
LOW_CONFIDENCE_THRESHOLD = 60.0

OCR_DISCLAIMER = (
    "Automated OCR result (machine-generated) - may contain errors or misread text. "
    "Extracted text is untrusted data for analyst review only; it is never treated as "
    "confirmed fact, app instructions, or AI prompts."
)

TESSERACT_INSTALL_GUIDE = (
    "Offline Tesseract OCR setup (Windows, D-drive recommended):\n\n"
    "1. Download the official Windows 64-bit Tesseract installer (Apache-2.0) from:\n"
    "   https://github.com/UB-Mannheim/tesseract/wiki\n"
    "2. During installation, change the destination folder from C:\\Program Files to a D-drive "
    "path such as D:\\imagetrace\\tools\\third-party\\tesseract\\\n"
    "3. Language packs (optional): ImageTrace never downloads language packs silently. "
    "Select desired languages in the installer OR manually place verified *.traineddata files "
    "(e.g. from https://github.com/tesseract-ocr/tessdata_fast) into your tessdata folder.\n"
    "4. Open Settings -> External tools in ImageTrace, set or auto-detect tesseract.exe "
    "(and optionally your tessdata folder), then click Test."
)

_LANG_RE = re.compile(r"^[A-Za-z0-9_]{2,20}(?:\+[A-Za-z0-9_]{2,20})*$")
_VERSION_EXTRACT_RE = re.compile(r"(\d+\.\d+(?:\.\d+)*(?:[-_.][A-Za-z0-9]+)?)")
_CONTROL_STRIP = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class OcrError(Exception):
    """OCR execution or export failed (safe to show to the user)."""


class TesseractNotFoundError(OcrError):
    pass


@dataclass(frozen=True)
class ParsedOcrItem:
    text: str
    confidence: float | None
    bounding_box: dict[str, int]


@dataclass(frozen=True)
class OcrItem:
    id: int
    ocr_run_id: int
    text: str
    confidence: float | None
    bounding_box: dict[str, Any]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> OcrItem:
        try:
            bbox = json.loads(row["bounding_box_json"])
        except ValueError:
            bbox = {}
        return cls(
            id=int(row["id"]),
            ocr_run_id=int(row["ocr_run_id"]),
            text=row["text"],
            confidence=float(row["confidence"]) if row["confidence"] is not None else None,
            bounding_box=bbox if isinstance(bbox, dict) else {},
        )


@dataclass(frozen=True)
class OcrRun:
    id: int
    evidence_id: str
    engine: str
    engine_version: str
    language: str
    run_at: str
    status: str
    full_text: str
    warnings: tuple[str, ...]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> OcrRun:
        try:
            warns = json.loads(row["warnings_json"])
        except ValueError:
            warns = []
        return cls(
            id=int(row["id"]),
            evidence_id=row["evidence_id"],
            engine=row["engine"],
            engine_version=row["engine_version"],
            language=row["language"],
            run_at=row["run_at"],
            status=row["status"],
            full_text=row["full_text"],
            warnings=tuple(str(w) for w in warns) if isinstance(warns, list) else (),
        )


@dataclass(frozen=True)
class OcrRunResult:
    run: OcrRun
    items: list[OcrItem]
    mean_confidence: float | None


@dataclass(frozen=True)
class OcrExportResult:
    path: Path
    sha256: str
    fmt: str
    item_count: int


# Injectable runner signature: (image_path, language, tessdata_dir) -> (engine, version, tsv)
OcrRunner = Callable[[Path, str, str], tuple[str, str, str]]


# --- validation & TSV parsing -------------------------------------------------------------
def validate_language(language: str) -> str:
    lang = (language or "").strip()
    if not lang or not _LANG_RE.fullmatch(lang):
        raise ValidationError(
            "Invalid OCR language code. Use Tesseract codes like 'eng' or 'eng+deu' "
            "(letters, digits, underscore, and '+' only)."
        )
    return lang


def sanitize_ocr_text(text: str) -> str:
    """Strip non-printable control chars while keeping untrusted visible text intact as data."""
    return _CONTROL_STRIP.sub("", text or "")


def parse_tesseract_tsv(tsv_text: str) -> tuple[str, list[ParsedOcrItem], list[str]]:
    """Parse Tesseract TSV output into (reconstructed_text, word_items, warnings).

    Handles malformed lines, non-numeric columns and negative confidences cleanly.
    """
    warnings_list: list[str] = []
    if not tsv_text or not tsv_text.strip():
        warnings_list.append(
            "No visible text was detected by OCR. This does not prove the image contains no text."
        )
        return "", [], warnings_list

    lines = tsv_text.lstrip("\ufeff").splitlines()
    if not lines:
        return "", [], warnings_list

    header = lines[0].split("\t")
    expected_cols = ("level", "left", "top", "width", "height", "conf", "text")
    if not all(col in header for col in expected_cols):
        raise OcrError("OCR engine returned an unrecognized TSV header format.")

    col_idx = {name: i for i, name in enumerate(header)}
    items: list[ParsedOcrItem] = []
    # Group words by (page, block, par, line) to reconstruct readable lines & paragraphs
    line_groups: dict[tuple[int, int, int, int], list[str]] = {}
    line_order: list[tuple[int, int, int, int]] = []
    malformed_rows = 0

    def _int_col(parts: list[str], name: str, default: int = 0) -> int:
        i = col_idx.get(name)
        if i is None or i >= len(parts):
            return default
        return int(parts[i])

    for raw_line in lines[1:]:
        if not raw_line:
            continue
        parts = raw_line.split("\t")
        if len(parts) < len(header):
            malformed_rows += 1
            continue
        try:
            level = _int_col(parts, "level", -1)
        except ValueError:
            malformed_rows += 1
            continue
        if level != 5:
            continue  # word-level entries only

        raw_word = sanitize_ocr_text(parts[col_idx["text"]]).strip()
        if not raw_word:
            continue

        try:
            left = _int_col(parts, "left")
            top = _int_col(parts, "top")
            width = _int_col(parts, "width")
            height = _int_col(parts, "height")
            page = _int_col(parts, "page_num", 1)
            block = _int_col(parts, "block_num", 0)
            par = _int_col(parts, "par_num", 0)
            line_num = _int_col(parts, "line_num", 0)
        except ValueError:
            malformed_rows += 1
            continue

        conf_raw = parts[col_idx["conf"]].strip()
        conf: float | None = None
        if conf_raw:
            try:
                c_val = float(conf_raw)
                if 0.0 <= c_val <= 100.0:
                    conf = round(c_val, 2)
            except ValueError:
                conf = None

        bbox = {
            "left": left,
            "top": top,
            "width": width,
            "height": height,
            "page": page,
            "block": block,
            "paragraph": par,
            "line": line_num,
        }
        items.append(ParsedOcrItem(text=raw_word, confidence=conf, bounding_box=bbox))
        key = (page, block, par, line_num)
        if key not in line_groups:
            line_groups[key] = []
            line_order.append(key)
        line_groups[key].append(raw_word)

    # Reconstruct readable text with blank lines between paragraphs/blocks
    text_chunks: list[str] = []
    prev_par: tuple[int, int, int] | None = None
    for key in line_order:
        par_key = key[:3]
        if prev_par is not None and par_key != prev_par:
            text_chunks.append("")
        text_chunks.append(" ".join(line_groups[key]))
        prev_par = par_key
    full_text = "\n".join(text_chunks)

    if malformed_rows:
        warnings_list.append(
            f"Ignored {malformed_rows} malformed TSV row(s) returned by the OCR engine."
        )
    if not items:
        warnings_list.append(
            "No visible text was detected by OCR. This does not prove the image contains no text "
            "(low contrast, skew, handwriting, or missing language data can cause misses)."
        )
    else:
        confs = [it.confidence for it in items if it.confidence is not None]
        if confs:
            avg = sum(confs) / len(confs)
            if avg < LOW_CONFIDENCE_THRESHOLD:
                warnings_list.append(
                    f"Low average OCR confidence ({avg:.1f}%); review extracted text carefully "
                    "against the image."
                )

    return full_text, items, warnings_list


def compute_mean_confidence(items: list[OcrItem]) -> float | None:
    vals = [it.confidence for it in items if it.confidence is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 1)


# --- Tesseract CLI subprocess helpers -----------------------------------------------------
def query_tesseract_version(exe_path: Path) -> str:
    if not exe_path.is_file():
        raise TesseractNotFoundError(f"Tesseract executable not found: {exe_path}")
    try:
        proc = subprocess.run(  # noqa: S603 (argument list only, no shell, validated exe path)
            [str(exe_path), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=VERSION_TIMEOUT_SECONDS,
            creationflags=_CREATIONFLAGS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OcrError("Tesseract timed out while reporting its version.") from exc
    except OSError as exc:
        raise OcrError(f"Could not launch Tesseract: {exc}") from exc

    first = (proc.stdout or proc.stderr).strip().splitlines()[:1]
    line = first[0] if first else ""
    m = _VERSION_EXTRACT_RE.search(line)
    if proc.returncode != 0 or not m:
        raise OcrError(f"Unexpected output from tesseract --version: {line[:200] or '(empty)'}")
    return m.group(1)


def list_tesseract_languages(exe_path: Path, tessdata_dir: str = "") -> list[str]:
    if not exe_path.is_file():
        return []
    cmd = [str(exe_path), "--list-langs"]
    if tessdata_dir and Path(tessdata_dir).is_dir():
        cmd += ["--tessdata-dir", tessdata_dir]
    try:
        proc = subprocess.run(  # noqa: S603 (argument list only, no shell)
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=VERSION_TIMEOUT_SECONDS,
            creationflags=_CREATIONFLAGS,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []
    langs: list[str] = []
    for raw in proc.stdout.splitlines():
        s = raw.strip()
        if not s or s.lower().startswith("list of available") or s == "osd":
            continue
        if _LANG_RE.fullmatch(s):
            langs.append(s)
    return sorted(set(langs))


def _run_tesseract_cli(
    exe_path: Path, image_path: Path, language: str, tessdata_dir: str
) -> tuple[str, str, str]:
    version = query_tesseract_version(exe_path)
    cmd = [str(exe_path), str(image_path), "stdout", "-l", language, "--oem", "3"]
    if tessdata_dir:
        if not Path(tessdata_dir).is_dir():
            raise OcrError(f"Configured tessdata folder not found: {tessdata_dir}")
        cmd += ["--tessdata-dir", tessdata_dir]
    cmd.append("tsv")
    try:
        proc = subprocess.run(  # noqa: S603 (argument list only, no shell, validated args)
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=OCR_TIMEOUT_SECONDS,
            creationflags=_CREATIONFLAGS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OcrError(f"Tesseract timed out after {OCR_TIMEOUT_SECONDS} seconds.") from exc
    except OSError as exc:
        raise OcrError(f"Could not run Tesseract: {exc}") from exc

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip()[:300]
        if "error opening data file" in err.lower() or "failed loading language" in err.lower():
            raise OcrError(
                f"Tesseract language pack '{language}' was not found. Install "
                f"'{language}.traineddata' into your tessdata folder (see Setup guide)."
            )
        raise OcrError(f"Tesseract failed (exit {proc.returncode}): {err or 'unknown error'}")
    return OCR_ENGINE_NAME, version, proc.stdout


def _render_safe_ocr_input(
    original_path: Path, temp_dir: Path, evidence_id: str, max_pixels: int
) -> Path:
    """Decode *original_path* via Pillow under the pixel ceiling into a clean temp PNG on D:."""
    temp_dir.mkdir(parents=True, exist_ok=True)
    out = temp_dir / f"ocr-{evidence_id}-{secrets.token_hex(4)}.png"
    with _PIL_LOCK:
        prev = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = max_pixels
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(original_path) as img:
                    if img.width * img.height > max_pixels:
                        raise DecompressionBombRejectedError(
                            f"Image ({img.width}x{img.height}) exceeds OCR pixel limit."
                        )
                    img.load()
                    work = img.copy()
                    with contextlib.suppress(Exception):
                        work = ImageOps.exif_transpose(work)
                    work.convert("RGB").save(out, format="PNG")
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            DecompressionBombRejectedError,
        ) as exc:
            out.unlink(missing_ok=True)
            raise OcrError(
                "Image exceeds the safety pixel limit for OCR "
                f"(potential decompression bomb): {exc}"
            ) from exc
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
            out.unlink(missing_ok=True)
            raise OcrError(
                f"Image could not be decoded safely for OCR ({type(exc).__name__}: {exc}). "
                "The original evidence file remains preserved unchanged."
            ) from exc
        finally:
            Image.MAX_IMAGE_PIXELS = prev
    return out


def _write_text_atomic(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    try:
        partial.write_text(text, encoding="utf-8")
        os.replace(partial, path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return sha256_file(path)


# --- service ------------------------------------------------------------------------------
class OcrService:
    def __init__(
        self,
        db: Database,
        evidence_repo: EvidenceRepository,
        actor: Callable[[], str] = lambda: "Analyst",
        tesseract_path_getter: Callable[[], str] = lambda: "",
        tessdata_dir_getter: Callable[[], str] = lambda: "",
        runner: OcrRunner | None = None,
    ) -> None:
        self.db = db
        self.evidence = evidence_repo
        self.actor = actor
        self.tesseract_path_getter = tesseract_path_getter
        self.tessdata_dir_getter = tessdata_dir_getter
        self._custom_runner = runner

    def resolve_tesseract(self) -> Path | None:
        return find_tesseract(self.tesseract_path_getter())

    def available_languages(self) -> list[str]:
        exe = self.resolve_tesseract()
        if exe is None:
            return ["eng"]
        langs = list_tesseract_languages(exe, self.tessdata_dir_getter())
        return langs or ["eng"]

    def reports_dir(self, case_id: str) -> Path:
        return case_dir(self.evidence.cases_root, case_id) / "reports"

    def latest_run(self, evidence_id: str) -> OcrRun | None:
        row = self.db.con.execute(
            "SELECT * FROM ocr_runs WHERE evidence_id = ? ORDER BY id DESC LIMIT 1",
            (evidence_id,),
        ).fetchone()
        return OcrRun.from_row(row) if row else None

    def items_for_run(self, ocr_run_id: int) -> list[OcrItem]:
        rows = self.db.con.execute(
            "SELECT * FROM ocr_items WHERE ocr_run_id = ? ORDER BY id", (ocr_run_id,)
        )
        return [OcrItem.from_row(r) for r in rows]

    def latest_ocr(self, evidence_id: str) -> tuple[OcrRun | None, list[OcrItem]]:
        run = self.latest_run(evidence_id)
        if run is None:
            return None, []
        return run, self.items_for_run(run.id)

    def completed_evidence_ids(self, case_id: str) -> set[str]:
        rows = self.db.con.execute(
            "SELECT DISTINCT r.evidence_id FROM ocr_runs r "
            "JOIN evidence e ON e.id = r.evidence_id "
            "WHERE e.case_id = ? AND r.status IN ('ok', 'warning')",
            (case_id,),
        )
        return {r[0] for r in rows}

    def run_ocr(
        self, evidence_id: str, *, language: str = "eng", max_pixels: int = 50_000_000
    ) -> OcrRunResult:
        """Run user-triggered offline OCR on a safe processing copy of *evidence_id*."""
        lang = validate_language(language)
        item = self.evidence.get_required(evidence_id)
        orig_path = self.evidence.original_path(item)
        if not orig_path.is_file():
            raise OcrError("Stored original evidence file is missing from disk.")

        if self._custom_runner is None:
            exe = self.resolve_tesseract()
            if exe is None:
                custom = self.tesseract_path_getter()
                if custom:
                    raise TesseractNotFoundError(
                        f"Configured Tesseract executable not found: {custom}. "
                        "Check Settings -> External tools."
                    )
                raise TesseractNotFoundError(
                    "Tesseract OCR is not installed or not configured. "
                    "See Setup guide or Settings -> External tools."
                )

        # Render safe Pillow copy on D-drive temp (rejects malformed/corrupt/bomb before Tesseract)
        temp_copy = _render_safe_ocr_input(
            orig_path, self.evidence.data_root / "temp", item.id, max_pixels
        )
        try:
            if self._custom_runner is not None:
                engine, engine_ver, tsv_out = self._custom_runner(
                    temp_copy, lang, self.tessdata_dir_getter()
                )
            else:
                exe = self.resolve_tesseract()
                if exe is None:
                    raise TesseractNotFoundError("Tesseract executable not found.")
                engine, engine_ver, tsv_out = _run_tesseract_cli(
                    exe, temp_copy, lang, self.tessdata_dir_getter()
                )
        finally:
            temp_copy.unlink(missing_ok=True)

        # Confirm original was never touched
        if sha256_file(orig_path) != item.sha256:
            raise OcrError("Original evidence file SHA-256 changed unexpectedly!")

        full_text, parsed_items, warnings_list = parse_tesseract_tsv(tsv_out)
        run_at = utc_now_iso()
        status = "warning" if warnings_list else "ok"

        with self.db.transaction() as con:
            cur = con.execute(
                "INSERT INTO ocr_runs ("
                "evidence_id, engine, engine_version, language, run_at, status, "
                "full_text, warnings_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.id,
                    engine,
                    engine_ver,
                    lang,
                    run_at,
                    status,
                    full_text,
                    json.dumps(warnings_list, ensure_ascii=False),
                ),
            )
            run_id = int(cur.lastrowid)
            con.executemany(
                "INSERT INTO ocr_items (ocr_run_id, text, confidence, bounding_box_json) "
                "VALUES (?, ?, ?, ?)",
                [
                    (
                        run_id,
                        it.text,
                        it.confidence,
                        json.dumps(it.bounding_box, sort_keys=True),
                    )
                    for it in parsed_items
                ],
            )
            confs = [it.confidence for it in parsed_items if it.confidence is not None]
            mean_conf = round(sum(confs) / len(confs), 1) if confs else None
            # Privacy: NEVER log raw OCR text in audit_events or application logs!
            append_event(
                con,
                "ocr_completed",
                case_id=item.case_id,
                evidence_id=item.id,
                actor_label=self.actor(),
                details={
                    "ocr_run_id": run_id,
                    "engine": engine,
                    "engine_version": engine_ver,
                    "language": lang,
                    "item_count": len(parsed_items),
                    "mean_confidence": mean_conf,
                    "status": status,
                },
            )

        run, saved_items = self.latest_ocr(item.id)
        if run is None:
            raise OcrError("OCR run was not found after saving.")
        # Privacy: log IDs and counts only, never OCR text!
        log.info(
            "OCR completed for %s via %s %s (lang=%s, items=%d, status=%s)",
            item.id,
            engine,
            engine_ver,
            lang,
            len(saved_items),
            status,
        )
        return OcrRunResult(run, saved_items, compute_mean_confidence(saved_items))

    def export_ocr(
        self, evidence_id: str, dest_path: Path, *, fmt: str = "json"
    ) -> OcrExportResult:
        """Export OCR results as structured JSON or plain text with provenance and caveats."""
        if fmt not in ("json", "txt"):
            raise ValidationError(f"Unsupported OCR export format: {fmt!r}")
        item = self.evidence.get_required(evidence_id)
        run, items = self.latest_ocr(evidence_id)
        if run is None:
            raise OcrError("No OCR run exists for this evidence item yet.")

        mean_conf = compute_mean_confidence(items)
        exported_at = utc_now_iso()
        if fmt == "json":
            payload = {
                "schema": "imagetrace.ocr_export.v1",
                "classification": "Automated result (machine-generated; requires human review)",
                "disclaimer": OCR_DISCLAIMER,
                "case_id": item.case_id,
                "evidence_id": item.id,
                "original_filename": item.original_filename,
                "evidence_sha256": item.sha256,
                "ocr_run_id": run.id,
                "engine": run.engine,
                "engine_version": run.engine_version,
                "language": run.language,
                "run_at": run.run_at,
                "exported_at": exported_at,
                "app_version": f"{APP_NAME} {__version__}",
                "mean_confidence": mean_conf,
                "warnings": list(run.warnings),
                "full_text_untrusted": run.full_text,
                "items": [
                    {
                        "text": it.text,
                        "confidence": it.confidence,
                        "bounding_box": it.bounding_box,
                    }
                    for it in items
                ],
            }
            content = json.dumps(payload, indent=2, ensure_ascii=False)
        else:
            header = [
                f"# {APP_NAME} {__version__} - OCR Text Export (AUTOMATED RESULT)",
                f"# Disclaimer: {OCR_DISCLAIMER}",
                f"# Case ID: {item.case_id}",
                f"# Evidence ID: {item.id}",
                f"# Original filename: {item.original_filename}",
                f"# Evidence SHA-256: {item.sha256}",
                f"# OCR Engine: {run.engine} {run.engine_version} (language: {run.language})",
                f"# Run at: {run.run_at}",
                f"# Exported at: {exported_at}",
                f"# Items: {len(items)}"
                + (f" (mean confidence: {mean_conf:.1f}%)" if mean_conf is not None else ""),
                "-" * 72,
                "",
                run.full_text,
                "",
            ]
            content = "\n".join(header)

        digest = _write_text_atomic(dest_path, content)
        with self.db.transaction() as con:
            append_event(
                con,
                "ocr_exported",
                case_id=item.case_id,
                evidence_id=item.id,
                actor_label=self.actor(),
                details={
                    "ocr_run_id": run.id,
                    "format": fmt,
                    "export_filename": dest_path.name,
                    "sha256": digest,
                    "item_count": len(items),
                },
            )
        return OcrExportResult(dest_path, digest, fmt, len(items))
