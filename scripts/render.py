"""Render an edit decision list (edl.json) to mp4. Frame-accurate, beat-locked, deterministic.

  render.py edl.json [-o renders/v1.mp4] [--preview] [--from 0 --to 12] [--still 8.4 [--still 9.1 ...]]

--preview  half size, fast encode.   --still T  write PNG frames at those times instead of a video.

EDL (paths are relative to the EDL file):
{
  "size": [1440, 1080], "fps": 30,
  "music": {"file": "assets/music/x.m4a", "start": 61.2, "gain_db": 0, "fade_in": 0, "fade_out": 1.5},
       or strung from stretches of the song (song seconds; seams on bar lines, see music.py seams):
           "segments": [[33.2, 42.59], {"from": 20.1, "to": 26.7, "xfade": 0.3}, {"rest_beats": 1}, [83.81, null]],
           "xfade": 0.05        (default crossfade at each seam; a rest is silence on the grid; null = to the end)
  "end_fade": 0.9,     (seconds of fade to black at the end, words included)
  "look": {"contrast": 1.06, "saturation": 1.04, "grain": 0.03, "vignette": 0.2, "normalize": 0.5, ...},
  "clips": [ {"src": "assets/src/a.mp4", "in": 63.2, "beats": 2, "speed": 1, "focus": "auto"|[x,y],
              "zoom": [1, 1.04], "fx": "cut|flash|whip_l|zoom|glitch|dissolve|circle|...", "mirror": false,
              "audio": -6, "freeze": false, "black": false, "layout": {...}, "tint": [0.9, 1, 1.12], "shade": [0.55, 0.7], "note": "why this shot"} ],
  "subs":  [ {"at": "b8", "beats": 3, "zh": "...", "en": "...", "style": "a|d|c|zh"} ],
  "texts": [ {"at": 0.3, "dur": 1.8, "text": "...", "style": "glass|lyric|thin|caption|big|quote|moment", "anim": "fade|pop|type|rise",
              "fade": [0.4, 0.5]} ],      (fade in/out seconds; default 4 frames)
  "dialog": [ {"src": "...", "in": 12.3, "out": 14.1, "at": "b4", "gain_db": 0, "duck_db": -10,
               "zh": "...", "en": "...", "stem": "vocals"|"mix"} ],      (zh/en = its subtitle, shown while spoken)
  "sfx": [ {"kind": "whoosh|hit|riser|boom", "at": "b16", "gain_db": -10, "dur": 1.0} ]
}
Clip durations: "beats" (snapped to the music's beat grid; 0.5 = eighth note), or "dur" seconds, or "until": "b32".
Times: seconds, or "bN" = beat N of the timeline's grid, counted from the first beat at/after the music start (b0).
"""
import argparse, json, math, os, subprocess, sys, tempfile, time
from pathlib import Path
import numpy as np, cv2
sys.path.insert(0, str(Path(__file__).parent))
from mc_common import FFMPEG, run, load_json, save_json, src_meta, probe, hi_section
import fx

SR = 48000


# ---------------------------------------------------------------- timing
class Grid:
    def __init__(self, beats, start):
        self.start = start
        b = np.array(beats or [], dtype=np.float64)
        if len(b) < 2:
            b = np.arange(0, 3600, 0.5)
        self.b = b
        self.idx = np.arange(len(b), dtype=np.float64)
        self.per = float(np.median(np.diff(b)))
        # b0 = first beat at/after the music start
        self.i0 = int(np.searchsorted(b, start - 0.03))

    def pos(self, t):
        """timeline seconds -> fractional beat index (song grid)"""
        x = t + self.start
        if x < self.b[0]:
            return (x - self.b[0]) / self.per
        if x > self.b[-1]:
            return self.idx[-1] + (x - self.b[-1]) / self.per
        return float(np.interp(x, self.b, self.idx))

    def time(self, p):
        """fractional beat index -> timeline seconds"""
        if p < 0:
            x = self.b[0] + p * self.per
        elif p > self.idx[-1]:
            x = self.b[-1] + (p - self.idx[-1]) * self.per
        else:
            x = float(np.interp(p, self.idx, self.b))
        return x - self.start

    def resolve(self, v):
        if isinstance(v, str) and v.startswith("b"):
            return self.time(self.i0 + float(v[1:]))
        return float(v)

    def after(self, t, beats):
        """end time of a span of `beats` starting at t, snapped to the beat grid at the span's resolution
        (a 2-beat clip that starts between beats ends on a beat; a 0.5-beat clip ends on an eighth)"""
        g = next((u for u in (1.0, 0.5, 0.25, 0.125) if abs(beats / u - round(beats / u)) < 1e-6), 0.125)
        p = self.pos(t)
        base = math.floor(p / g + 1e-3) * g
        return self.time(base + beats)


def music_pieces(m, beats, dur=None):
    """The song stretches on the timeline: [dict(a, b, t0, xfade)] (song seconds a..b placed at timeline t0; a=None
    is a rest) and the beat grid in timeline seconds. "segments" strings stretches together; without it the song
    plays on from "start". Seams belong on bar lines (music.py seams) so the grid stays even across them."""
    segs = m.get("segments")
    if not segs:
        return [dict(a=float(m.get("start", 0)), b=None, t0=0.0, xfade=0.0)], None
    b = np.array(beats if beats is not None else np.arange(0, 3600, 0.5), dtype=np.float64)
    per = float(np.median(np.diff(b)))
    pieces, tl, t = [], [], 0.0
    for s in segs:
        s = dict(s) if isinstance(s, dict) else {"from": s[0], "to": s[1]}
        xf = float(s.get("xfade", m.get("xfade", 0.05)))
        if "rest_beats" in s:            # silence; the grid runs on at the period of the last beats
            d = float(s["rest_beats"]) * (float(np.median(np.diff(tl[-5:]))) if len(tl) > 5 else per)
            nxt = (tl[-1] if tl else t) + per
            while nxt < t + d - 0.02:
                tl.append(nxt); nxt += per
            pieces.append(dict(a=None, b=None, t0=t, dur=d, xfade=xf))
            t += d
            continue
        a = float(s["from"])
        e = float(s["to"]) if s.get("to") is not None else float(dur or b[-1] + per)
        for v in [a] + ([e] if s.get("to") is not None else []):
            nb = b[np.argmin(np.abs(b - v))]
            if abs(v - nb) > 0.03:
                print(f"note: music seam at song {v:.3f}s is {1000 * (v - nb):+.0f} ms off the beat grid (nearest beat {nb:.3f})")
        inside = b[(b >= a - 0.02) & (b < e - 0.02)]
        tl += list(inside - a + t)
        pieces.append(dict(a=a, b=e, t0=t, xfade=xf))
        t += e - a
    return pieces, tl


