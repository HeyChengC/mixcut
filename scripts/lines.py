"""Dialogue lines of a source, with word times, so a line can be lifted onto the timeline.

  lines.py <src.mp4> [...] [--sep] [--lang en] [--force]
      -> <src>.lines.json, <src>.lines.txt (one line per row: "start-end  text")
  --sep   first split the voice from the music with demucs (trailers mix score under dialogue)
          -> <src>.vocals.wav (same timeline as the source). render.py uses it for dialog with "stem": "vocals".
  lines.py find <dir|src ...> "words"      search every logged source for a phrase

Whisper runs on the separated voice when there is one: it hears dialogue better without the score.
"""
import argparse, json, os, subprocess, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
from mc_common import FFMPEG, LIBRARY, save_json, load_json, fmt_t, sources, probe

WHISPER = LIBRARY / "models" / "faster-whisper-small"
SR = 44100


def extract_wav(src, out, sr=SR):
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "2", "-ar", str(sr), "-c:a", "pcm_f32le", str(out)], check=True)


def separate(src):
    """Voice stem via htdemucs (CPU). Cached as <src>.vocals.wav."""
    out = Path(str(src) + ".vocals.wav")
    if out.exists():
        return out
    import soundfile as sf, torch
    from demucs.pretrained import get_model
    from demucs.apply import apply_model
    tmp = Path(str(src) + ".mix.wav")
    extract_wav(src, tmp)
    y, sr = sf.read(str(tmp), always_2d=True, dtype="float32")
    tmp.unlink()
    m = get_model("htdemucs"); m.eval()
    torch.set_num_threads(max(1, (os.cpu_count() or 4) - 2))
    x = torch.tensor(y.T)[None]
    ref, sd = x.mean(), x.std() + 1e-8
    with torch.no_grad():
        s = apply_model(m, (x - ref) / sd, device="cpu", split=True, overlap=0.1, progress=False)[0]
    s = s * sd + ref
    v = s[m.sources.index("vocals")].numpy().T
    sf.write(str(out), v, sr, subtype="FLOAT")
    print("vocals", out)
    return out


_model = None


def transcribe(audio, lang=None):
    """Whisper on a 16 kHz mono array (decoded by ffmpeg: faster-whisper's own PyAV decode breaks on newer av)."""
    global _model
    from faster_whisper import WhisperModel
    if _model is None:
        _model = WhisperModel(str(WHISPER), device="cpu", compute_type="int8", cpu_threads=max(1, (os.cpu_count() or 4) - 2))
    pcm = subprocess.run([FFMPEG, "-v", "error", "-i", str(audio), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True, check=True).stdout
    segs, info = _model.transcribe(np.frombuffer(pcm, np.float32), language=lang, word_timestamps=True, vad_filter=True,
                                   vad_parameters=dict(min_silence_duration_ms=300), beam_size=5, condition_on_previous_text=False)
    out = []
    for s in segs:
        words = [dict(w=w.word.strip(), s=round(w.start, 3), e=round(w.end, 3), p=round(w.probability, 2)) for w in (s.words or [])]
        text = s.text.strip()
        if not text:
            continue
        st = words[0]["s"] if words else s.start
        en = words[-1]["e"] if words else s.end
        out.append(dict(start=round(st, 3), end=round(en, 3), text=text, words=words, p=round(float(np.mean([w["p"] for w in words])) if words else 0, 2)))
    return out, info.language


def voice_level(wav, lines):
    """Mean level (dB) of each line in the voice stem: tells a clean line from one buried under music/effects."""
    import soundfile as sf
    y, sr = sf.read(str(wav), always_2d=True, dtype="float32")
    m = y.mean(1)
    for l in lines:
        seg = m[int(l["start"] * sr):int(l["end"] * sr)]
        l["db"] = round(float(20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-9)), 1) if len(seg) else -99.0


def lines(src, sep=False, lang=None, force=False):
    src = str(src)
    out = src + ".lines.json"
    if os.path.exists(out) and not force:
        print("skip", src, "(already transcribed)")
        return load_json(out)
    audio = separate(src) if sep else None
    if audio is None and os.path.exists(src + ".vocals.wav"):
        audio = Path(src + ".vocals.wav")
    res, language = transcribe(audio or src, lang)
    if audio is not None:
        voice_level(audio, res)
    save_json(out, dict(src=src, language=language, stem=str(audio) if audio else None, lines=res))
    with open(src + ".lines.txt", "w", encoding="utf-8") as f:
        for l in res:
            lv = f" {l['db']:5.1f}dB" if "db" in l else ""
            f.write(f"{l['start']:7.2f}-{l['end']:7.2f}{lv}  {l['text']}\n")
    print(f"{src}: {len(res)} lines ({language})")
    return res


def find(paths, phrase):
    files = []
    for p in paths:
        p = Path(p)
        files += sorted(p.glob("*.lines.json")) if p.is_dir() else [Path(str(p) + ".lines.json")]
    q = phrase.lower()
    for fj in files:
        j = load_json(fj)
        if not j:
            continue
        for l in j["lines"]:
            if q in l["text"].lower():
                print(f"{Path(j['src']).name}  {l['start']:.2f}-{l['end']:.2f}  {l['text']}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "find":
        find(sys.argv[2:-1], sys.argv[-1])
        sys.exit()
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="+"); ap.add_argument("--sep", action="store_true")
    ap.add_argument("--lang"); ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    for s in sources(a.src):
        if not probe(s)["has_audio"]:
            print("skip", s, "(no audio)"); continue
        lines(s, a.sep, a.lang, a.force)
