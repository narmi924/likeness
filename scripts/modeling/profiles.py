"""wb3d.profiles — the 2D geometry a designed object starts from (no Blender needed, so it is unit-tested on its own):
outlines (rounded rectangles, squircles, capsules, circles), lathe profiles with real fillets, corner filleting of any
polyline, hole patterns for grilles, and the small vector maths parts.py builds with.

Outlines are closed, counter-clockwise lists of (x, y) in metres. Lathe profiles are lists of (r, z): radius from the
axis and height, from the bottom of the part to its top."""
import math


# ─────────────────────────────── vectors ───────────────────────────────


def arc(cx, cy, r, a0, a1, n=12):
    """Points on a circle of radius r about (cx, cy) from angle a0 to a1 (degrees), n segments → n + 1 points."""
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)), cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n)))
            for i in range(n + 1)]


def length(points, closed=False):
    pts = list(points) + ([points[0]] if closed else [])
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def bbox(points):
    xs = [p[0] for p in points]; ys = [p[1] for p in points]
    return (min(xs), min(ys)), (max(xs), max(ys))


def area(points):
    """Signed area of a closed outline: positive when counter-clockwise."""
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]))


def ccw(points):
    """The outline counter-clockwise (Blender's faces want it so their normal points +Z)."""
    return list(points) if area(points) >= 0 else list(reversed(points))


def dedupe(points, eps=1e-7):
    """Consecutive duplicates removed, a closing duplicate of the first point dropped, coordinates within 1e-12 of
    zero snapped to it (so an arc that ends on the axis really ends on it)."""
    out = []
    for p in points:
        p = tuple(0.0 if abs(c) < 1e-12 else c for c in p)
        if not out or math.dist(out[-1], p) > eps:
            out.append(p)
    if len(out) > 1 and math.dist(out[0], out[-1]) <= eps:
        out.pop()
    return out


def offset(points, delta):
    """A closed outline moved outwards by delta (inwards when negative) along its vertex normals: a lip, an inset,
    a wall. Exact for straight runs, slightly rounded at sharp corners — fine for the small offsets parts use."""
    n = len(points); out = []
    for i, (x, y) in enumerate(points):
        (xp, yp), (xn, yn) = points[i - 1], points[(i + 1) % n]
        e0 = _unit((x - xp, y - yp)); e1 = _unit((xn - x, yn - y))
        n0 = (e0[1], -e0[0]); n1 = (e1[1], -e1[0])                          # outward normals of a CCW outline
        m = _unit((n0[0] + n1[0], n0[1] + n1[1]))
        cos_half = max(0.3, m[0] * n0[0] + m[1] * n0[1])                   # miter length, clamped so spikes do not fly off
        out.append((x + m[0] * delta / cos_half, y + m[1] * delta / cos_half))
    return out


def _unit(v):
    l = math.hypot(*v) or 1.0
    return (v[0] / l, v[1] / l)


# ─────────────────────────────── outlines ───────────────────────────────


