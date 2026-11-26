"""Render the main window to PNG files for visual inspection (no display needed).

Usage: venv\\Scripts\\python.exe tools\\screenshot.py [output_dir]

Uses a throwaway data root under data\\temp populated with clearly labelled SYNTHETIC cases
and SYNTHETIC programmatic images. The real data root is never opened.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageCms, ImageDraw

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# The offscreen platform has no font database of its own; point it at the system fonts.
os.environ.setdefault(
    "QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "src"))

from imagetrace.app import create_app  # noqa: E402
from imagetrace.ui.case_dialog import CaseDialog  # noqa: E402
from imagetrace.ui.main_window import MainWindow  # noqa: E402
from imagetrace.ui.notes_tab import NoteDialog  # noqa: E402
from imagetrace.ui.search_dialog import GlobalSearchDialog  # noqa: E402
from imagetrace.ui.viewer_dialog import ImageViewerDialog  # noqa: E402

SYNTHETIC_CASES = [
    (
        "SYNTHETIC test case: harbour photo set",
        "synthetic, ui-review",
        "SYNTHETIC scope for screenshots.",
    ),
    ("SYNTHETIC test case: receipt scans", "synthetic", "SYNTHETIC scope for screenshots."),
]


def _inject_jpeg_segment(jpeg_path: Path, marker_byte: int, payload: bytes) -> None:
    """Insert a JPEG APP segment right after SOI (0xFFD8) without altering pixels."""
    raw = jpeg_path.read_bytes()
    if not raw.startswith(b"\xff\xd8"):
        return
    seg_len = len(payload) + 2
    seg = bytes([0xFF, marker_byte]) + seg_len.to_bytes(2, "big") + payload
    jpeg_path.write_bytes(raw[:2] + seg + raw[2:])


def _make_synthetic_images(folder: Path) -> list[Path]:
    paths: list[Path] = []
    # Image 1: synthetic geometric JPEG with sRGB ICC profile, mobile EXIF & XMP edit history
    im1 = Image.new("RGB", (320, 220), (35, 65, 110))
    d1 = ImageDraw.Draw(im1)
    d1.rectangle([20, 20, 150, 200], fill=(225, 228, 234))
    d1.ellipse([170, 30, 300, 190], fill=(190, 80, 60))
    d1.line([(0, 0), (320, 220)], fill=(30, 185, 120), width=5)
    prof = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    exif = im1.getexif()
    exif[0x010F] = "Apple"
    exif[0x0110] = "iPhone 15 Pro"
    exif[0x0112] = 1
    exif[0x0131] = "Adobe Photoshop 25.0 (Windows)"
    exif[0x0132] = "2026:05:10 16:45:00"
    p1 = folder / "SYNTHETIC_harbour_01.jpg"
    im1.save(p1, format="JPEG", quality=92, icc_profile=prof.tobytes(), exif=exif)
    # Add synthetic GPS coordinates & DateTimeOriginal via bundled ExifTool (never a real person's
    # location: Greenwich Royal Observatory 51.476852 N, 0.000500 W).
    et_exe = ROOT / "tools" / "third-party" / "exiftool" / "exiftool.exe"
    if et_exe.is_file():
        import subprocess

        subprocess.run(  # noqa: S603 (dev screenshot helper)
            [
                str(et_exe),
                "-overwrite_original",
                "-q",
                "-q",
                "-ExifIFD:DateTimeOriginal=2026:05:10 14:30:00",
                "-ExifIFD:LensModel=iPhone 15 Pro back triple camera 6.765mm f/1.78",
                "-GPSLatitude=51.476852",
                "-GPSLatitudeRef=N",
                "-GPSLongitude=0.000500",
                "-GPSLongitudeRef=W",
                "-GPSAltitude=47.0",
                "-GPSAltitudeRef=Above Sea Level",
                "-GPSDateStamp=2026:05:10",
                "-GPSTimeStamp=14:30:05",
                "--",
                str(p1),
            ],
            check=False,
            timeout=20,
        )
    # Embed synthetic XMP xmpMM:History + APP13 Photoshop IRB + APP14 Adobe markers for UI preview
    xmp_xml = (
        b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"'
        b' xmlns:xmp="http://ns.adobe.com/xap/1.0/"'
        b' xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"'
        b' xmlns:stEvt="http://ns.adobe.com/xap/1.0/sType/ResourceEvent#">'
        b'<rdf:Description xmp:CreatorTool="Adobe Photoshop 25.0 (Windows)"'
        b' xmpMM:DocumentID="xmp.did:synth-00112233"'
        b' xmpMM:OriginalDocumentID="xmp.did:synth-orig-9988">'
        b"<xmpMM:History><rdf:Seq>"
        b'<rdf:li stEvt:action="created" stEvt:when="2026-05-10T14:30:00Z"'
        b' stEvt:softwareAgent="Apple iPhone 15 Pro (iOS 18.2)"/>'
        b'<rdf:li stEvt:action="converted" stEvt:when="2026-05-10T16:12:00Z"'
        b' stEvt:softwareAgent="Adobe Photoshop 25.0 (Windows)"'
        b' stEvt:parameters="from image/heic to image/jpeg"/>'
        b'<rdf:li stEvt:action="saved" stEvt:when="2026-05-10T16:45:00Z"'
        b' stEvt:softwareAgent="Adobe Illustrator 28.0 (Windows)"'
        b' stEvt:changed="/"/>'
        b"</rdf:Seq></xmpMM:History>"
        b"</rdf:Description></rdf:RDF></x:xmpmeta>"
    )
    _inject_jpeg_segment(p1, 0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + xmp_xml)
    _inject_jpeg_segment(p1, 0xED, b"Photoshop 3.0\x008BIM\x04\x04\x00\x00\x00\x00")
    _inject_jpeg_segment(p1, 0xEE, b"Adobe\x00\x64\x00\x00\x00\x00\x01")
    paths.append(p1)

    # Image 2: re-encoded lower-quality JPEG (visually similar, different bytes)
    p2 = folder / "SYNTHETIC_harbour_01_recompressed.jpg"
    im1.save(p2, format="JPEG", quality=55)
    paths.append(p2)
    return paths


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    temp_parent = ROOT / "data" / "temp"
    temp_parent.mkdir(parents=True, exist_ok=True)
    app = create_app([])
    with tempfile.TemporaryDirectory(prefix="screenshot-", dir=temp_parent) as tmp:
        tmp_path = Path(tmp)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        src_dir = tmp_path / "sources"
        src_dir.mkdir()
        samples = _make_synthetic_images(src_dir)

        win = MainWindow(data_root=data_dir)
        for title, tags, scope in SYNTHETIC_CASES:
            win.cases.create(title, scope, tags=tags, notes="Synthetic notes for UI review.")
        first = win.cases.list()[-1]
        win.set_active_case(first.id)
        ev1 = win.evidence.import_file(
            first.id, samples[0], source_note="SYNTHETIC fixture for UI inspection"
        )
        win.evidence.import_file(first.id, samples[1], source_note="SYNTHETIC similar fixture")
        win.evidence.verify_integrity(ev1.id)
        if win.metadata is not None:
            win.metadata.extract(ev1.id)
        if win.gps is not None:
            win.gps.parse(ev1.id, extract_if_missing=False)
        if win.provenance is not None:
            win.provenance.analyze(ev1.id, extract_metadata_if_missing=False)
            # Also analyze second item so Case Correlation shows clusters & timeline
            ev2_list = [e for e in win.evidence.list_for_case(first.id) if e.id != ev1.id]
            if ev2_list:
                win.provenance.analyze(ev2_list[0].id, extract_metadata_if_missing=False)
        if win.ocr is not None:
            # Synthetic mock OCR output for UI inspection (real Tesseract is optional).
            synth_tsv = "\n".join(
                [
                    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
                    "5\t1\t1\t1\t1\t1\t16\t24\t96\t22\t94.2\tSYNTHETIC",
                    "5\t1\t1\t1\t1\t2\t120\t24\t84\t22\t91.5\tHARBOUR",
                    "5\t1\t1\t1\t2\t1\t16\t54\t70\t20\t88.0\tPIER-04",
                ]
            )
            win.ocr._custom_runner = lambda *_: ("Tesseract", "5.4.0-synthetic-preview", synth_tsv)
            win.ocr.run_ocr(ev1.id, language="eng")
            win.ocr._custom_runner = None
        if win.notes is not None:
            win.notes.create(
                first.id,
                ev1.id,
                category="Metadata / technical finding",
                confidence_label="Confirmed from file",
                source_reference="EXIF:IFD0:Make and EXIF:IFD0:Model",
                note_text="SYNTHETIC sample camera tags report SYNTHETIC-Cam / SYNTH-Model-1.",
            )
            win.notes.create(
                first.id,
                ev1.id,
                category="Object / structure",
                confidence_label="Analyst observation",
                source_reference="Upper-right quadrant of synthetic test image",
                note_text=(
                    "SYNTHETIC geometric pattern: red ellipse beside light grey rectangle "
                    "crossed by a green diagonal stripe."
                ),
            )
        if win.reports is not None:
            win.reports.generate_json(first.id)
            win.reports.generate_pdf(first.id)

        win.show()
        for theme in ("light", "dark"):
            win.set_theme(theme, persist=False)
            for page in (
                "Dashboard",
                "Cases",
                "Evidence",
                "Analysis",
                "Reports",
                "Settings",
                "About",
            ):
                win.navigate(page)
                if page == "Cases":
                    win.cases_page.reload(select_id=first.id)
                elif page == "Evidence":
                    win.evidence_page.reload(select_id=ev1.id)
                    win.evidence_page.details.tabs.setCurrentIndex(0)
                elif page == "Reports" and win.reports_page.table.rowCount():
                    win.reports_page.table.selectRow(0)
                for _ in range(3):
                    app.processEvents()
                path = out / f"{theme}-{page.lower()}.png"
                win.grab().save(str(path))
                print(path)
                if page == "Evidence":
                    for tab_idx, slug in (
                        (1, "origin"),
                        (2, "metadata"),
                        (3, "gps"),
                        (4, "ocr"),
                        (5, "properties"),
                        (6, "notes"),
                        (7, "integrity"),
                    ):
                        win.evidence_page.details.tabs.setCurrentIndex(tab_idx)
                        if tab_idx == 6 and win.evidence_page.details.notes_tab.table.rowCount():
                            win.evidence_page.details.notes_tab.table.selectRow(0)
                        for _ in range(3):
                            app.processEvents()
                        p_tab = out / f"{theme}-evidence-{slug}.png"
                        win.grab().save(str(p_tab))
                        print(p_tab)
            dlg = CaseDialog(win)
            dlg.title_edit.setText("SYNTHETIC dialog preview")
            dlg.authorized.setChecked(True)
            dlg.show()
            app.processEvents()
            path = out / f"{theme}-case-dialog.png"
            dlg.grab().save(str(path))
            dlg.close()
            print(path)

            ndlg = NoteDialog("Analyst", win)
            ndlg.source_edit.setText("SYNTHETIC EXIF:Make field")
            ndlg.text_edit.setPlainText("SYNTHETIC observation preview.")
            ndlg.show()
            app.processEvents()
            npath = out / f"{theme}-note-dialog.png"
            ndlg.grab().save(str(npath))
            ndlg.close()
            print(npath)

            win.lock_session()
            for _ in range(3):
                app.processEvents()
            lpath = out / f"{theme}-locked.png"
            win.grab().save(str(lpath))
            print(lpath)
            win.unlock_session()

            vdlg = ImageViewerDialog(win.evidence, ev1, initial_compare="ela", parent=win)
            vdlg.show()
            for _ in range(3):
                app.processEvents()
            vpath = out / f"{theme}-viewer-compare.png"
            vdlg.grab().save(str(vpath))
            vdlg.close()
            print(vpath)

            sdlg = GlobalSearchDialog(
                win.run_global_search_query, initial_query="Photoshop", parent=win
            )
            sdlg.show()
            for _ in range(3):
                app.processEvents()
            spath = out / f"{theme}-search-dialog.png"
            sdlg.grab().save(str(spath))
            sdlg.close()
            print(spath)
        win.close()
        app.processEvents()
    return 0


if __name__ == "__main__":
    sys.exit(main())
