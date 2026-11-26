# Development Log

## 2026-11-23: Phase 0 (environment and planning)

### Environment inspected
| Item | Finding |
|------|---------|
| OS | Windows 10 Pro 64-bit, 10.0.19045 |
| C-drive | **1.7 GB free** (109.4 GB used). Very low, so C: writes must be minimal. |
| D-drive | 73 GB free |
| Python | 3.12.10 64-bit at `C:\Users\<user>\AppData\Local\Programs\Python\Python312` (pre-existing); `py` launcher present |
| Git | 2.56.0.windows.4 |
| ExifTool / Tesseract | not installed / not on PATH |
| Default TEMP | `C:\Users\<user>\AppData\Local\Temp` (overridden per session, see ADR-003) |
| Workspace | `D:\imagetrace` existed and was empty |

### Actions
1. Created the directory structure from blueprint §2 under `D:\imagetrace` (plus
   `app\src\imagetrace` and `.cache\pip`).
2. Created the venv: `py -3.12 -m venv D:\imagetrace\venv`, with TEMP/TMP, PIP_CACHE_DIR and
   PYTHONPYCACHEPREFIX set to D: paths for that command. The venv is 6.4 MB. `sys.prefix` is
   `D:\imagetrace\venv`, `tempfile.gettempdir()` is `D:\imagetrace\data\temp`, SQLite is 3.49.1,
   and pip is 25.0.1.
3. Copied the blueprint to `BLUEPRINT.md`. The SHA-256 of the source and the copy match
   (`386D8C1D…0D17094`).
4. Read-only lookup of dependency metadata from the PyPI JSON API, exiftool.org/ver.txt and the
   GitHub releases API. Nothing was downloaded or installed.
5. Wrote README, ROADMAP, CHANGELOG, DECISIONS, this log, `.gitignore`,
   `docs/architecture/dependency-review.md`, `docs/security/security-model.md`,
   `tools/env.ps1`, `tools/verify_env.py`, `tools/third-party/README.md` and `venv/pip.ini`.
6. Ran `tools/verify_env.py` (results below) and made the initial git commit.

### C-drive footprint
- No project files, caches or packages were written to C:. The venv's `pyvenv.cfg` points at the
  pre-existing base interpreter on C:, which is unavoidable unless another Python is installed on
  D: (see ADR-002).
- Agent tool logs live in the assistant's own app-data folder. They aren't project data.

### Verification
See `docs/test-reports/2026-11-23-phase0.md`.

### Known issues / risks
- C: is almost full. Any tool that ignores the redirects (some installers, Windows Update) could
  fail. Freeing space on C: is recommended but is the user's choice.
- UB Mannheim's newest Tesseract Windows build on GitHub (5.4.0, 2024) is behind upstream (5.5.4).

### Next task
Phase 1 (GUI shell). Waiting for approval to install PySide6, pytest, pytest-qt and ruff into the venv.

## 2026-11-23: Phase 1 (GUI shell)

The user approved Phase 1 and its dependencies.

### Incident: PowerShell execution policy, with a brief C-drive write (resolved)
- The first install command dot-sourced `tools\env.ps1`. PowerShell's execution policy blocked it,
  so the redirects never applied. The rest of the command line then ran `python -m pip install`,
  which resolved to the **global** Python on C:, using the C: temp folder and pip cache.
- I stopped it within about 15 seconds. Nothing was installed into the global `site-packages`
  (checked). It had left 26 `pip-*` temp folders in `%LOCALAPPDATA%\Temp` and 54 new files
  (~50 MB) in `%LOCALAPPDATA%\pip\cache`, all created by that command. I removed only those
  items (filtered by creation time), and C: free space went back to 1.55 GB.
- Fixes: added `tools\env.cmd` and `tools\run_app.cmd` (cmd.exe isn't affected by the
  execution policy). Automation now calls `D:\imagetrace\venv\Scripts\python.exe` by full path
  with the redirect variables set inline, and never relies on a bare `python`.
