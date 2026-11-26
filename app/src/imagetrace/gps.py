"""Embedded GPS parsing, validation, storage and strictly opt-in external map tile helpers.

Rules (blueprint sections 1, 4, 5B, 7, 9, 11):
- Parse GPS ONLY from embedded metadata fields (never fabricate or infer coordinates).
- Validate coordinate ranges (-90..90, -180..180) and reject NaN/Inf/malformed values cleanly.
- Preserve raw GPS metadata fields alongside normalized decimal coordinates, source field and
  parser version.
- Never automatically reverse-geocode coordinates to a person's address.
- Never transmit coordinates or contact external map services by default. External tile fetches
  are strictly opt-in, on-demand only, after explicit disclosure, and never cached on disk.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from imagetrace import APP_NAME, __version__
from imagetrace.audit import append_event
from imagetrace.db import Database
from imagetrace.evidence import EvidenceRepository
from imagetrace.metadata import (
    AnalysisRun,
    MetadataRecord,
    MetadataService,
    parse_timestamp_value,
)
from imagetrace.timefmt import utc_now_iso

log = logging.getLogger(__name__)

GPS_MODULE_NAME = "gps_parser"
GPS_PARSER_VERSION = "ImageTrace-GPSParser/1.0"
NO_GPS_MESSAGE = "No GPS coordinates found in the examined file."
GPS_WARNING_BANNER = (
    "GPS coordinates (when present) reflect only what the camera or editing software wrote to "
    "the file at capture time - not the sender's current location or identity. Like all "
    "metadata, GPS tags can be inaccurate, spoofed, edited or stripped."
)
EXTERNAL_MAP_DISCLOSURE = (
    "External map preview is optional and OFF by default.\n\n"
    "If you proceed, ImageTrace will send HTTP requests to OpenStreetMap's public tile server "
    "(tile.openstreetmap.org) containing:\n"
    "  • Your network IP address and User-Agent (ImageTrace)\n"
    "  • Slippy-map tile coordinates (zoom / x / y) covering the general map area\n\n"
    "ImageTrace does NOT send your image, filename, case details, or reverse-geocode an "
    "address. Tiles are kept in memory only for this view and are not written to disk.\n\n"
    "Higher zoom levels cover a smaller geographic area. Continue?"
)
OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
MAP_ZOOM_MIN = 3
MAP_ZOOM_MAX = 15
DEFAULT_MAP_ZOOM = 12
TILE_TIMEOUT_SECONDS = 10

_FLOAT_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_DMS_RE = re.compile(
    r"^(?P<sign>[+-])?\s*(?P<d>\d+(?:\.\d+)?)\s*(?:deg|\u00b0)?\s*"
    r"(?:(?P<m>\d+(?:\.\d+)?)\s*['\u2032]\s*(?:(?P<s>\d+(?:\.\d+)?)\s*[\"\u2033]?)?)?\s*"
    r"(?P<hemi>[NSEW])?$",
    re.IGNORECASE,
)
_XMP_DDM_RE = re.compile(
    r"^(?P<d>\d+),(?P<m>\d+(?:\.\d+)?)(?:,(?P<s>\d+(?:\.\d+)?))?(?P<hemi>[NSEW])$",
    re.IGNORECASE,
)
_ALT_NUM_RE = re.compile(r"^(?P<val>[+-]?\d+(?:\.\d+)?)\s*(?:m|meters?)?$", re.IGNORECASE)

# Supporting embedded GPS tags preserved for examiner visibility (never reverse-geocoded).
SUPPORTING_GPS_FIELDS = (
    "GPSDOP",
    "GPSHPositioningError",
    "GPSImgDirection",
    "GPSImgDirectionRef",
    "GPSSpeed",
    "GPSSpeedRef",
    "GPSMapDatum",
    "GPSProcessingMethod",
    "GPSStatus",
    "GPSMeasureMode",
    "GPSSatellites",
    "GPSVersionID",
)


class GpsError(Exception):
    """GPS parsing or optional map fetch failed (safe to show to the user)."""


@dataclass(frozen=True)
class GpsRecord:
    id: int | None
    analysis_run_id: int | None
    evidence_id: str
    raw_fields: dict[str, dict[str, str]]
    latitude: float | None
    longitude: float | None
    altitude: float | None
    gps_timestamp: str | None
    source_field: str
    parser_version: str
    is_valid: bool
    warnings: tuple[str, ...]
    extracted_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> GpsRecord:
        try:
            raw = json.loads(row["raw_fields_json"])
        except ValueError:
            raw = {}
        try:
            warns = json.loads(row["warnings_json"])
        except ValueError:
            warns = []
        return cls(
            id=int(row["id"]),
            analysis_run_id=int(row["analysis_run_id"]),
            evidence_id=row["evidence_id"],
            raw_fields=raw if isinstance(raw, dict) else {},
            latitude=float(row["latitude"]) if row["latitude"] is not None else None,
            longitude=float(row["longitude"]) if row["longitude"] is not None else None,
            altitude=float(row["altitude"]) if row["altitude"] is not None else None,
            gps_timestamp=row["gps_timestamp"],
            source_field=row["source_field"],
            parser_version=row["parser_version"],
            is_valid=bool(row["is_valid"]),
            warnings=tuple(str(w) for w in warns) if isinstance(warns, list) else (),
            extracted_at=row["extracted_at"],
        )


@dataclass(frozen=True)
class GpsParseOutcome:
    has_gps: bool
    record: GpsRecord | None
    run: AnalysisRun


# --- pure parsing & validation helpers ----------------------------------------------------
def _normalize_hemi(ref: str | None, allowed: tuple[str, str]) -> str | None:
    if not ref:
        return None
    s = ref.strip().upper()
    if not s:
        return None
    first = s[0]
    return first if first in allowed else None


def parse_coordinate(
    raw_value: str,
    ref_value: str | None,
    *,
    axis: str,
) -> tuple[float | None, list[str]]:
    """Parse a single latitude or longitude value and validate its range.

    *axis* must be 'lat' (-90..90, N/S) or 'lon' (-180..180, E/W).
    Returns (decimal_degrees_or_None, warnings).
    """
    warnings_list: list[str] = []
    if axis == "lat":
        limit, pos_h, neg_h, label_name = 90.0, "N", "S", "latitude"
    elif axis == "lon":
        limit, pos_h, neg_h, label_name = 180.0, "E", "W", "longitude"
    else:
        raise ValueError(f"Unknown axis: {axis!r}")

    text = (raw_value or "").strip()
    if not text:
        return None, [f"Embedded GPS {label_name} value is empty."]

    val: float | None = None
    inline_hemi: str | None = None

    # 1. Plain numeric decimal (typical from ExifTool -n)
    if _FLOAT_RE.fullmatch(text):
        val = float(text)
    else:
        # 2. XMP DDM/DMS e.g. '40,42.7666N'
        mx = _XMP_DDM_RE.fullmatch(text)
        if mx:
            deg = float(mx.group("d"))
            mins = float(mx.group("m"))
            secs = float(mx.group("s")) if mx.group("s") else 0.0
            if mins >= 60 or secs >= 60:
                return None, [f"Invalid GPS {label_name} minutes/seconds in '{text}'."]
            val = deg + mins / 60.0 + secs / 3600.0
            inline_hemi = mx.group("hemi").upper()
        else:
            # 3. Formatted DMS e.g. 33 deg 52' 7.75" S
            md = _DMS_RE.fullmatch(text)
            if md:
                deg = float(md.group("d"))
                mins = float(md.group("m")) if md.group("m") else 0.0
                secs = float(md.group("s")) if md.group("s") else 0.0
                if mins >= 60 or secs >= 60:
                    return None, [f"Invalid GPS {label_name} minutes/seconds in '{text}'."]
                val = deg + mins / 60.0 + secs / 3600.0
                if md.group("sign") == "-":
                    val = -val
                inline_hemi = md.group("hemi").upper() if md.group("hemi") else None

    if val is None or not math.isfinite(val):
        return None, [f"Could not parse embedded GPS {label_name}: '{text}'."]

    ref_hemi = _normalize_hemi(ref_value, (pos_h, neg_h))
    if ref_value and ref_value.strip() and ref_hemi is None:
        warnings_list.append(
            f"Unrecognized GPS {label_name} reference '{ref_value.strip()}' "
            f"(expected {pos_h} or {neg_h})."
        )

    hemi = inline_hemi or ref_hemi
    if inline_hemi and ref_hemi and inline_hemi != ref_hemi:
        warnings_list.append(
            f"Conflicting GPS {label_name} hemisphere markers: value has '{inline_hemi}' "
            f"while Ref tag has '{ref_hemi}'."
        )
    if hemi == neg_h:
        val = -abs(val)
    elif hemi == pos_h:
        if val < 0:
            warnings_list.append(
                f"GPS {label_name} raw value ({val}) is negative while Ref tag is '{pos_h}'; "
                "using signed value for review."
            )
    elif val > 0:
        warnings_list.append(
            f"GPS {label_name} hemisphere reference ({pos_h}/{neg_h}) is missing; "
            f"assuming positive ({pos_h}) - verify hemisphere."
        )

    if abs(val) > limit:
        return None, [
            f"GPS {label_name} ({val:.6f}) is outside the valid range "
            f"[-{int(limit)}, +{int(limit)}] (raw: '{text}')."
        ]

    return round(val, 7), warnings_list


def parse_altitude(raw_value: str | None, ref_value: str | None) -> tuple[float | None, list[str]]:
    if not raw_value or not raw_value.strip():
        return None, []
    text = raw_value.strip()
    m = _ALT_NUM_RE.fullmatch(text)
    if not m:
        return None, [f"Could not parse embedded GPS altitude: '{text}'."]
    val = float(m.group("val"))
    if not math.isfinite(val):
        return None, [f"Embedded GPS altitude is non-finite: '{text}'."]

    warnings_list: list[str] = []
    ref = (ref_value or "").strip().lower()
    if ref in ("1", "below sea level", "-1", "negative"):
        val = -abs(val)
    elif ref and ref not in ("0", "above sea level", "positive"):
        warnings_list.append(f"Unrecognized GPSAltitudeRef '{ref_value}'.")

    if val < -500.0 or val > 100_000.0:
        warnings_list.append(
            f"Embedded GPS altitude ({val:.1f} m) is outside typical terrestrial/aviation "
            "ranges (-500 m to 100,000 m); verify raw metadata."
        )
    return round(val, 2), warnings_list


def parse_gps_timestamp(
    date_stamp: str | None, time_stamp: str | None, combined_xmp: str | None = None
) -> tuple[str | None, list[str]]:
    """Normalize GPS UTC date/time into ISO-8601 Z string."""
    if date_stamp and time_stamp:
        d = date_stamp.strip()
        t = time_stamp.strip()
        t_z = t if (t.endswith("Z") or "+" in t or "-" in t) else f"{t}Z"
        ts = parse_timestamp_value("EXIF:GPS:GPSDateStamp/GPSTimeStamp", f"{d} {t_z}")
        if ts.problem:
            return None, [ts.problem]
        return ts.normalized or None, []
    if combined_xmp and combined_xmp.strip():
        ts = parse_timestamp_value("XMP:XMP-exif:GPSDateTime", combined_xmp.strip())
        if ts.problem:
            return None, [ts.problem]
        return ts.normalized or None, []
    if date_stamp and date_stamp.strip():
        ts = parse_timestamp_value("EXIF:GPS:GPSDateStamp", date_stamp.strip())
        if ts.problem:
            return None, [ts.problem]
        return ts.normalized or None, []
    return None, []


def format_dms(value: float, axis: str) -> str:
    hemi = ("N" if value >= 0 else "S") if axis == "lat" else ("E" if value >= 0 else "W")
    av = abs(value)
    deg = int(av)
    rem = (av - deg) * 60.0
    mins = int(rem)
    secs = (rem - mins) * 60.0
    return f"{deg}° {mins:02d}' {secs:05.2f}\" {hemi}"


def parse_embedded_gps_records(
    records: list[MetadataRecord],
) -> tuple[bool, dict[str, Any]]:
    """Inspect embedded metadata records and extract normalized GPS fields if present.

    Returns ``(has_any_gps_fields, parsed_dict)``. Strictly ignores Composite/Tool tags.
    """
    exif_gps: dict[str, MetadataRecord] = {}
    xmp_gps: dict[str, MetadataRecord] = {}

    for r in records:
        if r.origin != "embedded":
            continue
        if r.group_name == "EXIF:GPS":
            exif_gps[r.field_name] = r
        elif r.category == "XMP" and r.field_name.startswith("GPS"):
            xmp_gps[r.field_name] = r

    # Decide source: EXIF:GPS primary, XMP GPS fallback
    has_exif_coords = any(k in exif_gps for k in ("GPSLatitude", "GPSLongitude"))
    has_xmp_coords = any(k in xmp_gps for k in ("GPSLatitude", "GPSLongitude"))
    has_any = has_exif_coords or has_xmp_coords or ("GPSAltitude" in exif_gps)

    if not has_any:
        return False, {}

    raw_fields: dict[str, dict[str, str]] = {}
    for bucket in (exif_gps, xmp_gps):
        for r in bucket.values():
            raw_fields[f"{r.group_name}:{r.field_name}"] = {
                "raw": r.raw_value,
                "formatted": r.normalized_value,
            }

    warnings_list: list[str] = []
    if has_exif_coords:
        source_bucket = exif_gps
        source_field = "EXIF:GPS:GPSLatitude, EXIF:GPS:GPSLongitude"
    elif has_xmp_coords:
        source_bucket = xmp_gps
        source_field = "XMP:GPSLatitude, XMP:GPSLongitude"
    else:
        source_bucket = exif_gps
        source_field = "EXIF:GPS (altitude/partial only)"

    lat_rec = source_bucket.get("GPSLatitude")
    lon_rec = source_bucket.get("GPSLongitude")
    lat_ref = source_bucket.get("GPSLatitudeRef")
    lon_ref = source_bucket.get("GPSLongitudeRef")

    lat: float | None = None
    lon: float | None = None
    if lat_rec is None and lon_rec is None:
        warnings_list.append("Partial GPS metadata found without latitude/longitude coordinates.")
    elif lat_rec is None:
        warnings_list.append("Incomplete GPS coordinates: longitude is present without latitude.")
    elif lon_rec is None:
        warnings_list.append("Incomplete GPS coordinates: latitude is present without longitude.")
    else:
        lat, lw = parse_coordinate(
            lat_rec.raw_value, lat_ref.raw_value if lat_ref else None, axis="lat"
        )
        lon, nw = parse_coordinate(
            lon_rec.raw_value, lon_ref.raw_value if lon_ref else None, axis="lon"
        )
        warnings_list.extend(lw)
        warnings_list.extend(nw)

    alt_rec = exif_gps.get("GPSAltitude") or xmp_gps.get("GPSAltitude")
    alt_ref = exif_gps.get("GPSAltitudeRef") or xmp_gps.get("GPSAltitudeRef")
    alt, aw = parse_altitude(
        alt_rec.raw_value if alt_rec else None, alt_ref.raw_value if alt_ref else None
    )
    warnings_list.extend(aw)

    gps_ts, tw = parse_gps_timestamp(
        exif_gps["GPSDateStamp"].raw_value if "GPSDateStamp" in exif_gps else None,
        exif_gps["GPSTimeStamp"].raw_value if "GPSTimeStamp" in exif_gps else None,
        xmp_gps["GPSDateTime"].raw_value if "GPSDateTime" in xmp_gps else None,
    )
    warnings_list.extend(tw)

    is_valid = lat is not None and lon is not None
    if is_valid and abs(lat) < 1e-6 and abs(lon) < 1e-6:
        warnings_list.append(
            "Coordinates are 0.0, 0.0 ('Null Island'), which is often a camera default or "
            "unset placeholder rather than an actual capture location."
        )

    # Note any conflict if BOTH EXIF:GPS and XMP GPS coordinates exist and disagree
    if has_exif_coords and has_xmp_coords and is_valid:
        xl, _ = parse_coordinate(
            xmp_gps["GPSLatitude"].raw_value,
            xmp_gps["GPSLatitudeRef"].raw_value if "GPSLatitudeRef" in xmp_gps else None,
            axis="lat",
        )
        xn, _ = parse_coordinate(
            xmp_gps["GPSLongitude"].raw_value,
            xmp_gps["GPSLongitudeRef"].raw_value if "GPSLongitudeRef" in xmp_gps else None,
            axis="lon",
        )
        if xl is not None and xn is not None and (abs(xl - lat) > 0.001 or abs(xn - lon) > 0.001):
            warnings_list.append(
                f"GPS discrepancy: EXIF:GPS ({lat:.6f}, {lon:.6f}) differs from XMP GPS "
                f"({xl:.6f}, {xn:.6f}). Reported as a factual discrepancy, not a conclusion."
            )

    return True, {
        "raw_fields": raw_fields,
        "latitude": lat,
        "longitude": lon,
        "altitude": alt,
        "gps_timestamp": gps_ts,
        "source_field": source_field,
        "is_valid": is_valid,
        "warnings": tuple(warnings_list),
    }


# --- opt-in OpenStreetMap tile math & guarded fetcher -------------------------------------
def lat_lon_to_tile(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """Slippy-map fractional tile coordinates (x, y) for Web Mercator (clamped to +-85.0511°)."""
    lat_c = max(-85.0511, min(85.0511, lat))
    lat_rad = math.radians(lat_c)
    n = 2**zoom
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def fetch_osm_tile(z: int, x: int, y: int, *, allow_external_map: bool) -> bytes:
    """Fetch a single 256x256 PNG map tile ONLY when *allow_external_map* is explicitly True.

    Never writes to disk; returns raw PNG bytes in memory.
    """
    if not allow_external_map:
        raise GpsError("External map preview is disabled (opt-in required).")
    if not (MAP_ZOOM_MIN <= z <= MAP_ZOOM_MAX):
        raise GpsError(f"Zoom must be between {MAP_ZOOM_MIN} and {MAP_ZOOM_MAX}.")
    n = 2**z
    tx = x % n
    ty = max(0, min(n - 1, y))
    url = OSM_TILE_URL.format(z=z, x=tx, y=ty)
    req = urllib.request.Request(  # noqa: S310 (fixed https://tile.openstreetmap.org URL)
        url,
        headers={
            "User-Agent": f"{APP_NAME}/{__version__} (local forensic workstation; opt-in preview)"
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TILE_TIMEOUT_SECONDS) as resp:  # noqa: S310 (fixed https://tile.openstreetmap.org URL)
            data = resp.read(2 * 1024 * 1024)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GpsError(f"Could not fetch map tile from OpenStreetMap: {exc}") from exc
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise GpsError("Map tile server returned a non-PNG response.")
    return data


# --- database service ---------------------------------------------------------------------
class GpsService:
    def __init__(
        self,
        db: Database,
        evidence_repo: EvidenceRepository,
        metadata_service: MetadataService,
        actor: Callable[[], str] = lambda: "Analyst",
    ) -> None:
        self.db = db
        self.evidence = evidence_repo
        self.metadata = metadata_service
        self.actor = actor

    def latest_run(self, evidence_id: str) -> AnalysisRun | None:
        row = self.db.con.execute(
            "SELECT * FROM analysis_runs WHERE evidence_id = ? AND module = ? "
            "ORDER BY id DESC LIMIT 1",
            (evidence_id, GPS_MODULE_NAME),
        ).fetchone()
        return AnalysisRun.from_row(row) if row else None

    def latest_record(self, evidence_id: str) -> GpsRecord | None:
        run = self.latest_run(evidence_id)
        if run is None:
            return None
        row = self.db.con.execute(
            "SELECT * FROM gps_records WHERE evidence_id = ? AND analysis_run_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (evidence_id, run.id),
        ).fetchone()
        return GpsRecord.from_row(row) if row else None

    def latest_state(self, evidence_id: str) -> tuple[AnalysisRun | None, GpsRecord | None]:
        run = self.latest_run(evidence_id)
        if run is None:
            return None, None
        row = self.db.con.execute(
            "SELECT * FROM gps_records WHERE evidence_id = ? AND analysis_run_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (evidence_id, run.id),
        ).fetchone()
        return run, (GpsRecord.from_row(row) if row else None)

    def parse(self, evidence_id: str, *, extract_if_missing: bool = True) -> GpsParseOutcome:
        """Parse and validate embedded GPS from extracted metadata records."""
        item = self.evidence.get_required(evidence_id)
        meta_run, meta_records = self.metadata.latest_metadata(evidence_id)
        if meta_run is None:
            if not extract_if_missing:
                raise GpsError(
                    "Metadata has not been extracted yet. Run metadata extraction first."
                )
            extracted = self.metadata.extract(evidence_id)
            meta_run, meta_records = extracted.run, extracted.records

        started_at = utc_now_iso()
        has_gps, parsed = parse_embedded_gps_records(meta_records)
        extracted_at = utc_now_iso()
        version_label = f"{GPS_PARSER_VERSION}; {meta_run.version}"

        if not has_gps:
            status = "ok"
            warnings_list: tuple[str, ...] = ()
            output_meta = {
                "has_gps": False,
                "is_valid": False,
                "parser_version": GPS_PARSER_VERSION,
                "metadata_run_id": meta_run.id,
                "message": NO_GPS_MESSAGE,
            }
        else:
            warnings_list = parsed["warnings"]
            is_valid = bool(parsed["is_valid"])
            status = "warning" if (not is_valid or warnings_list) else "ok"
            output_meta = {
                "has_gps": True,
                "is_valid": is_valid,
                "source_field": parsed["source_field"],
                "parser_version": GPS_PARSER_VERSION,
                "metadata_run_id": meta_run.id,
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
                    GPS_MODULE_NAME,
                    version_label,
                    started_at,
                    finished_at,
                    status,
                    json.dumps(output_meta, sort_keys=True, ensure_ascii=False),
                    json.dumps(list(warnings_list), ensure_ascii=False),
                ),
            )
            run_id = int(cur.lastrowid)
            if has_gps:
                con.execute(
                    "INSERT INTO gps_records ("
                    "analysis_run_id, evidence_id, raw_fields_json, latitude, longitude, "
                    "altitude, gps_timestamp, source_field, parser_version, is_valid, "
                    "warnings_json, extracted_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        item.id,
                        json.dumps(parsed["raw_fields"], sort_keys=True, ensure_ascii=False),
                        parsed["latitude"],
                        parsed["longitude"],
                        parsed["altitude"],
                        parsed["gps_timestamp"],
                        parsed["source_field"],
                        GPS_PARSER_VERSION,
                        1 if parsed["is_valid"] else 0,
                        json.dumps(list(warnings_list), ensure_ascii=False),
                        extracted_at,
                    ),
                )
            append_event(
                con,
                "gps_parsed",
                case_id=item.case_id,
                evidence_id=item.id,
                actor_label=self.actor(),
                details={
                    "analysis_run_id": run_id,
                    "has_gps": has_gps,
                    "is_valid": bool(parsed.get("is_valid")) if has_gps else False,
                    "parser_version": GPS_PARSER_VERSION,
                    "warnings_count": len(warnings_list),
                },
            )

        run, rec = self.latest_state(item.id)
        if run is None:
            raise GpsError("GPS run was not found after saving.")
        log.info(
            "GPS parsed for %s: has_gps=%s valid=%s",
            item.id,
            has_gps,
            bool(rec and rec.is_valid),
        )
        return GpsParseOutcome(has_gps=has_gps, record=rec, run=run)

    def record_external_map_load(self, evidence_id: str, zoom: int) -> None:
        """Audit an explicit opt-in external map preview load (no coordinates logged)."""
        item = self.evidence.get_required(evidence_id)
        with self.db.transaction() as con:
            append_event(
                con,
                "external_map_preview_loaded",
                case_id=item.case_id,
                evidence_id=item.id,
                actor_label=self.actor(),
                details={"provider": "tile.openstreetmap.org", "zoom": int(zoom)},
            )


def build_offline_summary_text(rec: GpsRecord) -> str:
    if not rec.is_valid or rec.latitude is None or rec.longitude is None:
        return "Invalid or incomplete GPS metadata"
    return f"{rec.latitude:.6f}, {rec.longitude:.6f}"
