# Architectural Decision Records

## ADR-001: Project root is `D:\imagetrace`
- **Date:** 2026-11-23 · **Status:** accepted
- The blueprint names `D:\ImageTrace`. The existing workspace folder is `D:\imagetrace`. Windows
  paths are case-insensitive, so this is the same location and nothing was renamed.

## ADR-002: Python 3.12 (existing install) for the venv
- **Date:** 2026-11-23 · **Status:** accepted (revisit before Phase 10)
- Python 3.12.10 is already installed and gets security fixes until Oct 2028. Every candidate
  dependency ships Windows wheels for cp312. Installing another interpreter would add more to C:,
  which only has about 1.7 GB free. We may move to 3.13 or later before packaging.

## ADR-003: Redirect temp and caches to D-drive per session
- **Date:** 2026-11-23 · **Status:** accepted
- `tools/env.ps1` sets TEMP/TMP, PIP_CACHE_DIR, PYTHONPYCACHEPREFIX, RUFF_CACHE_DIR and
  PYINSTALLER_CONFIG_DIR to paths under the project for the current session only. `venv/pip.ini`
  also pins the pip cache. No system or user environment variables are changed.

## ADR-004: Prefer a few lightweight dependencies; call CLI tools via subprocess
- **Date:** 2026-11-23 · **Status:** proposed
- ExifTool and Tesseract are called directly through `subprocess` with argument lists (never
  `shell=True`), JSON/TSV output and timeouts. This avoids the PyExifTool and pytesseract
  wrappers and keeps tool-version capture explicit.
- Perceptual hash (dHash) is written on top of Pillow, so ImageHash (which pulls in numpy and
  scipy) isn't needed. OpenCV is deferred until a scoped need appears.
- File-type detection uses magic-byte signatures plus Pillow, so the `filetype` package isn't needed.

## ADR-005: ReportLab for PDF reports
- **Date:** 2026-11-23 · **Status:** proposed
- BSD license, mature layout engine (Platypus), pure Python. fpdf2 (LGPL-3.0) is the alternative.

## ADR-006: Evidence and data are never committed to git
- **Date:** 2026-11-23 · **Status:** accepted
- `data/`, `venv/`, caches, build output and third-party binaries are git-ignored.

## ADR-007: Settings in a JSON file inside the data root, not QSettings
- **Date:** 2026-11-23 · **Status:** superseded by ADR-010
- On Windows, QSettings writes to the registry (HKCU), outside the configured data root.
  Phase 1 used an atomically written `data/config/settings.json`.

## ADR-008: Source layout without an editable install
- **Date:** 2026-11-23 · **Status:** accepted
- `app/src` goes on `sys.path` through pytest's `pythonpath` and `tools/run_app.cmd`
  (`PYTHONPATH`). This avoids `pip install -e .`, which would need the setuptools build backend.
  We'll revisit for packaging (Phase 10).

## ADR-009: Automation always calls the venv interpreter by full path
- **Date:** 2026-11-23 · **Status:** accepted
- After the execution-policy incident (DEVELOPMENT_LOG, Phase 1), scripts and agents call
  `D:\imagetrace\venv\Scripts\python.exe` explicitly with redirect variables set inline, and never
  a bare `python`/`pip`, which resolves to the global interpreter on C:.

## ADR-010: Settings in the SQLite `settings` table
- **Date:** 2026-11-24 · **Status:** accepted
- Values are JSON. Without a database (storage missing), settings live in memory only.
  The Phase 1 JSON file is imported once if the table is empty, and the file is left in place.

## ADR-011: Schema migrations via `PRAGMA user_version`
- **Date:** 2026-11-24 · **Status:** accepted
- Ordered migration steps live in `db.MIGRATIONS`. Each step and its version bump run in one
  transaction. An existing database is backed up (online backup API) before migrating. A failed
  step rolls back and leaves the previous version intact. A database newer than the app is
  refused, never downgraded.

## ADR-012: Case schema additions to the blueprint
- **Date:** 2026-11-24 · **Status:** accepted
- `cases.tags_json` was added, because the blueprint's case definition includes analyst tags but
  its table sketch didn't. Status values are `open` and `archived`; there is no delete
  operation. `audit_events.evidence_id` has no foreign key yet; the `evidence` table arrives
  in Phase 3.