def circle(r=0.5, n=96):
    return [(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def polygon(sides=6, r=0.5, rotate=0.0):
    return [(r * math.cos(math.radians(rotate) + 2 * math.pi * i / sides), r * math.sin(math.radians(rotate) + 2 * math.pi * i / sides))
            for i in range(sides)]


def rounded_rect(w=1.0, d=0.6, r=0.1, n=10, corner='round'):
    """A w × d rectangle centred on the origin with corner radius r. corner 'round' = circular arcs; 'squircle' =
    superellipse corners that blend into the sides without a visible break in curvature (the corner of a phone,
    a keycap, a MacBook lid)."""
    r = max(0.0, min(r, w / 2 - 1e-9, d / 2 - 1e-9))
    if r <= 0:
        return [(-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2)]
    hw, hd = w / 2 - r, d / 2 - r
    pts = []
    for cx, cy, a0 in ((hw, -hd, -90), (hw, hd, 0), (-hw, hd, 90), (-hw, -hd, 180)):
        if corner == 'squircle':
            pts += [(cx + r * _sgn(math.cos(a)) * abs(math.cos(a)) ** 0.5, cy + r * _sgn(math.sin(a)) * abs(math.sin(a)) ** 0.5)
                    for a in (math.radians(a0 + 90 * i / n) for i in range(n + 1))]
        else:
            pts += arc(cx, cy, r, a0, a0 + 90, n)
    return dedupe(pts)


def _sgn(x):
    return -1.0 if x < 0 else 1.0


def capsule(length=1.0, width=0.3, n=16):
    """A stadium: a slot, a pill button, a vent."""
    r = width / 2; h = max(0.0, length / 2 - r)
    return dedupe(arc(h, 0, r, -90, 90, n) + arc(-h, 0, r, 90, 270, n))


def superellipse(w=1.0, d=1.0, e=4.0, n=128):
    """A whole superellipse (|x/a|^e + |y/b|^e = 1): e = 2 circle, 4 the classic squircle, higher = squarer."""
    a, b = w / 2, d / 2
    return [(a * _sgn(math.cos(t)) * abs(math.cos(t)) ** (2 / e), b * _sgn(math.sin(t)) * abs(math.sin(t)) ** (2 / e))
            for t in (2 * math.pi * i / n for i in range(n))]


# ─────────────────────────────── filleting a polyline ───────────────────────────────


def fillet(points, radius, n=8, closed=False):
    """Round the corners of a polyline: radius a number (every corner) or a list per point (0 = sharp). This is how a
    lathe profile or an extrusion outline gets real fillets: draw the sharp design, then say which corners round and
    by how much. The radius is clamped so neighbouring fillets never overlap."""
    pts = [tuple(p) for p in points]
    m = len(pts)
    radii = list(radius) if hasattr(radius, '__len__') else [radius] * m
    out = []
    rng = range(m) if closed else range(1, m - 1)
    if not closed:
        out.append(pts[0])
    for i in rng:
        p = pts[i]; r = radii[i]
        a = pts[i - 1]; b = pts[(i + 1) % m]
        if not closed and (i == 0 or i == m - 1):
            continue
        if r <= 0:
            out.append(p); continue
        e0 = _unit((a[0] - p[0], a[1] - p[1])); e1 = _unit((b[0] - p[0], b[1] - p[1]))
        cos_t = max(-1.0, min(1.0, e0[0] * e1[0] + e0[1] * e1[1]))
        theta = math.acos(cos_t)
        if theta < 1e-6 or abs(theta - math.pi) < 1e-6:
            out.append(p); continue
        d = r / math.tan(theta / 2)                                         # tangent distance from the corner
        lim = min(math.dist(p, a), math.dist(p, b)) / 2 - 1e-9
        if d > lim:
            d = lim; r = d * math.tan(theta / 2)
        t0 = (p[0] + e0[0] * d, p[1] + e0[1] * d); t1 = (p[0] + e1[0] * d, p[1] + e1[1] * d)
        bis = _unit((e0[0] + e1[0], e0[1] + e1[1]))
        c = (p[0] + bis[0] * r / math.sin(theta / 2), p[1] + bis[1] * r / math.sin(theta / 2))
        a0 = math.degrees(math.atan2(t0[1] - c[1], t0[0] - c[0])); a1 = math.degrees(math.atan2(t1[1] - c[1], t1[0] - c[0]))
        while a1 - a0 > 180: a1 -= 360
        while a1 - a0 < -180: a1 += 360
        out += arc(c[0], c[1], r, a0, a1, n)
    if not closed:
        out.append(pts[-1])
    return dedupe(out)


# ─────────────────────────────── lathe profiles (r, z) ───────────────────────────────


def puck(radius=0.075, height=0.06, bottom=0.01, top=0.012, n=10):
    """A flat cylinder with filleted bottom and top edges: a speaker, a charger, a knob's body, a lens cap."""
    return fillet([(0, 0), (radius, 0), (radius, height), (0, height)], [0, bottom, top, 0], n)


def cup(radius=0.04, height=0.09, wall=0.002, base=0.004, bottom=0.006, lip=0.001, flare=0.0, n=8):
    """A hollow vessel seen from the side: a mug, a tumbler, a pot; flare widens the top (metres)."""
    ro = radius; rt = radius + flare
    sharp = [(0, 0), (ro, 0), (rt, height), (rt - wall, height), (ro - wall, base), (0, base)]
    return fillet(sharp, [0, bottom, lip, lip, wall, 0], n)


def bottle(radius=0.035, height=0.2, neck=0.013, neck_height=0.035, shoulder=0.03, bottom=0.004, n=10):
    """A bottle: body, rounded shoulder into the neck, open top (solid — give it a glass material and a liquid inside)."""
    body_top = height - neck_height - shoulder
    sharp = [(0, 0), (radius, 0), (radius, body_top), (neck, body_top + shoulder), (neck, height), (0, height)]
    return fillet(sharp, [0, bottom, shoulder * 0.9, shoulder * 0.6, 0.0015, 0], n)


def knob(radius=0.016, height=0.018, top_r=0.003, skirt=0.002, n=8):
    """A rotary knob: a slight skirt at the base, a rounded top edge."""
    sharp = [(0, 0), (radius + skirt, 0), (radius + skirt, skirt), (radius, skirt * 2), (radius, height), (0, height)]
    return fillet(sharp, [0, 0.0006, 0.0006, 0.001, top_r, 0], n)


def lens(radius=0.006, height=0.004, dome=0.0015, n=10):
    """A camera lens as seen on a device: a short cylinder with a domed glass top."""
    base = [(0, 0), (radius, 0), (radius, height - dome)]
    return dedupe(base + [(radius * math.cos(math.radians(a)), height - dome + dome * math.sin(math.radians(a))) for a in
                          (90 * i / n for i in range(1, n + 1))])


def profile_bezel(outer=0.03, inner=0.024, height=0.004, n=6):
    """A ring: a bezel, a washer, a trim ring (hollow in the middle)."""
    return fillet([(inner, 0), (outer, 0), (outer, height), (inner, height)], [0.0005, 0.0008, 0.0008, 0.0005], n, closed=True)


# ─────────────────────────────── patterns ───────────────────────────────


def grid(w, h, pitch, margin=None, stagger=False):
    """Centres on a rectangular grid inside w × h (centred on the origin). stagger: every other row shifted half a pitch."""
    margin = pitch / 2 if margin is None else margin
    nx = int((w - 2 * margin) / pitch) + 1; ny = int((h - 2 * margin) / pitch) + 1
    x0 = -(nx - 1) * pitch / 2; y0 = -(ny - 1) * pitch / 2
    pts = []
    for j in range(ny):
        shift = pitch / 2 if stagger and j % 2 else 0.0
        for i in range(nx):
            x = x0 + i * pitch + shift
            if abs(x) <= w / 2 - margin + 1e-9:
                pts.append((x, y0 + j * pitch))
    return pts


def hex_grid(w, h, pitch, margin=None):
    """Hexagonal packing (the speaker-grille pattern): rows pitch·√3/2 apart, every other row shifted half a pitch."""
    margin = pitch / 2 if margin is None else margin
    row = pitch * math.sqrt(3) / 2
    ny = int((h - 2 * margin) / row) + 1; y0 = -(ny - 1) * row / 2
    pts = []
    for j in range(ny):
        shift = pitch / 2 if j % 2 else 0.0
        nx = int((w - 2 * margin) / pitch) + 2
        x0 = -(nx - 1) * pitch / 2
        for i in range(nx):
            x = x0 + i * pitch + shift
            if abs(x) <= w / 2 - margin + 1e-9:
                pts.append((x, y0 + j * row))
    return pts


def ring(n, radius, start=0.0):
    """n points evenly around a circle: screws on a bezel, ribs on a band, keys on a dial."""
    return [(radius * math.cos(math.radians(start) + 2 * math.pi * i / n), radius * math.sin(math.radians(start) + 2 * math.pi * i / n))
            for i in range(n)]


def inside(points, outline):
    """Which of the points fall inside a closed outline (ray casting): a grille clipped to a shaped face."""
    out = []
    for x, y in points:
        hit = False
        for (x0, y0), (x1, y1) in zip(outline, outline[1:] + outline[:1]):
            if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0 + 1e-18) + x0:
                hit = not hit
        if hit:
            out.append((x, y))
    return out
