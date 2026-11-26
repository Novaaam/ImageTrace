"""Cross-evidence case correlation, chronological timeline, and global offline search (v0.3.0).

Strictly offline and read-only on evidence files:
- Links derivative images sharing XMP OriginalDocumentID / DocumentID / DerivedFrom.
- Clusters items by physical Camera Serial Number, Camera Make/Model, JPEG DQT table hash,
  and perceptual dHash visual similarity (clearly separating facts from heuristic clues).
- Builds a chronological case timeline flagging clock-skew or shutter-count ordering anomalies.
- Aggregates valid embedded GPS coordinates for multi-image offline map plotting.
- Provides fast case/workspace-wide offline search across cases, evidence, metadata, origin/trace,
  automated OCR text, and analyst notes.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from imagetrace.cases import CaseRepository
from imagetrace.db import Database
from imagetrace.evidence import Evidence, EvidenceRepository
from imagetrace.gps import GpsService
from imagetrace.image_analysis import DEFAULT_SIMILARITY_MAX_DISTANCE, dhash_distance
from imagetrace.metadata import MetadataService
from imagetrace.notes import NotesRepository
from imagetrace.ocr import OcrService
from imagetrace.provenance import ProvenanceReport, ProvenanceService


@dataclass(frozen=True)
class LineageLink:
    source_id: str
    source_name: str
    target_id: str
    target_name: str
    relationship: str
    shared_id: str


@dataclass(frozen=True)
class CorrelationCluster:
    cluster_type: str  # Camera Serial | Camera Device | DQT Fingerprint | Visual Similarity (dHash)
    classification: str  # Extracted fact | Automated heuristic clue
    label: str
    evidence_ids: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class TimelineEntry:
    evidence_id: str
    filename: str
    capture_time: str
    modify_time: str
    imported_at: str
    device: str
    shutter_count: int | None
    sort_key: str
    anomaly: str


@dataclass(frozen=True)
class CaseGpsPoint:
    evidence_id: str
    filename: str
    latitude: float
    longitude: float
    timestamp: str


@dataclass(frozen=True)
class CaseCorrelationReport:
    case_id: str
    evidence_count: int
    lineage_links: tuple[LineageLink, ...]
    clusters: tuple[CorrelationCluster, ...]
    timeline: tuple[TimelineEntry, ...]
    gps_points: tuple[CaseGpsPoint, ...]
    anomalies: tuple[str, ...]


@dataclass(frozen=True)
class SearchHit:
    kind: str  # Case | Evidence | Origin & Trace | Metadata | OCR | Analyst note
    case_id: str
    evidence_id: str | None
    tab_index: int  # EvidenceDetails tab index when evidence_id is set
    title: str
    snippet: str


def _parse_int(text: str) -> int | None:
    digits = "".join(ch for ch in (text or "") if ch.isdigit())
    return int(digits) if digits else None


def correlate_case(
    evidence_repo: EvidenceRepository,
    metadata_service: MetadataService,
    gps_service: GpsService,
    provenance_service: ProvenanceService,
    case_id: str,
) -> CaseCorrelationReport:
    """Build cross-evidence lineage, device/DQT/dHash clusters, timeline, and GPS summary."""
    items: list[Evidence] = evidence_repo.list_for_case(case_id)
    by_id = {ev.id: ev for ev in items}

    prov_by_id: dict[str, ProvenanceReport] = {}
    meta_dates: dict[str, tuple[str, str]] = {}  # (capture_dt, modify_dt)
    gps_points: list[CaseGpsPoint] = []

    for ev in items:
        rep = provenance_service.latest_report(ev.id)
        if rep is not None:
            prov_by_id[ev.id] = rep

        _mrun, mrecords = metadata_service.latest_metadata(ev.id)
        cap_dt = mod_dt = ""
        for r in mrecords:
            if r.origin != "embedded":
                continue
            if (r.field_name == "DateTimeOriginal" and not cap_dt) or (
                r.field_name == "CreateDate" and not cap_dt
            ):
                cap_dt = r.normalized_value
            elif r.field_name == "ModifyDate" and not mod_dt:
                mod_dt = r.normalized_value
        meta_dates[ev.id] = (cap_dt, mod_dt)

        _grun, grec = gps_service.latest_state(ev.id)
        if (
            grec is not None
            and grec.is_valid
            and grec.latitude is not None
            and grec.longitude is not None
        ):
            gps_points.append(
                CaseGpsPoint(
                    evidence_id=ev.id,
                    filename=ev.original_filename,
                    latitude=grec.latitude,
                    longitude=grec.longitude,
                    timestamp=grec.gps_timestamp or cap_dt or ev.imported_at,
                )
            )

    # 1. XMP Document Lineage links
    lineage: list[LineageLink] = []
    doc_id_owner: dict[str, str] = {}
    orig_doc_groups: dict[str, list[str]] = defaultdict(list)

    for ev in items:
        rep = prov_by_id.get(ev.id)
        if rep is None:
            continue
        did = rep.document_ids.get("DocumentID", "")
        odid = rep.document_ids.get("OriginalDocumentID", "")
        if did:
            doc_id_owner.setdefault(did, ev.id)
        if odid:
            orig_doc_groups[odid].append(ev.id)

    seen_edges: set[tuple[str, str, str]] = set()
    for ev in items:
        rep = prov_by_id.get(ev.id)
        if rep is None:
            continue
        derived_from = (
            rep.document_ids.get("DerivedFrom.documentID")
            or rep.document_ids.get("DerivedFrom.originalDocumentID")
            or ""
        )
        if derived_from and derived_from in doc_id_owner:
            src_id = doc_id_owner[derived_from]
            if src_id != ev.id and (src_id, ev.id, derived_from) not in seen_edges:
                seen_edges.add((src_id, ev.id, derived_from))
                lineage.append(
                    LineageLink(
                        source_id=src_id,
                        source_name=by_id[src_id].original_filename,
                        target_id=ev.id,
                        target_name=ev.original_filename,
                        relationship="XMP DerivedFrom -> DocumentID",
                        shared_id=derived_from,
                    )
                )

    for odid, eids in orig_doc_groups.items():
        if len(eids) >= 2:
            # Order earliest capture/import first
            ordered = sorted(eids, key=lambda i: meta_dates[i][0] or by_id[i].imported_at)
            root_id = ordered[0]
            for child_id in ordered[1:]:
                if (root_id, child_id, odid) not in seen_edges:
                    seen_edges.add((root_id, child_id, odid))
                    lineage.append(
                        LineageLink(
                            source_id=root_id,
                            source_name=by_id[root_id].original_filename,
                            target_id=child_id,
                            target_name=by_id[child_id].original_filename,
                            relationship="Shared XMP OriginalDocumentID",
                            shared_id=odid,
                        )
                    )

    # 2. Hardware / Device / DQT / dHash Clusters
    clusters: list[CorrelationCluster] = []
    serial_groups: dict[str, list[str]] = defaultdict(list)
    device_groups: dict[str, list[str]] = defaultdict(list)
    dqt_groups: dict[str, list[str]] = defaultdict(list)

    for ev in items:
        rep = prov_by_id.get(ev.id)
        if rep is None:
            continue
        for sk in ("SerialNumber", "BodySerialNumber", "InternalSerialNumber"):
            sv = rep.hardware_ids.get(sk)
            if sv:
                serial_groups[sv].append(ev.id)
                break
        if rep.capture_device and rep.capture_device != "Not recorded in file":
            device_groups[rep.capture_device].append(ev.id)
        dhash_qt = rep.dqt_info.get("dqt_hash") or rep.binary_findings.get("dqt_hash", "")
        if dhash_qt:
            dqt_groups[dhash_qt].append(ev.id)

    for serial, eids in sorted(serial_groups.items()):
        if len(eids) >= 2:
            clusters.append(
                CorrelationCluster(
                    cluster_type="Camera Serial Number",
                    classification="Extracted fact",
                    label=f"Serial {serial}",
                    evidence_ids=tuple(eids),
                    detail=f"{len(eids)} items report identical embedded camera serial '{serial}'.",
                )
            )
    for dev, eids in sorted(device_groups.items()):
        if len(eids) >= 2:
            clusters.append(
                CorrelationCluster(
                    cluster_type="Capture Device",
                    classification="Extracted fact",
                    label=dev,
                    evidence_ids=tuple(eids),
                    detail=f"{len(eids)} items report capture device '{dev}'.",
                )
            )
    for qt_h, eids in sorted(dqt_groups.items()):
        if len(eids) >= 2:
            clusters.append(
                CorrelationCluster(
                    cluster_type="JPEG DQT Table",
                    classification="Automated heuristic clue",
                    label=f"DQT {qt_h[:12]}…",
                    evidence_ids=tuple(eids),
                    detail=(
                        f"{len(eids)} JPEGs share identical quantization tables "
                        "(consistent encoder or platform re-save; not proof of same device)."
                    ),
                )
            )

    # Visual similarity (dHash) pairs
    for i, ev_a in enumerate(items):
        if not ev_a.dhash:
            continue
        for ev_b in items[i + 1 :]:
            if not ev_b.dhash:
                continue
            dist = dhash_distance(ev_a.dhash, ev_b.dhash)
            if dist is not None and dist <= DEFAULT_SIMILARITY_MAX_DISTANCE:
                exact = (
                    "exact SHA-256 duplicate" if ev_a.sha256 == ev_b.sha256 else "different bytes"
                )
                clusters.append(
                    CorrelationCluster(
                        cluster_type="Visual Similarity (dHash)",
                        classification="Automated heuristic clue",
                        label=f"{ev_a.id} <-> {ev_b.id}",
                        evidence_ids=(ev_a.id, ev_b.id),
                        detail=f"dHash distance {dist}/64 ({exact}).",
                    )
                )

    # 3. Chronological Case Timeline & Shutter-Count / Clock-Skew Anomalies
    raw_entries: list[tuple[str, Evidence, str, str, str, int | None]] = []
    for ev in items:
        cap_dt, mod_dt = meta_dates[ev.id]
        rep = prov_by_id.get(ev.id)
        dev = rep.capture_device if rep is not None else "Not analyzed"
        shutter = None
        if rep is not None:
            shutter = _parse_int(
                rep.hardware_ids.get("ShutterCount") or rep.hardware_ids.get("ImageNumber", "")
            )
        sort_k = cap_dt or mod_dt or ev.imported_at
        raw_entries.append((sort_k, ev, cap_dt, mod_dt, dev, shutter))

    raw_entries.sort(key=lambda t: t[0])

    anomalies: list[str] = []
    last_shutter_by_serial: dict[str, tuple[str, int, str]] = {}
    timeline_entries: list[TimelineEntry] = []

    for sort_k, ev, cap_dt, mod_dt, dev, shutter in raw_entries:
        item_notes: list[str] = []
        if cap_dt and mod_dt and mod_dt[:19] < cap_dt[:19]:
            msg = f"{ev.id}: ModifyDate ({mod_dt}) precedes capture date ({cap_dt})."
            item_notes.append("ModifyDate < CaptureDate")
            anomalies.append(msg)
        rep = prov_by_id.get(ev.id)
        serial = ""
        if rep is not None:
            for sk in ("SerialNumber", "BodySerialNumber", "InternalSerialNumber"):
                if rep.hardware_ids.get(sk):
                    serial = rep.hardware_ids[sk]
                    break
        if serial and shutter is not None and cap_dt:
            prev = last_shutter_by_serial.get(serial)
            if prev is not None and shutter < prev[1]:
                msg = (
                    f"{ev.id}: ShutterCount {shutter} is lower than earlier timestamped "
                    f"{prev[0]} (ShutterCount {prev[1]}) on camera serial {serial} "
                    "(possible clock skew or edited timestamp)."
                )
                item_notes.append("ShutterCount out of order")
                anomalies.append(msg)
            last_shutter_by_serial[serial] = (ev.id, shutter, cap_dt)

        timeline_entries.append(
            TimelineEntry(
                evidence_id=ev.id,
                filename=ev.original_filename,
                capture_time=cap_dt or "Not recorded",
                modify_time=mod_dt or "Not recorded",
                imported_at=ev.imported_at,
                device=dev,
                shutter_count=shutter,
                sort_key=sort_k,
                anomaly="; ".join(item_notes),
            )
        )

    return CaseCorrelationReport(
        case_id=case_id,
        evidence_count=len(items),
        lineage_links=tuple(lineage),
        clusters=tuple(clusters),
        timeline=tuple(timeline_entries),
        gps_points=tuple(gps_points),
        anomalies=tuple(anomalies),
    )


def _snippet(text: str, needle: str, radius: int = 45) -> str:
    s = " ".join((text or "").split())
    idx = s.lower().find(needle)
    if idx < 0:
        return s[:90]
    lo = max(0, idx - radius)
    hi = min(len(s), idx + len(needle) + radius)
    prefix = "…" if lo > 0 else ""
    suffix = "…" if hi < len(s) else ""
    return f"{prefix}{s[lo:hi]}{suffix}"


def search_workspace(
    db: Database,
    cases_repo: CaseRepository,
    evidence_repo: EvidenceRepository,
    metadata_service: MetadataService,
    ocr_service: OcrService,
    notes_repo: NotesRepository,
    provenance_service: ProvenanceService,
    query: str,
    *,
    active_case_id: str | None = None,
    category_filter: str = "All",
    limit: int = 120,
) -> list[SearchHit]:
    """Offline search across cases, evidence, origin & trace, metadata, OCR, and notes."""
    q = (query or "").strip().lower()
    if not q:
        return []

    hits: list[SearchHit] = []

    # 1. Cases
    if category_filter in ("All", "Case"):
        for c in cases_repo.list(include_archived=True):
            hay = f"{c.id} {c.title} {' '.join(c.tags)} {c.notes}".lower()
            if q in hay:
                hits.append(
                    SearchHit(
                        kind="Case",
                        case_id=c.id,
                        evidence_id=None,
                        tab_index=0,
                        title=f"{c.id} · {c.title}",
                        snippet=_snippet(f"{c.title} | Tags: {', '.join(c.tags)} | {c.notes}", q),
                    )
                )

    # Determine which cases' evidence to scan (active case first, or all if none)
    target_cases = [active_case_id] if active_case_id else [c.id for c in cases_repo.list()]
    for cid in target_cases:
        if not cid:
            continue
        ev_items = evidence_repo.list_for_case(cid)
        for ev in ev_items:
            if len(hits) >= limit:
                return hits

            # 2. Evidence item fields
            if category_filter in ("All", "Evidence"):
                ev_hay = (
                    f"{ev.id} {ev.original_filename} {ev.detected_format} "
                    f"{ev.sha256} {ev.import_source_note}"
                )
                if q in ev_hay.lower():
                    hits.append(
                        SearchHit(
                            kind="Evidence",
                            case_id=cid,
                            evidence_id=ev.id,
                            tab_index=0,
                            title=f"{ev.id} · {ev.original_filename}",
                            snippet=_snippet(ev_hay, q),
                        )
                    )

            # 3. Origin & Trace
            if category_filter in ("All", "Origin & Trace"):
                rep = provenance_service.latest_report(ev.id)
                if rep is not None:
                    ot_blob = " ".join(
                        [
                            rep.primary_category,
                            rep.primary_verdict,
                            rep.capture_device,
                            rep.capture_os_or_platform,
                            " ".join(rep.editing_software),
                            " ".join(rep.ai_indicators),
                            " ".join(f"{k}={v}" for k, v in rep.hardware_ids.items()),
                            rep.dqt_info.get("dqt_hash", ""),
                            rep.dqt_info.get("profile_hint", ""),
                        ]
                    )
                    if q in ot_blob.lower():
                        hits.append(
                            SearchHit(
                                kind="Origin & Trace",
                                case_id=cid,
                                evidence_id=ev.id,
                                tab_index=1,
                                title=f"{ev.id} · {ev.original_filename}",
                                snippet=_snippet(ot_blob, q),
                            )
                        )

            # 4. Extracted Metadata
            if category_filter in ("All", "Metadata"):
                _mrun, mrecs = metadata_service.latest_metadata(ev.id)
                for r in mrecs:
                    line = f"{r.group_name}:{r.field_name} = {r.normalized_value}"
                    if q in line.lower() or q in r.raw_value.lower():
                        hits.append(
                            SearchHit(
                                kind="Metadata",
                                case_id=cid,
                                evidence_id=ev.id,
                                tab_index=2,
                                title=f"{ev.id} · {r.field_name}",
                                snippet=_snippet(line, q),
                            )
                        )
                        break  # one top hit per evidence item keeps results readable

            # 5. Automated OCR text
            if category_filter in ("All", "OCR"):
                orun, _oitems = ocr_service.latest_ocr(ev.id)
                if orun is not None and q in (orun.full_text or "").lower():
                    hits.append(
                        SearchHit(
                            kind="OCR",
                            case_id=cid,
                            evidence_id=ev.id,
                            tab_index=4,
                            title=f"{ev.id} · {ev.original_filename} (Automated OCR)",
                            snippet=_snippet(orun.full_text, q),
                        )
                    )

            # 6. Analyst notes
            if category_filter in ("All", "Analyst note"):
                for n in notes_repo.list_for_evidence(ev.id, include_history=False):
                    n_blob = f"{n.category} {n.confidence_label} {n.source_reference} {n.note_text}"
                    if q in n_blob.lower():
                        hits.append(
                            SearchHit(
                                kind="Analyst note",
                                case_id=cid,
                                evidence_id=ev.id,
                                tab_index=6,
                                title=f"{ev.id} · Note #{n.id} ({n.confidence_label})",
                                snippet=_snippet(n_blob, q),
                            )
                        )
                        if len(hits) >= limit:
                            return hits

    # Keep db parameter referenced cleanly for any future direct SQL indexing
    _ = db
    return hits[:limit]


def summarize_correlation_counts(report: CaseCorrelationReport) -> dict[str, Any]:
    return {
        "evidence_count": report.evidence_count,
        "lineage_links": len(report.lineage_links),
        "clusters": len(report.clusters),
        "gps_points": len(report.gps_points),
        "anomalies": len(report.anomalies),
    }
