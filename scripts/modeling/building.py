"""Architectural parts in meters. Windows and doors face local +Z with local +Y up."""

from collections import Counter
import math

import bmesh
import bpy
from mathutils import Vector, geometry

from . import core, parts, profiles, shading


def material(name, color, roughness=0.7):
    return (
        bpy.data.materials.get(name)
        or shading.new(name, **{"Base Color": shading.col(color), "Roughness": roughness})[0]
    )


def mesh(name, verts, faces):
    data = bpy.data.meshes.new(name)
    data.from_pydata(verts, [], faces)
    data.update()
    ob = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(ob)
    return ob


def offset_path(points, offset, closed):
    directions = [(b - a).normalized() for a, b in zip(points, points[1:] + ([points[0]] if closed else []))]
    if any(d.length < 0.99 for d in directions):
        raise ValueError("Remove coincident path points.")
    normals = [Vector((-d.y, d.x)) for d in directions]
    result = []
    for i, p in enumerate(points):
        a = normals[(i - 1) % len(normals)] if closed or i else normals[0]
        b = normals[i % len(normals)] if closed or i < len(normals) else normals[-1]
        denom = 1 + a.dot(b)
        if denom < 1e-6:
            raise ValueError("A path cannot reverse at a single corner.")
        result.append(p + (a + b) * (offset / denom))
    return result


def wall(path, height=3.0, thickness=0.3, openings=()):
    points = [Vector(p[:2]) for p in path]
    if len(points) < 2 or min(height, thickness) <= 0:
        raise ValueError("Choose a wall path and positive dimensions.")
    closed = (points[0] - points[-1]).length < 1e-6
    if closed:
        points.pop()
    left = offset_path(points, thickness / 2, closed)
    right = offset_path(points, -thickness / 2, closed)
    verts = [
        (p.x, p.y, z) for a, b in zip(left, right) for p, z in ((a, 0), (b, 0), (b, height), (a, height))
    ]
    count = len(points) if closed else len(points) - 1
    faces = []
    for i in range(count):
        a = 4 * i
        b = 4 * ((i + 1) % len(points))
        for k in range(4):
            faces.append((a + k, b + k, b + (k + 1) % 4, a + (k + 1) % 4))
    if not closed:
        faces.extend([(0, 1, 2, 3), tuple(reversed(range(len(verts) - 4, len(verts))))])
    ob = mesh("Wall", verts, faces)
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(ob.data)
    bm.free()
    segments = [(points[i], points[(i + 1) % len(points)]) for i in range(count)]
    total = sum((b - a).length for a, b in segments)
    for center, bottom, width, tall in openings:
        if width <= 0 or tall <= 0 or bottom < 0 or bottom + tall > height + 1e-6 or width > total:
            raise ValueError("An opening must fit within the wall height and length.")
        lo, hi = center - width / 2, center + width / 2
        if not closed and (lo < 0 or hi > total):
            raise ValueError("An opening extends beyond the wall.")
        pos = 0.0
        cutters = []
        for a, b in segments:
            length = (b - a).length
            t = (b - a) / length
            n = Vector((-t.y, t.x))
            for shift in (-total, 0, total) if closed else (0,):
                s = max(0, lo + shift - pos)
                e = min(length, hi + shift - pos)
                if e <= s:
                    continue
                # Extend a crossing cutter across the miter of its adjoining segment.
                if s == 0 and lo + shift < pos:
                    s -= thickness
                if e == length and hi + shift > pos + length:
                    e += thickness
                corners = [
                    a + t * u + n * v
                    for u, v in ((s, -thickness), (e, -thickness), (e, thickness), (s, thickness))
                ]
                z = bottom - 0.001 if bottom == 0 else bottom
                cutters.append(parts.extrude(corners, bottom + tall - z, location=(0, 0, z)))
            pos += length
        if cutters:
            parts.cut(ob, cutters, sequential=True)
    return ob


def panel_boxes(name, boxes, mats):
    verts, faces, indices = [], [], []
    for (x, y, z), (w, h, d), slot in boxes:
        start = len(verts)
        verts.extend(
            (x + i * w / 2, y + j * h / 2, z + k * d / 2)
            for k in (-1, 1)
            for j, i in ((-1, -1), (-1, 1), (1, 1), (1, -1))
        )
        faces.extend(
            tuple(start + i for i in f)
            for f in ((3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7))
        )
        indices.extend([slot] * 6)
    ob = mesh(name, verts, faces)
    for m in mats:
        ob.data.materials.append(m)
    for poly, slot in zip(ob.data.polygons, indices):
        poly.material_index = slot
    return ob


def frame_boxes(w, h, f):
    if min(w, h) <= 2 * f or f <= 0:
        raise ValueError("The frame must fit inside the opening.")
    return [((x, h / 2, 0), (f, h, f * 1.25), 0) for x in ((f - w) / 2, (w - f) / 2)] + [
        ((0, y, 0), (w - 2 * f, f, f * 1.25), 0) for y in (f / 2, h - f / 2)
    ]


