"""Parallel ranged download (for slow links where each connection is throttled).

    python pget.py <url> <out_file> [--conns 12]
    python pget.py --hf Systran/faster-whisper-small <out_dir> [--mirror https://hf-mirror.com]
"""
import argparse, os, sys, threading, time, urllib.request

def head_size(url):
    req = urllib.request.Request(url, method="GET", headers={"Range": "bytes=0-0", "User-Agent": "pget"})
    with urllib.request.urlopen(req, timeout=60) as r:
        cr = r.headers.get("Content-Range")
        if cr and "/" in cr:
            return int(cr.split("/")[-1]), r.geturl()
        return int(r.headers.get("Content-Length", 0)), r.geturl()

def fetch(url, out, conns=12):
    size, final = head_size(url)
    if size <= 0:
        urllib.request.urlretrieve(url, out); return
    if os.path.exists(out) and os.path.getsize(out) == size:
        print(f"skip {out} (complete)"); return
    part = size // conns + 1
    tmp = out + ".part"
    with open(tmp, "wb") as f:
        f.truncate(size)
    done = [0]; lock = threading.Lock()
    def worker(a, b):
        pos = a
        while pos <= b:
            try:
                req = urllib.request.Request(final, headers={"Range": f"bytes={pos}-{b}", "User-Agent": "pget"})
                with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "r+b") as f:
                    f.seek(pos)
                    while True:
                        chunk = r.read(1 << 16)
                        if not chunk: break
                        f.write(chunk); pos += len(chunk)
                        with lock: done[0] += len(chunk)
            except Exception as e:
                time.sleep(2)
    ths = [threading.Thread(target=worker, args=(i * part, min(size - 1, (i + 1) * part - 1)), daemon=True) for i in range(conns)]
    t0 = time.time(); [t.start() for t in ths]
    while any(t.is_alive() for t in ths):
        time.sleep(5)
        el = time.time() - t0
        print(f"{os.path.basename(out)} {done[0]/1e6:.1f}/{size/1e6:.1f} MB {done[0]/max(el,1)/1e3:.0f} KB/s", flush=True)
    os.replace(tmp, out)
    print(f"done {out} {size/1e6:.1f} MB in {time.time()-t0:.0f}s")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("a"); ap.add_argument("b"); ap.add_argument("--conns", type=int, default=12)
    ap.add_argument("--hf", action="store_true"); ap.add_argument("--mirror", default="https://hf-mirror.com")
    a = ap.parse_args()
    if a.hf:
        os.makedirs(a.b, exist_ok=True)
        for fn in ["config.json", "tokenizer.json", "vocabulary.txt", "preprocessor_config.json", "model.bin"]:
            try:
                fetch(f"{a.mirror}/{a.a}/resolve/main/{fn}", os.path.join(a.b, fn), a.conns)
            except Exception as e:
                print("skip", fn, e)
    else:
        fetch(a.a, a.b, a.conns)
