# Changelog

All notable user-facing changes. Format: Keep a Changelog; versioning: SemVer.

## [0.3.0] - 2026-11-25
### Added (Milestones 1–3 — Deep Forensics, Formats, Zoom Viewer, Correlation & Visual Reports)
- **Deep Forensics (`forensics.py`, `provenance.py` v3.0, `ui/provenance_tab.py`):**
  - JPEG DQT quantization table hashing (`dqt_hash`) and heuristic encoder/platform profile matching
    (standard IJG quality tables, Photoshop Save-As tables, stripped 4:2:0 re-encoding clues).
  - MakerNotes hardware identity extraction (`SerialNumber`, `BodySerialNumber`,
    `LensSerialNumber`, `ShutterCount`, `ImageNumber`, `OwnerName`).
  - Offline C2PA / JUMBF readable claim parser (`claim_generator`, `c2pa.actions`,
    `digitalSourceType`) with explicit unverified-signature caveat.
  - Interactive **Visual Forensics Lab** (in-memory ELA with Quality `50–98` & Boost `1–40x`
    sliders, Median 3×3 Noise Residual, Luminance Gradient, RGB/YCbCr Channel Split, and offline
    `QPainter` JPEG Ghost Curve).
  - Side-by-side Main vs. Embedded EXIF thumbnail preview strip and safe read-only extraction of
    embedded EXIF thumbnails and trailing **Motion Photo `MP4`** / binary payloads to `derived/`.
- **Formats, Interactive Zoom Viewer & Workspace Ergonomics (`image_analysis.py`,
  `ui/viewer_dialog.py`, `ui/evidence_page.py`, `ui/batch_workers.py`):**
  - Magic-byte detection and safe read-only import for **HEIC / HEIF** and **Camera RAW**
    (`DNG`, `CR2`, `CR3`, `NEF`, `ARW`) with ExifTool embedded JPEG preview fallback (zero new
    dependencies).
  - Interactive **Zoom/Pan Loupe & Side-by-Side Comparison Viewer** (`25%–800%`, crisp pixels >200%,
    live `(x, y)` + RGB readout, synchronized side-by-side comparison against EXIF thumbnail,
    ELA/Noise/Gradient/Chroma maps, or another evidence item in the case).
  - Collapsible Evidence list toggle (`Expand details` / `Show list`) and background `QThread`
    workers (`SingleOcrWorker`, `PdfReportWorker`) for live Tesseract OCR and multi-item PDF runs.
- **Case Correlation, Global Search & Visual Reports (`correlation.py`, `ui/correlation_widget.py`,
  `ui/search_dialog.py`, `reports.py`, `ui/reports_page.py`):**
  - Cross-evidence **Case Correlation** on the Analysis page: XMP `OriginalDocumentID`/`DerivedFrom`
    lineage links, Camera Serial / Capture Device / DQT / dHash clusters, chronological timeline
    with clock-skew and out-of-order `ShutterCount` anomaly flags, and multi-image offline GPS plot.
  - **Global Offline Search (`Find…` / `Ctrl+K`)** across Cases, Evidence, Origin & Trace,
    Metadata, OCR text, and Analyst notes with direct jump-to-tab navigation.
  - **Visual PDF Reports** embedding safe scaled evidence thumbnails and highlighted side-by-side
    **Main vs. EXIF Thumbnail** mismatch comparisons, plus **Export case bundle (.zip)** packaging
    PDF + JSON reports, metadata sidecars, thumbnails, and `MANIFEST.sha256`.

