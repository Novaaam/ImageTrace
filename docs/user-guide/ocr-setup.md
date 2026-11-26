# Offline OCR Setup Guide (Tesseract)

ImageTrace supports optional, user-triggered, completely offline OCR using **Tesseract OCR**
(Apache-2.0 license). ImageTrace never downloads Tesseract or language packs silently and never
uploads images to online OCR services.

## 1. Install Tesseract on D-drive (recommended)
1. Download the official 64-bit Windows installer from UB Mannheim's Tesseract page:
   https://github.com/UB-Mannheim/tesseract/wiki
2. Run the installer and change the installation directory from `C:\Program Files\Tesseract-OCR`
   to a D-drive location, for example:
   ```
   D:\imagetrace\tools\third-party\tesseract\
   ```
   (Keeping binaries on D: avoids filling C: and lets ImageTrace auto-detect `tesseract.exe`.)

## 2. Installing language packs (`*.traineddata`)
- English (`eng`) is included by default in the installer.
- To add other languages (for example `deu`, `fra`, `ara`, `spa`):
  - Either select them during the Tesseract installer step, **or**
  - Manually download the corresponding `<lang>.traineddata` file from the official Tesseract
    `tessdata_fast` repository (https://github.com/tesseract-ocr/tessdata_fast), verify its
    source, and place it into your `tessdata` folder (e.g.
    `D:\imagetrace\tools\third-party\tesseract\tessdata\`).

## 3. Configure in ImageTrace
1. Open **Settings → External tools**.
2. Leave **Tesseract path** blank to auto-detect `tools\third-party\tesseract\tesseract.exe` (or
   `PATH`), or click **Browse…** to pick a custom `tesseract.exe`.
3. Optionally set **Tessdata folder** if your `.traineddata` language packs are stored elsewhere.
4. Click **Test** to verify the detected Tesseract version and installed language list.

## 4. Safety and limitations
- OCR runs **only when you click Run OCR** on an evidence item.
- OCR runs on a temporary Pillow-decoded PNG copy inside `data\temp\` (deleted immediately after),
  so the original evidence file is never modified.
- All OCR results are labelled as **Automated result** and treated strictly as **untrusted data**
  (rendered as plain text, never executed, never logged to `imagetrace.log` or audit details).
