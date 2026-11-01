"""Log footage so shots can be cast by eye.

  shots.py log <src.mp4> [...] [--force]      -> <src>.meta.json, <src>.shots.json, <src>.sheet01.jpg ...
  shots.py strip <src.mp4> <t0> <t1> [--n 12] [--out x.jpg] [--aspect 4:3]   frames to pick an exact in-point
  shots.py pool <dir|src ...> [--top 72] [--out pool.jpg] [--min 0.5]          best shots across sources

Per shot: start/end, luma, saturation, warmth, sharpness, motion, flow direction, faces (largest: centre + area),
text area (OCR boxes: title cards, credits, burned-in subtitles). Contact sheets show the shot's middle frame with
the output crop (default 4:3) drawn around the face, so you see what will actually be on screen.
"""
import argparse, math, os, subprocess, sys
from pathlib import Path
import numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0, str(Path(__file__).parent))
from mc_common import FFMPEG, run, probe, save_json, load_json, meta_path, LIBRARY, find_font, fmt_t, sources

YUNET = LIBRARY / "models" / "face_detection_yunet_2023mar.onnx"
PROXY_W = 640
_face = None
_ocr = None


def face_detector(w, h):
    global _face
    if _face is None and YUNET.exists():
        _face = cv2.FaceDetectorYN.create(str(YUNET), "", (w, h), 0.6, 0.3, 50)
    if _face is not None:
        _face.setInputSize((w, h))
    return _face


def faces_in(img):
    """[(cx, cy, area_frac, w_frac)] normalised to img, largest first. cy sits at eye height."""
    h, w = img.shape[:2]
    d = face_detector(w, h)
    if d is None:
        return []
    _, f = d.detect(img)
    out = []
    if f is not None:
        for r in f:
            x, y, fw, fh = r[:4]
            out.append((float((x + fw / 2) / w), float((y + fh * 0.4) / h), float(fw * fh / (w * h)), float(fw / w)))
    return sorted(out, key=lambda t: -t[2])


def ocr_boxes(img):
    """Recognised text boxes [(x0, y0, x1, y1) normalised, text]. Detection alone fires on faces and textures,
    so only boxes whose text is actually read (score >= 0.6, 2+ characters) count."""
    global _ocr
    try:
        if _ocr is None:
            import logging
            logging.disable(logging.WARNING)
            from rapidocr_onnxruntime import RapidOCR
            _ocr = RapidOCR()
        res, _ = _ocr(img)
    except Exception:
        return []
    h, w = img.shape[:2]
    out = []
    for box, txt, score in res or []:
        txt = (txt or "").strip()
        if float(score) < 0.6 or len(txt.replace(" ", "")) < 2:
            continue
        b = np.array(box, dtype=np.float32).reshape(-1, 2)
        out.append(((b[:, 0].min() / w, b[:, 1].min() / h, b[:, 0].max() / w, b[:, 1].max() / h), txt))
    return out


def text_area(boxes, keep=None):
    """(area fraction, box count, mean y, text) of the boxes that overlap `keep` (normalised x0, y0, x1, y1):
    the part that will be on screen, so a channel logo in a corner the crop removes does not condemn the shot."""
    area, ys, words = 0.0, [], []
    for (x0, y0, x1, y1), txt in boxes:
        if keep is not None:
            ox = max(0.0, min(x1, keep[2]) - max(x0, keep[0])) / max(1e-6, x1 - x0)
            oy = max(0.0, min(y1, keep[3]) - max(y0, keep[1])) / max(1e-6, y1 - y0)
            if ox * oy < 0.3:
                continue
        area += (x1 - x0) * (y1 - y0)
        ys.append((y0 + y1) / 2)
        words.append(txt)
    if not words:
        return 0.0, 0, 0.5, ""
    return float(area), len(words), float(np.mean(ys)), " ".join(words)[:60]


