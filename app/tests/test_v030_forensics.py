"""Unit & integration tests for v0.3.0 (Forensics, HEIC/RAW, Correlation, Visual PDF, Bundles)."""

from __future__ import annotations

import hashlib
import io
import struct
import urllib.request
import zipfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from imagetrace import image_analysis
from imagetrace.audit import list_events
from imagetrace.cases import CaseRepository
from imagetrace.correlation import correlate_case, search_workspace
from imagetrace.db import open_database
from imagetrace.evidence import EvidenceRepository
from imagetrace.forensics import (
    compute_channel_view,
    compute_jpeg_ghost_curve,
    compute_luminance_gradient,
    compute_noise_residual,
    dqt_fingerprint,
    parse_c2pa_claims,
)
from imagetrace.gps import GpsService
from imagetrace.hashing import sha256_file
from imagetrace.metadata import MetadataService
from imagetrace.notes import NotesRepository
from imagetrace.ocr import OcrService
from imagetrace.provenance import PROVENANCE_ENGINE_VERSION, ProvenanceService
from imagetrace.reports import ReportOptions, ReportService
from imagetrace.tools_status import find_exiftool
from synthetic_images import make_exif_jpeg_with_exiftool, make_valid_jpeg

SCOPE = "Authorized synthetic scope for ImageTrace v0.3.0 verification."


def _inject_jpeg_segment(jpeg_path: Path, marker_byte: int, payload: bytes) -> None:
    raw = jpeg_path.read_bytes()
    assert raw.startswith(b"\xff\xd8")
    seg_len = len(payload) + 2
    seg = bytes([0xFF, marker_byte]) + struct.pack(">H", seg_len) + payload
    jpeg_path.write_bytes(raw[:2] + seg + raw[2:])


def _block_network(monkeypatch) -> None:
    def _deny(*_a, **_k):
        raise AssertionError("Network access must NEVER occur during offline v0.3.0 workflows!")

    monkeypatch.setattr(urllib.request, "urlopen", _deny)


def _make_stack(tmp_path: Path):
    db = open_database(tmp_path)
    cases = CaseRepository(db, tmp_path / "cases", actor=lambda: "Analyst")
    ev_repo = EvidenceRepository(db, tmp_path, actor=lambda: "Analyst")
    meta = MetadataService(db, ev_repo, actor=lambda: "Analyst")
    gps = GpsService(db, ev_repo, meta, actor=lambda: "Analyst")
    ocr = OcrService(db, ev_repo, actor=lambda: "Analyst")
    notes = NotesRepository(db, actor=lambda: "Analyst")
    prov = ProvenanceService(db, ev_repo, meta, actor=lambda: "Analyst")
    reports = ReportService(
        db,
        tmp_path,
        cases,
        ev_repo,
        meta,
        gps,
        ocr,
        notes,
        provenance_service=prov,
        actor=lambda: "Analyst",
    )
    case = cases.create("v0.3.0 Forensic Case", SCOPE, tags="synthetic, v030")
    return db, case, cases, ev_repo, meta, gps, ocr, notes, prov, reports


def test_dqt_fingerprint_and_c2pa_offline_claim_parser():
    # Exact standard IJG Q=75 stripped 4:2:0 table
    from imagetrace.forensics import _PHOTOSHOP_Q10_LUMA, _PHOTOSHOP_Q12_LUMA, _ijg_zigzag_luma

    fp75 = dqt_fingerprint(list(_ijg_zigzag_luma(75)), subsampling="4:2:0", has_exif=False)
    assert len(fp75["dqt_hash"]) == 64
    assert fp75["classification"] == "Automated heuristic clue"
    assert "Q=75" in fp75["profile_hint"]

    fp_ps10 = dqt_fingerprint(list(_PHOTOSHOP_Q10_LUMA), has_adobe=True)
    assert "Photoshop Save As Q10" in fp_ps10["profile_hint"]

    fp_ps12 = dqt_fingerprint(list(_PHOTOSHOP_Q12_LUMA))
    assert "Q=100" in fp_ps12["profile_hint"]

    # Offline C2PA JUMBF readable claim extraction
    fake_jumbf = (
        b"\x00\x00jumbc2pa.claim"
        b'{"claim_generator":"Adobe Firefly 3.0","actions":["c2pa.created","c2pa.edited"],'
        b'"digitalSourceType":"http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"}'
    )
    claims = parse_c2pa_claims(fake_jumbf)
    assert claims["present"] is True
    assert "Adobe Firefly 3.0" in claims["claim_generator"]
    assert "c2pa.created" in claims["actions"]
    assert claims["ai_source_declared"] is True
    assert claims["signature_verified"] is False
    assert "NOT verified" in claims["caveat"]


