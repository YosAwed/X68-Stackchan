"""Remove sprite-sheet index labels from the top-left corner of face JPGs.

The source sheets include small hand-written index numbers in the top-left
white background area. They are useful while slicing, but visible on CoreS3.

Run:
    python3 firmware/tools/clean_face_labels.py
"""

from __future__ import annotations

from pathlib import Path
from statistics import median

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
FIRMWARE = HERE.parent
DATA_DIR = FIRMWARE / "data"

# Label marks fit inside this top-left background area after 240px export.
# Keep the rectangle above the face/headphone area and above emotion marks.
LABEL_RECT = (0, 0, 50, 34)
BACKGROUND_MIN = 220


def background_color(im: Image.Image) -> tuple[int, int, int]:
    """Estimate the white-ish paper background near the label."""
    x0, y0, x1, y1 = LABEL_RECT
    samples: list[tuple[int, int, int]] = []
    px = im.load()
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            r, g, b = px[x, y]
            if r >= BACKGROUND_MIN and g >= BACKGROUND_MIN and b >= BACKGROUND_MIN:
                samples.append((r, g, b))
    if not samples:
        return (255, 255, 255)
    return tuple(int(median(channel)) for channel in zip(*samples))


def clean_file(path: Path) -> None:
    im = Image.open(path).convert("RGB")
    fill = background_color(im)
    ImageDraw.Draw(im).rectangle(LABEL_RECT, fill=fill)
    im.save(path, "JPEG", quality=92, optimize=True, progressive=False)


def main() -> None:
    files = sorted(DATA_DIR.glob("face_*.jpg"))
    if not files:
        raise SystemExit(f"no face_*.jpg in {DATA_DIR}")
    for path in files:
        clean_file(path)
        print(f"cleaned {path.name}")


if __name__ == "__main__":
    main()
