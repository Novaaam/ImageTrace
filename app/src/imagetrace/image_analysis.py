"""Safe file-type detection, Pillow image inspection, thumbnails, dHash and derived copies.

Rules:
- Never modify original evidence files; all writes go to derived copies via temp files.
- Never unpack archives or execute embedded content.
- Guard every Pillow decode with an explicit pixel ceiling (decompression-bomb defence).
- Perceptual hashing (dHash) is a visual-similarity heuristic only, never proof of identity.
"""

from __future__ import annotations

import io
import os
import subprocess
import threading
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import PIL
from PIL import Image, ImageCms, ImageEnhance, ImageOps, UnidentifiedImageError

from imagetrace.hashing import sha256_file
from imagetrace.tools_status import find_exiftool

# Serialize access to Pillow's process-wide MAX_IMAGE_PIXELS setting.
_PIL_LOCK = threading.Lock()

THUMBNAIL_MAX_EDGE = 360
DHASH_BITS = 64
DEFAULT_SIMILARITY_MAX_DISTANCE = 10

SUPPORTED_EXTENSIONS: dict[str, set[str]] = {
    "JPEG": {".jpg", ".jpeg", ".jpe", ".jfif"},
    "PNG": {".png"},
    "WEBP": {".webp"},
    "TIFF": {".tif", ".tiff"},
    "BMP": {".bmp"},
    "GIF": {".gif"},
    "HEIC": {".heic", ".heif"},
    "DNG": {".dng"},
    "CR2": {".cr2"},
    "CR3": {".cr3"},
    "NEF": {".nef"},
    "ARW": {".arw"},
}

SUPPORTED_FILE_FILTER = (
    "Supported images (*.jpg *.jpeg *.png *.webp *.tif *.tiff *.bmp *.gif "
    "*.heic *.heif *.dng *.cr2 *.cr3 *.nef *.arw);;All files (*)"
)
_HEIC_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"heif")
_TIFF_RAW_EXTS = {
    ".dng": ("DNG", "image/x-adobe-dng"),
    ".nef": ("NEF", "image/x-nikon-nef"),
    ".arw": ("ARW", "image/x-sony-arw"),
}


class DecompressionBombRejectedError(ValueError):
    """Raised when image header dimensions exceed the configured pixel safety ceiling."""


@dataclass(frozen=True)
class DetectedFileType:
    format_name: str
    mime: str
    canonical_ext: str
    supported: bool
    reason: str = ""


@dataclass(frozen=True)
class ImageProperties:
    width: int | None
    height: int | None
    color_mode: str | None
    pil_format: str | None
    icc_profile_info: str | None
    dhash: str | None
    thumbnail_filename: str | None
    warnings: tuple[str, ...]
    decode_ok: bool


@dataclass(frozen=True)
class DerivedCopyResult:
    path: Path
    filename: str
    width: int
    height: int
    sha256: str
    original_sha256_after: str
    summary: str


def pillow_version() -> str:
    return PIL.__version__