def test_visual_forensics_lab_and_payload_extractors(tmp_path, monkeypatch):
    _block_network(monkeypatch)
    db, case, _cases, ev_repo, _meta, _gps, _ocr, _notes, prov, _reports = _make_stack(tmp_path)
    try:
        # Build JPEG with embedded mismatched EXIF thumbnail + trailing Motion Photo MP4
        src = make_valid_jpeg(tmp_path / "motion_thumb.jpg", (260, 140))
        thumb_im = Image.new("RGB", (80, 80), (200, 50, 40))
        tb = io.BytesIO()
        thumb_im.save(tb, format="JPEG", quality=80)
        _inject_jpeg_segment(
            src,
            0xE1,
            b"Exif\x00\x00II*\x00\x08\x00\x00\x00\x00\x00\x00\x00\x00\x00" + tb.getvalue(),
        )
        mp4_tail = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\xab" * 48
        src.write_bytes(src.read_bytes() + mp4_tail)

        ev = ev_repo.import_file(case.id, src)
        orig_path = ev_repo.original_path(ev)
        orig_sha = ev.sha256

        # Visual forensics maps (pure Pillow, strictly in-memory)
        nr = compute_noise_residual(orig_path, strength=12.0)
        assert nr.image.size == (260, 140)
        assert "Median" in nr.summary

        lg = compute_luminance_gradient(orig_path, strength=3.0)
        assert lg.image.size == (260, 140)

        for ch in ("R", "G", "B", "Y", "Cb", "Cr"):
            cv = compute_channel_view(orig_path, ch)
            assert cv.image.size == (260, 140)

        ghost = compute_jpeg_ghost_curve(orig_path)
        assert len(ghost.points) >= 5
        assert 50 <= ghost.min_quality <= 98

        # Provenance report captures DQT fingerprint + trailing MP4 flag
        rep = prov.analyze(ev.id, extract_metadata_if_missing=False)
        assert rep.engine_version == PROVENANCE_ENGINE_VERSION
        assert rep.dqt_info.get("dqt_hash")
        assert rep.binary_findings["trailing_is_mp4"] is True
        assert rep.binary_findings["trailing_bytes"] == len(mp4_tail)

        # Safe extraction of embedded EXIF thumbnail & Motion Photo MP4
        out_thumb, t_sha = prov.export_embedded_thumbnail(ev.id, tmp_path / "extracted_thumb.jpg")
        assert out_thumb.is_file() and len(t_sha) == 64
        out_mp4, m_sha, nbytes = prov.export_trailing_media(ev.id, tmp_path / "extracted.mp4")
        assert out_mp4.read_bytes() == mp4_tail
        assert nbytes == len(mp4_tail) and len(m_sha) == 64
        assert sha256_file(orig_path) == orig_sha
    finally:
        db.close()


def test_heic_and_raw_detection_and_preview_fallback(tmp_path, monkeypatch):
    # Magic-byte sniffing for HEIC, CR3, CR2, DNG, NEF, ARW
    heic = tmp_path / "iphone_photo.heic"
    heic.write_bytes(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 32)
    dt_heic = image_analysis.detect_file_type(heic)
    assert dt_heic.supported is True and dt_heic.format_name == "HEIC"

    cr3 = tmp_path / "canon.cr3"
    cr3.write_bytes(b"\x00\x00\x00\x18ftypcrx \x00\x00\x00\x01crx isom" + b"\x00" * 32)
    assert image_analysis.detect_file_type(cr3).format_name == "CR3"

    cr2 = tmp_path / "canon.cr2"
    cr2.write_bytes(b"II*\x00\x10\x00\x00\x00CR\x02\x00" + b"\x00" * 32)
    assert image_analysis.detect_file_type(cr2).format_name == "CR2"

    dng = tmp_path / "raw_shot.dng"
    dng.write_bytes(b"II*\x00\x08\x00\x00\x00" + b"\x00" * 32)
    assert image_analysis.detect_file_type(dng).format_name == "DNG"

    # Simulate ExifTool returning an embedded JPEG preview for the HEIC stub
    prev_buf = io.BytesIO()
    Image.new("RGB", (180, 120), (45, 90, 160)).save(prev_buf, format="JPEG", quality=85)
    monkeypatch.setattr(
        image_analysis, "_extract_preview_via_exiftool", lambda _p: prev_buf.getvalue()
    )

    db, case, _cases, ev_repo, *_rest = _make_stack(tmp_path)
    try:
        ev = ev_repo.import_file(case.id, heic)
        assert ev.detected_format == "HEIC"
        assert ev.width == 180 and ev.height == 120
        assert ev.dhash is not None
        assert ev_repo.thumbnail_path(ev) is not None
        assert any("embedded JPEG preview" in w for w in ev.warnings)
    finally:
        db.close()


