"""ぺけ子ちゃん表情シート 4 枚 (1254x1254, 3x3 グリッド) を
36 枚の 240x240 PNG にスライスして face_NN.png として保存する。

実行:
    python3 slice.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE.parent / "faces"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TARGET = 240  # 出力サイズ (CoreS3 320x240 に収まる)


def find_sheets() -> list[Path]:
    pngs = sorted(p for p in HERE.glob("*.png"))
    if not pngs:
        sys.exit(f"no .png in {HERE}")
    return pngs


def slice_grid(img: Image.Image) -> list[Image.Image]:
    """3x3 に分割。各セルから番号ラベルを避けるため中央をクロップして 240 にリサイズ"""
    w, h = img.size
    cw, ch = w // 3, h // 3
    cells: list[Image.Image] = []
    for ry in range(3):
        for cx in range(3):
            cell = img.crop((cx * cw, ry * ch, (cx + 1) * cw, (ry + 1) * ch))
            # 番号は左上の十数 px。安全めに上 8% / 左 6% を黒の代わりに背景色で潰す。
            # ぺけ子ちゃんは背景が真っ白なので、左上 12% x 12% を白で塗る。
            d = ImageDraw.Draw(cell)
            pad_x = int(cw * 0.13)
            pad_y = int(ch * 0.13)
            # 左上のラベル領域を背景色 (左上の元 pixel) で塗りつぶす
            bg = cell.getpixel((4, 4))  # 左上隅のピクセル
            d.rectangle((0, 0, pad_x, pad_y), fill=bg)

            # 顔は概ね中央上 60% に収まる。中央寄せでクロップして正方形に。
            # cell は 418x418 程度。少し下に重心があるので、下 25% を残すように切る
            side = min(cw, ch)
            left = (cw - side) // 2
            # 上を 0、下を side。240x240 リサイズで顔がきれいに収まる。
            top = 0
            cell = cell.crop((left, top, left + side, top + side))
            cell = cell.resize((TARGET, TARGET), Image.LANCZOS)
            cells.append(cell)
    return cells


def main() -> None:
    sheets = find_sheets()
    if len(sheets) != 4:
        sys.exit(f"expected 4 sheets, got {len(sheets)}: {sheets}")

    idx = 1
    for sheet in sheets:
        print(f"slice {sheet.name}")
        img = Image.open(sheet).convert("RGB")
        if img.size != (1254, 1254):
            print(f"  warn: size={img.size}, expected 1254x1254")
        for cell in slice_grid(img):
            # LittleFS 領域は限られているので JPG (quality=92) で保存
            out = OUT_DIR / f"face_{idx:02d}.jpg"
            cell.save(out, "JPEG", quality=92, optimize=True, progressive=False)
            idx += 1
    total_kb = sum((OUT_DIR / f"face_{i:02d}.jpg").stat().st_size for i in range(1, 37)) / 1024
    print(f"wrote {idx - 1} files to {OUT_DIR} ({total_kb:.1f} KB)")


if __name__ == "__main__":
    main()
