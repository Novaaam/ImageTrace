# ImageTrace — Complete Development Blueprint
**Version:** 1.0  
**Target:** Windows 10/11 desktop, primarily local/offline use  
**Project drive:** `D:\ImageTrace` (never use `C:\` for project data, downloads, logs, build output, databases, caches, or virtual environments unless a Windows/tool dependency cannot be relocated; explain and minimize such exceptions)

---

## 1. Vision and scope

ImageTrace is a local-first digital image evidence examination application. It accepts user-supplied images and analyzes them for authentic, technically verifiable information, including metadata, embedded GPS coordinates (if present), image/file characteristics, visible text and contextual clues, integrity hashes, and evidence-management/reporting.

**It is not a person-tracking or deanonymization tool.** It must not attempt to obtain a sender's IP address from an image, access accounts/devices/networks without authorization, covertly track a person, identify private individuals, infer a home address from facial recognition, or bypass platform/provider protections. The application must distinguish extracted facts, analyst observations, automated suggestions and speculation. It must never present guesses as confirmed findings.

### Core objectives
- Desktop GUI that is intuitive, modern, and usable on Windows.
- Work offline for core functions.
- Preserve the original evidence file unchanged.
- Extract available EXIF/IPTC/XMP and other metadata.
- Display GPS coordinates only if actually embedded in the examined file; map visualization is optional and must not send coordinates to third parties by default.
- Analyze visible text using OCR, with results labelled as machine-generated.
- Provide analyst-assisted visual clue notes; no automatic identification of private people.
- Compute SHA-256 hashes and document chain-of-custody events.
- Export a readable PDF and structured JSON report.
- Maintain a case/evidence organization system.
- Use D-drive storage by default and verify free space.
- Develop in small phases with tests, verification, and written logs.

## 2. Repository and D-drive layout

Create and use:

`D:\ImageTrace\`

Suggested structure:

```
D:\ImageTrace\
  README.md
  BLUEPRINT.md
  CHANGELOG.md
  ROADMAP.md
  DEVELOPMENT_LOG.md
  DECISIONS.md
  .gitignore
  app\
    src\
    tests\
    resources\
  docs\
    architecture\
    user-guide\
    security\
    test-reports\
  data\
    cases\
    database\
    exports\
    logs\
    backups\
    temp\
  tools\
    third-party\
  build\
  venv\
```

- Create the virtual environment at `D:\ImageTrace\venv`.
- Keep all app-created files under `D:\ImageTrace\` by default.
- Set temporary directory/environment variables for app/build workflows to D-drive paths where possible.
- Do not install or write large packages/caches to C-drive if an alternative can be configured.
- Windows and some installer/runtime components may still use C-drive. Before using such a component, explain why, check whether its location can be changed, and minimize its footprint.
- Never delete, clean, or overwrite the user's files without explicit confirmation.
- Keep third-party binaries in `tools\third-party\`, document their version, origin and license, and do not download or execute them without the user's approval.

## 3. Technology choices

Use a Python desktop stack, subject to a phase-0 compatibility check:

- Python 3.11 or a currently supported version compatible with chosen dependencies.
- PySide6 for the GUI.
- SQLite for local case/evidence records.
- ExifTool (bundled or user-provided executable) as the preferred broad metadata extraction engine. Document version and license; verify source and checksums when obtaining it.
- Pillow for image decoding and safe thumbnail generation.
- OpenCV only for explicitly scoped image processing and optional visual analysis.
- OCR: Tesseract as an optional, configurable component; make it offline and user-invoked. Include language-pack installation guidance rather than silently downloading packs.
- PDF reports: choose a maintained PDF library after checking compatibility/licensing.
- Map: optional local/offline map view or a user-configured provider. No coordinates are transmitted by default. If external map tiles are enabled, disclose what information may be sent and require opt-in.
- pytest for tests.
- Ruff (or equivalent) for linting and formatting.
- PyInstaller (or an alternative selected after evaluation) for packaging.

Before settling dependencies, Claude must verify supported versions, compatibility, licenses, and Windows support, record the findings in `docs/architecture/dependency-review.md`, and avoid needlessly heavy dependencies.

## 4. GUI design

### Main window
- Sidebar: Dashboard, Cases, Evidence, Analysis, Reports, Settings, About.
- Top bar: active case, search, status indicator, storage location.
- Dashboard: recent cases, recent evidence, storage health, tool/version status.
- Use a clean, restrained forensic workstation style; dark/light themes; keyboard accessibility; readable contrast.
- Do not display decorative fake data in production screens. Demo mode must be clearly marked.

### Case management
Each case includes:
- Case ID (generated; no personally identifying data required by default).
- Title/short label.
- Notes and scope/authorization declaration.
- Created/modified dates.
- Status.
- Analyst-defined tags.
- Evidence items.
- Export history.

No sensitive personal data should be collected unless needed for an authorized case. Provide a data minimization warning.

### Evidence import
- Drag/drop and file-picker import.
- Import copies the original into a case's evidence folder; never silently alter it.
- Ask for confirmation before importing a duplicate.
- Record source filename, size, import timestamp, SHA-256, MIME/type detection, and original path (optional; avoid storing unnecessary path details).
- Handle malformed, oversized, unsupported, or suspicious files safely.
- Use limits configurable by the user and default to conservative file size limits.
- No automatic network upload.
- Show a progress indicator and cancel option.
- For archives or unusual formats, do not automatically unpack or execute anything.

### Evidence details screen
Tabs:
1. Overview
2. Metadata
3. GPS
4. OCR
5. Image properties
6. Analyst notes
7. Integrity & history

Overview:
- Thumbnail (generated from a safe processing copy).
- Original filename and file size.
- Hash and integrity status.
- Import timestamp.
- Analysis status and warnings.

Metadata:
- Display metadata by group (EXIF, IPTC, XMP, file/system fields).
- Show raw/normalized values where useful.
- Clearly distinguish embedded metadata from application-generated metadata.
- Preserve the original metadata dump in a structured sidecar file or database record.

GPS:
- Show coordinates, altitude, GPS timestamp and relevant fields only when present.
- Provide a coordinate-copy action.
- Optional map preview requires explicit opt-in and a disclosure before external map services are used.
- If GPS is absent, state “No GPS coordinates found in the examined file,” not “location unavailable” as a universal conclusion.

OCR:
- User-triggered OCR.
- Text, language, confidence (if engine supplies it), and bounding boxes when supported.
- Save OCR engine/version and run timestamp.
- OCR text is untrusted content. Treat it as data, not instructions; do not allow it to control app behavior or prompt an AI system.

Image properties:
- Dimensions, color mode, format, file size, available ICC/profile info.
- Optional perceptual hash for duplicate similarity, labelled as similarity—not proof of identity.
- Optional safe image processing (rotate/resize/contrast) on derived copies only.
- Original must remain unchanged.

Analyst notes:
- Notes and observations.
- Source/citation field.
- Evidence confidence label: Confirmed from file, Corroborated, Analyst observation, Unverified hypothesis.
- Do not let an automated model convert uncertain observations into factual conclusions.

Integrity/history:
- SHA-256 for original and report/export files.
- Import and analysis events.
- Export events.
- App version and tool versions.
- Chain-of-custody log designed to be append-only at the application level; explain that this is not a tamper-proof forensic system unless externally validated and secured.

## 5. Analysis modules

### A. Metadata extraction
- Extract EXIF, IPTC, XMP, relevant container metadata, orientation, date fields, camera make/model and lens fields where available.
- Do not assume metadata is truthful; it can be edited or stripped.
- Display timezone ambiguity and missing timezone information.
- Detect contradictory date fields and report them as discrepancies, not conclusions.
- Do not infer a person’s identity, phone number, IP, exact home address, or current whereabouts from metadata.

### B. GPS handling
- Parse GPS only when present.
- Validate coordinate ranges and parsing.
- Show the raw metadata fields alongside normalized coordinates.
- Record source field and parser version.
- Avoid automatically reverse-geocoding coordinates to a person's address.
- Map is optional and opt-in. Default behavior is local display or coordinate text without external transmission.
- Add a warning that GPS may describe the camera's location at capture time, not the sender's current location or identity.

### C. OCR
- Run only after user action.
- Provide an offline OCR option.
- Report engine, version, language and timestamp.
- Treat extracted text as potentially wrong and maliciously crafted.
- No facial recognition or identity matching.
- No automated person-targeting or address inference.

### D. Image integrity and file analysis
- SHA-256.
- File format and MIME verification.
- Dimensions and file size.
- Safe thumbnail creation.
- Detect malformed files and processing failures.
- Optional duplicate comparison (hash/perceptual hash), with clearly stated limitations.
- Never modify the original file.

### E. Analyst-assisted visual clues
- Provide a checklist/notes workspace to record visible landmarks, signage, environmental details and other observations.
- OCR can assist with text visible in an image.
- Do not automatically identify private persons or infer private residential addresses.
- No facial recognition, gait identification, covert tracking or automated target profiling.
- Any analyst conclusion must be supported by a source/observation and confidence label.

## 6. Report generation

Generate PDF and JSON reports with:
- Case ID and case label.
- Evidence ID.
- File name, file size, format, dimensions.
- SHA-256.
- Import date/time (with timezone or explicit timezone-unknown note).
- Metadata extraction summary.
- GPS section only if data exists; include raw fields and normalized values.
- OCR summary and full OCR text optionally, with confidence caveat.
- Image properties.
- Analyst notes and confidence labels.
- Tool names and versions.
- Limitations and warnings.
- Event/history summary.
- Report generation time and app version.

Reports must not make unsupported conclusions. Use a clear legend:
- **Extracted fact:** directly present in the file or derived by deterministic parsing.
- **Automated result:** generated by a tool and needs review.
- **Analyst observation:** recorded by the examiner.
- **Hypothesis:** unverified interpretation.

## 7. Database design

Use SQLite with migrations and foreign keys. Proposed tables:

- `cases`: id, title, notes, scope_text, status, created_at, updated_at
- `evidence`: id, case_id, stored_filename, original_filename, file_size, detected_format, sha256, imported_at, import_source_note, status
- `metadata_records`: id, evidence_id, group_name, field_name, raw_value, normalized_value, source_tool, source_version, extracted_at
- `gps_records`: id, evidence_id, raw_fields_json, latitude, longitude, altitude, gps_timestamp, source_field, extracted_at
- `ocr_runs`: id, evidence_id, engine, engine_version, language, run_at, status
- `ocr_items`: id, ocr_run_id, text, confidence, bounding_box_json
- `analysis_runs`: id, evidence_id, module, version, started_at, finished_at, status, output_json, warnings_json
- `notes`: id, case_id, evidence_id, note_text, category, confidence_label, source_reference, created_at, updated_at
- `audit_events`: id, case_id, evidence_id, event_type, event_time, actor_label, details_json, previous_event_hash, event_hash
- `exports`: id, case_id, evidence_id, export_type, path, sha256, created_at
- `settings`: key, value, updated_at

Database requirements:
- Foreign keys enabled.
- Schema migration mechanism.
- Transactional writes.
- Backup and restore.
- Integrity check.
- No secrets stored in plaintext.
- Avoid unnecessary personal data.
- Never silently discard data on migration failure.

## 8. Storage and backup strategy

Default root: `D:\ImageTrace\data\`

Suggested:
- `cases\<case-id>\originals\`
- `cases\<case-id>\derived\`
- `cases\<case-id>\reports\`
- `cases\<case-id>\metadata\`
- `database\`
- `exports\`
- `logs\`
- `backups\`
- `temp\`

Requirements:
- Check D-drive availability and free space before import, processing and export.
- If D-drive is missing, do not silently fall back to C-drive. Ask the user to choose another location.
- Warn when free space is low.
- Configurable backup schedule and retention.
- Backups should be optionally encrypted, with recovery instructions.
- Provide backup verification and restore testing.
- Avoid storing temporary data outside the configured data root; if a dependency requires it, document the exception and clean it safely.
- Keep originals, derived files, reports and backups separated.
- Never delete backups automatically without clearly configurable retention and user confirmation.

## 9. Security and privacy requirements

- Local-first; network access disabled for core workflows.
- No telemetry by default.
- No upload of evidence to cloud AI or third-party APIs.
- No auto-opening imported files in external applications.
- No execution of content embedded in evidence.
- Safe file handling and path validation.
- Prevent path traversal and unsafe filenames.
- Restrict processing of unsupported file types.
- Handle decompression bombs and oversized files.
- Protect SQLite/database backups.
- Avoid logging raw sensitive evidence or OCR text by default.
- Use a lock/timeout option for the application.
- Document that Windows user account security and full-disk encryption matter.
- Provide a clear consent prompt before any external map or optional network feature.
- Treat all imported metadata and OCR text as untrusted.
- Security testing must include malformed inputs and dependency review.

## 10. Testing

Unit tests:
- EXIF parser normalization.
- GPS parser and coordinate validation.
- Hash computation.
- Database migrations and transactions.
- Path safety and storage-root handling.
- OCR-result parsing.
- Report generation.
- Duplicate detection limitations.

Integration tests:
- Import valid image.
- Import image with GPS metadata.
- Import image without metadata.
- Import image with malformed metadata.
- Import unsupported file.
- Import large file and cancellation.
- Generate PDF/JSON.
- Backup and restore.
- D-drive missing or low-space scenarios.

GUI tests:
- Case creation.
- Evidence import.
- Metadata screen.
- GPS screen.
- OCR run.
- Notes.
- Export.
- Settings.
- Error states.

Test fixtures:
- Use synthetic, non-sensitive images created specifically for testing.
- Do not use real people's private images as fixtures.
- Test metadata may be fabricated, but fixtures must be clearly labelled synthetic.
- Include images with EXIF, no EXIF, GPS, invalid GPS, conflicting timestamps and malformed metadata.

## 11. Development phases and acceptance criteria

### Phase 0 — Environment and planning
- Inspect Windows, Python availability, D-drive, disk space.
- Confirm D-drive path.
- Dependency/licensing review.
- Create repo, venv, docs and roadmap.
- No application features yet.

**Acceptance:** directory structure exists on D-drive; Python/venv works; dependency review is documented; no project data is written to C-drive except documented unavoidable system components.

### Phase 1 — GUI shell
- PySide6 main window.
- Sidebar and navigation.
- Dashboard placeholders clearly marked as empty.
- Theme and settings skeleton.

**Acceptance:** app launches, navigation works, no fake forensic data, tests pass.

### Phase 2 — Cases and database
- SQLite, migrations.
- Create/edit/archive cases.
- Case list/search.
- D-drive storage validation.

**Acceptance:** case data persists across restart; database tests pass; backup works.

### Phase 3 — Evidence import and integrity
- Import workflow.
- Safe storage.
- SHA-256.
- Basic file properties.
- Duplicate handling.

**Acceptance:** original preserved; hash reproducible; import errors handled; D-drive space checked.

### Phase 4 — Metadata
- ExifTool integration.
- Metadata display, structured storage, export.
- Metadata warnings.

**Acceptance:** synthetic fixture tests pass; metadata extraction is reproducible and source/version recorded.

### Phase 5 — GPS
- GPS parsing and validation.
- Local coordinate display.
- Optional map preview with explicit external-service disclosure.

**Acceptance:** valid GPS fixture displayed correctly; missing GPS clearly reported; no external transmission by default.

### Phase 6 — OCR
- Optional offline OCR setup.
- OCR run screen.
- Results and provenance.
- OCR text export.

**Acceptance:** OCR results labelled as automated; engine/version recorded; malformed inputs handled; OCR text treated as untrusted.

### Phase 7 — Analyst notes and history
- Notes, confidence categories, audit history.
- Append-only behavior at application level.

**Acceptance:** notes are attributable and timestamped; event history is consistent; limitations documented.

### Phase 8 — Reports
- PDF and JSON.
- Report validation.
- Export hashes and history.

**Acceptance:** report contains provenance, limitations and evidence hashes; exports verified.

### Phase 9 — Hardening
- Error handling.
- Security review.
- Malformed-file testing.
- Performance and memory tests.
- Backup restore tests.

**Acceptance:** critical issues fixed; test report documented; no unresolved critical/high severity issues without explicit explanation.

### Phase 10 — Packaging
- Windows packaging.
- Installer or portable build.
- Version and release notes.
- Dependency licenses.
- User guide.

**Acceptance:** clean-machine test; installation path/data path choices documented; uninstall does not delete case data by default.

### Phase 11 — Improvement cycle
- Review user feedback and test findings.
- Create issues in roadmap.
- Prioritize reliability, usability, privacy and defensible evidence handling.
- Never add invasive person-tracking capabilities.

## 12. Claude's autonomous project workflow

Claude should follow this workflow whenever working on the project:

1. Read `BLUEPRINT.md`, `ROADMAP.md`, `CHANGELOG.md`, `DEVELOPMENT_LOG.md`, `DECISIONS.md`, README and current test reports before changing anything.
2. Inspect the actual repository and current state. Do not assume a phase is complete based only on documentation.
3. Identify the current phase and the smallest next task.
4. Create a short plan and define acceptance criteria.
5. Make changes only within `D:\ImageTrace\` by default.
6. Before large changes, create a backup or version-control checkpoint.
7. Implement one focused task at a time.
8. Run tests, linting and applicable manual verification.
9. Fix failures before proceeding; do not hide, skip or weaken tests to claim success.
10. Inspect the GUI visually when GUI changes are made.
11. Update documentation, roadmap, changelog and development log.
12. Record files changed, commands run, test outcomes, known issues, decisions and next task.
13. Check D-drive free space and confirm no unintended C-drive project files/caches were created.
14. If a task requires a network service, external API, data upload, purchase, elevated permission, destructive action, or installation of a new dependency, explain and ask for approval first.
15. Do not delete or overwrite user data, evidence, backups or unrelated files without explicit approval.
16. If blocked, explain the exact blocker and propose safe alternatives; do not fabricate success.
17. After a phase passes acceptance criteria, mark it complete in the roadmap and move to the next phase only after reporting the results.
18. Keep improvements grounded in test findings and user feedback, not endless speculative feature additions.

## 13. Documentation files

- `README.md`: overview, setup, launch and safety.
- `BLUEPRINT.md`: this document.
- `ROADMAP.md`: phases, status, next tasks and acceptance criteria.
- `CHANGELOG.md`: user-facing changes by version.
- `DEVELOPMENT_LOG.md`: chronological work log.
- `DECISIONS.md`: architectural decision records.
- `docs/architecture/dependency-review.md`: versions, licenses and compatibility.
- `docs/security/security-model.md`: privacy, threat model, limitations.
- `docs/user-guide/`: installation, cases, importing, analysis, reports, backup/restore.
- `docs/test-reports/`: dated test reports.

## 14. Definition of done for a release

A release is not considered complete until:
- All phase-specific acceptance criteria pass.
- Tests pass and results are recorded.
- No critical/high security issues remain unresolved without documented mitigation and explicit acknowledgement.
- Original evidence is preserved and integrity hashes are verified.
- Backup and restore have been tested.
- Reports clearly distinguish facts, automated output and observations.
- User guide and changelog are updated.
- Packaging is tested on a clean Windows environment.
- Project files and app data remain on D-drive by default.
- No unapproved external data transfer occurs.

## 15. First instruction to the coding agent

Begin with **Phase 0 only**. Inspect the environment and D-drive, create the directory structure and documents, verify Python and the D-drive virtual environment, and write the dependency review. Do not implement later phases until Phase 0 is verified and reported. Keep a complete, truthful log of every command, action, file change, test, blocker and decision. Use the entire blueprint as the controlling specification; if a proposed feature conflicts with the scope or security requirements, stop and explain before proceeding.
