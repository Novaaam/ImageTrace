# Roadmap

Status values: `not started` · `in progress` · `awaiting review` · `complete`.
Full acceptance criteria: [BLUEPRINT.md](BLUEPRINT.md) §11.

| Phase | Title | Status |
|------:|-------|--------|
| 0 | Environment and planning | complete |
| 1 | GUI shell | complete |
| 2 | Cases and database | complete |
| 3 | Evidence import and integrity | complete |
| 4 | Metadata (ExifTool) | complete |
| 5 | GPS | complete |
| 6 | OCR | complete |
| 7 | Analyst notes and history | complete |
| 8 | Reports | complete |
| 9 | Hardening | complete |
| 10 | Packaging | complete (user approved by starting Phase 11) |
| 11 | Improvement cycle | complete (v0.1.1) |
| 12 | Advanced Origin & Trace forensics | complete (v0.2.0) |
| 13 | Deep Forensics, HEIC/RAW, Zoom Viewer, Correlation & Visual Bundles | awaiting review (v0.3.0) |

## v0.3.0: Deep Forensics, Zoom/Compare Viewer, HEIC/RAW & Case-Wide Correlation
- **Milestone 1:** DQT quantization table fingerprinting, MakerNotes hardware serial/shutter IDs,
  offline C2PA/JUMBF claim parser, interactive Visual Forensics Lab (ELA sliders, Noise Residual,
  Luminance Gradient, RGB/YCbCr channels, JPEG Ghost curve), side-by-side EXIF thumbnail preview,
  and safe EXIF thumbnail & Motion Photo `MP4` extraction (`forensics.py`, `provenance.py` v3.0).
- **Milestone 2:** HEIC/HEIF & Camera RAW (`DNG`, `CR2`, `CR3`, `NEF`, `ARW`) import with ExifTool
  embedded preview fallback (zero new deps), interactive Zoom/Pan & Synchronized Side-by-Side
  Comparison Viewer (`ui/viewer_dialog.py`), collapsible Evidence list (`Expand details`), and
  background `QThread` workers for live OCR & multi-item PDF exports (`IT-005` resolved!).
- **Milestone 3:** Cross-evidence Case Correlation, XMP lineage, hardware/DQT/dHash clusters,
  chronological timeline with shutter-count/clock anomalies, multi-image offline GPS plot
  (`correlation.py`, `ui/correlation_widget.py`), Global Offline Search (`Ctrl+K` / `Find…`),
  visual PDF reports (thumbnails + EXIF mismatch side-by-side), and verifiable `.zip` case bundle
  export with `MANIFEST.sha256`.

## v0.2.0: Advanced Origin & Trace Forensics
- Six-layer offline provenance engine (`provenance.py`) and **Origin & Trace** tab
  (`ui/provenance_tab.py`): mobile vs. real camera vs. editing software (Photoshop, Illustrator,
  etc.) vs. AI/C2PA vs. stripped classification; deep JPEG/PNG binary marker, quantization quality,
  and trailing-data inspection; step-by-step EXIF + XMP `xmpMM:History` timeline; embedded EXIF
  thumbnail consistency check; on-demand in-memory ELA preview; stripped-copy exporter; and opt-in
  manual public reverse-image-search website links (zero auto-uploads; never fabricates IP
  addresses or messaging forwarding chains). Wired into batch analysis and JSON/PDF reports.

## Phase 11: acceptance status (v0.1.1)
| Criterion | Result |
|-----------|--------|
| Review findings & create roadmap issues | ✅ Reviewed Phases 0–10 known limits & test reports; documented prioritized backlog `IT-001`–`IT-008` below |
| Reliability & defensible evidence handling | ✅ Moved `Analysis` batch metadata/GPS extraction onto a cancellable `QThread` worker (`BatchAnalysisWorker` + `BatchProgressDialog`, dedicated SQLite connection per ADR-018); added one-click case-wide **Verify all evidence integrity** (`BatchIntegrityWorker`); added Dashboard live backup-health indicator |
| Usability & ergonomics | ✅ Added programmatic multi-resolution icon (`imagetrace.ico` / `imagetrace.png`, embedded in `ImageTrace.exe` & window), Dashboard quick-action navigation buttons, Reports non-blocking missing-metadata readiness hint, and `F5` current-page refresh shortcut |
| Privacy & scope discipline | ✅ Zero network changes, zero invasive/person-tracking features, zero new dependencies |
| Tests, visual review & v0.1.1 portable build | ✅ 272 passed, ruff clean (95 files), `pip-audit` 0 vulnerabilities, 34/34 env checks, light/dark screenshots inspected, portable `v0.1.1` (`168.9 MB`) clean-machine & safe-uninstall verified |

## Prioritized Issues & Improvement Backlog (Phase 11+)
Focused strictly on **reliability, usability, privacy, and defensible evidence handling** (never
person-tracking or deanonymization):

| Issue | Priority | Area | Description | Status |
|-------|----------|------|-------------|--------|
| `IT-001` | High | Reliability | Move `Analysis` batch metadata + GPS extraction off the UI thread onto a cancellable worker with progress bar. | ✅ Done in `v0.1.1` |
| `IT-002` | High | Evidence integrity | Add one-click case-wide SHA-256 verification across all originals in the active case (with cancel & mismatch summary). | ✅ Done in `v0.1.1` |
| `IT-003` | Medium | Usability / backup hygiene | Surface live backup status/reminders on Dashboard + quick-action buttons, `F5` page refresh, and missing-metadata readiness hint on Reports. | ✅ Done in `v0.1.1` |
| `IT-004` | Medium | Packaging / polish | Create a crisp multi-resolution application icon (`imagetrace.ico`) via Pillow and embed it in `ImageTrace.exe` and `MainWindow`. | ✅ Done in `v0.1.1` |
| `IT-005` | Low | Responsiveness | Move individual OCR runs and large multi-hundred-image PDF exports onto `QThread` workers with progress/cancel if large cases become slow. | ✅ Done in `v0.3.0` |
| `IT-006` | Low | Packaging | Optional Inno Setup `.exe` installer wrapper around the portable one-dir build with install-dir / data-dir wizard pages and keep-data uninstall default. | Open (needs user preference) |
| `IT-007` | Low | Footprint | Evaluate switching dev/runtime dependency from `PySide6` to `PySide6-Essentials` to reduce `venv\` size by ~300+ MB on D:. | Open (needs user preference) |
| `IT-008` | Low | Optional OCR | Document/verify live Tesseract installation workflow on a machine with D-drive Tesseract + multiple `.traineddata` language packs. | Open (optional user setup) |
