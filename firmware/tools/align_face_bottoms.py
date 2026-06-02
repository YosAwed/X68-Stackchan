"""顔 JPG の bbox 中心を統一する後処理スクリプト (縦横 2D アライメント)。

ぺけ子ちゃんの 36 表情 (firmware/data/face_NN.jpg) は元のスプライトシートを
スライスしただけだと「顔のあるエリア」の縦横位置がセルごとにズレている。

  - 縦: content_top が 0..17、content_bottom が 234..239 (最大 5 px ズレ)
  - 横: 中心 X が 111..128 (最大 17 px ズレ)
  - シートのセル区切りに当たる JPG では下端 1px が真っ白 (白ライン)

このスクリプトは各 JPG について:
  1. 「行の 5% 以上が暗いピクセル」となる行 / 列を bbox 端点とする。
     細い 1〜2 ピクセルのスケッチ線 (髪先など) は無視する。
  2. bbox 中心を求める。
  3. (120, 120) に bbox 中心を合わせる縦横シフト量を計算する。
  4. 240x240 白キャンバスに元画像をシフトして貼り、再書き出しする。

結果として全 36 顔の表情中心が同じ位置に揃い、表情遷移時の縦横ガタつき
および白ライン問題が解消する。

実行:
    python3 firmware/tools/align_face_bottoms.py

冪等。元画像のバックアップは git 履歴 (もしくは事前に別ディレクトリへ cp)
で取っておくこと。スクリプト名は歴史的事情で bottoms のまま (内部は
2D アライメントを行う)。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent          # firmware/tools/
FIRMWARE = HERE.parent                          # firmware/
DATA_DIR = FIRMWARE / "data"                    # face_NN.jpg 一式

SIZE = 240                  # 出力サイズ (CoreS3 320x240 に中央寄せ前提)
TARGET_CX = SIZE // 2       # bbox 中心 X 目標
TARGET_CY = SIZE // 2       # bbox 中心 Y 目標
BG_THRESH = 220             # これ未満のチャネルがあれば「中身」とみなす
DARK_RATIO_MIN = 0.05       # 行/列内サンプルがこの割合以上 dark なら content
SAMPLE_STEP = 2             # 行/列内サンプル間隔 (px)
WHITE = (255, 255, 255)

# NOTE: 一時期、白背景を画面の dark grey に置換する処理を入れていたが、
# JPEG 再圧縮で文字周りに灰色ハロー (avg 200 前後) が出て画面上で「汚い」絵
# になったので撤回。アライメントのみ行い、白背景はそのまま残す方針。
# 画面下端の白いラインは firmware 側で 1px fillRect する。


def find_bbox(im: Image.Image) -> tuple[int, int, int, int]:
    """(top, bottom, left, right) を返す。
    行/列内サンプルの DARK_RATIO_MIN 以上が暗い時のみ "content 行/列" とみなす
    ので、1〜2 ピクセル幅の細線で誤検知しない。
    加えて、上下に近傍 content が無い「孤立した行 / 列」は無視する。これにより
    シートのセル境界線が顔の上端 (y=0) に貫通して残ったケースで、本来の
    顔輪郭まで bbox top を進めることができる。"""
    w, h = im.size
    px = im.load()
    samples_x = len(range(0, w, SAMPLE_STEP))
    samples_y = len(range(0, h, SAMPLE_STEP))
    thr_x = max(2, int(samples_x * DARK_RATIO_MIN))
    thr_y = max(2, int(samples_y * DARK_RATIO_MIN))

    def row_dark(y: int) -> bool:
        n = 0
        for x in range(0, w, SAMPLE_STEP):
            r, g, b = px[x, y]
            if r < BG_THRESH or g < BG_THRESH or b < BG_THRESH:
                n += 1
                if n >= thr_x:
                    return True
        return False

    def col_dark(x: int) -> bool:
        n = 0
        for y in range(0, h, SAMPLE_STEP):
            r, g, b = px[x, y]
            if r < BG_THRESH or g < BG_THRESH or b < BG_THRESH:
                n += 1
                if n >= thr_y:
                    return True
        return False

    # 近傍 (±4 px) のいずれかが content であれば「実体のある行/列」とみなす。
    # 孤立した 1〜2 ライン (セパレータの残り) はスキップされる。
    NEIGHBOR_OFFSETS = (2, 4, -2, -4)

    def real_row(y: int) -> bool:
        if not row_dark(y):
            return False
        for dy in NEIGHBOR_OFFSETS:
            ny = y + dy
            if 0 <= ny < h and row_dark(ny):
                return True
        return False

    def real_col(x: int) -> bool:
        if not col_dark(x):
            return False
        for dx in NEIGHBOR_OFFSETS:
            nx = x + dx
            if 0 <= nx < w and col_dark(nx):
                return True
        return False

    top = next((y for y in range(h) if real_row(y)), 0)
    bot = next((y for y in range(h - 1, -1, -1) if real_row(y)), h - 1)
    left = next((x for x in range(w) if real_col(x)), 0)
    right = next((x for x in range(w - 1, -1, -1) if real_col(x)), w - 1)
    return top, bot, left, right


def main() -> None:
    files = sorted(DATA_DIR.glob("face_*.jpg"))
    if not files:
        raise SystemExit(f"no face_*.jpg in {DATA_DIR}")

    print(f"{'file':>14}  bbox(t,b,l,r)         cx, cy   shift(x,y)")
    for f in files:
        im = Image.open(f).convert("RGB")
        if im.size != (SIZE, SIZE):
            print(f"  skip {f.name} (size={im.size})")
            continue

        top, bot, left, right = find_bbox(im)
        cx = (left + right) // 2
        cy = (top + bot) // 2
        shift_x = TARGET_CX - cx
        shift_y = TARGET_CY - cy

        print(f"  {f.name:>12}  ({top:3d},{bot:3d},{left:3d},{right:3d})   "
              f"({cx:3d},{cy:3d})  ({shift_x:+3d},{shift_y:+3d})")

        # アライメント (白キャンバスに貼り直す)。背景は元の白のままにする。
        if shift_x == 0 and shift_y == 0:
            continue
        canvas = Image.new("RGB", (SIZE, SIZE), WHITE)
        canvas.paste(im, (shift_x, shift_y))
        canvas.save(f, "JPEG", quality=92, optimize=True, progressive=False)

    print("done.")


if __name__ == "__main__":
    main()