def letterbox(src, info):
    """Active picture area [x, y, w, h] (removes black bars)."""
    W, H, dur = info["width"], info["height"], info["duration"]
    rate = 2 if dur <= 300 else max(0.2, 600.0 / dur)
    r = run([FFMPEG, "-hide_banner", "-i", src, "-vf", f"fps={rate},cropdetect=limit=0.09:round=2:reset=1",
             "-an", "-f", "null", "-"], check=False)
    boxes = []
    for line in r.stderr.splitlines():
        if "crop=" in line:
            try:
                w, h, x, y = map(int, line.rsplit("crop=", 1)[1].split()[0].split(":"))
            except ValueError:
                continue
            if w > W * 0.5 and h > H * 0.4:
                boxes.append((w, h))
    if not boxes:
        return [0, 0, W, H]
    # the most common box wins: bars that show on most frames are bars, full-frame shots lose a little
    from collections import Counter
    hs = Counter(round(h / 8) * 8 for _, h in boxes).most_common(1)[0][0]
    ws = Counter(round(w / 8) * 8 for w, _ in boxes).most_common(1)[0][0]
    w, h = min(W, ws) // 2 * 2, min(H, hs) // 2 * 2
    if w > W - 10: w = W
    if h > H - 10: h = H
    return [(W - w) // 4 * 2, (H - h) // 4 * 2, w, h]


def make_proxy(src, crop, out):
    """Small all-intra copy for analysis. All-intra matters: with a GOP every keyframe is a quality jump, and in
    dark continuous takes the shot detector reads those jumps as cuts (a false cut every GOP)."""
    x, y, w, h = crop
    run([FFMPEG, "-y", "-v", "error", "-i", src, "-vf", f"crop={w}:{h}:{x}:{y},scale={PROXY_W}:-2", "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-g", "1", out])


def detect_shots(proxy):
    from scenedetect import detect, AdaptiveDetector
    sc = detect(proxy, AdaptiveDetector(adaptive_threshold=2.6, min_scene_len=3, min_content_val=12.0), show_progress=False, start_in_scene=True)
    return [(float(s.seconds), float(e.seconds)) for s, e in sc]


def crop_box(fw, fh, aspect, face):
    """Crop rectangle (x0, y0, x1, y1) in a frame fw x fh for the output aspect, centred on the face."""
    if fw / fh > aspect:
        cw, ch = fh * aspect, fh
    else:
        cw, ch = fw, fw / aspect
    cx = face[0] * fw if face else fw / 2
    cy = (face[1] * fh + ch * 0.1) if face else fh / 2
    x0 = min(max(cx - cw / 2, 0), fw - cw)
    y0 = min(max(cy - ch / 2, 0), fh - ch)
    return int(x0), int(y0), int(x0 + cw), int(y0 + ch)


def log(src, force=False, aspect=4 / 3):
    src = str(src)
    out_json = src + ".shots.json"
    if os.path.exists(out_json) and not force:
        print(f"skip {src} (already logged)")
        return load_json(out_json)
    info = probe(src)
    crop = letterbox(src, info)
    info["crop"] = crop
    save_json(meta_path(src), info)
    proxy = src + ".proxy.mkv"
    if force or not os.path.exists(proxy):
        make_proxy(src, crop, proxy)
    bounds = detect_shots(proxy) or [(0.0, info["duration"])]
    cap = cv2.VideoCapture(proxy)
    pfps = cap.get(cv2.CAP_PROP_FPS) or info["fps"]
    need = {}
    for i, (s, e) in enumerate(bounds):
        for k, frac in enumerate((0.25, 0.5, 0.75)):
            need.setdefault(int((s + (e - s) * frac) * pfps), []).append((i, k))
    shot_of = np.full(int(info["duration"] * pfps) + 10, -1, dtype=np.int32)
    for i, (s, e) in enumerate(bounds):
        shot_of[int(s * pfps):int(e * pfps) + 1] = i
    keep, lum, mot = {}, {}, {}
    prev, f = None, 0
    while True:
        ok, img = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(img, (160, max(2, int(160 * img.shape[0] / img.shape[1])))), cv2.COLOR_BGR2GRAY).astype(np.float32)
        si = int(shot_of[f]) if f < len(shot_of) else -1
        lum.setdefault(si, []).append(g.mean() / 255)
        if prev is not None and si >= 0 and f > 0 and shot_of[f - 1] == si:
            mot.setdefault(si, []).append(np.abs(g - prev).mean() / 255)
        prev = g
        for key in need.get(f, []):
            keep[key] = img
        f += 1
    cap.release()
    shots, thumbs = [], {}
    for i, (s, e) in enumerate(bounds):
        mid = keep.get((i, 1))
        if mid is None:
            mid = keep.get((i, 0))
        if mid is None:
            continue
        h, w = mid.shape[:2]
        hsv = cv2.cvtColor(mid, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(mid, cv2.COLOR_BGR2GRAY)
        fl = []
        for k in (0, 1, 2):
            if keep.get((i, k)) is not None:
                fl += faces_in(keep[(i, k)])[:1]
        face = None
        if fl:
            arr = np.array(fl)[:, :3]
            face = [round(float(v), 3) for v in (np.median(arr, axis=0) if len(fl) > 1 else arr[0])]
        flow = [0.0, 0.0]
        a, b = keep.get((i, 0)), keep.get((i, 2))
        if a is not None and b is not None and e - s > 0.3:
            sz = (160, max(2, int(160 * h / w)))
            ga = cv2.cvtColor(cv2.resize(a, sz), cv2.COLOR_BGR2GRAY)
            gb = cv2.cvtColor(cv2.resize(b, sz), cv2.COLOR_BGR2GRAY)
            fw_ = cv2.calcOpticalFlowFarneback(ga, gb, None, 0.5, 3, 15, 3, 5, 1.2, 0)
            dt = max((e - s) * 0.5, 1e-3)
            flow = [round(float(np.median(fw_[..., 0])) / 160 / dt, 3), round(float(np.median(fw_[..., 1])) / 160 / dt, 3)]
        cx0, cy0, cx1, cy1 = crop_box(w, h, aspect, face)
        boxes = ocr_boxes(mid)
        ta, tn, ty, tt = text_area(boxes, (cx0 / w, cy0 / h, cx1 / w, cy1 / h))
        edge = len(boxes) - tn                               # text the crop removes: logo / watermark
        sh = dict(id=i, start=round(s, 3), end=round(e, 3), dur=round(e - s, 3),
                  luma=round(float(np.mean(lum.get(i, [gray.mean() / 255]))), 3),
                  sat=round(float(hsv[..., 1].mean() / 255), 3),
                  warm=round(float((mid[..., 2].astype(np.float32) - mid[..., 0]).mean() / 255), 3),
                  sharp=round(float(cv2.Laplacian(gray, cv2.CV_32F).var()), 1),
                  motion=round(float(np.mean(mot.get(i, [0.0]))) * 10, 3),
                  flow=flow, faces=len(faces_in(mid)), face=face,
                  text=round(ta, 4), text_n=tn, text_y=round(ty, 2), text_str=tt, edge_text=int(edge > 0))
        # usable range: drop fade-in / fade-out / black frames at the shot's head and tail
        L = np.array(lum.get(i, [0.5]))
        ok = L > max(0.035, 0.55 * float(np.median(L)))
        if ok.any():
            k0, k1 = int(np.argmax(ok)), int(len(ok) - 1 - np.argmax(ok[::-1]))
            sh["use"] = [round(s + k0 / pfps, 3), round(s + (k1 + 1) / pfps, 3)]
        else:
            sh["use"] = [round(s, 3), round(s, 3)]
        sh["flags"] = flags(sh)
        sh["q"] = quality(sh)
        shots.append(sh)
        thumbs[i] = mid
    res = dict(src=src, fps=info["fps"], duration=info["duration"], width=info["width"], height=info["height"],
               crop=crop, aspect=round(aspect, 4), shots=shots)
    try:
        os.remove(proxy)   # only needed for this pass
    except OSError:
        pass
    save_json(out_json, res)
    sheets(src, res, thumbs, aspect)
    print(f"logged {src}: {len(shots)} shots, crop {crop}")
    return res


def flags(s):
    f = []
    if s["text_n"] >= 1: f.append("TEXT")
    elif s.get("edge_text"): f.append("logo")      # text the crop removes: harmless
    if s["luma"] < 0.07: f.append("DARK")
    if s.get("use") and (s["use"][1] - s["use"][0]) < 0.8 * s["dur"]: f.append("FADE")
    if s["dur"] < 0.35: f.append("SHORT")
    if s["sharp"] < 15: f.append("SOFT")
    if s["face"] and s["face"][2] > 0.06: f.append("CU")
    elif s["face"] and s["face"][2] > 0.012: f.append("MS")
    elif s["face"]: f.append("WS")
    if s["motion"] > 0.25: f.append("MOVE")
    return f


def quality(s):
    q = 0.5
    if s["face"]: q += min(0.3, s["face"][2] * 3)
    q += min(0.15, s["motion"] * 0.3)
    q += min(0.1, s["sharp"] / 2000)
    if "TEXT" in s["flags"]: q -= 0.6
    if "DARK" in s["flags"]: q -= 0.4
    if "SHORT" in s["flags"]: q -= 0.2
    if "SOFT" in s["flags"]: q -= 0.15
    return round(q, 3)


_fonts = {}
def label_font(size=13):
    if size not in _fonts:
        p = find_font("consola.ttf", "arial.ttf")
        _fonts[size] = ImageFont.truetype(p, size) if p else ImageFont.load_default()
    return _fonts[size]


def tile(img_bgr, label, tw, aspect, face, color=(255, 230, 0), sub=None):
    h, w = img_bgr.shape[:2]
    th = int(tw * 9 / 16)
    im = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
    s = min(tw / w, th / h)
    im = im.resize((max(1, int(w * s)), max(1, int(h * s))))
    canvas = Image.new("RGB", (tw, th + 18), (24, 24, 24))
    ox, oy = (tw - im.width) // 2, 18 + (th - im.height) // 2
    canvas.paste(im, (ox, oy))
    d = ImageDraw.Draw(canvas)
    if aspect:
        x0, y0, x1, y1 = crop_box(im.width, im.height, aspect, face)
        d.rectangle([ox + x0, oy + y0, ox + x1 - 1, oy + y1 - 1], outline=(0, 220, 255), width=1)
    d.text((3, 2), label, fill=color, font=label_font())
    if sub:
        d.text((3, th + 3), sub, fill=(255, 140, 140), font=label_font(12))
    return canvas


def grid(tiles, cols):
    tw, th = tiles[0].size
    rows = math.ceil(len(tiles) / cols)
    g = Image.new("RGB", (cols * tw, rows * th), (10, 10, 10))
    for i, t in enumerate(tiles):
        g.paste(t, ((i % cols) * tw, (i // cols) * th))
    return g


def sheets(src, res, thumbs, aspect, cols=6, per=48, tw=300):
    for o in sorted(Path(src).parent.glob(Path(src).name + ".sheet*.jpg")):
        o.unlink()
    tiles = []
    for s in res["shots"]:
        im = thumbs.get(s["id"])
        if im is None:
            continue
        bad = [f for f in s["flags"] if f in ("TEXT", "DARK", "SHORT", "SOFT")]
        u = s.get("use") or [s["start"], s["end"]]
        lab = f"#{s['id']} {fmt_t(s['start'])} {s['dur']:.1f}s" + (f" use {fmt_t(u[0])}-{fmt_t(u[1])}" if "FADE" in s["flags"] else "")
        tiles.append(tile(im, lab, tw, aspect, s["face"],
                          (255, 90, 90) if bad else (255, 230, 0), " ".join(s["flags"])))
    for k in range(0, len(tiles), per):
        out = f"{src}.sheet{k // per + 1:02d}.jpg"
        grid(tiles[k:k + per], cols).save(out, quality=82)
        print("sheet", out)


def grab(src, times, width=480):
    """Frames (BGR) at the given times (accurate seek), letterbox removed."""
    m = load_json(meta_path(src)) or probe(src)
    x, y, w, h = m.get("crop") or [0, 0, m["width"], m["height"]]
    out = []
    for t in times:
        p = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{max(0, t):.3f}", "-i", str(src), "-frames:v", "1",
                            "-vf", f"crop={w}:{h}:{x}:{y},scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                           capture_output=True)
        out.append(cv2.imdecode(np.frombuffer(p.stdout, np.uint8), cv2.IMREAD_COLOR) if p.stdout else None)
    return out


def strip(src, t0, t1, n=12, out=None, aspect=4 / 3, cols=6):
    ts = [t0 + (t1 - t0) * i / max(1, n - 1) for i in range(n)]
    tiles = []
    for t, im in zip(ts, grab(src, ts)):
        if im is None:
            continue
        f = faces_in(im)
        tiles.append(tile(im, f"{t:.2f}", 300, aspect, f[0][:3] if f else None))
    out = out or f"{src}.strip_{t0:.1f}-{t1:.1f}.jpg"
    grid(tiles, min(cols, len(tiles))).save(out, quality=85)
    print(out)


def peek(items, out="peek.jpg", aspect=4 / 3, cols=6, base="."):
    """One sheet of frames from anywhere: items like "assets/src/a.mp4@63.2" or "a.mp4@63.2+2" (t, t+1, t+2 s).
    Checks candidate in-points across sources in one look."""
    tiles = []
    for it in items:
        path, t = it.rsplit("@", 1)
        n = 1
        if "+" in t:
            t, n = t.split("+"); n = int(n) + 1
        path = str(Path(base) / path)
        ts = [float(t) + k for k in range(n)]
        for tt, im in zip(ts, grab(path, ts)):
            if im is None:
                continue
            f = faces_in(im)
            tiles.append(tile(im, f"{Path(path).stem[:11]} {tt:.2f}", 300, aspect, f[0][:3] if f else None))
    if tiles:
        grid(tiles, min(cols, len(tiles))).save(out, quality=82)
        print(out, len(tiles))


def pool(paths, top=72, out="pool.jpg", min_dur=0.5, aspect=4 / 3):
    files = []
    for p in paths:
        p = Path(p)
        files += sorted(p.glob("*.shots.json")) if p.is_dir() else [Path(str(p) + ".shots.json")]
    cand = []
    for fjs in files:
        j = load_json(fjs)
        if not j:
            continue
        for s in j["shots"]:
            if s["dur"] >= min_dur and not ({"TEXT", "DARK"} & set(s["flags"])):
                cand.append((s["q"], j["src"], s))
    cand.sort(key=lambda c: -c[0])
    tiles = []
    for _, src, s in cand[:top]:
        im = grab(src, [(s["start"] + s["end"]) / 2])[0]
        if im is not None:
            tiles.append(tile(im, f"{Path(src).stem[:11]} #{s['id']} {fmt_t(s['start'])} {s['dur']:.1f}s", 300, aspect,
                              s["face"], sub=" ".join(s["flags"])))
    if tiles:
        grid(tiles, 6).save(out, quality=82)
        print(out, len(tiles), "shots")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    l = sp.add_parser("log"); l.add_argument("src", nargs="+"); l.add_argument("--force", action="store_true"); l.add_argument("--aspect", default="4:3")
    s = sp.add_parser("strip"); s.add_argument("src"); s.add_argument("t0", type=float); s.add_argument("t1", type=float)
    s.add_argument("--n", type=int, default=12); s.add_argument("--out"); s.add_argument("--aspect", default="4:3")
    k = sp.add_parser("peek"); k.add_argument("items", nargs="+"); k.add_argument("--out", default="peek.jpg"); k.add_argument("--aspect", default="4:3")
    p = sp.add_parser("pool"); p.add_argument("paths", nargs="+"); p.add_argument("--top", type=int, default=72)
    p.add_argument("--out", default="pool.jpg"); p.add_argument("--min", type=float, default=0.5); p.add_argument("--aspect", default="4:3")
    a = ap.parse_args()
    asp = float(a.aspect.split(":")[0]) / float(a.aspect.split(":")[1])
    if a.cmd == "log":
        for x in sources(a.src):
            log(x, a.force, asp)
    elif a.cmd == "strip":
        strip(a.src, a.t0, a.t1, a.n, a.out, asp)
    elif a.cmd == "peek":
        peek(a.items, a.out, asp)
    else:
        pool(a.paths, a.top, a.out, a.min, asp)