- Case IDs are `C-YYYYMMDD-XXXXXX` (six random hex digits from `secrets`) and contain no
  personal data. They are validated by regex before being used as folder names.

## ADR-013: Hash-chained audit log, append-only at the application level
- **Date:** 2026-11-24 · **Status:** accepted
- Each event stores SHA-256(canonical JSON of its fields plus the previous hash). Triggers block
  UPDATE and DELETE. `verify_chain` detects edited or deleted events. A fully recomputed chain by
  someone with file access can't be detected, and this is documented in the UI and the security model.
- Event details record field names and IDs, never case text (privacy).

## ADR-014: Data-root resolution and remembered location
- **Date:** 2026-11-24 · **Status:** accepted
- Priority: `IMAGETRACE_DATA_ROOT` env var, then `imagetrace.local.json` (git-ignored, project
  root), then `<project>/data`. A missing root triggers a prompt (choose / continue without
  storage / quit). There is never an automatic fallback, and choosing a folder on C: needs an
  extra confirmation.

## ADR-015: Launcher batch files
- **Date:** 2026-11-24 · **Status:** accepted
- `start.bat` runs the app with `pythonw.exe` (no console; `--console` for troubleshooting) and
  checks that the venv and PySide6 exist. It uses `data\temp` for TEMP only if that folder
  already exists, so a missing data root is still reported by the app.