## [0.2.0] - 2026-11-25
### Added (Advanced Origin & Trace Forensics)
- Six-layer offline forensic provenance engine (`app/src/imagetrace/provenance.py`) persisting runs
  in `analysis_runs` (`module = "origin_trace"`) and logging `origin_traced` /
  `reverse_search_link_opened` events to the hash-chained audit trail (no schema migration needed):
  - Mobile phone vs. dedicated camera vs. computer graphics/editing software (Photoshop, Illustrator,
    Lightroom, GIMP, Canva, Figma, etc.) vs. AI-generation markers (Stable Diffusion/ComfyUI PNG
    chunks, Midjourney, Firefly, DALL-E) vs. screenshot heuristic vs. stripped/re-encoded files.
  - Deep binary container inspection: JPEG `APP0`–`APP14` (`JFIF`, `EXIF`, `XMP`, `ICC_PROFILE`,
    `MPF`, `APP11:JUMBF/C2PA`, `APP12:Ducky`, `APP13:Photoshop 3.0 IRB`, `APP14:Adobe`, `COM`),
    DQT quantization quality estimator, `SOF0`/`SOF2` baseline/progressive + chroma subsampling,
    PNG `tEXt`/`iTXt` chunk inventory, and trailing data detection after JPEG `EOI` / PNG `IEND`.
  - Step-by-step embedded history timeline (`Step 1 -> End`) parsing EXIF capture fields, raw XMP
    `xmpMM:History` XML packets, `DocumentID`/`OriginalDocumentID`/`DerivedFrom` lineage IDs, and
    OS/platform hints (`iOS/iPadOS`, `Android`, `Windows`, `macOS`, `Linux`).
  - Embedded EXIF thumbnail vs. main image aspect-ratio and 64-bit dHash consistency check.
  - On-demand in-memory Error Level Analysis (ELA) diagnostic preview (pure Pillow, with scientific
    non-proof disclaimer), metadata-stripped JPEG copy exporter, and opt-in manual public reverse
    image search buttons (Google Lens, TinEye, Bing Visual Search, Yandex Images) gated by an
    explicit disclosure prompt (zero auto-uploads).
- New **Origin & Trace** tab (`ui/provenance_tab.py`) placed right after **Overview** on the
  Evidence details screen (now 8 tabs), with automatic refresh after Metadata extraction and
  inclusion in `BatchAnalysisWorker` and JSON/PDF reports (`reports.py`).

## [0.1.1] - 2026-11-25
### Added & Improved (Phase 11 — Improvement cycle)
- Cancellable background worker (`BatchAnalysisWorker` + `BatchProgressDialog`) for
  **Analysis → Extract metadata and check GPS for unanalyzed items** so large cases never block
  the UI (`IT-001`).
- One-click case-wide **Verify all evidence integrity** on the Analysis page
  (`BatchIntegrityWorker`), verifying SHA-256 across all stored originals in the active case with
  live progress and cancellation (`IT-002`).
- Dashboard **Storage and backup health** live backup status/reminder, quick-action navigation
  buttons (`Open Cases`, `Open Evidence`, `Run Analysis`, `Generate Reports`), `F5` current-page
  refresh shortcut, and non-blocking missing-metadata readiness hint on the Reports page (`IT-003`).
- Multi-resolution programmatic application icon (`app/resources/imagetrace.ico` and
  `imagetrace.png`, generated by `tools/make_icon.py`) set on `QApplication`/`MainWindow` and
  embedded into the portable `ImageTrace.exe` (`IT-004`).

## [0.1.0] - 2026-11-24
### Added (Phase 10 — Windows packaging & release)
- Reproducible portable Windows one-directory build (`tools/build_portable.py` using
  `pyinstaller==6.22.3`, dev-only), outputting `build/dist/ImageTrace/ImageTrace.exe` with
  dynamically linked Qt/PySide6 DLLs (LGPL-3.0 compliance), zero `QtWebEngine*` modules,
  bundled ExifTool 13.64 (when present), and `MANIFEST.sha256`.
- Frozen-mode path and SVG resource resolution (`paths.is_frozen()`, `paths.project_root()`,
  `paths.resources_dir()`, `ui/theme.py`).
- Data-preserving portable uninstaller (`tools/Uninstall-KeepData.cmd`), complete third-party
  license notices (`THIRD_PARTY_LICENSES.txt`), v0.1.0 release notes (`RELEASE_NOTES.md`), and
  getting-started user guide (`docs/user-guide/getting-started.md`).
