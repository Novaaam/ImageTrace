# ImageTrace 0.3.0 — Release Notes

**Release date:** 2026-11-25 · **Platform:** Windows 10/11 (64-bit) · **Package:** Portable one-directory build (`ImageTrace.exe`)

## What's new in 0.3.0 (Deep Forensics, Interactive Zoom Viewer, HEIC/RAW & Case Correlation)
- **Milestone 1 — Deep Forensics (`Origin & Trace v3` + Visual Forensics Lab):**
  - **JPEG DQT Quantization Table Fingerprinting:** hashes luminance & chrominance DQT tables (`dqt_hash`) and matches exact standard IJG quality tables, Photoshop Save-As tables, and stripped 4:2:0 re-encoding heuristic profiles (explicitly labeled as heuristic clues).
  - **MakerNotes Hardware Fingerprinting:** surfaces embedded `SerialNumber`, `BodySerialNumber`, `LensSerialNumber`, `ShutterCount`, `ImageNumber`, and `OwnerName` when present.
  - **Offline C2PA / JUMBF Claim Inspector:** reads embedded `claim_generator`, `c2pa.actions`, and `digitalSourceType` claims offline with an explicit caveat that trust-list cryptographic signature verification is not performed locally.
  - **Interactive Visual Forensics Lab (`Forensics lab`):** in-memory **ELA** (with interactive Quality `50–98` and Boost `1–40x` sliders), **Median-Filter Noise Residual**, **Luminance Gradient**, **RGB / YCbCr Channel Split** (`R`, `G`, `B`, `Cb`, `Cr`), and an offline **`QPainter` JPEG Ghost Curve**.
  - **Side-by-side Embedded EXIF Thumbnail Strip & Safe Payload Extractors:** visually compares Main Image vs. Embedded EXIF Thumbnail right in `Origin & Trace`, exports embedded EXIF thumbnails, and safely extracts trailing **Motion Photo `MP4`** (or binary) payloads after JPEG `EOI` / PNG `IEND` into `derived/` with SHA-256 verification and audit logging.
- **Milestone 2 — Real-World Formats, Interactive Zoom/Compare Viewer & Async Workers:**
  - **HEIC / HEIF & Camera RAW (`DNG`, `CR2`, `CR3`, `NEF`, `ARW`) Support:** magic-byte detection and safe read-only import with bundled ExifTool embedded JPEG preview fallback (zero new third-party dependencies!).
  - **Interactive Zoom/Pan Loupe & Side-by-Side Comparison Viewer (`Inspect / Zoom…`):** smooth `25%–800%` zoom (crisp nearest-neighbor pixels above `200%`), pan, live cursor `(x, y)` + RGB readout, and synchronized side-by-side comparison against the Embedded EXIF Thumbnail, ELA/Noise/Gradient/Chroma maps, or any other evidence item in the case.
  - **Collapsible Evidence List (`Expand details`) & Background Workers (`IT-005`):** collapse the evidence list for full-width inspection; live Tesseract OCR and multi-item visual PDF exports run off the UI thread with progress/cancel dialogs.
- **Milestone 3 — Case-Wide Correlation, Global Search & Visual Reports:**
  - **Cross-Evidence Correlation & Case Timeline (`Analysis` page):** links derivatives sharing `XMP DocumentID` / `OriginalDocumentID` / `DerivedFrom`, clusters items by Camera Serial, Capture Device, DQT table hash, and dHash visual similarity, builds a chronological timeline flagging `ModifyDate < CaptureDate` and out-of-order `ShutterCount` anomalies, and plots all geotagged case items on a 100% offline world coordinate plot.
  - **Global Offline Search (`Find…` / `Ctrl+K`):** searches across Cases, Evidence filenames/hashes, Origin & Trace devices/software, Metadata tags, OCR text, and Analyst notes, jumping directly to the exact item and tab.
  - **Visual PDF Reports & Verifiable Case Bundle (`.zip`):** embeds scaled evidence thumbnails — and highlighted side-by-side **Main vs. Embedded EXIF Thumbnail** comparisons when mismatches exist — inside offline PDF reports, plus one-click verifiable `.zip` bundle export containing PDF + JSON reports, metadata sidecars, thumbnails, and `MANIFEST.sha256` (never leaks SQLite DB or passphrases).

