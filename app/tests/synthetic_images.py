"""Synthetic, non-sensitive image fixtures generated programmatically for tests (blueprint §10).

Never uses real people's private photographs.
"""

from __future__ import annotations

import struct
import zipfile
import zlib
from pathlib import Path

from PIL import Image, ImageCms, ImageDraw


def make_patterned_image(size: tuple[int, int] = (160, 120), variant: int = 0) -> Image.Image:
    """Synthetic geometric pattern (gives non-trivial dHash values)."""
    w, h = size
    img = Image.new("RGB", (w, h), (30, 50, 90) if variant == 0 else (210, 180, 60))
    draw = ImageDraw.Draw(img)
    if variant == 0:
        draw.rectangle([10, 10, w // 2, h - 10], fill=(220, 220, 230))
        draw.ellipse([w // 2, 15, w - 12, h - 15], fill=(180, 70, 50))
        draw.line([(0, 0), (w, h)], fill=(20, 180, 110), width=4)
    else:
        draw.polygon([(w // 2, 8), (w - 8, h - 8), (8, h - 8)], fill=(30, 30, 60))
        draw.rectangle([w // 3, h // 3, 2 * w // 3, 2 * h // 3], fill=(240, 240, 240))
    return img


def make_valid_jpeg(
    path: Path,
    size: tuple[int, int] = (160, 120),
    *,
    variant: int = 0,
    quality: int = 92,
    with_icc: bool = False,
) -> Path:
    img = make_patterned_image(size, variant=variant)
    kwargs: dict[str, object] = {"format": "JPEG", "quality": quality}
    if with_icc:
        prof = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
        kwargs["icc_profile"] = prof.tobytes()
    img.save(path, **kwargs)
    return path


def make_valid_png(
    path: Path, size: tuple[int, int] = (120, 90), *, alpha: bool = False, variant: int = 0
) -> Path:
    img = make_patterned_image(size, variant=variant)
    if alpha:
        img = img.convert("RGBA")
        img.putalpha(200)
    img.save(path, format="PNG")
    return path


def make_valid_format(path: Path, fmt: str, size: tuple[int, int] = (80, 60)) -> Path:
    img = make_patterned_image(size)
    img.save(path, format=fmt)
    return path


def make_malformed_exif_jpeg(path: Path) -> Path:
    """Valid JPEG pixel stream with a deliberately malformed APP1 EXIF segment injected."""
    base = path.with_suffix(".tmp.jpg")
    make_valid_jpeg(base)
    raw = base.read_bytes()
    base.unlink()
    # Inject an APP1 segment (0xFFE1) carrying "Exif\0\0" followed by corrupt TIFF header bytes.
    bad_exif_payload = b"Exif\x00\x00II\x99\x99\xff\xff\xff\xff\x00\x01CORRUPT_EXIF"
    segment = b"\xff\xe1" + struct.pack(">H", len(bad_exif_payload) + 2) + bad_exif_payload
    assert raw.startswith(b"\xff\xd8")
    path.write_bytes(raw[:2] + segment + raw[2:])
    return path


def make_truncated_jpeg(path: Path) -> Path:
    """JPEG magic bytes followed by truncated/corrupted frame data."""
    base = path.with_suffix(".tmp.jpg")
    make_valid_jpeg(base, size=(200, 150))
    raw = base.read_bytes()
    base.unlink()
    # Cut off after header / partial scan so Pillow's load() fails cleanly.
    path.write_bytes(raw[: min(len(raw) // 4, 250)])
    return path


def make_decompression_bomb_png(path: Path, width: int = 20_000, height: int = 20_000) -> Path:
    """Craft a tiny PNG header declaring huge dimensions (400 MP) without allocating gigabytes."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    # IHDR: width, height, 1-bit grayscale, compression/filter/interlace = 0
    ihdr = struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0)
    # Minimal placeholder IDAT + IEND (header alone is enough to trigger decompression-bomb check)
    idat = zlib.compress(b"\x00" * 64)
    png_bytes = (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    )
    path.write_bytes(png_bytes)
    return path


def make_zip_file(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("inside.txt", "synthetic archive payload - must never be unpacked")
    return path


def make_svg_file(path: Path) -> Path:
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        encoding="utf-8",
    )
    return path


def make_exe_stub(path: Path) -> Path:
    path.write_bytes(b"MZ" + b"\x00" * 128)
    return path


def make_exif_jpeg_with_exiftool(
    path: Path, exiftool_exe: Path, tags: dict[str, str], *, size: tuple[int, int] = (180, 120)
) -> Path:
    """Create a synthetic JPEG and embed synthetic EXIF/IPTC/XMP tags using ExifTool."""
    import subprocess

    make_valid_jpeg(path, size=size)
    cmd = [str(exiftool_exe), "-overwrite_original", "-q", "-q"]
    for k, v in tags.items():
        cmd.append(f"-{k}={v}")
    cmd += ["--", str(path)]
    proc = subprocess.run(  # noqa: S603 (test helper, argument list only)
        cmd, capture_output=True, text=True, check=False, timeout=30
    )
    assert proc.returncode == 0, f"ExifTool tag setup failed: {proc.stderr}"
    return path
