"""Unit & integration tests for the v0.2.0 Origin & Trace forensic provenance engine."""

from __future__ import annotations

import io
import json
import struct
import urllib.request
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, PngImagePlugin

from imagetrace.audit import list_events
from imagetrace.cases import CaseRepository
from imagetrace.db import open_database
from imagetrace.evidence import EvidenceRepository
from imagetrace.gps import GpsService
from imagetrace.hashing import sha256_file
from imagetrace.metadata import MetadataService
from imagetrace.notes import NotesRepository
from imagetrace.ocr import OcrService
from imagetrace.provenance import (
    PROVENANCE_ENGINE_VERSION,
    ProvenanceService,
    compute_ela,
    inspect_binary_structure,
    parse_xmp_history_packets,
    save_stripped_copy,
)
from imagetrace.reports import ReportService
from imagetrace.tools_status import find_exiftool
from synthetic_images import make_exif_jpeg_with_exiftool, make_valid_jpeg

SCOPE = "Authorized synthetic test scope for Origin & Trace verification."


def _inject_jpeg_segment(jpeg_path: Path, marker_byte: int, payload: bytes) -> None:
    raw = jpeg_path.read_bytes()
    assert raw.startswith(b"\xff\xd8")
    seg_len = len(payload) + 2
    seg = bytes([0xFF, marker_byte]) + struct.pack(">H", seg_len) + payload
    jpeg_path.write_bytes(raw[:2] + seg + raw[2:])


def _make_services(tmp_path: Path):
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
    case = cases.create("Provenance test case", SCOPE)
    return db, case, ev_repo, meta, prov, reports


def _block_network(monkeypatch):
    def _deny(*_a, **_k):
        raise AssertionError("Network access must NEVER occur during Origin & Trace!")

    monkeypatch.setattr(urllib.request, "urlopen", _deny)


