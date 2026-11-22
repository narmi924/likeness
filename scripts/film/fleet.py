"""Several rented Runpod GPUs rendering one film together: rent, load, test, share out, watch, fetch, close.

  likeness fleet up    <job dir> <how many>   rent single-card machines and make each ready (Blender, data, build, test)
  likeness fleet go    <job dir>              share the pieces out and start a worker on every machine
  likeness fleet watch <job dir>              fetch finished pieces, hand work to idle machines, close each machine once all it
                                          made is here; close everything when the film is complete or a limit is reached
  likeness fleet stat  <job dir>              one line per machine, and what the job has cost so far
  likeness fleet down  <job dir>              close every machine of the job now (nothing is fetched)
  likeness fleet frames <job dir> <out dir>   the fetched pieces back into numbered PNG frames (f_00000.png)

<job dir>/fleet.json (see references/film.md for a full example):
  runpod_key   file holding the API key (never printed)
  ssh_key      private key whose .pub is registered in the Runpod account
  cards        GPU ids to try, in order ("NVIDIA GeForce RTX 4090", ...); single-card machines only
  regions      optional data centre ids to rent in, e.g. ["EU-RO-1"] (`likeness tools cloud offers` lists them per card);
               a machine placed anywhere else is released at once
  max_rate     optional USD an hour for one card; a dearer machine is released at once
  blender      Blender version to install on each machine, e.g. "5.2.4"
  disk         container disk in GB (default 120)
  upload       {"name in /root/work": "local path"}: sent to the first machine from here, copied machine to machine after
  build        optional command run in /root/work once the data is there (e.g. building the scene)
  test         optional command rendering a few frames; run twice, and a machine that fails either run is closed
  render       command rendering frames {first}..{last} into {frames}/f_NNNNN.png or f_NNNN.png (run in /root/work)
  frames       [first, last]; piece: frames a piece (60); fps; crf: quality of the pieces (6, near lossless; 0 for exact)
  hours, budget_usd   hard limits for the whole job, counted from the first rental and over every machine it ever rented.
               A guard process starts with the first rental and releases every machine at either limit, whether or not
               anything else is still running.
The job dir keeps leases.json (every machine rented: when, where, at what rate, when released), machines.txt (those in use),
fleet.log and pieces/NNN.mkv."""
import hashlib, io, json, os, shutil, subprocess, sys, time, uuid
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import home  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=30", "-o", "BatchMode=yes"]
DISK_USD_GB_MONTH = 0.10        # Runpod's list price for container disk on a running machine
PEER_PATIENCE = 300             # seconds a machine may wait for its copy from another machine before it gets the data from here


class Limit(Exception):
    pass


def short(card):
    return card.replace("NVIDIA ", "").replace("GeForce ", "").replace(" ", "")


def private_key(path):
    """Windows OpenSSH refuses a key file that other accounts can read, which is common for files in a project folder.
    Use a copy only this user can read, kept in the Likeness home."""
    if os.name != "nt":
        return path
    copy = home.home() / "cache" / "fleet-ssh" / hashlib.sha1(os.path.abspath(path).encode()).hexdigest()[:10] / "id"
    if not copy.exists() or copy.stat().st_mtime < os.path.getmtime(path):
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, copy)
        subprocess.run(["icacls", str(copy), "/inheritance:r", "/grant:r", f"{os.environ.get('USERNAME', '')}:F"], capture_output=True)
    return str(copy).replace("\\", "/")