### Earlier (Phase 9)
- Phase 9: security hardening, optional encrypted backups, session lock and dependency audit.
  - Installed `cryptography==50.0.2` (Apache-2.0 / BSD-3, runtime) and `pip-audit==2.10.1`
    (Apache-2.0, dev); upgraded venv `pip` to `26.3` so `pip-audit` reports **0 known
    vulnerabilities**.
  - Optional authenticated encrypted database backups (`.sqlite.itbak`) using **AES-256-GCM** with
    **scrypt** KDF (`N=65536`), AAD-authenticated 40-byte header, `.sha256` sidecar, best-effort
    zero-overwrite cleanup of temporary decrypted files, offline corrupt-DB recovery helper
    (`restore_corrupt_database_file`), configurable backup-age reminder (never auto-deleting),
    and recovery guide at `docs/user-guide/backup-recovery.md`.
  - Session lock privacy screen (**Lock** button / `Ctrl+L`) with configurable idle auto-lock
    timeout and optional unlock passphrase stored strictly as a salted scrypt hash (never
    plaintext), hiding all case, evidence, OCR, and note views while locked.
  - Hardened filename sanitization against Windows reserved device names (`CON`, `NUL`, `COM1`–`9`,
    `LPT1`–`9`), enforced `PlainText` on every `QLabel` in `MainWindow`, registered optional
    Windows TrueType Unicode fonts in ReportLab PDFs (with Helvetica/Courier fallback), and
    clamped unhandled-error dialog output.
  - Published Phase 9 security review (`docs/security/phase9-security-review.md`) and added
    malformed/adversarial file matrix, encrypted backup/corrupt-restore, AST static-security,
    and 20-image `tracemalloc` memory/performance tests.
### Earlier (Phase 8)
- Phase 8: offline PDF and JSON report generation, validation, export hashes and verification.
  - Installed `reportlab==5.1.0` (BSD) into `venv` and pinned it in `requirements.txt` and
    `pyproject.toml`.
  - Database migration v6 → v7 adding the append-only `exports` table (with `exports_no_delete`
    SQLite trigger and automatic pre-migration backup).
  - Offline report engine (`app/src/imagetrace/reports.py`):
    - Generates structured UTF-8 JSON (`imagetrace.report.v1`) and multi-page ReportLab PDF
      reports scoped to the entire active case or a single evidence item.
    - Enforces structural, SHA-256, provenance, GPS, OCR, note, and audit invariants via
      `validate_report_payload(...)` before writing any file.
    - Clearly separates **Extracted fact**, **Automated result**, **Analyst observation**, and
      **Hypothesis**, records all app & tool versions, performs a read-only live original SHA-256
      check, includes a GPS section **only** when GPS data exists (with raw, formatted, and
      normalized values), labels OCR as automated/untrusted (with optional full text), XML-escapes
      all dynamic text in PDFs, checks free space before writing, writes atomically with a
      `.sha256` sidecar, and logs sanitized `report_exported` / `export_verified` events.
  - Full **Reports** page replacing the last placeholder screen: scope selector, inclusion options,
    **Generate PDF / JSON report** and **Save as…** actions, export history table, one-click
    **Verify selected export** (checks file + `.sha256` sidecar against DB), Copy SHA-256, and
    Open reports folder.
  - Case details and Evidence Integrity & history now display live export counts and latest report
    timestamps; About page lists ReportLab 5.1.0 (BSD).
### Earlier (Phase 7)
- Phase 7: append-only analyst notes, visual-clue workspace, and audit-chain verification.
  - Database migration v5 → v6 adding the `notes` table (with `author_label`, `category`,
    `confidence_label`, `source_reference`, `status`, `supersedes_note_id`, UTC timestamps) and
    a SQLite `BEFORE DELETE` trigger enforcing append-only retention.
  - Structured confidence categories (`Confirmed from file`, `Corroborated`,
    `Analyst observation`, `Unverified hypothesis`) with mandatory source/citation references for
    `Confirmed from file` and `Corroborated`, defaulting conservatively to `Analyst observation`.
  - Append-only editing model: revising a note marks the prior row `superseded` and appends a
    linked new revision (`supersedes_note_id`); retracting marks a note `retracted` without
    deleting it. Raw note text and citations are never written to `imagetrace.log` or
    `audit_events.details_json`.
  - **Analyst notes** tab on the Evidence details screen with visual-clue guidance banner,
    Add/Revise/Retract actions, confidence filter, `Show superseded / retracted` toggle, plain-text
    detail viewer with attribution & revision chain, and explicit append-only limitation notice.
  - Active note counts added to Evidence Overview, Case details, and the Analysis page (broken down
    by confidence level), plus one-click **Verify audit chain** buttons on Evidence Integrity &
    history, Case details, and Analysis.
