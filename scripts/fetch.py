"""Find and download footage / music with yt-dlp.

  fetch.py search "Interstellar official trailer" [--n 8]
  fetch.py get <url|id> [<url|id> ...] --dir assets/src [--h 1080] [--subs] [--section 30-95] [--jobs 3]
  fetch.py music "Hans Zimmer No Time for Caution" --dir assets/music     (first search hit, audio only)
  fetch.py music <url> --dir assets/music
  fetch.py conform edl.json [--h 1080] [--pad 1.5]   full-quality sections of every proxy the EDL uses
  get / music / conform also take --cookies-from-browser <chrome|edge|firefox>: lets yt-dlp read that browser's
  YouTube login. Off by default; use it only after the user has said yes (YouTube sometimes answers
  "Sign in to confirm you're not a bot" and then nothing downloads without it).

Proxy first, conform last (an offline/online edit): long sources are fetched small (--h 360) for logging and
choosing; once the EDL is settled, conform pulls only the used seconds at full quality into assets/hi/ and writes
<proxy>.hi.json. render.py then reads those sections (frame-aligned to the proxy by matching thumbnails).

The link here is slow per connection, so HLS formats are preferred and fetched with 8 parallel fragments.
"""
import argparse, json, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
from mc_common import tool, run, load_json, save_json, probe, meta_path, FFMPEG

YTDLP = tool("yt-dlp")
BASE = [YTDLP, "--encoding", "utf-8", "--js-runtimes", "node", "--no-warnings", "--no-playlist", "-N", "8", "--retries", "10",
        "--fragment-retries", "20", "--ffmpeg-location", str(Path(tool("ffmpeg")).parent)]


def as_url(x):
    if re.match(r"^[\w-]{11}$", x):
        return f"https://www.youtube.com/watch?v={x}"
    return x


def search(q, n=8, quiet=False):
    r = run([YTDLP, "--encoding", "utf-8", "--js-runtimes", "node", "--no-warnings", "--flat-playlist", "--print",
             "%(id)s\t%(duration)s\t%(view_count)s\t%(channel)s\t%(title)s", f"ytsearch{n}:{q}"], check=False)
    rows = []
    for line in r.stdout.splitlines():
        p = line.split("\t")
        if len(p) < 5:
            continue
        vid, dur, views, ch, title = p[:5]
        try: dur = float(dur)
        except ValueError: dur = 0
        try: views = int(views)
        except ValueError: views = 0
        rows.append(dict(id=vid, duration=dur, views=views, channel=ch, title=title))
    if not quiet:
        for i, x in enumerate(rows):
            print(f"{i:2d} {x['id']}  {int(x['duration'])//60:2d}:{int(x['duration'])%60:02d}  {x['views']/1e6:7.2f}M  {x['channel'][:28]:28s}  {x['title'][:90]}")
    return rows


def get(url, outdir, h=1080, subs=False, section=None):
    url = as_url(url)
    outdir = Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    fmt = (f"bv*[height<={h}][protocol^=m3u8][vcodec^=avc1]+ba[ext=m4a]/"
           f"bv*[height<={h}][vcodec^=avc1]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]/b")
    cmd = BASE + ["-f", fmt, "--merge-output-format", "mp4", "-o", str(outdir / "%(id)s.%(ext)s"),
                  "--write-info-json", "--print", "after_move:filepath"]
    if subs:
        cmd += ["--write-subs", "--write-auto-subs", "--sub-langs", "en.*,en,zh-Hans,zh-Hant", "--sub-format", "vtt", "--convert-subs", "vtt"]
    if section:
        a, b = section.split("-")
        cmd += ["--download-sections", f"*{a}-{b}", "--force-keyframes-at-cuts"]
    cmd.append(url)
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8", errors="replace")
    paths = [l.strip() for l in r.stdout.splitlines() if l.strip().endswith((".mp4", ".mkv", ".webm"))]
    if r.returncode != 0 or not paths:
        print(f"FAILED {url}\n{r.stderr[-800:]}", file=sys.stderr)
        return None
    p = Path(paths[-1])
    info = outdir / (p.stem + ".info.json")
    if info.exists():   # keep only the useful fields
        j = json.loads(info.read_text(encoding="utf-8"))
        slim = {k: j.get(k) for k in ("id", "title", "channel", "uploader", "duration", "webpage_url", "width", "height", "fps", "upload_date", "view_count")}
        (outdir / (p.name + ".source.json")).write_text(json.dumps(slim, ensure_ascii=False, indent=1), encoding="utf-8")
        info.unlink()
    print(f"OK {p}")
    return str(p)


