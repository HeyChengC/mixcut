"""Frame sheet of a stretch of video, for studying cuts, transitions and type frame by frame.

  fsheet.py video.mp4 --from 4.2 --to 5.0 [--step 0.0333 | --every 1] [--cols 8] [--w 240] --out x.jpg

--every N  every Nth source frame (1 = all frames).  --step S  one frame each S seconds (default 0.25).
Each tile is labelled with its time (s) and frame number.
"""
import argparse, subprocess, sys, os
import numpy as np
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mc_common import FFMPEG, probe

ap = argparse.ArgumentParser()
ap.add_argument("video"); ap.add_argument("--from", dest="t0", type=float, default=0.0)
ap.add_argument("--to", dest="t1", type=float, default=None)
ap.add_argument("--step", type=float, default=0.25); ap.add_argument("--every", type=int, default=0)
ap.add_argument("--cols", type=int, default=8); ap.add_argument("--w", type=int, default=240)
ap.add_argument("--out", required=True)
a = ap.parse_args()

m = probe(a.video)
W, H, fps = m["width"], m["height"], m["fps"]
t1 = a.t1 if a.t1 is not None else m["duration"]
h = int(round(a.w * H / W / 2)) * 2
cmd = [FFMPEG, "-v", "error", "-ss", f"{max(0, a.t0 - 0.0):.4f}", "-t", f"{t1 - a.t0 + 0.001:.4f}", "-i", a.video, "-an",
       "-vf", f"scale={a.w}:{h}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
raw = subprocess.run(cmd, capture_output=True).stdout
n = len(raw) // (a.w * h * 3)
frames = np.frombuffer(raw[: n * a.w * h * 3], np.uint8).reshape(n, h, a.w, 3)
if a.every:
    idx = list(range(0, n, a.every))
else:
    idx = sorted(set(min(n - 1, int(round(k * a.step * fps))) for k in range(int((t1 - a.t0) / a.step + 1e-6) + 1)))
lab = 16
rows = (len(idx) + a.cols - 1) // a.cols
sheet = Image.new("RGB", (a.cols * (a.w + 2), rows * (h + lab + 2)), (24, 24, 26))
d = ImageDraw.Draw(sheet)
try:
    f = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 12)
except Exception:
    f = ImageFont.load_default()
for k, i in enumerate(idx):
    x, y = (k % a.cols) * (a.w + 2), (k // a.cols) * (h + lab + 2)
    sheet.paste(Image.fromarray(frames[i]), (x, y + lab))
    t = a.t0 + i / fps
    d.text((x + 2, y + 1), f"{t:6.3f}s  f{int(round(t * fps))}", fill=(250, 220, 90), font=f)
sheet.save(a.out, quality=82)
print(a.out, sheet.size, f"{len(idx)} tiles")
