# Security & Privacy Model (initial draft, Phase 0)

This draft gets updated as features are added. It is a design statement, not a security audit.

## Scope boundaries (non-negotiable)
ImageTrace will not do any of the following: get IP addresses from images; access accounts,
devices or networks; facial recognition, gait or identity matching; reverse-geocode coordinates
to a person or residence; covert tracking; automated target profiling.

## Assets
Original evidence files · derived copies · case database · reports/exports · backups · logs.

## Threats and planned mitigations
| Threat | Mitigation (phase) |
|--------|-------------------|
| Malicious or malformed image (parser exploits, decompression bombs) | Size limits, Pillow `MAX_IMAGE_PIXELS`, decode in a guarded worker with timeouts, keep dependencies up to date (3, 9) |
| Path traversal or unsafe filenames | App-generated stored filenames; resolved-path containment checks under the data root (2, 3) |
| Accidental change to originals | Copy on import, SHA-256 before and after, read-only attribute on stored originals, derived copies only (3) |
| Untrusted metadata or OCR text (injection into UI, reports, logs) | Treat as data; escape in PDF/HTML; never pass to a shell or AI system; no `shell=True` (4, 6, 8) |
| Data leaving the machine | No telemetry; no network in core flows; map tiles need explicit opt-in and a disclosure (5) |
| Silent data loss | Transactional writes, migrations with backup-before-migrate, append-only audit at app level (2, 7) |
| Disclosure through backups or logs | Optional encrypted backups; no raw OCR text or evidence in logs by default (2, 9) |
| Writing to unintended drive | Data root required; no silent C: fallback; free-space checks (2, 3) |

## Limitations (to be stated in-app and in reports)
- The hash-chained audit log is append-only at the application level only. Anyone with
  file-system access can change it. It is not tamper-proof unless externally secured and validated.
- Metadata can be forged or stripped. GPS shows where the camera reported it was at capture time,
  not the sender's identity or current location.
- OCR output is automated and may be wrong.
- Overall protection depends on Windows account security and full-disk encryption (e.g. BitLocker).

## Implemented controls (Phase 2)
- **Database:** foreign keys on every connection; explicit transactions; parameterised SQL
  (dynamic column names come only from a fixed whitelist); migrations back up first and never
  discard data; newer schemas refused.
- **Path safety:** case IDs are generated (`C-YYYYMMDD-XXXXXX`), validated by regex, and the
  resolved case folder must be a direct child of `cases/`.
- **Injection into the UI:** every label and message box shows text as **plain text**, so
  user-entered titles or notes can't inject HTML or links.
- **Audit trail:** hash-chained events; UPDATE and DELETE blocked by triggers; `Verify integrity`
  checks the chain. Details record field names and IDs only, never case text.
- **Logs:** `data/logs/imagetrace.log` records IDs, event types and file names only.
- **Backups:** consistent online copies, integrity-checked before being kept; SHA-256 sidecar;
  restore verifies first and always keeps a pre-restore copy; nothing deleted automatically.
  **Not encrypted:** anyone with access to `data/backups/` can read the case data.
- **Storage:** no silent fallback when the data root is missing; choosing a folder on C: needs
  an extra confirmation.
- **Data minimisation:** the case dialog warns against entering personal data; the analyst label
  can be a role or initials.

## Implemented controls (Phase 3)
- **Original preservation:** copies chunk-by-chunk to `.partial`, verifies SHA-256 matches source,
  atomically renames to an app-generated filename, marks read-only, and re-checks SHA-256 after
  analysis. Thumbnails (PNG, stripping EXIF) and derived viewing copies go only to `derived/`.
- **Input filtering & decompression-bomb defence:** magic-byte whitelist (JPEG/PNG/WebP/TIFF/BMP/GIF);
  archives, executables and scriptable SVG/XML/HTML are rejected without unpacking or running;
  configurable file-size (default 50 MB) and megapixel (default 50 MP) ceilings enforced before
  decoding pixel buffers.
- **Privacy on import:** source directory paths are never recorded automatically (only sanitised
  basename plus optional analyst note).
- **Safe path containment:** evidence IDs, stored filenames and thumbnail names are validated by
  strict regexes and verified as direct children of `originals/` or `derived/`.
- **Storage safety:** checks data-root availability and free space (`2× file size + 100 MB`)
  before copying; never falls back to C:; cleans up partial/newly-created files on cancel or
  database failure.
- **On-demand integrity verification:** recomputes stored original SHA-256 and logs the result to
  the hash-chained audit trail.

## Implemented controls (Phase 4)
- **Verified third-party binary:** ExifTool 13.64 zip verified against the published SHA-256 on
  `exiftool.org/checksums.txt` before extraction to `tools/third-party/exiftool/` (git-ignored).
- **Safe subprocess execution:** ExifTool is invoked with explicit argument lists, never
  `shell=True`, `--` before the file path, explicit timeouts (10 s version / 30 s extract), and
  `CREATE_NO_WINDOW`. Original file SHA-256 is verified unchanged after extraction.
- **Untrusted metadata handling:** control characters are stripped on display strings; all UI
  labels and table cells render plain text (no HTML evaluation); metadata is never executed or
  forwarded to an AI/network service.
- **Path privacy:** `File:System:Directory`, `FilePermissions`, `FileAccessDate` are omitted and
  `SourceFile` is replaced with `<stored_filename>` in sidecars and JSON exports.
- **Epistemic honesty:** timezone gaps and conflicting dates are surfaced as factual discrepancies
  with an explicit disclaimer that metadata can be edited, forged or stripped.

## Implemented controls (Phase 5)
- **Zero external transmission by default:** GPS parsing, validation and the local coordinate
  graticule plot run 100% offline (verified with `urllib.request.urlopen` hard-blocked in tests).
