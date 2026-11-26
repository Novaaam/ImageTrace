"""Detect optional external tools (bundled third-party folder, custom path, or PATH)."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from imagetrace.paths import project_root


@dataclass(frozen=True)
class ToolStatus:
    name: str
    path: Path | None
    needed_from: str

    @property
    def found(self) -> bool:
        return self.path is not None


def _find(exe: str, bundled: Path, custom: str = "") -> Path | None:
    if custom:
        candidate = Path(custom)
        if candidate.is_file():
            return candidate
        return None
    if bundled.is_file():
        return bundled
    found = shutil.which(exe)
    return Path(found) if found else None


def find_exiftool(custom_path: str = "") -> Path | None:
    third = project_root() / "tools" / "third-party"
    return _find("exiftool", third / "exiftool" / "exiftool.exe", custom_path)


def find_tesseract(custom_path: str = "") -> Path | None:
    third = project_root() / "tools" / "third-party"
    return _find("tesseract", third / "tesseract" / "tesseract.exe", custom_path)


def detect_tools(
    exiftool_custom_path: str = "", tesseract_custom_path: str = ""
) -> list[ToolStatus]:
    return [
        ToolStatus("ExifTool", find_exiftool(exiftool_custom_path), "Phase 4"),
        ToolStatus(
            "Tesseract OCR (optional)",
            find_tesseract(tesseract_custom_path),
            "Phase 6",
        ),
    ]