- I did not change the execution policy (that's a system security setting for the user to decide).

### Actions
1. `venv\Scripts\python.exe -m pip install PySide6==6.12.0 pytest==9.2.0 pytest-qt==4.5.0 ruff==0.17.6`
   with TEMP and PIP_CACHE_DIR on D:. The venv grew to 692 MB. The `PySide6` meta-package also pulls in
   `PySide6_Addons`, `PySide6_WebEngine` and `PySide6_Pdf` (see Known issues).
2. Added `pyproject.toml` (pinned dependencies, pytest config with `pythonpath=app/src`,
   `--basetemp=data/temp/pytest`, ruff config with the cache under `.cache/`).
3. Package `app/src/imagetrace/`: `app.py` (entry point), `__main__.py`, `paths.py` (data root plus
   storage checks, with no silent fallback), `settings.py` (atomic JSON in `data/config/`; QSettings
   avoided because it writes to the registry), `tools_status.py` (detects ExifTool/Tesseract
   without running them), and `ui/` (`main_window.py`, `pages.py`, `widgets.py`, `theme.py`).
4. Tests: `app/tests/test_paths.py`, `test_settings.py`, `test_main_window.py` (pytest-qt, offscreen).
5. `tools/screenshot.py` renders pages to PNG for visual review (output in `build/`, git-ignored).
6. Ruff found 6 long lines and 1 ambiguous EN DASH. Fixed them and applied `ruff format`.
7. Visual review found the tool-status card wrapping one word per line. Changed it to a
   single-column list.

### Verification
See `docs/test-reports/2026-11-23-phase1.md`: 36 tests passed, ruff clean, 34/34 environment checks.

### Known issues
- The `PySide6` meta-package installs WebEngine (~300+ MB) that we don't use. Option: switch to
  `PySide6-Essentials` only (smaller package and fewer LGPL components to ship). Needs a reinstall.
- The "System" theme follows Qt's detected colour scheme; not checked on a live dark-mode desktop.

### Next task
Phase 2 (cases and database). Waiting for the user to review Phase 1.

## 2026-11-24: Phase 2 (cases and database) plus launcher scripts

The user approved Phase 2 and asked for `start.bat` and "requirment.bat" (created as
`requirements.bat`; see ADR-015).

### Pre-checks
- After the server restart: git clean except a one-line user edit to `app/src/imagetrace/ui/__init__.py`
  (a blank line). Left as is; ruff format normalised it to an empty file again.
- C: had 6.68 GB free; D: had 70.6 GB free.

### Actions
1. New modules: `db.py` (connection, transactions, migrations, layout), `audit.py` (hash
   chain), `cases.py` (validation, IDs, repository), `backup.py` (backup, verify, restore),
   `hashing.py`, `timefmt.py`, `logging_setup.py`. Rewrote `settings.py` (stored in SQLite).
2. `paths.py`: remembered data-root location (`imagetrace.local.json`, git-ignored).
3. UI: `ui/dialogs.py` (plain-text modal wrappers), `ui/case_dialog.py`, `ui/cases_page.py`;
   dashboard recent cases; Settings analyst label and database card; main window owns the
   database, active case and top-bar search; start-up storage prompt in `app.py`.
4. `start.bat`, `requirements.bat`, `requirements.txt`, `requirements-dev.txt`.
5. `tools/screenshot.py` now uses a throwaway data root with SYNTHETIC cases (it never opens
   the real data root).
6. Tests: added `test_db.py`, `test_audit.py`, `test_cases.py`, `test_backup.py`,
   `test_cases_gui.py`; updated `test_settings.py`, `test_paths.py`, `test_main_window.py`;
   `conftest.py` now replaces modal dialogs and the machine-local config.

### Problems found and fixed
- `verify_backup` message lowercased "SHA-256"/"OK" (`str.capitalize`).
- An incorrect test assertion for literal `%` search.
- The test suite was slow (86 s) because identical stylesheets were re-applied. Now skipped (36 s).
- Visual review: invisible checkbox indicators (fixed with styled indicators and SVG ticks);
  details-panel background and clipping; history horizontal scrollbar.

### Verification
See `docs/test-reports/2026-11-24-phase2.md`: 123 tests passed, ruff clean, 34/34 environment
checks. `start.bat` launch test and `requirements.bat` run passed. No new `pip-*` temp folders on C:.

### Known issues / limits
- Backups are not encrypted, and there's no backup schedule or retention policy yet (Phase 9).
- Database operations run on the UI thread (fine at current sizes; revisit with evidence import).
- The checkbox SVG path is resolved from the source tree; Phase 10 packaging must bundle
  `app/resources`.

### Next task
Phase 3 (evidence import and integrity). Needs approval for the Pillow 13.0.0 dependency.

## 2026-11-24: Phase 3 (evidence import and integrity)

The user approved Phase 3 and installing Pillow 13.0.0.

### Actions
1. Installed `Pillow==13.0.0` into `D:\imagetrace\venv` using the full venv python path with
   TEMP, PIP_CACHE_DIR and PYTHONPYCACHEPREFIX on D: (`pip check` clean, 0 `pip-*` temp dirs on
   C:, C: free space unchanged at 6.54 GB). Pinned in `pyproject.toml` and `requirements.txt`,
   and added `PIL` to `start.bat`'s startup import check (CRLF preserved).
2. Schema v2 in `db.py`: added `evidence` table, case/hash indexes, and `audit_events(evidence_id)`
   index. Updated `audit.list_events` to filter by `evidence_id`.
3. Updated `settings.py`: added configurable `max_import_mb` (default 50, range 1–500) and
   `max_image_megapixels` (default 50, range 1–200).
4. Added `image_analysis.py`: magic-byte sniffing (JPEG/PNG/WebP/TIFF/BMP/GIF whitelist; explicit
   rejection of ZIP/RAR/7z/GZIP/PDF/PE/ELF/SVG/XML/HTML/text/empty), extension-mismatch detection,
   locked Pillow decompression-bomb guard, ICC profile description, safe PNG thumbnails, pure-Pillow
   64-bit dHash, and safe derived viewing copies (rotate/contrast/resize).
5. Added `evidence.py`: `EvidenceRepository` with streaming SHA-256 + cancel support, D-drive
   free-space checks, duplicate SHA-256 detection/confirmation, read-only originals with app-generated
   names, integrity verification, visual similarity query, and rollback cleanup on error/cancel.
6. Added `ui/evidence_import.py` (`ImportWorker` QThread with dedicated SQLite connection +
   `ImportProgressDialog` with Cancel & duplicate prompt) and `ui/evidence_page.py` (picker +
   drag-and-drop, filterable table, 7-tab Evidence details screen).
7. Wired `EvidenceRepository` into `main_window.py`, Dashboard ("Recent evidence" + Pillow
   version), Case details (evidence count + evidence audit labels), Settings (scrollable cards +
   import limits), and About (Pillow license).
8. Added synthetic programmatic fixtures (`app/tests/synthetic_images.py`) and 43 new tests across
   `test_image_analysis.py`, `test_evidence.py`, `test_evidence_gui.py`, plus updates to
   `test_db.py`, `test_settings.py` and `test_main_window.py` (total: 166 tests).
9. Updated `tools/screenshot.py` to generate synthetic geometric images inside a throwaway temp
   folder and capture Evidence tabs alongside Dashboard/Cases/Settings/About.

### Visual inspection & fixes
- Fixed 64-char SHA-256 labels overflowing the details scroll pane (switched to read-only
  `QLineEdit` fields with Copy button).
- Fixed Evidence table filename column squishing (interactive column widths + balanced splitter).
- Fixed `"Integrity & history"` rendering as `"Integrity _history"` (escaped to `&&`).
- Fixed `MainWindow.navigate_index` skipping `refresh()` when already on the target page.
- Constrained Settings import-limit spinboxes to 130 px.

### Verification
See `docs/test-reports/2026-11-24-phase3.md`: 166 passed, ruff clean, 34/34 env checks,
`requirements.bat` OK, `start.bat` smoke-launched against a throwaway data root.

### Known issues / limits
- Full EXIF/IPTC/XMP metadata extraction (ExifTool), GPS, OCR, analyst notes and reports are
  placeholders for Phases 4–8.
- Derived viewing copies are created synchronously on the UI thread (fast for typical images;
  guarded by the megapixel ceiling).

### Next task
Phase 4 (metadata extraction with ExifTool). Waiting for user review of Phase 3 and approval to
obtain/verify ExifTool 13.64 into `tools/third-party/exiftool/`.

## 2026-11-24: Phase 4 (metadata extraction with ExifTool)

The user approved Phase 4 and downloading ExifTool.

### Actions
1. Checked `https://exiftool.org/ver.txt` (13.64) and `https://exiftool.org/checksums.txt`,
   downloaded `exiftool-13.64_64.zip` from the official link to `D:\imagetrace\data\temp\`,
   verified exact SHA-256 match (`72c29c44…77804af`), extracted to
   `D:\imagetrace\tools\third-party\exiftool\`, renamed `exiftool(-k).exe` → `exiftool.exe`,
   verified `exiftool.exe -ver` → `13.64` (33.1 MB on D:), deleted temp zip, and recorded
   version/origin/hashes/license in `tools/third-party/README.md`.
2. Schema v3 in `db.py`: added `analysis_runs` and `metadata_records` tables with indexes.
3. Updated `settings.py` (`exiftool_path`) and `tools_status.py` (`find_exiftool`).
4. Added `metadata.py`: safe ExifTool subprocess runner (formatted + raw numeric JSON),
   three-way origin classification (`embedded` / `file_container` / `application_system`),
   timestamp normalization + missing-timezone & contradiction discrepancy checks, sanitized
   sidecar JSON preservation (`cases/<id>/metadata/<eid>-exiftool.json`), DB storage, and
   structured JSON export with provenance, legend, warnings and SHA-256.
5. Added `ui/metadata_tab.py` and wired it into `evidence_page.py` (Overview metadata status,
   Metadata tab, Integrity & history labels), `cases_page.py`, `pages.py` (Analysis page batch
   extract, Settings External tools card, About ExifTool notice), `dialogs.py` (`choose_save_file`)
   and `main_window.py`.
6. Added 26 new synthetic-fixture tests (`test_metadata.py`, `test_metadata_gui.py`) covering
   classification, timestamps, migration v2 → v3, real ExifTool reproducibility, missing TZ,
   conflicting EXIF/IPTC/XMP dates, no-EXIF files, malformed EXIF, untrusted HTML, JSON export,
   Analysis batch extract, and Settings ExifTool test/persistence (total: 192 tests).
7. Updated `tools/screenshot.py` to add synthetic EXIF tags and capture Metadata tab & Analysis
   page in light and dark themes. Tightened Metadata tab spacing so the table peeks above the fold.

### Verification
See `docs/test-reports/2026-11-24-phase4.md`: 192 passed, ruff clean (57 files), 34/34 env
checks, `start.bat` smoke-launched against a throwaway data root (migrated to schema v3).

### Known issues / limits
- ExifTool runs synchronously when clicking "Extract metadata" (~0.5 s for typical images, 30 s
  timeout); batch extraction on very large cases could be moved to a worker thread in Phase 9.
- GPS coordinate parsing/validation and optional map preview arrive in Phase 5.

### Next task
Phase 5 (GPS parsing, validation, local display, and optional opt-in map preview). No new
dependencies needed. Waiting for user review of Phase 4.

## 2026-11-24: Phase 5 (GPS parsing, validation & opt-in map preview)

The user approved Phase 5 (no new dependencies needed).

### Actions
1. Probed ExifTool 13.64's exact GPS formatting (`-n` raw unsigned `EXIF:GPS:GPSLatitude` +
   `GPSLatitudeRef`, unsigned `GPSAltitude` + `GPSAltitudeRef=1` vs signed `Composite:*`).
2. Schema v4 in `db.py`: added `gps_records` table and index.
3. Updated `settings.py`: added `allow_external_map: bool = False`.
4. Added `gps.py`: pure coordinate/altitude/timestamp parsers with range/NaN/hemisphere/Null-Island/
   conflict validation, `GpsService` (parses strictly from `origin='embedded'` records, stores
   `analysis_runs` + `gps_records` only when GPS exists, logs `gps_parsed` and
   `external_map_preview_loaded`), Web Mercator tile math, and guarded `fetch_osm_tile`.
5. Added `ui/gps_tab.py`: `LocalCoordinatePlot` (100% offline `QPainter` lat/lon graticule),
   `render_tile_preview` (in-memory 2×2 PNG composite), and `GpsTab` (capture-time warning,
   Copy coordinates, exact `"No GPS coordinates found in the examined file."` absent message,
   raw embedded GPS fields table, and on-demand opt-in external map preview with disclosure).
6. Wired `GpsService` and `GpsTab` into `evidence_page.py`, `cases_page.py`, `pages.py` (Analysis
   page + Settings Privacy & network opt-in toggle), and `main_window.py` (truthful status bar).
7. Added 19 new synthetic GPS tests (`test_gps.py`, `test_gps_gui.py`) including migration
   v3 → v4, strict `urlopen`-blocked offline verification, valid S/W + below-sea-level fixture,
   missing GPS exact wording, invalid/out-of-range/Null-Island/conflict edge cases, and opt-in
   disclosure decline/accept flows (total: 213 tests).
8. Updated `tools/screenshot.py` with synthetic Greenwich Royal Observatory coordinates and
   captured light/dark GPS tab screenshots. Fixed `&` mnemonic glitch on Analysis button.

### Verification
See `docs/test-reports/2026-11-24-phase5.md`: 213 passed, ruff clean (62 files), 34/34 env
checks, `start.bat` smoke-launched (migrated to schema v4).

### Known issues / limits
- Live public OpenStreetMap tile fetches were not exercised in automated offline tests (verified
  with mocked PNG tile fetcher + real `QImage`/`QPainter` compositing).
- OCR, analyst notes and reports arrive in Phases 6–8.

### Next task
Phase 6 (optional offline OCR with Tesseract). Waiting for user review of Phase 5 and decision on
installing Tesseract OCR on D: vs configurable path with manual installation guide.

## 2026-11-24: Phase 6 (optional offline OCR with Tesseract)

The user approved Phase 6. Per blueprint §2 and §3 (do not download or execute third-party
installers without explicit approval; Tesseract is optional and configurable with manual
language-pack guidance), implemented the full offline Tesseract CLI integration, setup guide,
and deterministic test suite without downloading an unapproved third-party installer.

### Actions
1. Schema v5 in `db.py`: added `ocr_runs` and `ocr_items` tables with indexes.
2. Updated `settings.py` (`tesseract_path`, `tessdata_dir`) and `tools_status.py` (`find_tesseract`).
3. Added `ocr.py`: strict language validation, Tesseract CLI version/languages/TSV subprocess
   helpers (no shell, timeouts, `CREATE_NO_WINDOW`), safe Pillow temp-copy preprocessing in
   `data/temp/` (cleaned up in `finally:`), resilient TSV parser (reconstructing lines/paragraphs,
   confidences, bounding boxes, low-confidence/empty/malformed warnings), `OcrService` (original
   SHA-256 verification, privacy-preserving audit events with zero OCR text), and JSON/TXT export.
4. Added `ui/ocr_tab.py` and `docs/user-guide/ocr-setup.md`; wired into `evidence_page.py`,
   `cases_page.py`, `pages.py` (Dashboard, Analysis, Settings External tools, About) and
   `main_window.py`.
5. Added 20 new OCR unit, migration v4 → v5, integration, privacy, malformed-image and GUI tests
   (`test_ocr.py`, `test_ocr_gui.py`; total: 233 tests).
6. Updated `tools/screenshot.py` to capture the OCR tab in light/dark themes. Visual review
   caught horizontal overflow on the 5-button OCR control bar; split into two compact rows.

### Verification
See `docs/test-reports/2026-11-24-phase6.md`: 233 passed, ruff clean (68 files), 34/34 env
checks, `start.bat` smoke-launched (migrated to schema v5).

### Known issues / limits
- Tesseract binary is optional and not yet installed on this workstation; installing it on D: per
  `docs/user-guide/ocr-setup.md` enables live OCR immediately.
- OCR runs synchronously on the UI thread (60 s timeout); moving long OCR runs to a worker thread
  can be evaluated in Phase 9.

### Next task
Phase 7 (analyst notes, confidence categories and chain-of-custody history). No new dependencies
needed. Waiting for user review of Phase 6.

## 2026-11-24: Phase 7 (analyst notes and history)

The user approved Phase 7 (no new dependencies required).

### Actions
1. Schema v6 in `db.py`: added `notes` table (`author_label`, `category`, `confidence_label`,
   `source_reference`, `status`, `supersedes_note_id`, UTC `created_at`/`updated_at`), index
   `idx_notes_case_evidence`, and SQLite trigger `notes_no_delete` blocking `DELETE FROM notes`.
2. Added `notes.py`: fixed 4 blueprint confidence labels (`Confirmed from file`, `Corroborated`,
   `Analyst observation`, `Unverified hypothesis`), mandatory citation enforcement for `Confirmed
   from file` and `Corroborated`, 6 visual-clue categories, and `NotesRepository` with append-only
   `create`, `revise` (supersedes prior entry and links new row), `retract` (never deletes), active
   confidence-level counts, and privacy-preserving audit events (`note_created`, `note_revised`,
   `note_retracted` with zero raw note text or citations).
3. Added `ui/notes_tab.py`: `NoteDialog` (conservative `Analyst observation` default, dynamic
   required-citation caption, buddy labels, inline validation) and `NotesTab` (visual-clue guidance
   banner, Add/Revise/Retract, confidence filter, `Show superseded / retracted` toggle, plain-text
   detail view with attribution & revision chain, and explicit append-only limitation notice).
4. Wired `NotesRepository` and `NotesTab` into `evidence_page.py` (Overview active notes row,
   Analyst notes tab, Integrity & history `Verify audit chain` button & limitation notice),
   `cases_page.py` (active notes count, `Verify audit chain` button & limitation notice),
   `pages.py` (`AnalysisPage` notes confidence breakdown & `Verify audit chain` button), and
   `main_window.py`.
5. Added unit, migration v5 → v6, append-only trigger, privacy, and GUI tests (`test_notes.py`,
   `test_notes_gui.py`) and updated `test_db.py` and `test_main_window.py` (total: 241 tests).
6. Updated `tools/screenshot.py` with synthetic notes and `NoteDialog` capture; inspected native
   light & dark screenshots for Notes tab, NoteDialog, Integrity & history, Analysis, and Cases.

### Verification
See `docs/test-reports/2026-11-24-phase7.md`: 241 passed, ruff clean (73 files), 34/34 env
checks, `start.bat` smoke-launched against a throwaway data root (migrated cleanly to schema v6).

### Known issues / limits
- Append-only protection on `notes` and `audit_events` is enforced at the application/SQLite-trigger
  level only; direct edits to the database file are not prevented unless files and backups are
  externally secured and validated (documented in-app and in `docs/security/security-model.md`).
- PDF and JSON case reports arrive in Phase 8.

### Next task
Phase 8 (PDF and JSON reports). Waiting for user review of Phase 7 and approval to install
`reportlab==5.1.0` (BSD license, ~5 MB into `D:\imagetrace\venv`) for offline PDF generation.

## 2026-11-24: Phase 8 (PDF and JSON reports)

The user approved Phase 8 and installing `reportlab==5.1.0`.

### Actions
1. Installed `reportlab==5.1.0` (BSD; plus transitive `charset-normalizer==3.5.3`, MIT) into
   `D:\imagetrace\venv` using D-drive pip cache/temp; pinned `reportlab==5.1.0` in
   `requirements.txt` and `pyproject.toml`.
2. Schema v7 in `db.py`: added append-only `exports` table (`id, case_id, evidence_id,
   export_type, path, sha256, actor_label, options_json, created_at`), index `idx_exports_case`,
   and trigger `exports_no_delete`.
3. Added `reports.py`: canonical JSON payload builder (`imagetrace.report.v1`),
   `validate_report_payload(...)`, offline ReportLab Platypus PDF renderer (`render_pdf_report`)
   with XML escaping (`_xml_safe`), 4-category blueprint legend, limitations/disclaimers, live
   original SHA-256 verification, GPS-only-when-present (raw + formatted + normalized), automated
   OCR caveats & optional full text, analyst notes, sanitized audit history, atomic `.partial`
   writes after >= 50 MiB free-space check, `.sha256` sidecars, and `verify_export(...)`.
4. Added `ui/reports_page.py` replacing the final `PlaceholderPage`; wired into `main_window.py`,
   `cases_page.py` (live Export history count/timestamp + audit labels), `evidence_page.py` (live
   Export history + audit labels), and `pages.py` (About page lists ReportLab 5.1.0 BSD).
5. Added comprehensive offline unit, migration v6 → v7, PDF escaping, tamper-verification, low-space,
   and GUI tests (`test_reports.py`, `test_reports_gui.py`); updated `test_db.py`,
   `test_main_window.py`, and forward-compatible migration check in `test_notes.py` (total: 246).
6. Updated `tools/screenshot.py` to generate synthetic PDF/JSON reports and capture light/dark
   Reports screenshots. Rendered and visually inspected all 4 pages of a synthetic PDF report;
   caught and fixed empty formatted GPS column key (`formatted`), empty metadata highlight rows,
   mid-word wrapping in body styles, and multi-line UTC audit timestamps.

### Verification
See `docs/test-reports/2026-11-24-phase8.md`: 246 passed, ruff clean (78 files), 34/34 env
checks, `start.bat` smoke-launched against a throwaway data root (migrated cleanly to schema v7),
native light/dark screenshots and 4-page synthetic PDF inspected.

### Known issues / limits
- PDF/JSON generation runs synchronously on the UI thread (fast for typical cases).
- ReportLab built-in Helvetica/Courier covers Latin-1; non-Latin Unicode OCR/notes preserve full
  UTF-8 in JSON and can gain a Windows TrueType font registration in Phase 9 if desired.

### Next task
Phase 9 (hardening: security & error-handling audit, malformed-file/stress tests, performance &
memory checks, session lock/timeout, and optional encrypted backups). Waiting for user review of
Phase 8 and decision on installing `cryptography==50.0.2` (optional encrypted backups) and/or
`pip-audit` (dev dependency audit).

## 2026-11-24: Phase 9 (hardening)

The user approved Phase 9, `cryptography==50.0.2`, and `pip-audit`.

### Actions
1. Installed `cryptography==50.0.2` (runtime; pinned in `requirements.txt` & `pyproject.toml`) and
   `pip-audit==2.10.1` (dev; pinned in `requirements-dev.txt` & `pyproject.toml`). Ran `pip-audit`
   and found 12 CVEs in the bundled venv `pip 25.0.1` (0 CVEs in app dependencies); upgraded venv
   `pip` to `26.3` so `pip-audit` reports **0 known vulnerabilities**.
2. Conducted full static & architectural security audit across `app/src/imagetrace/`:
   - Enforced `Qt.TextFormat.PlainText` on the 6 remaining direct `QLabel` instances across
     `main_window.py`, `evidence_page.py`, and `gps_tab.py`.
   - Hardened `evidence.py::sanitize_original_filename` against Windows reserved device names
     (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`9`, `LPT1`–`9`) and trailing dots/spaces.
   - Added optional Windows TrueType Unicode font registration (`arial.ttf`/`arialbd.ttf`/
     `cour.ttf`) in `reports.py` with clean Helvetica/Courier fallback.
   - Clamped `sys.excepthook` error dialog length in `app.py`.
3. Added `security.py` (salted scrypt passphrase hashing, constant-time verification with bounded
   work-factor check, best-effort `shred_and_unlink`, Windows reserved-name helper).
4. Upgraded `backup.py` with authenticated AES-256-GCM + scrypt encrypted backups (`.sqlite.itbak`,
   40-byte AAD-bound `ITBAK1` header, `.sha256` sidecar), passphrase-aware `verify_backup` and
   `restore_database`, offline `restore_corrupt_database_file`, and `latest_backup_age_days`.
   Documented container format & standalone recovery script in `docs/user-guide/backup-recovery.md`.
5. Added `ui/lock_ui.py` (`PassphraseDialog` and `LockOverlay`) and wired manual `Ctrl+L` lock,
   top-bar **Lock** button, single-shot idle auto-lock `QTimer` + `eventFilter`, backup reminders,
   and encrypted backup UI into `settings.py`, `dialogs.py`, `main_window.py`, and `pages.py`.
6. Added 19 new tests in `test_backup_encrypted.py` and `test_security_hardening.py` covering
   encrypted backups, tamper detection, corrupt-DB recovery, session lock/idle timer, adversarial
   file matrix, Unicode/hostile PDF export, AST static security invariants, runtime PlainText
   verification, and 20-image `tracemalloc` memory bounds (< 128 MiB) (total: 265 tests).
7. Published `docs/security/phase9-security-review.md` and `docs/test-reports/2026-11-24-phase9.md`;
   captured & visually inspected light/dark `locked.png`, `settings.png`, and `about.png`.

### Verification
See `docs/test-reports/2026-11-24-phase9.md`: 265 passed, ruff clean (84 files), `pip-audit` 0
vulnerabilities, 34/34 env checks, `start.bat` smoke-launched cleanly (schema v7).

### Known issues / limits
- Documented explicitly in `docs/security/phase9-security-review.md`: app-level append-only logs
  and session lock complement (never replace) Windows account security and BitLocker full-disk
  encryption; lost encrypted-backup passphrases have no recovery backdoor.

### Next task
Phase 10 (Windows packaging: portable one-dir build, third-party license bundle, user guide,
release notes, clean-environment test). Waiting for user review of Phase 9 and approval to install
`pyinstaller==6.22.3` (dev-only) into `D:\imagetrace\venv`.

## 2026-11-24: Phase 10 (Windows packaging)

The user approved Phase 10 and installing `pyinstaller==6.22.3` (dev-only).

### Actions
1. Installed `pyinstaller==6.22.3` (dev-only) into `D:\imagetrace\venv` using D-drive caches;
   pinned in `requirements-dev.txt` and `pyproject.toml`; re-ran `pip-audit` (0 vulnerabilities).
2. Updated `paths.py` (`is_frozen()`, frozen-aware `project_root()`, `resources_dir()`) and
   `ui/theme.py` so frozen builds resolve `imagetrace.local.json`, bundled ExifTool, and SVG icons
   cleanly from the portable directory and `_internal/resources/`.
3. Added `tools/launch_frozen.py`, `tools/build_portable.py` (`--onedir --windowed`, keeping Qt
   DLLs dynamically linked for LGPL-3.0 compliance, excluding `QtWebEngine*` and unused Qt add-ons,
   generating `MANIFEST.sha256`), and `tools/Uninstall-KeepData.cmd` (CRLF, removes app files while
   preserving `data\` and `imagetrace.local.json` by default).
4. Published `THIRD_PARTY_LICENSES.txt`, `RELEASE_NOTES.md` (v0.1.0), and
   `docs/user-guide/getting-started.md`.
5. Added `app/tests/test_packaging.py` (frozen path/resource/icon resolution, license/guide checks,
   automated `Uninstall-KeepData.cmd` preservation test; total: 268 tests).
6. Built the portable one-directory Windows release (`build\dist\ImageTrace\ImageTrace.exe`,
   168.8 MB incl. ExifTool 13.64) and verified it in a clean isolated folder with a separate
   `UserData` directory (launched standalone, created & migrated DB to schema v7, dynamically
   linked Qt DLLs present, 0 WebEngine DLLs, `Uninstall-KeepData.cmd` removed program files while
   keeping `UserData` intact).

### Verification
See `docs/test-reports/2026-11-24-phase10.md`: 268 passed, ruff clean (91 files), `pip-audit` 0
vulnerabilities, 34/34 env checks, portable clean-machine & safe-uninstall verification passed.

### Next task
Phase 11 (improvement cycle: review user feedback & test findings and prioritize reliability,
usability, privacy, and defensible evidence handling). Waiting for user review of Phase 10.

## 2026-11-25: Phase 11 (improvement cycle — v0.1.1)

The user approved Phase 11 (no new dependencies needed).

### Actions
1. Reviewed all Phases 0–10 known limits and test reports and created prioritized roadmap issues
   `IT-001`–`IT-008` in `ROADMAP.md`.
2. Implemented `IT-001` & `IT-002`: added `ui/batch_workers.py` (`BatchAnalysisWorker`,
   `BatchIntegrityWorker`, `BatchProgressDialog`) following ADR-018 (dedicated per-thread SQLite
   connection, clean cancellation between items) and wired both into `AnalysisPage`.
3. Implemented `IT-003`: added Dashboard **Storage and backup health** live status + quick-action
   navigation buttons (`Open Cases`, `Open Evidence`, `Run Analysis`, `Generate Reports`), `F5`
   current-page refresh shortcut on `MainWindow`, and non-blocking missing-metadata readiness hint
   on `ReportsPage`.
4. Implemented `IT-004`: added `tools/make_icon.py` generating multi-resolution
   `app/resources/imagetrace.ico` and `imagetrace.png` via Pillow, set as `QApplication`/
   `MainWindow` icon and embedded into `ImageTrace.exe` via `tools/build_portable.py`.
5. Bumped version to `0.1.1` (`__init__.py`, `pyproject.toml`, `RELEASE_NOTES.md`, `CHANGELOG.md`),
   added `app/tests/test_phase11_improvements.py`, updated `test_packaging.py` (total: 272 tests),
   inspected native light/dark screenshots, and rebuilt + clean-machine verified portable `v0.1.1`
   (`build\dist\ImageTrace\ImageTrace.exe`, 168.9 MB).

### Verification
See `docs/test-reports/2026-11-25-phase11.md`: 272 passed, ruff clean (95 files), `pip-audit` 0
vulnerabilities, 34/34 env checks, portable `v0.1.1` clean-machine & safe-uninstall verified.

### Status
All Blueprint Phases 0–11 implemented and verified (`v0.1.1`).

## 2026-11-25: v0.2.0 — Advanced Origin & Trace Forensics

Following Phase 11 review, the user requested deeper image-origin tracing (mobile camera vs. real
camera vs. Photoshop/Illustrator, step-by-step history from initial capture through computer edits,
and reverse-image lookup). After honestly clarifying technical & legal limits (image files never
store IP addresses or messaging-app forwarding chains), the user approved building an advanced
offline **Origin & Trace** module.

### Actions
1. Built six-layer offline forensic provenance engine (`app/src/imagetrace/provenance.py`, zero new
   dependencies, persisted in existing `analysis_runs` table with `module="origin_trace"`):
   - Device/software/AI/C2PA/screenshot/stripped classifier with strict separation of
     `Extracted fact` vs. `Automated heuristic clue`.
   - Deep binary container parser for JPEG `APP0`–`APP14` (`JFIF`, `EXIF`, `XMP`, `ICC_PROFILE`,
     `MPF`, `APP11:JUMBF/C2PA`, `APP12:Ducky`, `APP13:Photoshop 3.0 IRB`, `APP14:Adobe`, `COM`),
     DQT quantization quality estimator, `SOF0`/`SOF2` & chroma subsampling, PNG `tEXt`/`iTXt`
     chunks (Stable Diffusion `parameters`, ComfyUI `prompt`/`workflow`), and trailing data
     detection after JPEG `EOI` / PNG `IEND` (`MP4`, `ZIP`, `PDF`, `PE`, `HTML`, unknown binary).
   - Step-by-step embedded history timeline (`Step 1 -> End`) parsing EXIF capture tags, raw XMP
     `xmpMM:History` XML packets, ExifTool sidecar arrays, `DocumentID`/`OriginalDocumentID`/
     `DerivedFrom`, and OS/platform hints (`iOS/iPadOS`, `Android`, `Windows`, `macOS`, `Linux`).
   - Embedded EXIF thumbnail vs. main image aspect-ratio and 64-bit dHash mismatch detector.
   - Pure-Pillow in-memory ELA preview (`compute_ela`), metadata-stripped JPEG copy exporter
     (`save_stripped_copy`), and opt-in manual public reverse-search helpers (zero auto-uploads).
2. Built `ui/provenance_tab.py` and wired `ProvenanceService` & `ProvenanceTab` into
   `evidence_page.py` (Tab 1 right after `Overview`, auto-refreshing on metadata extraction),
   `cases_page.py`, `batch_workers.py` (`BatchAnalysisWorker`), `main_window.py`, and `reports.py`
   (JSON `origin_trace` block + ReportLab PDF assessment & timeline table).
3. Updated existing GUI test tab indices, added `test_provenance.py` and `test_provenance_gui.py`,
   updated `tools/screenshot.py` with a synthetic iPhone → Photoshop → Illustrator XMP fixture,
   visually inspected native light/dark screenshots (fixing capture OS precedence over edit OS and
   compact button/2×2 reverse-search layout to eliminate right-edge clipping), bumped version to
   `0.2.0`, updated docs, and rebuilt + clean-machine verified portable `v0.2.0`.

### Verification
See `docs/test-reports/2026-11-25-v0.2.0-origin-trace.md`.

## 2026-11-25: v0.3.0 — Deep Forensics, HEIC/RAW, Zoom/Compare Viewer, Correlation & Visual Bundles

The user approved the full three-milestone `v0.3.0` implementation plan (`zero new dependencies`).

### Actions
1. **Milestone 1 (Deep Forensics):** created `app/src/imagetrace/forensics.py` (DQT quantization
   table hashing & heuristic profile matching, offline C2PA/JUMBF claim parser, pure-Pillow Median
   Noise Residual, Luminance Gradient, RGB/YCbCr Channel Split, JPEG Ghost curve, safe embedded
   EXIF thumbnail & trailing Motion Photo MP4 extractors); upgraded `provenance.py` to v3.0
   (MakerNotes hardware serial/shutter IDs, DQT & C2PA claims, audit-logged payload extractors) and
   upgraded `ui/provenance_tab.py` with the interactive Visual Forensics Lab (`JpegGhostChart`),
   side-by-side EXIF thumbnail strip, and promoted timeline.
2. **Milestone 2 (Formats, Viewer & Workers):** expanded `image_analysis.py` to detect & safely
   import HEIC/HEIF and Camera RAW (`DNG`, `CR2`, `CR3`, `NEF`, `ARW`) using bundled ExifTool
   embedded preview extraction under the megapixel ceiling; created `ui/viewer_dialog.py`
   (interactive `25%–800%` zoom/pan loupe with live RGB readout and synchronized side-by-side
   comparison); added collapsible `Expand details` toggle on `EvidencePage` and `SingleOcrWorker` /
   `PdfReportWorker` in `batch_workers.py` (`IT-005`).
3. **Milestone 3 (Correlation, Search & Visual Bundles):** created `correlation.py`,
   `ui/correlation_widget.py`, and `ui/search_dialog.py` (`Ctrl+K` / `Find…` global search);
   wired Case Correlation (XMP lineage, Serial/Device/DQT/dHash clusters, chronological timeline
   with shutter-count anomalies, multi-image offline GPS plot) into `AnalysisPage`; upgraded
   `reports.py` & `ui/reports_page.py` with visual PDF thumbnails + side-by-side EXIF mismatch
   comparisons and verifiable `.zip` case bundle exports (`MANIFEST.sha256`, never leaking SQLite
   DBs).
4. Added `test_v030_forensics.py` and `test_v030_gui.py`, visually inspected native light/dark
   screenshots (including Zoom/Compare Viewer, Global Search, Case Correlation, and Reports),
   rebuilt & clean-machine verified portable `v0.3.0`, and ran full verification.

### Verification
See `docs/test-reports/2026-11-25-v0.3.0.md`.