def music_grid(edl, base):
    """(Grid in timeline seconds, music pieces) for an EDL; render and fetch.conform both lay clips on it"""
    m = edl.get("music") or {}
    beats, dur = None, None
    if m.get("file"):
        bj = load_json(str(base / m["file"]) + ".beats.json")
        beats, dur = (bj["beats"], bj.get("duration")) if bj else (None, None)
        if beats is None:
            print("note: no beats.json for the music; run music.py analyze first. Using a 120 bpm grid.")
    pieces, tl = music_pieces(m, beats, dur)
    if tl is None:
        return Grid(beats, float(m.get("start", 0))), pieces
    return Grid(tl, 0.0), pieces


def layout_clips(edl, grid):
    t = 0.0
    out = []
    for c in edl["clips"]:
        if "at" in c:
            t = grid.resolve(c["at"])
        if "beats" in c:
            t1 = grid.after(t, float(c["beats"]))
        elif "until" in c:
            t1 = grid.resolve(c["until"])
        else:
            t1 = t + float(c.get("dur", 1.0))
        out.append(dict(c, t0=t, t1=t1))
        t = t1
    return out


def lines_from_clips(edl, clips):
    """A clip's "say": [a, b] (source seconds) puts that line on the dialog track in sync with the picture:
    it starts where the picture reaches source time a, and may run on over the next shots."""
    out = list(edl.get("dialog", []))
    for c in clips:
        if not c.get("say") or c.get("black") or c.get("color"):
            continue
        a, b = map(float, c["say"])
        d = {k: c[k] for k in ("zh", "en", "stem", "sub_style", "duck_db", "gain_db", "sub_hold", "sub_lead", "fade_out", "sub_scale", "sub_x", "sub_y") if k in c}
        d.update(src=c["src"], **{"in": a, "out": b, "at": c["t0"] + (a - float(c["in"])) / float(c.get("speed", 1))})
        out.append(d)
    return out


def span(item, grid, default=1.0):
    t0 = grid.resolve(item.get("at", 0))
    if "beats" in item:
        t1 = grid.after(t0, float(item["beats"]))
    elif "until" in item:
        t1 = grid.resolve(item["until"])
    else:
        t1 = t0 + float(item.get("dur", default))
    return t0, t1


# ---------------------------------------------------------------- focus
_face = None


def detect_focus(src, times, crop):
    """median face centre (normalised in the letterbox-cropped frame) over a few frames, or None"""
    global _face
    from shots import faces_in
    x, y, w, h = crop
    pts = []
    for t in times:
        p = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{max(0, t):.3f}", "-i", src, "-frames:v", "1",
                            "-vf", f"crop={w}:{h}:{x}:{y},scale=640:-2", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
        if not p.stdout:
            continue
        img = cv2.imdecode(np.frombuffer(p.stdout, np.uint8), cv2.IMREAD_COLOR)
        f = faces_in(img)
        if f:
            pts.append(f[0][:3])
    if not pts:
        return None
    a = np.median(np.array(pts), axis=0)
    return [float(a[0]), float(a[1]), float(a[2])]


