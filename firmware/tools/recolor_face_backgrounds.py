"""Recolor Pekeko face image paper backgrounds for camera-friendly display."""

from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

TARGET_BG = (200, 200, 200)
BACKGROUND_MIN = 210
MAX_NEUTRAL_DELTA = 24


def is_background_candidate(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return min(rgb) >= BACKGROUND_MIN and max(rgb) - min(rgb) <= MAX_NEUTRAL_DELTA


def recolor_background(im: Image.Image) -> Image.Image:
    out = im.convert("RGB").copy()
    w, h = out.size
    px = out.load()
    seen = bytearray(w * h)
    q: deque[tuple[int, int]] = deque()

    def enqueue(x: int, y: int) -> None:
        idx = y * w + x
        if seen[idx] or not is_background_candidate(px[x, y]):
            return
        seen[idx] = 1
        q.append((x, y))

    for x in range(w):
        enqueue(x, 0)
        enqueue(x, h - 1)
    for y in range(h):
        enqueue(0, y)
        enqueue(w - 1, y)

    while q:
        x, y = q.popleft()
        px[x, y] = TARGET_BG
        if x > 0:
            enqueue(x - 1, y)
        if x + 1 < w:
            enqueue(x + 1, y)
        if y > 0:
            enqueue(x, y - 1)
        if y + 1 < h:
            enqueue(x, y + 1)

    return out


def main() -> None:
    files = sorted(DATA_DIR.glob("face_*.jpg"))
    if not files:
        raise SystemExit(f"no face_*.jpg in {DATA_DIR}")
    for path in files:
        im = Image.open(path)
        out = recolor_background(im)
        out.save(path, quality=92, optimize=True)
        print(path.name)


if __name__ == "__main__":
    main()
