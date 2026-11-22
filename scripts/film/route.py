"""A walk: from a route (a line on the ground, where to stop and what to look at) to the camera of every frame.

    python route.py route.json walk.json [--poses poses.json]

The route file:
    fps      frames a second (60)
    loop     true when the walk ends where it began and the film goes round (false)
    eye      camera height above the line, metres (1.62)
    sensor   sensor width, mm (36);  lens: the lens when a key names none, mm (28)
    speed    mean walking speed between stops, m/s (1.3), unless "legs" gives the seconds of each stretch
    seconds  optional: how long the whole film lasts; the walking time is stretched or squeezed to fit (standing is kept)
    line     [[x, y, z], ...] points on the ground along the route, in walking order
    keys     where the look is set, in walking order. Each key:
               at      metres along the line from its first point, or  near: [x, y]  (the closest point of the line)
               look    degrees to the left of the walking direction (negative: to the right), or
               target  [x, y, z] a point to look at (then pitch follows it too, unless pitch is given)
               pitch   degrees up;  lens: mm;  shift: upward lens shift (keeps buildings upright);  ev: exposure offset
               stop    an id: the walk comes to rest here;  name: what the page calls the place, "x" or ["en", "zh"]
               hold    seconds to stand at the stop;  pan: degrees the look turns left while standing
    legs     optional: seconds for each stretch between one stop and the next (a loop has one more: back to the start)
    must_see {stop id: [object name or [x, y, z], ...]}  what each stop is there to show (checked by `likeness sight`)
    spots    [{id, object}] flat things the visitor can click in the finished page

Between two stops the walk eases out, cruises and eases in; look, pitch, lens and shift follow the keys on the way."""
import json
import math
import sys


def spline(points, closed, step=0.05):
    """A smooth line through the points (centripetal Catmull-Rom), as samples about `step` metres apart."""
    pts = [tuple(float(v) for v in (list(p) + [0.0])[:3]) for p in points]
    pts = [p for i, p in enumerate(pts) if i == 0 or math.dist(p, pts[i - 1]) > 1e-6]
    if closed and math.dist(pts[0], pts[-1]) < 1e-6:
        pts.pop()
    if len(pts) < 2:
        raise ValueError("The line needs at least two different points.")
    n = len(pts)

    def at(i):
        if closed:
            return pts[i % n]
        if i < 0:
            return tuple(2 * a - b for a, b in zip(pts[0], pts[1]))
        if i >= n:
            return tuple(2 * a - b for a, b in zip(pts[-1], pts[-2]))
        return pts[i]

    out = []
    for i in range(n if closed else n - 1):
        p0, p1, p2, p3 = at(i - 1), at(i), at(i + 1), at(i + 2)
        t0 = 0.0
        t1 = t0 + max(1e-6, math.dist(p0, p1)) ** 0.5
        t2 = t1 + max(1e-6, math.dist(p1, p2)) ** 0.5
        t3 = t2 + max(1e-6, math.dist(p2, p3)) ** 0.5
        count = max(2, int(math.ceil(math.dist(p1, p2) / step)))
        for k in range(count):
            t = t1 + (t2 - t1) * k / count

            def mix(a, b, ta, tb):
                return tuple(((tb - t) * x + (t - ta) * y) / (tb - ta) for x, y in zip(a, b))
            a1, a2, a3 = mix(p0, p1, t0, t1), mix(p1, p2, t1, t2), mix(p2, p3, t2, t3)
            b1, b2 = mix(a1, a2, t0, t2), mix(a2, a3, t1, t3)
            out.append(mix(b1, b2, t1, t2))
    out.append(pts[0] if closed else pts[-1])
    return out