## Earlier in 0.2.0 (Advanced Origin & Trace Forensics)
- **Multi-layer `Origin & Trace` forensic engine & tab (`Evidence → Origin & Trace`):**
  - **Device & origin classification:** distinguishes **Mobile phone camera**, **Dedicated / real camera**, **Mobile/Dedicated capture + later software edit**, **Created or exported by computer graphics / editing software** (Adobe Photoshop, Illustrator, Lightroom, GIMP, Canva, Figma, Snapseed, etc.), **AI-generated / synthetic markers** (Stable Diffusion PNG `parameters`, ComfyUI `prompt`/`workflow`, DALL-E, Midjourney, Adobe Firefly), **Possible screenshot (heuristic)**, and **Metadata stripped / re-encoded**.
  - **Step-by-step Embedded History Timeline (`Step 1 → End`):** parses EXIF capture tags, raw XMP `xmpMM:History` XML edit chains, `DocumentID`/`OriginalDocumentID`/`DerivedFrom` lineage IDs, C2PA Content Credentials markers, OS/platform hints (`iOS / iPadOS`, `Android`, `Windows`, `macOS`, `Linux`), and ImageTrace import provenance — showing how an image moved from initial capture through computer software edits.
  - **Deep binary container forensics:** inspects JPEG `APP0`–`APP14` segments (`JFIF`, `EXIF`, `XMP`, `ICC_PROFILE`, `MPF`, `APP11:JUMBF/C2PA`, `APP12:Ducky`, `APP13:Photoshop 3.0 IRB`, `APP14:Adobe`, `COM`), DQT luminance quantization table quality estimation (`~Q1..100`), baseline vs. progressive encoding (`SOF0`/`SOF2`), chroma subsampling (`4:2:0`/`4:2:2`/`4:4:4`), PNG text chunk inventory, and trailing appended payload detection after JPEG `EOI` / PNG `IEND` (`MP4`, `ZIP`, `PDF`, `PE`, `HTML`, or unknown binary).
  - **Embedded EXIF thumbnail consistency check:** extracts embedded EXIF preview thumbnails and compares aspect ratio and 64-bit dHash against the main image to flag potential post-capture crops or edits that left the original thumbnail behind.
  - **On-demand Error Level Analysis (ELA) preview:** pure-Pillow in-memory `Q=90` recompression difference map with summary metrics and clear scientific limitations disclaimer (never modifies original evidence).
  - **Metadata-stripped copy exporter & opt-in manual public reverse image search:** exports a clean JPEG copy with all EXIF/XMP/IPTC/GPS stripped, plus opt-in manual browser shortcuts (Google Lens, TinEye, Bing Visual Search, Yandex Images) gated behind an explicit disclosure prompt (ImageTrace **never** auto-uploads evidence).
- **Batch analysis & report integration:** `Analysis` batch runs now populate Metadata, GPS, and Origin & Trace together; JSON and PDF reports include the full `Origin & Trace` assessment and step-by-step embedded history timeline whenever analyzed.

## Earlier in 0.1.1 (Phase 11 improvement cycle)
- **Cancellable background batch analysis:** `Analysis → Extract metadata and check GPS` now runs
  on a dedicated background worker (`BatchAnalysisWorker`) with live progress and a working
  **Cancel** button so large cases never freeze the UI.
- **Case-wide evidence integrity verification:** added one-click **Verify all evidence integrity**
  on the Analysis page (cancellable background SHA-256 check across all stored originals).
- **Dashboard & Reports ergonomics:** Dashboard now displays live backup health alongside quick
  navigation buttons (`Open Cases`, `Open Evidence`, `Run Analysis`, `Generate Reports`); Reports
  displays a non-blocking hint if evidence items are still missing extracted metadata; added `F5`
  shortcut to refresh the current screen.
