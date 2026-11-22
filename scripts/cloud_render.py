"""Runpod v2 controller for one authorized, time-limited render on ephemeral disk."""

import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import home  # noqa: E402


API = "https://api.runpod.io/v2"
IMAGE = "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404"


def add_commands(commands):
    cloud = commands.add_parser("cloud", help="Price, render and release a Runpod GPU.")
    actions = cloud.add_subparsers(dest="action", required=True)
    actions.add_parser("offers", help="Read current secure-cloud GPU capacity and list rates.")
    for action in ("status", "release"):
        actions.add_parser(action).add_argument("job", type=Path)
    start = actions.add_parser("render", help="Rent one GPU, render and retrieve a contiguous PNG/EXR sequence, then terminate.")
    start.add_argument("source", type=Path)
    start.add_argument("job", type=Path, help="New local job folder for this allocation and its frames.")
    start.add_argument("--frames", nargs=2, type=int, metavar=("FIRST", "LAST"), required=True)
    start.add_argument("--gpu", required=True, help="Exact GPU ID from offers.")
    start.add_argument("--hours", required=True, type=float, help="Approved maximum elapsed rental hours.")
    start.add_argument("--budget", required=True, type=float, help="Approved USD for this job.")
    start.add_argument("--extra-cost", type=float, required=True, help="USD reserved for disk, transfer and shutdown margin.")
    start.add_argument("--disk", type=int, required=True, help="Ephemeral container disk in GB.")
    start.add_argument("--key", type=Path, required=True, help="SSH private key with a matching .pub file.")
    start.add_argument("--asset", action="append", default=[], help="Required relative asset file or folder beside the blend; repeatable.")


def api(method, path, body=None, absent_ok=False):
    token = os.environ.get("RUNPOD_API_KEY")
    if not token and os.environ.get("RUNPOD_API_KEY_FILE"):
        token = Path(os.environ["RUNPOD_API_KEY_FILE"]).read_text(encoding="utf-8-sig").strip()
    if not token:
        raise ValueError("Set RUNPOD_API_KEY or RUNPOD_API_KEY_FILE locally.")
    request = Request(API + path, method=method, headers={"Authorization": "Bearer " + token,
                      "Content-Type": "application/json"}, data=json.dumps(body).encode() if body else None)
    try:
        with urlopen(request, timeout=25) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except HTTPError as error:
        if error.code == 404 and absent_ok:
            return None
        raise RuntimeError(f"Runpod {method} {path.split('?')[0]} returned HTTP {error.code}.") from None


def offers():
    rows = api("GET", "/catalog/gpus?include=AVAILABILITY&product=POD&cloud=SECURE&count=1")["gpus"]
    return [{"id": r["id"], "vram_gb": r["memory"], "min_host_ram_gb": r.get("minRam"),
             "usd_per_gpu_hour": r["price"]["secure"], "availability": r["availability"],
             "regions": {c["id"]: c["availability"] for c in r.get("dataCenters") or [] if c.get("availability") not in (None, "NONE")}}
            for r in rows if r["manufacturer"].upper() == "NVIDIA" and r["availability"] != "NONE"]


def find_pod(job, name):
    record = job / "pod-id"
    if record.exists():
        pod = api("GET", "/pods/" + quote(record.read_text().strip(), safe=""), absent_ok=True)
        if pod and pod["name"] != name:
            raise RuntimeError("Recorded pod name differs. Inspect the job before releasing it.")
        return pod
    # A POST may have succeeded even when its reply was lost. Never repeat it.
    path = "/pods"
    while path:
        page = api("GET", path)
        for pod in page["pods"]:
            if pod["name"] == name:
                record.write_text(pod["id"])
                return pod
        cursor = page["pagination"]["nextCursor"]
        path = "/pods?cursor=" + quote(cursor, safe="") if cursor else None
    return None


def release(job, name):
    pod = find_pod(job, name)
    if not pod and not (job / "pod-id").exists():
        raise RuntimeError(f"No pod currently found for {name}. Creation was unconfirmed; recheck this name in the account.")
    if pod and pod["status"] != "TERMINATED":
        path = "/pods/" + quote(pod["id"], safe="")
        api("DELETE", path, absent_ok=True)
        for _ in range(12):
            remaining = api("GET", path, absent_ok=True)
            if not remaining or remaining["status"] == "TERMINATED":
                break
            time.sleep(5)
        else:
            raise RuntimeError(f"Termination unconfirmed for {pod['id']}. Use cloud status and release.")
    return {"name": name, "released": True}


