"""Where Likeness keeps what it downloads and remembers on this machine, and where it finds Blender and FFmpeg.

Order for every setting: an environment variable, then `local.json` beside SKILL.md (not shared, not published),
then the default. local.json may hold {"home": "...", "library": "...", "blender": "..."}.

  home     LIKENESS_HOME      default: %LOCALAPPDATA%/likeness, ~/Library/Application Support/likeness, ~/.local/share/likeness
  library  LIKENESS_LIBRARY   default: <home>/library      scanned plants, rocks, ground textures and skies
  blender  BLENDER_EXE        default: <home>/.runtime/blender, then PATH, then the usual install places
"""
import glob
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

SKILL = Path(__file__).resolve().parents[1]
LOCAL = SKILL / "local.json"
BLENDER_MIN = (5, 2)


def settings():
    try:
        return json.loads(LOCAL.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}


def home():
    chosen = os.environ.get("LIKENESS_HOME") or settings().get("home")
    if chosen:
        return Path(chosen).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "likeness"


def runtime():
    return home() / ".runtime"


def library_root():
    chosen = os.environ.get("LIKENESS_LIBRARY") or settings().get("library")
    return Path(chosen).expanduser() if chosen else home() / "library"


def _version_in(text):
    m = re.search(r"(\d+)\.(\d+)", text)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def blender_candidates():
    """Every Blender this machine seems to have, the preferred one first."""
    chosen = os.environ.get("BLENDER_EXE") or settings().get("blender")
    if chosen:                                    # an explicit choice is the only candidate, and only if it is there
        return [Path(chosen).expanduser()] if Path(chosen).expanduser().is_file() else []
    exe = "blender.exe" if os.name == "nt" else "blender"
    found = [runtime() / "blender" / exe, runtime() / "blender" / "Blender.app" / "Contents" / "MacOS" / "Blender"]
    if shutil.which("blender"):
        found.append(Path(shutil.which("blender")))
    installed = []
    if os.name == "nt":
        for root in {os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", r"C:\Program Files")}:
            installed += glob.glob(os.path.join(root, "Blender Foundation", "Blender*", "blender.exe"))
        installed += glob.glob(os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Steam", "steamapps", "common", "Blender", "blender.exe"))
        installed += glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Blender*", "blender.exe"))
    elif sys.platform == "darwin":
        installed += glob.glob("/Applications/Blender*.app/Contents/MacOS/Blender")
        installed += glob.glob(os.path.expanduser("~/Applications/Blender*.app/Contents/MacOS/Blender"))
    else:
        installed += glob.glob("/snap/bin/blender") + glob.glob("/opt/blender*/blender") + glob.glob(os.path.expanduser("~/blender*/blender"))
    installed.sort(key=lambda p: _version_in(Path(p).parent.name if os.name == "nt" else p), reverse=True)
    found += [Path(p) for p in installed]
    seen, out = set(), []
    for p in found:
        key = str(p).casefold()
        if p.is_file() and key not in seen:
            seen.add(key)
            out.append(p)
    return out


def blender():
    found = blender_candidates()
    if not found:
        chosen = os.environ.get("BLENDER_EXE") or settings().get("blender")
        if chosen:
            raise ValueError(f"BLENDER_EXE (or local.json) names {chosen}, which does not exist. Fix it, or run "
                             "`likeness setup --get` to download Blender there.")
        raise ValueError("Blender was not found. Run `likeness setup --get` to download Blender into "
                         f"{runtime() / 'blender'}, or set BLENDER_EXE to a Blender {BLENDER_MIN[0]}.{BLENDER_MIN[1]}+ executable.")
    return str(found[0])


def blender_version(binary=None):
    try:
        out = subprocess.run([binary or blender(), "--version"], capture_output=True, text=True, timeout=60,
                             stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    m = re.search(r"Blender (\d+\.\d+(?:\.\d+)?)", out)
    return m.group(1) if m else None


def tool(name):
    """ffmpeg or ffprobe: on PATH, or the copy `likeness setup --get` put in <home>/.runtime/ffmpeg."""
    if shutil.which(name):
        return shutil.which(name)
    exe = name + (".exe" if os.name == "nt" else "")
    for p in (runtime() / "ffmpeg" / "bin" / exe, runtime() / "ffmpeg" / exe):
        if p.is_file():
            return str(p)
    raise FileNotFoundError(f"{name} was not found. Run `likeness setup --get`, or install FFmpeg and put it on PATH.")
