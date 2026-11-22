"""Watch a project folder in the browser: fresh views, steps, sources and outputs of its model file."""

import argparse
import datetime
import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import sys
import threading
from urllib.parse import quote, unquote

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import studio  # noqa: E402

CACHE = studio.PROJECT / "cache" / "watch"
PAGE = SCRIPTS / "watch.html"
TYPES = {".png": "image/png", ".html": "text/html; charset=utf-8"}
WAIT_PAGE = """<!doctype html><meta charset="utf-8"><title>Pick a point</title>
<body style="font:15px system-ui,sans-serif;color:#1d1d1b;background:#f3f2ef;padding:24px 16px">
<p id="m" style="color:#d5a886;font-weight:600">Preparing the picker, this takes a few seconds…</p>
<script>(async function tick(){try{const s=await (await fetch("/state.json",{cache:"no-store"})).json();
if(s.panel){location.replace(s.panel);return}if(s.error){document.getElementById("m").textContent=s.error}}
catch(e){}setTimeout(tick,1500)})()</script>"""


def slug(text):
    return re.sub(r"[^\w\-]+", "-", text).strip("-") or "model"


def clock(stamp_ns=None):
    moment = datetime.datetime.fromtimestamp(stamp_ns / 1e9) if stamp_ns else datetime.datetime.now()
    return moment.strftime("%H:%M:%S")