- **Explicit consent for optional map preview:** external OpenStreetMap tile fetches are disabled
  by default, require explicit user consent after a disclosure listing exact transmitted fields
  (IP, User-Agent, slippy tile `z/x/y`), keep PNG tiles in memory only (never written to disk),
  never reverse-geocode coordinates to addresses, and audit every opt-in preview load without
  logging coordinates.
- **Coordinate validation:** rejects NaN/Inf/out-of-range coordinates, flags Null Island `(0, 0)`,
  missing/conflicting hemisphere refs, and EXIF-vs-XMP GPS discrepancies, and explicitly reminds
  analysts that GPS describes capture-time metadata, not a sender's current location or identity.

## Implemented controls (Phase 6)
- **User-triggered offline OCR only:** OCR never runs automatically on import or selection, and
  never uploads images or silently downloads language packs.
- **Safe preprocessing & subprocess isolation:** originals are decoded through Pillow under the
  megapixel guard into a short-lived temp PNG inside `data/temp/` (deleted in `finally:`);
  Tesseract is invoked via argument lists (never `shell=True`) with strict regex validation on
  language codes (`^[A-Za-z0-9_]{2,20}(?:\+[A-Za-z0-9_]{2,20})*$`) and explicit timeouts.
- **Untrusted OCR text handling:** control characters are stripped, UI displays text strictly in
  plain-text widgets (`QPlainTextEdit` / `QTableWidgetItem`), every screen and export labels
  results as **Automated result / untrusted data**, and raw OCR text is never logged to
  `imagetrace.log` or `audit_events`.

## Implemented controls (Phase 7)
- **Append-only analyst notes:** revising a note marks the prior row `superseded` and inserts a
  linked revision (`supersedes_note_id`); retracting marks it `retracted` while keeping history
  intact. A SQLite `BEFORE DELETE` trigger (`notes_no_delete`) blocks deletion at the database
  layer.
- **Epistemic discipline & attribution:** every note records `author_label`, UTC timestamps,
  visual-clue category, and one of four fixed confidence labels (`Confirmed from file`,
  `Corroborated`, `Analyst observation`, `Unverified hypothesis`). Conclusions (`Confirmed from
  file` and `Corroborated`) require a non-empty source/citation reference; the creation dialog
  defaults conservatively to `Analyst observation`.
- **Plain-text rendering & log privacy:** note text and citations are rendered strictly as plain
  text (`QPlainTextEdit` / `Qt.TextFormat.PlainText`) and are never logged to `imagetrace.log` or
  `audit_events.details_json`.
- **Transparent limitation disclosure & chain checks:** one-click **Verify audit chain** buttons on
  Evidence Integrity & history, Cases, and Analysis let analysts verify hash-chain consistency at
  any time, alongside an explicit notice that application-level append-only rules are not
  tamper-proof against direct filesystem tampering unless the database and backups are externally
  secured and validated.

## Implemented controls (Phase 8)
- **Offline-only report generation:** PDF and JSON reporting performs zero network calls (verified
  with `urllib.request.urlopen` hard-blocked in tests) and never uploads or auto-opens exported
  files.
- **PDF markup injection defence:** all dynamic strings (filenames, case fields, metadata, OCR
  text, analyst notes) have control characters stripped and are XML-escaped before entering
  ReportLab `Paragraph` flowables, preventing XML injection or parser crashes on untrusted data.
- **Pre-write validation & integrity verification:** `validate_report_payload(...)` checks
  required provenance, SHA-256 digests, legend, limitations, GPS-only-when-present, automated OCR
  labelling, and note confidence labels before writing. Reports are written atomically after a
  free-space check (`>= 50 MiB`), paired with a `.sha256` sidecar, logged in the append-only
  `exports` table (`BEFORE DELETE` trigger), and verifiable against both DB and sidecar hashes.
- **Log & path privacy:** `audit_events` records only export ID, type, filename, SHA-256, and
  counts (never raw note/OCR text); internal exports store relative paths under the data root.

## Implemented controls (Phase 9)
- **Authenticated encrypted backups (`.sqlite.itbak`):** optional AES-256-GCM encryption with
  scrypt key derivation (`N=65536`, random 16-byte salt, random 12-byte nonce, 40-byte header
  authenticated as GCM AAD), `.sha256` sidecar, zero-overwrite cleanup of temporary decrypted
  files, offline corrupt-DB recovery helper, and documented independent recovery format
  (`docs/user-guide/backup-recovery.md`). Passphrases are never stored or logged.
- **Session lock & idle auto-lock:** manual `Ctrl+L` / top-bar **Lock** button and configurable
  idle timeout (`Settings -> Privacy, session lock and network`) cover the entire workspace and
  mask the active case title. Optional unlock passphrase is stored strictly as a salted scrypt
  hash (`scrypt$15$8$1$...`) and verified in constant time (never plaintext).
- **UI, filename & PDF hardening:** enforced `Qt.TextFormat.PlainText` on every `QLabel` in
  `MainWindow` (verified by runtime test); neutralized Windows reserved device names (`CON`,
  `NUL`, `COM1`–`9`, `LPT1`–`9`) and trailing dots/spaces in `sanitize_original_filename`;
  registered Windows TrueType Unicode fonts in ReportLab with Helvetica/Courier fallback; clamped
  unhandled-exception dialog output.
- **Verified zero-CVE dependencies & AST checks:** `pip-audit 2.10.1` reports 0 known
  vulnerabilities after upgrading venv `pip` to `26.3`; automated AST tests assert zero
  `shell=True`, `eval`, `exec`, `pickle`, `os.system`, or `setHtml` across `app/src/imagetrace/`.
  Full audit documented in `docs/security/phase9-security-review.md`.
