"""Phase 0 environment verification for ImageTrace.

Standard library only. Run with the project venv:
    D:\\imagetrace\\venv\\Scripts\\python.exe D:\\imagetrace\\tools\\verify_env.py

Exits non-zero if any required check fails. Makes no network calls and
writes nothing except a tiny probe file inside data\\temp (deleted afterwards).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOW_SPACE_GB = 10.0

REQUIRED_DIRS = [
    "app/src",
    "app/tests",
    "app/resources",
    "docs/architecture",
    "docs/user-guide",
    "docs/security",
    "docs/test-reports",
    "data/cases",
    "data/database",
    "data/exports",
    "data/logs",
    "data/backups",
    "data/temp",
    "tools/third-party",
    "build",
    "venv",
]
REQUIRED_DOCS = [
    "README.md",
    "BLUEPRINT.md",
    "CHANGELOG.md",
    "ROADMAP.md",
    "DEVELOPMENT_LOG.md",
    "DECISIONS.md",
    ".gitignore",
    "docs/architecture/dependency-review.md",
    "docs/security/security-model.md",
]

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


def main() -> int:
    check("Project root is on D-drive", ROOT.drive.upper() == "D:", str(ROOT))
    check("Python >= 3.11", sys.version_info >= (3, 11), sys.version.split()[0])
    check(
        "Running inside project venv",
        Path(sys.prefix).resolve() == (ROOT / "venv").resolve(),
        sys.prefix,
    )
    check("64-bit interpreter", sys.maxsize > 2**32)

    for d in REQUIRED_DIRS:
        check(f"Dir exists: {d}", (ROOT / d).is_dir())
    for f in REQUIRED_DOCS:
        check(f"Doc exists: {f}", (ROOT / f).is_file())

    usage = shutil.disk_usage(ROOT)
    free_gb = usage.free / 1024**3
    check(
        f"D-drive free space >= {LOW_SPACE_GB} GB",
        free_gb >= LOW_SPACE_GB,
        f"{free_gb:.1f} GB free",
    )

    temp_dir = ROOT / "data" / "temp"
    os.environ["TEMP"] = os.environ["TMP"] = str(temp_dir)
    tempfile.tempdir = None
    probe_dir = Path(tempfile.gettempdir()).resolve()
    check("tempfile resolves to D-drive data/temp", probe_dir == temp_dir.resolve(), str(probe_dir))
    try:
        with tempfile.NamedTemporaryFile(dir=temp_dir, delete=True) as fh:
            fh.write(b"probe")
        check("data/temp writable", True)
    except OSError as exc:
        check("data/temp writable", False, str(exc))

    con = sqlite3.connect(":memory:")
    try:
        con.execute("PRAGMA foreign_keys=ON")
        fk = con.execute("PRAGMA foreign_keys").fetchone()[0]
        check("SQLite available with foreign keys", fk == 1, sqlite3.sqlite_version)
    finally:
        con.close()

    pip_ini = ROOT / "venv" / "pip.ini"
    check(
        "venv pip.ini redirects cache to D-drive",
        pip_ini.is_file() and "D:\\imagetrace\\.cache\\pip" in pip_ini.read_text(encoding="utf-8"),
    )

    width = max(len(n) for n, _, _ in results)
    failed = 0
    for name, ok, detail in results:
        failed += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name.ljust(width)}  {detail}")
    print(f"\n{len(results) - failed}/{len(results)} checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
