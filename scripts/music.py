"""Music map: tempo, a steady beat grid, downbeats, sections, drops, accents -> <audio>.beats.json + <audio>.beats.png

  music.py analyze <audio> [--no-plot]
  music.py window <audio> --len 30 [--drop 0.35] [--n 4]       suggest windows (start on a downbeat, drop inside)
  music.py seams <audio> --out 40-44 --in 18-34 [--n 8]        score jumps from a bar line in one stretch to a bar line in another

The edit is laid on this grid: render.py turns "beats": 2 into seconds with these beat times, and "music.segments"
strings stretches of the song together at bar lines (pick the bar lines with `seams`).

Beats are tracked on a fine hop (2.9 ms) and then put on a steady grid: a local linear fit of beat time on beat
index. The tracker alone jitters by a frame and hops to catch up when its tempo is a little off; a cut laid on that
jitter lands early or late. analyze prints how far the grid sits from the music's accents, section by section.
"""
import argparse, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
from mc_common import save_json, load_json, fmt_t

SR, HOP, FINE = 22050, 256, 64


def decode(path, sr=SR):
    """mono float32 at sr via ffmpeg (librosa/soundfile can't open m4a/aac)."""
    import subprocess
    from mc_common import FFMPEG
    b = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True, check=True).stdout
    return np.frombuffer(b, np.float32).copy()


def fill_gaps(beats):
    """insert beats where the tracker dropped some (a gap of n periods gets n-1 evenly spaced beats)"""
    if len(beats) < 8:
        return np.asarray(beats, float)
    per = float(np.median(np.diff(beats)))
    out = [beats[0]]
    for b in beats[1:]:
        gap = b - out[-1]
        n = int(round(gap / per))
        if n > 1:
            out += [out[-1] + gap * k / n for k in range(1, n)]
        out.append(b)
    return np.array(out)


def accents(env, t, rel=1.2):
    """note onsets: local peaks of an onset envelope (60 ms apart at least) that stand out from the surrounding
    second, as [(time, contrast)]. contrast >= 2.5 are the hits worth cutting on."""
    from scipy.ndimage import maximum_filter1d, uniform_filter1d
    dt = t[1] - t[0]
    peak = env == maximum_filter1d(env, max(3, int(0.06 / dt) | 1))
    local = uniform_filter1d(env, max(3, int(1.0 / dt))) + 1e-9
    c = env / local
    i = np.nonzero(peak & (c >= rel))[0]
    return [(float(t[k]), float(c[k])) for k in i]


def steady_grid(raw, env, t, sigma=8.0, search=0.035):
    """Tracker beats -> a steady grid. Each beat is pulled to the onset peak near it (weighted by how clearly that
    peak stands out), then a Gaussian-weighted local linear fit of time on beat index (sigma beats) keeps slow tempo
    changes and drops the frame jitter and catch-up hops. Returns (grid, residual of the raw beats in seconds)."""
    from scipy.ndimage import uniform_filter1d
    n = len(raw)
    if n < 6:
        return np.asarray(raw, float), np.zeros(n)
    dt = t[1] - t[0]
    local = uniform_filter1d(env, max(3, int(1.0 / dt))) + 1e-9
    tt, w = np.array(raw, float), np.full(n, 0.15)
    for i, b in enumerate(raw):
        i0, i1 = np.searchsorted(t, [b - search, b + search])
        if i1 - i0 < 3:
            continue
        k = i0 + int(np.argmax(env[i0:i1]))
        clar = env[k] / local[k]
        if clar < 1.3:
            continue
        d = 0.0
        if 0 < k < len(env) - 1:   # parabolic peak
            a, c0, c1 = env[k - 1], env[k], env[k + 1]
            den = a - 2 * c0 + c1
            d = 0.5 * (a - c1) / den if abs(den) > 1e-9 else 0.0
        tt[i], w[i] = t[k] + float(np.clip(d, -1, 1)) * dt, min(3.0, clar)
    idx = np.arange(n, dtype=float)
    out = np.empty(n)
    r = int(3 * sigma)
    for i in range(n):
        lo, hi = max(0, i - r), min(n, i + r + 1)
        x = idx[lo:hi] - i
        sw = np.sqrt(w[lo:hi] * np.exp(-0.5 * (x / sigma) ** 2))
        X = np.vstack([np.ones_like(x), x]).T * sw[:, None]
        out[i] = np.linalg.lstsq(X, tt[lo:hi] * sw, rcond=None)[0][0]
    return out, np.asarray(raw, float) - out


