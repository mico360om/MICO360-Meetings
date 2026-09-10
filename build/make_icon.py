"""Generate the MICO360 Meetings app icon from the brand identity.

Produces a square, legible-at-16px mark: a brand-maroon squircle carrying the
"360" swoosh motif around a bold white "M". Renders at high resolution and
downsamples with anti-aliasing into a multi-resolution .ico plus PNG previews.

Run:  python build/make_icon.py
"""
from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
ASSETS = os.path.join(ROOT, "assets")

# Brand palette (from the MICO360 logo / app theme).
MAROON_TOP = (158, 34, 34)     # #9E2222
MAROON_BOT = (110, 20, 20)     # #6E1414
CHARCOAL = (26, 22, 26)        # #1A161A
WHITE = (255, 255, 255)
OFF_WHITE = (243, 240, 240)

S = 1024                       # super-sampled working size
R = int(S * 0.235)             # squircle corner radius (iOS-ish)


def _rounded_mask(size: int, radius: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def _vertical_gradient(size: int, top, bot) -> Image.Image:
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        grad.putpixel((0, y), tuple(round(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    return grad.resize((size, size))


def _load_bold_font(px: int) -> ImageFont.FreeTypeFont:
    for name in ("segoeuib.ttf", "arialbd.ttf", "Arialbd.ttf", "seguisb.ttf", "ariblk.ttf"):
        for base in (r"C:\Windows\Fonts", "/usr/share/fonts", ASSETS):
            p = os.path.join(base, name)
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, px)
                except Exception:
                    pass
    return ImageFont.load_default()


def build_master() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # --- squircle body with maroon gradient + subtle top sheen -------------
    body = _vertical_gradient(S, MAROON_TOP, MAROON_BOT).convert("RGBA")
    mask = _rounded_mask(S, R)
    img.paste(body, (0, 0), mask)

    draw = ImageDraw.Draw(img)

    # soft inner sheen near the top for depth
    sheen = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sheen)
    sd.ellipse([int(S * -0.15), int(S * -0.55), int(S * 1.15), int(S * 0.35)],
               fill=(255, 255, 255, 26))
    img = Image.alpha_composite(img, Image.composite(
        sheen, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask))
    draw = ImageDraw.Draw(img)

    # --- the 360 swoosh: an open white ring sweeping up to an arrowhead ----
    # (the full circular orbit *is* the "360"; the arrowhead echoes the logo's
    #  upward growth arrow — legible at every size, no cramped text).
    cx, cy = S / 2, S / 2
    ring_r = int(S * 0.325)
    ring_w = int(S * 0.058)
    bbox = [cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r]
    # gap at upper-right; sweep clockwise the long way round to just past the top
    start, end = -35, 250
    draw.arc(bbox, start=start, end=end, fill=OFF_WHITE, width=ring_w)

    # arrowhead at the `start` end (upper-right), pointing up along the tangent
    a = math.radians(start)
    tx, ty = cx + ring_r * math.cos(a), cy + ring_r * math.sin(a)
    td = a - math.radians(90)                      # clockwise travel direction
    tip = (tx + math.cos(td) * S * 0.10, ty + math.sin(td) * S * 0.10)
    perp = td + math.radians(90)
    half = S * 0.072
    b1 = (tx + math.cos(perp) * half, ty + math.sin(perp) * half)
    b2 = (tx - math.cos(perp) * half, ty - math.sin(perp) * half)
    draw.polygon([tip, b1, b2], fill=OFF_WHITE)

    # --- bold "M" centred inside the ring ---------------------------------
    font = _load_bold_font(int(S * 0.52))
    txt = "M"
    l, t, r, b = draw.textbbox((0, 0), txt, font=font)
    tw, th = r - l, b - t
    draw.text((cx - tw / 2 - l, cy - th / 2 - t), txt, font=font, fill=WHITE)

    return img


def main() -> None:
    master = build_master()
    master.save(os.path.join(ASSETS, "app_icon_1024.png"))

    # 256 PNG for in-app logo panels
    master.resize((256, 256), Image.LANCZOS).save(os.path.join(ASSETS, "logo_256.png"))

    # multi-resolution .ico (downsample each size from the master for crisp small icons)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [master.resize((s, s), Image.LANCZOS) for s in sizes]
    frames[-1].save(
        os.path.join(ASSETS, "app.ico"),
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[:-1],
    )
    print("Wrote assets/app.ico, assets/logo_256.png, assets/app_icon_1024.png")
    print("ICO sizes:", sizes)


if __name__ == "__main__":
    main()
