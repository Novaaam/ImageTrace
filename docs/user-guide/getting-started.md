# ImageTrace User Guide — Getting Started

ImageTrace is a local-first Windows desktop application for examining digital image evidence.
Core workflows run strictly offline with zero telemetry and zero cloud uploads.

---

## 1. Installation and choosing a data folder (D-drive recommended)

ImageTrace is distributed as a **portable one-directory Windows folder** (`ImageTrace\`) so it
requires no administrator privileges, writes nothing to the Windows Registry, and keeps Qt/PySide6
libraries dynamically linked.

1. Copy the `ImageTrace` folder to a drive with ample free space, for example `D:\ImageTrace\`.
2. Decide where your case data will live (**recommended:** a separate folder such as
   `D:\ImageTraceData\` so upgrading or uninstalling the application never touches your cases).
   - You can also create a `data\` subfolder inside `D:\ImageTrace\` or set the environment
     variable `IMAGETRACE_DATA_ROOT=D:\ImageTraceData`.
3. Double-click **`ImageTrace.exe`** (or **`start.bat`** when running from a source checkout).
4. On first launch (if `<app>\data` does not already exist), ImageTrace displays **Data folder
   unavailable** and asks you to **Choose folder…**:
   - Select your prepared data folder (e.g. `D:\ImageTraceData`).
   - Confirm **Remember this folder?** to save the location in `imagetrace.local.json` next to
     `ImageTrace.exe`.
   - ImageTrace **never** switches to the C-drive silently; if you select a folder on `C:`, it
     warns you and asks for explicit confirmation.

---

## 2. Core examination workflow

1. **Create a case (`Cases → New case…`):**
   - Enter a short title and an explicit **Scope / authorization** statement (confirming what you
     are authorized to examine and why). Avoid unnecessary personal data.
   - Click **Set as active case**.
2. **Import evidence (`Evidence → Import images…` or drag & drop):**
   - Supported formats: JPEG, PNG, WebP, TIFF, BMP, GIF, **HEIC / HEIF** (iPhone), and **Camera
     RAW** (`DNG`, `CR2`, `CR3`, `NEF`, `ARW`), verified by magic bytes. Originals are copied under
     app-generated IDs (`E-YYYYMMDD-XXXXXX.<ext>`), marked read-only, and hashed with SHA-256
     before and after copying. Click **Inspect / Zoom…** for pixel-accurate zoom/pan and
     side-by-side comparison, or **Expand details** for full-width tables.
3. **Trace origin, edit history & run Visual Forensics Lab (`Evidence → Origin & Trace`):**
   - Classifies **mobile phone camera**, **dedicated camera**, **graphics/editing software**
     (Photoshop, Illustrator, etc.), **AI-generation markers**, **possible screenshots**, or
     **stripped/re-encoded metadata**, and surfaces **MakerNotes** hardware serial/shutter IDs,
     **DQT quantization fingerprints**, and offline **C2PA** claims.
   - Reconstructs a **Step-by-Step Embedded History Timeline** (`Step 1 -> End`), displays
     side-by-side **Main vs. Embedded EXIF Thumbnail** comparisons, safely extracts EXIF thumbnails
     or trailing **Motion Photo `MP4`** clips, and provides an interactive **Visual Forensics Lab**
     (ELA sliders, Noise Residual, Luminance Gradient, RGB/YCbCr channels, JPEG Ghost curve) plus
     opt-in manual public reverse-search shortcuts (never auto-uploads; never extracts IP addresses).
4. **Correlate across the whole case & Global Search (`Analysis` + `Find…` / `Ctrl+K`):**
   - Use **Analysis → Correlate case** to discover XMP derivative lineage links, shared Camera
     Serial / Device / DQT / dHash clusters, chronological timeline anomalies, and a multi-image
     offline GPS plot. Press **`Ctrl+K`** (or click **Find…**) to search across all Cases,
     Evidence, Origin & Trace, Metadata, OCR text, and Analyst notes.
5. **Extract metadata (`Evidence → Metadata → Extract metadata` or `Analysis` batch run):**
   - Runs local `ExifTool` and separates **Embedded** fields from **File / container** and
     **Tool / system** fields, highlighting timezone gaps and date discrepancies for review.
4. **Inspect embedded GPS (`Evidence → GPS`):**
   - Displays coordinates **only** if embedded in the file; plots them on a 100% offline local
     graticule and never reverse-geocodes addresses.
   - Optional OpenStreetMap preview is **OFF by default** and requires opt-in in Settings plus an
     explicit disclosure prompt before loading.
5. **Run optional offline OCR (`Evidence → OCR`):**
   - User-triggered only; requires an optional local Tesseract installation (see `ocr-setup.md`).
   - All OCR text is labelled **Automated result / untrusted data**.
6. **Record analyst notes (`Evidence → Analyst notes`):**
   - Record visible landmarks, signage, environmental details, or technical findings with one of
     four confidence labels (`Confirmed from file`, `Corroborated`, `Analyst observation`,
     `Unverified hypothesis`).
   - `Confirmed from file` and `Corroborated` require a source/citation reference.
   - Notes are append-only: **Revise…** marks the prior note `superseded` and appends a linked
     revision; **Retract** preserves history.
7. **Generate & verify reports (`Reports`):**
   - Export offline **PDF** or **JSON** reports for the entire active case or a single evidence
     item. Every report is validated before writing, saved with a `.sha256` sidecar, recorded in
     export history, and verifiable with **Verify selected export**.

---

## 3. Backups, encryption & session lock

- **Backups (`Settings → Database and backups`):**
  - Click **Back up now** (plain `.sqlite`) or **Encrypted backup…** (AES-256-GCM + scrypt
    `.sqlite.itbak`). Backups are written to `<data_root>\backups\` with `.sha256` sidecars and
    are **never** deleted automatically.
  - Full recovery steps (including standalone decryption if the database is damaged) are in
    `docs\user-guide\backup-recovery.md`.
- **Session lock (`Lock` button / `Ctrl+L`):**
  - Covers the entire workspace and masks the active case title.
  - Configure an optional idle timeout and unlock passphrase under **Settings → Privacy, session
    lock and network** (passphrase stored solely as a salted scrypt hash).
  - Remember: session lock is a GUI privacy screen; Windows user account protection and BitLocker
    full-disk encryption remain essential.

---

## 4. Upgrading and uninstalling (without deleting case data)

- **Upgrading:** Copy the new portable `ImageTrace\` files over the old program files (your
  `imagetrace.local.json` and `data\` folder remain intact; any database schema update backs up
  the database automatically before migrating).
- **Uninstalling:** Run **`Uninstall-KeepData.cmd`** inside the `ImageTrace\` folder. It removes
  only program files (`ImageTrace.exe`, `_internal\`, `tools\`, `docs\`, notices) and **never
  deletes `data\`, `imagetrace.local.json`, or any external data folder**.