def test_mobile_to_photoshop_to_illustrator_xmp_timeline(tmp_path, monkeypatch):
    _block_network(monkeypatch)
    exiftool = find_exiftool()
    if exiftool is None:
        pytest.skip("ExifTool not installed")

    db, case, ev_repo, _meta, prov, _reports = _make_services(tmp_path)
    try:
        src = make_exif_jpeg_with_exiftool(
            tmp_path / "iphone_ps_ai.jpg",
            exiftool,
            {
                "IFD0:Make": "Apple",
                "IFD0:Model": "iPhone 15 Pro",
                "ExifIFD:LensModel": "iPhone 15 Pro back triple camera 6.765mm f/1.78",
                "ExifIFD:DateTimeOriginal": "2026:05:10 14:30:00",
                "IFD0:Software": "Adobe Photoshop 25.0 (Windows)",
                "IFD0:ModifyDate": "2026:05:10 16:45:00",
            },
        )
        xmp_xml = (
            b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
            b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"'
            b' xmlns:xmp="http://ns.adobe.com/xap/1.0/"'
            b' xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"'
            b' xmlns:stEvt="http://ns.adobe.com/xap/1.0/sType/ResourceEvent#">'
            b'<rdf:Description xmp:CreatorTool="Adobe Photoshop 25.0 (Windows)"'
            b' xmpMM:DocumentID="xmp.did:abc-123"'
            b' xmpMM:OriginalDocumentID="xmp.did:orig-999">'
            b"<xmpMM:History><rdf:Seq>"
            b'<rdf:li stEvt:action="created" stEvt:when="2026-05-10T14:30:00Z"'
            b' stEvt:softwareAgent="Apple iPhone 15 Pro (iOS 18.2)"/>'
            b'<rdf:li stEvt:action="converted" stEvt:when="2026-05-10T16:12:00Z"'
            b' stEvt:softwareAgent="Adobe Photoshop 25.0 (Windows)"'
            b' stEvt:parameters="from image/heic to image/jpeg"/>'
            b'<rdf:li stEvt:action="saved" stEvt:when="2026-05-10T16:45:00Z"'
            b' stEvt:softwareAgent="Adobe Illustrator 28.0 (Windows)"'
            b' stEvt:changed="/"/>'
            b"</rdf:Seq></xmpMM:History>"
            b"</rdf:Description></rdf:RDF></x:xmpmeta>"
        )
        _inject_jpeg_segment(src, 0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + xmp_xml)
        _inject_jpeg_segment(src, 0xED, b"Photoshop 3.0\x008BIM\x04\x04\x00\x00\x00\x00")
        _inject_jpeg_segment(src, 0xEE, b"Adobe\x00\x64\x00\x00\x00\x00\x01")

        ev = ev_repo.import_file(case.id, src)
        orig_sha = ev.sha256

        rep = prov.analyze(ev.id, extract_metadata_if_missing=True)
        assert rep.engine_version == PROVENANCE_ENGINE_VERSION
        assert rep.primary_category == "Mobile camera capture + later software edit"
        assert rep.capture_device == "Apple iPhone 15 Pro"
        assert rep.capture_os_or_platform == "iOS / iPadOS (mobile)"
        assert "Adobe Photoshop" in rep.editing_software
        assert "Adobe Illustrator" in rep.editing_software
        assert rep.document_ids.get("DocumentID") == "xmp.did:abc-123"
        assert rep.document_ids.get("OriginalDocumentID") == "xmp.did:orig-999"

        # Timeline should contain Capture -> 3 XMP steps -> Evidence Imported (5 steps)
        assert len(rep.timeline) == 5
        assert rep.timeline[0].stage == "1. Capture / Creation"
        assert "Apple iPhone 15 Pro" in rep.timeline[0].device_or_tool
        assert "Adobe Photoshop" in rep.timeline[2].device_or_tool
        assert "Windows" in rep.timeline[2].platform_hint
        assert "Adobe Illustrator" in rep.timeline[3].device_or_tool
        assert "Windows" in rep.timeline[3].platform_hint
        assert rep.timeline[-1].stage == "Evidence Imported"

        # Binary findings
        assert rep.binary_findings["container"] == "JPEG"
        assert rep.binary_findings["jpeg_estimated_quality"] is not None
        assert len(rep.binary_findings["adobe_markers"]) >= 2

        # Persisted in DB & audit chain; original file untouched
        loaded = prov.latest_report(ev.id)
        assert loaded is not None
        assert loaded.primary_category == rep.primary_category
        assert len(loaded.timeline) == 5
        assert sha256_file(ev_repo.original_path(ev)) == orig_sha
        events = list_events(db.con, evidence_id=ev.id)
        assert any(e.event_type == "origin_traced" for e in events)
    finally:
        db.close()


def test_dedicated_real_camera_classification(tmp_path, monkeypatch):
    _block_network(monkeypatch)
    exiftool = find_exiftool()
    if exiftool is None:
        pytest.skip("ExifTool not installed")

    db, case, ev_repo, _meta, prov, _reports = _make_services(tmp_path)
    try:
        src = make_exif_jpeg_with_exiftool(
            tmp_path / "canon_r5.jpg",
            exiftool,
            {
                "IFD0:Make": "Canon",
                "IFD0:Model": "Canon EOS R5",
                "ExifIFD:LensModel": "RF24-70mm F2.8 L IS USM",
                "ExifIFD:DateTimeOriginal": "2026:05:11 09:15:00",
            },
        )
        ev = ev_repo.import_file(case.id, src)
        rep = prov.analyze(ev.id, extract_metadata_if_missing=True)
        assert rep.primary_category == "Dedicated / real camera"
        assert rep.capture_device == "Canon EOS R5"
        assert "Dedicated camera" in rep.capture_os_or_platform
        assert rep.editing_software == ()
    finally:
        db.close()