def extend(beats, dur, k=16):
    """continue the grid to both ends of the song with the local period at each end (not the song's median)"""
    if len(beats) < 4:
        return beats
    p0 = np.polyfit(np.arange(min(k, len(beats))), beats[:k], 1)[0]
    p1 = np.polyfit(np.arange(min(k, len(beats))), beats[-k:], 1)[0]
    pre = np.arange(beats[0] - p0, -1e-6, -p0)[::-1]
    post = np.arange(beats[-1] + p1, dur, p1)
    return np.concatenate([pre, beats, post])


def offsets(beats, acc, win=0.07):
    """signed distance (s) from each beat to the nearest accent within win, NaN when there is none"""
    at = np.array([a for a, _ in acc]) if acc else np.zeros(0)
    out = np.full(len(beats), np.nan)
    if not len(at):
        return out
    j = np.clip(np.searchsorted(at, beats), 1, len(at) - 1)
    near = np.where(np.abs(at[j - 1] - beats) < np.abs(at[j] - beats), at[j - 1], at[j])
    d = near - beats
    out[np.abs(d) <= win] = d[np.abs(d) <= win]
    return out


def beat_features(y, sr, beats):
    """beat-synchronous chroma (12 x n) and timbre (MFCC 1-12, 12 x n) on the coarse hop; column i = beat i to i+1"""
    import librosa
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    ch = librosa.feature.chroma_stft(S=S ** 2, sr=sr)
    mf = librosa.feature.mfcc(S=librosa.power_to_db(librosa.feature.melspectrogram(S=S ** 2, sr=sr)), n_mfcc=13)[1:]
    bidx = np.clip(librosa.time_to_frames(beats, sr=sr, hop_length=HOP), 0, S.shape[1] - 1)
    syn = lambda F: librosa.util.sync(F, bidx, aggregate=np.mean)[:, 1:len(beats)]
    return S, syn(ch), syn(mf), bidx


