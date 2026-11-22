"""Likeness: build real places and things in Blender for the way they will be seen, through to a picture, a film,
a page you scroll through, or a model.

First run: `likeness setup`. Place, plan, walk and film commands are listed below. Every other command (inspect, edit,
view, compare, deliver, orbit, export, estimate, ...) is the modelling toolbox: run `likeness tools --help`."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "film"))
FILM = {"setup", "place", "plan", "fleet", "walk", "bake", "sight", "shoot", "sheet", "pieces", "web", "filmcheck", "movie", "serve", "page", "where", "library"}


def in_blender(blend, timeout=None, **params):
    """Run one action of film/inblender.py on a scene and return what it reports."""
    import studio
    blend = Path(blend).expanduser().resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="likeness-") as folder:
        ask, answer = Path(folder) / "params.json", Path(folder) / "result.json"
        ask.write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8")
        log = Path(folder) / "blender.log"
        with open(log, "w", encoding="utf-8", errors="replace") as out:
            done = subprocess.run([studio.blender_binary(), "--background", str(blend), "--python-exit-code", "1",
                                   "--python", str(SCRIPTS / "film" / "inblender.py"), "--", str(ask), str(answer)],
                                  stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, timeout=timeout)
        result = json.loads(answer.read_text(encoding="utf-8")) if answer.exists() else None
        if result is None or "error" in (result or {}):
            tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-25:])
            raise RuntimeError((result or {}).get("error") or f"Blender stopped without an answer (exit {done.returncode}).\n{tail}")
        if params.get("action") == "shoot":
            result["log_tail"] = [l for l in log.read_text(encoding="utf-8", errors="replace").splitlines() if l.startswith("FRAME")][-3:]
        return result


def new_file(path, suffix):
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() != suffix:
        raise ValueError(f"The output must end in {suffix}: {path}")
    if path.exists():
        raise FileExistsError(f"Choose a new name, the file is kept as it is: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def frame_list(spec, walk=None, count=None):
    """ "0-239", "0-239:4", "12,84,176", "check" (the keys of the walk and half-way between them) or "stops"."""
    if spec in ("check", "stops"):
        if walk is None:
            raise ValueError(f"--frames {spec} needs --walk walk.json.")
        data = json.loads(Path(walk).read_text(encoding="utf-8"))
        return data["check_frames"] if spec == "check" else [s["frame"] for s in data["stops"]]
    frames = []
    for part in spec.split(","):
        rng, _, step = part.partition(":")
        if "-" in rng:
            a, b = rng.split("-")
            frames += list(range(int(a), int(b) + 1, int(step or 1)))
        else:
            frames.append(int(rng))
    return frames


def walk(args):
    import route
    plan = json.loads(Path(args.route).read_text(encoding="utf-8"))
    line = None
    if args.ground:
        if not plan.get("ground"):
            raise ValueError('To lay the line on the ground, name the ground objects in the route: "ground": ["Terrain", "Path"].')
        line = route.Line(plan["line"], bool(plan.get("loop")))
        found = in_blender(args.ground, action="ground", objects=plan["ground"], points=[[p[0], p[1]] for p in line.pts])
        if found["missed"]:
            raise ValueError(f"{found['missed']} of {len(line.pts)} points of the line have none of {plan['ground']} under them.")
        line.pts = [(p[0], p[1], z) for p, z in zip(line.pts, found["z"])]
    made, report, rows, line = route.build(plan, line)
    Path(args.walk).write_text(json.dumps(made, ensure_ascii=False), encoding="utf-8")
    if args.poses:
        trial = route.poses(plan, rows, line)
        Path(args.poses).write_text(json.dumps(trial, ensure_ascii=False), encoding="utf-8")
        report["poses"] = {"file": args.poses, "views": trial["notes"]}
    report["walk"] = str(Path(args.walk).resolve())
    return report


def serve(site, port, seconds=None):
    """A local address for the page: browsers will not play the film from a double-clicked file."""
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class Handler(SimpleHTTPRequestHandler):
        extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".mjs": "text/javascript", ".js": "text/javascript",
                          ".json": "application/json", ".h264": "application/octet-stream", ".html": "text/html; charset=utf-8"}

        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(site), **k)

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(json.dumps({"page": f"http://127.0.0.1:{server.server_address[1]}/", "serving": str(site),
                      "stops": f"after {seconds:g} s" if seconds else "when the process is ended (Ctrl+C, or stop the background task)"}), flush=True)
    if seconds:
        import threading
        threading.Timer(seconds, server.shutdown).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def library_root():
    import home
    return home.library_root()


def library():
    """What is already on this machine: scanned plants and rocks, ground textures, skies. Look here before downloading."""
    root, items = library_root(), []
    for provider in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        for asset in sorted(p for p in provider.iterdir() if p.is_dir()):
            files = [f for f in asset.rglob("*") if f.is_file()]
            kinds = {f.suffix.lower() for f in files}
            kind = "sky" if kinds & {".hdr", ".exr"} else "model" if kinds & {".gltf", ".glb", ".blend", ".fbx", ".obj"} else "textures"
            main = next((f for f in files if f.suffix.lower() in ((".hdr", ".exr") if kind == "sky" else (".gltf", ".glb", ".blend", ".fbx", ".obj"))), None)
            items.append({"id": f"{provider.name}:{asset.name}", "kind": kind, "mb": round(sum(f.stat().st_size for f in files) / 1e6),
                          "path": str(main or asset)})
    return {"library": str(root), "assets": items,
            "add": "build scripts download a missing Poly Haven asset by itself: outdoor.library(\"polyhaven:<id>\"); "
                   "find ids with `python <skill>/scripts/sources.py search polyhaven <words>`"}


def where():
    import home
    try:
        blender = home.blender()
    except ValueError as error:
        blender = str(error)
    try:
        ffmpeg = home.tool("ffmpeg")
    except FileNotFoundError as error:
        ffmpeg = str(error)
    return {"skill": str(SCRIPTS.parent), "home": str(home.home()), "blender": blender, "ffmpeg": ffmpeg,
            "python": sys.executable, "library": str(library_root()),
            "settings": str(home.LOCAL) + (" (present)" if home.LOCAL.is_file() else " (none: defaults)"),
            "work_folder": "put scenes, frames and previews in <your project>/tmp/, never in the skill folder"}


def main():
    tools = len(sys.argv) > 1 and sys.argv[1] == "tools"
    if tools:
        sys.argv.pop(1)
    for name, module in (("setup", "setup"), ("place", "place"), ("plan", "plan")):   # first run, a real place, the plan
        if len(sys.argv) > 1 and sys.argv[1] == name:
            return __import__(module).main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "fleet":  # several cloud GPUs rendering one film: see film/fleet.py
        import fleet
        return fleet.main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "page":   # look at a page in headless Chrome: see web/page.mjs
        sys.exit(subprocess.run(["node", str(SCRIPTS / "web" / "page.mjs"), *sys.argv[2:]]).returncode)
    if tools or len(sys.argv) < 2 or sys.argv[1] not in FILM | {"-h", "--help"}:
        import studio
        studio.main()
        return
    parser = argparse.ArgumentParser(prog="likeness", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="First run: what is installed, what is missing; --get fetches Blender, FFmpeg and the starter assets.")
    sub.add_parser("place", help="A real place: elevation and OpenStreetMap outlines round a point, and a map to plan on. See place.py.")
    sub.add_parser("plan", help="Draw route.json from above (over a place map) to agree the route and stops before building. See plan.py.")
    c = sub.add_parser("where", help="Where the skill, its home folder, Blender and FFmpeg are on this machine.")
    c = sub.add_parser("library", help="Scanned plants, rocks, ground textures and skies already on this machine.")
    c = sub.add_parser("walk", help="Route file -> the camera of every frame (walk.json).")
    c.add_argument("route"); c.add_argument("walk")
    c.add_argument("--ground", metavar="SCENE.blend", help="lay the line on the route's ground objects in this scene")
    c.add_argument("--poses", metavar="POSES.json", help="also write trial views round every stop, to choose the stops from")
    c = sub.add_parser("bake", help="Put a walk on the scene's camera, one key a frame, and save a new scene file.")
    c.add_argument("scene"); c.add_argument("walk"); c.add_argument("output")
    c = sub.add_parser("sight", help="At every stop: is what it must show inside the picture and unhidden? Along the walk: eye height, obstacles.")
    c.add_argument("scene"); c.add_argument("walk")
    c = sub.add_parser("shoot", help="Render frames of a baked walk into a folder. Frames already there are skipped.")
    c.add_argument("scene"); c.add_argument("folder")
    c.add_argument("--frames", required=True, help='"0-239", "0-239:4", "12,84", "check" or "stops" (the last two need --walk)')
    c.add_argument("--walk"); c.add_argument("--width", type=int); c.add_argument("--samples", type=int)
    c.add_argument("--jpg", action="store_true", help="drafts as JPEG; leave out for frames a film is made from")
    c.add_argument("--again", action="store_true", help="render the named frames even if the folder already has them")
    c = sub.add_parser("sheet", help="Lay frames out on one picture.")
    c.add_argument("output"); c.add_argument("sources", nargs="+", help="folders of frames, or pictures")
    c.add_argument("--cols", type=int, default=4); c.add_argument("--width", type=int, default=640)
    c.add_argument("--every", type=int, default=1); c.add_argument("--walk"); c.add_argument("--fps", type=float)
    c.add_argument("--frames", help='only these frames of a folder: "check", "stops" (both need --walk) or "0,48,96"')
    c = sub.add_parser("pieces", help="Frames -> the master: one near-lossless piece a second.")
    c.add_argument("frames"); c.add_argument("film"); c.add_argument("--fps", type=int, required=True)
    c = sub.add_parser("web", help="Master -> a folder a page can walk through: sizes, film.json and the player.")
    c.add_argument("film"); c.add_argument("site"); c.add_argument("walk")
    c.add_argument("--scene", help="the baked scene, to find where the route's clickable spots stand")
    c.add_argument("--heights", help="sizes to make, e.g. 1080,720 (default: every usual size up to the master's)")
    c.add_argument("--extra", help="a JSON file whose fields are added to film.json (weather along the walk, music, ...)")
    c = sub.add_parser("filmcheck", help="Look through a finished film for jumps; save overview sheets.")
    c.add_argument("film"); c.add_argument("prefix"); c.add_argument("--fps", type=int)
    c.add_argument("--loop", action="store_true", default=None)
    c = sub.add_parser("movie", help="Master -> one ordinary video file.")
    c.add_argument("film"); c.add_argument("output"); c.add_argument("--fps", type=int, required=True); c.add_argument("--height", type=int)
    sub.add_parser("fleet", help="Several rented GPUs render one film: fleet up|go|watch|stat|down|frames <job dir>. See film/fleet.py.")
    sub.add_parser("page", help='Look at a page in headless Chrome: page shot <url> <out.png> | page record <url> <out.mp4>. See web/page.mjs.')
    c = sub.add_parser("serve", help="Open a finished site folder at a local address.")
    c.add_argument("site"); c.add_argument("--port", type=int, default=8765)
    c.add_argument("--for", dest="seconds", type=float, metavar="SECONDS", help="stop by itself after this long (for a check from a script)")
    args = parser.parse_args()

    import pack
    if args.command == "where":
        result = where()
    elif args.command == "library":
        result = library()
    elif args.command == "walk":
        result = walk(args)
    elif args.command == "bake":
        output = new_file(args.output, ".blend")
        result = in_blender(args.scene, action="bake", walk=str(Path(args.walk).resolve(strict=True)), output=str(output))
    elif args.command == "sight":
        result = in_blender(args.scene, action="sight", walk=str(Path(args.walk).resolve(strict=True)))
    elif args.command == "shoot":
        frames = frame_list(args.frames, args.walk)
        result = in_blender(args.scene, action="shoot", frames=frames, output=str(Path(args.folder).expanduser().resolve()),
                            width=args.width, samples=args.samples, ext="jpg" if args.jpg else "png", again=args.again)
    elif args.command == "sheet":
        only = set(frame_list(args.frames, args.walk)) if args.frames else None
        result = pack.sheet(args.sources, str(new_file(args.output, ".jpg")), args.cols, args.width, args.fps, args.walk, args.every, only)
    elif args.command == "pieces":
        result = pack.pieces(args.frames, args.film, args.fps)
    elif args.command == "web":
        spots_file = None
        with tempfile.TemporaryDirectory(prefix="likeness-") as folder:
            data = json.loads(Path(args.walk).read_text(encoding="utf-8"))
            if data.get("spots"):
                if not args.scene:
                    raise ValueError("The route has clickable spots: pass --scene so their corners can be read from the scene.")
                spots_file = os.path.join(folder, "spots.json")
                found = in_blender(args.scene, action="spots", walk=str(Path(args.walk).resolve()))
                Path(spots_file).write_text(json.dumps(found), encoding="utf-8")
            heights = [int(h) for h in args.heights.split(",")] if args.heights else None
            result = pack.web(args.film, args.site, args.walk, spots_file, heights, args.extra)
    elif args.command == "filmcheck":
        result = pack.check(args.film, args.prefix, args.fps, args.loop)
    elif args.command == "movie":
        result = pack.movie(args.film, str(new_file(args.output, ".mp4")), args.fps, args.height)
    elif args.command == "serve":
        serve(Path(args.site).resolve(strict=True), args.port, args.seconds)
        return
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    try:
        main()
    except (ValueError, FileExistsError, FileNotFoundError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