def window(width, height, frame=0.06, mullions=(1, 1), glass=True):
    boxes = frame_boxes(width, height, frame)
    cols, rows = mullions
    if min(cols, rows) < 0:
        raise ValueError("Mullion counts must be nonnegative.")
    w, h = width - 2 * frame, height - 2 * frame
    for i in range(cols):
        boxes.append(((-w / 2 + w * (i + 1) / (cols + 1), height / 2, 0), (frame * 0.59, h, frame), 0))
    for i in range(rows):
        boxes.append(((0, frame + h * (i + 1) / (rows + 1), 0.003), (w, frame * 0.59, frame), 0))
    mats = [material("Painted joinery", "#d6c5a2")]
    if glass:
        pane = shading.new(
            "Window glass",
            **{
                "Base Color": shading.col("#698993"),
                "Roughness": 0.19,
                "Transmission Weight": 0.48,
                "IOR": 1.5,
            },
        )[0]
        mats.append(pane)
        boxes.append(((0, height / 2, -frame * 0.45), (w, h, 0.012), 1))
    return panel_boxes("Window", boxes, mats)


def door(width, height, frame=0.07, panel=True):
    boxes = frame_boxes(width, height, frame)
    w, h = width - 2 * frame, height - 2 * frame
    boxes.append(((0, height / 2, -0.006), (w, h, frame * 0.72), 1))
    if panel:
        for y in (0.29, 0.7):
            boxes.append(((0, height * y, frame * 0.33), (w * 0.78, h * 0.29, frame * 0.18), 1))
    boxes.append(((w * 0.35, height * 0.47, frame * 0.65), (max(0.05, w * 0.13), 0.035, 0.055), 2))
    return panel_boxes(
        "Door",
        boxes,
        [
            material("Painted joinery", "#d6c5a2"),
            material("Door oak", "#674832"),
            material("Latch brass", "#bd9857", 0.34),
        ],
    )


def stairs(steps, rise=0.17, run=0.28, width=1.2, kind="straight"):
    if kind != "straight" or steps < 1 or min(rise, run, width) <= 0:
        raise ValueError("Choose positive straight stair dimensions.")
    side = [(0.0, 0.0)]
    for i in range(steps):
        side.extend([(i * run, (i + 1) * rise), ((i + 1) * run, (i + 1) * rise)])
    side.append((steps * run, 0))
    n = len(side)
    verts = [(x, y, z) for x in (-width / 2, width / 2) for y, z in side]
    faces = [tuple(range(n)), tuple(reversed(range(n, 2 * n)))]
    faces.extend((i, n + i, n + (i + 1) % n, (i + 1) % n) for i in range(n))
    ob = mesh("Stairs", verts, faces)
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(ob.data)
    bm.free()
    return ob


def slab(outline, thickness):
    if thickness <= 0:
        raise ValueError("Choose positive slab thickness.")
    return parts.extrude(outline, thickness, name="Slab")


def column(radius, height, style="plain"):
    if min(radius, height) <= 0 or style not in {"plain", "fluted"}:
        raise ValueError("Choose valid column dimensions and style.")
    n = 96
    rings = [
        (0, 1.16),
        (height * 0.045, 1.16),
        (height * 0.065, 1),
        (height * 0.93, 0.88),
        (height * 0.96, 1.07),
        (height, 1.07),
    ]
    verts = []
    for j, (z, r) in enumerate(rings):
        for i in range(n):
            a = math.tau * i / n
            flute = 0.095 * (0.5 + 0.5 * math.cos(16 * a)) if style == "fluted" and j in (2, 3) else 0.0
            verts.append((radius * (r - flute) * math.cos(a), radius * (r - flute) * math.sin(a), z))
    faces = [tuple(reversed(range(n))), tuple(range(n * (len(rings) - 1), n * len(rings)))]
    for j in range(len(rings) - 1):
        faces.extend(
            (j * n + i, j * n + (i + 1) % n, (j + 1) * n + (i + 1) % n, (j + 1) * n + i) for i in range(n)
        )
    return core.smooth(mesh("Column", verts, faces), 25)