def test_case_correlation_lineage_clusters_anomalies_and_global_search(tmp_path, monkeypatch):
    _block_network(monkeypatch)
    exiftool = find_exiftool()
    if exiftool is None:
        pytest.skip("ExifTool not installed")

    db, case, cases, ev_repo, meta, gps, ocr, notes, prov, _reports = _make_stack(tmp_path)
    try:
        # Image A: original capture with SerialNumber, ShutterCount=150, DocumentID=root-1
        p_a = make_exif_jpeg_with_exiftool(
            tmp_path / "orig_shot.jpg",
            exiftool,
            {
                "IFD0:Make": "Canon",
                "IFD0:Model": "Canon EOS R5",
                "ExifIFD:DateTimeOriginal": "2026:05:10 10:00:00",
                "ExifIFD:SerialNumber": "CAM-SERIAL-7788",
                "XMP-aux:ImageNumber": "150",
            },
        )
        xmp_a = (
            b"http://ns.adobe.com/xap/1.0/\x00"
            b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
            b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"'
            b' xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/">'
            b'<rdf:Description xmpMM:DocumentID="xmp.did:root-1"'
            b' xmpMM:OriginalDocumentID="xmp.did:root-1"/>'
            b"</rdf:RDF></x:xmpmeta>"
        )
        _inject_jpeg_segment(p_a, 0xE1, xmp_a)

        # Image B: edited derivative with same Serial, lower ShutterCount=140 (anomaly!), GPS
        p_b = make_exif_jpeg_with_exiftool(
            tmp_path / "edited_ps.jpg",
            exiftool,
            {
                "IFD0:Make": "Canon",
                "IFD0:Model": "Canon EOS R5",
                "IFD0:Software": "Adobe Photoshop 25.0 (Windows)",
                "ExifIFD:DateTimeOriginal": "2026:05:10 12:00:00",
                "ExifIFD:SerialNumber": "CAM-SERIAL-7788",
                "XMP-aux:ImageNumber": "140",
                "GPSLatitude": "51.476852",
                "GPSLatitudeRef": "N",
                "GPSLongitude": "0.000500",
                "GPSLongitudeRef": "W",
            },
        )
        xmp_b = (
            b"http://ns.adobe.com/xap/1.0/\x00"
            b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
            b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"'
            b' xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"'
            b' xmlns:stRef="http://ns.adobe.com/xap/1.0/sType/ResourceRef#">'
            b'<rdf:Description xmpMM:DocumentID="xmp.did:child-2"'
            b' xmpMM:OriginalDocumentID="xmp.did:root-1">'
            b'<xmpMM:DerivedFrom stRef:documentID="xmp.did:root-1"/>'
            b"</rdf:Description></rdf:RDF></x:xmpmeta>"
        )
        _inject_jpeg_segment(p_b, 0xE1, xmp_b)

        ev_a = ev_repo.import_file(case.id, p_a)
        ev_b = ev_repo.import_file(case.id, p_b)
        for eid in (ev_a.id, ev_b.id):
            meta.extract(eid)
            gps.parse(eid, extract_if_missing=False)
            prov.analyze(eid, extract_metadata_if_missing=False)

        # Add mock OCR & Analyst note for global search verification
        ocr._custom_runner = lambda *_: (
            "Tesseract",
            "5.4.0-synth",
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t10\t10\t80\t20\t95.0\tHARBOURGATE",
        )
        ocr.run_ocr(ev_b.id, language="eng")
        notes.create(
            case.id,
            ev_b.id,
            category="Signage / visible text",
            confidence_label="Analyst observation",
            note_text="Distinctive red lighthouse visible on the pier.",
        )

        # Correlate case
        corr = correlate_case(ev_repo, meta, gps, prov, case.id)
        assert corr.evidence_count == 2
        assert len(corr.lineage_links) >= 1
        assert corr.lineage_links[0].source_id == ev_a.id
        assert corr.lineage_links[0].target_id == ev_b.id

        cluster_types = {c.cluster_type for c in corr.clusters}
        assert "Camera Serial Number" in cluster_types
        assert "Capture Device" in cluster_types
        assert any("ShutterCount" in a for a in corr.anomalies)
        assert len(corr.gps_points) == 1
        assert corr.gps_points[0].evidence_id == ev_b.id

        # Global search across Origin & Trace, OCR, and Analyst notes
        hits_ps = search_workspace(
            db, cases, ev_repo, meta, ocr, notes, prov, "Photoshop", active_case_id=case.id
        )
        assert any(h.kind == "Origin & Trace" and h.evidence_id == ev_b.id for h in hits_ps)

        hits_ocr = search_workspace(
            db, cases, ev_repo, meta, ocr, notes, prov, "HARBOURGATE", active_case_id=case.id
        )
        assert any(h.kind == "OCR" and h.tab_index == 4 for h in hits_ocr)

        hits_note = search_workspace(
            db, cases, ev_repo, meta, ocr, notes, prov, "lighthouse", active_case_id=case.id
        )
        assert any(h.kind == "Analyst note" and h.tab_index == 6 for h in hits_note)
    finally:
        db.close()


