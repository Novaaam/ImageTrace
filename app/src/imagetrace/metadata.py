"""Metadata extraction via ExifTool, normalization, discrepancy checks and structured export.

Rules (blueprint sections 4, 5A, 7, 9):
- Runs ExifTool directly via subprocess with argument lists (never ``shell=True``) and explicit
  timeouts (ADR-004).
- Never modifies original evidence files.
- Distinguishes embedded metadata from file/container fields and application/tool/system fields.
- Preserves sanitized raw + formatted ExifTool dumps in ``cases/<case-id>/metadata/`` AND
  structured rows in ``metadata_records`` linked to ``analysis_runs``.
- Never assumes metadata is truthful; reports timezone ambiguity, missing timezones, and
  contradictory date fields as factual discrepancies, not conclusions.
- Treats all extracted metadata values as untrusted data.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from imagetrace import APP_NAME, __version__
from imagetrace.audit import append_event
from imagetrace.cases import case_dir
from imagetrace.db import Database
from imagetrace.evidence import Evidence, EvidenceRepository
from imagetrace.hashing import sha256_file
from imagetrace.timefmt import utc_now_iso
from imagetrace.tools_status import find_exiftool

log = logging.getLogger(__name__)

MODULE_NAME = "metadata_exiftool"
EXIFTOOL_TIMEOUT_SECONDS = 30
VERSION_TIMEOUT_SECONDS = 10
_VERSION_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?$")
_DATE_RE = re.compile(
    r"^(?P<y>\d{4})[:\-](?P<m>\d{2})[:\-](?P<d>\d{2})"
    r"(?:[T ](?P<H>\d{2}):(?P<M>\d{2})(?::(?P<S>\d{2})(?:\.(?P<f>\d+))?)?)?"
    r"(?P<tz>Z|[+-]\d{2}:?\d{2})?$"
)
_OFFSET_RE = re.compile(r"^(?P<sign>[+-])(?P<h>\d{2}):?(?P<m>\d{2})$")
_DATE_ONLY_RE = re.compile(r"^(?P<y>\d{4})[:\-](?P<m>\d{2})[:\-](?P<d>\d{2})$")
_TIME_ONLY_RE = re.compile(
    r"^(?P<H>\d{2}):(?P<M>\d{2})(?::(?P<S>\d{2}))?(?P<tz>Z|[+-]\d{2}:?\d{2})?$"
)
_CONTROL_STRIP = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# System tags that expose local host directory paths or irrelevant OS copy attributes.
OMITTED_TAGS = frozenset(
    {
        "SourceFile",
        "File:System:Directory",
        "File:System:FilePermissions",
        "File:System:FileAccessDate",
    }
)

ORIGIN_LABELS = {
    "embedded": "Embedded",
    "file_container": "File / container",
    "application_system": "Tool / system",
}

METADATA_DISCLAIMER = (
    "Metadata can be edited, forged or stripped. Extracted fields show only what is recorded in "
    "the examined file, not proof of capture time, camera, origin or identity."
)

# Embedded timestamp fields inspected for timezone ambiguity and contradictions.
TIMESTAMP_FIELDS: tuple[tuple[str, str | None], ...] = (
    ("EXIF:ExifIFD:DateTimeOriginal", "EXIF:ExifIFD:OffsetTimeOriginal"),
    ("EXIF:ExifIFD:CreateDate", "EXIF:ExifIFD:OffsetTimeDigitized"),
    ("EXIF:IFD0:ModifyDate", "EXIF:ExifIFD:OffsetTime"),
    ("XMP:XMP-xmp:CreateDate", None),
    ("XMP:XMP-xmp:ModifyDate", None),
    ("XMP:XMP-photoshop:DateCreated", None),
)

_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class MetadataError(Exception):
    """Metadata extraction or export failed (safe to show to the user)."""


class ExifToolNotFoundError(MetadataError):
    pass


@dataclass(frozen=True)
class MetadataRecord:
    id: int | None
    analysis_run_id: int | None
    evidence_id: str
    group_name: str
    category: str
    origin: str
    field_name: str
    raw_value: str
    normalized_value: str
    source_tool: str
    source_version: str
    extracted_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> MetadataRecord:
        return cls(
            id=int(row["id"]),
            analysis_run_id=int(row["analysis_run_id"]),
            evidence_id=row["evidence_id"],
            group_name=row["group_name"],
            category=row["category"],
            origin=row["origin"],
            field_name=row["field_name"],
            raw_value=row["raw_value"],
            normalized_value=row["normalized_value"],
            source_tool=row["source_tool"],
            source_version=row["source_version"],
            extracted_at=row["extracted_at"],
        )


@dataclass(frozen=True)
class AnalysisRun:
    id: int
    evidence_id: str
    module: str
    version: str
    started_at: str
    finished_at: str
    status: str
    output: dict[str, Any]
    warnings: tuple[str, ...]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> AnalysisRun:
        try:
            out = json.loads(row["output_json"])
        except ValueError:
            out = {}
        try:
            warns = json.loads(row["warnings_json"])
        except ValueError:
            warns = []
        return cls(
            id=int(row["id"]),
            evidence_id=row["evidence_id"],
            module=row["module"],
            version=row["version"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            status=row["status"],
            output=out if isinstance(out, dict) else {},
            warnings=tuple(str(w) for w in warns) if isinstance(warns, list) else (),
        )


@dataclass(frozen=True)
class MetadataExtractionResult:
    run: AnalysisRun
    records: list[MetadataRecord]
    sidecar_path: Path | None
    sidecar_sha256: str | None


@dataclass(frozen=True)
class MetadataExportResult:
    path: Path
    sha256: str
    record_count: int


# --- ExifTool subprocess helpers ----------------------------------------------------------
def query_exiftool_version(exe_path: Path) -> str:
    """Return ExifTool's version string (e.g. '13.64') using argument-list subprocess."""
    if not exe_path.is_file():
        raise ExifToolNotFoundError(f"ExifTool executable not found: {exe_path}")
    try:
        proc = subprocess.run(  # noqa: S603 (argument list only, no shell, validated exe path)
            [str(exe_path), "-ver"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=VERSION_TIMEOUT_SECONDS,
            creationflags=_CREATIONFLAGS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MetadataError("ExifTool timed out while reporting its version.") from exc
    except OSError as exc:
        raise MetadataError(f"Could not launch ExifTool: {exc}") from exc
    ver = proc.stdout.strip()
    if proc.returncode != 0 or not _VERSION_RE.fullmatch(ver):
        err = (proc.stderr or proc.stdout).strip()[:200]
        raise MetadataError(f"Unexpected output from ExifTool -ver: {err or '(empty)'}")
    return ver


def _run_exiftool_json(exe_path: Path, image_path: Path, *, numeric: bool) -> dict[str, Any]:
    cmd = [str(exe_path), "-j", "-G0:1", "-a", "-s", "-charset", "filename=utf8"]
    if numeric:
        cmd.append("-n")
    cmd += ["--", str(image_path)]
    try:
        proc = subprocess.run(  # noqa: S603 (argument list only, no shell, '--' before path)
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=EXIFTOOL_TIMEOUT_SECONDS,
            creationflags=_CREATIONFLAGS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MetadataError(
            f"ExifTool timed out after {EXIFTOOL_TIMEOUT_SECONDS} seconds."
        ) from exc
    except OSError as exc:
        raise MetadataError(f"Could not run ExifTool: {exc}") from exc

    stdout = proc.stdout.strip()
    if not stdout:
        err = proc.stderr.strip()[:300]
        raise MetadataError(f"ExifTool produced no JSON output (exit {proc.returncode}): {err}")
    try:
        parsed = json.loads(stdout)
    except ValueError as exc:
        raise MetadataError(f"Could not parse ExifTool JSON output: {exc}") from exc
    if not isinstance(parsed, list) or not parsed or not isinstance(parsed[0], dict):
        raise MetadataError("ExifTool JSON output had an unexpected structure.")
    return parsed[0]


# --- classification & normalization -------------------------------------------------------
_CONTAINER_GROUPS = frozenset({"JFIF", "GIF", "BMP", "RIFF"})
_PNG_CONTAINER_FIELDS = frozenset(
    {
        "ImageWidth",
        "ImageHeight",
        "BitDepth",
        "ColorType",
        "Compression",
        "Filter",
        "Interlace",
        "SRGBRendering",
        "Gamma",
        "PixelsPerUnitX",
        "PixelsPerUnitY",
        "PixelUnits",
        "Palette",
        "BackgroundColor",
        "Transparency",
    }
)


def classify_key(full_key: str) -> tuple[str, str, str, str]:
    """Split an ExifTool `-G0:1` key into (group_name, category, origin, field_name)."""
    parts = full_key.split(":")
    if len(parts) >= 3:
        g0, g1, field = parts[0], parts[1], ":".join(parts[2:])
        group_name = f"{g0}:{g1}"
    elif len(parts) == 2:
        g0, g1, field = parts[0], parts[0], parts[1]
        group_name = g0
    else:
        return "Other", "Other", "application_system", full_key

    if g0 in ("ExifTool", "Composite"):
        return (
            group_name,
            ("Tool" if g0 == "ExifTool" else "Composite"),
            "application_system",
            field,
        )
    if g0 == "File":
        if g1 == "System":
            return group_name, "File/System", "application_system", field
        return group_name, "File/Container", "file_container", field
    if g0 in _CONTAINER_GROUPS or (g0 == "PNG" and field in _PNG_CONTAINER_FIELDS):
        return group_name, "File/Container", "file_container", field
    if g0 == "EXIF":
        cat = "GPS" if g1 == "GPS" else "EXIF"
        return group_name, cat, "embedded", field
    if g0 in ("IPTC", "XMP", "ICC_Profile", "MakerNotes"):
        return group_name, g0, "embedded", field
    # Embedded metadata segments (Photoshop, Adobe, MPF, PNG text chunks, etc.)
    return group_name, g0, "embedded", field


def stringify_value(value: Any) -> str:
    """Convert any untrusted JSON metadata value into a clean display string."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        text = ", ".join(stringify_value(v) for v in value)
    elif isinstance(value, dict):
        text = json.dumps(value, sort_keys=True, ensure_ascii=False)
    else:
        text = str(value)
    return _CONTROL_STRIP.sub("", text).strip()


@dataclass(frozen=True)
class ParsedTimestamp:
    field_key: str
    raw: str
    normalized: str
    dt: datetime | None
    has_timezone: bool
    date_only: bool = False
    problem: str | None = None


def _parse_tz(tz_str: str | None) -> timezone | None:
    if not tz_str:
        return None
    s = tz_str.strip()
    if s.upper() == "Z":
        return UTC
    m = _OFFSET_RE.fullmatch(s)
    if not m:
        return None
    hours, mins = int(m.group("h")), int(m.group("m"))
    if hours > 23 or mins > 59:
        return None
    delta = timedelta(hours=hours, minutes=mins)
    if m.group("sign") == "-":
        delta = -delta
    return timezone(delta)


def parse_timestamp_value(
    field_key: str, raw_value: str, offset_value: str | None = None
) -> ParsedTimestamp:
    """Normalize an EXIF/XMP/IPTC timestamp and detect invalid/placeholder/tz-missing states."""
    raw = raw_value.strip()
    if not raw:
        return ParsedTimestamp(field_key, raw, "", None, False)
    if raw.startswith("0000:00:00") or raw.startswith("0000-00-00"):
        return ParsedTimestamp(
            field_key,
            raw,
            f"{raw} (placeholder/zero date)",
            None,
            False,
            problem=f"{field_key} contains a zero/placeholder timestamp ('{raw}').",
        )
    m = _DATE_RE.fullmatch(raw)
    if not m:
        return ParsedTimestamp(
            field_key,
            raw,
            f"{raw} (unparseable date)",
            None,
            False,
            problem=f"{field_key} contains an invalid or unparseable date format ('{raw}').",
        )
    y, mo, d = int(m.group("y")), int(m.group("m")), int(m.group("d"))
    has_time = m.group("H") is not None
    hh = int(m.group("H")) if has_time else 0
    mm = int(m.group("M")) if has_time else 0
    ss = int(m.group("S")) if (has_time and m.group("S") is not None) else 0
    tz_raw = m.group("tz") or (offset_value.strip() if offset_value else None)
    tz_obj = _parse_tz(tz_raw)
    if tz_raw and tz_obj is None:
        return ParsedTimestamp(
            field_key,
            raw,
            f"{raw} (invalid timezone '{tz_raw}')",
            None,
            False,
            problem=f"{field_key} has an invalid timezone offset ('{tz_raw}').",
        )
    try:
        dt = datetime(y, mo, d, hh, mm, ss, tzinfo=tz_obj)
    except ValueError:
        return ParsedTimestamp(
            field_key,
            raw,
            f"{raw} (invalid calendar date/time)",
            None,
            False,
            problem=f"{field_key} has an impossible calendar date or time ('{raw}').",
        )

    if not has_time:
        return ParsedTimestamp(
            field_key,
            raw,
            f"{y:04d}-{mo:02d}-{d:02d} (date only; timezone unknown)",
            dt,
            False,
            date_only=True,
        )
    iso = dt.isoformat(timespec="seconds").replace("+00:00", "Z")
    if tz_obj is None:
        return ParsedTimestamp(
            field_key,
            raw,
            f"{iso} (timezone unknown)",
            dt,
            False,
        )
    return ParsedTimestamp(field_key, raw, iso, dt, True)


def analyze_timestamps(
    raw_map: dict[str, Any], now_utc: datetime | None = None
) -> tuple[dict[str, str], list[str]]:
    """Inspect embedded date fields; return (normalized_overrides_by_key, warning_messages)."""
    now = now_utc or datetime.now(UTC)
    overrides: dict[str, str] = {}
    warnings_list: list[str] = []
    parsed: dict[str, ParsedTimestamp] = {}

    for key, offset_key in TIMESTAMP_FIELDS:
        if key not in raw_map:
            continue
        raw_s = stringify_value(raw_map[key])
        off_s = (
            stringify_value(raw_map[offset_key]) if (offset_key and offset_key in raw_map) else None
        )
        ts = parse_timestamp_value(key, raw_s, off_s)
        parsed[key] = ts
        if ts.normalized:
            overrides[key] = ts.normalized
        if ts.problem:
            warnings_list.append(ts.problem)

    # IPTC DateCreated (+ optional TimeCreated)
    if "IPTC:DateCreated" in raw_map:
        d_raw = stringify_value(raw_map["IPTC:DateCreated"])
        t_raw = stringify_value(raw_map.get("IPTC:TimeCreated", ""))
        combined = (
            f"{d_raw} {t_raw}".strip() if (t_raw and _TIME_ONLY_RE.fullmatch(t_raw)) else d_raw
        )
        ts = parse_timestamp_value("IPTC:DateCreated", combined)
        parsed["IPTC:DateCreated"] = ts
        if ts.normalized:
            overrides["IPTC:DateCreated"] = ts.normalized
        if ts.problem:
            warnings_list.append(ts.problem)

    # GPS UTC timestamp (date + time) for cross-check only (full GPS handling arrives in Phase 5)
    if "EXIF:GPS:GPSDateStamp" in raw_map and "EXIF:GPS:GPSTimeStamp" in raw_map:
        gd = stringify_value(raw_map["EXIF:GPS:GPSDateStamp"])
        gt = stringify_value(raw_map["EXIF:GPS:GPSTimeStamp"])
        if _DATE_ONLY_RE.fullmatch(gd) and _TIME_ONLY_RE.fullmatch(gt):
            gt_z = gt if gt.endswith("Z") or "+" in gt or "-" in gt else f"{gt}Z"
            gps_ts = parse_timestamp_value("EXIF:GPS:GPSDateTime", f"{gd} {gt_z}")
            if gps_ts.dt is not None:
                parsed["EXIF:GPS:GPSDateTime"] = gps_ts

    # 1. Missing timezone warnings (explicitly note timezone ambiguity)
    missing_tz = [k for k, ts in parsed.items() if ts.dt is not None and not ts.has_timezone]
    has_tz = [k for k, ts in parsed.items() if ts.dt is not None and ts.has_timezone]
    if missing_tz:
        names = ", ".join(k.split(":")[-1] for k in missing_tz)
        warnings_list.append(
            f"Timezone information is missing for embedded date field(s): {names}. "
            "Without a UTC offset, local capture time cannot be converted to UTC reliably."
        )
    if missing_tz and has_tz:
        warnings_list.append(
            "Timezone ambiguity: some embedded timestamps include a UTC offset while others do "
            "not; comparisons across mixed aware/naive timestamps are approximate."
        )

    # 2. Future timestamp check (> 24 hours ahead of workstation UTC clock)
    for key, ts in parsed.items():
        if ts.dt is None or ts.date_only:
            continue
        dt_cmp = ts.dt.astimezone(UTC) if ts.has_timezone else ts.dt.replace(tzinfo=UTC)
        if dt_cmp > now + timedelta(hours=24):
            warnings_list.append(
                f"Timestamp anomaly: {key} ({ts.normalized}) is more than 24 hours in the future "
                "relative to the workstation clock at extraction time (camera clock may be wrong "
                "or value may have been altered)."
            )

    # 3. Contradictory date checks (reported strictly as discrepancies, not conclusions)
    def _compare(k1: str, k2: str) -> timedelta | None:
        t1, t2 = parsed.get(k1), parsed.get(k2)
        if not t1 or not t2 or t1.dt is None or t2.dt is None:
            return None
        if t1.date_only or t2.date_only:
            return None
        if t1.has_timezone and t2.has_timezone:
            return t1.dt.astimezone(UTC) - t2.dt.astimezone(UTC)
        if not t1.has_timezone and not t2.has_timezone:
            return t1.dt - t2.dt
        return None  # mixed aware/naive: don't fabricate a precise delta

    # ModifyDate earlier than DateTimeOriginal
    delta_mod = _compare("EXIF:IFD0:ModifyDate", "EXIF:ExifIFD:DateTimeOriginal")
    if delta_mod is not None and delta_mod < timedelta(seconds=-60):
        warnings_list.append(
            f"Timestamp discrepancy: EXIF:IFD0:ModifyDate "
            f"({parsed['EXIF:IFD0:ModifyDate'].normalized}) is earlier than "
            f"EXIF:ExifIFD:DateTimeOriginal "
            f"({parsed['EXIF:ExifIFD:DateTimeOriginal'].normalized}). "
            "Reported as a factual discrepancy for review, not a conclusion."
        )

    # Creation-date pairs disagreeing by > 60 seconds
    creation_keys = [
        "EXIF:ExifIFD:DateTimeOriginal",
        "EXIF:ExifIFD:CreateDate",
        "XMP:XMP-xmp:CreateDate",
        "XMP:XMP-photoshop:DateCreated",
    ]
    present_creation = [k for k in creation_keys if k in parsed and parsed[k].dt is not None]
    for i, k1 in enumerate(present_creation):
        for k2 in present_creation[i + 1 :]:
            diff = _compare(k1, k2)
            if diff is not None and abs(diff) > timedelta(seconds=60):
                warnings_list.append(
                    f"Timestamp discrepancy: {k1} ({parsed[k1].normalized}) differs from "
                    f"{k2} ({parsed[k2].normalized}) by {_format_delta(abs(diff))}. "
                    "Reported as a discrepancy for analyst review, not a conclusion."
                )

    # Calendar-date mismatch for date-only IPTC:DateCreated vs DateTimeOriginal
    iptc_ts = parsed.get("IPTC:DateCreated")
    dto_ts = parsed.get("EXIF:ExifIFD:DateTimeOriginal")
    if iptc_ts and dto_ts and iptc_ts.dt and dto_ts.dt:
        if iptc_ts.date_only:
            if iptc_ts.dt.date() != dto_ts.dt.date():
                warnings_list.append(
                    f"Timestamp discrepancy: IPTC:DateCreated ({iptc_ts.normalized}) has a "
                    f"different calendar date from EXIF:ExifIFD:DateTimeOriginal "
                    f"({dto_ts.normalized}). Reported as a discrepancy, not a conclusion."
                )
        else:
            diff = _compare("IPTC:DateCreated", "EXIF:ExifIFD:DateTimeOriginal")
            if diff is not None and abs(diff) > timedelta(seconds=60):
                warnings_list.append(
                    f"Timestamp discrepancy: IPTC:DateCreated ({iptc_ts.normalized}) differs "
                    f"from EXIF:ExifIFD:DateTimeOriginal ({dto_ts.normalized}) by "
                    f"{_format_delta(abs(diff))}. Reported as a discrepancy, not a conclusion."
                )

    return overrides, warnings_list


def _format_delta(delta: timedelta) -> str:
    secs = int(delta.total_seconds())
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins, s = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins:
        parts.append(f"{mins}m")
    if s or not parts:
        parts.append(f"{s}s")
    return " ".join(parts)


def _sanitize_dump(dump: dict[str, Any], stored_filename: str) -> dict[str, Any]:
    """Strip host directory paths while preserving every extracted tag faithfully."""
    out: dict[str, Any] = {}
    for k, v in dump.items():
        if k == "SourceFile":
            out[k] = stored_filename
        elif k in OMITTED_TAGS:
            continue
        else:
            out[k] = v
    return out


def _build_summary(records: list[MetadataRecord]) -> dict[str, str]:
    """Extract a concise factual camera/date summary from embedded records only."""
    by_field: dict[str, str] = {}
    for r in records:
        if r.origin == "embedded":
            by_field[f"{r.group_name}:{r.field_name}"] = r.normalized_value

    def _first(*keys: str) -> str:
        for k in keys:
            if by_field.get(k):
                return by_field[k]
        return ""

    return {
        "make": _first("EXIF:IFD0:Make", "XMP:XMP-tiff:Make"),
        "model": _first("EXIF:IFD0:Model", "XMP:XMP-tiff:Model"),
        "lens": _first("EXIF:ExifIFD:LensModel", "EXIF:ExifIFD:LensInfo", "XMP:XMP-aux:Lens"),
        "orientation": _first("EXIF:IFD0:Orientation"),
        "software": _first("EXIF:IFD0:Software", "XMP:XMP-xmp:CreatorTool", "PNG:Software"),
        "date_original": _first("EXIF:ExifIFD:DateTimeOriginal"),
        "date_create": _first(
            "EXIF:ExifIFD:CreateDate", "XMP:XMP-xmp:CreateDate", "IPTC:DateCreated"
        ),
        "date_modify": _first("EXIF:IFD0:ModifyDate", "XMP:XMP-xmp:ModifyDate"),
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    data = json.dumps(payload, indent=2, ensure_ascii=False)
    try:
        partial.write_text(data, encoding="utf-8")
        os.replace(partial, path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return sha256_file(path)


# --- repository / service -----------------------------------------------------------------
class MetadataService:
    def __init__(
        self,
        db: Database,
        evidence_repo: EvidenceRepository,
        actor: Callable[[], str] = lambda: "Analyst",
        exiftool_path_getter: Callable[[], str] = lambda: "",
    ) -> None:
        self.db = db
        self.evidence = evidence_repo
        self.actor = actor
        self.exiftool_path_getter = exiftool_path_getter

    def resolve_exiftool(self) -> Path:
        custom = self.exiftool_path_getter()
        exe = find_exiftool(custom)
        if exe is None:
            if custom:
                raise ExifToolNotFoundError(
                    f"Configured ExifTool executable not found: {custom}. "
                    "Check Settings -> External tools."
                )
            raise ExifToolNotFoundError(
                "ExifTool executable not found. Place it in tools\\third-party\\exiftool\\ "
                "or set its path in Settings -> External tools."
            )
        return exe

    def metadata_dir(self, case_id: str) -> Path:
        return case_dir(self.evidence.cases_root, case_id) / "metadata"

    def reports_dir(self, case_id: str) -> Path:
        return case_dir(self.evidence.cases_root, case_id) / "reports"

    def latest_run(self, evidence_id: str) -> AnalysisRun | None:
        row = self.db.con.execute(
            "SELECT * FROM analysis_runs WHERE evidence_id = ? AND module = ? "
            "ORDER BY id DESC LIMIT 1",
            (evidence_id, MODULE_NAME),
        ).fetchone()
        return AnalysisRun.from_row(row) if row else None

    def records_for_run(self, run_id: int) -> list[MetadataRecord]:
        rows = self.db.con.execute(
            "SELECT * FROM metadata_records WHERE analysis_run_id = ? "
            "ORDER BY CASE origin WHEN 'embedded' THEN 0 WHEN 'file_container' THEN 1 ELSE 2 END, "
            "category, group_name, field_name, id",
            (run_id,),
        )
        return [MetadataRecord.from_row(r) for r in rows]

    def latest_metadata(self, evidence_id: str) -> tuple[AnalysisRun | None, list[MetadataRecord]]:
        run = self.latest_run(evidence_id)
        if run is None:
            return None, []
        return run, self.records_for_run(run.id)

    def extracted_evidence_ids(self, case_id: str) -> set[str]:
        rows = self.db.con.execute(
            "SELECT DISTINCT ar.evidence_id FROM analysis_runs ar "
            "JOIN evidence e ON e.id = ar.evidence_id "
            "WHERE e.case_id = ? AND ar.module = ? AND ar.status IN ('ok', 'warning')",
            (case_id, MODULE_NAME),
        )
        return {r[0] for r in rows}

    def extract(self, evidence_id: str) -> MetadataExtractionResult:
        """Run ExifTool on the stored original (read-only), store sidecar + DB records."""
        item: Evidence = self.evidence.get_required(evidence_id)
        orig_path = self.evidence.original_path(item)
        if not orig_path.is_file():
            raise MetadataError("Stored original evidence file is missing from disk.")

        exe = self.resolve_exiftool()
        et_version = query_exiftool_version(exe)
        tool_version_label = f"ExifTool {et_version}; {APP_NAME} {__version__}"
        started_at = utc_now_iso()

        formatted_dump = _run_exiftool_json(exe, orig_path, numeric=False)
        raw_dump = _run_exiftool_json(exe, orig_path, numeric=True)
        extracted_at = utc_now_iso()

        # Verify original file was never altered by ExifTool
        if sha256_file(orig_path) != item.sha256:
            raise MetadataError("Original evidence file SHA-256 changed unexpectedly!")

        clean_formatted = _sanitize_dump(formatted_dump, item.stored_filename)
        clean_raw = _sanitize_dump(raw_dump, item.stored_filename)

        ts_overrides, warnings_list = analyze_timestamps(clean_raw)

        # Surface ExifTool's own warnings / errors
        for key in ("ExifTool:Warning", "ExifTool:Error"):
            if key in clean_formatted:
                val = stringify_value(clean_formatted[key])
                if val:
                    warnings_list.append(f"ExifTool reported ({key}): {val}")

        # Build structured records from union of keys (preserving order from formatted dump)
        all_keys: list[str] = []
        seen: set[str] = set()
        for src_dict in (clean_formatted, clean_raw):
            for k in src_dict:
                if k == "SourceFile" or k in OMITTED_TAGS or k in seen:
                    continue
                seen.add(k)
                all_keys.append(k)

        staged: list[MetadataRecord] = []
        for full_key in all_keys:
            group_name, category, origin, field_name = classify_key(full_key)
            raw_s = stringify_value(clean_raw.get(full_key, clean_formatted.get(full_key)))
            norm_s = ts_overrides.get(
                full_key, stringify_value(clean_formatted.get(full_key, raw_s))
            )
            staged.append(
                MetadataRecord(
                    id=None,
                    analysis_run_id=None,
                    evidence_id=item.id,
                    group_name=group_name,
                    category=category,
                    origin=origin,
                    field_name=field_name,
                    raw_value=raw_s,
                    normalized_value=norm_s,
                    source_tool="ExifTool",
                    source_version=et_version,
                    extracted_at=extracted_at,
                )
            )

        embedded_count = sum(1 for r in staged if r.origin == "embedded")
        descriptive_count = sum(
            1
            for r in staged
            if r.origin == "embedded" and r.category in ("EXIF", "GPS", "IPTC", "XMP", "MakerNotes")
        )
        if descriptive_count == 0:
            warnings_list.append(
                "No embedded EXIF, IPTC or XMP metadata was found in this file. "
                "Metadata may never have been written or may have been stripped."
            )

        # Preserve original metadata dump in structured sidecar file
        sidecar_filename = f"{item.id}-exiftool.json"
        sidecar_path = self.metadata_dir(item.case_id) / sidecar_filename
        sidecar_payload = {
            "schema": "imagetrace.metadata_sidecar.v1",
            "disclaimer": METADATA_DISCLAIMER,
            "evidence_id": item.id,
            "case_id": item.case_id,
            "stored_filename": item.stored_filename,
            "evidence_sha256": item.sha256,
            "source_tool": "ExifTool",
            "source_version": et_version,
            "app_version": f"{APP_NAME} {__version__}",
            "extracted_at": extracted_at,
            "warnings": warnings_list,
            "exiftool_formatted": clean_formatted,
            "exiftool_raw_numeric": clean_raw,
        }
        sidecar_sha = _write_json_atomic(sidecar_path, sidecar_payload)

        summary = _build_summary(staged)
        status = "warning" if warnings_list else "ok"
        output_meta = {
            "source_tool": "ExifTool",
            "source_version": et_version,
            "sidecar_filename": sidecar_filename,
            "sidecar_sha256": sidecar_sha,
            "total_records": len(staged),
            "embedded_records": embedded_count,
            "summary": summary,
        }
        finished_at = utc_now_iso()

        with self.db.transaction() as con:
            cur = con.execute(
                "INSERT INTO analysis_runs ("
                "evidence_id, module, version, started_at, finished_at, status, "
                "output_json, warnings_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.id,
                    MODULE_NAME,
                    tool_version_label,
                    started_at,
                    finished_at,
                    status,
                    json.dumps(output_meta, sort_keys=True, ensure_ascii=False),
                    json.dumps(warnings_list, ensure_ascii=False),
                ),
            )
            run_id = int(cur.lastrowid)
            con.executemany(
                "INSERT INTO metadata_records ("
                "analysis_run_id, evidence_id, group_name, category, origin, field_name, "
                "raw_value, normalized_value, source_tool, source_version, extracted_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id,
                        r.evidence_id,
                        r.group_name,
                        r.category,
                        r.origin,
                        r.field_name,
                        r.raw_value,
                        r.normalized_value,
                        r.source_tool,
                        r.source_version,
                        r.extracted_at,
                    )
                    for r in staged
                ],
            )
            append_event(
                con,
                "metadata_extracted",
                case_id=item.case_id,
                evidence_id=item.id,
                actor_label=self.actor(),
                details={
                    "analysis_run_id": run_id,
                    "source_tool": "ExifTool",
                    "source_version": et_version,
                    "embedded_records": embedded_count,
                    "total_records": len(staged),
                    "warnings_count": len(warnings_list),
                    "sidecar_filename": sidecar_filename,
                    "sidecar_sha256": sidecar_sha,
                },
            )

        run = self.latest_run(item.id)
        if run is None:
            raise MetadataError("Metadata run was not found after saving.")
        records = self.records_for_run(run.id)
        log.info(
            "Metadata extracted: %s via ExifTool %s (%d embedded, %d total, %d warnings)",
            item.id,
            et_version,
            embedded_count,
            len(records),
            len(warnings_list),
        )
        return MetadataExtractionResult(run, records, sidecar_path, sidecar_sha)

    def export_json(self, evidence_id: str, dest_path: Path | None = None) -> MetadataExportResult:
        """Export structured metadata JSON with provenance, warnings and origin separation."""
        item = self.evidence.get_required(evidence_id)
        run, records = self.latest_metadata(evidence_id)
        if run is None:
            raise MetadataError("No metadata has been extracted for this evidence item yet.")

        target = dest_path or (self.reports_dir(item.case_id) / f"{item.id}-metadata.json")
        grouped: dict[str, list[dict[str, str]]] = {
            "embedded": [],
            "file_container": [],
            "application_system": [],
        }
        for r in records:
            grouped.setdefault(r.origin, []).append(
                {
                    "group": r.group_name,
                    "category": r.category,
                    "field": r.field_name,
                    "raw_value": r.raw_value,
                    "normalized_value": r.normalized_value,
                }
            )

        payload = {
            "schema": "imagetrace.metadata_export.v1",
            "legend": {
                "embedded": "Extracted fact: directly embedded in the examined image file.",
                "file_container": "Extracted fact: derived deterministically from the file bytes.",
                "application_system": (
                    "Application/tool metadata: generated by ExifTool or the local filesystem "
                    "(not embedded inside the image)."
                ),
            },
            "limitations": [
                METADATA_DISCLAIMER,
                (
                    "Embedded timestamps without a timezone offset cannot be converted "
                    "to UTC reliably."
                ),
            ],
            "case_id": item.case_id,
            "evidence_id": item.id,
            "original_filename": item.original_filename,
            "evidence_sha256": item.sha256,
            "analysis_run_id": run.id,
            "source_tool": run.output.get("source_tool", "ExifTool"),
            "source_version": run.output.get("source_version", ""),
            "tool_version": run.version,
            "extracted_at": run.finished_at,
            "exported_at": utc_now_iso(),
            "app_version": f"{APP_NAME} {__version__}",
            "warnings": list(run.warnings),
            "summary": run.output.get("summary", {}),
            "records_by_origin": grouped,
        }
        digest = _write_json_atomic(target, payload)
        with self.db.transaction() as con:
            append_event(
                con,
                "metadata_exported",
                case_id=item.case_id,
                evidence_id=item.id,
                actor_label=self.actor(),
                details={
                    "analysis_run_id": run.id,
                    "export_filename": target.name,
                    "sha256": digest,
                    "record_count": len(records),
                },
            )
        return MetadataExportResult(target, digest, len(records))
