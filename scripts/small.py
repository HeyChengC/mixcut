"""Shrunk copies of images for looking at, so a conversation does not fill up with big pictures.

  small.py <img> [<img> ...] [--w 1000] [--out _view]      -> <out>/<name>.jpg   (default: ./_view)

Contact sheets, cuts.jpg and peek sheets are often 2000+ px wide; look at them only through this (<= 1000 px).
"""
import argparse, os
from PIL import Image

ap = argparse.ArgumentParser()
ap.add_argument("images", nargs="+")
ap.add_argument("--w", type=int, default=1000)
ap.add_argument("--out", default="_view")
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
for p in a.images:
    im = Image.open(p).convert("RGB")
    if im.width > a.w:
        im = im.resize((a.w, int(im.height * a.w / im.width)), Image.LANCZOS)
    o = os.path.join(a.out, os.path.basename(p).rsplit(".", 1)[0] + ".jpg")
    im.save(o, quality=72)
    print(o, im.size, os.path.getsize(o) // 1024, "KB")