- **Application icon:** added multi-resolution forensic application icon (`imagetrace.ico` /
  `imagetrace.png`) embedded in `ImageTrace.exe` and window title bars.

## Overview (0.1.0 baseline)
ImageTrace 0.1.0 is a local-first Windows desktop application for examining digital image evidence
with clear separation between **Extracted facts**, **Automated results**, **Analyst observations**,
and **Unverified hypotheses**.

### Key capabilities
- **Cases & chain-of-custody database:** SQLite schema v7 with foreign keys, transactional writes,
  pre-migration backups, and hash-chained append-only audit events (`Verify audit chain`).
- **Evidence import & integrity:** magic-byte whitelist (JPEG, PNG, WebP, TIFF, BMP, GIF),
  decompression-bomb & size guards, streaming SHA-256 verification before and after copying,
  read-only app-generated originals (`E-YYYYMMDD-XXXXXX.<ext>`), 64-bit dHash similarity heuristic,
  and non-destructive derived viewing copies.
- **Metadata extraction (ExifTool 13.64):** distinguishes Embedded metadata from File/container and
  Tool/system fields, preserves sanitized JSON sidecars, flags missing timezones and contradictory
  dates as factual discrepancies, and supports structured JSON export.
- **Embedded GPS inspection:** strictly offline embedded-only parsing & validation, 100% offline
  coordinate graticule plot, explicit absence reporting (`No GPS coordinates found in the examined
  file.`), and strictly opt-in, on-demand, in-memory OpenStreetMap tile preview with explicit
  disclosure (never reverse-geocodes addresses).
- **Optional offline OCR (Tesseract):** user-triggered only, preprocesses via Pillow under the
  megapixel guard, labels all results as automated/untrusted data, and supports JSON/TXT export.
- **Append-only analyst notes:** attributable, UTC-timestamped visual-clue observations with 4
  confidence categories (`Confirmed from file`, `Corroborated`, `Analyst observation`,
  `Unverified hypothesis`), mandatory source citations for confirmed/corroborated entries, linked
  revisions (`superseded`), and retractions (never silently deleted).
- **Offline PDF & JSON reports:** validated structured JSON (`imagetrace.report.v1`) and ReportLab
  PDF reports with Windows TrueType Unicode font support, XML-escaped text, live original SHA-256
  verification, `.sha256` sidecars, and one-click export verification.
- **Hardening & data protection:** optional **AES-256-GCM + scrypt** encrypted database backups
  (`.sqlite.itbak`), offline corrupt-DB recovery helper, manual (`Ctrl+L`) and idle-timeout
  session lock with salted scrypt passphrase verifier, and zero-CVE audited dependencies.

## What ImageTrace does NOT do
ImageTrace is **not** a person-tracking or deanonymization tool. It does not obtain IP addresses
from images, identify private individuals, perform facial recognition or gait analysis, infer
residential addresses, or upload evidence to cloud/AI services.

## Installation, data path & safe uninstall
- Extract the portable `ImageTrace` folder onto a non-system drive with ample free space (e.g.
  `D:\ImageTrace\`).
- Keep your case data in a separate folder (e.g. `D:\ImageTraceData\`) or `D:\ImageTrace\data\`.
  ImageTrace **never** falls back to the C-drive silently; if no data folder is configured on first
  launch, it prompts you to choose one.
- To uninstall without touching case data, run `Uninstall-KeepData.cmd` (or delete the program
  files while leaving your `data\` folder intact). **Uninstall never deletes case data by default.**

## Known limitations
- Hash-chained audit events and append-only notes are application-level integrity controls, not a
  tamper-proof forensic log against direct filesystem edits unless externally secured.
- Session lock (`Ctrl+L`) is a GUI privacy screen; Windows user account security and BitLocker
  full-disk encryption remain essential.
- Lost encrypted-backup passphrases cannot be recovered (no backdoor).
- Tesseract OCR is optional and installed separately on D: (see `docs/user-guide/ocr-setup.md`).