def test_ai_generation_png_chunks_detected(tmp_path, monkeypatch):
    _block_network(monkeypatch)
    db, case, ev_repo, _meta, prov, _reports = _make_services(tmp_path)
    try:
        im = Image.new("RGB", (128, 128), (40, 80, 120))
        info = PngImagePlugin.PngInfo()
        info.add_text("parameters", "a synthetic harbour scene\nSteps: 28, Sampler: Euler a")
        info.add_text("prompt", '{"1": {"class_type": "KSampler"}}')
        info.add_text("workflow", '{"nodes": []}')
        src = tmp_path / "ai_sample.png"
        im.save(src, format="PNG", pnginfo=info)

        ev = ev_repo.import_file(case.id, src)
        rep = prov.analyze(ev.id, extract_metadata_if_missing=False)
        assert rep.primary_category == "AI-generated / synthetic markers detected"
        joined = " ".join(rep.ai_indicators)
        assert "Stable Diffusion" in joined
        assert "ComfyUI" in joined
    finally:
        db.close()


def test_stripped_whatsapp_style_image_honest_verdict(tmp_path, monkeypatch):
    _block_network(monkeypatch)
    db, case, ev_repo, _meta, prov, _reports = _make_services(tmp_path)
    try:
        src = make_valid_jpeg(tmp_path / "IMG-20260510-WA0001.jpg", (240, 180))
        ev = ev_repo.import_file(case.id, src)
        rep = prov.analyze(ev.id, extract_metadata_if_missing=True)
        assert rep.metadata_stripped_likely is True
        assert "stripped" in rep.primary_category.lower()
        assert "whatsapp" in rep.primary_verdict.lower()
        # Filename match must be clearly separated as heuristic clue, NOT extracted fact!
        wa_sigs = [s for s in rep.evidence_signals if s["layer"] == "Filename Heuristic"]
        assert len(wa_sigs) == 1
        assert wa_sigs[0]["classification"] == "Automated heuristic clue"
        # Honest IP disclaimer always present
        assert any("IP addresses" in lim for lim in rep.limitations)
    finally:
        db.close()


def test_embedded_exif_thumbnail_mismatch_detected(tmp_path, monkeypatch):
    _block_network(monkeypatch)
    db, case, ev_repo, _meta, prov, _reports = _make_services(tmp_path)
    try:
        # Main image: wide 2:1 landscape with smooth left-to-right brightening gradient
        main_im = Image.new("RGB", (320, 160), (20, 20, 20))
        d_main = ImageDraw.Draw(main_im)
        for x in range(320):
            v = int(20 + (220 * x) / 319)
            d_main.line([(x, 0), (x, 159)], fill=(v, v, v))
        src = tmp_path / "thumb_mismatch.jpg"
        main_im.save(src, format="JPEG", quality=90)

        # Completely different square 1:1 thumbnail with right-to-left darkening gradient
        thumb_im = Image.new("RGB", (100, 100), (240, 240, 240))
        d_th = ImageDraw.Draw(thumb_im)
        for x in range(100):
            v = int(240 - (220 * x) / 99)
            d_th.line([(x, 0), (x, 99)], fill=(v, v, v))
        tb = io.BytesIO()
        thumb_im.save(tb, format="JPEG", quality=80)
        # Valid minimal TIFF IFD0 (0 entries) followed by mismatched embedded JPEG thumbnail
        _inject_jpeg_segment(
            src,
            0xE1,
            b"Exif\x00\x00II*\x00\x08\x00\x00\x00\x00\x00\x00\x00\x00\x00" + tb.getvalue(),
        )

        ev = ev_repo.import_file(case.id, src)
        rep = prov.analyze(ev.id, extract_metadata_if_missing=False)
        assert rep.thumbnail_check["present"] is True
        assert rep.thumbnail_check["status"] == "mismatch"
        assert any("DISCREPANCY" in w for w in rep.warnings)
    finally:
        db.close()