- `requirements.bat` creates the venv if needed (prefers `py -3.12`, requires 3.11 or newer),
  writes `venv\pip.ini`, checks free space, and installs `requirements-dev.txt` (default) or
  `requirements.txt` (`runtime`). Temp and pip cache go to `.cache\`.
  (The user asked for "requirment.bat"; it uses the conventional spelling to match
  `requirements.txt`.)

## ADR-016: Evidence IDs, canonical stored filenames and read-only originals
- **Date:** 2026-11-24 · **Status:** accepted
- Evidence IDs follow `E-YYYYMMDD-XXXXXX` (six random hex chars from `secrets`, no personal data).
- Stored files live at `cases/<case-id>/originals/<evidence-id><canonical-ext>` where the
  extension comes from magic-byte detection, never from user-supplied filenames. Stored originals
  are marked read-only (`stat.S_IREAD`). Original directory paths are never recorded automatically;
  only the sanitised basename and an optional analyst note are stored.

## ADR-017: Supported image formats, decompression-bomb guard and malformed-image policy
- **Date:** 2026-11-24 · **Status:** accepted
- Allowed raster formats: JPEG, PNG, WebP, TIFF, BMP, GIF (verified by magic bytes). Archives
  (ZIP/RAR/7z/GZIP), executables (PE/ELF), PDFs, SVG/XML/HTML, text and empty files are rejected
  before copying and never unpacked or executed.
- Decompression bombs exceeding the configurable megapixel limit (default 50 MP) and files
  exceeding the size limit (default 50 MB) are rejected before decoding pixels.
- Files with valid supported image magic bytes whose pixel bodies are truncated or corrupt are
  preserved unchanged with `status='warning'` and no thumbnail/dHash, so damaged evidence can
  still be hashed and tracked safely without crashing.

## ADR-018: Background import worker with dedicated SQLite connection
- **Date:** 2026-11-24 · **Status:** accepted
- `ImportWorker(QThread)` opens its own short-lived `Database` connection inside `run()` and
  closes it in `finally:`, avoiding cross-thread `sqlite3.Connection` sharing. Duplicate prompts
  pause the worker on a `threading.Event` while the main thread displays `dialogs.confirm`.

## ADR-019: Pure-Pillow 64-bit dHash for visual similarity
- **Date:** 2026-11-24 · **Status:** accepted (implements ADR-004)
- 64-bit dHash is computed from a 9×8 grayscale Pillow resize (`LANCZOS`), avoiding `ImageHash`
  (and numpy/scipy). Matches are displayed strictly as an automated similarity heuristic, never
  proof of identity.

## ADR-020: Direct ExifTool subprocess with dual formatted + raw numeric JSON dumps
- **Date:** 2026-11-24 · **Status:** accepted (implements ADR-004)
- Invokes ExifTool directly via `subprocess.run` with argument lists (`-j -G0:1 -a -s -charset
  filename=utf8 -- <path>`, plus `-n` for raw numeric values), `shell=False`, 30 s timeout and
  `CREATE_NO_WINDOW`. Avoids `PyExifTool` and captures both human-readable normalized values and
  raw numeric/original values reproducibly.
- Strips host-path tags (`File:System:Directory`, `FilePermissions`, `FileAccessDate`) and
  sanitizes `SourceFile` to `<stored_filename>` so local workstation paths never leak into
  sidecars, database records or JSON exports.

## ADR-021: Three-way metadata origin classification and immutable analysis runs
- **Date:** 2026-11-24 · **Status:** accepted
- Every metadata record stores `origin ∈ {'embedded', 'file_container', 'application_system'}` so
  UI filters and exports clearly distinguish embedded image metadata (EXIF, GPS, IPTC, XMP,
  ICC_Profile, MakerNotes, PNG text chunks) from container geometry headers (JFIF, File, PNG IHDR)
  and tool/OS fields (`ExifTool`, `Composite`, `File:System`).
- `metadata_records` rows link to an immutable `analysis_runs` row; re-extracting appends a new
  run rather than deleting prior extraction history.

## ADR-022: Embedded-only GPS parsing and in-memory opt-in map tiles (no WebEngine)
- **Date:** 2026-11-24 · **Status:** accepted
- `GpsService` reads strictly from `origin='embedded'` metadata records (`EXIF:GPS:*` primary,
  `XMP` GPS fallback) and ignores ExifTool `Composite:*` fields so coordinates are traced to
  actual embedded tags with raw values preserved alongside normalized decimal degrees.
- Default map visualization is a local 100% offline `QPainter` lat/lon graticule.
- Optional external map preview fetches a 2×2 grid of `tile.openstreetmap.org` PNG tiles into
  memory only (no disk cache, no cookies, no JavaScript, no Qt WebEngine, no reverse-geocoding),
  strictly OFF by default and requiring explicit consent after disclosure before every load.

## ADR-023: Direct Tesseract CLI on safe Pillow temp copies (TSV output, never auto-run)
- **Date:** 2026-11-24 · **Status:** accepted (implements ADR-004)
- Invokes Tesseract directly via `subprocess.run` (`tesseract <temp_png> stdout -l <lang> --oem 3 tsv`)
  with validated language codes, no shell, 60 s timeout, and `CREATE_NO_WINDOW`. Avoids
  `pytesseract` and captures word-level confidence + bounding boxes + engine version explicitly.
- Decodes the stored original through Pillow (under the locked `max_pixels` ceiling and EXIF
  orientation transpose) into a short-lived PNG inside `<data_root>/temp/` (deleted in
  `finally:`), keeping originals untouched and preventing corrupt/bomb files from reaching
  Tesseract's C++ decoders.
- Tesseract binary and `*.traineddata` language packs are strictly optional and user-installed
  (never silently downloaded); `OcrService` accepts an injectable runner for deterministic offline
  testing.

## ADR-024: Append-only analyst notes via linked revisions and SQLite DELETE trigger
- **Date:** 2026-11-24 · **Status:** accepted
- Analyst notes are stored in the `notes` table (schema v6) with explicit `author_label`, UTC
  timestamps, `category`, `confidence_label`, `source_reference`, `status ∈ {'active',
  'superseded', 'retracted'}`, and `supersedes_note_id` foreign key.
- Revising a note never overwrites `note_text` or `source_reference` in place: it marks the prior
  row `superseded` and inserts a new `active` row linked via `supersedes_note_id`. Retracting
  marks a note `retracted` while keeping it viewable when history is toggled on. A SQLite
  `BEFORE DELETE ON notes` trigger blocks deletion.
- Conclusions (`Confirmed from file` and `Corroborated`) require a non-empty `source_reference`;
  the note dialog defaults conservatively to `Analyst observation`.
- Privacy & limits: `audit_events` and `imagetrace.log` record only note IDs, category,
  confidence label, and whether a source reference exists (never raw note text or citations). The
  UI and security model explicitly state that application-level append-only controls are not
  tamper-proof against direct edits to the SQLite file unless externally secured.

## ADR-025: Offline ReportLab PDF & JSON reports with XML escaping, validation & SHA-256 sidecars
- **Date:** 2026-11-24 · **Status:** accepted (implements ADR-005)
- `ReportService` builds a canonical JSON-compatible report payload (`imagetrace.report.v1`),
  validates all structural/provenance/epistemic invariants with `validate_report_payload(...)`,
  and renders either UTF-8 JSON or a multi-page ReportLab 5.1.0 Platypus PDF strictly offline.
- Every dynamic string passed into a ReportLab `Paragraph` is sanitized (control chars stripped)
  and XML-escaped (`xml.sax.saxutils.escape`) via `_xml_safe(...)` so untrusted filenames,
  metadata, OCR text, or analyst notes containing `<`, `>`, or `&` cannot inject markup or break
  PDF rendering.
- Default output lives under `cases/<case-id>/reports/` (separate from originals, derived copies,
  and backups), checked for D-drive availability and >= 50 MiB free space, written atomically via
  `.partial`, accompanied by a `.sha256` sidecar, recorded in the append-only `exports` table
  (schema v7, `BEFORE DELETE` trigger), and verifiable on demand via `verify_export(...)`.

## ADR-026: Authenticated encrypted backups (ITBAK1: AES-256-GCM + scrypt) and session lock
- **Date:** 2026-11-24 · **Status:** accepted
- Optional encrypted database backups use a simple, documented 40-byte binary header (`ITBAK1\x00\x01`,
  scrypt `n_exp/r/p`, 16-byte random salt, 12-byte random nonce) bound as GCM Additional
  Authenticated Data (AAD) with AES-256-GCM via `cryptography==50.0.2`. Any bit flip in the header
  or ciphertext fails decryption cleanly, and the format is independently recoverable without
  ImageTrace (documented in `docs/user-guide/backup-recovery.md`).
- Session lock (`Ctrl+L` + optional idle `QTimer`) uses an outer `QStackedWidget` overlay hiding
  all case/evidence widgets and masking the top-bar active case label. Optional unlock passphrases
  are stored solely as salted scrypt verifiers (`scrypt$15$8$1$...`) with bounded work-factor
  validation and constant-time verification — zero plaintext secrets in SQLite, logs or audit.

## ADR-027: Portable PyInstaller one-directory Windows release with LGPL dynamic DLLs
- **Date:** 2026-11-24 · **Status:** accepted (implements ADR-003)
- `tools/build_portable.py` packages ImageTrace using PyInstaller 6.22.3 in `--onedir --windowed`
  mode (never `--onefile`) into `build/dist/ImageTrace/` with all caches/temp redirected to D:.
  One-dir mode keeps `_internal/PySide6/*.dll` dynamically linked & replaceable for LGPL-3.0
  compliance AND prevents runtime self-extraction into `C:\Users\...\Temp\_MEI*`.
- Excludes unused Qt add-ons (including `QtWebEngine*`, `Qt3D*`, `QtMultimedia*`, `QtQuick*`,
  `QtNetwork`, etc.) to minimize footprint (168.8 MB including ExifTool 13.64) and attack surface.
- `paths.py` and `ui/theme.py` detect `sys.frozen` / `sys._MEIPASS` so config (`imagetrace.local.json`),
  bundled ExifTool, and SVG icons resolve cleanly next to `ImageTrace.exe`.
- Ships `Uninstall-KeepData.cmd` removing only application binaries while preserving `data\`,
  `imagetrace.local.json`, and any external data folder by default.

## ADR-028: Cancellable background workers for batch analysis & case-wide integrity checks
- **Date:** 2026-11-25 · **Status:** accepted (extends ADR-018)
- `BatchAnalysisWorker` and `BatchIntegrityWorker` (`ui/batch_workers.py`) execute batch
  ExifTool/GPS analysis and case-wide SHA-256 verification on dedicated `QThread` workers with
  per-thread SQLite `Database` connections closed in `finally:`, paired with `BatchProgressDialog`
  so long runs stay responsive and cleanly cancellable between items.
- The multi-resolution application icon (`imagetrace.ico` / `imagetrace.png`) is generated
  reproducibly via Pillow (`tools/make_icon.py`) with zero external design assets or dependencies.

## ADR-029: Offline six-layer Origin & Trace forensics without schema bump or auto-uploads
- **Date:** 2026-11-25 · **Status:** accepted
- User requested deeper origin tracing (mobile camera vs. real camera vs. Photoshop/Illustrator,
  step-by-step history from initial capture through edits, and reverse-lookup capabilities).
- Implemented strictly within ImageTrace's ethical and technical boundaries in `provenance.py` and
  `ui/provenance_tab.py` (Tab 1 right after `Overview`, zero new dependencies, zero DB schema
  migration — persists structured JSON reports in `analysis_runs` with `module="origin_trace"` and
  logs sanitized `origin_traced` / `reverse_search_link_opened` audit events):
  1. Honest classification of mobile vs. dedicated camera vs. graphics/editing software vs. AI/C2PA
     markers vs. screenshot heuristics vs. stripped/re-encoded files; never fabricates IP addresses
     or forwarding chains across metadata-stripping messaging platforms.
  2. Pure-Python binary parser for JPEG `APP0`–`APP14`/`DQT`/`SOF0`/`SOF2`/`COM`, PNG text chunks,
     and trailing payloads after JPEG `EOI` (entropy-stream scanned) and PNG `IEND`.
  3. Step-by-step embedded history timeline combining EXIF capture fields, raw XMP `xmpMM:History`
     XML packets, ExifTool arrays, `DocumentID`/`OriginalDocumentID`/`DerivedFrom`, and OS hints.
  4. Embedded EXIF thumbnail vs. main image aspect-ratio and 64-bit dHash discrepancy detection.
  5. Pure-Pillow in-memory ELA preview with explicit scientific disclaimer + metadata-stripped JPEG
     copy exporter + strictly manual browser-only reverse-image-search website buttons gated by an
     explicit disclosure confirmation (zero automatic uploads).

## ADR-030: Zero-dependency Deep Forensics Lab, HEIC/RAW ExifTool previews, Zoom Viewer & Case Correlation (v0.3.0)
- **Date:** 2026-11-25 · **Status:** accepted
- Delivered all three v0.3.0 upgrade milestones with **zero new third-party dependencies** and zero
  SQLite schema changes (stays cleanly on schema v7):
  - **Deep Forensics (`forensics.py`):** pure-Pillow Noise Residual (3×3 median), Luminance
    Gradient, RGB/YCbCr Channel Split, JPEG Ghost quality curve (`QPainter` chart), deterministic
    SHA-256 JPEG DQT table fingerprinting (`Automated heuristic clue`), offline C2PA/JUMBF readable
    claim extraction (explicitly flagging unverified cryptographic signatures), MakerNotes hardware
    IDs, and safe read-only extraction of embedded EXIF thumbnails and trailing Motion Photo MP4s.
  - **HEIC/HEIF & Camera RAW (`image_analysis.py`):** magic-byte detection (`ftypheic`/`mif1`,
    `ftypcrx `, `CR2`, TIFF-based `.dng`/`.nef`/`.arw`) paired with bundled ExifTool `-b
    -PreviewImage`/`-JpgFromRaw`/`-ThumbnailImage` fallback under the same decompression-bomb pixel
    ceiling, avoiding native `libheif` binary bloat while unlocking authentic mobile/DSLR originals.
  - **Interactive Zoom/Compare Viewer (`ui/viewer_dialog.py`) & Workers (`batch_workers.py`):**
    pure-Qt `QPainter` zoom/pan loupe (`25%–800%`, crisp pixels >200%, synchronized pan/zoom) and
    `QThread` workers for live Tesseract OCR and multi-item visual PDF exports (`IT-005`).
  - **Correlation, Global Search & Verifiable Bundles (`correlation.py`, `reports.py`):**
    cross-evidence XMP lineage, hardware/DQT/dHash clustering, chronological shutter-count anomaly
    checks, `Ctrl+K` offline search, embedded thumbnails + EXIF mismatch comparisons in ReportLab
    PDFs, and atomic `.zip` case bundles with internal `MANIFEST.sha256` + `.zip.sha256` sidecar
    logged as `case_bundle_exported` (preserving schema v7 `exports` CHECK constraints and never
    leaking raw SQLite DB files or lock passphrase hashes).
