# Dependency Review (Phase 0)

**Date:** 2026-11-23 · **Interpreter:** CPython 3.12.10 64-bit (Windows 10 Pro 22H2, build 19045)
**Method:** Versions, release dates, `requires_python`, licenses and Windows cp312/universal
wheel availability came from the PyPI JSON API (`https://pypi.org/pypi/<pkg>/json`).
ExifTool's version came from https://exiftool.org/ver.txt and Tesseract's from the GitHub releases
API. **Nothing has been installed or downloaded.**

## Python runtime
| Item | Finding |
|------|---------|
| CPython 3.12.10 | Security-fix phase; EOL Oct 2028. Bundled SQLite 3.49.1 (foreign keys verified). |

## Proposed Python packages
| Package | Latest | Released | Requires | Win cp312 wheel | License | Use | Phase | Verdict |
|---------|--------|----------|----------|-----------------|---------|-----|-------|---------|
| PySide6 | 6.12.0 | 2026-10-08 | ≥3.10,<3.16 | yes (abi3) | LGPL-3.0 / GPL | GUI | 1 | **Adopt.** Keep Qt libraries replaceable (PyInstaller one-dir) for LGPL compliance. |
| pytest | 9.2.0 | 2026-10-27 | ≥3.10 | yes | MIT | tests | 1 | **Adopt** (dev) |
| pytest-qt | 4.5.0 | 2025-07-01 | ≥3.9 | yes | MIT | GUI tests | 1 | **Adopt** (dev). Check it works with PySide6 6.12 at install time. |
| ruff | 0.17.6 | 2026-11-19 | ≥3.7 | yes | MIT | lint/format | 1 | **Adopt** (dev) |
| Pillow | 13.0.0 | 2026-10-16 | ≥3.11 | yes | MIT-CMU | decode, thumbnails, dHash | 3 | **Installed (Phase 3).** `Image.MAX_IMAGE_PIXELS` enforced via configurable megapixel limit (default 50 MP) under a lock. |
| reportlab | 5.1.0 | 2026-10-22 | ≥3.9,<4 | yes | BSD | PDF reports | 8 | **Installed (Phase 8).** Pulls in `charset-normalizer==3.5.3` (MIT, 214 KB). All text XML-escaped before `Paragraph` rendering (ADR-025). |
| fpdf2 | 2.9.0 | 2026-10-29 | ≥3.11 | yes | LGPL-3.0 | PDF (alternative) | — | Alternative only |
| cryptography | 50.0.2 | 2026-09-30 | ≥3.9 | yes | Apache-2.0 / BSD-3 | optional backup encryption & scrypt lock verifier | 9 | **Installed (Phase 9).** Pulls in `cffi==2.1.1` (MIT) and `pycparser==3.12` (BSD-3). Audited via `pip-audit` (0 CVEs). |
| pip-audit | 2.10.1 | — | ≥3.9 | yes | Apache-2.0 | vulnerability audit (dev) | 9 | **Installed (Phase 9, dev-only).** Scanned venv; upgraded bundled `pip` 25.0.1 → 26.3 to reach 0 known vulnerabilities. |
| pyinstaller | 6.22.3 | 2026-09-12 | ≥3.8,<3.16 | yes | GPL-2.0 + bootloader exception | packaging | 10 | **Installed (Phase 10, dev-only).** Used in one-dir mode (`tools/build_portable.py`) so Qt/PySide6 DLLs remain replaceable (LGPL-3.0) and zero runtime `_MEI*` temp extraction occurs on C:. Audited via `pip-audit` (0 CVEs). |
| opencv-python-headless | 5.1.0.95 | 2026-11-17 | ≥3.6 | yes | Apache-2.0 | optional image processing | — | **Defer.** Heavy (~40 MB+) and not needed for current scope. |
| ImageHash | 4.3.2 | 2025-02-01 | — | yes | BSD-2 | perceptual hash | — | **Reject.** Pulls in numpy and scipy; we'll write dHash with Pillow instead. |
| pytesseract | 0.3.13 | 2024-08-16 | ≥3.8 | yes | Apache-2.0 | OCR wrapper | — | **Reject.** Call the Tesseract CLI directly (ADR-004). |
| PyExifTool | 0.5.6 | 2023-10-22 | ≥3.6 | yes | GPLv3+/BSD | ExifTool wrapper | — | **Reject.** Call `exiftool -j -G1 -a -s` directly (ADR-004). |
| filetype | 1.2.0 | 2022-11-02 | — | yes | MIT | type sniffing | — | **Reject.** Use our own magic-byte table plus Pillow (last release in 2022). |

## External tools (not installed; each needs approval)
| Tool | Latest found | License | Notes |
|------|--------------|---------|-------|
| ExifTool | 13.64 (exiftool.org) | Perl Artistic / GPL v1+ | **Installed (Phase 4, 2026-11-24).** Official Windows 64-bit package verified against `exiftool.org/checksums.txt` SHA-256 (`72c29c44…77804af`), extracted to `tools/third-party/exiftool/exiftool.exe` (33.1 MB on D:, git-ignored). See `tools/third-party/README.md`. |
| Tesseract OCR | Upstream 5.5.4 (2026-10-28). UB Mannheim's latest Windows release on GitHub is v5.4.0.20240606. | Apache-2.0 | Optional (Phase 6). The installer defaults to `C:\Program Files`; install it to a D: path instead. Users add language packs (`*.traineddata`) manually; nothing is downloaded silently. Check whether a newer Windows build exists at install time. |

## Licensing summary
- With the proposed set, the app can be distributed under a permissive or LGPL-compatible
  license, provided PySide6/Qt stays dynamically linked and replaceable, and LGPL/GPL notices for
  Qt, ExifTool (if bundled) and PyInstaller's bootloader exception are shipped. This needs a final
  check in Phase 10.

## Disk footprint estimate (all on D:)
| Group | Approx. |
|-------|---------|
| PySide6 (+ shiboken6, Addons) | ~550–650 MB |
| pytest, pytest-qt, ruff | ~30 MB |
| Pillow, reportlab | ~20 MB |
| ExifTool | ~25 MB |
D-drive free space: 73 GB, which is plenty.

## Follow-ups
- Pin exact versions in `pyproject.toml` / a lock file when installing (Phase 1).
- Re-run this review before each phase that adds a dependency, and before release.
- Run `pip-audit` (or equivalent) in Phase 9 (would be a new dev dependency; ask first).
