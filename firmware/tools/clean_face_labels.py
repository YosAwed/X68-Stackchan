"""Remove sprite-sheet index labels from the top-left corner of face JPGs.

The source sheets include small hand-written index numbers in the top-left
white background area. They are useful while slicing, but visible on CoreS3.

Run:
    python3 firmware/tools/clean_face_labels.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from face_postprocess import remove_index_label

HERE = Path(__file__).resolve().parent
FIRMWARE = HERE.parent
DATA_DIR = FIRMWARE / "data"

def clean_file(path: Path) -> None:
    im = Image.open(path).convert("RGB")
    im = remove_index_label(im)
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
