"""ぺけ子ちゃん表情シート 4 枚 (1254x1254, 3x3 グリッド) を
36 枚の 240x240 JPG にスライスして firmware/data/face_NN.jpg として保存する。

JPG にしているのは ESP32-S3 の LittleFS 領域節約のため。アニメ調なので
quality=92 でも見分けが付かない。

実行:
    python3 firmware/tools/slice.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent          # firmware/tools/
FIRMWARE = HERE.parent                          # firmware/
SRC_DIR  = FIRMWARE / "data" / "raw"            # 元シートを置く場所
OUT_DIR  = FIRMWARE / "data"                    # LittleFS root に直接書き出す

TARGET = 240  # 出力サイズ (CoreS3 320x240 に中央寄せ)


def find_sheets() -> list[Path]:
    pngs = sorted(p for p in SRC_DIR.glob("*.png"))
    if not pngs:
        sys.exit(f"no .png in {SRC_DIR}")
    return pngs


def slice_grid(img: Image.Image) -> list[Image.Image]:
    w, h = img.size
    cw, ch = w // 3, h // 3
    out: list[Image.Image] = []
    for ry in range(3):
        for cx in range(3):
            cell = img.crop((cx * cw, ry * ch, (cx + 1) * cw, (ry + 1) * ch))
            # 番号ラベル (左上 ~13%) を背景色で塗りつぶす
            d = ImageDraw.Draw(cell)
            pad = int(min(cw, ch) * 0.13)
            bg = cell.getpixel((4, 4))
            d.rectangle((0, 0, pad, pad), fill=bg)

            # 顔は概ね上端〜中央。 正方形に整えて 240 にリサイズ
            side = min(cw, ch)
            left = (cw - side) // 2
            top = 0
            cell = cell.crop((left, top, left + side, top + side))
            cell = cell.resize((TARGET, TARGET), Image.LANCZOS)
            out.append(cell)
    return out


def main() -> None:
    sheets = find_sheets()
    if len(sheets) != 4:
        sys.exit(f"expected 4 sheets, got {len(sheets)}: {sheets}")

    idx = 1
    for sheet in sheets:
        print(f"slice {sheet.name}")
        img = Image.open(sheet).convert("RGB")
        for cell in slice_grid(img):
            out = OUT_DIR / f"face_{idx:02d}.jpg"
            cell.save(out, "JPEG", quality=92, optimize=True, progressive=False)
            idx += 1

    total = sum((OUT_DIR / f"face_{i:02d}.jpg").stat().st_size for i in range(1, 37))
    print(f"wrote {idx - 1} files to {OUT_DIR}  total={total/1024:.1f} KB")


if __name__ == "__main__":
    main()
