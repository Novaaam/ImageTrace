"""Storage-root resolution and health checks.

Rules (blueprint section 8):
- The default data root is ``<project>\\data``. It can be overridden with ``IMAGETRACE_DATA_ROOT``.
- If the data root is missing, we never silently fall back to another drive (e.g. C:).
  The caller gets a status object and must ask the user.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

LOW_SPACE_BYTES = 10 * 1024**3  # warn below 10 GiB
ENV_DATA_ROOT = "IMAGETRACE_DATA_ROOT"


def is_frozen() -> bool:
    """Return True when running from a PyInstaller-packaged executable."""
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    """Return executable directory when frozen, or repository root when run from source."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def resources_dir() -> Path:
    """Locate UI resource assets (SVG icons, etc.) in source or PyInstaller one-dir mode."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cand = Path(meipass) / "resources"
            if cand.is_dir():
                return cand
        return project_root() / "resources"
    return project_root() / "app" / "resources"


def local_config_path() -> Path:
    """Machine-local bootstrap config (git-ignored). Holds only the chosen data-root path."""
    return project_root() / "imagetrace.local.json"


def configured_data_root() -> Path | None:
    try:
        raw = json.loads(local_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = raw.get("data_root") if isinstance(raw, dict) else None
    return Path(value) if isinstance(value, str) and value.strip() else None


def save_configured_data_root(path: Path) -> None:
    target = local_config_path()
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps({"data_root": str(path)}, indent=2), encoding="utf-8")
    os.replace(tmp, target)


def default_data_root() -> Path:
    """Priority: IMAGETRACE_DATA_ROOT env var > remembered location > <project>/data."""
    override = os.environ.get(ENV_DATA_ROOT)
    if override:
        return Path(override)
    configured = configured_data_root()
    if configured is not None:
        return configured
    return project_root() / "data"


@dataclass(frozen=True)
class StorageStatus:
    root: Path
    available: bool
    free_bytes: int | None = None
    total_bytes: int | None = None
    message: str = ""

    @property
    def low_space(self) -> bool:
        return self.free_bytes is not None and self.free_bytes < LOW_SPACE_BYTES

    @property
    def free_gb(self) -> float | None:
        return None if self.free_bytes is None else self.free_bytes / 1024**3


def check_storage(root: Path) -> StorageStatus:
    """Check that *root* exists, is a writable directory, and report free space.

    Does not create the directory and does not touch any other location.
    """
    if not root.exists():
        return StorageStatus(root, False, message=f"Data root not found: {root}")
    if not root.is_dir():
        return StorageStatus(root, False, message=f"Data root is not a directory: {root}")
    if not os.access(root, os.W_OK):
        return StorageStatus(root, False, message=f"Data root is not writable: {root}")
    usage = shutil.disk_usage(root)
    status = StorageStatus(root, True, usage.free, usage.total)
    if status.low_space:
        return StorageStatus(
            root,
            True,
            usage.free,
            usage.total,
            message=f"Low free space: {status.free_gb:.1f} GB remaining",
        )
    return status


def format_bytes(n: int | None) -> str:
    if n is None:
        return "unknown"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{n} B"  # unreachable