def test_visual_pdf_with_exif_mismatch_and_verifiable_zip_bundle(tmp_path, monkeypatch):
    _block_network(monkeypatch)
    db, case, _cases, ev_repo, _meta, _gps, _ocr, _notes, prov, reports = _make_stack(tmp_path)
    try:
        # Landscape main image with mismatched square EXIF thumbnail
        main_im = Image.new("RGB", (320, 160), (20, 20, 20))
        d_m = ImageDraw.Draw(main_im)
        for x in range(320):
            v = int(20 + (220 * x) / 319)
            d_m.line([(x, 0), (x, 159)], fill=(v, v, v))
        src = tmp_path / "mismatch_pdf.jpg"
        main_im.save(src, format="JPEG", quality=90)

        thumb_im = Image.new("RGB", (100, 100), (240, 240, 240))
        d_t = ImageDraw.Draw(thumb_im)
        for x in range(100):
            v = int(240 - (220 * x) / 99)
            d_t.line([(x, 0), (x, 99)], fill=(v, v, v))
        tb = io.BytesIO()
        thumb_im.save(tb, format="JPEG", quality=80)
        _inject_jpeg_segment(
            src,
            0xE1,
            b"Exif\x00\x00II*\x00\x08\x00\x00\x00\x00\x00\x00\x00\x00\x00" + tb.getvalue(),
        )

        ev = ev_repo.import_file(case.id, src)
        prov.analyze(ev.id, extract_metadata_if_missing=False)

        # Visual PDF vs text-only PDF (back-to-back in same second must never collide)
        pdf_vis = reports.generate_pdf(case.id, options=ReportOptions(include_visuals=True))
        pdf_txt = reports.generate_pdf(case.id, options=ReportOptions(include_visuals=False))
        assert reports.resolve_path(pdf_vis) != reports.resolve_path(pdf_txt)
        vis_bytes = reports.resolve_path(pdf_vis).read_bytes()
        txt_bytes = reports.resolve_path(pdf_txt).read_bytes()
        assert vis_bytes.startswith(b"%PDF-")
        assert b"/Subtype /Image" in vis_bytes
        assert len(vis_bytes) > len(txt_bytes)

        # Verifiable ZIP Case Bundle
        bundle = reports.generate_case_bundle(case.id, options=ReportOptions(include_visuals=True))
        assert bundle.path.is_file()
        assert bundle.path.with_name(bundle.path.name + ".sha256").is_file()
        assert sha256_file(bundle.path) == bundle.sha256

        with zipfile.ZipFile(bundle.path, "r") as zf:
            names = set(zf.namelist())
            assert "MANIFEST.sha256" in names
            assert any(n.startswith("reports/") and n.endswith(".pdf") for n in names)
            assert any(n.startswith("reports/") and n.endswith(".json") for n in names)
            assert any(n.startswith("thumbnails/") and n.endswith(".png") for n in names)
            # Never leak SQLite DB into exported bundle
            assert not any(n.endswith(".sqlite") for n in names)
            # Verify every line in MANIFEST.sha256 against actual archive bytes
            manifest_lines = zf.read("MANIFEST.sha256").decode("utf-8").strip().splitlines()
            assert len(manifest_lines) >= 3
            for line in manifest_lines:
                exp_hash, arc = line.split("  ", 1)
                actual_hash = hashlib.sha256(zf.read(arc)).hexdigest()
                assert actual_hash == exp_hash

        events = list_events(db.con, case_id=case.id)
        assert any(e.event_type == "case_bundle_exported" for e in events)
    finally:
        db.close()
