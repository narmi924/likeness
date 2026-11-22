"""From rendered frames to a film a web page can walk through. Plain Python with FFmpeg; no Blender needed.

    pieces   frames -> one near-lossless piece a second (the master: every later version is made from it, never re-rendered)
    web      master -> the sizes a page fetches, and film.json (camera of every frame, stops, clickable things, frame sizes)
    check    looks through the finished film for jumps and at the join of a loop; saves overview sheets
    sheet    lays frames out on one picture, for looking at many at once
    movie    master -> one ordinary video file, for any player"""
import json
import math
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import home  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
COLOUR = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]
# height: bit-rate cap in Mbit/s, H.264 level, the codec string a page needs. The cap keeps a second full of leaves
# within what a visitor's connection carries.
RUNGS = {2160: (45, "5.2", "avc1.640034"), 1440: (24, "5.1", "avc1.640033"), 1080: (14, "4.2", "avc1.64002A"),
         720: (7, "3.2", "avc1.640020"), 540: (4, "3.1", "avc1.64001F")}


def run(args):
    done = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode:
        raise RuntimeError(f"{args[0]} failed: {done.stderr.strip()[-600:]}")
    return done.stdout


def probe(path):
    out = run([home.tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", path])
    w, h = out.strip().split(",")[:2]
    return int(w), int(h)


def group_of(fps):
    """Frames between two complete pictures: a fifth of a second, so any frame is quick to reach in both directions."""
    for g in range(max(1, fps // 5), 0, -1):
        if fps % g == 0:
            return g
    return 1


def frame_files(folder):
    found = {}
    for name in os.listdir(folder):
        stem, ext = os.path.splitext(name)
        if stem.startswith("f_") and stem[2:].isdigit() and ext.lower() in (".png", ".jpg"):
            found[int(stem[2:])] = name
    return found


# ---------------------------------------------------------------- pieces
def pieces(frames, film, fps, workers=2):
    fps = int(fps)
    found = frame_files(frames)
    if not found:
        raise ValueError(f"No frames named f_00000.png in {frames}.")
    ext = os.path.splitext(next(iter(found.values())))[1]
    width = len(os.path.splitext(found[min(found)])[0]) - 2
    keep = os.path.join(film, "keep")
    os.makedirs(keep, exist_ok=True)
    count = (max(found) + 1) // fps
    whole = [k for k in range(count) if all(f in found for f in range(k * fps, (k + 1) * fps))]

    def encode(k):
        out = os.path.join(keep, f"{k:03d}.h264")
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return 0
        run([home.tool("ffmpeg"), "-y", "-v", "error", "-framerate", str(fps), "-start_number", str(k * fps), "-i", os.path.join(frames, f"f_%0{width}d{ext}"),
             "-frames:v", str(fps), "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2:out_color_matrix=bt709:out_range=tv,format=yuv420p",
             "-c:v", "libx264", "-preset", "medium", "-profile:v", "high", "-crf", "12", "-bf", "0", "-g", str(fps), "-keyint_min", str(fps),
             "-sc_threshold", "0", "-x264-params", "aud=1:repeat-headers=1", *COLOUR, "-f", "h264", out + ".part"])
        os.replace(out + ".part", out)
        return 1

    with ThreadPoolExecutor(workers) as pool:
        made = sum(pool.map(encode, whole))
    missing = [k for k in range(count) if k not in whole]
    size = sum(os.path.getsize(os.path.join(keep, f"{k:03d}.h264")) for k in whole)
    return {"film": film, "pieces": len(whole), "encoded_now": made, "seconds_missing_frames": missing[:20],
            "left_over_frames": (max(found) + 1) % fps, "master_mb": round(size / 1e6, 1)}


# ---------------------------------------------------------------- web
def frame_sizes(data):
    """Byte length of every picture in a piece. Each picture starts with an access unit delimiter (NAL type 9)."""
    starts, i = [], 0
    while True:
        i = data.find(b"\x00\x00\x01\x09", i)
        if i < 0:
            break
        starts.append(i - 1 if i > 0 and data[i - 1] == 0 else i)
        i += 4
    return [b - a for a, b in zip(starts, starts[1:] + [len(data)])]


def web(film, site, walk_file, spots_file=None, heights=None, extra_file=None, workers=3):
    walk = json.load(open(walk_file, encoding="utf-8"))
    fps, count = int(round(walk["fps"])), len(walk["frames"])
    keep = os.path.join(film, "keep")
    names = sorted(f for f in os.listdir(keep) if f.endswith(".h264"))
    if len(names) * fps != count:
        raise ValueError(f"The walk has {count} frames ({count / fps:g} s) but the master has {len(names)} one-second pieces. "
                         f"Render the missing frames and run pieces again.")
    src_w, src_h = probe(os.path.join(keep, names[0]))
    wanted = heights or [h for h in RUNGS if h <= src_h] or [src_h]
    if not heights and src_h not in wanted and src_h > wanted[0]:
        wanted.insert(0, src_h)
    gop = group_of(fps)
    out = os.path.join(site, "film")
    rungs = []
    for h in wanted:
        w = int(round(src_w * h / src_h / 2)) * 2
        cap, level, codec = RUNGS[min((k for k in RUNGS if k >= h), default=2160)]
        rungs.append((w, h, cap, level, codec))

    def encode(job):
        name, (w, h, cap, level, codec) = job
        folder = os.path.join(out, str(h))
        target = os.path.join(folder, name)
        if os.path.exists(target) and os.path.getsize(target) > 0:
            return 0
        os.makedirs(folder, exist_ok=True)
        run([home.tool("ffmpeg"), "-y", "-v", "error", "-r", str(fps), "-i", os.path.join(keep, name),
             "-vf", f"scale={w}:{h}:flags=lanczos:out_color_matrix=bt709:out_range=tv,format=yuv420p",
             "-c:v", "libx264", "-preset", "slow", "-profile:v", "high", "-crf", "26", "-maxrate", f"{cap}M", "-bufsize", f"{cap}M", "-level", level,
             "-bf", "0", "-refs", "2", "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0", "-x264-params", "aud=1:repeat-headers=1",
             *COLOUR, "-f", "h264", target + ".part"])
        os.replace(target + ".part", target)
        return 1

    with ThreadPoolExecutor(workers) as pool:
        made = sum(pool.map(encode, [(n, r) for r in rungs for n in names]))

    video, summary = [], []
    for w, h, cap, level, codec in rungs:
        sizes, total, peak = [], 0, 0
        for name in names:
            data = open(os.path.join(out, str(h), name), "rb").read()
            s = frame_sizes(data)
            if len(s) != fps or sum(s) != len(data):
                raise RuntimeError(f"{h}/{name}: found {len(s)} pictures where {fps} were expected.")
            sizes += s
            total += len(data)
            peak = max(peak, len(data))
        video.append({"width": w, "height": h, "codec": codec, "path": str(h), "gop": gop, "segment": fps,
                      "rate": round(total * 8 / (count / fps)), "peak": peak * 8, "sizes": sizes})
        summary.append({"height": h, "mb": round(total / 1e6, 1), "mbit_s": round(total * 8 / (count / fps) / 1e6, 1)})

    spots = json.load(open(spots_file, encoding="utf-8"))["spots"] if spots_file else []
    data = {"fps": fps, "sensor": walk["sensor"], "count": count, "loop": bool(walk.get("loop")), "aspect": round(src_w / src_h, 5),
            "frames": [[round(f["pos"][0], 3), round(f["pos"][1], 3), round(f["pos"][2], 3), round(f["yaw"], 3), round(f["pitch"], 3),
                        f["lens"], f.get("shift", 0.0)] for f in walk["frames"]],
            "stops": walk["stops"], "spots": spots, "video": video}
    if extra_file:   # whatever else the page needs beside the film: weather along the walk, which music plays where
        data.update(json.load(open(extra_file, encoding="utf-8")))
    with open(os.path.join(out, "film.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    player = os.path.join(os.path.dirname(HERE), "web")
    copied = []
    for name in ("index.html", "film.mjs", "walk.mjs"):
        if not os.path.exists(os.path.join(site, name)):
            shutil.copyfile(os.path.join(player, name), os.path.join(site, name))
            copied.append(name)
    return {"site": site, "sizes": summary, "encoded_now": made, "film_json_kb": os.path.getsize(os.path.join(out, "film.json")) // 1024,
            "spots": len(spots), "player_files_added": copied, "next": f"likeness serve {site}"}


# ---------------------------------------------------------------- check
def check(film, prefix, fps=None, loop=None):
    import numpy as np
    from PIL import Image, ImageDraw
    data_file = next((p for p in (os.path.join(film, "film", "film.json"), os.path.join(film, "film.json")) if os.path.exists(p)), None)
    folder = os.path.join(film, "keep") if os.path.isdir(os.path.join(film, "keep")) else film
    if data_file:   # a site folder: read the smallest size, which is the quickest to decode
        data = json.load(open(data_file, encoding="utf-8"))
        fps = fps or data["fps"]
        loop = data["loop"] if loop is None else loop
        folder = os.path.join(os.path.dirname(data_file), min(data["video"], key=lambda v: v["height"])["path"])
    if not fps:
        raise ValueError("Give --fps: there is no film.json beside these pieces to read it from.")
    fps = int(fps)
    names = sorted(f for f in os.listdir(folder) if f.endswith(".h264"))
    if not names:
        raise ValueError(f"No film pieces in {folder}. Give the folder made by `pieces` or the site folder made by `web`.")
    listing = os.path.join(folder, "list.txt")
    open(listing, "w", newline="\n").write("".join(f"file '{f}'\n" for f in names))
    w, h = probe(os.path.join(folder, names[0]))
    W = 320
    H = int(round(W * h / w / 2)) * 2
    p = subprocess.Popen([home.tool("ffmpeg"), "-v", "error", "-r", str(fps), "-f", "concat", "-safe", "0", "-i", listing, "-vf", f"scale={W}:{H}",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE, bufsize=W * H * 3)
    prev = first = None
    diffs, keep, i = [], [], 0
    while True:
        buf = p.stdout.read(W * H * 3)
        if len(buf) < W * H * 3:
            break
        a = np.frombuffer(buf, np.uint8).astype(np.int16).reshape(H, W, 3)
        if first is None:
            first = a
        if prev is not None:
            diffs.append(float(np.abs(a - prev).mean()))
        if i % (2 * fps) == fps:
            keep.append((i, a.astype(np.uint8)))
        prev = a
        i += 1
    p.wait()
    os.remove(listing)
    d = np.array(diffs)
    jumps = []   # a jump stands out from its neighbours: compare each change with the median of those round it
    for k in range(len(d)):
        near = np.median(d[max(0, k - 8):k + 9])
        if d[k] > 3.0 * near + 2.0:
            jumps.append({"frame": k + 1, "change": round(float(d[k]), 2), "usual": round(float(near), 2)})
    sheets, cols = [], 6
    for part in range(math.ceil(len(keep) / 30)):
        chunk = keep[part * 30:(part + 1) * 30]
        sheet = Image.new("RGB", (cols * W, math.ceil(len(chunk) / cols) * H))
        draw = ImageDraw.Draw(sheet)
        for n, (frame, a) in enumerate(chunk):
            x, y = (n % cols) * W, (n // cols) * H
            sheet.paste(Image.fromarray(a), (x, y))
            draw.rectangle((x, y, x + 40, y + 13), fill=(0, 0, 0))
            draw.text((x + 3, y + 1), f"{frame / fps:.0f}s", fill=(255, 255, 255))
        sheets.append(f"{prefix}-{part + 1}.jpg")
        sheet.save(sheets[-1], quality=88)
    result = {"frames": i, "pieces": len(names), "from": folder, "mean_change": round(float(d.mean()), 2),
              "largest_change": round(float(d.max()), 2), "at_frame": int(d.argmax()) + 1, "jumps": jumps[:30], "sheets": sheets}
    if loop:
        seam = float(np.abs(first - prev).mean())
        result["last_to_first"] = round(seam, 2)
        if seam > 3.0 * float(np.median(d)) + 2.0:
            result["jumps"].append({"frame": 0, "change": round(seam, 2), "usual": round(float(np.median(d)), 2), "where": "the join of the loop"})
    return result


# ---------------------------------------------------------------- sheet
def sheet(sources, output, cols=4, width=640, fps=None, walk_file=None, every=1, only=None):
    from PIL import Image, ImageDraw, ImageFont
    files = []
    for src in sources:
        if os.path.isdir(src):
            found = frame_files(src)
            files += [(i, os.path.join(src, found[i])) for i in sorted(found) if only is None or i in only]
        else:
            stem = os.path.splitext(os.path.basename(src))[0]
            files.append((int(stem[2:]) if stem.startswith("f_") and stem[2:].isdigit() else None, src))
    files = files[::max(1, every)]
    if not files:
        raise ValueError("No pictures to lay out.")
    stops = {}
    if walk_file:
        walk = json.load(open(walk_file, encoding="utf-8"))
        fps = fps or walk["fps"]
        stops = {s["frame"]: s["id"] for s in walk["stops"]}
    try:
        font = ImageFont.truetype("msyh.ttc" if os.name == "nt" else "DejaVuSans.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    first = Image.open(files[0][1])
    height = int(round(width * first.height / first.width))
    rows = math.ceil(len(files) / cols)
    out = Image.new("RGB", (cols * width, rows * height), (20, 20, 20))
    draw = ImageDraw.Draw(out)
    for n, (frame, path) in enumerate(files):
        x, y = (n % cols) * width, (n // cols) * height
        out.paste(Image.open(path).convert("RGB").resize((width, height), Image.LANCZOS), (x, y))
        if frame is None:
            label = os.path.splitext(os.path.basename(path))[0]
        else:
            label = f"{frame}" + (f"  {frame / fps:.1f}s" if fps else "") + (f"  {stops[frame]}" if frame in stops else "")
        box = draw.textbbox((0, 0), label, font=font)
        draw.rectangle((x, y, x + box[2] + 12, y + box[3] + 8), fill=(0, 0, 0))
        draw.text((x + 6, y + 2), label, fill=(255, 255, 255), font=font)
    out.save(output, quality=90)
    return {"sheet": output, "pictures": len(files), "size": list(out.size)}


# ---------------------------------------------------------------- movie
def movie(film, output, fps, height=None):
    keep = os.path.join(film, "keep")
    names = sorted(f for f in os.listdir(keep) if f.endswith(".h264"))
    if not names:
        raise ValueError(f"No master pieces in {keep}.")
    listing = os.path.join(keep, "list.txt")
    open(listing, "w", newline="\n").write("".join(f"file '{f}'\n" for f in names))
    try:
        scale = [f"scale=-2:{int(height)}:flags=lanczos"] if height else []
        run([home.tool("ffmpeg"), "-y", "-v", "error", "-r", str(int(fps)), "-f", "concat", "-safe", "0", "-i", listing, "-vf", ",".join(scale + ["format=yuv420p"]),
             "-c:v", "libx264", "-preset", "medium", "-crf", "18", *COLOUR, "-movflags", "+faststart", output])
    finally:
        os.remove(listing)
    return {"movie": output, "seconds": len(names), "mb": round(os.path.getsize(output) / 1e6, 1)}