def pack(source, assets, archive):
    root = source.parent
    with tarfile.open(archive, "w") as bundle:
        bundle.add(source, arcname=source.name, recursive=False)
        for value in assets:
            path = (root / value).resolve(strict=True)
            if not path.is_relative_to(root) or path == root or archive.is_relative_to(path):
                raise ValueError(f"Specify an asset beneath the scene directory: {value}")
            for file in path.rglob("*") if path.is_dir() else [path]:
                if file.is_symlink() or not file.resolve().is_relative_to(root):
                    raise ValueError(f"Use a project-local asset: {file}")
                if file.is_file():
                    bundle.add(file, arcname=file.relative_to(root).as_posix(), recursive=False)


# This runs on the pod. The provider injects a pod-scoped key. Never upload the local account key.
WATCHDOG = """
import os, time, sys
from urllib.request import Request, urlopen
url = 'https://api.runpod.io/v2/pods/' + os.environ['RUNPOD_POD_ID']
headers = {'Authorization': 'Bearer ' + os.environ['RUNPOD_API_KEY']}
with urlopen(Request(url, headers=headers), timeout=15): pass
open('/tmp/studio-armed', 'w').close()
time.sleep(max(0, float(sys.argv[1]) - time.time()))
for attempt in range(12):
    try:
        with urlopen(Request(url, method='DELETE', headers=headers), timeout=15): break
    except OSError:
        time.sleep(5)
"""


