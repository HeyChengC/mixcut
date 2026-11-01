"""Frame effects for render.py: look (grade), cut effects, overlap transitions, layouts helpers, text.

All frames are uint8 RGB arrays (H, W, 3). Everything is deterministic (seeded by frame index).
"""
import math
import numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from mc_common import find_font


def ease_out(x):
    x = min(1.0, max(0.0, x)); return 1 - (1 - x) ** 3


def ease_in_out(x):
    x = min(1.0, max(0.0, x)); return x * x * (3 - 2 * x)


def rng(seed):
    return np.random.default_rng(seed & 0xFFFFFFFF)


# ---------------------------------------------------------------- look
class Look:
    """Unifies footage from many films: exposure normalisation per clip + one global grade."""

    def __init__(self, W, H, cfg=None):
        c = dict(contrast=1.06, saturation=1.04, warmth=0.0, tint=0.0, lift=0.0, gamma=1.0, split=0.0,
                 grain=0.03, vignette=0.2, sharpen=0.25, glow=0.0, ca=0.0, normalize=0.5, fade=0.0)
        c.update(cfg or {})
        self.c, self.W, self.H = c, W, H
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / math.sqrt(2)
        self.vig = (1 - c["vignette"] * np.clip(r - 0.35, 0, 1) ** 1.6 * 1.9).clip(0, 1)[..., None].astype(np.float32)
        g = rng(7)
        self.grain = [(g.standard_normal((H // 2, W // 2)) * 255 * c["grain"]).astype(np.float32) for _ in range(6)]
        self.base_lut = self._lut(1.0)
        self.identity = abs(c["contrast"] - 1) < 1e-3 and not (c["lift"] or c["warmth"] or c["tint"] or c["split"] or c["fade"]) and abs(c["gamma"] - 1) < 1e-3

    def _lut(self, gain):
        c = self.c
        x = np.arange(256, dtype=np.float32) / 255.0
        x = np.clip(x * gain, 0, 1)
        x = c["lift"] + (1 - c["lift"]) * x
        x = np.power(np.clip(x, 0, 1), 1.0 / max(0.2, c["gamma"]))
        k = c["contrast"]
        if abs(k - 1) > 1e-3:   # filmic S around mid grey
            x = 0.5 + (x - 0.5) * k
        x = np.clip(x, 0, 1)
        if c["fade"]:
            x = c["fade"] * 0.08 + x * (1 - c["fade"] * 0.12)
        luts = []
        w, t, sp = c["warmth"], c["tint"], c["split"]
        for ch in range(3):   # R G B
            y = x.copy()
            if ch == 0: y = y + w * 0.06 * (1 - y) * y * 4
            if ch == 2: y = y - w * 0.06 * (1 - y) * y * 4
            if ch == 1: y = y - t * 0.04 * (1 - y) * y * 4
            if sp:   # teal shadows / warm highlights
                sh, hi = np.clip(1 - y * 2, 0, 1), np.clip(y * 2 - 1, 0, 1)
                if ch == 0: y = y - sp * 0.05 * sh + sp * 0.05 * hi
                if ch == 1: y = y + sp * 0.01 * sh
                if ch == 2: y = y + sp * 0.06 * sh - sp * 0.05 * hi
            luts.append(np.clip(y * 255 + 0.5, 0, 255).astype(np.uint8))
        return np.stack(luts, axis=1).reshape(256, 1, 3)

    def clip_lut(self, frames):
        """Per-clip exposure LUT: a gamma that pulls the clip's mean luma toward mid grey (strength = normalize).
        Gamma (not gain) so highlights never clip."""
        n = self.c["normalize"]
        if not n or not frames:
            return None
        L = float(np.mean([cv2.cvtColor(cv2.resize(f, (96, 72), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY).mean() / 255 for f in frames]))
        L = min(0.9, max(0.02, L))
        target = 0.38
        gam = math.log(target) / math.log(L)
        gam = 1 - (1 - gam) * n
        gam = float(np.clip(gam, 0.62, 1.45))
        if abs(gam - 1) < 0.02:
            return None
        x = np.arange(256, dtype=np.float32) / 255.0
        y = np.clip(np.power(x, gam) * 255 + 0.5, 0, 255).astype(np.uint8)
        return np.stack([y, y, y], axis=1).reshape(256, 1, 3)

    def apply(self, img, lut, f):
        c = self.c
        out = img if self.identity else cv2.LUT(img, lut)
        if abs(c["saturation"] - 1) > 1e-3:
            gray = cv2.cvtColor(out, cv2.COLOR_RGB2GRAY)
            out = cv2.addWeighted(out, c["saturation"], cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB), 1 - c["saturation"], 0)
        if c["glow"]:
            small = cv2.resize(out, (self.W // 4, self.H // 4), interpolation=cv2.INTER_AREA)
            hi = np.clip(small.astype(np.int16) - 150, 0, 255).astype(np.uint8)
            hi = cv2.GaussianBlur(hi, (0, 0), 6)
            hi = cv2.resize(hi, (self.W, self.H), interpolation=cv2.INTER_LINEAR)
            out = cv2.add(out, (hi * c["glow"]).astype(np.uint8))
        if c["sharpen"]:
            bl = cv2.GaussianBlur(out, (0, 0), 1.2)
            out = cv2.addWeighted(out, 1 + c["sharpen"], bl, -c["sharpen"], 0)
        if c["ca"]:
            out = chroma_shift(out, c["ca"])
        o = out.astype(np.float32) * self.vig
        if c["grain"]:
            gn = cv2.resize(self.grain[f % len(self.grain)], (self.W, self.H), interpolation=cv2.INTER_LINEAR)
            o += gn[..., None]
        return np.clip(o, 0, 255).astype(np.uint8)


def chroma_shift(img, amt):
    """Lateral chromatic aberration: red scaled out, blue scaled in, around the centre."""
    H, W = img.shape[:2]
    out = img.copy()
    for ch, s in ((0, 1 + amt), (2, 1 - amt)):
        M = cv2.getRotationMatrix2D((W / 2, H / 2), 0, s)
        out[..., ch] = cv2.warpAffine(img[..., ch], M, (W, H), borderMode=cv2.BORDER_REFLECT)
    return out


# ---------------------------------------------------------------- per-frame camera
def frame_from_work(work, W, H, cx, cy, s, dx=0.0, dy=0.0, angle=0.0, mirror=False):
    """W x H view of a work frame: work point (cx, cy) lands at the output centre, scale s (work px -> output px)."""
    M = cv2.getRotationMatrix2D((cx, cy), angle, s)
    M[0, 2] += W / 2 - cx + dx
    M[1, 2] += H / 2 - cy + dy
    out = cv2.warpAffine(work, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    return np.ascontiguousarray(out[:, ::-1]) if mirror else out


def clamp_center(cx, cy, ww, wh, W, H, s):
    hw, hh = W / s / 2, H / s / 2
    cx = min(max(cx, hw), ww - hw) if ww >= 2 * hw else ww / 2
    cy = min(max(cy, hh), wh - hh) if wh >= 2 * hh else wh / 2
    return cx, cy


# ---------------------------------------------------------------- cut effects (non-overlap)
def motion_blur(img, length, horizontal=True):
    length = int(max(1, length))
    if length < 3:
        return img
    k = np.zeros((length, length), np.float32)
    if horizontal: k[length // 2, :] = 1.0 / length
    else: k[:, length // 2] = 1.0 / length
    return cv2.filter2D(img, -1, k, borderType=cv2.BORDER_REFLECT)


def shift(img, dx, dy=0):
    H, W = img.shape[:2]
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, M, (W, H), borderMode=cv2.BORDER_REFLECT)


def zoom_img(img, s, cx=None, cy=None):
    H, W = img.shape[:2]
    M = cv2.getRotationMatrix2D((W / 2 if cx is None else cx, H / 2 if cy is None else cy), 0, s)
    return cv2.warpAffine(img, M, (W, H), borderMode=cv2.BORDER_REFLECT)


def blend(a, b, t):
    return cv2.addWeighted(a, 1 - t, b, t, 0)


def white(img, t):
    return cv2.addWeighted(img, 1 - t, np.full_like(img, 255), t, 0)


def black(img, t):
    return cv2.convertScaleAbs(img, alpha=1 - t)


def glitch(img, seed, strength=1.0):
    g = rng(seed)
    H, W = img.shape[:2]
    out = img.copy()
    dx = int(g.integers(8, 26) * strength)
    out[..., 0] = np.roll(img[..., 0], dx, axis=1)
    out[..., 2] = np.roll(img[..., 2], -dx, axis=1)
    for _ in range(int(g.integers(4, 9))):
        y0 = int(g.integers(0, H - 10)); h = int(g.integers(6, max(8, H // 10)))
        off = int(g.normal(0, 50 * strength))
        out[y0:y0 + h] = np.roll(out[y0:y0 + h], off, axis=1)
    if g.random() < 0.5:
        out = cv2.addWeighted(out, 1.0, np.full_like(out, int(g.integers(10, 40))), 0.3, 0)
    return out


def rgb_split(img, amt):
    dx = int(amt)
    if dx == 0:
        return img
    out = img.copy()
    out[..., 0] = np.roll(img[..., 0], dx, axis=1)
    out[..., 2] = np.roll(img[..., 2], -dx, axis=1)
    return out


def dblur(img, length, horizontal=True):
    """fast directional (box) motion blur"""
    n = int(max(1, length))
    if n < 3:
        return img
    return cv2.blur(img, (n, 1) if horizontal else (1, n), borderType=cv2.BORDER_REFLECT)


def gblur(img, sigma):
    return cv2.GaussianBlur(img, (0, 0), sigma) if sigma > 0.4 else img


def expose(img, q):
    """Push towards blown-out white (q 0..1): highlights clip first, like overexposure, then a light veil."""
    if q <= 0.01:
        return img
    x = img.astype(np.float32) / 255.0
    y = 1 - (1 - x) ** (1 + 5 * q)
    y = y * (1 - 0.35 * q) + 0.35 * q
    return np.clip(y * 255, 0, 255).astype(np.uint8)


def screen(img, light):
    x = img.astype(np.float32)
    return (255 - (255 - x) * (255 - np.clip(light, 0, 255)) / 255).astype(np.uint8)


def light_rays(img, s, focus=None, tint=(255, 238, 210)):
    """Radial light burst (ref A 7.85 s): the frame's highlights smeared outward from focus, screened on, strength s."""
    if s <= 0.01:
        return img
    H, W = img.shape[:2]
    cx, cy = focus or (W / 2, H * 0.42)
    w4, h4 = W // 4, H // 4
    small = cv2.resize(img, (w4, h4), interpolation=cv2.INTER_AREA).astype(np.float32)
    lum = small.mean(axis=2, keepdims=True)
    hi = np.clip((lum - 120) / 135, 0, 1) * small
    acc = np.zeros_like(hi)
    n = 12
    for i in range(n):
        M = cv2.getRotationMatrix2D((cx / 4, cy / 4), 0, 1 + 0.07 * i)
        acc += cv2.warpAffine(hi, M, (w4, h4), borderMode=cv2.BORDER_CONSTANT) * (1 - i / n)
    acc = cv2.GaussianBlur(acc / (n / 2.5), (0, 0), 1.5)
    acc = cv2.resize(acc, (W, H), interpolation=cv2.INTER_LINEAR) * (np.array(tint, np.float32) / 255) * s
    return screen(img, acc)


def light_leak(img, p, s, color=(255, 138, 58)):
    """Warm light leak sweeping across (ref D 28.6 s): an orange glow with a hot core moving left to right, screened on."""
    if s <= 0.01:
        return img
    H, W = img.shape[:2]
    cx, cy = W * (0.1 + 0.8 * p), H * (0.62 - 0.2 * p)
    yy, xx = np.ogrid[0:H, 0:W]
    d = np.sqrt(((xx - cx) / (W * 0.6)) ** 2 + ((yy - cy) / (H * 0.7)) ** 2).astype(np.float32)
    glow = (np.clip(1 - d, 0, 1) ** 1.5 * s)[..., None] * np.array(color, np.float32)
    core = (np.clip(1 - d * 2.2, 0, 1) ** 2 * s * 255)[..., None]
    return screen(img, glow + core)


CUT_FX = {"cut", "flash", "flash_black", "dip", "whip", "whip_l", "whip_r", "whip_u", "whip_d", "zoom", "glitch",
          "shake", "rgb", "blur", "strobe", "zoom_out",
          "whip_in", "whip_in_l", "whip_in_u", "whip_in_d", "flash_blur", "rays", "leak", "blur_dark"}
OVERLAP_FX = {"dissolve", "circle", "wipe", "wipe_l", "wipe_r", "strips", "slide", "slide_l", "slide_r", "luma", "white_dissolve"}
FX_LEN = {"flash": 3, "flash_black": 3, "dip": 4, "whip": 4, "whip_l": 4, "whip_r": 4, "whip_u": 4, "whip_d": 4, "zoom": 7,
          "glitch": 4, "shake": 9, "rgb": 5, "blur": 7, "strobe": 6, "zoom_out": 4,
          "dissolve": 10, "circle": 10, "wipe": 8, "wipe_l": 8, "wipe_r": 8, "strips": 10, "slide": 6, "slide_l": 6,
          "slide_r": 6, "luma": 12, "white_dissolve": 10,
          "whip_in": 3, "whip_in_l": 3, "whip_in_u": 3, "whip_in_d": 3, "flash_blur": 4, "rays": 12, "leak": 8, "blur_dark": 14}


def fx_out(name, img, k_left, L, seed):
    """Effect on the OUTGOING clip's last frames (k_left = frames until the cut, 1 = last frame)."""
    if name == "flash" and k_left == 1:
        return white(img, 0.25)
    if name in ("flash_black", "dip") and k_left <= 2:
        return black(img, 0.35 * (3 - k_left))
    if name.startswith("whip") and not name.startswith("whip_in") and k_left <= L // 2:   # whip_in leaves the outgoing shot alone
        p = (L // 2 - k_left + 1) / (L // 2)
        horiz = not name.endswith(("_u", "_d"))
        sgn = -1 if name.endswith(("_l", "_u")) else 1
        W = img.shape[1] if horiz else img.shape[0]
        b = motion_blur(img, W * 0.12 * p, horiz)
        return shift(b, sgn * W * 0.10 * p ** 2, 0) if horiz else shift(b, 0, sgn * W * 0.10 * p ** 2)
    if name == "zoom_out" and k_left <= L:
        p = (L - k_left + 1) / L
        return zoom_img(img, 1 + 0.18 * p ** 2)
    if name == "glitch" and k_left == 1:
        return glitch(img, seed + 99, 0.6)
    if name == "flash_blur" and k_left <= L:      # ref D 23.8 s: the outgoing shot blurs and blows out into the cut
        q = (L - k_left + 1) / L
        return expose(gblur(img, 16 * q * q), 0.9 * q * q)
    if name == "blur_dark" and k_left <= 3:       # ref D 10.4 s: the shot blurs away under a title
        q = (4 - k_left) / 3
        return black(gblur(img, 16 * q), 0.3 * q)
    return img


def fx_in(name, img, k, L, seed, focus=None):
    """Effect on the INCOMING clip's first frames (k = 0 is the cut frame)."""
    if name == "flash" and k < L:
        return white(img, [0.85, 0.45, 0.15, 0.05][min(k, 3)])
    if name in ("flash_black", "dip") and k < L:
        return black(img, [0.85, 0.5, 0.2, 0.05][min(k, 3)])
    if name.startswith("whip") and not name.startswith("whip_in") and k < (L + 1) // 2:
        p = 1 - k / ((L + 1) // 2)
        horiz = not name.endswith(("_u", "_d"))
        sgn = -1 if name.endswith(("_l", "_u")) else 1
        W = img.shape[1] if horiz else img.shape[0]
        b = motion_blur(img, W * 0.12 * p, horiz)
        return shift(b, -sgn * W * 0.10 * p ** 2, 0) if horiz else shift(b, 0, -sgn * W * 0.10 * p ** 2)
    if name == "zoom" and k < L:
        p = ease_out(k / L)
        s = 1.22 - 0.22 * p
        out = zoom_img(img, s, *(focus or (None, None)))
        if k < 2:
            out = cv2.addWeighted(out, 0.6, zoom_img(img, s * 1.06, *(focus or (None, None))), 0.4, 0)
        return out
    if name == "glitch" and k < L:
        return glitch(img, seed + k, 1.0 - k / (L + 1))
    if name == "shake" and k < L:
        g = rng(seed + k)
        a = (1 - k / L) ** 1.5
        return shift(img, g.normal(0, 16) * a, g.normal(0, 12) * a)
    if name == "rgb" and k < L:
        return rgb_split(img, 18 * (1 - k / L))
    if name == "blur" and k < L:
        s = 14 * (1 - ease_out(k / L))
        return cv2.GaussianBlur(img, (0, 0), s) if s > 0.5 else img
    if name == "strobe" and k < L:
        return white(img, 0.45) if k % 2 == 0 else img
    if name.startswith("whip_in") and k < L:      # ref A: only the incoming shot is smeared; clear in 2-4 frames
        p = (1 - k / L) ** 1.6
        horiz = not name.endswith(("_u", "_d"))
        sgn = -1 if name.endswith(("_l", "_u")) else 1
        n = img.shape[1] if horiz else img.shape[0]
        b = dblur(img, n * 0.30 * p, horiz)
        return shift(b, sgn * n * 0.05 * p, 0) if horiz else shift(b, 0, sgn * n * 0.05 * p)
    if name == "flash_blur" and k < L:            # ...and the incoming one starts blown out and clears
        q = 1 - k / L
        return expose(gblur(img, 16 * q * q), 0.95 * q * q)
    if name == "rays" and k < L:                  # ref A 7.85 s: light bursts out of the frame, ~12 frames
        return light_rays(img, 1.4 * (1 - k / L) ** 1.3, focus)
    if name == "leak" and k < L:                  # ref D 28.6 s: white, then a warm leak sweeps across
        p = k / L
        return light_leak(white(img, 0.7) if k == 0 else img, p, (1 - p) ** 0.8)
    if name == "blur_dark" and k < L:             # the next shot rises out of the dark
        return black(img, 0.92 * (1 - ease_in_out(k / L)))
    return img


def transition(name, a, b, p, seed, focus_b=None):
    """Overlap transition: p in [0, 1] from fully A to fully B."""
    H, W = a.shape[:2]
    if name == "dissolve":
        return blend(a, b, ease_in_out(p))
    if name == "white_dissolve":
        if p < 0.5:
            return white(a, ease_in_out(p * 2) * 0.9)
        return white(b, (1 - ease_in_out((p - 0.5) * 2)) * 0.9)
    if name == "luma":
        la = cv2.cvtColor(b, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
        t = ease_in_out(p) * 1.4 - 0.2
        m = np.clip((la - (1 - t)) / 0.2 + 0.5, 0, 1)[..., None]
        return (a * (1 - m) + b * m).astype(np.uint8)
    if name == "circle":
        cx, cy = focus_b or (W / 2, H / 2)
        yy, xx = np.ogrid[0:H, 0:W]
        rmax = math.hypot(max(cx, W - cx), max(cy, H - cy))
        r = ease_in_out(p) * rmax
        d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        m = np.clip((r - d) / 18 + 0.5, 0, 1)[..., None].astype(np.float32)
        return (a * (1 - m) + b * m).astype(np.uint8)
    if name.startswith("wipe"):
        sgn = -1 if name.endswith("_l") else 1
        xx = np.arange(W, dtype=np.float32)
        edge = ease_in_out(p) * (W + 120) - 60
        pos = xx if sgn > 0 else W - xx
        m = np.clip((edge - pos) / 60 + 0.5, 0, 1)[None, :, None]
        return (a * (1 - m) + b * m).astype(np.uint8)
    if name == "strips":
        n = 4
        out = a.copy()
        hb = H // n
        for i in range(n):
            q = ease_out(min(1, max(0, p * 1.6 - i * 0.15)))
            off = int((1 - q) * W) * (1 if i % 2 == 0 else -1)
            band = b[i * hb:(i + 1) * hb if i < n - 1 else H]
            sh = np.roll(band, off, axis=1)
            if off > 0: sh[:, :off] = out[i * hb:i * hb + band.shape[0], :off]
            elif off < 0: sh[:, off:] = out[i * hb:i * hb + band.shape[0], off:]
            out[i * hb:i * hb + band.shape[0]] = sh
        return out
    if name.startswith("slide"):
        sgn = -1 if name.endswith("_l") else 1
        q = ease_in_out(p)
        off = int(q * W) * sgn
        a2 = motion_blur(shift(a, -off), 40 * math.sin(q * math.pi), True)
        b2 = motion_blur(shift(b, sgn * W - off), 40 * math.sin(q * math.pi), True)
        m = np.zeros((1, W, 1), np.float32)
        xx = np.arange(W)
        m[0, :, 0] = (xx >= W - abs(off)) if sgn > 0 else (xx < abs(off))
        return (a2 * (1 - m) + b2 * m).astype(np.uint8)
    return b if p >= 0.5 else a


# ---------------------------------------------------------------- shapes for layouts
def rounded_mask(w, h, r):
    m = np.zeros((h, w), np.uint8)
    r = int(min(r, w // 2, h // 2))
    cv2.rectangle(m, (r, 0), (w - r, h), 255, -1)
    cv2.rectangle(m, (0, r), (w, h - r), 255, -1)
    for cx, cy in ((r, r), (w - r - 1, r), (r, h - r - 1), (w - r - 1, h - r - 1)):
        cv2.circle(m, (cx, cy), r, 255, -1, lineType=cv2.LINE_AA)
    return m.astype(np.float32) / 255


def paste(dst, src, x, y, mask=None, shadow=0.0):
    """Paste src (h, w, 3) onto dst at (x, y) with optional float mask and drop shadow."""
    H, W = dst.shape[:2]
    h, w = src.shape[:2]
    if mask is None:
        mask = np.ones((h, w), np.float32)
    if shadow:
        pad = 24
        sm = np.zeros((h + pad * 2, w + pad * 2), np.float32)
        sm[pad:pad + h, pad:pad + w] = mask
        sm = cv2.GaussianBlur(sm, (0, 0), 10) * shadow
        sx, sy = x - pad + 6, y - pad + 10
        x0, y0 = max(0, sx), max(0, sy); x1, y1 = min(W, sx + sm.shape[1]), min(H, sy + sm.shape[0])
        if x1 > x0 and y1 > y0:
            region = dst[y0:y1, x0:x1].astype(np.float32)
            a = sm[y0 - sy:y1 - sy, x0 - sx:x1 - sx][..., None]
            dst[y0:y1, x0:x1] = (region * (1 - a)).astype(np.uint8)
    x0, y0 = max(0, x), max(0, y); x1, y1 = min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return dst
    a = mask[y0 - y:y1 - y, x0 - x:x1 - x][..., None]
    region = dst[y0:y1, x0:x1].astype(np.float32)
    dst[y0:y1, x0:x1] = (region * (1 - a) + src[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32) * a).astype(np.uint8)
    return dst


def cover(img, w, h, cx=0.5, cy=0.5):
    """Resize-crop img to exactly w x h, keeping (cx, cy) (normalised) in view."""
    ih, iw = img.shape[:2]
    s = max(w / iw, h / ih)
    rw, rh = int(math.ceil(iw * s)), int(math.ceil(ih * s))
    r = cv2.resize(img, (rw, rh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
    x0 = int(min(max(cx * rw - w / 2, 0), rw - w)); y0 = int(min(max(cy * rh - h / 2, 0), rh - h))
    return r[y0:y0 + h, x0:x0 + w]


# ---------------------------------------------------------------- text
FONTS = {
    "zh_bold": ("NotoSansSC-VF.ttf", "msyhbd.ttc", "simhei.ttf"),
    "zh_reg": ("msyh.ttc", "NotoSansSC-VF.ttf", "simhei.ttf"),
    "zh_light": ("msyhl.ttc", "Dengl.ttf", "msyh.ttc"),
    "zh_serif": ("NotoSerifSC-VF.ttf", "simsun.ttc"),
    "zh_sans": ("NotoSansSC-VF.ttf", "msyhl.ttc"),          # variable: pass weight (300 = light)
    "en_serif": ("times.ttf", "georgia.ttf"),
    "en_serif_bold": ("georgiab.ttf", "timesbd.ttf"),
    "en_serif_italic": ("georgiai.ttf", "timesi.ttf"),
    "en_display": ("georgiaz.ttf", "georgiab.ttf"),
    "en_thin": ("segoeuil.ttf", "bahnschrift.ttf", "arial.ttf"),
    "en_thin_italic": ("segoeuii.ttf", "segoeuil.ttf"),
    "en_sans_bold": ("segoeuib.ttf", "arialbd.ttf"),
    "pf_roman": ("PlayfairDisplay-Regular.ttf", "georgia.ttf"),          # high-contrast serif (ref D's title/lyrics)
    "pf_italic": ("PlayfairDisplay-Italic.ttf", "georgiai.ttf"),
    "pf_italic_med": ("PlayfairDisplay-MediumItalic.ttf", "georgiai.ttf"),
}
_fcache = {}


def font(kind, size, weight=None):
    key = (kind, size, weight)
    if key in _fcache:
        return _fcache[key]
    path = find_font(*FONTS.get(kind, (kind,)))
    f = ImageFont.truetype(path, size) if path else ImageFont.load_default()
    if path and path.endswith("-VF.ttf"):
        try:
            axes = f.get_variation_axes()
            if axes:
                f.set_variation_by_axes([weight or (700 if "bold" in kind else 400)])
        except Exception:
            pass
    _fcache[key] = f
    return f


def parse_marks(text):
    """'在[错误]中自由了' -> [('在', False), ('错误', True), ('中自由了', False)]"""
    out, cur, hl = [], "", False
    for ch in text:
        if ch == "[" and not hl:
            if cur: out.append((cur, False)); cur = ""
            hl = True
        elif ch == "]" and hl:
            out.append((cur, True)); cur = ""; hl = False
        else:
            cur += ch
    if cur: out.append((cur, hl))
    return out


def text_width(f, s, tracking=0):
    if not s:
        return 0
    return f.getlength(s) + tracking * (len(s) - 1)


def draw_line(d, x, y, parts, f, fill, hl_fill, tracking=0, shadow=None, stroke=0, stroke_fill=(0, 0, 0, 255)):
    for seg, hl in parts:
        col = hl_fill if hl else fill
        if tracking:
            for ch in seg:
                d.text((x, y), ch, font=f, fill=col, stroke_width=stroke, stroke_fill=stroke_fill)
                x += f.getlength(ch) + tracking
        else:
            d.text((x, y), seg, font=f, fill=col, stroke_width=stroke, stroke_fill=stroke_fill)
            x += f.getlength(seg)
    return x


def text_layer(W, H, items):
    """Render text items onto a transparent RGBA layer. item: dict(text, kind, size, x, y, anchor, fill, hl,
    tracking, shadow, glow, stroke, weight). x, y normalised. anchor: 'c' centre, 'l' left, 'r' right."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    glow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d, dg, ds = ImageDraw.Draw(layer), ImageDraw.Draw(glow_layer), ImageDraw.Draw(shadow_layer)
    any_glow = any_shadow = False
    blur = max([float(it.get("shadow_blur", 3)) for it in items] or [3.0])
    for it in items:
        f = font(it.get("kind", "zh_bold"), int(it.get("size", 40)), it.get("weight"))
        parts = parse_marks(it["text"])
        plain = "".join(p for p, _ in parts)
        tr = it.get("tracking", 0) * it.get("size", 40)
        tw = text_width(f, plain, tr)
        asc, desc = f.getmetrics()
        x = it["x"] * W; y = it["y"] * H - (asc + desc) / 2
        anc = it.get("anchor", "c")
        if anc == "c": x -= tw / 2
        elif anc == "r": x -= tw
        fill = tuple(it.get("fill", (255, 255, 255, 255)))
        hl = tuple(it.get("hl", (230, 40, 40, 255)))
        if it.get("shadow"):
            any_shadow = True
            off = it.get("shadow_off", (0, 2))
            draw_line(ds, x + off[0], y + off[1], parts, f, (0, 0, 0, int(255 * it["shadow"])), (0, 0, 0, int(255 * it["shadow"])), tr)
        if it.get("offset_shadow"):   # hard coloured offset (retro lyric type)
            oc, (ox, oy) = tuple(it["offset_shadow"][0]), it["offset_shadow"][1]
            draw_line(d, x + ox, y + oy, parts, f, oc, oc, tr)
        if it.get("glow"):
            any_glow = True
            draw_line(dg, x, y, parts, f, (255, 255, 255, int(255 * it["glow"])), (255, 255, 255, int(255 * it["glow"])), tr, stroke=3, stroke_fill=(255, 255, 255, int(255 * it["glow"])))
        draw_line(d, x, y, parts, f, fill, hl, tr, stroke=it.get("stroke", 0), stroke_fill=tuple(it.get("stroke_fill", (0, 0, 0, 200))))
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if any_shadow:
        out = Image.alpha_composite(out, shadow_layer.filter(ImageFilter.GaussianBlur(blur)))
    if any_glow:
        out = Image.alpha_composite(out, glow_layer.filter(ImageFilter.GaussianBlur(8)))
    out = Image.alpha_composite(out, layer)
    a = np.array(out)
    ys, xs = np.nonzero(a[..., 3] > 0)
    if len(ys) == 0:
        return None
    y0, y1, x0, x1 = max(0, ys.min() - 2), min(H, ys.max() + 3), max(0, xs.min() - 2), min(W, xs.max() + 3)
    return dict(rgba=a[y0:y1, x0:x1], x=int(x0), y=int(y0))


def comp_rgba(frame, layer, alpha=1.0, dx=0, dy=0, scale=1.0):
    if layer is None or alpha <= 0:
        return frame
    rgba = layer["rgba"]
    x, y = layer["x"] + int(dx), layer["y"] + int(dy)
    if abs(scale - 1) > 1e-3:
        h, w = rgba.shape[:2]
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        rgba = cv2.resize(rgba, (nw, nh), interpolation=cv2.INTER_LINEAR)
        x += (w - nw) // 2; y += (h - nh) // 2
    H, W = frame.shape[:2]
    h, w = rgba.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return frame
    sub = rgba[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32)
    a = sub[..., 3:4] / 255.0 * alpha
    reg = frame[y0:y1, x0:x1].astype(np.float32)
    frame[y0:y1, x0:x1] = (reg * (1 - a) + sub[..., :3] * a).astype(np.uint8)
    return frame


def glass_band(frame, x0, y0, x1, y1, strength=1.0):
    """Frosted-glass band behind a title: blur + lighten the region with feathered edges."""
    H, W = frame.shape[:2]
    x0, y0, x1, y1 = max(0, int(x0)), max(0, int(y0)), min(W, int(x1)), min(H, int(y1))
    if x1 <= x0 or y1 <= y0:
        return frame
    reg = frame[y0:y1, x0:x1]
    bl = cv2.GaussianBlur(reg, (0, 0), 18)
    bl = cv2.addWeighted(bl, 0.82, np.full_like(bl, 255), 0.18, 0)
    h, w = reg.shape[:2]
    m = np.ones((h, w), np.float32)
    fe = max(4, min(h, w) // 3)
    ramp_y = np.clip(np.minimum(np.arange(h), np.arange(h)[::-1]) / fe, 0, 1)
    ramp_x = np.clip(np.minimum(np.arange(w), np.arange(w)[::-1]) / (fe * 3), 0, 1)
    m = (ramp_y[:, None] * ramp_x[None, :] * strength)[..., None]
    frame[y0:y1, x0:x1] = (reg * (1 - m) + bl * m).astype(np.uint8)
    return frame
