"""Shared post-processing for exported Pekeko face images."""

from __future__ import annotations

from statistics import median

from PIL import Image, ImageDraw

# Label marks fit inside this top-left background area after 240px export.
# Keep the rectangle above the face/headphone area and above emotion marks.
LABEL_RECT = (0, 0, 50, 34)
BACKGROUND_MIN = 220
TARGET_CONTENT_BOTTOM = 239
CONTENT_THRESHOLD = 180
MIN_DARK_PIXELS_PER_ROW = 20
WHITE = (255, 255, 255)
DISPLAY_BG = (33, 32, 33)  # firmware/include/pekeko_theme.h X68_BG (RGB565 0x2104)
EDGE_FADE_WIDTH = 18


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
        return WHITE
    return tuple(int(median(channel)) for channel in zip(*samples))


def remove_index_label(im: Image.Image) -> Image.Image:
    out = im.convert("RGB").copy()
    ImageDraw.Draw(out).rectangle(LABEL_RECT, fill=background_color(out))
    return out


def content_bottom(im: Image.Image) -> int | None:
    """Return the last row that contains enough visible non-background pixels."""
    im = im.convert("RGB")
    px = im.load()
    for y in range(im.height - 1, -1, -1):
        dark = 0
        for x in range(im.width):
            r, g, b = px[x, y]
            if min(r, g, b) < CONTENT_THRESHOLD:
                dark += 1
                if dark >= MIN_DARK_PIXELS_PER_ROW:
                    return y
    return None


def align_bottom(im: Image.Image, target: int = TARGET_CONTENT_BOTTOM) -> Image.Image:
    bottom = content_bottom(im)
    if bottom is None or bottom >= target:
        return im.convert("RGB")
    shift_y = target - bottom
    out = Image.new("RGB", im.size, WHITE)
    out.paste(im.convert("RGB"), (0, shift_y))
    return out


def fade_side_edges(im: Image.Image, width: int = EDGE_FADE_WIDTH) -> Image.Image:
    """Blend the left/right edges into the display background color."""
    out = im.convert("RGB").copy()
    px = out.load()
    w, h = out.size
    fade = max(1, min(width, w // 2))

    for x in range(fade):
        t = x / fade
        bg_weight = (1.0 - t) ** 2
        for y in range(h):
            for xx in (x, w - 1 - x):
                r, g, b = px[xx, y]
                px[xx, y] = (
                    int(r * (1.0 - bg_weight) + DISPLAY_BG[0] * bg_weight),
                    int(g * (1.0 - bg_weight) + DISPLAY_BG[1] * bg_weight),
                    int(b * (1.0 - bg_weight) + DISPLAY_BG[2] * bg_weight),
                )
    return out


def postprocess_image(im: Image.Image) -> Image.Image:
    return fade_side_edges(align_bottom(remove_index_label(im)))
