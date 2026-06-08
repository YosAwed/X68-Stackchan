"""Fade Pekeko face image side edges into the display background."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from face_postprocess import fade_side_edges

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def main() -> None:
    files = sorted(DATA_DIR.glob("face_*.jpg"))
    if not files:
        raise SystemExit(f"no face_*.jpg in {DATA_DIR}")
    for path in files:
        out = fade_side_edges(Image.open(path))
        out.save(path, "JPEG", quality=92, optimize=True, progressive=False)
        print(path.name)


if __name__ == "__main__":
    main()
