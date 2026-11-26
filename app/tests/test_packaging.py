import os
import subprocess
import sys
from pathlib import Path

from imagetrace import paths, tools_status
from imagetrace.ui import theme

ROOT = Path(__file__).resolve().parents[2]


def test_frozen_mode_path_and_resource_resolution(tmp_path, monkeypatch):
    fake_bundle = tmp_path / "ImageTrace"
    fake_internal = fake_bundle / "_internal"
    fake_res = fake_internal / "resources"
    fake_res.mkdir(parents=True)
    (fake_res / "check-light.svg").write_text("<svg/>", encoding="utf-8")
    fake_exe = fake_bundle / "ImageTrace.exe"
    fake_exe.write_bytes(b"MZ")
    fake_et = fake_bundle / "tools" / "third-party" / "exiftool" / "exiftool.exe"
    fake_et.parent.mkdir(parents=True)
    fake_et.write_bytes(b"MZ")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_internal), raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    assert paths.is_frozen() is True
    assert paths.project_root() == fake_bundle.resolve()
    assert paths.resources_dir() == fake_res.resolve()
    assert paths.default_data_root() == fake_bundle.resolve() / "data"
    assert tools_status.find_exiftool() == fake_et.resolve()
    icon_url = theme._check_icon("light")
    assert icon_url.endswith("/resources/check-light.svg")
    assert Path(icon_url).is_file()


def test_release_files_and_license_notices_present():
    licenses = (ROOT / "THIRD_PARTY_LICENSES.txt").read_text(encoding="utf-8")
    for required in (
        "PySide6",
        "LGPL-3.0",
        "Pillow",
        "ReportLab",
        "cryptography",
        "ExifTool",
        "Tesseract",
        "PyInstaller",
    ):
        assert required in licenses

    rel = (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    assert "0.3.0" in rel
    assert "never deletes case data" in rel.lower()
    assert (ROOT / "app" / "resources" / "imagetrace.ico").is_file()

    guide = (ROOT / "docs" / "user-guide" / "getting-started.md").read_text(encoding="utf-8")
    assert "Uninstall-KeepData.cmd" in guide
    assert "D-drive" in guide

    uninst = ROOT / "tools" / "Uninstall-KeepData.cmd"
    assert uninst.is_file()
    raw = uninst.read_bytes()
    assert b"\r\n" in raw or os.name != "nt"


def test_uninstall_script_removes_app_and_preserves_case_data(tmp_path):
    """Verify Uninstall-KeepData.cmd removes program binaries while keeping data/ untouched."""
    bundle = tmp_path / "ImageTrace"
    bundle.mkdir()
    (bundle / "ImageTrace.exe").write_bytes(b"fake-exe")
    (bundle / "_internal").mkdir()
    (bundle / "_internal" / "Qt6Core.dll").write_bytes(b"fake-dll")
    (bundle / "tools" / "third-party" / "exiftool").mkdir(parents=True)
    (bundle / "tools" / "third-party" / "exiftool" / "exiftool.exe").write_bytes(b"fake-et")
    (bundle / "docs").mkdir()
    (bundle / "docs" / "guide.md").write_text("guide", encoding="utf-8")
    (bundle / "THIRD_PARTY_LICENSES.txt").write_text("lic", encoding="utf-8")
    (bundle / "RELEASE_NOTES.md").write_text("rel", encoding="utf-8")
    (bundle / "MANIFEST.sha256").write_text("sha", encoding="utf-8")

    # User case data & remembered config that MUST survive uninstall
    data_db = bundle / "data" / "database" / "imagetrace.sqlite"
    data_db.parent.mkdir(parents=True)
    data_db.write_bytes(b"IMPORTANT_CASE_DB")
    orig_ev = bundle / "data" / "cases" / "C-20261124-AAAAAA" / "originals" / "E-1.jpg"
    orig_ev.parent.mkdir(parents=True)
    orig_ev.write_bytes(b"ORIGINAL_EVIDENCE_BYTES")
    cfg = bundle / "imagetrace.local.json"
    cfg.write_text('{"data_root": "D:\\\\ImageTraceData"}', encoding="utf-8")

    uninst_dst = bundle / "Uninstall-KeepData.cmd"
    uninst_dst.write_bytes((ROOT / "tools" / "Uninstall-KeepData.cmd").read_bytes())

    comspec = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
    proc = subprocess.run(  # noqa: S603 (testing bundled uninstall cmd)
        [comspec, "/c", str(uninst_dst), "/Y"],
        cwd=bundle,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    # Program files removed
    assert not (bundle / "ImageTrace.exe").exists()
    assert not (bundle / "_internal").exists()
    assert not (bundle / "tools").exists()
    assert not (bundle / "docs").exists()
    assert not (bundle / "THIRD_PARTY_LICENSES.txt").exists()

    # Case data & config 100% preserved!
    assert data_db.read_bytes() == b"IMPORTANT_CASE_DB"
    assert orig_ev.read_bytes() == b"ORIGINAL_EVIDENCE_BYTES"
    assert cfg.is_file()
