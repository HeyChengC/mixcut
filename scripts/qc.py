"""Check a render before anyone watches it.

  qc.py <render.mp4> [--every 0.25] [--cols 8]

Reads <render>.timeline.json (written by render.py) when present.
Prints: duration / size / loudness (LUFS, true peak) / black and frozen stretches / cuts off the beat grid /
shots that repeat a source moment. Writes:
  <render>.cuts.jpg   one tile per shot: the frame 2 frames after the cut, labelled  #i  t  beat  fx  note
  <render>.strip.jpg  a frame every --every seconds (same layout as the reference contact sheets)
"""
import argparse, json, math, re, subprocess, sys
from pathlib import Path
import numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0, str(Path(__file__).parent))
from mc_common import FFMPEG, probe, load_json, find_font, fmt_t


def frames_at(path, times, width=320):
    out = []
    for t in times:
        p = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{max(0, t):.3f}", "-i", str(path), "-frames:v", "1",
                            "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
        out.append(cv2.cvtColor(cv2.imdecode(np.frombuffer(p.stdout, np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB) if p.stdout else None)
    return out


def all_frames(path, every, width=240):
    """Frames every `every` seconds in one decode pass."""
    info = probe(path)
    h = int(round(width * info["height"] / info["width"] / 2)) * 2
    b = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vf", f"fps={1 / every},scale={width}:{h}", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    n = len(b) // (width * h * 3)
    return [np.frombuffer(b, np.uint8, width * h * 3, i * width * h * 3).reshape(h, width, 3) for i in range(n)]


def sheet(tiles, labels, cols, out, subs=None):
    f = ImageFont.truetype(find_font("consola.ttf", "arial.ttf"), 12)
    fz = ImageFont.truetype(find_font("msyh.ttc", "simhei.ttf", "arial.ttf"), 12)
    tiles = [t for t in tiles if t is not None]
    if not tiles:
        return
    th, tw = tiles[0].shape[:2]
    lab = 16 + (16 if subs else 0)
    rows = math.ceil(len(tiles) / cols)
    im = Image.new("RGB", (cols * tw, rows * (th + lab)), (20, 20, 20))
    d = ImageDraw.Draw(im)
    for i, t in enumerate(tiles):
        x, y = (i % cols) * tw, (i // cols) * (th + lab)
        im.paste(Image.fromarray(cv2.resize(t, (tw, th))), (x, y + lab))
        d.text((x + 2, y + 1), labels[i], fill=(255, 220, 0), font=f)
        if subs and subs[i]:
            d.text((x + 2, y + 16), subs[i][:40], fill=(150, 220, 255), font=fz)
    im.save(out, quality=85)
    print("sheet", out)


def loudness(path):
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", r)
    tp = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", r)
    return (float(i[-1]) if i else None), (float(tp[-1]) if tp else None)


def detect(path, flt, key):
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path), "-vf", flt, "-an", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    return re.findall(key, r)


def qc(path, every=0.25, cols=8):
    path = Path(path)
    info = probe(path)
    tl = load_json(str(path) + ".timeline.json")
    print(f"{path.name}: {info['duration']:.2f}s {info.get('width')}x{info.get('height')} {info.get('fps', 0):.2f}fps audio={info['has_audio']}")
    problems = []
    if info["has_audio"]:
        I, TP = loudness(path)
        print(f"  loudness {I} LUFS, true peak {TP} dBFS")
        if I is not None and not (-16.5 <= I <= -11.5):
            problems.append(f"loudness {I} LUFS (want about -14)")
        if TP is not None and TP > -0.5:
            problems.append(f"true peak {TP} dBFS (want <= -1)")
    blacks = detect(path, "blackdetect=d=0.08:pix_th=0.06", r"black_start:([\d.]+) black_end:([\d.]+)")
    for a, b in blacks:
        a, b = float(a), float(b)
        if a > 0.05 and b < info["duration"] - 0.1:
            problems.append(f"black {a:.2f}-{b:.2f}s")
    frz = detect(path, "freezedetect=n=0.002:d=0.6", r"freeze_start: ([\d.]+)")
    for a in frz:
        problems.append(f"frozen picture from {float(a):.2f}s")
    labels, subs, times = [], [], []
    if tl:
        fps = tl["fps"]
        clips = tl["clips"]
        seen = {}
        for c in clips:
            t = c["t0"] + 2 / fps
            times.append(min(t, info["duration"] - 0.05))
            b = c.get("beat")
            labels.append(f"#{c['i']} {c['t0']:.2f}s b{b:g} {c.get('fx', 'cut')}" if b is not None else f"#{c['i']} {c['t0']:.2f}s")
            subs.append(c.get("note", ""))
            if b is not None and c["i"] > 0 and abs(b * 4 - round(b * 4)) > 0.12:
                problems.append(f"cut #{c['i']} at {c['t0']:.2f}s is off the grid (beat {b})")
            key = (c.get("src"), round(c["in"], 1) if c.get("in") is not None else None)
            if c.get("src") and key[1] is not None and key in seen:
                problems.append(f"#{c['i']} repeats the shot of #{seen[key]}")
            seen.setdefault(key, c["i"])
        lens = [c["t1"] - c["t0"] for c in clips]
        print(f"  {len(clips)} shots, mean {np.mean(lens):.2f}s, median {np.median(lens):.2f}s, shortest {min(lens):.2f}s, longest {max(lens):.2f}s")
        sheet(frames_at(path, times), labels, cols, str(path) + ".cuts.jpg", subs)
    fr = all_frames(path, every)
    sheet(fr, [f"{i * every:.2f}" for i in range(len(fr))], cols, str(path) + ".strip.jpg")
    print("  problems:" if problems else "  no problems found")
    for p in problems:
        print("   -", p)
    return problems


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video"); ap.add_argument("--every", type=float, default=0.25); ap.add_argument("--cols", type=int, default=8)
    a = ap.parse_args()
    qc(a.video, a.every, a.cols)
