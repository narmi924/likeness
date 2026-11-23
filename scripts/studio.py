"""Create, inspect and revise local Blender files from the command line."""

import argparse
import base64
import html
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid
from urllib.parse import quote


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import home  # noqa: E402
WORKER = SCRIPTS / "worker.py"
PROJECT = home.home()


def blender_binary():
    return home.blender()


def live_code(code):
    """Execute through the local Blender Lab add-on's NUL-terminated JSON protocol."""
    request = {"type": "execute", "code": code, "strict_json": True}
    port = int(os.environ.get("BLENDER_MCP_PORT", "9876"))
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.settimeout(300)
        sock.sendall(json.dumps(request).encode("utf-8") + b"\0")
        with sock.makefile("rb") as stream:
            # The peer delimits a complete reply with NUL, without requiring EOF.
            reply = bytearray()
            while b"\0" not in reply:
                chunk = stream.read1(65536)
                if not chunk:
                    raise RuntimeError("Blender Lab closed the connection before replying.")
                reply.extend(chunk)
    data = json.loads(reply.partition(b"\0")[0])
    if data.get("status") != "ok":
        raise RuntimeError(data.get("message") or data.get("stderr") or "Blender Lab rejected the operation.")
    return data["result"]


def snapshot_live(destination):
    return live_code(
        "import bpy\n"
        "result = {'file': bpy.data.filepath, 'scene': bpy.context.scene.name}\n"
        f"bpy.ops.wm.save_as_mainfile(filepath={str(destination)!r}, copy=True, relative_remap=True)"
    )


def render_live(params, frozen=None):
    return live_code(
        "import bpy, runpy\n"
        f"result = runpy.run_path({str(WORKER)!r})['live_view']({params!r}, {str(frozen) if frozen else None!r})"
    )


SESSIONS = PROJECT / ".runtime" / "session"
READ_ONLY = {"inspect", "check", "view", "compare", "uv", "locate", "locate_many", "landmark_world", "landmark_error"}


def session_file(path):
    import hashlib
    key = hashlib.sha1(str(Path(path).resolve()).casefold().encode("utf-8")).hexdigest()[:10]
    return SESSIONS / f"{Path(path).stem}-{key}.json"


def session_port(path):
    """The port of a running session for this file, or None."""
    marker = session_file(path)
    if not marker.is_file():
        return None
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
        with socket.create_connection(("127.0.0.1", record["port"]), timeout=1):
            return record["port"]
    except (OSError, ValueError, KeyError):
        marker.unlink(missing_ok=True)
        return None


def session_request(port, params, timeout=None):
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.settimeout(timeout or 900)
        sock.sendall(json.dumps(params, ensure_ascii=True).encode("utf-8") + b"\0")
        reply = bytearray()
        with sock.makefile("rb") as stream:
            while b"\0" not in reply:
                chunk = stream.read1(65536)
                if not chunk:
                    raise RuntimeError("The session closed before replying.")
                reply.extend(chunk)
    value = json.loads(reply.partition(b"\0")[0])
    if "error" in value:
        raise ValueError(value["error"])
    return value


def session(command, path=None, idle=1800):
    """Keep one Blender open on a file so views and checks skip the start-up and shader compilation."""
    if command == "status":
        records = []
        for marker in SESSIONS.glob("*.json") if SESSIONS.is_dir() else []:
            record = json.loads(marker.read_text(encoding="utf-8"))
            record["online"] = session_port(record["file"]) is not None
            records.append(record)
        return {"sessions": records}
    path = Path(path).expanduser().resolve(strict=True)
    if command == "stop":
        port = session_port(path)
        if port is None:
            return {"file": str(path), "stopped": False}
        return {"file": str(path), **session_request(port, {"action": "stop"})}
    if session_port(path):
        return {"file": str(path), "running": True}
    SESSIONS.mkdir(parents=True, exist_ok=True)
    marker = session_file(path)
    marker.unlink(missing_ok=True)
    log = marker.with_suffix(".log")
    params = {"action": "serve", "new": False, "source": str(path), "port_file": str(marker), "idle": idle}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as result:
        result_path = result.name
    process = subprocess.Popen([blender_binary(), "--background", "--factory-startup", "--disable-autoexec", str(path),
                                "--python-exit-code", "1", "--python", str(WORKER), "--", result_path,
                                json.dumps(params, ensure_ascii=True)],
                               stdin=subprocess.DEVNULL, stdout=open(log, "w", encoding="utf-8"), stderr=subprocess.STDOUT,
                               creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0))
    import time
    for _ in range(300):
        if marker.is_file():
            break
        if process.poll() is not None:
            raise RuntimeError("The session Blender exited: " + Path(log).read_text(encoding="utf-8")[-2000:])
        time.sleep(0.1)
    return {"file": str(path), "pid": process.pid, "port": session_port(path), "idle_seconds": idle}