# ---------------------------------------------------------------- reading frames
class Reader:
    """Streams one clip's frames (pre-roll + body + post-roll) as RGB work frames."""

    look = None

    def __init__(self, base, c, W, H, fps, pre, post, focus_cache):
        self.c, self.W, self.H, self.fps = c, W, H, fps
        self.proc, self.i, self.last, self.lut, self.pad_front = None, -1, None, None, 0
        self.pre, self.post = pre, post
        self.n_body = max(1, int(round(c["t1"] * fps)) - int(round(c["t0"] * fps)))
        self.n = self.pre + self.n_body + self.post
        if c.get("black") or c.get("color"):
            return
        self.orig = str((base / c["src"]).resolve()) if not os.path.isabs(c["src"]) else c["src"]
        self.speed = float(c.get("speed", 1.0))
        t_in = float(c["in"])
        self.src, self.toff = hi_section(self.orig, max(0.0, t_in - self.pre / fps * self.speed),
                                         t_in + (self.n_body + self.post) / fps * self.speed)
        m = src_meta(self.src)
        self.meta = m
        cx, cy, cw, ch = m.get("crop") or [0, 0, m["width"], m["height"]]
        self.crop = [cx, cy, cw, ch]
        om = src_meta(self.orig) if self.src != self.orig else m
        self.orig_crop = om.get("crop") or [0, 0, om["width"], om["height"]]
        z = c.get("zoom")
        if z is None:
            z = [1.0, 1.035] if (c["t1"] - c["t0"]) >= 1.4 and not c.get("freeze") else [1.0, 1.0]
        elif isinstance(z, (int, float)):
            z = [float(z), float(z)]
        self.zoom = [float(z[0]), float(z[1])]
        # focus
        foc = c.get("focus", "auto")
        if foc == "auto" or foc is None:
            key = f"{self.orig}|{float(c['in']):.2f}"
            if key not in focus_cache:
                d = (c["t1"] - c["t0"]) * self.speed
                focus_cache[key] = detect_focus(self.orig, [c["in"] + min(0.1, d / 4), c["in"] + d / 2], self.orig_crop)
            f = focus_cache[key]
            foc = [f[0], min(0.62, max(0.3, f[1] + 0.06))] if f else [0.5, 0.5]
        elif foc == "center":
            foc = [0.5, 0.5]
        self.focus = [float(foc[0]), float(foc[1])]
        fe = c.get("focus_end")
        self.focus_end = [float(fe[0]), float(fe[1])] if fe else self.focus
        # work window: horizontal span of the letterbox-cropped source we need
        zmax = max(self.zoom + [1.0])
        s_cover = max(W / cw, H / ch)                # source px -> output px at zoom 1
        q = min(1.25, max(1.0, zmax))                # keep detail for zooms
        self.s_cover, self.q = s_cover, q
        self.ws = s_cover * q                        # source px -> work px
        need_w = W / s_cover * 1.10                  # visible width (source px) + margin
        need_h = H / s_cover * 1.10
        fx0 = min(self.focus[0], self.focus_end[0]) * cw - need_w / 2
        fx1 = max(self.focus[0], self.focus_end[0]) * cw + need_w / 2
        fy0 = min(self.focus[1], self.focus_end[1]) * ch - need_h / 2
        fy1 = max(self.focus[1], self.focus_end[1]) * ch + need_h / 2
        wx0, wx1 = max(0, int(fx0)), min(cw, int(math.ceil(fx1)))
        wy0, wy1 = max(0, int(fy0)), min(ch, int(math.ceil(fy1)))
        if wx1 - wx0 < need_w * 0.95: wx0, wx1 = (0, cw) if need_w >= cw else (int(max(0, min(cw - need_w, fx0))), int(max(0, min(cw - need_w, fx0)) + need_w))
        if wy1 - wy0 < need_h * 0.95: wy0, wy1 = (0, ch) if need_h >= ch else (int(max(0, min(ch - need_h, fy0))), int(max(0, min(ch - need_h, fy0)) + need_h))
        self.win = [wx0 // 2 * 2, wy0 // 2 * 2, (wx1 - wx0) // 2 * 2, (wy1 - wy0) // 2 * 2]
        self.ww = max(2, int(round(self.win[2] * self.ws / 2)) * 2)
        self.wh = max(2, int(round(self.win[3] * self.ws / 2)) * 2)

    def start(self):
        c = self.c
        if c.get("black") or c.get("color"):
            return
        ss = float(c["in"]) - self.toff - self.pre / self.fps * self.speed
        self.pad_front = 0
        if ss < 0:
            self.pad_front = int(round(-ss / self.speed * self.fps))
            ss = 0.0
        frames_needed = self.n - self.pad_front
        if c.get("freeze"):
            frames_needed = 1
        dur = frames_needed / self.fps * self.speed + 0.6
        cx, cy, cw, ch = self.crop
        wx, wy, wwid, whei = self.win
        vf = [f"crop={cw}:{ch}:{cx}:{cy}", f"crop={wwid}:{whei}:{wx}:{wy}"]
        if self.speed != 1.0:
            vf.append(f"setpts=(PTS-STARTPTS)/{self.speed}")
        interp = c.get("interp")
        if interp == "blend" and self.speed < 1:
            vf.append(f"framerate=fps={self.fps}")
        elif interp == "mci" and self.speed < 1:
            vf.append(f"minterpolate=fps={self.fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1")
        else:
            vf.append(f"fps={self.fps}")
        vf.append(f"scale={self.ww}:{self.wh}:flags=bicubic")
        if c.get("reverse"):
            vf.append("reverse")
        cmd = [FFMPEG, "-v", "error", "-ss", f"{ss:.3f}", "-t", f"{dur:.3f}", "-i", self.src, "-an",
               "-vf", ",".join(vf), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=self.ww * self.wh * 3 * 4)
        self.nbytes = self.ww * self.wh * 3

    def _read(self):
        if self.proc is None:
            return None
        buf = self.proc.stdout.read(self.nbytes)
        if len(buf) < self.nbytes:
            return None
        return np.frombuffer(buf, np.uint8).reshape(self.wh, self.ww, 3)

    def work(self, i):
        """work frame for clip-local index i (0 = first pre-roll frame)"""
        c = self.c
        if c.get("black") or c.get("color"):
            col = c.get("color", "#000000").lstrip("#")
            rgb = [int(col[k:k + 2], 16) for k in (0, 2, 4)]
            if self.last is None:
                self.last = np.full((self.H, self.W, 3), rgb, np.uint8)
            return self.last
        if self.proc is None:
            self.start()
        if c.get("freeze") and self.last is not None:
            return self.last
        while self.i < i:
            self.i += 1
            if self.i < getattr(self, "pad_front", 0):
                continue
            fr = self._read()
            if fr is not None:
                self.last = fr
                if self.lut is None and self.look is not None:
                    self.lut = self.look.clip_lut([fr]) if "gain" not in c else None
                    self.lut = self.lut if self.lut is not None else False
            elif self.last is None:
                self.last = np.zeros((self.wh, self.ww, 3), np.uint8)
        if self.last is None:
            fr = self._read()
            self.last = fr if fr is not None else np.zeros((self.wh, self.ww, 3), np.uint8)
        return self.last

    def frame(self, i):
        """output-size frame for clip-local index i, with the clip's camera (focus, zoom, mirror)"""
        w = self.work(i)
        c = self.c
        if c.get("black") or c.get("color"):
            return w.copy()
        p = 0.0 if self.n_body <= 1 else min(1.0, max(0.0, (i - self.pre) / (self.n_body - 1)))
        pe = fx.ease_in_out(p) if c.get("ease", True) else p
        z = self.zoom[0] + (self.zoom[1] - self.zoom[0]) * pe
        fxn = self.focus[0] + (self.focus_end[0] - self.focus[0]) * pe
        fyn = self.focus[1] + (self.focus_end[1] - self.focus[1]) * pe
        cw, ch = self.crop[2], self.crop[3]
        sx = self.ww / self.win[2]; sy = self.wh / self.win[3]          # source px -> work px (after rounding)
        cxw = (fxn * cw - self.win[0]) * sx
        cyw = (fyn * ch - self.win[1]) * sy
        s_out = z / self.q                                              # work px -> output px
        cxw, cyw = fx.clamp_center(cxw, cyw, self.ww, self.wh, self.W, self.H, s_out)
        out = fx.frame_from_work(w, self.W, self.H, cxw, cyw, s_out, mirror=bool(c.get("mirror")))
        if c.get("tint"):              # per-shot colour, e.g. [0.9, 1.0, 1.12] = colder (a section that changes temperature)
            out = np.clip(out.astype(np.float32) * np.array(c["tint"], np.float32), 0, 255).astype(np.uint8)
        if c.get("shade"):             # [y0, a]: darken from y0 down to the bottom (a = darkness at the bottom), a bed for words
            y0, a = c["shade"]
            h = out.shape[0]
            ramp = np.clip((np.arange(h, dtype=np.float32) / h - y0) / max(1e-3, 1 - y0), 0, 1) * a
            out = (out.astype(np.float32) * (1 - ramp)[:, None, None]).astype(np.uint8)
        return out

    def focus_px(self):
        return (self.W / 2, self.H * 0.42)

    def close(self):
        if self.proc:
            try:
                self.proc.stdout.close(); self.proc.kill()
            except Exception:
                pass
            self.proc = None


# ---------------------------------------------------------------- layouts
def layout_frame(base_img, lay, k, n, readers, W, H, seed):
    """Composite a layout over the clip's own frame. k = frame index in clip body."""
    t = lay.get("type")
    if t == "split":
        # two halves side by side. ref D 27.3 s: the asker is alone on the left, the answer cuts in on the right
        # b_k frames later ("b_k"); "ax"/"bx" = where each half crops its frame (0.5 = centre, the face is centred)
        other = readers[0].frame(k + readers[0].pre) if readers else base_img
        gap = int(lay.get("gap", 4))
        left = fx.cover(base_img, W // 2 - gap // 2, H, lay.get("ax", 0.5), 0.45)
        right = fx.cover(other, W - W // 2 - gap // 2, H, lay.get("bx", 0.5), 0.45)
        if lay.get("swap"):
            left, right = fx.cover(other, W // 2 - gap // 2, H, lay.get("bx", 0.5), 0.45), fx.cover(base_img, W - W // 2 - gap // 2, H, lay.get("ax", 0.5), 0.45)
        out = np.zeros_like(base_img)
        p = fx.ease_out(min(1, k / 6)) if lay.get("anim", True) else 1
        dy = int((1 - p) * H * 0.3)
        out[:, :left.shape[1]] = np.roll(left, -dy, axis=0) if dy else left
        if k >= int(lay.get("b_k", 0)):
            # "b_fade": the late half rises out of the dark over that many frames instead of popping in
            q = min(1.0, (k - int(lay.get("b_k", 0)) + 1) / max(1, int(lay.get("b_fade", 0))))
            r = np.roll(right, dy, axis=0) if dy else right
            out[:, W - right.shape[1]:] = r if q >= 1 else (r.astype(np.float32) * fx.ease_out(q)).astype(r.dtype)
        return out
    if t == "pip":
        bg = cv2.GaussianBlur(base_img, (0, 0), lay.get("bg_blur", 14))
        bg = cv2.convertScaleAbs(bg, alpha=lay.get("bg_dim", 0.7))
        for r, it in zip(readers, lay.get("items", [])):
            appear = int(it.get("k", 0))
            if k < appear:
                continue
            x, y, w, h = it["rect"]
            pw, ph = int(w * W), int(h * H)
            img = fx.cover(r.frame(k + r.pre), pw, ph, 0.5, 0.42)
            q = fx.ease_out(min(1, (k - appear) / 4))
            s = 0.86 + 0.14 * q
            if s < 0.999:
                img2 = cv2.resize(img, (max(2, int(pw * s)), max(2, int(ph * s))))
                ox, oy = (pw - img2.shape[1]) // 2, (ph - img2.shape[0]) // 2
            else:
                img2, ox, oy = img, 0, 0
            fx.paste(bg, img2, int(x * W) + ox, int(y * H) + oy, None, shadow=0.55 * q)
        return bg
    if t == "panels":
        bg = cv2.GaussianBlur(base_img, (0, 0), 16)
        npan = int(lay.get("n", 3))
        pw = int(W * 0.23); ph = int(H * 0.8); gap = int(W * 0.02)
        total = npan * pw + (npan - 1) * gap
        x0 = (W - total) // 2
        for i in range(npan):
            src_img = readers[i].frame(k + readers[i].pre) if i < len(readers) else base_img
            q = fx.ease_out(min(1, max(0, (k - i * 2) / 6)))
            if q <= 0:
                continue
            fxn = (i + 0.5) / npan if i >= len(readers) else 0.5
            img = fx.cover(src_img, pw, ph, fxn if i >= len(readers) else 0.5, 0.45)
            yo = int((1 - q) * H * 0.5) * (1 if i % 2 == 0 else -1)
            yy = (H - ph) // 2 + (int(H * 0.04) if i % 2 == 0 else -int(H * 0.04)) + yo
            fx.paste(bg, img, x0 + i * (pw + gap), yy, fx.rounded_mask(pw, ph, pw * 0.12), shadow=0.6)
        return bg
    if t == "nested":
        levels = int(lay.get("levels", 3))
        out = base_img.copy()
        q = fx.ease_out(min(1, k / 6))
        for lv in range(1, levels):
            s = 1 - (0.17 * lv) * q
            w, h = int(W * s), int(H * s)
            if w < 20:
                break
            img = cv2.resize(base_img, (w, h), interpolation=cv2.INTER_AREA)
            fx.paste(out, img, (W - w) // 2, (H - h) // 2, None, shadow=0.7)
        return out
    return base_img


# ---------------------------------------------------------------- text
STYLE_H = 1080.0


def sub_items(s, W, H):
    k = H / STYLE_H
    st = s.get("style", "a")
    zh, en = s.get("zh", ""), s.get("en", "")
    items = []
    y = s.get("y")
    if st == "a":
        if zh: items.append(dict(text=zh, kind="zh_bold", size=40 * k, x=0.5, y=y or 0.866, shadow=0.7))
        if en: items.append(dict(text=en, kind="en_serif", size=37 * k, x=0.5, y=(y or 0.866) + 0.04 if zh else (y or 0.88), fill=(242, 193, 78, 255), shadow=0.7))
    elif st == "d":
        if zh: items.append(dict(text=zh, kind="zh_bold", size=46 * k, x=0.5, y=y or 0.84, shadow=0.55))
        if en: items.append(dict(text=en, kind="en_thin_italic", size=36 * k, x=0.5, y=(y or 0.84) + 0.048 if zh else (y or 0.88), shadow=0.55))
    elif st == "c":
        items.append(dict(text=zh, kind="zh_bold", size=48 * k, x=s.get("x", 0.155), y=y or 0.815, anchor="l", glow=0.55,
                          hl=(232, 36, 44, 255), shadow=0.3))
    elif st in ("serif", "thin"):   # restrained: small, no stroke or glow, a soft shadow just to read on bright frames
        y = y or 0.872
        soft = dict(shadow=0.6, shadow_blur=4, shadow_off=(0, 1))
        if st == "serif":
            zk, ek = dict(kind="zh_serif", weight=500, size=35 * k, tracking=0.04), dict(kind="en_serif_italic", size=26 * k)
        else:
            zk, ek = dict(kind="zh_sans", weight=300, size=34 * k, tracking=0.06), dict(kind="en_thin", size=24 * k, tracking=0.04)
        sc, x = float(s.get("scale", 1.0)), s.get("x", 0.5)      # "sub_scale" (EDL or line), "sub_x"
        zk["size"] *= sc; ek["size"] *= sc
        if zh: items.append(dict(text=zh, x=x, y=y, fill=(247, 245, 240, 250), **zk, **soft))
        if en: items.append(dict(text=en, x=x, y=y + 0.043 * sc if zh else y + 0.02, fill=(236, 233, 226, 235), **ek, **soft))
    else:  # zh only, centred
        items.append(dict(text=zh or en, kind="zh_bold", size=44 * k, x=0.5, y=y or 0.87, shadow=0.7))
    return items


def text_items(t, W, H):
    k = H / STYLE_H
    st = t.get("style", "big")
    x, y = t.get("x", 0.5), t.get("y", 0.5)
    size = t.get("size")
    base = dict(text=t["text"], x=x, y=y, anchor=t.get("anchor", "c"))
    if st == "glass":
        return [dict(base, kind="zh_bold", size=(size or 38) * k, tracking=t.get("tracking", 1.05), shadow=0.25)]
    if st == "lyric":
        return [dict(base, kind="en_display", size=(size or 110) * k, fill=(246, 212, 20, 255),
                     offset_shadow=((205, 22, 40, 255), (int(5 * k), int(5 * k))))]
    if st == "thin":
        return [dict(base, kind="en_thin", size=(size or 96) * k, fill=tuple(t.get("fill", (205, 188, 130, 225))), tracking=t.get("tracking", 0.0))]
    if st == "caption":
        return [dict(base, text=t["text"].upper(), kind="en_thin", size=(size or 30) * k, y=t.get("y", 0.94),
                     fill=(255, 255, 255, 215), tracking=t.get("tracking", 0.1))]
    if st == "quote":
        return [dict(base, kind="zh_light", size=(size or 38) * k, fill=(240, 240, 240, 230), shadow=0.4)]
    if st == "serif":
        return [dict(base, kind="zh_serif", size=(size or 64) * k, tracking=t.get("tracking", 0.25), shadow=0.5, weight=600)]
    if st == "moment":   # one of the film's two or three words on screen: a quiet line, widely spaced
        return [dict(base, kind=t.get("kind", "zh_serif"), weight=t.get("weight", 400), size=(size or 54) * k,
                     tracking=t.get("tracking", 0.2), fill=tuple(t.get("fill", (246, 244, 238, 245))),
                     shadow=t.get("shadow", 0.5), shadow_blur=5, shadow_off=(0, 1))]
    return [dict(base, kind=t.get("kind", "zh_bold"), size=(size or 86) * k, tracking=t.get("tracking", 0.12), shadow=0.55,
                 fill=tuple(t.get("fill", (255, 255, 255, 255))))]


class Overlay:
    def __init__(self, f0, f1, layer, anim="fade", glass=None, prefixes=None, words=None, fin=4, fout=4):
        self.f0, self.f1, self.layer, self.anim, self.glass = f0, f1, layer, anim, glass
        self.prefixes, self.words = prefixes, words
        self.fin, self.fout = max(1, fin), max(1, fout)

    def draw(self, img, f):
        if not (self.f0 <= f < self.f1):
            return img
        k, n = f - self.f0, self.f1 - self.f0
        fin, fout = self.fin, self.fout
        a = min(1.0, (k + 1) / fin) if self.anim != "none" else 1.0
        a = min(a, (n - k) / fout) if n - k < fout else a
        dx = dy = 0; sc = 1.0
        if self.anim == "pop":
            sc = 1.25 - 0.25 * fx.ease_out(k / 5)
        elif self.anim == "rise":
            dy = 24 * (1 - fx.ease_out(k / 8))
        if self.glass:
            x0, y0, x1, y1 = self.glass
            img = fx.glass_band(img, x0, y0, x1, y1, strength=min(1.0, a * 1.2))
        if self.words:
            for (lay, wk) in self.words:
                if k >= wk:
                    q = min(1.0, (k - wk + 1) / 3)
                    img = fx.comp_rgba(img, lay, alpha=a * q, scale=1.18 - 0.18 * fx.ease_out((k - wk) / 4))
            return img
        layer = self.layer
        if self.prefixes:
            m = len(self.prefixes)
            idx = min(m - 1, int(k / max(1, (n * 0.45) / m)))
            layer = self.prefixes[idx]
        return fx.comp_rgba(img, layer, alpha=a, dx=dx, dy=dy, scale=sc)


def build_overlays(edl, grid, W, H, fps):
    ovs = []
    for d in edl.get("dialog", []):   # a line carries its own subtitle: shown while it is spoken
        if d.get("zh") or d.get("en"):
            t0 = grid.resolve(d.get("at", 0)) + float(d.get("sub_lead", -0.05))
            t1 = t0 + (float(d["out"]) - float(d["in"])) + float(d.get("sub_hold", 0.3))
            st = dict(zh=d.get("zh", ""), en=d.get("en", ""), style=d.get("sub_style", edl.get("sub_style", "a")),
                      scale=d.get("sub_scale", edl.get("sub_scale", 1.0)), x=d.get("sub_x", 0.5))
            if d.get("sub_y"):
                st["y"] = d["sub_y"]
            ovs.append(Overlay(int(round(t0 * fps)), int(round(t1 * fps)), fx.text_layer(W, H, sub_items(st, W, H)), "fade"))
    for s in edl.get("subs", []):
        t0, t1 = span(s, grid, 1.5)
        layer = fx.text_layer(W, H, sub_items(s, W, H))
        ovs.append(Overlay(int(round(t0 * fps)), int(round(t1 * fps)), layer, s.get("anim", "fade")))
    for t in edl.get("texts", []):
        t0, t1 = span(t, grid, 1.5)
        f0, f1 = int(round(t0 * fps)), int(round(t1 * fps))
        items = text_items(t, W, H)
        anim = t.get("anim", "fade")
        glass = None
        if t.get("style") == "glass":
            lay = fx.text_layer(W, H, items)
            if lay:
                h, w = lay["rgba"].shape[:2]
                padx, pady = int(W * 0.09), int(h * 0.9)
                glass = (lay["x"] - padx, lay["y"] - pady, lay["x"] + w + padx, lay["y"] + h + pady)
            ovs.append(Overlay(f0, f1, lay, anim, glass))
            continue
        if anim == "type":
            txt = t["text"]
            full = fx.text_layer(W, H, items)
            if full:
                its_l = [dict(it, anchor="l", x=full["x"] / W) for it in items]
                prefixes = [fx.text_layer(W, H, [dict(it, text=txt[:i]) for it in its_l]) for i in range(1, len(txt) + 1)]
                ovs.append(Overlay(f0, f1, full, "fade", None, prefixes))
            continue
        if t.get("words"):
            # word-by-word reveal: words laid out on one line, each appears at its time (seconds from at)
            words = t["words"]
            full = fx.text_layer(W, H, items)
            if not full:
                continue
            x = full["x"] / W
            lays = []
            f_ = fx.font(items[0]["kind"], int(items[0]["size"]))
            space = f_.getlength(" ")
            cur = 0.0
            for wd in words:
                w_txt, w_t = (wd if isinstance(wd, (list, tuple)) else (wd, None))
                it = dict(items[0], text=w_txt, anchor="l", x=x + cur / W)
                lays.append([fx.text_layer(W, H, [it]), w_t])
                cur += f_.getlength(w_txt) + space
            n = len(lays)
            for i, l in enumerate(lays):
                if l[1] is None:
                    l[1] = (t1 - t0) * 0.5 * i / max(1, n)
                l[1] = int(round(l[1] * fps))
            ovs.append(Overlay(f0, f1, full, anim, None, None, [(l[0], l[1]) for l in lays]))
            continue
        ovs.append(Overlay(f0, f1, fx.text_layer(W, H, items), anim,
                           fin=int(round(float(t.get("fade", [0.13, 0.13])[0]) * fps)),
                           fout=int(round(float(t.get("fade", [0.13, 0.13])[1]) * fps))))
    return ovs


# ---------------------------------------------------------------- audio
def load_audio(path, ss=0.0, dur=None):
    cmd = [FFMPEG, "-v", "error", "-ss", f"{max(0, ss):.4f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.4f}"]
    cmd += ["-i", str(path), "-vn", "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
    p = subprocess.run(cmd, capture_output=True)
    a = np.frombuffer(p.stdout, np.float32)
    return a.reshape(-1, 2).copy() if len(a) else np.zeros((0, 2), np.float32)


def fade(a, fin, fout):
    n = len(a)
    if n == 0:
        return a
    i, o = min(n, int(fin * SR)), min(n, int(fout * SR))
    if i > 0: a[:i] *= np.linspace(0, 1, i, dtype=np.float32)[:, None]
    if o > 0: a[n - o:] *= np.linspace(1, 0, o, dtype=np.float32)[:, None]
    return a


def music_track(path, pieces, n):
    """The music bus: each stretch read from the song and laid at its place on the timeline, joined to the next by an
    equal-power crossfade of `xfade` seconds centred on the seam (both sides run on xfade/2 past it)."""
    out = np.zeros((n, 2), np.float32)
    for k, p in enumerate(pieces):
        if p["a"] is None:
            continue
        h_in = pieces[k]["xfade"] / 2 if k > 0 else 0.0
        h_out = pieces[k + 1]["xfade"] / 2 if k + 1 < len(pieces) else 0.0
        a0, t0 = p["a"] - h_in, p["t0"] - h_in
        if a0 < 0:
            t0, a0 = t0 - a0, 0.0
        dur = (p["b"] + h_out - a0) if p["b"] is not None else n / SR - t0
        seg = load_audio(path, a0, max(0.0, dur))
        ni, no = min(len(seg), int(2 * h_in * SR)), min(len(seg), int(2 * h_out * SR))
        if ni: seg[:ni] *= np.sin(0.5 * np.pi * np.linspace(0, 1, ni, dtype=np.float32))[:, None]
        if no: seg[len(seg) - no:] *= np.cos(0.5 * np.pi * np.linspace(0, 1, no, dtype=np.float32))[:, None]
        i = int(round(t0 * SR))
        j = min(n, i + len(seg))
        if j > max(0, i):
            out[max(0, i):j] += seg[max(0, -i):j - i]
    return out


def k_loudness(a):
    """BS.1770 K-weighted loudness (LUFS, ungated) of a stereo block at 48 kHz"""
    from scipy.signal import lfilter
    if len(a) < SR // 10:
        return -99.0
    y = lfilter([1.53512485958697, -2.69169618940638, 1.19839281085285], [1, -1.69065929318241, 0.73248077421585], a, axis=0)
    y = lfilter([1.0, -2.0, 1.0], [1, -1.99004745483398, 0.99007225036621], y, axis=0)
    return float(-0.691 + 10 * np.log10(np.mean(y ** 2, axis=0).sum() + 1e-12))


def synth(kind, dur=None, seed=1):
    g = np.random.default_rng(seed)
    if kind == "whoosh":
        d = dur or 0.45; n = int(d * SR); t = np.linspace(0, 1, n)
        noise = g.standard_normal(n).astype(np.float32)
        from scipy.signal import butter, sosfilt
        out = np.zeros(n, np.float32)
        for i in range(0, n, 1024):   # sweeping band-pass
            fc = 400 + 3000 * math.sin(math.pi * min(1, i / n))
            sos = butter(2, [max(50, fc * 0.6), min(SR / 2 - 100, fc * 1.6)], btype="band", fs=SR, output="sos")
            out[i:i + 1024] = sosfilt(sos, noise[i:i + 1024])
        env = np.sin(np.pi * t) ** 2
        mono = out * env * 0.5
        pan = np.linspace(-0.6, 0.6, n)
        return np.stack([mono * (1 - pan) / 1.6, mono * (1 + pan) / 1.6], 1).astype(np.float32)
    if kind in ("hit", "boom"):
        d = dur or (0.6 if kind == "hit" else 1.6); n = int(d * SR); t = np.arange(n) / SR
        f = 55 * np.exp(-t * 3) + 38
        ph = 2 * np.pi * np.cumsum(f) / SR
        mono = (np.sin(ph) * np.exp(-t * (6 if kind == "hit" else 2.2)) * 0.9 + g.standard_normal(n) * np.exp(-t * 40) * 0.2).astype(np.float32)
        return np.stack([mono, mono], 1)
    if kind == "riser":
        d = dur or 1.5; n = int(d * SR); t = np.linspace(0, 1, n)
        noise = g.standard_normal(n).astype(np.float32)
        from scipy.signal import butter, sosfilt
        out = np.zeros(n, np.float32)
        for i in range(0, n, 1024):
            fc = 300 + 6000 * (i / n) ** 2
            sos = butter(2, min(SR / 2 - 100, fc), btype="low", fs=SR, output="sos")
            out[i:i + 1024] = sosfilt(sos, noise[i:i + 1024])
        mono = out * t ** 2 * 0.45
        return np.stack([mono, mono], 1).astype(np.float32)
    return np.zeros((1, 2), np.float32)


def mix_audio(edl, base, grid, clips, length, out_wav, pieces=None):
    n = int(math.ceil(length * SR)) + 1
    mixbus = np.zeros((n, 2), np.float32)
    duck = np.ones(n, np.float32)
    voice = np.zeros((n, 2), np.float32)
    spoken = []

    def place(bus, a, t, gain_db=0.0):
        i = int(round(t * SR))
        if i >= n or len(a) == 0:
            return
        j = min(n, i + len(a))
        a = a[max(0, -i):j - i] if i < 0 else a[:j - i]
        bus[max(0, i):j] += a * (10 ** (gain_db / 20))

    def add_duck(t0, t1, db):
        g = 10 ** (db / 20)
        r0, r1 = 0.12, 0.3
        i0, i1 = int((t0 - r0) * SR), int((t1 + r1) * SR)
        idx = np.arange(max(0, i0), min(n, i1))
        if len(idx) == 0:
            return
        tt = idx / SR
        env = np.ones(len(idx), np.float32)
        down = tt < t0
        env[down] = 1 - (1 - g) * np.clip((tt[down] - (t0 - r0)) / r0, 0, 1)
        mid = (tt >= t0) & (tt <= t1)
        env[mid] = g
        up = tt > t1
        env[up] = g + (1 - g) * np.clip((tt[up] - t1) / r1, 0, 1)
        duck[idx] = np.minimum(duck[idx], env)

    for d in edl.get("dialog", []):
        src = str((base / d["src"]).resolve())
        stem = d.get("stem", "vocals")   # the film's voice without its score (lines.py --sep); "mix" = as released
        if stem and stem != "mix":
            if os.path.exists(src + f".{stem}.wav"):
                src = src + f".{stem}.wav"
            elif "stem" in d:
                print(f"note: no {stem} stem for {d['src']} (run lines.py --sep); using the full mix")
        a = load_audio(src, d["in"], d["out"] - d["in"])
        if len(a) and d.get("norm", True):
            # lines come from different films and stems (20 dB apart): level each one by its loud half
            fr = a[: len(a) // 480 * 480].reshape(-1, 480, 2)
            e = np.sqrt((fr ** 2).mean(axis=(1, 2)) + 1e-12)
            loud = e[e >= np.median(e)] if len(e) else e
            rms = float(np.sqrt(np.mean(loud ** 2))) if len(loud) else 0.0
            if rms > 1e-5:
                a = a * float(np.clip(10 ** (-16 / 20) / rms, 10 ** (-12 / 20), 10 ** (30 / 20)))
        a = fade(a, d.get("fade_in", 0.015), d.get("fade_out", 0.04))
        t = grid.resolve(d.get("at", 0))
        place(voice, a, t, d.get("gain_db", 0))
        spoken.append((t, t + len(a) / SR, d.get("en") or d.get("zh") or Path(d["src"]).stem))
        if d.get("duck_db", -10) < 0:
            add_duck(t, t + len(a) / SR, d.get("duck_db", -10))
    for c in clips:
        if c.get("audio") is None or c.get("black") or c.get("color"):
            continue
        src = str((base / c["src"]).resolve())
        dur = (c["t1"] - c["t0"])
        a = load_audio(src, c["in"], dur * float(c.get("speed", 1)))
        if float(c.get("speed", 1)) != 1 and len(a):
            idx = np.linspace(0, len(a) - 1, int(dur * SR)).astype(int)
            a = a[idx]
        a = fade(a, 0.01, 0.03)
        place(voice, a, c["t0"], float(c["audio"]))
        if c.get("duck_db"):
            add_duck(c["t0"], c["t1"], c["duck_db"])
    m = edl.get("music")
    if m:
        a = music_track(base / m["file"], pieces or [dict(a=float(m.get("start", 0)), b=None, t0=0.0, xfade=0.0)], n)
        a = fade(a, m.get("fade_in", 0.0), m.get("fade_out", 1.5))
        mixbus += a * duck[:, None] * (10 ** (m.get("gain_db", 0) / 20))
    for i, s in enumerate(edl.get("sfx", [])):
        place(mixbus, synth(s["kind"], s.get("dur"), i + 1), grid.resolve(s["at"]), s.get("gain_db", -10))
    # each line against what plays under it (K-weighted): a line should sit about 10-15 dB over the music
    for t0, t1, txt in sorted(spoken):
        i0, i1 = max(0, int(t0 * SR)), min(n, int(t1 * SR))
        v, u = k_loudness(voice[i0:i1]), k_loudness(mixbus[i0:i1])
        flag = "  <- music too loud for this line" if v - u < 8 else ("  <- music very low" if v - u > 20 else "")
        print(f"  line {t0:6.2f}-{t1:6.2f}s  voice {v:6.1f}  music under it {u:6.1f}  -> {v - u:+5.1f} dB  {txt[:44]}{flag}")
    mixbus += voice
    peak = float(np.abs(mixbus).max()) if len(mixbus) else 0
    if peak > 0.98:
        mixbus *= 0.98 / peak
    import soundfile as sf
    raw = out_wav + ".raw.wav"
    sf.write(raw, mixbus, SR, subtype="FLOAT")
    # loudness: two-pass loudnorm to -14 LUFS / -1.5 dBTP (short-video platforms)
    r = run([FFMPEG, "-v", "info", "-hide_banner", "-i", raw, "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"], check=False)
    js = r.stderr[r.stderr.rfind("{"):r.stderr.rfind("}") + 1]
    try:
        j = json.loads(js)
        af = (f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={j['input_i']}:measured_TP={j['input_tp']}:"
              f"measured_LRA={j['input_lra']}:measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
    except Exception:
        af = "loudnorm=I=-14:TP=-1.5:LRA=11"
    run([FFMPEG, "-y", "-v", "error", "-i", raw, "-af", af, "-ar", str(SR), out_wav])
    os.remove(raw)


# ---------------------------------------------------------------- main
def render(edl_path, out=None, preview=False, t_from=None, t_to=None, stills=None):
    edl_path = Path(edl_path).resolve()
    base = edl_path.parent
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    W, H = edl.get("size", [1440, 1080])
    if preview:
        W, H = W // 2 // 2 * 2, H // 2 // 2 * 2
    fps = float(edl.get("fps", 30))
    grid, pieces = music_grid(edl, base)
    clips = layout_clips(edl, grid)
    edl["dialog"] = lines_from_clips(edl, clips)
    length = float(edl.get("length") or clips[-1]["t1"])
    nfr = int(round(length * fps))
    for c in clips:
        c["f0"], c["f1"] = int(round(c["t0"] * fps)), int(round(c["t1"] * fps))
    # overlap transitions need pre/post roll
    for i, c in enumerate(clips):
        c["_pre"] = c["_post"] = 0
    for i, c in enumerate(clips):
        name = c.get("fx", "cut")
        if name in fx.OVERLAP_FX and i > 0:
            L = int(c.get("fx_len", fx.FX_LEN.get(name, 8)))
            c["_pre"] = L // 2
            clips[i - 1]["_post"] = L - L // 2
            c["_L"] = L
    focus_cache_path = base / ".focus_cache.json"
    focus_cache = load_json(focus_cache_path, {}) or {}
    look = fx.Look(W, H, edl.get("look"))
    Reader.look = look
    overlays = build_overlays(edl, grid, W, H, fps)
    print(f"{len(clips)} clips, {length:.2f}s, {nfr} frames at {W}x{H}")
    readers = {}
    side = {}

    def reader(i):
        if i not in readers:
            c = clips[i]
            readers[i] = Reader(base, c, W, H, fps, c["_pre"], c["_post"], focus_cache)
            lay = c.get("layout")
            if lay:
                subs = []
                items = lay.get("items") or ([lay["b"]] if lay.get("b") else lay.get("srcs", []))
                for it in items:
                    sc = dict(it, t0=c["t0"], t1=c["t1"])
                    sc.setdefault("zoom", [1.0, 1.0])
                    subs.append(Reader(base, sc, W, H, fps, 0, 0, focus_cache))
                side[i] = subs
        return readers[i]

    def clip_frame(i, f):
        c = clips[i]
        r = reader(i)
        local = f - (c["f0"] - c["_pre"])
        img = r.frame(local)
        if c.get("layout"):
            img = layout_frame(img, c["layout"], f - c["f0"], c["f1"] - c["f0"], side.get(i, []), W, H, i)
        if r.lut is not None and r.lut is not False:
            img = cv2.LUT(img, r.lut)
        # cut effects: incoming (this clip's fx) and outgoing (next clip's fx)
        name = c.get("fx", "cut")
        if name in fx.CUT_FX and name != "cut" and i > 0:
            L = int(c.get("fx_len", fx.FX_LEN.get(name, 4)))
            img = fx.fx_in(name, img, f - c["f0"], L, i * 1000)
        if i + 1 < len(clips):
            nn = clips[i + 1].get("fx", "cut")
            if nn in fx.CUT_FX and nn != "cut":
                L = int(clips[i + 1].get("fx_len", fx.FX_LEN.get(nn, 4)))
                img = fx.fx_out(nn, img, c["f1"] - f, L, (i + 1) * 1000)
        return img

    frame_range = range(nfr)
    if t_from is not None or t_to is not None:
        frame_range = range(int((t_from or 0) * fps), min(nfr, int((t_to or length) * fps)))
    if stills:
        frame_range = sorted(set(min(nfr - 1, int(round(t * fps))) for t in stills))
    home = base.parent if base.name == "source" else base          # works/<date_topic>/source/edl.json -> works/<date_topic>/renders
    out = Path(out) if out else home / "renders" / (edl_path.stem + ("_preview" if preview else "") + ".mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    enc = None
    tmp_wav = None
    if not stills:
        tmp_wav = str(out.with_suffix(".wav"))
        t_a = time.time()
        mix_audio(edl, base, grid, clips, length, tmp_wav, pieces)
        print(f"audio mixed in {time.time() - t_a:.1f}s")
        ss = frame_range.start / fps
        # crf 18 looks the same as 16 here; the cap keeps grain and fast cuts from blowing the bitrate up
        vcodec = ["-c:v", "libx264", "-preset", "veryfast" if preview else "medium", "-crf", "23" if preview else "18",
                  "-maxrate", "16M", "-bufsize", "32M", "-pix_fmt", "yuv420p"]
        cmd = [FFMPEG, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", f"{fps}", "-i", "-",
               "-ss", f"{ss:.3f}", "-i", tmp_wav, "-map", "0:v", "-map", "1:a"] + vcodec + \
              ["-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart", str(out)]
        enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t_start = time.time()
    end_fade = int(round(float(edl.get("end_fade", 0)) * fps))
    ci = 0
    for f in frame_range:
        while ci + 1 < len(clips) and f >= clips[ci]["f1"]:
            ci += 1
        while ci > 0 and f < clips[ci]["f0"]:
            ci -= 1
        c = clips[ci]
        img = None
        # overlap transition into the next clip (second half) or from the previous (first half)
        nxt = clips[ci + 1] if ci + 1 < len(clips) else None
        if nxt is not None and nxt.get("_L") and f >= nxt["f0"] - nxt["_pre"]:
            L = nxt["_L"]; p = (f - (nxt["f0"] - nxt["_pre"]) + 0.5) / L
            img = fx.transition(nxt["fx"], clip_frame(ci, f), clip_frame(ci + 1, f), p, ci, reader(ci + 1).focus_px())
        elif c.get("_L") and f < c["f0"] + (c["_L"] - c["_pre"]) and ci > 0:
            L = c["_L"]; p = (f - (c["f0"] - c["_pre"]) + 0.5) / L
            img = fx.transition(c["fx"], clip_frame(ci - 1, f), clip_frame(ci, f), p, ci - 1, reader(ci).focus_px())
        else:
            img = clip_frame(ci, f)
        img = look.apply(img, look.base_lut, f)
        for ov in overlays:
            img = ov.draw(img, f)
        if end_fade and f >= nfr - end_fade:      # "end_fade": seconds of fade to black at the very end, words included
            img = (img.astype(np.float32) * ((nfr - 1 - f) / end_fade)).astype(np.uint8)
        # free finished readers
        for j in list(readers):
            if j < ci - 1:
                readers[j].close(); del readers[j]
                for s in side.pop(j, []):
                    s.close()
        if stills:
            p = out.parent / f"{edl_path.stem}_still_{f / fps:06.2f}.png"
            cv2.imwrite(str(p), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
            print(p)
        else:
            enc.stdin.write(np.ascontiguousarray(img).tobytes())
        k = f - frame_range[0] if not stills else 0
        if not stills and k and k % 150 == 0:
            el = time.time() - t_start
            print(f"  frame {f}/{nfr}  {k / el:.1f} fps", flush=True)
    for r in readers.values():
        r.close()
    for ss_ in side.values():
        for s in ss_:
            s.close()
    save_json(focus_cache_path, focus_cache)
    if enc:
        enc.stdin.close(); enc.wait()
        if tmp_wav and os.path.exists(tmp_wav):
            os.remove(tmp_wav)
        print(f"wrote {out}  ({time.time() - t_start:.0f}s)")
    # timeline sidecar for qc: cut times + beat positions, the beat grid and the music seams
    save_json(str(out) + ".timeline.json", dict(fps=fps, length=length, music_start=grid.start, size=[W, H],
                                                cuts=[c["t0"] for c in clips[1:]],
                                                beats=[round(float(b - grid.start), 4) for b in grid.b if -0.5 <= b - grid.start <= length + 0.5],
                                                seams=[round(p["t0"], 4) for p in pieces[1:]],
                                                clips=[dict(i=i, t0=round(c["t0"], 3), t1=round(c["t1"], 3), src=c.get("src"), fx=c.get("fx", "cut"), **{"in": c.get("in")},
                                                            beat=round(grid.pos(c["t0"]) - grid.i0, 2), note=c.get("note", "")) for i, c in enumerate(clips)]))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("edl"); ap.add_argument("-o", "--out")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--from", dest="t_from", type=float); ap.add_argument("--to", dest="t_to", type=float)
    ap.add_argument("--still", type=float, action="append")
    a = ap.parse_args()
    render(a.edl, a.out, a.preview, a.t_from, a.t_to, a.still)