def music(q, outdir):
    outdir = Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    url = q
    if not re.match(r"^(https?://|[\w-]{11}$)", q):
        hits = search(q, 5, quiet=True)
        if not hits:
            sys.exit("no search results")
        h = hits[0]
        print(f"using: {h['title']}  [{h['channel']}]  {int(h['duration'])}s  ({h['id']})")
        url = h["id"]
    cmd = BASE + ["-f", "ba[ext=m4a]/ba", "-o", str(outdir / "%(title).60B [%(id)s].%(ext)s"), "--print", "after_move:filepath", as_url(url)]
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8", errors="replace")
    paths = [l.strip() for l in r.stdout.splitlines() if l.strip()]
    if r.returncode != 0 or not paths:
        sys.exit(f"FAILED\n{r.stderr[-800:]}")
    print(f"OK {paths[-1]}")
    return paths[-1]


def thumbs(path, t0=None, dur=None, fps=None):
    """16x9 grey thumbnails of every frame (float32, n x 144)."""
    cmd = [FFMPEG, "-v", "error"]
    if t0 is not None:
        cmd += ["-ss", f"{max(0.0, t0):.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    vf = (f"fps={fps}," if fps else "") + "scale=16:9:flags=area,format=gray"
    cmd += ["-i", str(path), "-an", "-vf", vf, "-f", "rawvideo", "-"]
    b = subprocess.run(cmd, capture_output=True).stdout
    a = np.frombuffer(b, np.uint8).astype(np.float32)
    return a[: len(a) // 144 * 144].reshape(-1, 144)


def align(proxy, hi, nominal, fps, search=4.0):
    """Source time of the hi-res section's first frame, found by sliding its thumbnails along the proxy's."""
    h = thumbs(hi, fps=fps)
    if len(h) < 8:
        return nominal, 1e9
    h = h[: int(fps * 12)]
    p0 = max(0.0, nominal - search)
    p = thumbs(proxy, p0, len(h) / fps + 2 * search, fps)
    best, err = 0, 1e18
    hz = h - h.mean(1, keepdims=True)
    for k in range(0, max(1, len(p) - len(h) + 1)):
        seg = p[k:k + len(h)]
        e = float(np.mean(((seg - seg.mean(1, keepdims=True)) - hz) ** 2))
        if e < err:
            best, err = k, e
    return p0 + best / fps, err


def conform(edl_path, h=1080, pad=1.5):
    import render
    edl_path = Path(edl_path).resolve()
    base = edl_path.parent
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    grid, _ = render.music_grid(edl, base)
    need = {}
    def want(src, t_in, dur, speed=1.0):
        p = (base / src).resolve()
        sj = load_json(str(p) + ".source.json")
        if not sj or (probe(p).get("height") or 0) >= h * 0.9:
            return
        # sections are cut at keyframes, which can land seconds after the asked start: ask early
        need.setdefault(str(p), []).append([max(0.0, t_in - pad - 4.0), t_in + dur * speed + pad])
    for c in render.layout_clips(edl, grid):
        if c.get("black") or c.get("color") or "src" not in c:
            continue
        want(c["src"], float(c["in"]), c["t1"] - c["t0"], float(c.get("speed", 1)))
        lay = c.get("layout") or {}
        for it in (lay.get("items") or ([lay["b"]] if lay.get("b") else lay.get("srcs", []))):
            want(it["src"], float(it["in"]), c["t1"] - c["t0"], float(it.get("speed", 1)))
    hidir = base / "assets" / "hi"
    hidir.mkdir(parents=True, exist_ok=True)
    for src, rs in need.items():
        rs.sort()
        merged = [rs[0]]
        for a, b in rs[1:]:
            if a <= merged[-1][1] + 3:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        info = load_json(src + ".source.json")
        old = load_json(src + ".hi.json", {"sections": []})
        have = [s for s in old["sections"] if Path(s["file"]).exists()]
        todo = [r for r in merged if not any(s["t0"] <= r[0] + 4.05 and r[1] <= s["t1"] - 0.05 for s in have)]
        if not todo:
            print(f"{Path(src).name}: all {len(merged)} sections already here"); continue
        fmt = (f"bv*[height<={h}][protocol^=m3u8][vcodec^=avc1]/bv*[height<={h}][vcodec^=avc1]/bv*[height<={h}]")
        cmd = BASE + ["-f", fmt, "-o", str(hidir / f"{info['id']}_%(section_start)s.%(ext)s"), "--print", "after_move:filepath"]
        for a, b in todo:
            cmd += ["--download-sections", f"*{a:.2f}-{b:.2f}"]
        cmd.append(info["webpage_url"])
        print(f"{Path(src).name}: fetching {len(todo)} sections ({sum(b - a for a, b in todo):.0f}s) at {h}p")
        r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8", errors="replace")
        files = [l.strip() for l in r.stdout.splitlines() if l.strip().endswith((".mp4", ".mkv", ".webm"))]
        if r.returncode != 0 or not files:
            print(f"FAILED {src}\n{r.stderr[-800:]}", file=sys.stderr)
            if "Sign in" in (r.stderr or "") or "cookies" in (r.stderr or ""):
                print("  YouTube wants a login. Ask the user before using --cookies-from-browser; until then this source renders from the proxy.", file=sys.stderr)
            continue
        pm = load_json(meta_path(src)) or probe(src)
        for f, (a, b) in zip(sorted(files, key=lambda x: float(re.findall(r"_([\d.]+)\.\w+$", x)[0])), todo):
            fi = probe(f)
            t0, err = align(src, f, a, pm.get("fps", 24))
            k = fi["width"] / pm["width"]
            x, y, w, hh = pm.get("crop") or [0, 0, pm["width"], pm["height"]]
            fi["crop"] = [int(x * k) // 2 * 2, int(y * k) // 2 * 2, int(w * k) // 2 * 2, int(hh * k) // 2 * 2]
            save_json(meta_path(f), fi)
            have.append(dict(file=str(Path(f).resolve()), t0=round(t0, 4), t1=round(t0 + fi["duration"], 4), err=round(err, 1)))
            print(f"  {Path(f).name}: {fi['width']}x{fi['height']}  source {t0:.3f}-{t0 + fi['duration']:.3f} (asked {a:.2f}-{b:.2f}, match err {err:.0f})")
        save_json(src + ".hi.json", dict(sections=have))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("search"); s.add_argument("query"); s.add_argument("--n", type=int, default=8)
    g = sp.add_parser("get"); g.add_argument("urls", nargs="+"); g.add_argument("--dir", default="assets/src")
    g.add_argument("--h", type=int, default=1080); g.add_argument("--subs", action="store_true")
    g.add_argument("--section"); g.add_argument("--jobs", type=int, default=3)
    m = sp.add_parser("music"); m.add_argument("query"); m.add_argument("--dir", default="assets/music")
    c = sp.add_parser("conform"); c.add_argument("edl"); c.add_argument("--h", type=int, default=1080); c.add_argument("--pad", type=float, default=1.5)
    for p in (g, m, c):
        p.add_argument("--cookies-from-browser", metavar="BROWSER", help="only after the user said yes: use this browser's YouTube login")
    a = ap.parse_args()
    if getattr(a, "cookies_from_browser", None):
        BASE += ["--cookies-from-browser", a.cookies_from_browser]
    if a.cmd == "search":
        search(a.query, a.n)
    elif a.cmd == "get":
        with ThreadPoolExecutor(a.jobs) as ex:
            res = list(ex.map(lambda u: get(u, a.dir, a.h, a.subs, a.section), a.urls))
        bad = [u for u, r in zip(a.urls, res) if not r]
        if bad:
            print("failed:", " ".join(bad)); sys.exit(1)
    elif a.cmd == "conform":
        conform(a.edl, a.h, a.pad)
    else:
        music(a.query, a.dir)