class Job:
    def __init__(self, folder):
        self.dir = os.path.abspath(folder)
        self.c = json.load(open(os.path.join(self.dir, "fleet.json"), encoding="utf-8"))
        self.key = open(self.c["runpod_key"], encoding="utf-8-sig").read().strip()
        self.pieces = os.path.join(self.dir, "pieces"); os.makedirs(self.pieces, exist_ok=True)
        for k, v in (("piece", 60), ("fps", 60), ("hours", 3), ("budget_usd", 20), ("disk", 120), ("crf", 6)):
            self.c.setdefault(k, v)
        self.ssh_key = private_key(self.c["ssh_key"]) if os.path.exists(self.c["ssh_key"]) else self.c["ssh_key"]
        self.leases_file = os.path.join(self.dir, "leases.json")
        self.warned = set()
        # machines come and go on reused addresses: keep their host keys in the job, apart from the user's own list
        SSH_OPTS[:0] = ["-o", "UserKnownHostsFile=" + os.path.join(self.dir, "known_hosts").replace("\\", "/")]

    def log(self, *a):
        line = time.strftime("%H:%M:%S ") + " ".join(map(str, a))
        print(line, flush=True)
        with open(os.path.join(self.dir, "fleet.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ---------------------------------------------------------------- Runpod
    def api(self, method, path, body=None):
        req = Request("https://api.runpod.io" + path, method=method,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=90) as r:
                raw = r.read().decode()
                return json.loads(raw) if raw else {}
        except HTTPError as e:
            return {"error": e.code, "body": e.read().decode(errors="replace")[:300]}
        except Exception as e:
            return {"error": str(e)}

    def account(self):
        """Every machine on the account, all pages."""
        pods, path = [], "/v2/pods"
        while path:
            page = self.api("GET", path)
            pods += page.get("pods") or []
            cursor = (page.get("pagination") or {}).get("nextCursor")
            path = f"/v2/pods?cursor={cursor}" if cursor else None
        return pods

    # ---------------------------------------------------------------- what was rented, and what it costs
    def leases(self):
        for attempt in range(10):                 # the guard and the main run share this file
            try:
                if not os.path.exists(self.leases_file):
                    return {"tag": uuid.uuid4().hex[:6], "pods": {}}
                with open(self.leases_file, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                time.sleep(0.3)
        raise RuntimeError(f"cannot read {self.leases_file}")

    def keep(self, d):
        part = f"{self.leases_file}.{os.getpid()}.part"
        with open(part, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
        for attempt in range(10):
            try:
                os.replace(part, self.leases_file); return
            except OSError:
                time.sleep(0.3)
        raise RuntimeError(f"cannot write {self.leases_file}")

    def open_leases(self):
        return {pid: p for pid, p in self.leases()["pods"].items() if not p.get("released")}

    def spent(self):
        now = time.time()
        return sum(((p.get("released") or now) - p["created"]) * p["rate"] / 3600 for p in self.leases()["pods"].values())

    def deadline(self):
        return self.leases().get("deadline") or time.time() + self.c["hours"] * 3600

    def rent(self, card):
        d = self.leases(); now = time.time()
        if not any(not p.get("released") for p in d["pods"].values()) and now > d.get("deadline", 0):
            d["deadline"] = now + self.c["hours"] * 3600           # a new run of the job: the clock starts at its first rental
        if now > d["deadline"] - 300 or self.spent() >= self.c["budget_usd"]:
            raise Limit(f"no more rentals: {self.spent():.2f} of {self.c['budget_usd']} USD spent, "
                        f"{(d['deadline'] - now) / 60:.0f} minutes left")
        name = f"likeness-{d['tag']}-{len(d['pods']) + 1}"
        body = {"name": name, "cloud": "SECURE", "gpu": {"id": card, "count": 1}, "templateId": "runpod-torch-v280",
                "disk": self.c["disk"], "startSsh": True}
        if self.c.get("regions"):
            body["dataCenterIds"] = list(self.c["regions"])
        got = self.api("POST", "/v2/pods", body)
        if "id" not in got:
            # the reply can be lost after the machine was made: look for it by its name before anything else
            got = next((p for p in self.account() if p.get("name") == name), None) or got
        if "id" not in got:
            self.log("no", short(card), "to rent:", got.get("error"), str(got.get("body", ""))[:160])
            return None
        rate = float(got.get("cost") or got.get("costPerHr") or 0)
        region = got.get("dataCenterId") or (self.api("GET", f"/v2/pods/{got['id']}").get("dataCenterId") or "")
        d["pods"][got["id"]] = {"name": name, "card": short(card), "region": region, "created": now,
                                "rate": round((rate or 2.0) + self.c["disk"] * DISK_USD_GB_MONTH / 730, 4)}
        d.setdefault("budget_usd", self.c["budget_usd"])
        self.keep(d)
        self.start_guard()
        self.log("rented", name, short(card), f"{rate:.2f} USD/h" if rate else "rate not reported, counted as 2 USD/h", region)
        if self.c.get("regions") and region not in self.c["regions"]:
            self.release(got["id"], f"(placed in {region or 'an unknown region'}, not in {self.c['regions']})"); return None
        if self.c.get("max_rate") and rate > self.c["max_rate"]:
            self.release(got["id"], f"({rate} USD/h is above max_rate)"); return None
        return dict(name=name, id=got["id"], card=short(card), rate=rate)

    def release(self, pid, why=""):
        """Delete a machine and wait until Runpod itself says it is gone."""
        for attempt in range(15):
            p = self.api("GET", f"/v2/pods/{pid}")
            if p.get("error") == 404 or p.get("status") == "TERMINATED":
                break
            if attempt % 3 == 0:
                self.api("DELETE", f"/v2/pods/{pid}")
            time.sleep(4)
        else:
            self.log(pid, "release NOT confirmed by Runpod: check the account now"); return False
        d = self.leases()
        if pid in d["pods"] and not d["pods"][pid].get("released"):
            d["pods"][pid]["released"] = time.time(); self.keep(d)
        self.save([x for x in self.machines() if x["id"] != pid])
        self.log(d["pods"].get(pid, {}).get("name", pid), "released", why)
        return True

    def close(self, r, why=""):
        self.release(r["id"], why)

    def close_all(self, why=""):
        for pid in self.open_leases():
            self.release(pid, why)
        tag = f"likeness-{self.leases()['tag']}-"
        left = [p for p in self.account() if (p.get("name") or "").startswith(tag) and p.get("status") != "TERMINATED"]
        for p in left:
            self.release(p["id"], why)
        still = [p["name"] for p in self.account() if (p.get("name") or "").startswith(tag) and p.get("status") != "TERMINATED"]
        self.log(f"spent about {self.spent():.2f} USD; machines of this job still on the account:", still or "none")

    # ---------------------------------------------------------------- the guard
    def start_guard(self):
        beat = os.path.join(self.dir, "guard.beat")
        if os.path.exists(beat) and time.time() - os.path.getmtime(beat) < 60:
            return
        with open(beat, "w") as f:
            f.write("starting")
        out = open(os.path.join(self.dir, "guard.log"), "ab")
        args = [sys.executable, os.path.abspath(__file__), "guard", self.dir]
        if os.name == "nt":
            # its own process, outside the agent's process tree when Windows allows it, so it outlives the session
            for flags in (0x01000000 | 0x00000008 | 0x00000200, 0x00000008 | 0x00000200):
                try:
                    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=out, close_fds=True, creationflags=flags); break
                except OSError:
                    continue
        else:
            subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=out, close_fds=True, start_new_session=True)

    def machines(self):
        p = os.path.join(self.dir, "machines.txt")
        rows = []
        if os.path.exists(p):
            for line in io.open(p, encoding="utf-8"):
                f = line.split()
                if len(f) >= 6:
                    rows.append(dict(name=f[0], id=f[1], host=f[2], port=f[3], card=f[4], rate=float(f[5]), since=float(f[6]) if len(f) > 6 else time.time()))
        return rows

    def save(self, rows):
        io.open(os.path.join(self.dir, "machines.txt"), "w", encoding="utf-8", newline="\n").write(
            "".join(f"{r['name']} {r['id']} {r['host']} {r['port']} {r['card']} {r['rate']} {r['since']:.0f}\n" for r in rows))

    def ssh(self, r, cmd, timeout=120, stdin=None):
        args = ["ssh", "-i", self.ssh_key, "-p", str(r["port"]), *SSH_OPTS, f"root@{r['host']}", cmd]
        try:
            p = subprocess.run(args, capture_output=True, timeout=timeout, input=stdin if stdin is not None else b"")
        except subprocess.TimeoutExpired:
            return 124, ""
        if p.returncode == 255:                  # ssh itself failed, not the command: say why, once per machine and message
            msg = p.stderr.decode(errors="replace").strip().splitlines()[-1:] or ["no message"]
            if (r["name"], msg[0]) not in self.warned:
                self.warned.add((r["name"], msg[0])); self.log(r["name"], "ssh:", msg[0])
        return p.returncode, p.stdout.decode(errors="replace").strip()


def guard(job):
    """Runs on its own from the first rental. Releases every machine of the job at the time or budget limit."""
    beat = os.path.join(job.dir, "guard.beat")
    while True:
        try:
            with open(beat, "w") as f:
                f.write(str(time.time()))
            if not job.open_leases():
                return
            if time.time() > job.deadline() or job.spent() > job.c["budget_usd"]:
                job.log(f"guard: limit reached ({job.spent():.2f} USD), releasing every machine")
                job.close_all("(guard: time or budget limit)")
                if not job.open_leases():
                    return
        except Exception as e:                    # never stop guarding because of one bad round
            print(time.strftime("%H:%M:%S"), "guard:", e, flush=True)
        time.sleep(20)


def send(job, r):
    """The upload, from here to one machine."""
    job.log(r["name"], "receives the data from here")
    tar = subprocess.Popen(["tar", "cf", "-", *[x for path in job.c["upload"].values()
                                                for x in ("-C", os.path.dirname(os.path.abspath(path)) or ".", os.path.basename(os.path.abspath(path)))]],
                           stdout=subprocess.PIPE)
    p = subprocess.run(["ssh", "-i", job.ssh_key, "-p", str(r["port"]), *SSH_OPTS, f"root@{r['host']}",
                        "mkdir -p /root/work && cd /root/work && tar xf - --no-same-owner"], stdin=tar.stdout, capture_output=True)
    tar.stdout.close(); tar.wait()
    if p.returncode != 0 or tar.returncode != 0:
        job.log(r["name"], "upload failed:", p.stderr.decode(errors="replace").strip()[-300:]); return False
    for name, path in job.c["upload"].items():          # names on the machine follow the upload map
        base = os.path.basename(os.path.abspath(path))
        if base != name:
            job.ssh(r, f"cd /root/work && mv '{base}' '{name}'")
    job.ssh(r, "echo ok > /root/data.done")
    return True


def up(job, count):
    missing = [p for p in job.c.get("upload", {}).values() if not os.path.exists(p)]
    if missing:
        raise FileNotFoundError("upload paths not found: " + ", ".join(missing))
    made = []
    try:
        for card in job.c["cards"] * 2:
            while len(made) < count:
                m = job.rent(card)
                if not m:
                    break
                made.append(m)
    except Limit as e:
        job.log(e)
    if len(made) < count:
        job.log(f"only {len(made)} of {count} machines could be rented")
    ready = []
    for m in made:
        for i in range(40):
            p = job.api("GET", f"/v2/pods/{m['id']}")
            s = (p.get("ssh") or {}).get("direct") or {}
            if s.get("host"):
                ready.append(dict(m, host=s["host"], port=str(s["port"]), since=time.time())); break
            time.sleep(10)
        else:
            job.release(m["id"], "(never became reachable)")
    job.save(job.machines() + ready)
    v = job.c["blender"]; major = ".".join(v.split(".")[:2])
    setup = ("export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq libxi6 libxxf86vm1 libxfixes3 libxrender1 libgl1 "
             "libxkbcommon0 libsm6 libice6 libegl1 libx11-6 xz-utils ffmpeg python3 > /root/apt.log 2>&1; cd /root && "
             f"curl -sL -o b.tar.xz https://download.blender.org/release/Blender{major}/blender-{v}-linux-x64.tar.xz && tar xf b.tar.xz && "
             f"mv blender-{v}-linux-x64 blender && rm b.tar.xz && ln -sf /root/blender/blender /usr/local/bin/blender && blender --version | head -1 > /root/setup.done")
    for r in ready:
        for i in range(12):
            code, out = job.ssh(r, f"mkdir -p /root/work/.likeness && (nohup bash -c '{setup}' > /root/setup.log 2>&1 &); echo started")
            if "started" in out:
                break
            time.sleep(15)
    # the data: from here to the first machine that has none, then machine to machine (much faster than from home)
    every = job.machines()
    source = next((r for r in every if r not in ready and job.ssh(r, "cat /root/data.done")[1] == "ok"), None)
    names = " ".join(job.c["upload"].keys())
    peer = {}                                            # machine -> (source, when its copy started)
    for r in ready:
        if source is None:
            if send(job, r):
                source = r
                job.ssh(source, "[ -f /root/.ssh/likeness ] || ssh-keygen -q -t ed25519 -N '' -f /root/.ssh/likeness")
            else:
                job.close(r, "(the data could not be sent)")
        else:
            pub = job.ssh(source, "[ -f /root/.ssh/likeness ] || ssh-keygen -q -t ed25519 -N '' -f /root/.ssh/likeness; cat /root/.ssh/likeness.pub")[1]
            job.ssh(r, f"mkdir -p /root/.ssh && echo '{pub}' >> /root/.ssh/authorized_keys")
            job.ssh(source, f"cd /root/work && (nohup bash -c \"tar cf - {names} | ssh -i /root/.ssh/likeness -p {r['port']} -o StrictHostKeyChecking=no -o LogLevel=ERROR "
                            f"root@{r['host']} 'tar xf - --no-same-owner -C /root/work && echo ok > /root/data.done'\" > /root/copy-{r['name']}.log 2>&1 &); echo copying")
            peer[r["name"]] = (source, time.time())
            job.log(r["name"], "receives the data from", source["name"])
    # Blender, data, build, test
    worker = open(os.path.join(HERE, "fleet_worker.py"), "rb").read()
    pending = {r["name"]: "wait" for r in ready if any(x["id"] == r["id"] for x in job.machines())}
    for i in range(120):
        for r in ready:
            st = pending.get(r["name"])
            if st == "wait":
                code, out = job.ssh(r, "ls /root/setup.done /root/data.done 2>/dev/null | wc -l")
                if out.strip() == "2":
                    job.ssh(r, "cat > /root/work/.likeness/worker.py", stdin=worker)
                    build = job.c.get("build") or "true"
                    # the test renders into its own folder, twice; {frames}, {first}, {last} as in the render command
                    a = job.c["frames"][0]
                    test = (job.c.get("test") or "true").format(frames="/root/work/.likeness/test", first=a, last=a + 2, work="/root/work")
                    script = (f"cd /root/work\n({build}) > .likeness/build.log 2>&1 || {{ echo bad > .likeness/tested; exit; }}\necho ok > .likeness/built\n"
                              f"mkdir -p .likeness/test\n({test}) > .likeness/test1.log 2>&1 && ({test}) > .likeness/test2.log 2>&1 && "
                              f"! grep -q 'Illegal address' .likeness/test1.log .likeness/test2.log && echo ok > .likeness/tested || echo bad > .likeness/tested\n")
                    job.ssh(r, "cat > /root/work/.likeness/ready.sh", stdin=script.encode())
                    job.ssh(r, "cd /root/work && (nohup bash .likeness/ready.sh > /dev/null 2>&1 &)")
                    pending[r["name"]] = "testing"; job.log(r["name"], "building and testing")
                elif r["name"] in peer and time.time() - peer[r["name"]][1] > PEER_PATIENCE:
                    src = peer.pop(r["name"])[0]
                    job.log(r["name"], "no data from", src["name"], "yet:", job.ssh(src, f"tail -c 300 /root/copy-{r['name']}.log")[1] or "no message")
                    if not send(job, r):
                        pending.pop(r["name"]); job.close(r, "(the data could not be sent)")
            elif st == "testing":
                code, out = job.ssh(r, "cat /root/work/.likeness/tested 2>/dev/null")
                if out == "ok":
                    pending.pop(r["name"]); job.log(r["name"], "ready", r["card"])
                elif out == "bad":
                    pending.pop(r["name"]); job.close(r, "(failed its build or test; see .likeness/*.log there)")
        if not pending or time.time() > job.deadline():
            break
        time.sleep(20)
    for name in pending:
        r = next((x for x in job.machines() if x["name"] == name), None)
        if r:
            job.close(r, "(not ready in time)")


def job_file(job):
    return json.dumps({"render": job.c["render"], "frames": job.c["frames"], "piece": job.c["piece"], "fps": job.c["fps"], "crf": job.c["crf"]})


def all_pieces(job):
    a, b = job.c["frames"]; n = job.c["piece"]
    return list(range(a // n, b // n + 1))


def here(job):
    return {int(f[:3]) for f in os.listdir(job.pieces) if f.endswith(".mkv")}


def go(job):
    rows = job.machines()
    todo = [k for k in all_pieces(job) if k not in here(job)]
    share = [todo[i::len(rows)] for i in range(len(rows))]
    for r, mine in zip(rows, share):
        job.ssh(r, "cat > /root/work/.likeness/job.json", stdin=job_file(job).encode())
        job.ssh(r, "cd /root/work/.likeness && rm -rf now lock && printf '%s\\n' " + " ".join(map(str, mine)) + " > queue")
        job.ssh(r, "cd /root/work && (nohup python3 .likeness/worker.py /root/work > .likeness/worker.out 2>&1 &)")
        job.log(r["name"], r["card"], len(mine), "pieces")


def state(job, r):
    code, out = job.ssh(r, "cd /root/work/.likeness && echo Q $(cat queue 2>/dev/null | tr '\\n' ' ') && echo N $(ls now 2>/dev/null | tr '\\n' ' ') "
                           "&& echo D $(cat done 2>/dev/null | tr '\\n' ' ') && echo X $(cat failed 2>/dev/null | tr '\\n' ' ') "
                           "&& echo W $(pgrep -f '[w]orker.py /root/work' | wc -l)", 60)
    if code != 0 or "W " not in out:
        return None
    d = {}
    for line in out.splitlines():
        k, _, v = line.partition(" "); d[k] = [int(x) for x in v.split() if x.lstrip("-").isdigit()]
    return dict(queue=d.get("Q", []), now=d.get("N", []), done=d.get("D", []), failed=d.get("X", []), workers=(d.get("W") or [0])[0])


def fetch(job, r, pieces):
    for k in pieces:
        dst = os.path.join(job.pieces, f"{k:03d}.mkv")
        if os.path.exists(dst):
            continue
        code, size = job.ssh(r, f"stat -c %s /root/work/pieces/{k:03d}.mkv")
        if not size.isdigit():
            continue
        p = subprocess.run(["scp", "-q", "-i", job.ssh_key, "-P", r["port"], *SSH_OPTS, f"root@{r['host']}:/root/work/pieces/{k:03d}.mkv", dst + ".part"],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=1800)
        if p.returncode == 0 and os.path.getsize(dst + ".part") == int(size):
            os.replace(dst + ".part", dst)
        elif p.returncode != 0:
            job.log(r["name"], f"piece {k} not fetched:", p.stderr.decode(errors="replace").strip()[-200:])


def watch(job):
    want = set(all_pieces(job)); silent = {}; missing_before = set()
    while True:
        rows = job.machines()
        if not rows:
            job.log("no machines left; pieces here:", len(here(job) & want), "/", len(want))
            if job.open_leases():
                job.close_all("(nothing left in use)")
            return
        states = {}
        for r in rows:
            st = state(job, r); states[r["name"]] = st
            if st is None:
                silent[r["name"]] = silent.get(r["name"], 0) + 1
                if silent[r["name"]] >= 4:
                    job.close(r, "(does not answer)")
                continue
            silent[r["name"]] = 0
            fetch(job, r, st["done"])
        have = here(job) & want
        alive = [r for r in job.machines() if states.get(r["name"])]
        job.log(f"{len(have)}/{len(want)} pieces here, about {job.spent():.2f} USD so far |",
                " | ".join(f"{r['name']}: {len(states[r['name']]['queue'])} waiting, {states[r['name']]['now']} now" for r in alive))
        if want <= have:
            job.log("every piece is here"); job.close_all("(done)"); return
        if time.time() > job.deadline() or job.spent() > job.c["budget_usd"]:
            job.log("time or budget limit reached; keeping what is here"); job.close_all("(limit)"); return
        held = set(have)
        for r in alive:
            st = states[r["name"]]; held |= set(st["queue"]) | set(st["now"]) | set(st["done"])
        missing = want - held
        lost = sorted(missing & missing_before)      # missing twice running, not just between two reads
        missing_before = missing
        for r in alive:
            st = states[r["name"]]
            if st["workers"] == 0 and st["queue"]:
                job.ssh(r, "cd /root/work && rm -rf .likeness/now .likeness/lock && (nohup python3 .likeness/worker.py /root/work > .likeness/worker.out 2>&1 &)")
                job.log(r["name"], "worker restarted")
        idle = [r for r in alive if not states[r["name"]]["queue"] and not states[r["name"]]["now"] and states[r["name"]]["workers"] == 0]
        for r in idle:
            give = []
            if lost:
                give, lost = lost[:2], lost[2:]
            else:
                donor = max(alive, key=lambda x: len(states[x["name"]]["queue"]))
                q = states[donor["name"]]["queue"]
                if donor is not r and len(q) >= 2:
                    give = q[-(len(q) // 2):]
                    left = [x for x in q if x not in give]
                    job.ssh(donor, "cd /root/work/.likeness && while ! mkdir lock 2>/dev/null; do sleep 0.2; done; "
                                   "printf '%s\\n' " + " ".join(map(str, left)) + " | grep . > queue; rmdir lock")
                    states[donor["name"]]["queue"] = left
            if give:
                job.ssh(r, "cd /root/work/.likeness && printf '%s\\n' " + " ".join(map(str, give)) + " >> queue && cd /root/work && "
                           "(nohup python3 .likeness/worker.py /root/work > .likeness/worker.out 2>&1 &)")
                job.log(r["name"], "takes", give)
            elif set(states[r["name"]]["done"]) <= here(job):
                job.close(r, "(finished, all its pieces are here)")
        if lost and [r for r in alive if r in job.machines()]:
            r = min(alive, key=lambda x: len(states[x["name"]]["queue"]))
            job.ssh(r, "cd /root/work/.likeness && printf '%s\\n' " + " ".join(map(str, lost)) + " >> queue")
            job.log(r["name"], "takes lost pieces", lost)
        time.sleep(30)


def frames(job, out):
    os.makedirs(out, exist_ok=True)
    n = job.c["piece"]
    for name in sorted(os.listdir(job.pieces)):
        if name.endswith(".mkv"):
            k = int(name[:3]); first = max(job.c["frames"][0], k * n)
            subprocess.run([home.tool("ffmpeg"), "-v", "error", "-y", "-i", os.path.join(job.pieces, name), "-start_number", str(first),
                            "-pix_fmt", "rgb24", "-compression_level", "1", os.path.join(out, "f_%05d.png")], check=True)
    print("frames written to", out)


def main(argv):
    if len(argv) < 2 or argv[0] in ("-h", "--help"):
        print(__doc__); return
    cmd, job = argv[0], Job(argv[1])
    if cmd == "up":
        up(job, int(argv[2]))
    elif cmd == "go":
        go(job)
    elif cmd == "watch":
        watch(job)
    elif cmd == "down":
        job.close_all("(by hand)")
    elif cmd == "guard":
        guard(job)
    elif cmd == "frames":
        frames(job, argv[2])
    else:
        for r in job.machines():
            print(r["name"], r["card"], state(job, r))
        left = job.deadline() - time.time()
        print(f"spent about {job.spent():.2f} of {job.c['budget_usd']} USD; {max(0, left) / 60:.0f} minutes left; "
              f"{len(job.open_leases())} machines rented now")


if __name__ == "__main__":
    main(sys.argv[1:])