class Line:
    """The route on the ground: position and walking direction at a distance along it."""

    def __init__(self, points, closed):
        self.closed = closed
        self.pts = spline(points, closed)
        self.s = [0.0]
        for a, b in zip(self.pts, self.pts[1:]):
            self.s.append(self.s[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
        self.length = self.s[-1]
        # the walking direction, as an angle that never jumps, smoothed over a metre so the camera does not twitch
        raw = []
        for i in range(len(self.pts) - 1):
            a, b = self.pts[i], self.pts[i + 1]
            yaw = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
            if raw:
                yaw += 360.0 * round((raw[-1] - yaw) / 360.0)
            raw.append(yaw)
        # a closed line turns through a whole circle (or none, for a figure of eight) by the time it is back at its start
        self.turn = 360.0 * round((raw[-1] - raw[0]) / 360.0) if closed else 0.0
        raw.append(raw[0] + self.turn if closed else raw[-1])
        half = max(1, int(0.5 / max(1e-6, self.length / (len(self.pts) - 1))))
        m = len(raw) - 1
        self.yaw = []
        for i in range(len(raw)):
            total = 0.0
            for k in range(i - half, i + half + 1):
                if closed:
                    total += raw[k % m] + self.turn * (k // m)
                else:
                    total += raw[max(0, min(m, k))]
            self.yaw.append(total / (2 * half + 1))

    def index(self, s):
        lo, hi = 0, len(self.s) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.s[mid] <= s:
                lo = mid
            else:
                hi = mid
        return lo, (s - self.s[lo]) / max(1e-9, self.s[hi] - self.s[lo])

    def point(self, s):
        laps = math.floor(s / self.length) if self.closed else 0
        s = s - laps * self.length if self.closed else max(0.0, min(self.length, s))
        i, f = self.index(s)
        a, b = self.pts[i], self.pts[min(i + 1, len(self.pts) - 1)]
        pos = tuple(x + (y - x) * f for x, y in zip(a, b))
        yaw = self.yaw[i] + (self.yaw[min(i + 1, len(self.yaw) - 1)] - self.yaw[i]) * f + laps * self.turn
        return pos, yaw

    def nearest(self, xy):
        best = min(range(len(self.pts)), key=lambda i: math.hypot(self.pts[i][0] - xy[0], self.pts[i][1] - xy[1]))
        return self.s[best]


def hermite(p0, p1, m0, m1, u):
    u2, u3 = u * u, u * u * u
    return (2 * u3 - 3 * u2 + 1) * p0 + (u3 - 2 * u2 + u) * m0 + (-2 * u3 + 3 * u2) * p1 + (u3 - u2) * m1


def build(route, line=None):
    fps = float(route.get("fps", 60))
    loop = bool(route.get("loop", False))
    eye = float(route.get("eye", 1.62))
    sensor = float(route.get("sensor", 36.0))
    lens0 = float(route.get("lens", 28.0))
    line = line or Line(route["line"], loop)
    notes = []

    # ---- the keys, in order along the line
    keys = []
    for raw in route["keys"]:
        k = dict(raw)
        k["s"] = float(line.nearest(k["near"]) if "near" in k else k.get("at", 0.0))
        keys.append(k)
    if any(b["s"] < a["s"] - 1e-6 for a, b in zip(keys, keys[1:])):
        raise ValueError("The keys must follow the walking order: each one further along the line than the one before.")
    if not keys[0].get("stop"):
        keys[0]["stop"] = "start"
    if not loop and not keys[-1].get("stop"):
        keys[-1]["stop"] = "end"
    if loop:
        closing = {k: v for k, v in keys[0].items() if k not in ("hold", "pan")}
        closing["s"] = keys[0]["s"] + line.length
        closing["closing"] = True
        keys.append(closing)

    def settle(k, look_before):
        """Look and pitch at a key, from its target if it has one. The look is kept within half a turn of the one before."""
        pos, yaw = line.point(k["s"])
        if "target" in k:
            t = [float(v) for v in k["target"]]
            look = math.degrees(math.atan2(t[1] - pos[1], t[0] - pos[0])) - yaw
            pitch = math.degrees(math.atan2(t[2] - (pos[2] + eye), math.hypot(t[0] - pos[0], t[1] - pos[1])))
        else:
            look, pitch = float(k.get("look", 0.0)), 0.0
        if look_before is None:
            look = (look + 180.0) % 360.0 - 180.0
        else:
            look += 360.0 * round((look_before - look) / 360.0)
        return look, float(k.get("pitch", pitch))

    rows, look_before = [], None   # s, look, pitch, lens, shift, ev, name ("x" a stop, "x+" the end of standing there)
    for k in keys:
        look, pitch = settle(k, look_before)
        row = dict(s=k["s"], look=look, pitch=pitch, lens=float(k.get("lens", lens0)), shift=float(k.get("shift", 0.0)),
                   ev=float(k.get("ev", 0.0)), name=k.get("stop"), label=k.get("name"), hold=0.0, closing=k.get("closing", False))
        rows.append(row)
        look_before = look
        if k.get("stop") and float(k.get("hold", 0.0)) > 0:
            row["hold"] = float(k["hold"])
            look_before = look + float(k.get("pan", 0.0))
            rows.append(dict(row, look=look_before, name=k["stop"] + "+", hold=0.0))

    # ---- how long each stretch takes
    stop_rows = [i for i, r in enumerate(rows) if r["name"]]
    travel = [(a, b) for a, b in zip(stop_rows, stop_rows[1:]) if not rows[b]["name"].endswith("+")]
    given = route.get("legs")
    if given is not None and len(given) != len(travel):
        raise ValueError(f"legs has {len(given)} values, but the walk has {len(travel)} stretches between stops.")
    speed = float(route.get("speed", 1.3))
    seconds = [float(v) for v in given] if given else [max(1.0, (rows[b]["s"] - rows[a]["s"]) / speed) for a, b in travel]
    holds = sum(r["hold"] for r in rows)
    if route.get("seconds"):
        if float(route["seconds"]) <= holds:
            raise ValueError(f"seconds is {route['seconds']}, but standing at the stops alone takes {holds}.")
        fit = (float(route["seconds"]) - holds) / sum(seconds)
        seconds = [v * fit for v in seconds]
    whole = math.ceil(sum(seconds) + holds - 1e-6)
    if abs(whole - sum(seconds) - holds) > 1e-6:   # the film is cut into one-second pieces
        grow = (whole - holds) / sum(seconds)
        seconds = [v * grow for v in seconds]
        notes.append(f"Stretched the walking time by {100 * (grow - 1):.1f}% so that the film lasts a whole number of seconds ({whole}).")
    legs, times, t0, n = [], [0.0] * len(rows), 0.0, 0
    for a, b in zip(stop_rows, stop_rows[1:]):
        pause = rows[b]["name"].endswith("+")
        T = rows[a]["hold"] if pause else seconds[n]
        n += 0 if pause else 1
        s0, s1 = rows[a]["s"], rows[b]["s"]
        legs.append((t0, T, s0, s1))
        for i in range(a, b + 1):
            if s1 - s0 < 1e-6:
                times[i] = t0 + (T if i == b else 0.0)
            else:
                f = (rows[i]["s"] - s0) / (s1 - s0)
                times[i] = t0 + T * (0.5 - math.sin(math.asin(1 - 2 * f) / 3))   # when 3u^2 - 2u^3 reaches f
        t0 += T
    total = t0

    def s_at(t):
        for ta, T, s0, s1 in legs:
            if t <= ta + T + 1e-9:
                u = max(0.0, min(1.0, (t - ta) / T)) if T > 0 else 1.0
                return s0 + (s1 - s0) * u * u * (3 - 2 * u)
        return legs[-1][3]

    def pausing(i):
        name = rows[i]["name"]
        return bool(name) and (name.endswith("+") or (i + 1 < len(rows) and rows[i + 1]["name"] == name + "+"))

    def channel(field, flow):
        """Everything comes to rest at a stop; with flow, the value turns at one steady rate while the walker stands (the pan)."""
        values = [r[field] for r in rows]
        last = len(values) - 1
        tang = []
        for i in range(len(values)):
            if flow and pausing(i):
                j = i if rows[i]["name"].endswith("+") else i + 1   # the key that ends the standing
                tang.append((values[j] - values[j - 1]) / max(1e-9, times[j] - times[j - 1]))
            elif i in (0, last) or rows[i]["name"] or times[i + 1] - times[i - 1] < 1e-9:
                tang.append(0.0)
            else:
                tang.append((values[i + 1] - values[i - 1]) / (times[i + 1] - times[i - 1]))

        def at(t):
            i = max(0, min(last - 1, next((k for k in range(last) if times[k + 1] >= t), last - 1)))
            dt = times[i + 1] - times[i]
            if dt < 1e-9:
                return values[i + 1]
            return hermite(values[i], values[i + 1], tang[i] * dt, tang[i + 1] * dt, (t - times[i]) / dt)
        return at

    c_look = channel("look", True)
    c_pitch, c_lens, c_shift, c_ev = (channel(f, False) for f in ("pitch", "lens", "shift", "ev"))

    def pose(t):
        s = s_at(t)
        pos, yaw = line.point(s)
        return {"t": round(t, 4), "pos": [round(pos[0], 4), round(pos[1], 4), round(pos[2] + eye, 4)], "yaw": round(yaw + c_look(t), 3),
                "pitch": round(c_pitch(t), 3), "lens": round(c_lens(t), 2), "shift": round(max(0.0, c_shift(t)), 4),
                "ev": round(c_ev(t), 3), "s": round(s, 3)}

    # A whole number of seconds of frames. A loop's next frame would be its first again; a walk that ends has all but come
    # to rest by its last frame.
    count = int(round(total * fps))
    frames = [pose(f / fps) for f in range(count)]
    fastest = max((math.dist(a["pos"], b["pos"]) * fps for a, b in zip(frames, frames[1:])), default=0.0)
    turning = max((abs(b["yaw"] - a["yaw"]) * fps for a, b in zip(frames, frames[1:])), default=0.0)

    stops = []
    for i in stop_rows:
        r = rows[i]
        if r["name"].endswith("+") or r["closing"]:
            continue
        t = times[i] + r["hold"] / 2
        label = r["label"] or r["name"]
        stops.append({"id": r["name"], "name": label if isinstance(label, list) else [label, label], "t": round(t, 4),
                      "frame": min(count - 1, int(round(t * fps)))})
    stretches = []
    for (a, b), T in zip(travel, seconds):
        metres = rows[b]["s"] - rows[a]["s"]
        stretches.append({"from": rows[a]["name"].rstrip("+"), "to": rows[b]["name"], "seconds": round(T, 2), "metres": round(metres, 1),
                          "mean_m_s": round(metres / T, 2), "peak_m_s": round(1.5 * metres / T, 2)})
        if metres / T > 2.0:
            notes.append(f"{stretches[-1]['from']} to {stretches[-1]['to']}: {metres / T:.1f} m/s is faster than a walk "
                         f"(about 1.3 m/s). Give the stretch more seconds, or accept that it reads as a ride.")
    marks = sorted({min(count - 1, int(round(t * fps))) for t in (times[:-1] if loop else times)} |   # a loop's last key is its first again
                   {min(count - 1, int(round((a + b) / 2 * fps))) for a, b in zip(times, times[1:]) if b - a > 0.5})
    walk = {"fps": fps, "duration": round(total, 4), "loop": loop, "sensor": sensor, "lens": lens0, "eye": eye,
            "metres": round((rows[-1]["s"] - rows[0]["s"]), 1), "frames": frames, "stops": stops, "check_frames": marks,
            "must_see": route.get("must_see", {}), "spots": route.get("spots", [])}
    report = {"frames": count, "seconds": round(total, 2), "metres": walk["metres"], "fastest_m_s": round(fastest, 2),
              "fastest_turn_deg_s": round(turning, 1), "stretches": stretches,
              "stops": [(s["id"], s["frame"]) for s in stops], "check_frames": marks, "notes": notes}
    return walk, report, rows, line


def poses(route, rows, line, along=4.0, turn=12.0):
    """Trial views round every stop: a few metres before and after it, looking a little left and right. To choose stops from."""
    eye, lens0 = float(route.get("eye", 1.62)), float(route.get("lens", 28.0))
    frames, notes = [], []
    for r in rows:
        if not r["name"] or r["name"].endswith("+") or r["closing"]:
            continue
        for ds in (-along, 0.0, along):
            for dl in (-turn, 0.0, turn):
                pos, yaw = line.point(r["s"] + ds)
                notes.append(f"{r['name']}: {ds:+.0f} m along, look {r['look'] + dl:.0f}")
                frames.append({"t": len(frames), "pos": [round(pos[0], 4), round(pos[1], 4), round(pos[2] + eye, 4)],
                               "yaw": round(yaw + r["look"] + dl, 3), "pitch": r["pitch"], "lens": r["lens"], "shift": r["shift"],
                               "ev": r["ev"], "s": round(r["s"] + ds, 3)})
    return {"fps": 1.0, "duration": len(frames), "loop": False, "sensor": float(route.get("sensor", 36.0)), "lens": lens0, "eye": eye,
            "frames": frames, "stops": [], "notes": notes, "check_frames": list(range(len(frames))), "must_see": {}, "spots": []}


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    route = json.load(open(argv[0], encoding="utf-8"))
    walk, report, rows, line = build(route)
    json.dump(walk, open(argv[1], "w", encoding="utf-8"), ensure_ascii=False)
    if "--poses" in argv:
        trial = poses(route, rows, line)
        target = argv[argv.index("--poses") + 1]
        json.dump(trial, open(target, "w", encoding="utf-8"), ensure_ascii=False)
        report["poses"] = {"file": target, "views": trial["notes"]}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