### Earlier (Phase 6)
- Phase 6: optional, user-triggered offline OCR (Tesseract CLI integration).
  - Database migration v4 → v5 adding `ocr_runs` and `ocr_items` tables (with automatic
    pre-migration backup).
  - Direct Tesseract CLI integration via subprocess with argument lists (never `shell=True`),
    timeouts, `--version` / `--list-langs` discovery, TSV parsing (word text, confidence,
    bounding boxes, reading-order line/paragraph reconstruction), and strict language-code
    validation.
  - Feeds Tesseract a safe, Pillow-decoded, EXIF-transposed PNG copy in `data/temp/` (deleted
    immediately after in `finally:`) so originals stay untouched and malformed/decompression-bomb
    images are stopped before reaching Tesseract.
  - OCR tab on Evidence details screen: prominent **Automated result / untrusted data** banner,
    language selector, user-triggered **Run OCR** button, plain-text `QPlainTextEdit` output,
    word/confidence/bounding-box table, Copy text, JSON & TXT export with provenance/SHA-256,
    and built-in Setup guide.
  - Settings → External tools adds configurable **Tesseract OCR** executable path, optional
    **Tessdata folder** for manual `*.traineddata` packs, **Test** button, and **Guide…**
    (never downloads language packs silently).
  - Offline installation & language-pack guide added at `docs/user-guide/ocr-setup.md`.
  - Raw OCR text is never written to `imagetrace.log` or `audit_events`.
### Earlier (Phase 5)
- Phase 5: embedded GPS parsing, validation, local offline plot and opt-in external map preview.
  - Database migration v3 → v4 adding `gps_records` table (with automatic pre-migration backup).
  - Parses embedded `EXIF:GPS:*` (and `XMP` GPS fallback) strictly from embedded metadata —
    ignores non-embedded `Composite` fields, validates `-90..90` / `-180..180` ranges, rejects
    NaN/Inf/malformed values, applies hemisphere and below-sea-level signs accurately, flags
    missing/conflicting hemisphere refs, Null Island `(0, 0)`, and EXIF-vs-XMP GPS discrepancies.
  - GPS tab on Evidence details screen:
    - Prominent capture-time & editability warning banner; never reverse-geocodes addresses.
    - Displays normalized decimal degrees, DMS, altitude, GPS UTC timestamp, source field,
      parser version, Copy coordinates button, raw embedded GPS fields table, and a 100% offline
      `QPainter` world latitude/longitude coordinate plot.
    - Explicitly states “No GPS coordinates found in the examined file.” when absent.
    - Optional on-demand OpenStreetMap 2×2 tile preview strictly **OFF by default**, requiring
      explicit user consent after a clear disclosure before sending tile coordinates; tiles are
      kept in memory only and never written to disk; explicit opt-in loads are logged to the
      audit trail (without coordinates).
  - Settings → Privacy and network card adds an opt-in toggle for optional external map preview
    (with disclosure), and the status bar truthfully reflects offline vs opt-in state.
  - Metadata extraction and Analysis batch run automatically check embedded GPS locally offline.
### Earlier (Phase 4)
- Phase 4: metadata extraction via ExifTool 13.64 (verified by published SHA-256, installed under
  `tools/third-party/exiftool/`).
  - Runs ExifTool locally via subprocess with argument lists (never `shell=True`), explicit
    timeouts, and version capture; never modifies original evidence files.
  - Database migration v2 → v3 adding `analysis_runs` and `metadata_records` tables (with
    automatic pre-migration backup).
  - Preserves sanitized raw numeric and formatted ExifTool dumps in structured sidecar files
    (`cases/<case-id>/metadata/<evidence-id>-exiftool.json`) with verified SHA-256, omitting
    local host directory paths.
  - Clearly distinguishes **Embedded** metadata (EXIF, GPS, IPTC, XMP, ICC_Profile, MakerNotes,
    PNG text chunks) from **File / container** headers (JFIF, File, PNG/BMP/GIF headers) and
    **Tool / system** fields (ExifTool, Composite, File:System).
  - Normalizes EXIF/IPTC/XMP timestamps, flags missing timezones and mixed-timezone ambiguity,
    and reports contradictory dates (`ModifyDate < DateTimeOriginal`, mismatched creation dates,
    future timestamps) strictly as factual discrepancies for review — never as proven conclusions.
  - Metadata tab on Evidence details screen: disclaimer banner, Extract / Re-extract button,
    embedded camera/lens/orientation/software/date summary, warnings list, origin/group/search
    filters, raw + normalized value table, and structured JSON export with provenance, legend
    and SHA-256.
  - Analysis page now shows active-case metadata extraction progress with a one-click batch
    extract for unanalyzed evidence items.
  - Settings page adds an **External tools** card to inspect, override, or test the ExifTool
    executable path.
  - Dashboard Tool status detects bundled/configured ExifTool; About page lists ExifTool's
    license (Perl Artistic / GPL v1+).
