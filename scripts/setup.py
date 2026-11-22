"""First run on a machine: what Likeness needs, where it is, and what is missing.

    likeness setup          report what is here
    likeness setup --get    also fetch what is missing: Blender 5.2 LTS and FFmpeg (Windows, Linux), numpy and Pillow
                            (pip), and the Poly Haven assets the starter scene uses

Downloads go to the Likeness home folder (`likeness where`), never into the skill folder. Move it with LIKENESS_HOME
or {"home": "..."} in local.json beside SKILL.md. On macOS Blender and FFmpeg are installed by hand:
`brew install --cask blender` and `brew install ffmpeg`."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
from urllib.request import Request, urlopen
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import home  # noqa: E402

AGENT = {"User-Agent": "likeness-setup/1.0"}
BLENDER_INDEX = "https://download.blender.org/release/Blender{0}.{1}/"
FFMPEG = {"nt": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
          "linux": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"}
STARTER = home.SKILL / "examples" / "starter" / "build.py"


def say(*a):
    print(*a, flush=True)


def download(url, target, label):
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    have = part.stat().st_size if part.is_file() else 0          # an interrupted download goes on where it stopped
    headers = dict(AGENT, Range=f"bytes={have}-") if have else AGENT
    with urlopen(Request(url, headers=headers), timeout=120) as r:
        resumed = have and r.status == 206
        out = open(part, "ab" if resumed else "wb")
        done = have if resumed else 0
        total, shown, start = int(r.headers.get("Content-Length") or 0) + done, -1, time.time()
        if resumed:
            say(f"  {label}: going on from {done / 1e6:.0f} MB")
        with out:
            while True:
                block = r.read(1 << 20)
                if not block:
                    break
                out.write(block)
                done += len(block)
                step = int(done * 10 / total) if total else done >> 26
                if step != shown:
                    shown = step
                    rate = done / max(0.1, time.time() - start) / 1e6
                    say(f"  {label}: {done / 1e6:.0f}" + (f" / {total / 1e6:.0f}" if total else "") + f" MB, {rate:.1f} MB/s")
    part.replace(target)
    return target


def unpack_into(archive, folder, inner_marker):
    """Unpack an archive whose files sit in one top folder, and move that folder's content into `folder`."""
    scratch = folder.with_name(folder.name + ".unpack")
    shutil.rmtree(scratch, ignore_errors=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(scratch)
    else:
        with tarfile.open(archive) as t:
            t.extractall(scratch, filter="data") if sys.version_info >= (3, 12) else t.extractall(scratch)
    top = next(p.parent for p in scratch.rglob(inner_marker))
    shutil.rmtree(folder, ignore_errors=True)
    shutil.move(str(top), str(folder))
    shutil.rmtree(scratch, ignore_errors=True)
    archive.unlink(missing_ok=True)


def get_blender():
    if sys.platform == "darwin":
        say("Blender: install Blender 5.2 LTS from https://www.blender.org/download/lts/ or `brew install --cask blender`.")
        return
    index = BLENDER_INDEX.format(*home.BLENDER_MIN)
    page = urlopen(Request(index, headers=AGENT), timeout=60).read().decode("utf-8", "replace")
    kind = "windows-x64.zip" if os.name == "nt" else "linux-x64.tar.xz"
    names = sorted(set(re.findall(r"blender-(\d+\.\d+\.\d+)-" + re.escape(kind), page)), key=lambda v: [int(x) for x in v.split(".")])
    if not names:
        raise RuntimeError(f"No Blender {kind} found at {index}")
    version = names[-1]
    url = f"{index}blender-{version}-{kind}"
    say(f"Blender {version}: downloading {url}")
    archive = download(url, home.runtime() / "downloads" / f"blender-{version}-{kind}", f"Blender {version}")
    unpack_into(archive, home.runtime() / "blender", "blender.exe" if os.name == "nt" else "blender")
    say(f"Blender {version}: ready in {home.runtime() / 'blender'}")


def get_ffmpeg():
    if sys.platform == "darwin":
        say("FFmpeg: install it with `brew install ffmpeg`.")
        return
    url = FFMPEG["nt" if os.name == "nt" else "linux"]
    say(f"FFmpeg: downloading {url}")
    archive = download(url, home.runtime() / "downloads" / url.rsplit("/", 1)[1], "FFmpeg")
    unpack_into(archive, home.runtime() / "ffmpeg", "bin")
    say(f"FFmpeg: ready in {home.runtime() / 'ffmpeg'}")


def starter_assets():
    return sorted(set(re.findall(r'lib\("(polyhaven:[A-Za-z0-9_.-]+)"\)', STARTER.read_text(encoding="utf-8"))))


def get_assets(ids):
    import sources
    for i, asset in enumerate(ids, 1):
        folder = home.library_root().joinpath(*asset.split(":"))
        if folder.is_dir():
            continue
        say(f"Assets {i}/{len(ids)}: {asset}")
        sources.fetch_library(asset, folder)


def report():
    status = {"python": sys.version.split()[0], "home": str(home.home()), "library": str(home.library_root())}
    free = shutil.disk_usage(next(p for p in [home.home(), *home.home().parents] if p.exists())).free
    status["free_disk_gb"] = round(free / 1e9, 1)
    found = home.blender_candidates()
    status["blender"] = {"path": str(found[0]), "version": home.blender_version(str(found[0]))} if found else None
    if status["blender"] and not status["blender"]["version"]:
        status["blender"]["problem"] = "this file does not start as Blender"
    for name in ("ffmpeg", "ffprobe"):
        try:
            status[name] = home.tool(name)
        except FileNotFoundError:
            status[name] = None
    for module, label in (("numpy", "numpy"), ("PIL", "Pillow")):
        try:
            __import__(module)
            status[label] = True
        except ImportError:
            status[label] = False
    status["node (optional: page shots)"] = shutil.which("node")
    ids = starter_assets()
    status["starter_assets"] = f"{sum(home.library_root().joinpath(*a.split(':')).is_dir() for a in ids)} of {len(ids)} in the library"
    missing = []
    b = status["blender"]
    if not b or not b["version"] or tuple(int(x) for x in b["version"].split(".")[:2]) < home.BLENDER_MIN:
        missing.append("blender")
    if not status["ffmpeg"] or not status["ffprobe"]:
        missing.append("ffmpeg")
    if not status["numpy"] or not status["Pillow"]:
        missing.append("python packages")
    if not status["starter_assets"].startswith(f"{len(ids)} of"):
        missing.append("starter assets")
    status["missing"] = missing
    return status


def main(argv):
    get = "--get" in argv
    status = report()
    if not get or not status["missing"]:
        if status["missing"]:
            status["next"] = "likeness setup --get   (downloads into the home folder above)"
        print(json.dumps(status, indent=2, ensure_ascii=False))
        return
    say(f"Fetching into {home.home()}: {', '.join(status['missing'])}")
    if "blender" in status["missing"]:
        get_blender()
    if "ffmpeg" in status["missing"]:
        get_ffmpeg()
    if "python packages" in status["missing"]:
        say("Python packages: numpy, Pillow")
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "numpy", "pillow"], check=False)
    if "starter assets" in status["missing"]:
        get_assets(starter_assets())
    after = report()
    if "python packages" in after["missing"]:
        # Debian and Ubuntu keep the system Python for apt (PEP 668): pip refuses, apt does it
        after["next"] = ("sudo apt install python3-numpy python3-pil" if Path("/etc/debian_version").exists()
                         else f"{sys.executable} -m pip install numpy pillow")
        after["why"] = "numpy and Pillow are needed by `likeness place` and `likeness plan` only"
    print(json.dumps(after, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main(sys.argv[1:])
