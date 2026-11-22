"""Runs on a rented machine (or any machine): takes pieces from the shared queue, renders each with the job's render
command, encodes it into one near-lossless file and records it. Several workers may share one queue.

  python3 fleet_worker.py <work dir>

Files under <work dir>/.likeness/: job.json (render command, frames, piece size, fps), queue (one piece number a line),
now/<piece> (being rendered), done (finished pieces), failed (pieces that failed twice), log.
Pieces are written to <work dir>/pieces/NNN.mkv (H.264 in RGB, no colour conversion; quality 6 by default, which looks the
same as the frames but is not bit for bit; "crf": 0 in the job makes it exact). Frames go to <work dir>/frames/f_NNNNN.png
(or f_NNNN.png) and are deleted once their piece is encoded."""
import json, os, shutil, subprocess, sys, time

WORK = os.path.abspath(sys.argv[1])
L = os.path.join(WORK, ".likeness")
JOB = json.load(open(os.path.join(L, "job.json"), encoding="utf-8"))
FRAMES, PIECES = os.path.join(WORK, "frames"), os.path.join(WORK, "pieces")
for d in (FRAMES, PIECES, os.path.join(L, "now")):
    os.makedirs(d, exist_ok=True)


def log(*a):
    with open(os.path.join(L, "log"), "a", encoding="utf-8") as f:
        f.write(time.strftime("%H:%M:%S ") + " ".join(map(str, a)) + "\n")


class Lock:
    """A directory as the lock: making one is atomic everywhere."""
    def __enter__(self):
        while True:
            try:
                os.mkdir(os.path.join(L, "lock")); return self
            except FileExistsError:
                time.sleep(0.2)

    def __exit__(self, *a):
        os.rmdir(os.path.join(L, "lock"))


def take():
    with Lock():
        q = os.path.join(L, "queue")
        lines = [l.strip() for l in open(q)] if os.path.exists(q) else []
        lines = [l for l in lines if l]
        if not lines:
            return None
        open(q + ".new", "w").write("".join(l + "\n" for l in lines[1:]))
        os.replace(q + ".new", q)
        k = int(lines[0])
        open(os.path.join(L, "now", str(k)), "w").write(str(os.getpid()))
        return k


def finish(k, ok):
    with Lock():
        try:
            os.remove(os.path.join(L, "now", str(k)))
        except OSError:
            pass
        if ok:
            open(os.path.join(L, "done"), "a").write(f"{k}\n")
            return
        tries = os.path.join(L, f"tries.{k}")
        n = int(open(tries).read()) + 1 if os.path.exists(tries) else 1
        open(tries, "w").write(str(n))
        if n >= 2:
            open(os.path.join(L, "failed"), "a").write(f"{k}\n")
        else:
            open(os.path.join(L, "queue"), "a").write(f"{k}\n")


def frame_file(f):
    for width in (5, 4):
        path = os.path.join(FRAMES, f"f_{f:0{width}d}.png")
        if os.path.exists(path):
            return path
    return None


def piece_range(k):
    first, last = JOB["frames"]
    a = max(first, k * JOB["piece"]); b = min(last, (k + 1) * JOB["piece"] - 1)
    return a, b


while True:
    k = take()
    if k is None:
        break
    a, b = piece_range(k)
    t = time.time()
    cmd = JOB["render"].format(first=a, last=b, frames=FRAMES, work=WORK)
    with open(os.path.join(L, f"render-{k}.log"), "w", encoding="utf-8", errors="replace") as out:
        code = subprocess.run(cmd, shell=True, cwd=WORK, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL).returncode
    text = open(os.path.join(L, f"render-{k}.log"), encoding="utf-8", errors="replace").read()
    missing = [f for f in range(a, b + 1) if not frame_file(f)]
    if code != 0 or missing or "Illegal address" in text:
        log("piece", k, "FAILED", "exit", code, "missing", len(missing), "gpu error" if "Illegal address" in text else "")
        finish(k, False); continue
    out = os.path.join(PIECES, f"{k:03d}.mkv")
    width = len(os.path.basename(frame_file(a))) - len("f_.png")
    enc = subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(JOB["fps"]), "-start_number", str(a), "-i", os.path.join(FRAMES, f"f_%0{width}d.png"),
                          "-frames:v", str(b - a + 1), "-c:v", "libx264rgb", "-preset", "medium", "-crf", str(JOB.get("crf", 6)), "-pix_fmt", "bgr24", out + ".part.mkv"],
                         stdin=subprocess.DEVNULL).returncode
    if enc != 0:
        log("piece", k, "ENCODING FAILED"); finish(k, False); continue
    os.replace(out + ".part.mkv", out)
    for f in range(a, b + 1):
        os.remove(frame_file(f))
    log("piece", k, "done", f"{time.time() - t:.0f}s")
    finish(k, True)
log("queue empty; worker", os.getpid(), "stops")