def worker(source, action, *, timeout=None, **params):
    """Use a separate Blender process for every operation on a saved project; a running session answers read-only ones."""
    if action in READ_ONLY and source not in {"live", "new"} and not params.get("pick_image"):
        port = session_port(source)
        if port:
            return session_request(port, dict(params, action=action, new=False, source=str(Path(source).resolve())), timeout)
    with tempfile.TemporaryDirectory(prefix="likeness-") as directory:
        scratch = Path(directory)
        origin = str(source)
        if source == "live":
            saved = scratch / "working.blend"
            live = snapshot_live(saved)
            source = saved
            origin = "live: " + (live["file"] or "(unsaved)")
        args = [blender_binary(), "--background", "--factory-startup", "--disable-autoexec"]
        if source != "new":
            model = Path(source).expanduser().resolve(strict=True)
            if model.suffix.lower() != ".blend":
                raise ValueError("Expected a .blend source.")
            args.append(str(model))
        params.update(action=action, new=(source == "new"), source=origin)
        result = scratch / "result.json"
        args.extend(["--python-exit-code", "1", "--python", str(WORKER), "--",
                     str(result), json.dumps(params, ensure_ascii=True)])
        try:
            proc = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Blender exceeded the interactive time limit. Use a clay view or a smaller subject.") from exc
        if proc.returncode or not result.exists():
            raise RuntimeError(proc.stdout.strip()[-6000:] or "Blender exited without a result.")
        value = json.loads(result.read_text(encoding="utf-8"))
        if "error" in value:
            raise ValueError(value["error"])
        return value


def publish_new(staged, output):
    """Publish a finished file only if another writer has not already claimed the name."""
    if os.name == "nt":
        staged.rename(output)
    else:
        os.link(staged, output)


def write_html(output, page):
    staged = output.with_name(f".{output.stem}-{uuid.uuid4().hex}.html")
    try:
        staged.write_text(page, encoding="utf-8")
        publish_new(staged, output)
    finally:
        staged.unlink(missing_ok=True)


def retire(file):
    """Move an existing deliverable or project file into the project's history folder."""
    file = Path(file)
    history = file.parent / "history"
    history.mkdir(exist_ok=True)
    taken = {p.name for p in history.iterdir()}
    index = 1
    while f"{file.stem}-{index:02}{file.suffix}" in taken:
        index += 1
    target = history / f"{file.stem}-{index:02}{file.suffix}"
    file.rename(target)
    return str(target)


def script_label(script):
    script = Path(script).resolve()
    try:
        return script.relative_to(PROJECT).as_posix()
    except ValueError:
        return str(script)