def render(args, blender_binary):
    first, last = args.frames
    if first > last or args.disk < 1 or not all(math.isfinite(v) for v in (args.budget, args.hours, args.extra_cost)):
        raise ValueError("Check frame range, disk and finite rental limits.")
    if not (args.hours > 0 and 0 <= args.extra_cost < args.budget):
        raise ValueError("Specify a positive time limit and budget with room for compute.")
    for exe in ("ssh", "scp", "ffmpeg"):
        if not shutil.which(exe):
            raise ValueError(f"Missing {exe}.")
    source = args.source.resolve(strict=True)
    if source.suffix.lower() != ".blend":
        raise ValueError("Expected an editable .blend file.")
    identity = args.key.resolve(strict=True)
    public = Path(str(identity) + ".pub").read_text().strip()
    if not public.startswith(("ssh-", "ecdsa-")) or "\n" in public:
        raise ValueError("Expected one OpenSSH public key.")
    version_text = subprocess.check_output([blender_binary(), "--version"], encoding="utf-8")
    version = re.search(r"Blender (\d+\.\d+\.\d+)", version_text).group(1)
    url = f"https://download.blender.org/release/Blender{version.rsplit('.', 1)[0]}/blender-{version}-linux-x64.tar.xz"
    row = next((r for r in offers() if r["id"] == args.gpu), None)
    compute_limit = (args.budget - args.extra_cost) / args.hours
    if not row or not 0 < row["usd_per_gpu_hour"] <= compute_limit:
        raise ValueError("No matching capacity within the approved compute rate.")
    job = args.job.resolve()
    job.mkdir(parents=True, exist_ok=False)
    archive = job / "upload.tar"
    pack(source, args.asset, archive)
    name = "studio-" + uuid.uuid4().hex
    (job / "name").write_text(name)
    print(f"Render job {name} in {job}", file=sys.stderr, flush=True)
    deadline = time.time() + args.hours * 3600

    def remaining():
        seconds = deadline - time.time()
        if seconds <= 0:
            raise TimeoutError("Rental deadline reached.")
        return seconds

    try:
        pod = api("POST", "/pods", {"name": name, "image": IMAGE, "gpu": {"id": args.gpu, "count": 1},
                  "cloud": "SECURE", "disk": args.disk, "ports": ["22/tcp"], "startSsh": True,
                  "env": {"PUBLIC_KEY": public}})
        (job / "pod-id").write_text(pod["id"])
        print(f"Allocated {pod['id']} at USD {pod['cost']}/compute-hour.", file=sys.stderr, flush=True)
        if not 0 < pod["cost"] <= compute_limit:
            raise ValueError("Allocated compute rate exceeds the approved rate or was unavailable.")
        ready_by = min(deadline, time.time() + 300)
        while time.time() < ready_by:
            pod = find_pod(job, name)
            if not pod or pod["status"] in {"EXITED", "ERROR", "TERMINATED"}:
                raise RuntimeError("Pod failed to start.")
            direct = (pod.get("ssh") or {}).get("direct")
            if direct:
                break
            time.sleep(min(5, remaining()))
        else:
            raise TimeoutError("Pod did not expose direct SSH in time.")
        host = direct["username"] + "@" + direct["host"]
        options = ["-i", str(identity), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                   "-o", "StrictHostKeyChecking=accept-new", "-o", 'UserKnownHostsFile="' + (job / "known_hosts").as_posix() + '"']
        port = str(direct["port"])

        def ssh(command, timeout=None):
            return subprocess.check_output(["ssh", *options, "-p", port, host, command], text=True,
                                           timeout=min(remaining(), timeout or remaining()))

        def copy(src, dst):
            subprocess.run(["scp", *options, "-P", port, src, dst], check=True, timeout=remaining())

        while True:
            try:
                ssh("true", 15)
                break
            except subprocess.CalledProcessError:
                if time.time() >= ready_by:
                    raise
                time.sleep(min(5, remaining()))
        watch = "python3 -c " + shlex.quote(WATCHDOG) + " " + str(int(deadline))
        ssh(f"nohup {watch} >/tmp/studio-timer.log 2>&1 </dev/null & "
            "for i in $(seq 1 25); do test ! -f /tmp/studio-armed || exit 0; sleep 1; done; exit 1")
        ssh("mkdir -p /tmp/studio/input /tmp/studio/frames")
        copy(str(archive), host + ":/tmp/studio/input.tar")
        copy(str(Path(__file__).with_name("cloud_frames.py")), host + ":/tmp/studio/frames.py")
        copy(str(Path(__file__).with_name("worker.py")), host + ":/tmp/studio/worker.py")
        ssh("cd /tmp/studio && tar xf input.tar -C input && "
            "apt-get -qq update && DEBIAN_FRONTEND=noninteractive apt-get -qq install -y curl xz-utils "
            "libxi6 libxxf86vm1 libxfixes3 libxrender1 libgl1 libxkbcommon0 libsm6 libegl1 >/dev/null && "
            f"curl -fsSL {shlex.quote(url)} -o blender.tar.xz && mkdir blender && tar xf blender.tar.xz --strip-components=1 -C blender")
        command = ("cd /tmp/studio && ./blender/blender -b -noaudio --disable-autoexec " +
                   shlex.quote("input/" + source.name) + " --python-exit-code 1 --python frames.py -- " +
                   f"{first} {last} /tmp/studio/frames")
        ssh("nohup sh -c " + shlex.quote(f"{command} >/tmp/studio/render.log 2>&1; echo $? >/tmp/studio/exit") +
            " </dev/null >/dev/null 2>&1 &")
        frames = job / "frames"
        frames.mkdir()
        expected = {f"{n:06d}" for n in range(first, last + 1)}
        while expected:
            # Read the completion flag first: the listing must include the final publication.
            listing = ssh("cd /tmp/studio && (cat exit 2>/dev/null || echo working) && ls -1 frames").splitlines()
            ready = [f for f in listing[1:] if re.fullmatch(r"-?\d+\.(png|exr)", f) and Path(f).stem in expected]
            for offset in range(0, len(ready), 32):
                batch = ready[offset:offset + 32]
                transfer = job / "download.tar"
                command = "cd /tmp/studio/frames && tar -cf - -- " + " ".join(batch)
                with transfer.open("wb") as stream:
                    subprocess.run(["ssh", *options, "-p", port, host, command], stdout=stream,
                                   check=True, timeout=remaining())
                with tarfile.open(transfer) as files:
                    for filename in batch:
                        partial = frames / ("copy-" + filename)
                        with files.extractfile(filename) as src, partial.open("wb") as dst:
                            shutil.copyfileobj(src, dst)
                        subprocess.run([home.tool("ffmpeg"), "-v", "error", "-xerror", "-i", str(partial), "-frames:v", "1",
                                        "-f", "null", "-"], check=True, timeout=remaining())
                        partial.replace(frames / filename)
                        expected.remove(Path(filename).stem)
                print(f"{len(expected)} frames remaining.", file=sys.stderr, flush=True)
            if listing[0] != "working":
                if listing[0] != "0" or expected:
                    raise RuntimeError("Remote render incomplete: " + ssh("tail -c 1800 /tmp/studio/render.log"))
                break
            if expected:
                time.sleep(min(5, remaining()))
    finally:
        release(job, name)
    return {"frames": str(frames), "count": last - first + 1, "released": True}


def run(args, blender_binary):
    if args.action == "offers":
        return offers()
    if args.action == "render":
        return render(args, blender_binary)
    name = (args.job / "name").read_text().strip()
    if not re.fullmatch(r"studio-[0-9a-f]{32}", name):
        raise ValueError("Invalid job identity.")
    if args.action == "release":
        return release(args.job, name)
    pod = find_pod(args.job, name)
    return {"name": name, "status": pod["status"] if pod else "not found",
            "usd_per_compute_hour": pod["cost"] if pod else None}