### Earlier (Phase 3)
- Phase 3: evidence import and integrity workflow.
  - Import images into the active case via file picker (`Ctrl+I`) or drag-and-drop, with a
    background progress dialog and working Cancel button.
  - Original files are copied into `cases/<case-id>/originals/` under app-generated IDs
    (`E-YYYYMMDD-XXXXXX.<ext>`), marked read-only, and verified with SHA-256 before and after
    copying. Source folder paths are never stored automatically; an optional provenance note
    field is provided instead.
  - Magic-byte signature detection supports JPEG, PNG, WebP, TIFF, BMP and GIF, and rejects
    archives (ZIP/RAR/7z/GZIP), executables, SVG/XML/HTML, PDF, text and empty files without
    unpacking or executing anything.
  - Configurable import safety limits in Settings (max file size, default 50 MB; max image
    megapixels decompression-bomb guard, default 50 MP), plus D-drive free-space checks.
  - Exact SHA-256 duplicate confirmation prompt before importing a duplicate into a case.
  - 7-tab Evidence details screen: Overview (safe PNG thumbnail, properties, copyable SHA-256,
    on-demand integrity verification, warnings), Image properties (dimensions, color mode, ICC
    profile, 64-bit dHash perceptual similarity with explicit non-proof caveat, and safe
    rotate/contrast/resize derived viewing copies that leave originals untouched), Integrity &
    history (per-evidence chain-of-custody events, read-only status, tool versions), plus
    honest placeholders for Metadata (Phase 4), GPS (Phase 5), OCR (Phase 6) and Analyst notes
    (Phase 7).
  - Dashboard "Recent evidence" card, Case details evidence count, and Pillow version reporting.
  - Database migration v1 → v2 (`evidence` table and `audit_events` evidence index, with
    automatic pre-migration backup).
### Changed
- Added `Pillow==13.0.0` (MIT-CMU) to runtime dependencies (`pyproject.toml`, `requirements.txt`,
  and `start.bat` startup check).
- Settings page is now scrollable so all cards fit cleanly on smaller windows.
### Earlier
- Phase 2: case management. Create, edit, archive and reopen cases (never deleted), with a
  generated case ID (no personal data), title, tags, a required scope/authorization declaration
  with confirmation, notes, and a data-minimisation warning.
- Cases page with filter, show-archived toggle, details panel and per-case history.
  The top-bar search jumps to filtered cases.
- Active case, shown in the top bar and remembered between sessions.
- Dashboard lists recent cases.
- Local SQLite database with schema migrations (backup before migrating; refuses newer schemas).
- Hash-chained, append-only (application-level) history of case events.
- Settings: analyst label (recorded on history events), database backup, integrity
  verification (database plus history chain), and restore from backup (current database is
  backed up first).
- If the data folder is missing at start-up, you're asked to choose a folder (and optionally
  remember it), continue without storage, or quit.
- Log file in `data/logs/imagetrace.log` (IDs and event types only; no case text).
- `start.bat` (launch the app) and `requirements.bat` (create the environment and install
  pinned dependencies), with `requirements.txt` / `requirements-dev.txt`.
### Changed
- Settings moved from `data/config/settings.json` to the database (imported automatically
  once; the old file is left in place).
- All labels render user-entered text as plain text (no HTML interpretation).
- Checkboxes now show a visible box and tick mark in both themes.
### Earlier
- Phase 1: desktop window with sidebar navigation (Dashboard, Cases, Evidence, Analysis,
  Reports, Settings, About), a top bar (active case, search placeholder, status) and a status bar
  showing the storage location and offline state.
- Dashboard with honest empty states, storage health (free space, missing-root error) and
  external-tool detection.
- Light/dark/system themes.
- Keyboard navigation: Ctrl+1…Ctrl+7 and arrow keys in the sidebar.
- Phase 0: project skeleton, development documentation, D-drive environment helper and
  environment verification script.