def railing(path, height=1.0, posts_every=1.2):
    points = [Vector((p[0], p[1], p[2] if len(p) > 2 else 0)) for p in path]
    if len(points) < 2 or min(height, posts_every) <= 0:
        raise ValueError("Choose positive rail height and spacing.")
    curve = bpy.data.curves.new("Railing", "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = 0.028
    curve.bevel_resolution = 2
    curve.use_fill_caps = True

    def line(points, radius):
        sp = curve.splines.new("POLY")
        sp.points.add(len(points) - 1)
        for bp, p in zip(sp.points, points):
            bp.co = (*p, 1)
            bp.radius = radius

    for h, r in ((height, 1.3), (height * 0.47, 0.72)):
        line([p + Vector((0, 0, h)) for p in points], r)
    posts = [points[0]]
    for a, b in zip(points, points[1:]):
        n = max(1, math.ceil((b - a).length / posts_every))
        posts.extend(a.lerp(b, i / n) for i in range(1, n + 1))
    for p in posts:
        line([p, p + Vector((0, 0, height))], 1)
    ob = bpy.data.objects.new("Railing", curve)
    bpy.context.collection.objects.link(ob)
    return ob


def clip_polygon(poly, plane):
    """Clip a convex polygon to a*x+b*y+c >= 0."""
    if not poly:
        return []
    a, b, c = plane
    output = []
    prev = poly[-1]
    d0 = a * prev.x + b * prev.y + c
    for p in poly:
        d1 = a * p.x + b * p.y + c
        if (d1 >= 0) != (d0 >= 0):
            output.append(prev.lerp(p, d0 / (d0 - d1)))
        if d1 >= 0:
            output.append(p)
        prev, d0 = p, d1
    return output if len(output) >= 3 else []


def roof(outline, pitch=30, kind="gable", overhang=0.4, thickness=0.2):
    pts = [Vector(p[:2]) for p in profiles.ccw(profiles.dedupe(outline))]
    if (
        len(pts) < 3
        or kind not in {"gable", "hip", "flat"}
        or not 0 <= pitch < 85
        or min(overhang, thickness) < 0
        or thickness == 0
    ):
        raise ValueError("Choose a simple roof outline, a supported roof shape and positive thickness.")
    pts = offset_path(pts, -overhang, True)
    if kind == "flat":
        return parts.extrude(pts, thickness, name="Flat roof")
    edges = list(zip(pts, pts[1:] + pts[:1]))
    tris = geometry.tessellate_polygon([pts])
    cells = [[pts[i] for i in tri] for tri in tris]

    def value(plane, p):
        return plane[0] * p.x + plane[1] * p.y + plane[2]

    def sub(a, b):
        return tuple(x - y for x, y in zip(a, b))

    if kind == "gable":
        a, b = max(edges, key=lambda pair: (pair[1] - pair[0]).length)
        t = (b - a).normalized()
        n = Vector((-t.y, t.x))
        lo, hi = min(n.dot(p) for p in pts), max(n.dot(p) for p in pts)
        planes = [(n.x, n.y, -lo), (-n.x, -n.y, hi)]
        cells = [
            flat
            for tri in cells
            for i in (0, 1)
            if (flat := clip_polygon(tri, sub(planes[1 - i], planes[i])))
        ]

        def height_at(p):
            return min(value(plane, p) for plane in planes)
    else:
        # Each edge contributes a piecewise planar distance, including beyond its endpoints.
        # Split the lower envelope at every ridge and valley, including on reentrant footprints.
        fields = []
        for a, b in edges:
            t = (b - a).normalized()
            n = Vector((-t.y, t.x))
            fields.append(
                [(n.x, n.y, -n.dot(a)), (-n.x, -n.y, n.dot(a)), (-t.x, -t.y, t.dot(a)), (t.x, t.y, -t.dot(b))]
            )
        partition = [(poly, None) for poly in cells]
        for planes in fields:
            updated = []
            for poly, previous in partition:
                for index, plane in enumerate(planes):
                    part = poly
                    for j, other in enumerate(planes):
                        if j != index:
                            part = clip_polygon(part, sub(plane, other))
                        if not part:
                            break
                    if not part:
                        continue
                    if previous is None or previous == plane:
                        updated.append((part, plane))
                    else:
                        for p, q in ((plane, previous), (previous, plane)):
                            section = clip_polygon(part, sub(q, p))
                            if section:
                                updated.append((section, p))
            partition = updated
        cells = [cell for cell, _ in partition]

        def height_at(p):
            return min(max(value(f, p) for f in field) for field in fields)

    vertices = []
    lookup = {}
    creases = set()

    def index(p):
        key = (round(p.x, 6), round(p.y, 6))
        if key not in lookup:
            lookup[key] = len(vertices)
            vertices.append(Vector(key))
        return lookup[key]

    boundary = [index(p) for p in pts]
    for cell in cells:
        ids = [index(p) for p in cell]
        creases.update(tuple(sorted((a, b))) for a, b in zip(ids, ids[1:] + ids[:1]) if a != b)
    coords, _, triangles, *_ = geometry.delaunay_2d_cdt(vertices, list(creases), [boundary], 1, 1e-6)
    slope = math.tan(math.radians(pitch))
    n = len(coords)
    verts = [(p.x, p.y, max(0, height_at(p)) * slope + z) for z in (0, thickness) for p in coords]
    facets = [tuple(reversed(tri)) for tri in triangles] + [tuple(n + i for i in tri) for tri in triangles]
    usage = Counter(tuple(sorted((a, b))) for tri in triangles for a, b in zip(tri, tri[1:] + tri[:1]))
    for tri in triangles:
        for a, b in zip(tri, tri[1:] + tri[:1]):
            if usage[tuple(sorted((a, b)))] == 1:
                facets.append((a, b, n + b, n + a))
    return mesh(kind.title() + " roof", verts, facets)