def test_trailing_appended_data_detected_jpeg_and_png(tmp_path):
    # JPEG with appended ZIP archive after EOI
    jpg = make_valid_jpeg(tmp_path / "with_zip.jpg", (100, 100))
    zip_payload = b"PK\x03\x04" + b"\x14\x00" * 30
    jpg.write_bytes(jpg.read_bytes() + zip_payload)
    bf_jpg = inspect_binary_structure(jpg)
    assert bf_jpg["trailing_bytes"] == len(zip_payload)
    assert "ZIP" in bf_jpg["trailing_signature"]

    # JPEG with appended MP4 motion-photo payload after EOI
    jpg2 = make_valid_jpeg(tmp_path / "with_mp4.jpg", (100, 100))
    mp4_payload = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 40
    jpg2.write_bytes(jpg2.read_bytes() + mp4_payload)
    bf_mp4 = inspect_binary_structure(jpg2)
    assert bf_mp4["trailing_bytes"] == len(mp4_payload)
    assert "MP4" in bf_mp4["trailing_signature"]

    # PNG with appended PDF after IEND
    png = tmp_path / "with_pdf.png"
    Image.new("RGB", (64, 64), (10, 20, 30)).save(png, format="PNG")
    pdf_payload = b"%PDF-1.7\n" + b"0" * 40
    png.write_bytes(png.read_bytes() + pdf_payload)
    bf_png = inspect_binary_structure(png)
    assert bf_png["trailing_bytes"] == len(pdf_payload)
    assert "PDF" in bf_png["trailing_signature"]


def test_ela_and_stripped_copy_preserve_original(tmp_path):
    src = make_valid_jpeg(tmp_path / "ela_src.jpg", (160, 120))
    xmp_xml = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/></x:xmpmeta>'
    _inject_jpeg_segment(src, 0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + xmp_xml)
    orig_sha = sha256_file(src)

    ela = compute_ela(src, quality=90, scale=15.0)
    assert ela.image.size == (160, 120)
    assert 0.0 <= ela.mean_diff <= 255.0
    assert 0 <= ela.max_diff <= 255
    assert sha256_file(src) == orig_sha

    stripped_path, stripped_sha = save_stripped_copy(src, tmp_path / "clean.jpg")
    assert stripped_path.is_file()
    assert len(stripped_sha) == 64
    bf = inspect_binary_structure(stripped_path)
    assert bf["raw_xmp_packets"] == []
    assert "APP1:EXIF" not in bf["segments"]
    assert sha256_file(src) == orig_sha


def test_parse_xmp_history_handles_malformed_xml_safely():
    hist, ids = parse_xmp_history_packets(["<not valid xml", "<x:xmpmeta></x:xmpmeta>"])
    assert hist == []
    assert ids == {}


def test_reports_include_origin_trace_in_json_and_pdf(tmp_path, monkeypatch):
    _block_network(monkeypatch)
    exiftool = find_exiftool()
    if exiftool is None:
        pytest.skip("ExifTool not installed")

    db, case, ev_repo, _meta, prov, reports = _make_services(tmp_path)
    try:
        src = make_exif_jpeg_with_exiftool(
            tmp_path / "rep_test.jpg",
            exiftool,
            {
                "IFD0:Make": "Apple",
                "IFD0:Model": "iPhone 14",
                "IFD0:Software": "Adobe Photoshop 25.0 (Windows)",
                "ExifIFD:DateTimeOriginal": "2026:05:10 11:00:00",
            },
        )
        ev = ev_repo.import_file(case.id, src)
        prov.analyze(ev.id, extract_metadata_if_missing=True)

        json_exp = reports.generate_json(case.id)
        data = json.loads(reports.resolve_path(json_exp).read_text(encoding="utf-8"))
        ot = data["evidence"][0]["origin_trace"]
        assert ot["primary_category"] == "Mobile camera capture + later software edit"
        assert len(ot["timeline"]) >= 2
        assert any(t["name"] == "ImageTrace Origin & Trace" for t in data["tools"])

        pdf_exp = reports.generate_pdf(case.id)
        pdf_bytes = reports.resolve_path(pdf_exp).read_bytes()
        assert pdf_bytes.startswith(b"%PDF-")
        assert len(pdf_bytes) > 2000
    finally:
        db.close()
