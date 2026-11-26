"""Build a portable Windows one-directory ImageTrace package on D-drive (Phase 10).

Usage:
    venv\\Scripts\\python.exe tools\\build_portable.py [--no-exiftool]

Design rules (blueprint sections 2, 8, 11; ADR-003, ADR-005):
- Builds in **one-directory** mode (``--onedir --windowed``) under ``build\\dist\\ImageTrace\\``:
  1. Keeps Qt/PySide6 libraries dynamically linked & replaceable (LGPL-3.0 compliance).
  2. Avoids PyInstaller ``--onefile`` runtime temp extraction to ``C:\\Users\\...\\Temp\\_MEI*``.
- Keeps all build caches, work dirs and temp files inside ``D:\\imagetrace\\`` (never C:).
- Excludes unused Qt add-ons (WebEngine, 3D, Multimedia, Quick/Qml, Charts, etc.) to reduce
  attack surface and footprint.
- Copies license notices, release notes, user guides, ``Uninstall-KeepData.cmd``, and (by default)
  verified ExifTool into the portable folder, while NEVER shipping developer ``data\\`` files.
- Writes ``MANIFEST.sha256`` for release verification.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_SRC = ROOT / "app" / "src"
RESOURCES = ROOT / "app" / "resources"
DIST_ROOT = ROOT / "build" / "dist"
WORK_ROOT = ROOT / "build" / "pyinstaller-work"
SPEC_ROOT = ROOT / "build" / "pyinstaller-spec"
BUNDLE_DIR = DIST_ROOT / "ImageTrace"

# Unused PySide6 / stdlib modules excluded to keep the bundle lean and exclude QtWebEngine.
EXCLUDED_MODULES = (
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DAnimation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuick3D",
    "PySide6.QtQml",
    "PySide6.QtCharts",
    "PySide6.QtGraphs",
    "PySide6.QtDataVisualization",
    "PySide6.QtSql",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtLocation",
    "PySide6.QtPositioning",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtScxml",
    "PySide6.QtStateMachine",
    "PySide6.QtTextToSpeech",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtWebSockets",
    "PySide6.QtWebChannel",
    "PySide6.QtRemoteObjects",
    "PySide6.QtHttpServer",
    "PySide6.QtSpatialAudio",
    "PySide6.QtTest",
    "PySide6.QtNetwork",
    "PySide6.QtPrintSupport",
    "PySide6.QtXml",
    "PySide6.QtConcurrent",
    "PySide6.QtDBus",
    "tkinter",
    "unittest",
    "pytest",
    "pip",
    "pip_audit",
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def build(*, include_exiftool: bool = True) -> Path:
    pyinstaller_exe = ROOT / "venv" / "Scripts" / "pyinstaller.exe"
    if not pyinstaller_exe.is_file():
        raise FileNotFoundError(
            f"PyInstaller not found at {pyinstaller_exe}. Install dev requirements first."
        )

    for folder in (DIST_ROOT, WORK_ROOT, SPEC_ROOT, ROOT / "data" / "temp"):
        folder.mkdir(parents=True, exist_ok=True)
    if BUNDLE_DIR.exists():
        shutil.rmtree(BUNDLE_DIR)

    env = os.environ.copy()
    env["TEMP"] = str(ROOT / "data" / "temp")
    env["TMP"] = str(ROOT / "data" / "temp")
    env["PYINSTALLER_CONFIG_DIR"] = str(ROOT / ".cache" / "pyinstaller")
    env["PYTHONPYCACHEPREFIX"] = str(ROOT / ".cache" / "pycache")

    cmd = [
        str(pyinstaller_exe),
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        "ImageTrace",
        "--distpath",
        str(DIST_ROOT),
        "--workpath",
        str(WORK_ROOT),
        "--specpath",
        str(SPEC_ROOT),
        "--paths",
        str(APP_SRC),
        "--add-data",
        f"{RESOURCES}{os.pathsep}resources",
        "--hidden-import",
        "cryptography.hazmat.backends.openssl",
        "--hidden-import",
        "cryptography.hazmat.primitives.kdf.scrypt",
        "--hidden-import",
        "cryptography.hazmat.primitives.ciphers.aead",
    ]
    icon_path = RESOURCES / "imagetrace.ico"
    if icon_path.is_file():
        cmd += ["--icon", str(icon_path)]
    for mod in EXCLUDED_MODULES:
        cmd += ["--exclude-module", mod]
    cmd.append(str(ROOT / "tools" / "launch_frozen.py"))

    print("Running PyInstaller (one-dir windowed, all caches on D:)...")
    proc = subprocess.run(cmd, env=env, check=False)  # noqa: S603 (build tool)
    if proc.returncode != 0:
        raise RuntimeError(f"PyInstaller failed with exit code {proc.returncode}")

    exe = BUNDLE_DIR / "ImageTrace.exe"
    if not exe.is_file():
        raise RuntimeError(f"Expected executable not produced: {exe}")

    # Copy documentation, license notices and safe data-preserving uninstaller
    for name in ("LICENSE", "THIRD_PARTY_LICENSES.txt", "RELEASE_NOTES.md", "README.md"):
        src = ROOT / name
        if src.is_file():
            shutil.copy2(src, BUNDLE_DIR / name)
    docs_out = BUNDLE_DIR / "docs" / "user-guide"
    docs_out.mkdir(parents=True, exist_ok=True)
    for guide in ("getting-started.md", "backup-recovery.md", "ocr-setup.md"):
        src = ROOT / "docs" / "user-guide" / guide
        if src.is_file():
            shutil.copy2(src, docs_out / guide)
    uninst = ROOT / "tools" / "Uninstall-KeepData.cmd"
    if uninst.is_file():
        shutil.copy2(uninst, BUNDLE_DIR / "Uninstall-KeepData.cmd")

    # Optionally bundle verified ExifTool (if installed in tools/third-party/exiftool)
    et_src = ROOT / "tools" / "third-party" / "exiftool"
    if include_exiftool and (et_src / "exiftool.exe").is_file():
        et_dst = BUNDLE_DIR / "tools" / "third-party" / "exiftool"
        shutil.copytree(et_src, et_dst, dirs_exist_ok=True)
        readme_3p = ROOT / "tools" / "third-party" / "README.md"
        if readme_3p.is_file():
            shutil.copy2(readme_3p, BUNDLE_DIR / "tools" / "third-party" / "README.md")

    # Write release manifest with SHA-256 of key files
    manifest_targets = [
        BUNDLE_DIR / "ImageTrace.exe",
        BUNDLE_DIR / "THIRD_PARTY_LICENSES.txt",
        BUNDLE_DIR / "RELEASE_NOTES.md",
        BUNDLE_DIR / "Uninstall-KeepData.cmd",
    ]
    et_bundled = BUNDLE_DIR / "tools" / "third-party" / "exiftool" / "exiftool.exe"
    if et_bundled.is_file():
        manifest_targets.append(et_bundled)

    lines = [
        f"{_sha256(p)}  {p.relative_to(BUNDLE_DIR).as_posix()}"
        for p in manifest_targets
        if p.is_file()
    ]
    (BUNDLE_DIR / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="ascii")

    total_mb = _dir_size(BUNDLE_DIR) / (1024 * 1024)
    print(f"Portable build complete: {BUNDLE_DIR} ({total_mb:.1f} MB)")
    print(f"Executable SHA-256: {_sha256(exe)}")
    return BUNDLE_DIR


def main() -> int:
    parser = argparse.ArgumentParser(description="Build portable Windows ImageTrace package.")
    parser.add_argument(
        "--no-exiftool",
        action="store_true",
        help="Do not copy bundled ExifTool into the portable package.",
    )
    args = parser.parse_args()
    build(include_exiftool=not args.no_exiftool)
    return 0


if __name__ == "__main__":
    sys.exit(main())
