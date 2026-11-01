"""Shared helpers for the mixcut scripts: tool paths, subprocess, json, media probing."""
import json, os, shutil, subprocess, sys
from pathlib import Path

LOCAL_BIN = Path(os.path.expanduser("~")) / ".local" / "bin"
STUDIO = Path(os.environ.get("STUDIO_ROOT", Path(__file__).resolve().parent.parent))
LIBRARY = STUDIO / "library"
FONT_DIRS = [LIBRARY / "fonts", Path("C:/Windows/Fonts")]

for _s in (sys.stdout, sys.stderr):   # Windows consoles default to gbk; titles carry any script
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def tool(name):
    """Full path of ffmpeg / ffprobe / yt-dlp (PATH first, then ~/.local/bin)."""
    p = shutil.which(name)
    if p:
        return p
    for ext in ("", ".exe"):
        c = LOCAL_BIN / (name + ext)
        if c.exists():
            return str(c)
    sys.exit(f"{name} not found (expected on PATH or in {LOCAL_BIN})")


FFMPEG, FFPROBE = tool("ffmpeg"), tool("ffprobe")


def run(cmd, check=True, capture=True, **kw):
    r = subprocess.run([str(c) for c in cmd], capture_output=capture, text=True, encoding="utf-8", errors="replace", **kw)
    if check and r.returncode != 0:
        raise RuntimeError(f"command failed ({r.returncode}): {' '.join(map(str, cmd))[:300]}\n{(r.stderr or '')[-2000:]}")
    return r


def load_json(p, default=None):
    p = Path(p)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def save_json(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def probe(path):
    """width, height, fps, duration, has_audio for a media file (cached next to it)."""
    path = Path(path)
    r = run([FFPROBE, "-v", "error", "-show_entries",
             "stream=codec_type,width,height,r_frame_rate,avg_frame_rate:format=duration", "-of", "json", path])
    j = json.loads(r.stdout)
    info = {"duration": float(j.get("format", {}).get("duration", 0) or 0), "has_audio": False}
    for s in j.get("streams", []):
        if s.get("codec_type") == "video" and "width" not in info:
            num, den = (s.get("avg_frame_rate") or s.get("r_frame_rate") or "30/1").split("/")
            fps = float(num) / float(den or 1) if float(den or 1) else 30.0
            if fps <= 0 or fps > 240:
                num, den = s.get("r_frame_rate", "30/1").split("/"); fps = float(num) / float(den)
            info.update(width=int(s["width"]), height=int(s["height"]), fps=fps)
        elif s.get("codec_type") == "audio":
            info["has_audio"] = True
    return info


VIDEO_EXT = (".mp4", ".mkv", ".webm", ".mov", ".m4v")
DERIVED = (".proxy.", ".vocals.", ".mix.", "_preview", ".strip_", ".sheet")


def sources(args):
    """Source videos named by args (files or folders); skips files the scripts derive (proxies, previews)."""
    out = []
    for a in args:
        a = Path(a)
        cand = sorted(x for x in a.iterdir() if x.suffix.lower() in VIDEO_EXT) if a.is_dir() else [a]
        out += [str(x) for x in cand if not any(d in x.name for d in DERIVED)]
    return out


def meta_path(src):
    return Path(str(src) + ".meta.json")


def src_meta(src):
    """Probe + letterbox crop, cached in <src>.meta.json (written by shots.py log)."""
    m = load_json(meta_path(src))
    if m and "width" in m:
        return m
    m = probe(src)
    m["crop"] = [0, 0, m.get("width", 0), m.get("height", 0)]
    return m


def hi_section(src, t0, t1):
    """Full-quality section of a proxy covering source seconds [t0, t1] (written by fetch.py conform).
    Returns (file, source time of the file's first frame); (src, 0) when there is none.
    Sections whose frame match failed (err >= 50) are skipped."""
    j = load_json(str(src) + ".hi.json")
    for s in (j or {}).get("sections", []):
        if s["t0"] <= t0 + 1e-3 and t1 <= s["t1"] - 1e-3 and os.path.exists(s["file"]) and s.get("err", 0) < 50:
            return s["file"], float(s["t0"])
    return str(src), 0.0


def find_font(*names):
    """First existing font file among names (searched in library/fonts, then Windows fonts)."""
    for n in names:
        if os.path.isabs(n) and os.path.exists(n):
            return n
        for d in FONT_DIRS:
            p = d / n
            if p.exists():
                return str(p)
    return None


def fmt_t(t):
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:05.2f}" if m else f"{s:.2f}"
