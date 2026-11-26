# Security and Hardening Review — Phase 9

**Date:** 2026-11-24 · **App Version:** ImageTrace 0.1.0 · **Scope:** All modules under
`app/src/imagetrace/`, database/backup/report storage, subprocess boundaries, UI rendering,
and installed Python dependencies (`D:\imagetrace\venv`).

> This is an engineering security & error-handling review with automated tests and static checks,
> not an external third-party penetration test or formal certification.

---

## 1. Summary Verdict
- **Critical issues unresolved:** 0
- **High issues unresolved:** 0
- **Medium issues resolved in Phase 9:** 5 (see table below)
- **Dependency vulnerabilities (`pip-audit 2.10.1`):** 0 (`No known vulnerabilities found` after
  upgrading venv `pip` from `25.0.1` to `26.3`)

---

## 2. Findings and Mitigations Applied in Phase 9

| ID | Severity | Finding | Fix Applied | Verification |
|----|----------|---------|-------------|--------------|
| SEC-01 | High (dev env) | Bundled `pip 25.0.1` in `D:\imagetrace\venv` reported 12 known vulnerabilities in `pip-audit`. | Upgraded project venv `pip` to `26.3`; re-ran `pip-audit`. | `pip-audit`: `No known vulnerabilities found` across all runtime & dev packages. |
| SEC-02 | Medium | Six direct `QLabel` instances (`main_window.py`, `evidence_page.py`, `gps_tab.py`) did not explicitly call `setTextFormat(Qt.TextFormat.PlainText)`. | Explicitly set `PlainText` on all six labels and added a runtime GUI invariant test checking every `QLabel` child of `MainWindow`. | `test_every_qlabel_in_main_window_uses_plain_text` passes (0 non-PlainText labels). |
| SEC-03 | Medium | `sanitize_original_filename` did not neutralize Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`) or trailing dots/spaces. | Hardened `sanitize_original_filename` via `security.is_windows_reserved_stem`, separator normalization, and trailing dot/space trimming. | Parameterized `test_sanitize_original_filename_blocks_traversal_and_reserved_names`. |
| SEC-04 | Medium | Backups were unencrypted `.sqlite` files only; no app-level idle/session lock existed (blueprint §8, §9). | Added authenticated AES-256-GCM + scrypt encrypted backups (`.sqlite.itbak`, 40-byte AAD-bound header, zero-overwrite temp cleanup, corrupt-DB recovery helper, `docs/user-guide/backup-recovery.md`) and `Ctrl+L` / idle-timeout `LockOverlay` with optional salted scrypt passphrase verifier (zero plaintext secrets). | `test_backup_encrypted.py` & `test_security_hardening.py` pass; verified passphrase never appears in SQLite `settings`, `audit_events`, or `imagetrace.log`. |
| SEC-05 | Low | ReportLab built-in Helvetica/Courier covered Latin-1 only, potentially dropping non-Latin OCR/notes in PDF exports; `sys.excepthook` dialog did not clamp oversized error messages. | Added optional Windows TrueType Unicode font registration (`arial.ttf`/`arialbd.ttf`/`cour.ttf` with transparent Helvetica/Courier fallback) and clamped unhandled-error dialog messages to 400 characters. | `test_unicode_and_hostile_strings_end_to_end_pdf_and_json` passes with Cyrillic/Arabic/accented/HTML/SQL strings. |

---

## 3. Core Invariants Audited & Verified
1. **No shell execution or code evaluation:** AST scan across all files in `app/src/imagetrace/`
   confirms zero `shell=True`, zero `eval()`/`exec()`, zero `pickle`, zero `os.system()`, and
   zero `setHtml()`.
2. **Subprocess safety:** ExifTool (`metadata.py`) and Tesseract (`ocr.py`) invoke validated paths
   with explicit argument lists, `--` before evidence paths, `CREATE_NO_WINDOW`, and timeouts.
3. **SQL injection defence:** Every query uses parameterized `?` bindings; dynamic `UPDATE` column
   names in `cases.py` come solely from a frozen whitelist; `PRAGMA user_version` casts `int()`.
4. **Offline-by-default guarantee:** Hard-blocking `urllib.request.urlopen` confirms zero network
   access across case management, import, metadata, GPS parsing, OCR, notes, backups, and PDF/JSON
   report generation. Only explicit, disclosed, on-demand OSM tile previews in `gps.py` contact
   `https://tile.openstreetmap.org/...`.
5. **Malformed & adversarial inputs:** Tested empty files, spoofed PE/ELF/PDF/SVG/ZIP/HTML files,
   400 MP PNG decompression bombs, oversized files, truncated JPEGs, and JPEG+HTML polyglots — all
   rejected safely or preserved read-only without executing content or leaking partial/temp files.
6. **Memory & cleanliness:** 20-image batch import + notes + PDF/JSON report export stayed well
   below the 128 MiB traced Python heap ceiling with zero leaked files in `data/temp/`.

---

## 4. Documented Residual Limitations (Accepted by Design)
- **Application-level append-only logs & notes:** `audit_events`, `notes`, and `exports` use
  hash-chaining and SQLite triggers, which protect against accidental app-level modification but
  cannot prevent offline tampering by someone with raw filesystem access unless databases and
  backups are externally secured and validated.
- **Session lock scope:** `LockOverlay` (`Ctrl+L` / idle timeout) hides case data inside the
  running GUI window; it is not an OS screen lock or disk encryption layer. Windows user account
  security and full-disk encryption (BitLocker) remain essential.
- **Best-effort temp wiping:** `shred_and_unlink` overwrites temporary decrypted backup files
  before deletion, which cannot guarantee physical erasure on SSDs due to wear-leveling. Full-disk
  encryption protects data at rest.
- **No passphrase recovery backdoor:** Lost encrypted-backup passphrases cannot be recovered.