def detect_file_type(path: Path) -> DetectedFileType:
    """Sniff file type from magic bytes (reads at most 64 bytes)."""
    with Path(path).open("rb") as fh:
        header = fh.read(64)
    if not header:
        return DetectedFileType(
            "Empty file", "application/octet-stream", "", False, "File is empty."
        )

    # --- supported raster image signatures ---
    if header.startswith(b"\xff\xd8\xff"):
        return DetectedFileType("JPEG", "image/jpeg", ".jpg", True)
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return DetectedFileType("PNG", "image/png", ".png", True)
    if header.startswith((b"GIF87a", b"GIF89a")):
        return DetectedFileType("GIF", "image/gif", ".gif", True)
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return DetectedFileType("WEBP", "image/webp", ".webp", True)
    if header.startswith((b"II*\x00", b"MM\x00*")):
        if len(header) >= 10 and header[8:10] == b"CR":
            return DetectedFileType("CR2", "image/x-canon-cr2", ".cr2", True)
        ext_low = Path(path).suffix.lower()
        if ext_low in _TIFF_RAW_EXTS:
            fmt, mime = _TIFF_RAW_EXTS[ext_low]
            return DetectedFileType(fmt, mime, ext_low, True)
        return DetectedFileType("TIFF", "image/tiff", ".tif", True)
    if header.startswith(b"BM") and Path(path).stat().st_size >= 26:
        return DetectedFileType("BMP", "image/bmp", ".bmp", True)
    if len(header) >= 12 and header[4:8] == b"ftyp":
        major = header[8:12]
        compat = header[16:64]
        if major == b"crx " or b"crx " in compat:
            return DetectedFileType("CR3", "image/x-canon-cr3", ".cr3", True)
        if major in _HEIC_BRANDS or any(brand in compat for brand in _HEIC_BRANDS):
            return DetectedFileType("HEIC", "image/heic", ".heic", True)

    # --- recognized unsupported / hazardous containers (never unpacked or executed) ---
    if header.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return DetectedFileType(
            "ZIP archive",
            "application/zip",
            "",
            False,
            "Archives are not imported or unpacked automatically.",
        )
    if header.startswith(b"Rar!\x1a\x07"):
        return DetectedFileType(
            "RAR archive",
            "application/vnd.rar",
            "",
            False,
            "Archives are not imported or unpacked automatically.",
        )
    if header.startswith(b"7z\xbc\xaf\x27\x1c"):
        return DetectedFileType(
            "7-Zip archive",
            "application/x-7z-compressed",
            "",
            False,
            "Archives are not imported or unpacked automatically.",
        )
    if header.startswith(b"\x1f\x8b"):
        return DetectedFileType(
            "GZIP archive",
            "application/gzip",
            "",
            False,
            "Archives are not imported or unpacked automatically.",
        )
    if header.startswith(b"%PDF-"):
        return DetectedFileType(
            "PDF document",
            "application/pdf",
            "",
            False,
            "Only supported raster image formats (JPEG, PNG, WebP, TIFF, BMP, GIF) are allowed.",
        )
    if header.startswith(b"MZ"):
        return DetectedFileType(
            "Windows executable",
            "application/vnd.microsoft.portable-executable",
            "",
            False,
            "Executables are rejected and never run.",
        )
    if header.startswith(b"\x7fELF"):
        return DetectedFileType(
            "ELF executable",
            "application/x-elf",
            "",
            False,
            "Executables are rejected and never run.",
        )
    stripped = header.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if stripped.startswith((b"<svg", b"<?xml", b"<!doctype", b"<html")):
        return DetectedFileType(
            "SVG/XML/HTML document",
            "text/xml",
            "",
            False,
            "Scriptable vector/markup documents are not imported.",
        )
    return DetectedFileType(
        "Unsupported file",
        "application/octet-stream",
        "",
        False,
        "File signature does not match a supported image format (JPEG, PNG, WebP, TIFF, BMP, GIF).",
    )


def extension_mismatch_warning(original_filename: str, detected: DetectedFileType) -> str | None:
    ext = Path(original_filename).suffix.lower()
    expected = SUPPORTED_EXTENSIONS.get(detected.format_name, set())
    if ext and expected and ext not in expected:
        return (
            f"Filename extension '{ext}' does not match detected content format "
            f"{detected.format_name} ({detected.mime})."
        )
    return None


def _check_pixel_limit(width: int, height: int, max_pixels: int) -> None:
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid image dimensions: {width}x{height}")
    if width * height > max_pixels:
        mp = (width * height) / 1_000_000
        cap_mp = max_pixels / 1_000_000
        raise DecompressionBombRejectedError(
            f"Image dimensions ({width} x {height} = {mp:.1f} MP) exceed the safety limit of "
            f"{cap_mp:.0f} MP (potential decompression bomb). Increase the limit in Settings only "
            "if you trust this file."
        )


def check_header_pixel_limit(path: Path, max_pixels: int) -> None:
    """Inspect image dimensions without decoding full pixel buffers.

    Raises DecompressionBombRejectedError if dimensions exceed *max_pixels*.
    Other decode errors are ignored here so malformed images with valid magic bytes
    can still be preserved with a warning during full analysis.
    """
    with _PIL_LOCK:
        prev = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = max_pixels
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(path) as img:
                    _check_pixel_limit(img.width, img.height, max_pixels)
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise DecompressionBombRejectedError(
                f"Image exceeds the safety pixel limit ({max_pixels // 1_000_000} MP; potential "
                f"decompression bomb): {exc}"
            ) from exc
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
            return
        finally:
            Image.MAX_IMAGE_PIXELS = prev


def _describe_icc(raw_icc: object) -> str:
    if not isinstance(raw_icc, (bytes, bytearray)) or not raw_icc:
        return "None embedded"
    size = len(raw_icc)
    try:
        profile = ImageCms.ImageCmsProfile(io.BytesIO(raw_icc))
        desc = (ImageCms.getProfileDescription(profile) or "").strip()
        if desc:
            return f"{desc} ({size} bytes)"
    except Exception:  # corrupt ICC bytes must never crash import
        return f"Present ({size} bytes; description unreadable)"
    return f"Present ({size} bytes)"


