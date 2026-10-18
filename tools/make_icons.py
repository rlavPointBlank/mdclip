#!/usr/bin/env python3
"""
Regenerate the extension icons.

They are drawn from primitives rather than vendored as binary assets, so the
artwork is reviewable in the same way the rest of this repo is: the file you
read is what produces the PNGs.

    python3 tools/make_icons.py        # requires Pillow
"""
import os
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icons")


def icon(size):
    # Drawn 8x then downscaled: PIL has no antialiased vector drawing, so
    # supersampling is what keeps the 16px icon from looking ragged.
    s = size * 8
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=(31, 41, 55, 255))

    cx = s // 2
    shaft = int(s * 0.10)
    top, mid = int(s * 0.20), int(s * 0.54)
    d.rounded_rectangle([cx - shaft // 2, top, cx + shaft // 2, mid],
                        radius=shaft // 2, fill=(96, 165, 250, 255))
    head = int(s * 0.17)
    d.polygon([(cx - head, mid - int(s * 0.03)),
               (cx + head, mid - int(s * 0.03)),
               (cx, mid + int(s * 0.17))], fill=(96, 165, 250, 255))
    bar_y, bar_h = int(s * 0.79), int(s * 0.085)
    d.rounded_rectangle([int(s * 0.22), bar_y, int(s * 0.78), bar_y + bar_h],
                        radius=bar_h // 2, fill=(226, 232, 240, 255))
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for n in (16, 32, 48, 128):
        icon(n).save(os.path.join(OUT, "icon%d.png" % n))
        print("wrote icons/icon%d.png" % n)
