"""Generate crisp multi-resolution ImageTrace application icons using Pillow (zero extra deps).

Outputs:
    app/resources/imagetrace.ico  (16, 24, 32, 48, 64, 128, 256 px)
    app/resources/imagetrace.png  (256 px)
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "app" / "resources"


def draw_icon(size: int = 256) -> Image.Image:
    """Render a restrained forensic-workstation icon: framed image card + precision lens."""
    s = size * 2  # 2x supersampling for crisp anti-aliased edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Rounded deep-navy slate tile
    pad = int(s * 0.06)
    rad = int(s * 0.18)
    d.rounded_rectangle([pad, pad, s - pad, s - pad], radius=rad, fill=(16, 42, 67, 255))
    # Subtle outer rim
    d.rounded_rectangle(
        [pad, pad, s - pad, s - pad],
        radius=rad,
        outline=(76, 155, 232, 200),
        width=max(2, s // 64),
    )

    # Clean image frame inside tile (upper-left shifted)
    fx0, fy0 = int(s * 0.18), int(s * 0.20)
    fx1, fy1 = int(s * 0.72), int(s * 0.68)
    d.rounded_rectangle(
        [fx0, fy0, fx1, fy1],
        radius=int(s * 0.04),
        fill=(244, 246, 248, 255),
        outline=(188, 204, 220, 255),
        width=max(2, s // 80),
    )
    # Minimal geometric landscape bars inside image frame (evidence photo motif)
    d.polygon(
        [
            (fx0 + int(s * 0.05), fy1 - int(s * 0.04)),
            (fx0 + int(s * 0.20), fy0 + int(s * 0.22)),
            (fx0 + int(s * 0.34), fy1 - int(s * 0.04)),
        ],
        fill=(51, 78, 104, 255),
    )
    d.polygon(
        [
            (fx0 + int(s * 0.24), fy1 - int(s * 0.04)),
            (fx0 + int(s * 0.40), fy0 + int(s * 0.16)),
            (fx1 - int(s * 0.04), fy1 - int(s * 0.04)),
        ],
        fill=(72, 101, 129, 255),
    )
    # Small sun/point circle in top-left of frame
    sr = int(s * 0.045)
    scx, scy = fx0 + int(s * 0.12), fy0 + int(s * 0.12)
    d.ellipse([scx - sr, scy - sr, scx + sr, scy + sr], fill=(11, 92, 173, 255))

    # Inspection magnifying lens overlapping lower-right (forensic examination)
    cx, cy = int(s * 0.65), int(s * 0.63)
    lr = int(s * 0.16)
    ring_w = max(4, int(s * 0.045))
    # Handle
    hx0, hy0 = cx + int(lr * 0.72), cy + int(lr * 0.72)
    hx1, hy1 = int(s * 0.87), int(s * 0.85)
    d.line([(hx0, hy0), (hx1, hy1)], fill=(76, 155, 232, 255), width=max(5, int(s * 0.065)))
    d.ellipse(
        [hx1 - int(s * 0.03), hy1 - int(s * 0.03), hx1 + int(s * 0.03), hy1 + int(s * 0.03)],
        fill=(76, 155, 232, 255),
    )
    # Lens interior + precision crosshair + ring
    d.ellipse([cx - lr, cy - lr, cx + lr, cy + lr], fill=(224, 242, 254, 235))
    ch = int(lr * 0.55)
    cw = max(2, s // 96)
    d.line([(cx - ch, cy), (cx + ch, cy)], fill=(11, 92, 173, 220), width=cw)
    d.line([(cx, cy - ch), (cx, cy + ch)], fill=(11, 92, 173, 220), width=cw)
    d.ellipse(
        [cx - lr, cy - lr, cx + lr, cy + lr],
        outline=(76, 155, 232, 255),
        width=ring_w,
    )

    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> int:
    RESOURCES.mkdir(parents=True, exist_ok=True)
    base = draw_icon(256)
    png_path = RESOURCES / "imagetrace.png"
    ico_path = RESOURCES / "imagetrace.ico"
    base.save(png_path, format="PNG")
    base.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"Wrote {png_path} and {ico_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