def compute_dhash(img: Image.Image) -> str:
    """Compute a 64-bit difference hash (dHash) using Pillow only (ADR-004).

    Returns a 16-character lowercase hex string.
    """
    small = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(small.get_flattened_data())
    bits = 0
    for y in range(8):
        row_start = y * 9
        for x in range(8):
            bits = (bits << 1) | int(pixels[row_start + x] > pixels[row_start + x + 1])
    return f"{bits:016x}"


def dhash_distance(hash_a: str | None, hash_b: str | None) -> int | None:
    """Hamming distance (0..64) between two 16-char hex dHashes, or None if invalid."""
    if not hash_a or not hash_b or len(hash_a) != 16 or len(hash_b) != 16:
        return None
    try:
        return (int(hash_a, 16) ^ int(hash_b, 16)).bit_count()
    except ValueError:
        return None


def _safe_working_copy(img: Image.Image, warnings_list: list[str]) -> Image.Image:
    """Return a clean RGB/RGBA processing copy, applying EXIF orientation safely."""
    work = img.copy()
    try:
        work = ImageOps.exif_transpose(work)
    except Exception as exc:  # malformed orientation tags must not block thumbnails
        warnings_list.append(
            f"EXIF orientation tag could not be applied cleanly ({type(exc).__name__}); "
            "thumbnail uses raw pixel orientation."
        )
    if work.mode in ("RGBA", "LA") or (work.mode == "P" and "transparency" in work.info):
        return work.convert("RGBA")
    return work.convert("RGB")