class Watcher:
    """Polls the project file and renders into the cache from one background thread."""

    def __init__(self, folder, size, subject=None):
        self.model = studio.project_file(folder)
        if self.model is None:
            raise ValueError(f"Not a project folder (expected <folder>/<folder>.blend): {folder}")
        self.folder, self.size, self.subject = self.model.parent, size, subject
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.done, self.pick_wanted, self.cameras = None, False, []
        self.state = {"name": self.folder.name, "file": self.model.name, "saved": None, "rendering": None,
                      "last_render": None, "error": None, "sheet": None, "views": [], "build": [], "steps": [],
                      "sources": "", "summary": None, "outputs": [], "panel": None}

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def snapshot(self):
        with self.lock:
            return json.dumps(self.state, ensure_ascii=False)

    def revision(self):
        return studio.file_revision(self.model)

    def cache_name(self, revision, label, suffix=".png"):
        return f"{slug(self.folder.name)}-{revision[0]}-{slug(label)}{suffix}"

    def url(self, name):
        return "/cache/" + quote(name)

    def run(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # keep watching after a failed render
                self.update(error=str(exc)[-1200:], rendering=None)
            self.stop.wait(2)

    def tick(self):
        revision = self.revision()
        self.refresh_folder(revision)
        if revision != self.done:
            self.render_all(revision)
        if self.pick_wanted:
            self.make_panel(revision)

    def refresh_folder(self, revision):
        """The parts that need no Blender: build scripts, sources, output freshness."""
        build = self.folder / "build"
        scripts = sorted(p.name for p in build.glob("*.py")) if build.is_dir() else []
        sources = self.folder / "sources.md"
        text = sources.read_text(encoding="utf-8", errors="replace") if sources.is_file() else ""
        outputs = [dict(d, file=Path(d["file"]).name) for d in studio.deliverables(self.model, self.cameras)]
        self.update(build=scripts, sources=text, outputs=outputs, saved=clock(int(revision[0])))

    def changed(self, revision):
        return self.revision() != revision

    def framed(self, render):
        """Frame on --subject, else on an object or collection named like the project, else the whole scene."""
        candidates = [self.subject] if self.subject else [self.folder.name, None]
        for subject in candidates:
            try:
                return render(subject)
            except ValueError:
                if subject == candidates[-1]:
                    raise

    def render_all(self, revision):
        self.update(error=None)

        def sheet(subject):  # four views first, so something appears quickly
            image = CACHE / self.cache_name(revision, "sheet-" + (subject or "scene"))
            if not image.exists():
                studio.preview(self.model, image, angle="sheet", subject=subject, style="clay", size=self.size,
                               timeout=600)
            return image
        self.update(rendering="four views")
        self.update(sheet=self.url(self.framed(sheet).name), last_render=clock())
        if self.changed(revision):
            return
        self.update(rendering="scene report")
        report = studio.worker(self.model, "check", timeout=600, subject=None, budget=None)
        self.cameras = report["cameras"]
        self.update(summary=report["summary"], steps=report["build_log"])
        self.refresh_folder(revision)
        views = []
        for camera in self.cameras:
            name = camera["name"]
            if not re.match(r"\d\d", name):
                continue
            if self.changed(revision):
                return
            image = CACHE / self.cache_name(revision, name)
            if not image.exists():
                self.update(rendering=name)
                studio.preview(self.model, image, angle="camera", style="render", size=self.size,
                               quality="draft", camera=name, timeout=600)
            views.append({"name": name, "url": self.url(image.name)})
            self.update(views=list(views), last_render=clock())
        self.update(rendering=None, last_render=clock())
        self.done = revision

    def make_panel(self, revision):
        def panel(subject):
            page = CACHE / self.cache_name(revision, "pick-" + (subject or "scene"), ".html")
            if not page.exists():
                studio.local_panel(str(self.model), page, "three-quarter", subject, "clay", self.size, None)
            return page
        self.update(rendering="point picker")
        try:
            page = self.framed(panel)
        finally:
            self.update(rendering=None)
        self.pick_wanted = False
        self.update(panel=self.url(page.name))

    def request_panel(self):
        """Return the panel for the current model if it exists, else queue one."""
        revision = self.revision()
        for subject in ([self.subject] if self.subject else [self.folder.name, None]):
            page = CACHE / self.cache_name(revision, "pick-" + (subject or "scene"), ".html")
            if page.exists():
                return self.url(page.name)
        self.update(panel=None)
        self.pick_wanted = True
        return None


class Handler(BaseHTTPRequestHandler):
    watcher = None

    def log_message(self, format, *args):  # quiet, the terminal shows only the URL
        pass

    def send(self, status, body, content_type, cache=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable" if cache else "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = unquote(self.path.split("?", 1)[0])
        if path == "/":
            page = PAGE.read_text(encoding="utf-8").replace("{{name}}", html.escape(self.watcher.folder.name))
            return self.send(200, page.encode("utf-8"), TYPES[".html"])
        if path == "/state.json":
            return self.send(200, self.watcher.snapshot().encode("utf-8"), "application/json; charset=utf-8")
        if path == "/pick":
            ready = self.watcher.request_panel()
            if ready:
                self.send_response(302)
                self.send_header("Location", ready)
                self.end_headers()
                return None
            return self.send(200, WAIT_PAGE.encode("utf-8"), TYPES[".html"])
        if path.startswith("/cache/"):
            name = path[len("/cache/"):]
            file = (CACHE / name).resolve()
            if "/" not in name and "\\" not in name and file.parent == CACHE.resolve() and file.suffix in TYPES \
                    and file.is_file():
                return self.send(200, file.read_bytes(), TYPES[file.suffix], cache=True)
        return self.send(404, b"Not found", "text/plain; charset=utf-8")


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("project", help="A project folder (<folder>/<folder>.blend).")
    cli.add_argument("--port", type=int, default=8765)
    cli.add_argument("--size", type=int, default=900, help="Longest edge of each view in pixels.")
    cli.add_argument("--subject", help="Object or collection to frame the four views on; default: one named like the project.")
    args = cli.parse_args()
    if not 64 <= args.size <= 4096:
        raise ValueError("Choose a size between 64 and 4096.")
    CACHE.mkdir(parents=True, exist_ok=True)
    watcher = Watcher(Path(args.project).expanduser().resolve(), args.size, args.subject)
    Handler.watcher = watcher
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=watcher.run, name="watch-render", daemon=True)
    thread.start()
    print(f"Watching {watcher.model}\nOpen http://127.0.0.1:{args.port}/  (Ctrl+C stops)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        watcher.stop.set()
        server.server_close()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))
