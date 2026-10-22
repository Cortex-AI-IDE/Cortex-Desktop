"""Generate the tile images an MSIX package must ship.

The Store shows these in the app list, the Start tiles and the listing.
They are derived from the same logo the .exe uses, so the two channels
always look identical.

Non-square tiles (Wide310x150) are letterboxed on a transparent canvas
rather than stretched, because a squashed logo is an easy certification
comment and looks careless next to other IDEs.

Usage:  python make_assets.py <source-logo> <output-dir>
"""
from __future__ import annotations

import sys
from pathlib import Path

# name -> (width, height). Square150x150, Square44x44 and StoreLogo are the
# ones the manifest requires; the rest make Start-menu tiles look right.
TILES: dict[str, tuple[int, int]] = {
    "StoreLogo": (50, 50),
    "Square44x44Logo": (44, 44),
    "Square71x71Logo": (71, 71),
    "Square150x150Logo": (150, 150),
    "Square310x310Logo": (310, 310),
    "Wide310x150Logo": (310, 150),
}


def build(source: Path, out_dir: Path) -> list[Path]:
    from PIL import Image

    # Pillow 9.1 moved the constants under Image.Resampling and kept the old
    # names as aliases; prefer the canonical one, fall back for older Pillow.
    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS")

    if not source.is_file():
        raise SystemExit(f"source logo not found: {source}")
    out_dir.mkdir(parents=True, exist_ok=True)

    logo = Image.open(source).convert("RGBA")
    written: list[Path] = []

    for name, (width, height) in TILES.items():
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        # Fit inside the tile, preserving aspect ratio, with a small margin
        # so the icon does not touch the tile edge.
        box = int(min(width, height) * 0.9) or 1
        scaled = logo.copy()
        scaled.thumbnail((box, box), resample)
        canvas.paste(
            scaled,
            ((width - scaled.width) // 2, (height - scaled.height) // 2),
            scaled,
        )
        target = out_dir / f"{name}.png"
        canvas.save(target, "PNG")
        written.append(target)

    return written


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    written = build(Path(sys.argv[1]), Path(sys.argv[2]))
    for path in written:
        print(f"  [OK] {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