def _extract_preview_via_exiftool(original_path: Path) -> bytes | None:
    """Safely ask local ExifTool for an embedded JPEG preview in HEIC/RAW containers."""
    exe = find_exiftool()
    if exe is None or not exe.is_file():
        return None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for tag in ("-PreviewImage", "-JpgFromRaw", "-ThumbnailImage"):
        try:
            proc = subprocess.run(  # noqa: S603 (fixed local exiftool executable, arg list)
                [str(exe), "-b", tag, "--", str(original_path)],
                capture_output=True,
                timeout=15,
                check=False,
                creationflags=flags,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode == 0 and proc.stdout.startswith(b"\xff\xd8\xff"):
            return proc.stdout
    return None


def _save_png_atomic(img: Image.Image, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    try:
        img.save(partial, format="PNG")
        os.replace(partial, dest)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def analyze_image(
    original_path: Path,
    derived_dir: Path,
    evidence_id: str,
    detected: DetectedFileType,
    max_pixels: int,
) -> ImageProperties:
    """Read basic properties, create a safe thumbnail and compute dHash from *original_path*.

    Never modifies *original_path*. Raises DecompressionBombRejectedError if dimensions
    exceed *max_pixels*. Other malformed-image failures return decode_ok=False with warnings
    so the original evidence file can still be safely preserved.
    """
    notes: list[str] = []
    width = height = None
    color_mode = pil_format = icc_info = dhash_hex = thumb_name = None
    decode_ok = False

    with _PIL_LOCK:
        prev = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = max_pixels
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(original_path) as img:
                    width, height = img.size
                    _check_pixel_limit(width, height, max_pixels)
                    color_mode = img.mode
                    pil_format = img.format
                    if pil_format and pil_format.upper() != detected.format_name.upper():
                        notes.append(
                            f"Container signature is {detected.format_name}, but Pillow "
                            f"identified format {pil_format}."
                        )
                    icc_info = _describe_icc(img.info.get("icc_profile"))
                    n_frames = getattr(img, "n_frames", 1)
                    if n_frames > 1:
                        notes.append(
                            f"Multi-frame image ({n_frames} frames); thumbnail and perceptual "
                            "hash reflect frame 1 only."
                        )
                    try:
                        img.getexif()
                    except Exception as exc:  # malformed orientation tags must not block thumbnails
                        notes.append(
                            f"Embedded EXIF header appears malformed ({type(exc).__name__}); "
                            "full metadata extraction arrives in Phase 4."
                        )
                    img.load()
                    work = _safe_working_copy(img, notes)
                    dhash_hex = compute_dhash(work)
                    thumb = work.copy()
                    thumb.thumbnail(
                        (THUMBNAIL_MAX_EDGE, THUMBNAIL_MAX_EDGE), Image.Resampling.LANCZOS
                    )
                    thumb_name = f"{evidence_id}-thumb.png"
                    _save_png_atomic(thumb, derived_dir / thumb_name)
                    decode_ok = True
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise DecompressionBombRejectedError(
                f"Image exceeds the safety pixel limit ({max_pixels // 1_000_000} MP; potential "
                f"decompression bomb): {exc}"
            ) from exc
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
            # For HEIC/Camera RAW containers that Pillow doesn't natively decode, try ExifTool's
            # embedded JPEG preview (read-only) under the exact same pixel safety limit.
            preview_bytes = (
                _extract_preview_via_exiftool(original_path)
                if detected.format_name in ("HEIC", "DNG", "CR2", "CR3", "NEF", "ARW")
                else None
            )
            if preview_bytes is not None:
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("error", Image.DecompressionBombWarning)
                        with Image.open(io.BytesIO(preview_bytes)) as p_img:
                            width, height = p_img.size
                            _check_pixel_limit(width, height, max_pixels)
                            color_mode = p_img.mode
                            pil_format = f"{detected.format_name} (embedded JPEG preview)"
                            icc_info = _describe_icc(p_img.info.get("icc_profile"))
                            p_img.load()
                            work = _safe_working_copy(p_img, notes)
                            dhash_hex = compute_dhash(work)
                            thumb = work.copy()
                            thumb.thumbnail(
                                (THUMBNAIL_MAX_EDGE, THUMBNAIL_MAX_EDGE),
                                Image.Resampling.LANCZOS,
                            )
                            thumb_name = f"{evidence_id}-thumb.png"
                            _save_png_atomic(thumb, derived_dir / thumb_name)
                            decode_ok = True
                            notes.append(
                                f"Thumbnail and dHash were built from the {detected.format_name} "
                                "file's embedded JPEG preview via ExifTool (original preserved)."
                            )
                except (Image.DecompressionBombError, Image.DecompressionBombWarning) as b_exc:
                    raise DecompressionBombRejectedError(str(b_exc)) from b_exc
                except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
                    preview_bytes = None
            if preview_bytes is None:
                notes.append(
                    f"Image decoding failed ({type(exc).__name__}: {exc}). "
                    "The original file is preserved unchanged; metadata extraction still works "
                    "via ExifTool, while thumbnail and perceptual hash could not be generated."
                )
                thumb_name = None
                dhash_hex = None
                decode_ok = False
        finally:
            Image.MAX_IMAGE_PIXELS = prev

    return ImageProperties(
        width=width,
        height=height,
        color_mode=color_mode,
        pil_format=pil_format,
        icc_profile_info=icc_info,
        dhash=dhash_hex,
        thumbnail_filename=thumb_name,
        warnings=tuple(notes),
        decode_ok=decode_ok,
    )


def create_derived_copy(
    original_path: Path,
    derived_dir: Path,
    evidence_id: str,
    *,
    rotate_deg: int = 0,
    contrast: float = 1.0,
    max_edge: int | None = None,
    max_pixels: int,
) -> DerivedCopyResult:
    """Create a processed PNG copy in *derived_dir*. Never modifies *original_path*."""
    if rotate_deg not in (0, 90, 180, 270):
        raise ValueError("Rotation must be 0, 90, 180 or 270 degrees.")
    if not (0.2 <= contrast <= 3.0):
        raise ValueError("Contrast must be between 0.2 and 3.0.")
    if max_edge is not None and not (64 <= max_edge <= 8192):
        raise ValueError("Max edge must be between 64 and 8192 pixels.")

    notes: list[str] = []
    with _PIL_LOCK:
        prev = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = max_pixels
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(original_path) as img:
                    _check_pixel_limit(img.width, img.height, max_pixels)
                    img.load()
                    work = _safe_working_copy(img, notes)
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise DecompressionBombRejectedError(str(exc)) from exc
        finally:
            Image.MAX_IMAGE_PIXELS = prev

    ops: list[str] = []
    if rotate_deg:
        # PIL rotate() is counter-clockwise; negate for intuitive clockwise rotation.
        work = work.rotate(-rotate_deg, expand=True)
        ops.append(f"rotated {rotate_deg} deg CW")
    if abs(contrast - 1.0) >= 0.01:
        if work.mode == "RGBA":
            rgb = ImageEnhance.Contrast(work.convert("RGB")).enhance(contrast)
            rgb.putalpha(work.getchannel("A"))
            work = rgb
        else:
            work = ImageEnhance.Contrast(work).enhance(contrast)
        ops.append(f"contrast {contrast:.2f}x")
    if max_edge is not None and max(work.size) > max_edge:
        work.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        ops.append(f"resized to fit {max_edge} px")
    if not ops:
        ops.append("unmodified viewing copy")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    filename = f"{evidence_id}-view-{stamp}.png"
    dest = derived_dir / filename
    _save_png_atomic(work, dest)
    derived_sha = sha256_file(dest)
    orig_sha = sha256_file(original_path)
    return DerivedCopyResult(
        path=dest,
        filename=filename,
        width=work.width,
        height=work.height,
        sha256=derived_sha,
        original_sha256_after=orig_sha,
        summary="; ".join(ops),
    )