def analyze(path, plot=True):
    import librosa
    y, sr = decode(path), SR
    dur = len(y) / sr
    # beats. A plain beat_track on the coarse hop makes every interval a whole number of 11.6 ms frames and
    # quantises its tempo prior too (126 or 129 bpm for a 128 bpm cue), so it drifts and hops to catch up. So:
    # track once on the coarse hop for the tempo (the slope over 16 beats is exact even when single beats jitter),
    # track again with that tempo on the same envelope resampled to a 2.9 ms hop, then smooth (steady_grid).
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    oenv = librosa.onset.onset_strength(S=librosa.amplitude_to_db(S, ref=np.max), sr=sr, hop_length=HOP)
    t_frames = librosa.frames_to_time(np.arange(len(oenv)), sr=sr, hop_length=HOP)
    _, bc = librosa.beat.beat_track(onset_envelope=oenv, sr=sr, hop_length=HOP, tightness=120, trim=False)
    cb = t_frames[bc]
    per0 = float(np.median([(cb[i + 16] - cb[i]) / 16 for i in range(len(cb) - 16)])) if len(cb) > 20 else 0.5
    fenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=FINE)
    ft = librosa.frames_to_time(np.arange(len(fenv)), sr=sr, hop_length=FINE)
    _, bf = librosa.beat.beat_track(onset_envelope=np.interp(ft, t_frames, oenv), sr=sr, hop_length=FINE,
                                    bpm=60 / per0, tightness=800, trim=False)
    raw = fill_gaps(ft[bf])
    acc = accents(fenv, ft)
    grid, resid = steady_grid(raw, fenv, ft) if len(raw) > 8 else (raw, np.zeros(len(raw)))
    n_pre = 0
    if len(grid) > 8:
        full = extend(grid, dur)
        n_pre = int(np.searchsorted(full, grid[0] - 1e-6))
        grid = full
    beats = grid
    per = float(np.median(np.diff(beats))) if len(beats) > 1 else 0.5
    tempo = 60.0 / per
    _, chs, mfs, bidx = beat_features(y, sr, beats)
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    lowenv = np.maximum(0, np.diff(np.log1p(S[freqs < 150].sum(0)), prepend=0))

    def at(env, t, w=0.05):
        i0, i1 = np.searchsorted(t_frames, [t - w, t + w])
        return float(env[i0:max(i0 + 1, i1)].max()) if i1 > i0 else 0.0
    # downbeats: the phase (of 4) with the strongest bass/onset accents, and the one where the harmony changes most
    acc_b = np.array([at(lowenv, b) * 2 + at(oenv, b) for b in beats])
    chg = np.zeros(len(beats))
    if chs.shape[1] > 2:
        c = chs / (np.linalg.norm(chs, axis=0, keepdims=True) + 1e-9)
        chg[1:chs.shape[1]] = 1 - (c[:, 1:] * c[:, :-1]).sum(0)    # change from the beat before into beat i
    sc_acc = np.array([acc_b[p::4].mean() if len(acc_b[p::4]) else 0 for p in range(4)])
    sc_chg = np.array([chg[p::4].mean() if len(chg[p::4]) else 0 for p in range(4)])
    z = lambda v: (v - v.mean()) / (v.std() + 1e-9)
    phase = int(np.argmax(z(sc_acc) + z(sc_chg)))
    downbeats = beats[phase::4]
    # energy per beat (dB) and sections from novelty on beat-synced features
    rms = librosa.feature.rms(S=S, frame_length=2048, hop_length=HOP)[0]
    seg = np.split(rms, bidx[1:]) if len(bidx) > 1 else [rms]
    e_beat = np.array([20 * np.log10(np.mean(s) + 1e-6) if len(s) else -60 for s in seg])
    mf = librosa.feature.mfcc(S=librosa.power_to_db(librosa.feature.melspectrogram(S=S ** 2, sr=sr)), n_mfcc=13)
    ch = librosa.feature.chroma_stft(S=S ** 2, sr=sr)
    feat = np.vstack([librosa.util.normalize(mf, axis=1), ch])
    fb = librosa.util.sync(feat, bidx, aggregate=np.mean)
    sections = []
    try:
        k = 16
        X = (fb - fb.mean(1, keepdims=True)) / (fb.std(1, keepdims=True) + 1e-6)
        R = np.nan_to_num(np.corrcoef(X.T))
        n = R.shape[0]
        kern = np.kron(np.array([[1, -1], [-1, 1]]), np.ones((k // 2, k // 2)))
        nov = np.zeros(n)
        for i in range(k // 2, n - k // 2):
            nov[i] = (R[i - k // 2:i + k // 2, i - k // 2:i + k // 2] * kern).sum()
        nov = np.maximum(0, nov)
        from scipy.signal import find_peaks
        pk, _ = find_peaks(nov, distance=8, prominence=np.percentile(nov, 75) if nov.any() else 0)
        db_set = set(np.searchsorted(beats, downbeats))
        bounds = [0]
        for p in pk:   # snap to nearest downbeat
            cand = min(db_set, key=lambda d: abs(d - p)) if db_set else p
            if cand - bounds[-1] >= 8:
                bounds.append(int(cand))
        bounds.append(len(beats) - 1)
        for a, b in zip(bounds[:-1], bounds[1:]):
            sections.append(dict(start=round(float(beats[a]), 3), end=round(float(beats[b]), 3), b0=int(a), b1=int(b),
                                 energy=round(float(np.mean(e_beat[a:b])), 1)))
    except Exception as ex:
        print("sections failed:", ex)
    if sections:
        es = np.array([s["energy"] for s in sections])
        lo, hi = np.percentile(es, 33), np.percentile(es, 66)
        for s in sections:
            s["level"] = "high" if s["energy"] >= hi else ("low" if s["energy"] <= lo else "mid")
    # how well the grid sits on the music: share of beats with a note onset within 25 ms, against the same share
    # half a beat later (what an off-beat grid would score), and how far the raw tracker strayed from the grid
    off = offsets(beats, acc)
    off_half = offsets(beats + per / 2, acc)
    res_full = np.full(len(beats), np.nan)
    res_full[n_pre:n_pre + len(resid)] = resid
    near = lambda o: float(np.mean(np.abs(np.nan_to_num(o, nan=1.0)) <= 0.025)) if len(o) else 0.0
    for s in sections:
        o = off[s["b0"]:s["b1"]]
        hit = o[~np.isnan(o)]
        rr = res_full[s["b0"]:s["b1"]]
        rr = rr[~np.isnan(rr)]
        s["on_beat"] = round(near(o), 2)
        s["half_beat"] = round(near(off_half[s["b0"]:s["b1"]]), 2)
        s["accent_ms"] = round(float(np.median(hit) * 1000), 1) if len(hit) else None
        s["tracker_ms"] = round(float(np.median(np.abs(rr)) * 1000), 1) if len(rr) else None
    # drops: beats where the 8-beat mean energy after exceeds the 8 before by > 4 dB, on a downbeat
    drops = []
    dbi = set(range(phase, len(beats), 4))
    for i in range(8, len(beats) - 8):
        if i in dbi:
            jump = e_beat[i:i + 8].mean() - e_beat[i - 8:i].mean()
            if jump > 4:
                drops.append((round(float(beats[i]), 3), round(float(jump), 1), i))
    drops.sort(key=lambda d: -d[1])
    kept = []
    for d in drops:
        if all(abs(d[2] - k[2]) >= 16 for k in kept):
            kept.append(d)
    res = dict(file=str(path), duration=round(dur, 3), tempo=round(tempo, 2), beat=round(60 / tempo, 4),
               beats=[round(float(b), 4) for b in beats], downbeat_phase=phase,
               downbeat_scores=dict(accent=[round(float(v), 3) for v in sc_acc], harmony=[round(float(v), 3) for v in sc_chg]),
               downbeats=[round(float(b), 4) for b in downbeats],
               energy_db=[round(float(e), 1) for e in e_beat], sections=sections,
               drops=[dict(t=d[0], jump_db=d[1], beat=d[2]) for d in sorted(kept[:6])],
               beat_offset_ms=[None if np.isnan(o) else round(float(o) * 1000, 1) for o in off],
               accents=[[round(t, 3), round(c, 2)] for t, c in acc],
               onsets=[round(t, 3) for t, c in acc if c >= 2.5])
    out = str(path) + ".beats.json"
    save_json(out, res)
    hit = off[~np.isnan(off)]
    print(f"{Path(path).name}: {dur:.1f}s  tempo {tempo:.2f} bpm (beat {60/tempo:.4f}s)  {len(beats)} beats  downbeat phase {phase}"
          f"  (accent says {int(np.argmax(sc_acc))}, harmony says {int(np.argmax(sc_chg))})")
    print(f"  grid vs note onsets: {near(off) * 100:.0f}% of beats have one within 25 ms (half a beat later: {near(off_half) * 100:.0f}%),"
          f" median offset {np.median(hit) * 1000 if len(hit) else 0:+.1f} ms. {len(acc)} onsets, {len(res['onsets'])} strong.")
    for s in sections:
        print(f"  section {fmt_t(s['start']):>7} - {fmt_t(s['end']):>7}  beats {s['b0']:3d}-{s['b1']:3d}  {s['energy']:6.1f} dB  {s.get('level',''):4s}"
              f"  on-beat {s['on_beat']:.2f} (half {s['half_beat']:.2f})  offset {s['accent_ms'] if s['accent_ms'] is not None else '-':>6} ms"
              f"  tracker strays {s['tracker_ms'] if s['tracker_ms'] is not None else '-'} ms")
    for d in res["drops"]:
        print(f"  drop at {fmt_t(d['t'])} (beat {d['beat']}, +{d['jump_db']} dB)")
    if plot:
        draw(res, y, sr, str(path) + ".beats.png")
    return res


def draw(res, y, sr, out):
    from PIL import Image, ImageDraw, ImageFont
    from mc_common import find_font
    W, H = 2400, 420
    dur = res["duration"]
    im = Image.new("RGB", (W, H), (18, 18, 22)); d = ImageDraw.Draw(im)
    f = ImageFont.truetype(find_font("consola.ttf", "arial.ttf"), 14)
    X = lambda t: int(t / dur * (W - 20)) + 10
    n = W - 20
    env = np.array([np.abs(y[int(i * len(y) / n):int((i + 1) * len(y) / n)]).max(initial=0) for i in range(n)])
    env = env / (env.max() + 1e-9)
    for i, v in enumerate(env):
        d.line([(10 + i, 200 - v * 120), (10 + i, 200 + v * 120)], fill=(70, 110, 160))
    cols = {"high": (220, 80, 60), "mid": (200, 160, 60), "low": (80, 140, 90)}
    for s in res["sections"]:
        d.rectangle([X(s["start"]), 40, X(s["end"]), 60], fill=cols.get(s.get("level"), (120, 120, 120)))
        d.text((X(s["start"]) + 3, 42), f"{s['energy']:.0f}", fill=(0, 0, 0), font=f)
        d.line([(X(s["start"]), 40), (X(s["start"]), 340)], fill=(255, 255, 255), width=1)
    for b in res["downbeats"]:
        d.line([(X(b), 330), (X(b), 345)], fill=(255, 220, 0))
    for dr in res["drops"]:
        d.line([(X(dr["t"]), 30), (X(dr["t"]), 360)], fill=(255, 60, 200), width=3)
        d.text((X(dr["t"]) + 4, 345), f"DROP {fmt_t(dr['t'])}", fill=(255, 60, 200), font=f)
    eb = np.array(res["energy_db"]); bt = res["beats"]
    if len(eb) > 1:
        lo_, hi_ = np.percentile(eb, 5), np.percentile(eb, 98)
        e = np.clip((eb - lo_) / (hi_ - lo_ + 1e-6), 0, 1)
        pts = [(X(t), 320 - v * 100) for t, v in zip(bt, e)]
        d.line(pts, fill=(255, 255, 255), width=2)
    for t in range(0, int(dur) + 1, 10):
        d.line([(X(t), 380), (X(t), 390)], fill=(200, 200, 200))
        d.text((X(t) + 2, 392), fmt_t(t), fill=(200, 200, 200), font=f)
    d.text((10, 8), f"{Path(res['file']).name}  {res['tempo']} bpm  beat {res['beat']}s", fill=(255, 255, 255), font=f)
    im.save(out); print("plot", out)


def window(path, length, drop_pos=0.35, n=4):
    m = load_json(str(path) + ".beats.json") or analyze(path, plot=False)
    beats, dbs = np.array(m["beats"]), np.array(m["downbeats"])
    e = np.array(m["energy_db"])
    out = []
    for s in dbs:
        if s + length > m["duration"]:
            break
        i0, i1 = np.searchsorted(beats, [s, s + length])
        if i1 - i0 < 8:
            continue
        seg = e[i0:i1]
        score = seg.mean() * 0.2
        for dr in m["drops"]:
            if s < dr["t"] < s + length:
                pos = (dr["t"] - s) / length
                score += dr["jump_db"] * (1 - abs(pos - drop_pos) * 2)
        end_db = min(dbs, key=lambda x: abs(x - (s + length)))
        out.append((score, s, end_db, int(i0)))
    out.sort(key=lambda o: -o[0])
    picked = []
    for o in out:
        if all(abs(o[1] - p[1]) > length / 2 for p in picked):
            picked.append(o)
        if len(picked) >= n:
            break
    for sc, s, e_, i0 in picked:
        print(f"start {s:7.3f}s ({fmt_t(s)}) beat {i0}  end~{e_:.3f}s  len {e_ - s:.2f}s  score {sc:.1f}")
    return picked


def seams(path, out_rng, in_rng, n=8, bars=1):
    """Jumps from bar line A (the music leaves here) to bar line B (it comes back in here). For each pair:
    harm  = chroma correlation of the bar we land on (from B) with the bar that would have followed A: high = the
            harmony carries on, the jump is felt only as a change of texture or level
    ctx   = chroma correlation of the bar before A with the bar before B: high = B is where the music already was
    tex   = timbre (MFCC) correlation of those two landing bars: low = an audible change of texture, a "contrast cut"
    dE    = level of B's bar minus the level of the bar after A (dB)
    phr   = bar number within an 8-bar phrase at A and at B (equal = the jump keeps the phrase position)"""
    m = load_json(str(path) + ".beats.json") or analyze(path, plot=False)
    beats, dbs = np.array(m["beats"]), np.array(m["downbeats"])
    y = decode(path)
    S, chs, mfs, _ = beat_features(y, SR, beats)
    e = np.array(m["energy_db"])
    bi = {round(float(b), 4): int(i) for i, b in enumerate(beats)}
    first = int(np.searchsorted(beats, dbs[0] - 1e-6))
    L = 4 * bars

    def col(F, i, k):   # mean feature of beats i .. i+k-1
        i0, i1 = max(0, i), min(F.shape[1], i + k)
        return F[:, i0:i1].mean(1) if i1 > i0 else np.zeros(F.shape[0])
    corr = lambda a, b: float(np.corrcoef(a, b)[0, 1]) if a.std() > 1e-9 and b.std() > 1e-9 else 0.0
    A = [d for d in dbs if out_rng[0] - 1e-3 <= d <= out_rng[1] + 1e-3]
    B = [d for d in dbs if in_rng[0] - 1e-3 <= d <= in_rng[1] + 1e-3]
    rows = []
    for a in A:
        ia = bi[round(float(a), 4)]
        for b in B:
            ib = bi[round(float(b), 4)]
            if ia == ib:
                continue
            harm = corr(col(chs, ia, L), col(chs, ib, L))
            ctx = corr(col(chs, ia - L, L), col(chs, ib - L, L))
            tex = corr(col(mfs, ia, L), col(mfs, ib, L))
            dE = float(e[ib:ib + L].mean() - e[ia:ia + L].mean())
            pa, pb = ((ia - first) // 4) % 8, ((ib - first) // 4) % 8
            score = harm + 0.5 * ctx + (0.15 if pa == pb else 0) + (0.1 if pa % 4 == pb % 4 else 0)
            rows.append((score, a, b, ia, ib, harm, ctx, tex, dE, pa, pb))
    rows.sort(key=lambda r: -r[0])
    print(f"{len(A)} bar lines out x {len(B)} in; best {min(n, len(rows))} (bars compared: {bars}):")
    print("   out (s)   in (s)    beat  ->  beat   harm   ctx   tex     dE   phr  score")
    for r in rows[:n]:
        sc, a, b, ia, ib, harm, ctx, tex, dE, pa, pb = r
        print(f"  {a:8.3f} {b:8.3f}   {ia:4d} -> {ib:4d}   {harm:+.2f} {ctx:+.2f} {tex:+.2f} {dE:+6.1f}  {pa}/{pb}  {sc:5.2f}")
    return rows[:n]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    a1 = sp.add_parser("analyze"); a1.add_argument("audio"); a1.add_argument("--no-plot", action="store_true")
    a2 = sp.add_parser("window"); a2.add_argument("audio"); a2.add_argument("--len", type=float, default=30)
    a2.add_argument("--drop", type=float, default=0.35); a2.add_argument("--n", type=int, default=4)
    a3 = sp.add_parser("seams"); a3.add_argument("audio"); a3.add_argument("--out", required=True, help="t0-t1: where the music may leave")
    a3.add_argument("--in", dest="inn", required=True, help="t0-t1: where it may come back in"); a3.add_argument("--n", type=int, default=8)
    a3.add_argument("--bars", type=int, default=1)
    a = ap.parse_args()
    if a.cmd == "analyze":
        analyze(a.audio, not a.no_plot)
    elif a.cmd == "window":
        window(a.audio, a.len, a.drop, a.n)
    else:
        rng = lambda s: tuple(float(x) for x in s.split("-"))
        seams(a.audio, rng(a.out), rng(a.inn), a.n, a.bars)
