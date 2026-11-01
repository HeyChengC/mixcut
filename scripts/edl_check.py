"""Check an EDL before rendering: does each clip stay inside one shot of its source, and where do lines land?

  edl_check.py edl.json

Per clip: timeline span, beat, source span, and the source shot it sits in (from <src>.shots.json). Flags a clip that
runs over the end of its shot (a flash of the next shot) or starts in the last frames of one. Lines ("say" and
"dialog") are listed with their timeline span so overlaps between lines show up.
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from mc_common import load_json
import render


def main(path):
    path = Path(path).resolve()
    base = path.parent
    edl = json.loads(path.read_text(encoding="utf-8"))
    grid, pieces = render.music_grid(edl, base)
    clips = render.layout_clips(edl, grid)
    shots = {}
    bad = 0
    for i, c in enumerate(clips):
        d = c["t1"] - c["t0"]
        beat = grid.pos(c["t0"]) - grid.i0
        if c.get("black") or c.get("color"):
            print(f"#{i:2d} {c['t0']:6.2f}-{c['t1']:6.2f} b{beat:5.1f}  {'black':28s}")
            continue
        src = str((base / c["src"]).resolve())
        if src not in shots:
            shots[src] = (load_json(src + ".shots.json") or {}).get("shots", [])
        a = float(c["in"]); b = a + d * float(c.get("speed", 1))
        sh = next((s for s in shots[src] if s["start"] - 1e-3 <= a < s["end"]), None)
        flag = ""
        if sh is None:
            flag = "  (no shot log)"
        elif b > sh["end"] + 0.02:
            flag = f"  <-- runs {b - sh['end']:.2f}s into the next shot"; bad += 1
        elif sh["end"] - a < 0.2:
            flag = "  <-- starts at the very end of a shot"; bad += 1
        name = Path(c["src"]).stem[:12]
        span = f"shot {sh['start']:.2f}-{sh['end']:.2f}" if sh else ""
        print(f"#{i:2d} {c['t0']:6.2f}-{c['t1']:6.2f} b{beat:5.1f}  {name:12s} {a:7.2f}-{b:7.2f}  {span:22s} {c.get('note', '')[:34]}{flag}")
    print("lines:")
    for d in sorted(render.lines_from_clips(edl, clips), key=lambda d: grid.resolve(d.get("at", 0))):
        t = grid.resolve(d.get("at", 0))
        print(f"   {t:6.2f}-{t + d['out'] - d['in']:6.2f}  {(d.get('en') or d.get('zh') or '')[:60]}")
    print(f"length {clips[-1]['t1']:.2f}s, {len(clips)} clips, {bad} problem(s); music seams at "
          + ", ".join(f"{p['t0']:.2f}" for p in pieces[1:]))


if __name__ == "__main__":
    main(sys.argv[1])
