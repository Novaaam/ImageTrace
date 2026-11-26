"""Offline visual & binary forensic diagnostics (v0.3.0 Milestone 1).

Strictly read-only on original evidence files, 100% offline, pure Pillow + Python stdlib:
- JPEG DQT quantization table hashing & heuristic encoder/platform fingerprinting.
- Offline C2PA / JUMBF readable claim extraction (honestly flagged as unverified signature).
- Interactive ELA, Median-Filter Noise Residual, Luminance Gradient, RGB/YCbCr channel split,
  and JPEG Ghost recompression quality curve.
- Safe read-only extraction of embedded EXIF JPEG thumbnails and trailing payloads (e.g. MP4
  Motion Photos after JPEG EOI / PNG IEND) without ever modifying originals.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

from imagetrace.hashing import sha256_file
from imagetrace.image_analysis import _PIL_LOCK

FORENSICS_LAB_DISCLAIMER = (
    "Visual forensic maps (ELA, Noise Residual, Luminance Gradient, Channel Split, JPEG Ghost) "
    "are exploratory diagnostic aids — NOT proof of tampering. Sharp edges, high-contrast text, "
    "and uniform re-encoding naturally create bright regions."
)

# Standard IJG Annex K tables (zig-zag and natural order for exact quality generation)
_IJG_LUMA = (
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
_ZIGZAG = (
    0,
    1,
    8,
    16,
    9,
    2,
    3,
    10,
    17,
    24,
    32,
    25,
    18,
    11,
    4,
    5,
    12,
    19,
    26,
    33,
    40,
    48,
    41,
    34,
    27,
    20,
    13,
    6,
    7,
    14,
    21,
    28,
    35,
    42,
    49,
    56,
    57,
    50,
    43,
    36,
    29,
    22,
    15,
    23,
    30,
    37,
    44,
    51,
    58,
    59,
    52,
    45,
    38,
    31,
    39,
    46,
    53,
    60,
    61,
    54,
    47,
    55,
    62,
    63,
)

# Adobe Photoshop "Save As" Quality 12 (max) & Quality 10 luminance tables (zig-zag order)
_PHOTOSHOP_Q12_LUMA = (
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
)
_PHOTOSHOP_Q10_LUMA = (
    2,
    2,
    2,
    3,
    2,
    2,
    3,
    3,
    3,
    3,
    4,
    4,
    4,
    5,
    6,
    10,
    7,
    6,
    6,
    6,
    6,
    12,
    9,
    9,
    7,
    10,
    14,
    13,
    15,
    15,
    14,
    13,
    14,
    14,
    16,
    18,
    23,
    20,
    16,
    17,
    22,
    17,
    14,
    14,
    20,
    27,
    20,
    22,
    24,
    25,
    26,
    26,
    26,
    16,
    20,
    29,
    31,
    28,
    25,
    30,
    23,
    26,
    26,
    25,
)

_PRINTABLE_RE = re.compile(rb"[ -~]{4,4096}")
_C2PA_ACTION_RE = re.compile(r"c2pa\.[a-zA-Z0-9_.-]{2,40}")
_C2PA_SOURCE_RE = re.compile(r"https?://cv\.iptc\.org/newscodes/digitalsourcetype/[a-zA-Z0-9_-]+")


class ForensicsError(Exception):
    """Offline forensic diagnostic or payload extraction failed."""


@dataclass(frozen=True)
class ForensicMapResult:
    mode: str
    image: Image.Image
    summary: str
    disclaimer: str = FORENSICS_LAB_DISCLAIMER


@dataclass(frozen=True)
class JpegGhostResult:
    points: tuple[tuple[int, float], ...]
    min_quality: int
    min_error: float
    max_error: float
    summary: str
    disclaimer: str = FORENSICS_LAB_DISCLAIMER


def _ijg_zigzag_luma(quality: int) -> tuple[int, ...]:
    q = max(1, min(100, int(quality)))
    scale = 5000 // q if q < 50 else 200 - 2 * q
    nat = [max(1, min(255, (v * scale + 50) // 100)) for v in _IJG_LUMA]
    return tuple(nat[idx] for idx in _ZIGZAG)


# Precompute exact IJG standard tables for Q=50..100 for instant O(1) lookup
_IJG_EXACT_LUMA: dict[tuple[int, ...], int] = {_ijg_zigzag_luma(q): q for q in range(50, 101)}


def dqt_fingerprint(
    luma_qt: list[int] | None,
    chroma_qt: list[int] | None = None,
    *,
    subsampling: str = "",
    progressive: bool | None = None,
    has_exif: bool = False,
    has_adobe: bool = False,
) -> dict[str, str]:
    """Compute a deterministic SHA-256 fingerprint of JPEG DQT tables + heuristic encoder clue."""
    if not luma_qt or len(luma_qt) != 64:
        return {}
    canonical = {"luma": list(luma_qt)}
    if chroma_qt and len(chroma_qt) == 64:
        canonical["chroma"] = list(chroma_qt)
    digest = hashlib.sha256(
        json.dumps(canonical, separators=(",", ":")).encode("ascii")
    ).hexdigest()

    qt_tuple = tuple(int(v) for v in luma_qt)
    hints: list[str] = []

    if qt_tuple == _PHOTOSHOP_Q12_LUMA:
        hints.append("All-1s maximum quality table (Q=100 / Photoshop Save As 12 / lossless-like)")
    elif qt_tuple == _PHOTOSHOP_Q10_LUMA:
        hints.append("Matches Adobe Photoshop Save As Q10 non-standard luminance table")
    elif qt_tuple in _IJG_EXACT_LUMA:
        ijg_q = _IJG_EXACT_LUMA[qt_tuple]
        if ijg_q in (70, 75, 80, 82, 85) and subsampling == "4:2:0" and not has_exif:
            hints.append(
                f"Exact standard IJG Q={ijg_q} 4:2:0 table with stripped EXIF "
                "(common on messaging/social re-encoders like WhatsApp/Telegram/web exports)"
            )
        elif ijg_q == 75:
            hints.append(
                "Exact standard libjpeg/IJG default Q=75 table (Pillow/Android/web default)"
            )
        else:
            hints.append(f"Exact standard libjpeg/IJG Q={ijg_q} table")
    else:
        if has_adobe:
            hints.append(
                "Custom non-IJG quantization table with Adobe marker (Photoshop/Lightroom)"
            )
        else:
            hints.append(
                "Custom non-IJG quantization table (often camera firmware, iOS, or pro editor)"
            )

    if progressive and subsampling == "4:2:0" and not has_exif:
        hints.append("Progressive 4:2:0 stripped JPEG (typical of web/social CDN re-encoding)")

    return {
        "dqt_hash": digest,
        "dqt_short": digest[:16],
        "profile_hint": "; ".join(hints),
        "classification": "Automated heuristic clue",
    }


def parse_c2pa_claims(raw_bytes: bytes) -> dict[str, Any]:
    """Offline read-only extraction of readable C2PA / JUMBF claim fields from image bytes."""
    if not raw_bytes or b"c2pa" not in raw_bytes:
        return {}
    # Focus around the first c2pa/jumb region (bounded slice so large files stay instant)
    anchor = raw_bytes.find(b"jumb")
    if anchor < 0:
        anchor = raw_bytes.find(b"c2pa")
    window = raw_bytes[max(0, anchor - 64) : anchor + 262_144]
    text_chunks = [
        m.group(0).decode("ascii", errors="ignore") for m in _PRINTABLE_RE.finditer(window)
    ]
    joined = "\n".join(text_chunks)

    actions = sorted(set(_C2PA_ACTION_RE.findall(joined)))
    sources = sorted(set(_C2PA_SOURCE_RE.findall(joined)))

    claim_generator = ""
    for idx, tok in enumerate(text_chunks):
        low = tok.lower()
        if "claim_generator" in low:
            # Value may be in the same chunk (JSON) or the immediately following printable string
            m = re.search(r'claim_generator["\s:=]+([A-Za-z0-9 ._/+()-]{3,80})', tok)
            if m:
                claim_generator = m.group(1).strip(' ":,')
                break
            if idx + 1 < len(text_chunks):
                nxt = text_chunks[idx + 1].strip(' ":,')
                if nxt and not nxt.startswith("c2pa."):
                    claim_generator = nxt[:80]
                    break

    ai_source = any(
        "trainedalgorithmicmedia" in s.lower()
        or "compositewithtrainedalgorithmicmedia" in s.lower()
        for s in sources
    )
    return {
        "present": True,
        "claim_generator": claim_generator or "Not parsed from raw JUMBF",
        "actions": actions[:12],
        "digital_source_types": sources[:6],
        "ai_source_declared": ai_source,
        "signature_verified": False,
        "caveat": (
            "Offline claim inspection only — cryptographic C2PA certificate chain & signature "
            "integrity are NOT verified locally."
        ),
    }


def _open_for_forensics(orig_path: Path, max_edge: int = 1200) -> Image.Image:
    with _PIL_LOCK:
        try:
            with Image.open(orig_path) as im:
                rgb = ImageOps.exif_transpose(im).convert("RGB")
                rgb.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
                return rgb
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ForensicsError(f"Could not decode image for visual forensics: {exc}") from exc


def compute_noise_residual(orig_path: Path, *, strength: float = 12.0) -> ForensicMapResult:
    """Compute a median-filter high-pass noise residual map strictly in memory."""
    rgb = _open_for_forensics(orig_path, max_edge=1200)
    denoised = rgb.filter(ImageFilter.MedianFilter(size=3))
    diff = ImageChops.difference(rgb, denoised)
    raw = diff.tobytes()
    mean_v = sum(raw) / max(len(raw), 1)
    max_v = max(raw) if raw else 0
    vis = ImageEnhance.Brightness(diff).enhance(max(1.0, min(40.0, float(strength))))
    return ForensicMapResult(
        mode="Noise Residual",
        image=vis,
        summary=(
            f"3x3 Median high-pass residual · Boost {strength:.1f}x · "
            f"Mean noise: {mean_v:.2f} · Max: {max_v}/255"
        ),
    )


def compute_luminance_gradient(orig_path: Path, *, strength: float = 3.0) -> ForensicMapResult:
    """Compute an amplified luminance edge/gradient map strictly in memory."""
    rgb = _open_for_forensics(orig_path, max_edge=1200)
    gray = rgb.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    raw = edges.tobytes()
    mean_v = sum(raw) / max(len(raw), 1)
    vis = ImageEnhance.Brightness(edges.convert("RGB")).enhance(
        max(1.0, min(20.0, float(strength)))
    )
    return ForensicMapResult(
        mode="Luminance Gradient",
        image=vis,
        summary=f"Luminance edge gradient · Boost {strength:.1f}x · Mean energy: {mean_v:.2f}",
    )


def compute_channel_view(orig_path: Path, channel: str) -> ForensicMapResult:
    """Extract an individual RGB or YCbCr channel for forensic inspection."""
    rgb = _open_for_forensics(orig_path, max_edge=1200)
    ch = channel.upper()
    if ch in ("R", "G", "B"):
        r, g, b = rgb.split()
        band = {"R": r, "G": g, "B": b}[ch]
        label_name = {"R": "Red (R)", "G": "Green (G)", "B": "Blue (B)"}[ch]
    elif ch in ("Y", "CB", "CR"):
        y, cb, cr = rgb.convert("YCbCr").split()
        band = {"Y": y, "CB": cb, "CR": cr}[ch]
        label_name = {
            "Y": "Luminance (Y)",
            "CB": "Chroma Blue-Diff (Cb)",
            "CR": "Chroma Red-Diff (Cr)",
        }[ch]
    else:
        raise ForensicsError(f"Unsupported channel: {channel}")
    raw = band.tobytes()
    mean_v = sum(raw) / max(len(raw), 1)
    vis = ImageOps.autocontrast(band).convert("RGB")
    return ForensicMapResult(
        mode=f"Channel {label_name}",
        image=vis,
        summary=f"{label_name} channel (auto-contrasted) · Mean level: {mean_v:.1f}/255",
    )


def compute_jpeg_ghost_curve(
    orig_path: Path,
    *,
    qualities: tuple[int, ...] = (50, 56, 62, 68, 74, 80, 86, 92, 96, 98),
) -> JpegGhostResult:
    """Compute mean recompression error across JPEG qualities (JPEG Ghost curve)."""
    rgb = _open_for_forensics(orig_path, max_edge=600)
    pts: list[tuple[int, float]] = []
    for q in qualities:
        buf = io.BytesIO()
        rgb.save(buf, format="JPEG", quality=int(q))
        buf.seek(0)
        with Image.open(buf) as recomp:
            diff = ImageChops.difference(rgb, recomp.convert("RGB"))
            raw = diff.tobytes()
            mean_err = sum(raw) / max(len(raw), 1)
            pts.append((int(q), round(mean_err, 3)))

    min_q, min_err = min(pts, key=lambda p: p[1])
    max_err = max(p[1] for p in pts)
    return JpegGhostResult(
        points=tuple(pts),
        min_quality=min_q,
        min_error=min_err,
        max_error=max_err,
        summary=(
            f"Lowest global recompression error at Q={min_q} (mean diff {min_err:.2f}; "
            f"range {min_err:.2f}-{max_err:.2f}). Local dips may reflect prior save quality."
        ),
    )


def extract_embedded_thumbnail_bytes(orig_path: Path) -> bytes | None:
    """Return embedded EXIF JPEG thumbnail bytes from JPEG APP1 without modifying the file."""
    try:
        raw = orig_path.read_bytes()
    except OSError:
        return None
    if not raw.startswith(b"\xff\xd8"):
        return None
    pos = 2
    n = len(raw)
    while pos + 4 <= n:
        if raw[pos] != 0xFF:
            break
        while pos < n and raw[pos] == 0xFF:
            pos += 1
        if pos >= n:
            break
        marker = raw[pos]
        pos += 1
        if marker in (0xD9, 0xDA):
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
        if marker == 0xE1 and seg_data.startswith(b"Exif\x00\x00"):
            t_start = seg_data.find(b"\xff\xd8\xff", 12)
            if t_start > 0:
                t_end = seg_data.find(b"\xff\xd9", t_start + 3)
                if t_end > t_start:
                    return seg_data[t_start : t_end + 2]
    return None


def find_trailing_payload_offset(raw: bytes) -> int | None:
    """Return byte offset right after JPEG EOI or PNG IEND if trailing data exists."""
    n = len(raw)
    if raw.startswith(b"\xff\xd8"):
        pos = 2
        while pos + 2 <= n:
            if raw[pos] != 0xFF:
                break
            while pos < n and raw[pos] == 0xFF:
                pos += 1
            if pos >= n:
                break
            marker = raw[pos]
            pos += 1
            if marker == 0xD9:
                return pos if pos < n - 4 else None
            if marker in (0x01, 0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7):
                continue
            if pos + 2 > n:
                break
            seg_len = struct.unpack(">H", raw[pos : pos + 2])[0]
            if seg_len < 2 or pos + seg_len > n:
                break
            pos += seg_len
            if marker == 0xDA:
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
                        eoi = ff + 2
                        return eoi if eoi < n - 4 else None
                    if ff + 4 <= n:
                        inner_len = struct.unpack(">H", raw[ff + 2 : ff + 4])[0]
                        i = ff + 2 + max(inner_len, 2)
                    else:
                        break
                break
    elif raw.startswith(b"\x89PNG\r\n\x1a\n"):
        pos = 8
        while pos + 12 <= n:
            clen = struct.unpack(">I", raw[pos : pos + 4])[0]
            ctype = raw[pos + 4 : pos + 8]
            end = pos + 12 + clen
            if end > n:
                break
            pos = end
            if ctype == b"IEND":
                return pos if pos < n - 4 else None
    return None


def _write_bytes_atomic(dest: Path, data: bytes) -> tuple[Path, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    try:
        partial.write_bytes(data)
        os.replace(partial, dest)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return dest, sha256_file(dest)


def save_embedded_thumbnail(orig_path: Path, dest_path: Path) -> tuple[Path, str]:
    """Save embedded EXIF JPEG thumbnail to *dest_path* without modifying *orig_path*."""
    thumb = extract_embedded_thumbnail_bytes(orig_path)
    if not thumb:
        raise ForensicsError("No embedded EXIF thumbnail found in this file.")
    # Validate thumbnail decodes safely before writing
    with _PIL_LOCK:
        try:
            with Image.open(io.BytesIO(thumb)) as im:
                im.load()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ForensicsError(f"Embedded EXIF thumbnail could not be decoded: {exc}") from exc
    return _write_bytes_atomic(dest_path, thumb)


def save_trailing_payload(orig_path: Path, dest_path: Path) -> tuple[Path, str, int]:
    """Safely extract trailing bytes after JPEG EOI / PNG IEND (never alters original)."""
    try:
        raw = orig_path.read_bytes()
    except OSError as exc:
        raise ForensicsError(f"Could not read original file: {exc}") from exc
    offset = find_trailing_payload_offset(raw)
    if offset is None or offset >= len(raw):
        raise ForensicsError("No trailing payload found after the image end marker.")
    tail = raw[offset:]
    out, digest = _write_bytes_atomic(dest_path, tail)
    return out, digest, len(tail)
