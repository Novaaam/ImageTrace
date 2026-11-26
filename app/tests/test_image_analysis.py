from pathlib import Path

import pytest
from PIL import Image

from imagetrace.hashing import sha256_file
from imagetrace.image_analysis import (
    DecompressionBombRejectedError,
    analyze_image,
    check_header_pixel_limit,
    compute_dhash,
    create_derived_copy,
    detect_file_type,
    dhash_distance,
    extension_mismatch_warning,
)
from synthetic_images import (
    make_decompression_bomb_png,
    make_exe_stub,
    make_malformed_exif_jpeg,
    make_patterned_image,
    make_svg_file,
    make_truncated_jpeg,
    make_valid_format,
    make_valid_jpeg,
    make_valid_png,
    make_zip_file,
)


@pytest.mark.parametrize(
    ("pil_fmt", "ext", "expected_fmt", "expected_mime"),
    [
        ("JPEG", ".jpg", "JPEG", "image/jpeg"),
        ("PNG", ".png", "PNG", "image/png"),
        ("WEBP", ".webp", "WEBP", "image/webp"),
        ("TIFF", ".tif", "TIFF", "image/tiff"),
        ("BMP", ".bmp", "BMP", "image/bmp"),
        ("GIF", ".gif", "GIF", "image/gif"),
    ],
)
def test_detect_supported_image_formats(tmp_path, pil_fmt, ext, expected_fmt, expected_mime):
    p = make_valid_format(tmp_path / f"img{ext}", pil_fmt)
    det = detect_file_type(p)
    assert det.supported
    assert det.format_name == expected_fmt
    assert det.mime == expected_mime
    assert det.canonical_ext == ext


def test_detect_rejects_archives_executables_svg_and_text(tmp_path):
    cases = [
        (make_zip_file(tmp_path / "a.zip"), "ZIP"),
        (make_exe_stub(tmp_path / "a.exe"), "executable"),
        (make_svg_file(tmp_path / "a.svg"), "SVG"),
    ]
    txt = tmp_path / "notes.txt"
    txt.write_text("hello", encoding="utf-8")
    cases.append((txt, "Unsupported"))
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.7\nhello")
    cases.append((pdf, "PDF"))
    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")
    cases.append((empty, "Empty"))

    for path, word in cases:
        det = detect_file_type(path)
        assert not det.supported, f"{path.name} should be rejected"
        assert word.lower() in det.format_name.lower()


def test_extension_mismatch_warning(tmp_path):
    jpg_as_png = make_valid_jpeg(tmp_path / "disguised.png")
    det = detect_file_type(jpg_as_png)
    assert det.format_name == "JPEG"
    warn = extension_mismatch_warning("disguised.png", det)
    assert warn is not None and ".png" in warn and "JPEG" in warn
    assert extension_mismatch_warning("normal.jpg", det) is None


def test_decompression_bomb_rejected(tmp_path):
    bomb = make_decompression_bomb_png(tmp_path / "bomb.png", 20_000, 20_000)
    with pytest.raises(DecompressionBombRejectedError, match="decompression bomb"):
        check_header_pixel_limit(bomb, max_pixels=50_000_000)


def test_analyze_valid_jpeg_with_icc(tmp_path):
    src = make_valid_jpeg(tmp_path / "ok.jpg", (200, 120), with_icc=True)
    orig_hash = sha256_file(src)
    det = detect_file_type(src)
    derived = tmp_path / "derived"
    props = analyze_image(src, derived, "E-20261124-AAAAAA", det, max_pixels=50_000_000)
    assert props.decode_ok
    assert (props.width, props.height) == (200, 120)
    assert props.color_mode == "RGB"
    assert props.icc_profile_info and "sRGB" in props.icc_profile_info
    assert props.dhash and len(props.dhash) == 16
    assert props.thumbnail_filename == "E-20261124-AAAAAA-thumb.png"
    assert (derived / props.thumbnail_filename).is_file()
    assert sha256_file(src) == orig_hash  # original untouched


def test_analyze_malformed_exif_jpeg_does_not_crash(tmp_path):
    src = make_malformed_exif_jpeg(tmp_path / "bad_exif.jpg")
    orig_hash = sha256_file(src)
    det = detect_file_type(src)
    props = analyze_image(
        src, tmp_path / "derived", "E-20261124-BBBBBB", det, max_pixels=50_000_000
    )
    assert props.decode_ok
    assert props.thumbnail_filename is not None
    assert sha256_file(src) == orig_hash


def test_analyze_truncated_jpeg_returns_warning_without_crashing(tmp_path):
    src = make_truncated_jpeg(tmp_path / "trunc.jpg")
    orig_hash = sha256_file(src)
    det = detect_file_type(src)
    assert det.supported  # starts with JPEG magic
    props = analyze_image(
        src, tmp_path / "derived", "E-20261124-CCCCCC", det, max_pixels=50_000_000
    )
    assert not props.decode_ok
    assert props.thumbnail_filename is None
    assert props.dhash is None
    assert any("decoding failed" in w.lower() for w in props.warnings)
    assert sha256_file(src) == orig_hash


def test_dhash_similarity_and_distance(tmp_path):
    img_a = make_patterned_image((180, 120), variant=0)
    img_a_resized = img_a.resize((90, 60), Image.Resampling.LANCZOS)
    img_b = make_patterned_image((180, 120), variant=1)
    h_a = compute_dhash(img_a)
    h_a2 = compute_dhash(img_a_resized)
    h_b = compute_dhash(img_b)
    assert dhash_distance(h_a, h_a) == 0
    assert dhash_distance(h_a, h_a2) <= 4
    assert dhash_distance(h_a, h_b) > 10
    assert dhash_distance(h_a, None) is None
    assert dhash_distance("not-hex", h_a) is None


def test_create_derived_copy_preserves_original(tmp_path):
    src = make_valid_png(tmp_path / "orig.png", (160, 100), alpha=True)
    orig_hash = sha256_file(src)
    res = create_derived_copy(
        src,
        tmp_path / "derived",
        "E-20261124-DDDDDD",
        rotate_deg=90,
        contrast=1.3,
        max_edge=120,
        max_pixels=50_000_000,
    )
    assert res.path.is_file()
    assert res.height == 120 and res.width < res.height  # rotated 90 deg & resized
    assert res.original_sha256_after == orig_hash
    assert sha256_file(src) == orig_hash
    assert sha256_file(res.path) == res.sha256
    with pytest.raises(ValueError):
        create_derived_copy(
            src, tmp_path / "derived", "E-20261124-DDDDDD", rotate_deg=45, max_pixels=50_000_000
        )


def test_thumbnail_strips_metadata_by_converting_to_png(tmp_path: Path):
    src = make_valid_jpeg(tmp_path / "meta.jpg", with_icc=True)
    det = detect_file_type(src)
    derived = tmp_path / "derived"
    props = analyze_image(src, derived, "E-20261124-EEEEEE", det, max_pixels=50_000_000)
    with Image.open(derived / props.thumbnail_filename) as thumb:
        assert thumb.format == "PNG"
        assert "exif" not in thumb.info
