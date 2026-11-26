"""Advanced offline Origin & Trace forensic provenance engine (v0.2.0).

Inspects six independent local evidence layers without modifying originals or making network calls:
1. Device, camera, mobile, graphics-software, screenshot, AI-tool & stripped-file classification.
2. Deep binary container forensics (JPEG APP0-APP14/DQT/SOF/COM/Photoshop IRB/Adobe/C2PA JUMBF,
   PNG chunk inventory, JPEG quality estimate, trailing data after EOI/IEND).
3. Step-by-step embedded edit & transfer timeline (EXIF capture tags, raw XMP ``xmpMM:History``
   XML + ExifTool sidecar arrays, ``DocumentID``/``DerivedFrom``, C2PA Content Credentials, OS
   detection across Windows/macOS/iOS/Android/Linux, and ImageTrace import).
4. Embedded EXIF thumbnail vs. main image aspect-ratio and 64-bit dHash consistency check.
5. On-demand Error Level Analysis (ELA) diagnostic preview (pure Pillow, strictly in memory).
6. Honest limitations & opt-in manual public reverse-image search helpers (zero auto-uploads,
   never obtains IP addresses or identifies private individuals).
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import re
import struct
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageEnhance, ImageOps, UnidentifiedImageError

from imagetrace.audit import append_event
from imagetrace.db import Database
from imagetrace.evidence import Evidence, EvidenceRepository
from imagetrace.forensics import (
    ForensicsError,
    dqt_fingerprint,
    parse_c2pa_claims,
    save_embedded_thumbnail,
    save_trailing_payload,
)
from imagetrace.hashing import sha256_file
from imagetrace.image_analysis import _PIL_LOCK, compute_dhash, dhash_distance
from imagetrace.metadata import AnalysisRun, MetadataRecord, MetadataService
from imagetrace.timefmt import utc_now_iso

log = logging.getLogger(__name__)

PROVENANCE_MODULE_NAME = "origin_trace"
PROVENANCE_ENGINE_VERSION = "ImageTrace-OriginTrace/3.0"

PROVENANCE_BANNER = (
    "Origin & Trace analyzes embedded metadata, XMP edit history, binary file markers (JPEG/PNG/"
    "Adobe/C2PA), and embedded thumbnails locally offline. Image files do NOT store IP addresses "
    "or forwarding chains across messaging apps; missing or stripped metadata is reported honestly."
)

ELA_DISCLAIMER = (
    "Error Level Analysis (ELA) visualizes JPEG recompression error differences (Q=90). "
    "High-contrast edges, sharp text, and uniformly re-saved files also brighten — ELA is an "
    "exploratory visual aid, NOT proof of tampering."
)

REVERSE_SEARCH_DISCLOSURE = (
    "External Reverse Image Search (Manual Only)\n\n"
    "ImageTrace NEVER uploads your evidence file automatically.\n"
    "Clicking Yes will open the selected public search website in your default web browser.\n\n"
    "Before manually uploading any image to an external website:\n"
    "  • Confirm you are authorized to share the image publicly.\n"
    "  • Never upload sensitive, private, or confidential evidence to public services.\n"
    "  • Consider using 'Save stripped copy…' so embedded EXIF/XMP tags are removed first.\n\n"
    "Open the external search website in your browser?"
)

REVERSE_SEARCH_ENGINES: tuple[tuple[str, str], ...] = (
    ("Google Lens", "https://lens.google.com/"),
    ("TinEye", "https://tineye.com/"),
    ("Bing Visual Search", "https://www.bing.com/visualsearch"),
    ("Yandex Images", "https://yandex.com/images/"),
)

# --- Device & software signature tables ---------------------------------------------------
_MOBILE_MAKERS = (
    "apple",
    "samsung",
    "google",
    "xiaomi",
    "redmi",
    "poco",
    "huawei",
    "honor",
    "oneplus",
    "oppo",
    "vivo",
    "realme",
    "motorola",
    "nothing",
    "nokia",
    "hmd",
    "asus",
    "tecno",
    "infinix",
    "itel",
    "meizu",
    "zte",
    "nubia",
    "fairphone",
)
_MOBILE_MODEL_HINTS = (
    "iphone",
    "ipad",
    "pixel",
    "galaxy",
    "sm-",
    "sc-",
    "redmi",
    "poco",
    "mi ",
    "xperia",
    "moto ",
    "oneplus",
    "cph",
    "v20",
    "v21",
    "v22",
    "v23",
    "rmx",
    "a0",
    "a1",
    "a2",
    "a3",
    "a5",
    "s2",
    "s9",
)
_DEDICATED_CAMERA_MAKERS = (
    "canon",
    "nikon",
    "fujifilm",
    "olympus",
    "om digital",
    "panasonic",
    "leica",
    "pentax",
    "ricoh",
    "hasselblad",
    "phase one",
    "gopro",
    "dji",
    "insta360",
    "kodak",
    "sigma",
    "minolta",
    "konica",
    "casio",
    "mamiya",
    "polaroid",
    "blackmagic",
    "red ",
)
_DEDICATED_MODEL_HINTS = (
    "eos",
    "powershot",
    "ilce",
    "ilca",
    "dsc-",
    "zv-",
    "lumix",
    "coolpix",
    "nikon z",
    "nikon d",
    "hero",
    "mavic",
    "fc3",
    "fc6",
    "fc7",
    "x-t",
    "x-pro",
    "x100",
    "gfx",
    "e-m",
    "om-1",
    "alpha",
)
_GRAPHICS_SOFTWARE: tuple[tuple[str, str], ...] = (
    ("adobe photoshop", "Adobe Photoshop"),
    ("photoshop", "Adobe Photoshop"),
    ("adobe illustrator", "Adobe Illustrator"),
    ("illustrator", "Adobe Illustrator"),
    ("adobe photoshop lightroom", "Adobe Lightroom"),
    ("lightroom", "Adobe Lightroom"),
    ("adobe premiere", "Adobe Premiere"),
    ("adobe after effects", "Adobe After Effects"),
    ("adobe indesign", "Adobe InDesign"),
    ("adobe firefly", "Adobe Firefly"),
    ("adobe", "Adobe Creative Software"),
    ("gimp", "GIMP"),
    ("canva", "Canva"),
    ("figma", "Figma"),
    ("affinity photo", "Affinity Photo"),
    ("affinity designer", "Affinity Designer"),
    ("affinity", "Affinity"),
    ("coreldraw", "CorelDRAW"),
    ("corel", "Corel Graphics"),
    ("procreate", "Procreate"),
    ("inkscape", "Inkscape"),
    ("blender", "Blender"),
    ("paint.net", "Paint.NET"),
    ("paintshop", "PaintShop Pro"),
    ("snapseed", "Snapseed"),
    ("picsart", "PicsArt"),
    ("capcut", "CapCut"),
    ("vsco", "VSCO"),
    ("pixelmator", "Pixelmator"),
    ("capture one", "Capture One"),
    ("darktable", "darktable"),
    ("rawtherapee", "RawTherapee"),
    ("acorn", "Acorn"),
    ("krita", "Krita"),
    ("clip studio", "Clip Studio Paint"),
    ("sketch", "Sketch"),
    ("imagemagick", "ImageMagick"),
    ("pillow", "Python Pillow"),
    ("microsoft paint", "Microsoft Paint"),
    ("mspaint", "Microsoft Paint"),
    ("snipping tool", "Windows Snipping Tool"),
    ("sharex", "ShareX"),
    ("greenshot", "Greenshot"),
    ("flameshot", "Flameshot"),
    ("apple photos", "Apple Photos"),
    ("preview", "macOS Preview"),
    ("windows photo", "Windows Photos"),
)
_AI_TOOL_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("midjourney", "Midjourney"),
    ("dall-e", "OpenAI DALL-E"),
    ("chatgpt", "OpenAI / ChatGPT"),
    ("openai", "OpenAI"),
    ("stable diffusion", "Stable Diffusion"),
    ("automatic1111", "Stable Diffusion (Automatic1111)"),
    ("comfyui", "ComfyUI"),
    ("invokeai", "InvokeAI"),
    ("novelai", "NovelAI"),
    ("adobe firefly", "Adobe Firefly"),
    ("firefly", "Adobe Firefly"),
    ("imagen", "Google Imagen"),
    ("flux", "FLUX"),
    ("trainedalgorithmicmedia", "C2PA Trained Algorithmic Media"),
    ("compositewithtrainedalgorithmicmedia", "C2PA AI Composite"),
)
_SCREEN_RESOLUTIONS = frozenset(
    {
        (1920, 1080),
        (2560, 1440),
        (3840, 2160),
        (1366, 768),
        (1440, 900),
        (1536, 864),
        (1280, 720),
        (1280, 800),
        (2560, 1600),
        (2880, 1800),
        (3024, 1964),
        (3456, 2234),
        (1170, 2532),
        (1179, 2556),
        (1284, 2778),
        (1290, 2796),
        (1080, 2400),
        (1080, 2340),
        (1440, 3200),
        (1440, 3120),
    }
)
_WHATSAPP_NAME_RE = re.compile(r"^(?:IMG|WA)-\d{8}-WA\d{4,}", re.IGNORECASE)
_SCREENSHOT_NAME_RE = re.compile(r"^(?:screenshot|screen[\s_-]?shot|capture|snip)", re.IGNORECASE)
_CONTROL_STRIP = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Standard JPEG Annex K luminance quantization table (used to estimate quality factor)
_STD_LUMA_QT = (
    16,
    11,
    10,
    16,
    24,
    40,
    51,
    61,
    12,
    12,
    14,
    19,
    26,
    58,
    60,
    55,
    14,
    13,
    16,
    24,
    40,
    57,
    69,
    56,
    14,
    17,
    22,
    29,
    51,
    87,
    80,
    62,
    18,
    22,
    37,
    56,
    68,
    109,
    103,
    77,
    24,
    35,
    55,
    64,
    81,
    104,
    113,
    92,
    49,
    64,
    78,
    87,
    103,
    121,
    120,
    101,
    72,
    92,
    95,
    98,
    112,
    100,
    103,
    99,
)


class ProvenanceError(Exception):
    """Origin & Trace analysis failed (safe to show to user)."""


@dataclass(frozen=True)
class TraceStep:
    step_no: int
    stage: str  # Capture / Creation, Edit / Software, Conversion / Export, File / Import
    timestamp: str
    device_or_tool: str
    platform_hint: str
    action: str
    evidence_source: str
    classification: str  # Extracted fact | Automated heuristic clue

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_no": self.step_no,
            "stage": self.stage,
            "timestamp": self.timestamp,
            "device_or_tool": self.device_or_tool,
            "platform_hint": self.platform_hint,
            "action": self.action,
            "evidence_source": self.evidence_source,
            "classification": self.classification,
        }


@dataclass(frozen=True)
class ElaResult:
    image: Image.Image
    quality: int
    scale: float
    mean_diff: float
    max_diff: int
    high_energy_pct: float


@dataclass(frozen=True)
class ProvenanceReport:
    evidence_id: str
    engine_version: str
    analyzed_at: str
    primary_category: str
    primary_verdict: str
    confidence_note: str
    capture_device: str
    capture_os_or_platform: str
    editing_software: tuple[str, ...]
    ai_indicators: tuple[str, ...]
    has_c2pa: bool
    metadata_stripped_likely: bool
    binary_findings: dict[str, Any]
    thumbnail_check: dict[str, Any]
    document_ids: dict[str, str]
    timeline: tuple[TraceStep, ...]
    evidence_signals: tuple[dict[str, str], ...]
    warnings: tuple[str, ...]
    limitations: tuple[str, ...]
    hardware_ids: dict[str, str] = ()  # type: ignore[assignment]
    c2pa_claims: dict[str, Any] = ()  # type: ignore[assignment]
    dqt_info: dict[str, str] = ()  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not isinstance(self.hardware_ids, dict):
            object.__setattr__(self, "hardware_ids", {})
        if not isinstance(self.c2pa_claims, dict):
            object.__setattr__(self, "c2pa_claims", {})
        if not isinstance(self.dqt_info, dict):
            object.__setattr__(self, "dqt_info", {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "engine_version": self.engine_version,
            "analyzed_at": self.analyzed_at,
            "primary_category": self.primary_category,
            "primary_verdict": self.primary_verdict,
            "confidence_note": self.confidence_note,
            "capture_device": self.capture_device,
            "capture_os_or_platform": self.capture_os_or_platform,
            "editing_software": list(self.editing_software),
            "ai_indicators": list(self.ai_indicators),
            "has_c2pa": self.has_c2pa,
            "metadata_stripped_likely": self.metadata_stripped_likely,
            "binary_findings": self.binary_findings,
            "thumbnail_check": self.thumbnail_check,
            "document_ids": self.document_ids,
            "hardware_ids": self.hardware_ids,
            "c2pa_claims": self.c2pa_claims,
            "dqt_info": self.dqt_info,
            "timeline": [s.to_dict() for s in self.timeline],
            "evidence_signals": list(self.evidence_signals),
            "warnings": list(self.warnings),
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProvenanceReport:
        steps = tuple(
            TraceStep(
                step_no=int(s.get("step_no", i + 1)),
                stage=str(s.get("stage", "")),
                timestamp=str(s.get("timestamp", "")),
                device_or_tool=str(s.get("device_or_tool", "")),
                platform_hint=str(s.get("platform_hint", "")),
                action=str(s.get("action", "")),
                evidence_source=str(s.get("evidence_source", "")),
                classification=str(s.get("classification", "Extracted fact")),
            )
            for i, s in enumerate(data.get("timeline") or [])
            if isinstance(s, dict)
        )
        return cls(
            evidence_id=str(data.get("evidence_id", "")),
            engine_version=str(data.get("engine_version", PROVENANCE_ENGINE_VERSION)),
            analyzed_at=str(data.get("analyzed_at", "")),
            primary_category=str(data.get("primary_category", "Unknown / inconclusive")),
            primary_verdict=str(data.get("primary_verdict", "")),
            confidence_note=str(data.get("confidence_note", "")),
            capture_device=str(data.get("capture_device", "Not recorded in file")),
            capture_os_or_platform=str(data.get("capture_os_or_platform", "Unknown")),
            editing_software=tuple(str(x) for x in (data.get("editing_software") or [])),
            ai_indicators=tuple(str(x) for x in (data.get("ai_indicators") or [])),
            has_c2pa=bool(data.get("has_c2pa", False)),
            metadata_stripped_likely=bool(data.get("metadata_stripped_likely", False)),
            binary_findings=dict(data.get("binary_findings") or {}),
            thumbnail_check=dict(data.get("thumbnail_check") or {}),
            document_ids=dict(data.get("document_ids") or {}),
            hardware_ids=dict(data.get("hardware_ids") or {}),
            c2pa_claims=dict(data.get("c2pa_claims") or {}),
            dqt_info=dict(data.get("dqt_info") or {}),
            timeline=steps,
            evidence_signals=tuple(
                dict(s) for s in (data.get("evidence_signals") or []) if isinstance(s, dict)
            ),
            warnings=tuple(str(w) for w in (data.get("warnings") or [])),
            limitations=tuple(str(x) for x in (data.get("limitations") or [])),
        )


def _clean(val: Any) -> str:
    s = "" if val is None else str(val)
    return _CONTROL_STRIP.sub("", s.replace("\r\n", "\n").replace("\r", "\n")).strip()


def _detect_os_hint(text: str) -> str:
    low = text.lower()
    if "(windows)" in low or "windows" in low or "win32" in low or "win64" in low:
        return "Windows (computer)"
    if "(macintosh)" in low or "macos" in low or "mac os" in low or "os x" in low:
        return "macOS (computer)"
    if "iphone" in low or "ipad" in low or "ios" in low:
        return "iOS / iPadOS (mobile)"
    if "android" in low:
        return "Android (mobile)"
    if "linux" in low or "ubuntu" in low or "fedora" in low:
        return "Linux (computer)"
    return ""


def _detect_software_names(text: str) -> list[str]:
    low = text.lower()
    found: list[str] = []
    for needle, label in _GRAPHICS_SOFTWARE:
        if needle in low and label not in found:
            # Avoid generic "Adobe Creative Software" if a specific Adobe app already matched
            if label == "Adobe Creative Software" and any(x.startswith("Adobe ") for x in found):
                continue
            found.append(label)
    return found


def _detect_ai_names(text: str) -> list[str]:
    low = text.lower()
    found: list[str] = []
    for needle, label in _AI_TOOL_KEYWORDS:
        if needle in low and label not in found:
            found.append(label)
    return found


def _estimate_jpeg_quality(qtable: list[int]) -> int | None:
    """Estimate IJG-equivalent JPEG quality (1-100) from a 64-entry luminance DQT table."""
    if len(qtable) != 64:
        return None
    if all(v <= 1 for v in qtable):
        return 100
    ratios = [qtable[i] / _STD_LUMA_QT[i] for i in range(64) if _STD_LUMA_QT[i] > 0]
    if not ratios:
        return None
    mean_scale = (sum(ratios) / len(ratios)) * 100.0
    if mean_scale <= 1.0:
        return 100
    q = round((200.0 - mean_scale) / 2.0) if mean_scale < 100.0 else round(5000.0 / mean_scale)
    return max(1, min(100, int(q)))


def _identify_trailing_signature(tail: bytes) -> str:
    if not tail:
        return ""
    if b"ftyp" in tail[:32]:
        return "Embedded MP4/HEVC video (e.g. Android Motion Photo or Live Photo)"
    if tail.startswith(b"PK\x03\x04"):
        return "ZIP archive signature appended after image end!"
    if tail.startswith(b"%PDF-"):
        return "PDF document appended after image end!"
    if b"<script" in tail.lower() or b"<html" in tail.lower():
        return "HTML/script content appended after image end (polyglot)!"
    if tail.startswith(b"MZ"):
        return "Windows PE executable header appended after image end!"
    return f"Unidentified binary data ({len(tail):,} bytes)"


# --- Layer 2: Deep Binary Container Inspection --------------------------------------------
def inspect_binary_structure(path: Path) -> dict[str, Any]:
    """Read-only structural analysis of JPEG segments or PNG chunks + XMP/C2PA/trailing data."""
    out: dict[str, Any] = {
        "container": "other",
        "segments": [],
        "adobe_markers": [],
        "comments": [],
        "c2pa_detected": False,
        "jpeg_progressive": None,
        "jpeg_subsampling": "",
        "jpeg_estimated_quality": None,
        "dqt_luma": None,
        "dqt_chroma": None,
        "c2pa_claims": {},
        "png_text_chunks": {},
        "trailing_bytes": 0,
        "trailing_signature": "",
        "trailing_is_mp4": False,
        "raw_xmp_packets": [],
        "embedded_exif_thumb_bytes": None,
    }
    try:
        raw = path.read_bytes()
    except OSError:
        return out

    # Scan for embedded XMP packet(s) anywhere in file bytes (works for JPEG, PNG, WebP, TIFF)
    start = 0
    while len(out["raw_xmp_packets"]) < 3:
        idx = raw.find(b"<x:xmpmeta", start)
        if idx < 0:
            idx = raw.find(b"<rdf:RDF", start)
            if idx < 0:
                break
            end_tag = b"</rdf:RDF>"
        else:
            end_tag = b"</x:xmpmeta>"
        end = raw.find(end_tag, idx)
        if end < 0 or (end - idx) > 2_000_000:
            break
        packet = raw[idx : end + len(end_tag)].decode("utf-8", errors="replace")
        out["raw_xmp_packets"].append(packet)
        start = end + len(end_tag)

    # Check binary C2PA JUMBF marker signature and parse readable claims offline
    if b"c2pa" in raw and (b"jumb" in raw or b"c2pa.actions" in raw or b"urn:uuid" in raw):
        out["c2pa_detected"] = True
        out["c2pa_claims"] = parse_c2pa_claims(raw)

    # --- JPEG parser ---
    if raw.startswith(b"\xff\xd8"):
        out["container"] = "JPEG"
        pos = 2
        n = len(raw)
        eoi_end: int | None = None
        while pos + 2 <= n:
            if raw[pos] != 0xFF:
                break
            while pos < n and raw[pos] == 0xFF:
                pos += 1
            if pos >= n:
                break
            marker = raw[pos]
            pos += 1
            if marker == 0xD9:  # EOI
                eoi_end = pos
                break
            if marker in (0x01, 0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7):
                continue
            if pos + 2 > n:
                break
            seg_len = struct.unpack(">H", raw[pos : pos + 2])[0]
            if seg_len < 2 or pos + seg_len > n:
                break
            seg_data = raw[pos + 2 : pos + seg_len]
            pos += seg_len

            if marker == 0xE0 and seg_data.startswith(b"JFIF\x00"):
                out["segments"].append("APP0:JFIF")
            elif marker == 0xE1:
                if seg_data.startswith(b"Exif\x00\x00"):
                    out["segments"].append("APP1:EXIF")
                    # Locate embedded EXIF JPEG thumbnail inside APP1 (SOI..EOI after offset 12)
                    t_start = seg_data.find(b"\xff\xd8\xff", 12)
                    if t_start > 0:
                        t_end = seg_data.find(b"\xff\xd9", t_start + 3)
                        if t_end > t_start:
                            out["embedded_exif_thumb_bytes"] = seg_data[t_start : t_end + 2]
                elif b"http://ns.adobe.com/xap/1.0/" in seg_data[:40]:
                    out["segments"].append("APP1:XMP")
                else:
                    out["segments"].append("APP1:Other")
            elif marker == 0xE2:
                if seg_data.startswith(b"ICC_PROFILE"):
                    if "APP2:ICC_PROFILE" not in out["segments"]:
                        out["segments"].append("APP2:ICC_PROFILE")
                elif seg_data.startswith(b"MPF\x00"):
                    out["segments"].append("APP2:MPF")
            elif marker == 0xEB:
                out["segments"].append("APP11:JUMBF")
                if b"c2pa" in seg_data or b"jumb" in seg_data:
                    out["c2pa_detected"] = True
            elif marker == 0xEC and seg_data.startswith(b"Ducky"):
                out["segments"].append("APP12:Ducky (Adobe Save-for-Web)")
                out["adobe_markers"].append("JPEG APP12 Ducky (Adobe Save-for-Web)")
            elif marker == 0xED and seg_data.startswith(b"Photoshop 3.0"):
                out["segments"].append("APP13:Photoshop 3.0 IRB")
                out["adobe_markers"].append("JPEG APP13 Photoshop 3.0 Image Resource Block")
            elif marker == 0xEE and seg_data.startswith(b"Adobe"):
                out["segments"].append("APP14:Adobe")
                out["adobe_markers"].append("JPEG APP14 Adobe DCT marker")
            elif marker == 0xFE:
                c_txt = _clean(seg_data.decode("utf-8", errors="replace"))[:200]
                if c_txt:
                    out["comments"].append(c_txt)
                    out["segments"].append("COM")
            elif marker == 0xDB:  # DQT
                p_q = 0
                while p_q < len(seg_data):
                    pq_tq = seg_data[p_q]
                    precision = (pq_tq >> 4) & 0x0F
                    table_id = pq_tq & 0x0F
                    p_q += 1
                    entry_len = 128 if precision else 64
                    if p_q + entry_len > len(seg_data):
                        break
                    vals = (
                        list(struct.unpack(">64H", seg_data[p_q : p_q + 128]))
                        if precision
                        else list(seg_data[p_q : p_q + 64])
                    )
                    if table_id == 0 and out["jpeg_estimated_quality"] is None:
                        out["dqt_luma"] = vals
                        out["jpeg_estimated_quality"] = _estimate_jpeg_quality(vals)
                    elif table_id == 1 and out["dqt_chroma"] is None:
                        out["dqt_chroma"] = vals
                    p_q += entry_len
            elif marker in (0xC0, 0xC2):  # SOF0 baseline / SOF2 progressive
                out["jpeg_progressive"] = marker == 0xC2
                out["segments"].append("SOF2:Progressive" if marker == 0xC2 else "SOF0:Baseline")
                if len(seg_data) >= 6:
                    n_comp = seg_data[5]
                    if n_comp == 3 and len(seg_data) >= 6 + 9:
                        hv0 = seg_data[7]
                        h0, v0 = (hv0 >> 4) & 0x0F, hv0 & 0x0F
                        if (h0, v0) == (2, 2):
                            out["jpeg_subsampling"] = "4:2:0"
                        elif (h0, v0) == (2, 1):
                            out["jpeg_subsampling"] = "4:2:2"
                        elif (h0, v0) == (1, 1):
                            out["jpeg_subsampling"] = "4:4:4"
            elif marker == 0xDA:  # SOS -> scan entropy stream to first real EOI (0xFFD9)
                i = pos
                while i + 1 < n:
                    ff = raw.find(b"\xff", i)
                    if ff < 0 or ff + 1 >= n:
                        break
                    nxt = raw[ff + 1]
                    if nxt == 0x00 or (0xD0 <= nxt <= 0xD7):
                        i = ff + 2
                        continue
                    if nxt == 0xFF:
                        i = ff + 1
                        continue
                    if nxt == 0xD9:
                        eoi_end = ff + 2
                        break
                    # Progressive JPEGs can have additional DHT/SOS scans before EOI
                    if ff + 4 <= n:
                        inner_len = struct.unpack(">H", raw[ff + 2 : ff + 4])[0]
                        i = ff + 2 + max(inner_len, 2)
                    else:
                        break
                if eoi_end is None:
                    eoi_idx = raw.rfind(b"\xff\xd9")
                    if eoi_idx >= pos:
                        eoi_end = eoi_idx + 2
                break

        if eoi_end is not None and eoi_end < n:
            tail = raw[eoi_end:]
            if len(tail) > 4:
                out["trailing_bytes"] = len(tail)
                out["trailing_signature"] = _identify_trailing_signature(tail)
                out["trailing_is_mp4"] = b"ftyp" in tail[:32]

    # --- PNG parser ---
    elif raw.startswith(b"\x89PNG\r\n\x1a\n"):
        out["container"] = "PNG"
        pos = 8
        n = len(raw)
        iend_end: int | None = None
        while pos + 12 <= n:
            clen = struct.unpack(">I", raw[pos : pos + 4])[0]
            ctype = raw[pos + 4 : pos + 8].decode("latin-1", errors="replace")
            data_start = pos + 8
            data_end = data_start + clen
            if data_end + 4 > n:
                break
            cdata = raw[data_start:data_end]
            if ctype not in ("IDAT",) and ctype not in out["segments"]:
                out["segments"].append(ctype)
            if ctype == "tEXt" and b"\x00" in cdata:
                k_b, v_b = cdata.split(b"\x00", 1)
                key = _clean(k_b.decode("latin-1", errors="replace"))[:60]
                val = _clean(v_b.decode("latin-1", errors="replace"))[:400]
                if key:
                    out["png_text_chunks"][key] = val
            elif ctype == "iTXt" and b"\x00" in cdata:
                parts = cdata.split(b"\x00", 5)
                if len(parts) >= 2:
                    key = _clean(parts[0].decode("utf-8", errors="replace"))[:60]
                    val = _clean(parts[-1].decode("utf-8", errors="replace"))[:400]
                    if key:
                        out["png_text_chunks"][key] = val
            pos = data_end + 4
            if ctype == "IEND":
                iend_end = pos
                break
        if iend_end is not None and iend_end < n:
            tail = raw[iend_end:]
            if len(tail) > 4:
                out["trailing_bytes"] = len(tail)
                out["trailing_signature"] = _identify_trailing_signature(tail)

    return out


# --- Layer 3: XMP xmpMM:History XML parser ------------------------------------------------
def parse_xmp_history_packets(packets: list[str]) -> tuple[list[dict[str, str]], dict[str, str]]:
    """Parse XMP ``xmpMM:History`` edit steps & ``DocumentID``/``DerivedFrom`` from XMP XML."""
    history: list[dict[str, str]] = []
    doc_ids: dict[str, str] = {}

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    for pkt in packets:
        try:
            root = ET.fromstring(pkt)  # noqa: S314 (stdlib ElementTree does not fetch external DTDs)
        except ET.ParseError:
            continue

        for elem in root.iter():
            lname = _local(elem.tag)
            # Extract document lineage IDs (from attributes or child text)
            for attr_k, attr_v in elem.attrib.items():
                ak = _local(attr_k)
                if ak in ("DocumentID", "OriginalDocumentID", "InstanceID") and attr_v.strip():
                    doc_ids.setdefault(ak, _clean(attr_v))
                elif (
                    ak in ("documentID", "originalDocumentID", "instanceID")
                    and lname == "DerivedFrom"
                ):
                    doc_ids.setdefault(f"DerivedFrom.{ak}", _clean(attr_v))
            if (
                lname in ("DocumentID", "OriginalDocumentID", "InstanceID")
                and (elem.text or "").strip()
            ):
                doc_ids.setdefault(lname, _clean(elem.text))

            # Extract xmpMM:History <rdf:Seq><rdf:li .../></rdf:Seq>
            if lname == "History":
                for li in elem.iter():
                    if _local(li.tag) != "li":
                        continue
                    entry: dict[str, str] = {}
                    for k, v in li.attrib.items():
                        lk = _local(k)
                        if lk in (
                            "action",
                            "when",
                            "softwareAgent",
                            "parameters",
                            "changed",
                            "instanceID",
                        ):
                            entry[lk] = _clean(v)
                    for child in li:
                        ck = _local(child.tag)
                        if (
                            ck
                            in (
                                "action",
                                "when",
                                "softwareAgent",
                                "parameters",
                                "changed",
                                "instanceID",
                            )
                            and (child.text or "").strip()
                        ):
                            entry[ck] = _clean(child.text)
                    if entry:
                        history.append(entry)
    return history, doc_ids


# --- Layer 4: Embedded EXIF Thumbnail Consistency Check -----------------------------------
def compare_embedded_thumbnail(
    orig_path: Path, thumb_bytes: bytes | None, main_ev: Evidence
) -> dict[str, Any]:
    if not thumb_bytes:
        return {
            "present": False,
            "status": "none",
            "message": "No embedded EXIF preview thumbnail found in file.",
        }
    with _PIL_LOCK:
        try:
            with Image.open(io.BytesIO(thumb_bytes)) as t_im:
                t_im.load()
                t_w, t_h = t_im.size
                t_hash = compute_dhash(t_im)
            with Image.open(orig_path) as m_im:
                m_im = ImageOps.exif_transpose(m_im)
                m_w, m_h = m_im.size
                m_hash = compute_dhash(m_im)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            return {
                "present": True,
                "status": "warning",
                "message": (
                    f"Embedded EXIF thumbnail present ({len(thumb_bytes)} bytes) "
                    f"but unreadable: {exc}"
                ),
            }

    dist = dhash_distance(t_hash, m_hash)
    t_ratio = t_w / max(t_h, 1)
    m_ratio = m_w / max(m_h, 1)
    # Check rotated orientation too (thumbnails sometimes remain unrotated)
    ratio_diff = min(abs(t_ratio - m_ratio), abs((1.0 / max(t_ratio, 1e-6)) - m_ratio))
    mismatch = dist >= 18 or ratio_diff > 0.25
    if mismatch:
        msg = (
            f"DISCREPANCY: Embedded EXIF thumbnail ({t_w}x{t_h}) differs significantly from the "
            f"main image ({m_w}x{m_h}; dHash distance {dist}/64). This can occur if the main "
            "image was cropped or edited without regenerating the EXIF thumbnail."
        )
        status = "mismatch"
    else:
        msg = (
            f"Embedded EXIF thumbnail ({t_w}x{t_h}) is visually consistent with the main image "
            f"({m_w}x{m_h}; dHash distance {dist}/64)."
        )
        status = "consistent"
    return {
        "present": True,
        "status": status,
        "thumb_size": f"{t_w}x{t_h}",
        "main_size": f"{m_w}x{m_h}",
        "dhash_distance": dist,
        "message": msg,
    }


# --- Layer 5: On-Demand Error Level Analysis (ELA) ----------------------------------------
def compute_ela(orig_path: Path, *, quality: int = 90, scale: float = 15.0) -> ElaResult:
    """Compute a non-destructive Error Level Analysis preview strictly in memory."""
    with _PIL_LOCK:
        try:
            with Image.open(orig_path) as im:
                rgb = ImageOps.exif_transpose(im).convert("RGB")
                # Cap preview dimension so ELA stays fast & low-memory even on huge photos
                rgb.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                rgb.save(buf, format="JPEG", quality=quality)
                buf.seek(0)
                with Image.open(buf) as recomp:
                    recomp_rgb = recomp.convert("RGB")
                    diff = ImageChops.difference(rgb, recomp_rgb)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ProvenanceError(f"Could not compute ELA preview: {exc}") from exc

    raw_bytes = diff.tobytes()
    total_vals = max(len(raw_bytes), 1)
    max_diff = max(raw_bytes) if raw_bytes else 0
    mean_diff = sum(raw_bytes) / total_vals
    high_energy = sum(1 for v in raw_bytes if v >= 15) / total_vals * 100.0

    # Amplify difference cleanly for visual inspection
    auto_scale = scale if max_diff == 0 else min(scale, 255.0 / max(max_diff, 1))
    effective = max(auto_scale, 1.0)
    vis = ImageEnhance.Brightness(diff).enhance(effective)
    return ElaResult(
        image=vis,
        quality=quality,
        scale=round(effective, 2),
        mean_diff=round(mean_diff, 2),
        max_diff=int(max_diff),
        high_energy_pct=round(high_energy, 2),
    )


def save_stripped_copy(orig_path: Path, dest_path: Path) -> tuple[Path, str]:
    """Save a metadata-stripped JPEG viewing copy (never modifies original)."""
    with _PIL_LOCK:
        try:
            with Image.open(orig_path) as im:
                rgb = ImageOps.exif_transpose(im).convert("RGB")
                rgb.save(dest_path, format="JPEG", quality=92, optimize=True)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ProvenanceError(f"Could not create stripped copy: {exc}") from exc
    return dest_path, sha256_file(dest_path)


# --- Core Classification & Timeline Synthesis ---------------------------------------------
def _classify_and_trace(
    ev: Evidence,
    orig_path: Path,
    meta_records: list[MetadataRecord],
    sidecar_data: dict[str, Any] | None,
) -> ProvenanceReport:
    binary = inspect_binary_structure(orig_path)
    thumb_bytes = binary.pop("embedded_exif_thumb_bytes", None)
    xmp_packets = binary.pop("raw_xmp_packets", [])
    dqt_luma = binary.pop("dqt_luma", None)
    dqt_chroma = binary.pop("dqt_chroma", None)
    c2pa_claims = binary.pop("c2pa_claims", None) or {}

    # Build quick lookups of embedded metadata
    emb_by_field: dict[str, str] = {}
    emb_by_full: dict[str, str] = {}
    embedded_count = 0
    has_gps = False
    for r in meta_records:
        if r.origin == "embedded":
            embedded_count += 1
            full_key = f"{r.group_name}:{r.field_name}"
            emb_by_full[full_key] = r.normalized_value
            emb_by_field.setdefault(r.field_name, r.normalized_value)
            if r.group_name == "EXIF:GPS" or (
                r.category == "XMP" and r.field_name.startswith("GPS")
            ):
                has_gps = True

    make = _clean(emb_by_field.get("Make", ""))
    model = _clean(emb_by_field.get("Model", ""))
    lens = _clean(emb_by_field.get("LensModel") or emb_by_field.get("LensInfo") or "")
    software_raw = _clean(emb_by_field.get("Software", ""))
    creator_tool = _clean(emb_by_field.get("CreatorTool", ""))
    user_comment = _clean(emb_by_field.get("UserComment", ""))
    dt_orig = _clean(emb_by_field.get("DateTimeOriginal", ""))
    dt_create = _clean(emb_by_field.get("CreateDate", ""))
    dt_mod = _clean(emb_by_field.get("ModifyDate", ""))
    icc = _clean(ev.icc_profile_info or emb_by_field.get("ProfileDescription", ""))

    # Extract MakerNotes / hardware identity fields (camera serial, shutter count, owner)
    hardware_ids: dict[str, str] = {}
    for hw_key in (
        "SerialNumber",
        "InternalSerialNumber",
        "BodySerialNumber",
        "LensSerialNumber",
        "ShutterCount",
        "ImageNumber",
        "OwnerName",
        "CameraOwnerName",
    ):
        val = _clean(emb_by_field.get(hw_key, ""))
        if val:
            hardware_ids[hw_key] = val

    # Compute DQT quantization fingerprint & encoder heuristic
    dqt_info = dqt_fingerprint(
        dqt_luma,
        dqt_chroma,
        subsampling=binary.get("jpeg_subsampling", ""),
        progressive=binary.get("jpeg_progressive"),
        has_exif="APP1:EXIF" in (binary.get("segments") or []),
        has_adobe=bool(binary.get("adobe_markers")),
    )
    if dqt_info:
        binary["dqt_hash"] = dqt_info["dqt_hash"]

    # Parse XMP History from raw XMP XML packets (most reliable for array alignment)
    xmp_history, doc_ids = parse_xmp_history_packets(xmp_packets)
    # Fallback: read XMP-xmpMM:History arrays from ExifTool formatted sidecar if XML wasn't found
    if not xmp_history and sidecar_data:
        fmt_dump = sidecar_data.get("formatted") or {}
        acts = fmt_dump.get("XMP:XMP-xmpMM:HistoryAction")
        whens = fmt_dump.get("XMP:XMP-xmpMM:HistoryWhen")
        agents = fmt_dump.get("XMP:XMP-xmpMM:HistorySoftwareAgent")
        params = fmt_dump.get("XMP:XMP-xmpMM:HistoryParameters")
        if acts:
            acts_l = acts if isinstance(acts, list) else [acts]
            whens_l = whens if isinstance(whens, list) else ([whens] if whens else [])
            agents_l = agents if isinstance(agents, list) else ([agents] if agents else [])
            params_l = params if isinstance(params, list) else ([params] if params else [])
            for i, act in enumerate(acts_l):
                xmp_history.append(
                    {
                        "action": _clean(act),
                        "when": _clean(whens_l[i]) if i < len(whens_l) else "",
                        "softwareAgent": _clean(agents_l[i]) if i < len(agents_l) else "",
                        "parameters": _clean(params_l[i]) if i < len(params_l) else "",
                    }
                )
        for k_short in ("DocumentID", "OriginalDocumentID", "InstanceID"):
            val = fmt_dump.get(f"XMP:XMP-xmpMM:{k_short}")
            if val:
                doc_ids.setdefault(k_short, _clean(val))

    # Gather all text surfaces for software / AI detection
    png_chunks_text = " ".join(
        f"{k}: {v}" for k, v in (binary.get("png_text_chunks") or {}).items()
    )
    comments_text = " ".join(binary.get("comments") or [])
    history_agents_text = " ".join(h.get("softwareAgent", "") for h in xmp_history)
    all_software_text = " | ".join(
        [software_raw, creator_tool, history_agents_text, png_chunks_text, comments_text]
    )

    editing_software = _detect_software_names(all_software_text)
    if binary.get("adobe_markers") and not any("Adobe" in s for s in editing_software):
        editing_software.append("Adobe software (binary JPEG marker)")

    ai_indicators = _detect_ai_names(
        all_software_text + " " + " ".join(f"{k}={v}" for k, v in emb_by_field.items())
    )
    # Stable Diffusion / ComfyUI PNG chunk keys
    png_keys_lower = {k.lower() for k in (binary.get("png_text_chunks") or {})}
    if (
        "parameters" in png_keys_lower
        and "steps:" in png_chunks_text.lower()
        and "Stable Diffusion (PNG parameters chunk)" not in ai_indicators
    ):
        ai_indicators.append("Stable Diffusion (PNG parameters chunk)")
    if {
        "prompt",
        "workflow",
    } <= png_keys_lower and "ComfyUI (PNG prompt/workflow chunks)" not in ai_indicators:
        ai_indicators.append("ComfyUI (PNG prompt/workflow chunks)")

    has_c2pa = bool(binary.get("c2pa_detected")) or any(
        "c2pa" in k.lower() or "jumbf" in k.lower() for k in emb_by_full
    )

    # Device classification (Mobile vs Dedicated Camera vs Software vs Screenshot vs Stripped)
    make_low = make.lower()
    model_low = model.lower()
    make_model = " ".join(x for x in (make, model) if x).strip()
    if make_low and model_low.startswith(make_low):
        make_model = model  # avoid "Canon Canon EOS R5"

    is_mobile = bool(
        make_model
        and (
            any(m in make_low for m in _MOBILE_MAKERS)
            or any(h in model_low for h in _MOBILE_MODEL_HINTS)
        )
    )
    is_dedicated = bool(
        make_model
        and not is_mobile
        and (
            any(m in make_low for m in _DEDICATED_CAMERA_MAKERS)
            or any(h in model_low for h in _DEDICATED_MODEL_HINTS)
        )
    )
    # Special case: Sony makes both Xperia phones and ILCE/DSC cameras
    if "sony" in make_low:
        if any(h in model_low for h in ("xperia", "xq-", "so-")):
            is_mobile, is_dedicated = True, False
        elif model_low:
            is_dedicated, is_mobile = True, False

    is_screenshot_clue = bool(
        "screenshot" in user_comment.lower()
        or "screenshot" in software_raw.lower()
        or _SCREENSHOT_NAME_RE.search(ev.original_filename)
        or (
            ev.detected_format == "PNG"
            and not make_model
            and not editing_software
            and ev.width
            and ev.height
            and (
                (ev.width, ev.height) in _SCREEN_RESOLUTIONS
                or (ev.height, ev.width) in _SCREEN_RESOLUTIONS
            )
        )
    )
    is_whatsapp_clue = bool(_WHATSAPP_NAME_RE.match(ev.original_filename))
    metadata_stripped_likely = bool(
        embedded_count == 0 and not xmp_history and not binary.get("png_text_chunks")
    )

    # Determine primary category & honest plain-English verdict
    capture_device = make_model or "Not recorded in file"
    if is_mobile:
        if "apple" in make_low or "iphone" in model_low or "ipad" in model_low:
            capture_os = "iOS / iPadOS (mobile)"
        else:
            capture_os = _detect_os_hint(make_model) or "Android / Mobile phone OS"
    elif is_dedicated:
        capture_os = "Dedicated camera firmware"
    else:
        capture_os = (
            _detect_os_hint(f"{make_model} {software_raw} {creator_tool} {history_agents_text}")
            or "Unknown"
        )

    signals: list[dict[str, str]] = []
    warnings_list: list[str] = []

    if make_model:
        signals.append(
            {
                "layer": "EXIF Hardware",
                "classification": "Extracted fact",
                "finding": f"Make/Model: {make_model}" + (f" · Lens: {lens}" if lens else ""),
            }
        )
    if software_raw or creator_tool:
        sw_joined = " / ".join(dict.fromkeys(x for x in (software_raw, creator_tool) if x))
        signals.append(
            {
                "layer": "Software Tag",
                "classification": "Extracted fact",
                "finding": f"Embedded software tag: {sw_joined}",
            }
        )
    for am in binary.get("adobe_markers") or []:
        signals.append(
            {
                "layer": "Binary Marker",
                "classification": "Extracted fact",
                "finding": am,
            }
        )
    if binary.get("jpeg_estimated_quality") is not None:
        mode = "Progressive" if binary.get("jpeg_progressive") else "Baseline"
        sub = binary.get("jpeg_subsampling") or "unknown"
        signals.append(
            {
                "layer": "JPEG Encoding",
                "classification": "Extracted fact",
                "finding": (
                    f"{mode} JPEG · estimated quality ~{binary['jpeg_estimated_quality']}/100 · "
                    f"chroma subsampling {sub}"
                ),
            }
        )
    if icc:
        signals.append(
            {
                "layer": "Color Profile",
                "classification": "Extracted fact",
                "finding": f"ICC profile: {icc}",
            }
        )
    if has_c2pa:
        c2pa_detail = "C2PA / JUMBF Content Credentials box detected in file."
        if c2pa_claims.get("claim_generator"):
            c2pa_detail += f" Generator: {c2pa_claims['claim_generator']}."
        if c2pa_claims.get("actions"):
            c2pa_detail += f" Actions: {', '.join(c2pa_claims['actions'][:4])}."
        if (
            c2pa_claims.get("ai_source_declared")
            and "C2PA Trained Algorithmic Media" not in ai_indicators
        ):
            ai_indicators.append("C2PA Trained Algorithmic Media")
        c2pa_detail += " (Offline claim inspection — signature not verified.)"
        signals.append(
            {
                "layer": "C2PA Content Credentials",
                "classification": "Extracted fact",
                "finding": c2pa_detail,
            }
        )
    if hardware_ids:
        hw_str = "; ".join(f"{k}={v}" for k, v in hardware_ids.items())
        signals.append(
            {
                "layer": "Hardware / MakerNotes IDs",
                "classification": "Extracted fact",
                "finding": hw_str,
            }
        )
    if dqt_info:
        signals.append(
            {
                "layer": "JPEG DQT Fingerprint",
                "classification": dqt_info["classification"],
                "finding": f"DQT {dqt_info['dqt_short']}… · {dqt_info['profile_hint']}",
            }
        )
    if ai_indicators:
        signals.append(
            {
                "layer": "AI Generation Markers",
                "classification": "Extracted fact",
                "finding": "AI-tool markers found: " + ", ".join(ai_indicators),
            }
        )
    if binary.get("comments"):
        signals.append(
            {
                "layer": "JPEG Comment",
                "classification": "Extracted fact",
                "finding": "; ".join(binary["comments"][:3]),
            }
        )
    if binary.get("trailing_bytes", 0) > 0:
        t_msg = (
            f"{binary['trailing_bytes']:,} trailing bytes after image end: "
            f"{binary['trailing_signature']}"
        )
        signals.append(
            {
                "layer": "Trailing Data",
                "classification": "Extracted fact",
                "finding": t_msg,
            }
        )
        warnings_list.append(f"Trailing data after image end marker: {t_msg}")
    if is_whatsapp_clue:
        signals.append(
            {
                "layer": "Filename Heuristic",
                "classification": "Automated heuristic clue",
                "finding": (
                    f"Original filename '{ev.original_filename}' matches WhatsApp naming format "
                    "(heuristic only — filenames are easily renamed)."
                ),
            }
        )

    # Embedded thumbnail comparison
    thumb_check = compare_embedded_thumbnail(orig_path, thumb_bytes, ev)
    if thumb_check.get("present"):
        signals.append(
            {
                "layer": "EXIF Thumbnail",
                "classification": "Extracted fact",
                "finding": thumb_check["message"],
            }
        )
        if thumb_check.get("status") == "mismatch":
            warnings_list.append(thumb_check["message"])

    # Primary verdict logic (always honest & evidence-backed)
    if ai_indicators:
        primary_category = "AI-generated / synthetic markers detected"
        primary_verdict = (
            f"Embedded metadata/chunks contain AI-generation markers ({', '.join(ai_indicators)})."
        )
        confidence_note = (
            "Based on explicit embedded metadata/PNG chunks. Metadata can be forged or stripped."
        )
    elif is_mobile and editing_software:
        primary_category = "Mobile camera capture + later software edit"
        primary_verdict = (
            f"Embedded EXIF reports mobile phone capture ({make_model}), followed by editing or "
            f"re-saving in {', '.join(editing_software)}."
        )
        confidence_note = "Supported by both camera EXIF tags and embedded software/history tags."
    elif is_dedicated and editing_software:
        primary_category = "Dedicated camera capture + later software edit"
        primary_verdict = (
            f"Embedded EXIF reports a dedicated camera ({make_model}), followed by editing or "
            f"export in {', '.join(editing_software)}."
        )
        confidence_note = "Supported by both camera EXIF tags and embedded software/history tags."
    elif is_mobile:
        primary_category = "Mobile phone camera"
        primary_verdict = f"Embedded EXIF tags report capture on a mobile device: {make_model}."
        confidence_note = (
            "Based on embedded EXIF camera tags (editable/spoofable; not cryptographic proof)."
        )
    elif is_dedicated:
        primary_category = "Dedicated / real camera"
        primary_verdict = f"Embedded EXIF tags report capture on a dedicated camera: {make_model}."
        confidence_note = (
            "Based on embedded EXIF camera tags (editable/spoofable; not cryptographic proof)."
        )
    elif make_model:
        primary_category = "Camera / hardware device (unspecified class)"
        primary_verdict = f"Embedded EXIF reports device '{make_model}'" + (
            f" with software {', '.join(editing_software)}." if editing_software else "."
        )
        confidence_note = "Camera Make/Model present in EXIF; verify model against vendor specs."
    elif editing_software:
        primary_category = "Created or exported by computer graphics / editing software"
        primary_verdict = (
            f"No camera Make/Model found. Embedded tags/binary markers indicate creation or export "
            f"via {', '.join(editing_software)}."
        )
        confidence_note = (
            "Could be originally created in software OR a camera photo whose camera EXIF was "
            "removed during export."
        )
    elif is_screenshot_clue:
        primary_category = "Possible computer / phone screenshot (heuristic)"
        primary_verdict = (
            f"No camera EXIF found. Format ({ev.detected_format}), resolution "
            f"({ev.width}x{ev.height}), or markers are consistent with a screen capture."
        )
        confidence_note = (
            "Heuristic clue only — stripped camera photos or resized graphics can share dimensions."
        )
    elif metadata_stripped_likely:
        primary_category = "Metadata stripped / re-encoded (origin device unknown)"
        primary_verdict = (
            "No embedded EXIF, IPTC, or XMP metadata is present. This commonly happens when an "
            "image is sent via WhatsApp, Telegram, Instagram, Facebook, or saved without metadata."
        )
        confidence_note = (
            "Original phone, computer, or camera cannot be determined reliably from stripped bytes."
        )
        warnings_list.append(
            "All embedded EXIF/XMP/IPTC metadata is absent — original device/editor chain cannot "
            "be recovered from this file."
        )
    else:
        primary_category = "Inconclusive (limited embedded metadata)"
        primary_verdict = (
            "Some metadata is present, but no camera Make/Model or recognized editing software "
            "signature was found."
        )
        confidence_note = "Review the Metadata tab and binary signals manually."

    # --- Build chronological Step-by-Step Embedded History Timeline ---
    steps: list[TraceStep] = []
    step_no = 1

    # Step 1: Capture / creation event (from EXIF / camera tags)
    if make_model or dt_orig or dt_create:
        cap_desc = (
            f"Captured on {make_model}" if make_model else "Initial file/image creation timestamp"
        )
        if lens:
            cap_desc += f" (lens: {lens})"
        if has_gps:
            cap_desc += " · embedded GPS present"
        steps.append(
            TraceStep(
                step_no=step_no,
                stage="1. Capture / Creation",
                timestamp=dt_orig or dt_create or "Timestamp not recorded",
                device_or_tool=make_model
                or (editing_software[0] if editing_software else "Unknown"),
                platform_hint=capture_os,
                action=cap_desc,
                evidence_source="EXIF Make/Model & DateTimeOriginal/CreateDate",
                classification="Extracted fact",
            )
        )
        step_no += 1

    # Intermediate steps: XMP xmpMM:History (real Adobe / editor step-by-step audit trail!)
    for idx, h in enumerate(xmp_history, start=1):
        agent = h.get("softwareAgent") or "Unknown software"
        act = h.get("action") or "modified"
        params = h.get("parameters") or ""
        changed = h.get("changed") or ""
        action_txt = act
        if params:
            action_txt += f" ({params})"
        if changed:
            action_txt += f" [{changed}]"
        os_hint = _detect_os_hint(agent) or "Computer / editor"
        steps.append(
            TraceStep(
                step_no=step_no,
                stage="Edit / Processing",
                timestamp=h.get("when") or "Timestamp not recorded",
                device_or_tool=agent,
                platform_hint=os_hint,
                action=action_txt,
                evidence_source=f"XMP xmpMM:History entry #{idx}",
                classification="Extracted fact",
            )
        )
        step_no += 1

    # Software / ModifyDate step if no XMP history covered the editing software
    if (editing_software or software_raw or dt_mod) and not xmp_history:
        sw_label = software_raw or creator_tool or ", ".join(editing_software)
        if sw_label and sw_label != make_model:
            steps.append(
                TraceStep(
                    step_no=step_no,
                    stage="Edit / Export",
                    timestamp=dt_mod or "Timestamp not recorded",
                    device_or_tool=sw_label,
                    platform_hint=_detect_os_hint(sw_label) or "Software / unknown OS",
                    action="Processed, edited, or exported by software",
                    evidence_source=(
                        "EXIF:Software / XMP:CreatorTool"
                        if (software_raw or creator_tool)
                        else "JPEG binary Adobe marker"
                    ),
                    classification="Extracted fact",
                )
            )
            step_no += 1

    # Stripped-metadata / transfer clue step when no capture/history exists
    if metadata_stripped_likely:
        clue = (
            "Likely forwarded/re-encoded via messaging or social app (WhatsApp filename pattern)"
            if is_whatsapp_clue
            else "Metadata stripped before import (common on messaging/social apps or export tools)"
        )
        steps.append(
            TraceStep(
                step_no=step_no,
                stage="Prior Handling (Unknown)",
                timestamp="Unknown",
                device_or_tool="Metadata stripped",
                platform_hint="Unknown device / platform",
                action=clue,
                evidence_source="Absence of EXIF/IPTC/XMP + container inspection",
                classification="Automated heuristic clue",
            )
        )
        step_no += 1

    # Final step: Evidence imported into ImageTrace
    steps.append(
        TraceStep(
            step_no=step_no,
            stage="Evidence Imported",
            timestamp=ev.imported_at,
            device_or_tool="ImageTrace",
            platform_hint="Local workstation",
            action=f"Preserved read-only as {ev.stored_filename} (SHA-256: {ev.sha256[:16]}…)",
            evidence_source="ImageTrace import audit record",
            classification="Extracted fact",
        )
    )

    limitations = (
        "Image files NEVER contain IP addresses, router logs, phone numbers, or sender identities.",
        "Forwarding through WhatsApp, Telegram, Instagram, Facebook, or X typically strips EXIF "
        "and XMP history and re-encodes the file.",
        "Embedded camera/software/XMP tags can be edited or spoofed unless backed by verified "
        "cryptographic C2PA credentials.",
    )

    return ProvenanceReport(
        evidence_id=ev.id,
        engine_version=PROVENANCE_ENGINE_VERSION,
        analyzed_at=utc_now_iso(),
        primary_category=primary_category,
        primary_verdict=primary_verdict,
        confidence_note=confidence_note,
        capture_device=capture_device,
        capture_os_or_platform=capture_os,
        editing_software=tuple(editing_software),
        ai_indicators=tuple(ai_indicators),
        has_c2pa=has_c2pa,
        metadata_stripped_likely=metadata_stripped_likely,
        binary_findings=binary,
        thumbnail_check=thumb_check,
        document_ids=doc_ids,
        hardware_ids=hardware_ids,
        c2pa_claims=c2pa_claims,
        dqt_info=dqt_info,
        timeline=tuple(steps),
        evidence_signals=tuple(signals),
        warnings=tuple(warnings_list),
        limitations=limitations,
    )


# --- Database-backed Service --------------------------------------------------------------
class ProvenanceService:
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

    def latest_report(self, evidence_id: str) -> ProvenanceReport | None:
        row = self.db.con.execute(
            "SELECT * FROM analysis_runs WHERE evidence_id = ? AND module = ? "
            "ORDER BY id DESC LIMIT 1",
            (evidence_id, PROVENANCE_MODULE_NAME),
        ).fetchone()
        if row is None:
            return None
        run = AnalysisRun.from_row(row)
        return ProvenanceReport.from_dict(run.output) if run.output else None

    def analyze(
        self, evidence_id: str, *, extract_metadata_if_missing: bool = True
    ) -> ProvenanceReport:
        """Run full multi-layer offline Origin & Trace analysis and persist in analysis_runs."""
        ev = self.evidence.get_required(evidence_id)
        orig_path = self.evidence.original_path(ev)
        if not orig_path.is_file():
            raise ProvenanceError(f"Stored original file is missing: {ev.stored_filename}")

        meta_run, meta_records = self.metadata.latest_metadata(ev.id)
        if meta_run is None and extract_metadata_if_missing:
            with contextlib.suppress(Exception):
                extracted = self.metadata.extract(ev.id)
                meta_run, meta_records = extracted.run, extracted.records

        # Load sanitized ExifTool sidecar JSON if available (preserves full list arrays)
        sidecar_data: dict[str, Any] | None = None
        if meta_run is not None:
            sidecar_name = meta_run.output.get("sidecar_filename")
            if isinstance(sidecar_name, str) and sidecar_name:
                sc_path = self.metadata.metadata_dir(ev.case_id) / sidecar_name
                if sc_path.is_file():
                    with contextlib.suppress(OSError, ValueError):
                        sidecar_data = json.loads(sc_path.read_text(encoding="utf-8"))

        started_at = utc_now_iso()
        report = _classify_and_trace(ev, orig_path, meta_records, sidecar_data)
        finished_at = utc_now_iso()

        # Verify original file was never modified
        if sha256_file(orig_path) != ev.sha256:
            raise ProvenanceError("Original evidence file SHA-256 changed unexpectedly!")

        status = "warning" if report.warnings else "ok"
        author = (self.actor() or "Analyst").strip() or "Analyst"
        with self.db.transaction() as con:
            cur = con.execute(
                "INSERT INTO analysis_runs ("
                "evidence_id, module, version, started_at, finished_at, status, "
                "output_json, warnings_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ev.id,
                    PROVENANCE_MODULE_NAME,
                    PROVENANCE_ENGINE_VERSION,
                    started_at,
                    finished_at,
                    status,
                    json.dumps(report.to_dict(), ensure_ascii=False),
                    json.dumps(list(report.warnings), ensure_ascii=False),
                ),
            )
            run_id = int(cur.lastrowid)
            append_event(
                con,
                "origin_traced",
                case_id=ev.case_id,
                evidence_id=ev.id,
                actor_label=author,
                details={
                    "analysis_run_id": run_id,
                    "primary_category": report.primary_category,
                    "timeline_steps": len(report.timeline),
                    "has_c2pa": report.has_c2pa,
                    "warnings_count": len(report.warnings),
                },
            )
        log.info(
            "Origin & Trace completed for %s (%s, %d timeline steps)",
            ev.id,
            report.primary_category,
            len(report.timeline),
        )
        return report

    def export_embedded_thumbnail(self, evidence_id: str, dest_path: Path) -> tuple[Path, str]:
        """Safely extract embedded EXIF thumbnail into a derived copy and log audit event."""
        ev = self.evidence.get_required(evidence_id)
        orig_path = self.evidence.original_path(ev)
        try:
            out_path, sha = save_embedded_thumbnail(orig_path, dest_path)
        except ForensicsError as exc:
            raise ProvenanceError(str(exc)) from exc
        if sha256_file(orig_path) != ev.sha256:
            raise ProvenanceError("Original evidence file SHA-256 changed unexpectedly!")
        author = (self.actor() or "Analyst").strip() or "Analyst"
        with self.db.transaction() as con:
            append_event(
                con,
                "derived_copy_created",
                case_id=ev.case_id,
                evidence_id=ev.id,
                actor_label=author,
                details={
                    "derived_filename": out_path.name,
                    "derived_sha256": sha,
                    "kind": "embedded_exif_thumbnail",
                },
            )
        return out_path, sha

    def export_trailing_media(self, evidence_id: str, dest_path: Path) -> tuple[Path, str, int]:
        """Safely extract trailing payload after JPEG EOI/PNG IEND (e.g. Motion Photo MP4)."""
        ev = self.evidence.get_required(evidence_id)
        orig_path = self.evidence.original_path(ev)
        try:
            out_path, sha, nbytes = save_trailing_payload(orig_path, dest_path)
        except ForensicsError as exc:
            raise ProvenanceError(str(exc)) from exc
        if sha256_file(orig_path) != ev.sha256:
            raise ProvenanceError("Original evidence file SHA-256 changed unexpectedly!")
        author = (self.actor() or "Analyst").strip() or "Analyst"
        with self.db.transaction() as con:
            append_event(
                con,
                "derived_copy_created",
                case_id=ev.case_id,
                evidence_id=ev.id,
                actor_label=author,
                details={
                    "derived_filename": out_path.name,
                    "derived_sha256": sha,
                    "trailing_bytes": nbytes,
                    "kind": "trailing_payload_extract",
                },
            )
        return out_path, sha, nbytes

    def log_reverse_search_opened(self, evidence_id: str, engine_name: str) -> None:
        ev = self.evidence.get_required(evidence_id)
        author = (self.actor() or "Analyst").strip() or "Analyst"
        with self.db.transaction() as con:
            append_event(
                con,
                "reverse_search_link_opened",
                case_id=ev.case_id,
                evidence_id=ev.id,
                actor_label=author,
                details={"engine": _clean(engine_name)[:60]},
            )
