"""PyInstaller frozen entry point for ImageTrace (one-directory Windows build)."""

from __future__ import annotations

import sys

from imagetrace.app import main

if __name__ == "__main__":
    sys.exit(main())