def edit(source, script, output, pack=False, promote=False):
    output = Path(output).expanduser().resolve()
    if output.suffix.lower() != ".blend":
        raise ValueError("The working copy must have a .blend extension.")
    if output.exists() and not promote:
        raise FileExistsError(f"Choose a new output name, or promote into the project file: {output}")
    script = Path(script).expanduser().resolve(strict=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    # An adjacent temporary file preserves Blender's relative asset paths.
    staged = output.with_name(f".{output.stem}-{uuid.uuid4().hex}.blend")
    try:
        info = worker(source, "edit", script=str(script), output=str(staged), pack=pack,
                      script_label=script_label(script), final=str(output))
        retired = retire(output) if output.exists() else None
        publish_new(staged, output)
        return {**info, "file": str(output), **({"previous": retired} if retired else {})}
    finally:
        staged.unlink(missing_ok=True)


ANGLES = ("camera", "three-quarter", "front", "right", "back", "left", "top")
STYLES = ("clay", "render", "wire", "normals")

def output_job(source, action, output, suffix, *, timeout=None, execute=None, **params):
    output = Path(output).expanduser().resolve()
    if output.suffix.lower() != suffix or output.exists():
        raise ValueError(f"Choose a new {suffix} output file: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staged = output.with_name(f".{output.stem}-{uuid.uuid4().hex}{suffix}")
    try:
        if execute:
            result = execute(dict(params, output=str(staged)))
        else:
            result = worker(source, action, timeout=timeout, output=str(staged), **params)
        publish_new(staged, output)
        return result
    finally:
        staged.unlink(missing_ok=True)


QUALITIES = ("draft", "final")
VIEW_ENGINES = ("scene", "eevee", "cycles")


def preview(source, output, angle="three-quarter", subject=None, style="clay",
            size=900, frame=None, pick_image=None, timeout=None, frozen=None,
            quality="draft", engine="scene", camera=None, projection=False):
    if source == "new":
        raise ValueError("Preview an existing .blend or live session.")
    if angle not in (*ANGLES, "sheet") or style not in STYLES or not 64 <= size <= 4096:
        raise ValueError("Use a supported view and a size between 64 and 4096 pixels.")
    if quality not in QUALITIES or engine not in VIEW_ENGINES:
        raise ValueError("Choose draft or final quality and a scene, eevee or cycles engine.")
    if angle == "sheet" and pick_image:
        raise ValueError("Choose a single angle for point selection, or save the sheet as a PNG.")
    output = Path(output).expanduser().resolve()
    if output.suffix.lower() != ".png":
        raise ValueError("Preview output must be a PNG file.")
    if output.exists():
        raise FileExistsError(f"Choose a new preview path: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    execute = (lambda params: render_live(dict(params, source="live"), frozen)) if source == "live" else None
    info = output_job(source, "view", output, ".png", execute=execute, timeout=timeout, angle=angle, subject=subject,
                      style=style, size=size, frame=frame, quality=quality, engine=engine, camera=camera,
                      projection=projection, pick_image=str(Path(pick_image).resolve()) if pick_image else None)
    info["image"] = str(output)
    return info


def capture(source, angle=None, subject=None, style="clay", size=900, frame=None, timeout=None, frozen=None, camera=None):
    if angle is None:
        angle = "camera" if camera else "three-quarter"
    if camera and angle != "camera":
        raise ValueError("A named camera requires the camera angle.")
    if source != "live":
        source = str(Path(source).expanduser().resolve(strict=True))
    with tempfile.TemporaryDirectory(prefix="blender-view-") as directory:
        png = Path(directory) / "view.png"
        ids = Path(directory) / "objects.png"
        info = preview(source, png, angle, subject, style, size, frame, ids, timeout, frozen, camera=camera)
        del info["image"]
        info["input"] = dict(source=source, angle=angle, subject=subject, style=style, size=size, frame=frame)
        if camera:
            info["input"]["camera"] = camera
        info["pickMap"] = base64.b64encode(ids.read_bytes()).decode("ascii")
        return base64.b64encode(png.read_bytes()).decode("ascii"), info


def local_panel(source, output, angle, subject, style, size, frame, camera=None):
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Choose a new preview path: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    live_file = None
    if source == "live":
        frozen = output.with_suffix(".view.blend")
        if frozen.exists():
            raise FileExistsError(f"Choose a new preview name: {frozen}")
        staged = frozen.with_name(f".{frozen.stem}-{uuid.uuid4().hex}.blend")
        try:
            image, info = capture("live", angle, subject, style, size, frame, frozen=staged, camera=camera)
            publish_new(staged, frozen)
            live_file = frozen
        finally:
            staged.unlink(missing_ok=True)
        source = str(frozen)
    try:
        source = str(Path(source).expanduser().resolve(strict=True))
        revision = file_revision(source)
        if live_file:
            info["input"]["source"] = source
        else:
            image, info = capture(source, angle, subject, style, size, frame, camera=camera)
        if file_revision(source) != revision:
            raise ValueError("The model changed during capture. Refresh the view.")
        info["revision"] = revision
        # Scene and material names are data, even when embedded inside a local HTML file.
        payload = json.dumps({"image": image, "view": info}, ensure_ascii=True).replace("<", "\\u003c")
        template = (SCRIPTS / "panel.html").read_text(encoding="utf-8")
        write_html(output, template.replace("<!-- local capture -->",
                   '<script type="application/json" id="capture">' + payload + '</script>'))
        return {"panel": str(output), "source": info["source"], "frame": info["frame"]}
    except BaseException:
        if live_file:
            live_file.unlink(missing_ok=True)
        raise


def inspect(source, name):
    if source == "live":
        return live_code(
            "import runpy\n"
            f"result = runpy.run_path({str(WORKER)!r})['inspect_scene']({name!r})"
        )
    return worker(source, "inspect", name=name)


def file_revision(path):
    stat = Path(path).stat()
    return (str(stat.st_mtime_ns), stat.st_size)


def locate(source, selection, *, pick_layer=None):
    if not selection.get("point"):
        raise ValueError("Click a surface in the picture to get a point.")
    return worker(source, "locate", timeout=180, scene=selection["scene"], frame=selection["frame"],
                  object=selection["object"], projection=selection["projection"], point=selection["point"],
                  pick_layer=pick_layer)


def turntable(args):
    output = Path(args.output).expanduser().resolve()
    if output.suffix.lower() != ".mp4" or output.exists():
        raise ValueError("Choose a new .mp4 file for the turntable.")
    if min(args.width,args.height) < 64 or args.width % 2 or args.height % 2:
        raise ValueError("Movie dimensions must be even and at least 64 pixels.")
    if args.samples < 1 or (args.frames is not None and args.frames < 2) or (args.fps is not None and args.fps <= 0):
        raise ValueError("Use positive samples and fps, and at least two frames.")
    encoder = home.tool("ffmpeg")
    output.parent.mkdir(parents=True, exist_ok=True)
    staged = output.with_name("." + output.stem + "-" + uuid.uuid4().hex + ".mp4")
    try:
        with tempfile.TemporaryDirectory(prefix="blender-orbit-") as frames:
            result = worker(args.source, "orbit", directory=frames, frame=args.frame, frames=args.frames,
                            fps=args.fps, size=[args.width,args.height], camera=args.camera, subject=args.subject,
                            style=args.style, samples=args.samples, engine=args.engine, quality=args.quality)
            if len(list(Path(frames).glob("frame-*.png"))) != result["frames"]:
                raise RuntimeError("Blender did not render the complete orbit.")
            subprocess.run([encoder, "-hide_banner", "-loglevel", "error", "-framerate", str(result["fps"]),
                            "-start_number", str(result["start"]), "-i", str(Path(frames)/"frame-%04d.png"),
                            "-frames:v", str(result["frames"]), "-c:v", "libx264", "-crf", "17", "-pix_fmt", "yuv420p",
                            "-g", "12", "-keyint_min", "12", "-sc_threshold", "0", "-movflags", "+faststart", str(staged)],
                           stdin=subprocess.DEVNULL, check=True)
        publish_new(staged, output)
    finally:
        staged.unlink(missing_ok=True)
    return {"movie": str(output), **result}


def player(movie, output, poster=None, model=None):
    output = Path(output).expanduser().resolve()
    movie = Path(movie).expanduser().resolve(strict=True)
    if output.suffix.lower() != ".html" or movie.suffix.lower() != ".mp4":
        raise ValueError("Supply an MP4 and a new HTML file beside it.")

    def media_url(path):
        file = Path(path).expanduser().resolve(strict=True)
        if file.parent != output.parent:
            raise ValueError("Keep the movie, poster, Blender file and player in the same folder.")
        return html.escape(quote(file.name), quote=True)

    page = (SCRIPTS / "spin.html").read_text(encoding="utf-8")
    title = html.escape(Path(model or movie).stem)
    page = page.replace("{{title}}",title).replace("{{movie}}",media_url(movie))
    page = page.replace("{{poster}}", f'poster="{media_url(poster)}"' if poster else "")
    page = page.replace("{{model}}", f'<a href="{media_url(model)}" download>Blender file</a>' if model else "")
    write_html(output, page)
    return {"player": str(output)}


def export_model(args):
    out = Path(args.output).expanduser().resolve()
    if out.exists():
        raise FileExistsError(f"Choose a new export name: {out}")
    if out.suffix.lower() not in {".glb",".fbx",".usdc",".stl"} or args.scale <= 0:
        raise ValueError("Choose a new GLB, FBX, USDC or STL file and a positive scale.")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent, prefix=".export-") as directory:
        staged = Path(directory)/out.name
        result = worker(args.source,"export",output=str(staged),subject=args.subject,
                        scale=args.scale,apply_modifiers=args.apply_modifiers)
        assets = staged.with_name(staged.stem+"-textures")
        owned = None
        try:
            if assets.is_dir():
                target = out.with_name(assets.name)
                target.mkdir()
                owned = target
                shutil.copytree(assets, target, dirs_exist_ok=True)
                result["textures"] = str(target)
            publish_new(staged,out)
        except BaseException:
            if owned is not None:
                shutil.rmtree(owned)
            raise
    return {"file":str(out), **result}


def project_file(path):
    """The project's canonical file: projects/<work>/<work>.blend, or None for any other file."""
    file = Path(path).expanduser().resolve()
    if file.suffix.lower() == ".blend" and file.parent.name == file.stem:
        return file
    if file.is_dir() and (file / f"{file.name}.blend").is_file():
        return file / f"{file.name}.blend"
    return None


def deliverable_name(camera):
    """'01 Portrait' → 'Portrait.png'; a turntable camera becomes '360.mp4'."""
    import re
    if re.search(r"turntable|360", camera, re.IGNORECASE):
        return "360.mp4"
    label = re.sub(r"^\d+\s*[^\w(]*\s*", "", camera).strip()
    label = re.sub(r'[<>:"/\\|?*]+', "-", label) or camera
    return label + ".png"


def deliverables(project, cameras):
    """Standard outputs of a project file: one PNG per numbered camera and one turntable movie."""
    items = []
    stale_from = project.stat().st_mtime_ns
    for cam in cameras:
        if not cam["name"][:1].isdigit():
            continue
        name = deliverable_name(cam["name"])
        target = project.parent / name
        state = "missing" if not target.exists() else ("stale" if target.stat().st_mtime_ns < stale_from else "current")
        items.append({"camera": cam["name"], "file": str(target), "state": state})
    return items


def deliver(args):
    project = project_file(args.project)
    if project is None:
        raise ValueError("Name a project folder or its projects/<work>/<work>.blend file.")
    scene = worker(project, "inspect", name=None)
    items = deliverables(project, scene["cameras"])
    if not items:
        raise ValueError("No numbered cameras to deliver from. Name cameras like '01 Portrait'.")
    if args.check:
        return {"project": str(project), "deliverables": items}
    for item in items:
        if item["state"] == "current" and not args.all:
            continue
        target = Path(item["file"])
        if target.exists():
            item["previous"] = retire(target)
        if target.suffix == ".mp4":
            # A turntable camera that does not move is only a framing hint; orbit the scene instead.
            camera = next(c for c in scene["cameras"] if c["name"] == item["camera"])
            spec = argparse.Namespace(source=str(project), output=str(target),
                                      camera=item["camera"] if camera["animated"] else None, subject=None,
                                      frame=None, frames=args.frames, fps=24, width=args.width, height=args.height,
                                      samples=24 if args.quality == "draft" else 64,
                                      engine="eevee" if args.quality == "draft" else "scene",
                                      style="render", quality=args.quality)
            turntable(spec)
        else:
            preview(project, target, angle="camera", style="render", size=args.size, quality=args.quality,
                    camera=item["camera"])
        item["state"] = "delivered"
    return {"project": str(project), "deliverables": items}


def next_build_script(output, label):
    """build/NN-label.py beside a project file, or NN-label.py beside any other output."""
    import re
    output = Path(output).expanduser().resolve()
    folder = output.parent / "build" if project_file(output) else output.parent
    folder.mkdir(parents=True, exist_ok=True)
    numbers = [int(m.group(1)) for p in folder.glob("*.py") if (m := re.match(r"(\d{2})-", p.name))]
    return folder / f"{max(numbers, default=0) + 1:02}-{label}.py"


def embed_path(path, output):
    """Python source for a path: relative to the project when the file lives inside it."""
    path = Path(path).resolve()
    root = Path(output).expanduser().resolve().parent
    try:
        return f"Path(__file__).resolve().parents[1] / {path.relative_to(root).as_posix()!r}" if project_file(output) \
            else f"Path({str(path)!r})"
    except ValueError:
        return f"Path({str(path)!r})"


def locate_at(source, name, point, angle="three-quarter", subject=None, camera=None, frame=None):
    """Resolve a normalized image position from a study or camera view to the surface of a named object."""
    with tempfile.TemporaryDirectory(prefix="blender-locate-") as directory:
        png = Path(directory) / "view.png"
        framed = None if angle == "camera" else (subject or name)
        info = preview(source, png, angle, framed, "clay", 600, frame, projection=True, camera=camera)
    return worker(source, "locate", timeout=180, scene=info["scene"], frame=info["frame"],
                  object={"name": name, "library": None}, projection=info["projection"], point=point)


def match_scene_photo(args, photo, name):
    """Recover a reference camera from a blockout or an overlapping aligned photo."""
    import photofit
    if args.anchors:
        anchors = json.loads(Path(args.anchors).read_text(encoding="utf-8-sig"))
        if not isinstance(anchors, list) or not all(isinstance(a, dict) and "world" in a and "pixel" in a for a in anchors):
            raise ValueError("Each scene anchor needs a world coordinate and a photo pixel.")
        world = [a["world"] for a in anchors]
        pixels = [a["pixel"] for a in anchors]
    else:
        if not args.surface:
            raise ValueError("Name the visible mesh or meshes with --surface for automatic matching.")
        camera = args.from_camera
        with tempfile.TemporaryDirectory(prefix="likeness-photo-") as folder:
            reference = Path(folder) / "reference"
            worker(args.source, "photo_reference", camera=camera, output=str(reference))
            pairs = photofit.feature_pairs(reference, photo)
            point_file = Path(folder) / "pixels.json"
            point_file.write_text(json.dumps([p[0] for p in pairs]), encoding="utf-8")
            hits = worker(args.source, "photo_hits", camera=camera, surfaces=args.surface, pixels=str(point_file))
        world = [hit for hit in hits if hit is not None]
        pixels = [pair[1] for pair, hit in zip(pairs, hits) if hit is not None]
    solved = photofit.scene_camera(world, pixels, photo, args.fov, automatic=bool(args.from_camera))
    script = next_build_script(args.output, "scene-photo-camera")
    content = (
        "from pathlib import Path\nimport bpy\nfrom modeling import fit\n\n"
        f"camera = fit.photo_camera(bpy.context.scene, {name!r}, {solved['matrix']!r}, "
        f"{solved['lens_mm']!r}, {embed_path(photo, args.output)}, "
        f"({solved['width']}, {solved['height']}), {{}}, error_px={solved['error_px']!r})\n"
        f"camera['focal_source'] = {solved['focal_source']!r}\n"
        f"camera.data.clip_end = {solved['clip_end']!r}\n"
        "camera.data.background_images[0].image.pack()\n"
        f"if camera.name != {name!r}:\n    raise ValueError('Choose a new photo camera name.')\n")
    with script.open("x", encoding="utf-8") as out:
        out.write(content)
    saved = edit(args.source, script, args.output, promote=args.promote)
    if args.anchors:
        errors = [{"point": a.get("name", i+1), "error_px": round(error, 2),
                   "inlier": i in solved["inliers"]}
                  for i, (a, error) in enumerate(zip(anchors, solved["residuals"]))]
        saved["anchor_errors"] = sorted(errors, key=lambda p: -p["error_px"])
    return {**saved, "camera": name, "inliers": len(solved["inliers"]), "points": solved["total"],
            "error_px": round(solved["error_px"], 2), "fov_deg": round(solved["fov"], 2),
            "focal_source": solved["focal_source"]}


def match_photo(args):
    """Match a photo to a scene or fit a portrait camera from facial landmarks."""
    import photofit
    photo = Path(args.photo).expanduser().resolve(strict=True)
    name = args.name or f"Photo | {photo.stem}"
    if not args.head:
        return match_scene_photo(args, photo, name)
    found = photofit.detect(photo)
    if found is None:
        raise ValueError(f"No face was found in {photo.name}.")
    # A turned head hides one side; keep the landmarks the photograph actually shows.
    found["points"] = photofit.visible_points(found["points"])
    known = worker(args.source, "landmark_world", object=args.head)
    if known["points"] and not args.redetect:
        vertices = {}
        world = known["points"]
    else:
        with tempfile.TemporaryDirectory(prefix="blender-match-") as directory:
            render = Path(directory) / "study.png"
            info = preview(args.source, render, args.angle, args.head, args.style, 1024, None, projection=True)
            seen = photofit.detect(render)
            if seen is None:
                raise ValueError("No face was found in the model's study render. Try --style render or another angle.")
            width, height = info["size"]
            points = {n: [p[0] / width, p[1] / height] for n, p in seen["points"].items()
                      if 0 <= p[0] / width <= 1 and 0 <= p[1] / height <= 1}
            hits = worker(args.source, "locate_many", object=args.head, projection=info["projection"], points=points)
        vertices = {n: h["vertex"] for n, h in hits["points"].items() if h}
        world = {n: h["world"] for n, h in hits["points"].items() if h}
    rigid = [n for n in photofit.RIGID if n in world and n in found["points"]]
    if len(rigid) < 6:
        raise ValueError("Too few rigid landmarks hit the head mesh; check --head names the face mesh.")
    solved = photofit.solve_camera([world[n] for n in rigid], [found["points"][n] for n in rigid],
                                   found["width"], found["height"])
    script = next_build_script(args.output, "photo-camera-" + "".join(c if c.isalnum() else "-" for c in photo.stem).strip("-").lower())
    script.write_text(
        f'"""Camera matching {photo.name}: {solved["error_px"]:.1f} px error over {len(rigid)} landmarks."""\n'
        "from pathlib import Path\n"
        "import json\n"
        "import bpy\n"
        "from modeling import fit\n\n"
        "scene = bpy.context.scene\n"
        f"head = bpy.data.objects[{args.head!r}]\n"
        f"known = json.loads(head.get(fit.LANDMARKS) or '{{}}')\n"
        f"known.update({json.dumps(vertices)})\n"
        "fit.store_landmarks(head, known)\n"
        f"photo = {embed_path(photo, args.output)}\n"
        f"cam = fit.photo_camera(scene, {name!r}, {json.dumps(solved['matrix'])}, {solved['lens_mm']:.4f}, photo,\n"
        f"                       ({found['width']}, {found['height']}), {json.dumps(found['points'])},\n"
        f"                       error_px={solved['error_px']:.3f})\n",
        encoding="utf-8")
    saved = edit(args.source, script, args.output, promote=args.promote)
    return {**saved, "camera": name, "build_script": str(script), "landmarks": len(vertices) or len(world),
            "photo_landmarks": len(found["points"]),
            "solve_landmarks": len(rigid), "error_px": round(solved["error_px"], 2),
            "fov_deg": round(solved["fov"], 1), "lens_mm": round(solved["lens_mm"], 2)}


def fit_face(args):
    """Move the head's landmarks to where the photo cameras see them, as a shape key, and report the pixel error."""
    before = worker(args.source, "landmark_error", object=args.head, cameras=args.camera)
    script = next_build_script(args.output, "face-fit")
    transfers = "".join(f"fit.transfer_delta(bpy.data.objects[{n!r}], head)\n" for n in (args.transfer or []))
    transfers += "".join(f"fit.transfer_delta(bpy.data.objects[{n!r}], head, rigid=True)\n" for n in (args.move or []))
    script.write_text(
        f'"""Fit {args.head} to {", ".join(args.camera)} by landmarks."""\n'
        "import bpy\n"
        "from modeling import fit\n\n"
        "scene = bpy.context.scene\n"
        f"head = bpy.data.objects[{args.head!r}]\n"
        f"cameras = [bpy.data.objects[n] for n in {json.dumps(args.camera)}]\n"
        f"fit.fit_to_photos(head, cameras, width={args.width!r}, strength={args.strength!r})\n"
        + transfers,
        encoding="utf-8")
    saved = edit(args.source, script, args.output, promote=args.promote)
    after = worker(args.output, "landmark_error", object=args.head, cameras=args.camera)
    return {**saved, "build_script": str(script),
            "error_px": [{"camera": b["camera"], "before": round(b["rms_px"], 2), "after": round(a["rms_px"], 2)}
                         for b, a in zip(before["errors"], after["errors"])]}


def live_brush(expected, script):
    """Operate only in the explicitly named, open working file with an undo step."""
    path = str(Path(expected).expanduser().resolve(strict=True))
    brush = str(Path(script).expanduser().resolve(strict=True))
    return live_code(
        "import bpy, os, runpy, sys\n"
        f"if not bpy.data.filepath or not os.path.samefile(bpy.data.filepath, {path!r}):\n"
        "    raise ValueError('The connected Blender window has a different working file.')\n"
        "if bpy.app.background or not bpy.context.preferences.edit.use_global_undo:\n"
        "    raise ValueError('Live brushes require an interactive window with Global Undo enabled.')\n"
        "bpy.ops.ed.undo_push(message='Before Blender Studio brush')\n"
        f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
        "try:\n"
        f"    runpy.run_path({brush!r}, run_name='__main__')\n"
        "except BaseException:\n"
        "    bpy.ops.ed.undo_push(message='Incomplete Blender Studio brush')\n"
        "    bpy.ops.ed.undo()\n"
        "    raise\n"
        "finally:\n"
        f"    sys.path.remove({str(SCRIPTS)!r})\n"
        "bpy.ops.ed.undo_push(message='Blender Studio brush')\n"
        "result = {'file': bpy.data.filepath, 'saved': False}"
    )


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    selftest = commands.add_parser("selftest", help="Exercise the bundled modeling tools in Blender.")
    selftest.add_argument("--quick", action="store_true", help="Skip full Rigify generation and binding.")
    read = commands.add_parser("inspect", help="Read a saved scene or the live Blender selection.")
    read.add_argument("source", help="A .blend path or 'live'.")
    read.add_argument("--object", dest="name")
    check = commands.add_parser("check", help="Inspect geometry, materials, image files and a polygon budget.")
    check.add_argument("source")
    check.add_argument("--subject")
    check.add_argument("--budget", type=int)
    change = commands.add_parser("edit", help="Run a bpy script and save a separate editable version.")
    change.add_argument("source", help="A .blend path, 'live' for unsaved work, or 'new' for an empty scene.")
    change.add_argument("script")
    change.add_argument("output")
    change.add_argument("--pack", action="store_true", help="Pack available external images into the output.")
    change.add_argument("--promote", action="store_true",
                        help="Replace the project file; the previous version moves into its history folder.")
    show = commands.add_parser("view", help="Render a camera frame or an automatically framed study.")
    show.add_argument("source")
    show.add_argument("output")
    show.add_argument("--angle", choices=(*ANGLES, "sheet"))
    show.add_argument("--subject", help="Object or collection for automatic framing.")
    show.add_argument("--style", choices=STYLES, default="clay")
    show.add_argument("--frame", type=int)
    show.add_argument("--size", type=int, default=900, help="Longest edge in pixels.")
    show.add_argument("--camera", help="Name of the scene camera. Selects the camera angle when omitted.")
    show.add_argument("--quality", choices=QUALITIES, default="draft", help="Draft renders quickly; final keeps scene settings.")
    show.add_argument("--engine", choices=VIEW_ENGINES, default="scene")
    compare = commands.add_parser("compare", help="Put a reference on the left and a matching Blender view on the right.")
    compare.add_argument("source")
    compare.add_argument("reference")
    compare.add_argument("output")
    compare.add_argument("--angle", choices=ANGLES, default="front")
    compare.add_argument("--subject")
    compare.add_argument("--style", choices=STYLES, default="clay")
    compare.add_argument("--size", type=int, default=900)
    compare.add_argument("--camera", help="Scene camera to render through with --angle camera.")
    compare.add_argument("--quality", choices=QUALITIES, default="draft")
    compare.add_argument("--engine", choices=VIEW_ENGINES, default="scene")
    compare.add_argument("--overlay", action="store_true", help="Add a third panel blending photo and render.")
    look = commands.add_parser("watch", help="Open a local page that shows a project's latest views as its file changes.")
    look.add_argument("project")
    look.add_argument("--port", type=int, default=8765)
    look.add_argument("--size", type=int, default=900)
    look.add_argument("--subject", help="Object or collection to frame; default: one named like the project.")
    keep = commands.add_parser("session", help="Keep a Blender open on a file so views and checks answer faster.")
    keep.add_argument("state", choices=("start", "stop", "status"))
    keep.add_argument("file", nargs="?")
    keep.add_argument("--idle", type=int, default=1800, help="Seconds without requests before the session exits.")
    ship = commands.add_parser("deliver", help="Render a project's standard outputs from its project file.")
    ship.add_argument("project", help="projects/<work> folder or its <work>.blend file.")
    ship.add_argument("--check", action="store_true", help="Only report which outputs are missing or older than the model.")
    ship.add_argument("--all", action="store_true", help="Re-render outputs that are already current.")
    ship.add_argument("--quality", choices=QUALITIES, default="final")
    ship.add_argument("--size", type=int, default=1600, help="Longest edge of each still.")
    ship.add_argument("--frames", type=int, default=72)
    ship.add_argument("--width", type=int, default=1024)
    ship.add_argument("--height", type=int, default=1024)
    uv = commands.add_parser("uv", help="Export the active UV layout of a named mesh to PNG.")
    uv.add_argument("source")
    uv.add_argument("output")
    uv.add_argument("--object", required=True)
    uv.add_argument("--size", type=int, default=1024)
    export = commands.add_parser("export", help="Export a model or subject to GLB, FBX, USDC or STL.")
    export.add_argument("source")
    export.add_argument("output")
    export.add_argument("--subject")
    export.add_argument("--scale", type=float, default=1.0)
    export.add_argument("--apply-modifiers", action="store_true")
    brush = commands.add_parser("brush", help="Run a window-only sculpt script on a named open working file.")
    brush.add_argument("working_file")
    brush.add_argument("script")
    open_scene = commands.add_parser("open", help="Open a working file in Blender with its local Lab bridge.")
    open_scene.add_argument("working_file")
    hit = commands.add_parser("locate", help="Resolve a copied panel reference, or an image position, to a 3D surface and UVs.")
    hit.add_argument("reference", nargs="?", help="JSON file or '-' to read JSON from stdin.")
    hit.add_argument("--object", help="With --at: the object whose surface to hit.")
    hit.add_argument("--at", help="With --object: image position x,y in 0..1 from the upper left of a study view.")
    hit.add_argument("--source", help="With --at: the .blend to look at.")
    hit.add_argument("--angle", choices=ANGLES, default="three-quarter")
    hit.add_argument("--subject")
    hit.add_argument("--camera")
    match = commands.add_parser("match-photo", help="Match a Blender camera to a photo of a face, object or place.")
    match.add_argument("source")
    match.add_argument("photo")
    match.add_argument("output")
    method = match.add_mutually_exclusive_group(required=True)
    method.add_argument("--head", help="The face mesh object.")
    method.add_argument("--anchors", help='Scene reference JSON: [{"world":[x,y,z],"pixel":[x,y]}, ...].')
    method.add_argument("--from-camera", help="Automatically match details in this camera's reference photo.")
    match.add_argument("--surface", action="append", help="A mesh to use for automatic matches; repeat as needed.")
    match.add_argument("--fov", type=float, help="Known horizontal field of view in degrees for a scene photo.")
    match.add_argument("--name", help="Camera name, default 'Photo | <photo stem>'.")
    match.add_argument("--angle", choices=ANGLES, default="front", help="Study angle for landmark detection on the model.")
    match.add_argument("--style", choices=STYLES, default="render")
    match.add_argument("--redetect", action="store_true", help="Find landmarks on the model again instead of reusing stored ones.")
    match.add_argument("--promote", action="store_true")
    fitting = commands.add_parser("fit-face", help="Move the head's landmarks to their photographed positions.")
    fitting.add_argument("source")
    fitting.add_argument("output")
    fitting.add_argument("--head", required=True)
    fitting.add_argument("--camera", action="append", required=True, help="A photo camera; repeat for several.")
    fitting.add_argument("--width", type=float, help="Blend radius in meters; default from landmark spacing.")
    fitting.add_argument("--strength", type=float, default=1.0)
    fitting.add_argument("--transfer", action="append", help="Hair, brows or lashes that deform with the skin.")
    fitting.add_argument("--move", action="append", help="Eyeballs, corneas or teeth that shift as one piece.")
    fitting.add_argument("--promote", action="store_true")
    orbit = commands.add_parser("orbit", help="Render a full local turntable to MP4.")
    orbit.add_argument("source")
    orbit.add_argument("output")
    view = orbit.add_mutually_exclusive_group()
    view.add_argument("--camera", help="Use an existing animated camera and its frame range.")
    view.add_argument("--subject", help="Automatically orbit an object or collection at a fixed pose.")
    orbit.add_argument("--frame", type=int, help="Fixed pose for auto-orbit or first frame for an existing camera.")
    orbit.add_argument("--frames", type=int, default=72)
    orbit.add_argument("--fps", type=float, default=24)
    orbit.add_argument("--engine", choices=("scene", "eevee", "cycles"), default="eevee")
    orbit.add_argument("--width", type=int, default=1024)
    orbit.add_argument("--height", type=int, default=1024)
    orbit.add_argument("--samples", type=int, default=24)
    orbit.add_argument("--style", choices=("clay","render"), default="render")
    orbit.add_argument("--quality", choices=QUALITIES, default="draft", help="Final keeps the scene's sampling.")
    spin = commands.add_parser("player", help="Make an offline, drag-to-rotate viewer for an existing turntable.")
    spin.add_argument("movie")
    spin.add_argument("output")
    spin.add_argument("--poster")
    spin.add_argument("--model")
    commands.add_parser("hardware", help="Report local Cycles devices, host memory and available storage.")
    estimate = commands.add_parser("estimate", help="Time representative frames with the saved scene's final render settings.")
    estimate.add_argument("source")
    estimate.add_argument("--frames", type=int, nargs="+", help="Sample frame numbers. Defaults to the current frame.")
    estimate.add_argument("--count", type=int, required=True, help="Total number of deliverable frames, including sampled frames.")
    import cloud_render
    cloud_render.add_commands(commands)
    args = cli.parse_args()
    if args.command == "view":
        args.angle = args.angle or ("camera" if args.camera else "three-quarter")
        if args.camera and args.angle != "camera":
            cli.error("--camera requires the camera angle.")
    if args.command == "inspect":
        value = inspect(args.source, args.name)
    elif args.command == "estimate":
        value = worker(args.source, "estimate", frames=args.frames, count=args.count)
    elif args.command == "hardware":
        value = worker("new", "hardware")
    elif args.command == "cloud":
        value = cloud_render.run(args, blender_binary)
    elif args.command == "selftest":
        value = worker("new", "selftest", quick=args.quick)
    elif args.command == "check":
        if args.budget is not None and args.budget < 0:
            raise ValueError("The face budget must be nonnegative.")
        value = worker(args.source, "check", subject=args.subject, budget=args.budget)
        project = project_file(args.source)
        if project:
            value["deliverables"] = deliverables(project, value["cameras"])
            stale = [d["file"] for d in value["deliverables"] if d["state"] != "current"]
            if stale:
                value["summary"] += f" {len(stale)} deliverable(s) missing or older than the model."
        print(value["summary"], file=sys.stderr)
    elif args.command == "compare":
        if not 64 <= args.size <= 4096:
            raise ValueError("Choose a size between 64 and 4096.")
        value = output_job(args.source, "compare", args.output, ".png", reference=str(Path(args.reference).resolve(strict=True)),
                           angle=args.angle, subject=args.subject, style=args.style, size=args.size, frame=None,
                           pick_image=None, quality=args.quality, engine=args.engine, camera=args.camera,
                           overlay=args.overlay)
    elif args.command == "deliver":
        value = deliver(args)
    elif args.command == "watch":
        import watch
        sys.argv = [sys.argv[0], args.project, "--port", str(args.port), "--size", str(args.size),
                    *(["--subject", args.subject] if args.subject else [])]
        watch.main()
        return
    elif args.command == "session":
        if args.state != "status" and not args.file:
            raise ValueError("Name the .blend file to keep open.")
        value = session(args.state, args.file, args.idle)
    elif args.command == "edit":
        value = edit(args.source, args.script, args.output, args.pack, args.promote)
    elif args.command == "brush":
        value = live_brush(args.working_file, args.script)
    elif args.command == "uv":
        if not 64 <= args.size <= 8192:
            raise ValueError("Choose a UV image size between 64 and 8192.")
        value = output_job(args.source, "uv", args.output, ".png", object=args.object, size=args.size)
    elif args.command == "export":
        value = export_model(args)
    elif args.command == "match-photo":
        value = match_photo(args)
    elif args.command == "fit-face":
        value = fit_face(args)
    elif args.command == "locate" and args.at:
        if not (args.object and args.source):
            raise ValueError("Use --at with --object and --source.")
        x, y = (float(v) for v in args.at.split(","))
        value = locate_at(args.source, args.object, [x, y], args.angle, args.subject, args.camera)
    elif args.command == "locate":
        if not args.reference:
            raise ValueError("Give a reference JSON file, or --source, --object and --at.")
        raw = sys.stdin.read() if args.reference == "-" else Path(args.reference).read_text(encoding="utf-8-sig")
        selection = json.loads(raw)
        source = selection["input_source"]
        if source == "live":
            raise ValueError("This reference predates saved live views. Create a fresh view of the open scene.")
        if selection.get("revision") and tuple(selection["revision"]) != file_revision(source):
            raise ValueError("The model changed after selection. Refresh the model before editing.")
        for path, revision in (selection.get("dependencies") or {}).items():
            if tuple(revision) != file_revision(path):
                raise ValueError("An external asset changed after selection. Refresh the model before editing.")
        value = locate(source, selection)
    elif args.command == "orbit":
        value = turntable(args)
    elif args.command == "player":
        value = player(args.movie, args.output, args.poster, args.model)
    elif args.command == "open":
        file = Path(args.working_file).resolve(strict=True)
        if file.suffix.lower() != ".blend":
            raise ValueError("Choose a .blend file to open.")
        process = subprocess.Popen([blender_binary(), "--disable-autoexec", "--online-mode",
                                    "--addons", "bl_ext.user_default.mcp", str(file)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        value = {"file": str(file), "pid": process.pid}
    elif Path(args.output).suffix.lower() == ".html":
        value = local_panel(args.source, args.output, args.angle, args.subject, args.style, args.size, args.frame, args.camera)
    else:
        value = preview(args.source, args.output, args.angle, args.subject, args.style, args.size, args.frame,
                        quality=args.quality, engine=args.engine, camera=args.camera)
    print(json.dumps(value, indent=2, ensure_ascii=False))
    if args.command == "selftest" and not value["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        sys.exit(str(exc))
