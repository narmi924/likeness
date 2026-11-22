"""wb3d.parts — designed parts, the way a product modeller makes them: a profile turned on a lathe, an outline
extruded, a shell with a wall, a seam cut around a body, holes and slots cut in, ribs and knurling, type raised or
engraved, real fillets with hardened normals. Everything is a function of its dimensions in metres; the result is a
mesh with clean topology that `check_topology` can vouch for.

    body = W.lathe(W.P.puck(0.075, 0.06, bottom=0.012, top=0.016), material=W.soft_touch('#1b1d21'))
    W.groove(body, z=0.046, width=0.0015, depth=0.0008)                 # the seam between body and cap
    W.grille(body, at=(0, -0.075, 0.03), normal=(0, -1, 0), w=0.08, h=0.02, pitch=0.0035, hole=0.0012)
    W.emboss(body, text='LIKENESS', size=0.012, at=(0, -0.075, 0.012), normal=(0, -1, 0), raised=True)

Parts are built flat (outlines in XY facing +Z, lathes around +Z) and placed with `location` / `at` + `normal`.
"""
import math

import bpy
import bmesh
from mathutils import Vector, Matrix

from . import profiles as P
from .core import (link, assign, smooth, mesh_object, apply_all, evaluated_mesh, duplicate, remove, join, world_bounds, frame,
                   set_parent, islands, inflate, harden)


# ─────────────────────────────── solids from profiles ───────────────────────────────


def lathe(profile, steps=192, material=None, name='Lathe', location=(0, 0, 0), smooth_angle=30.0):
    """A solid of revolution about +Z from an (r, z) profile — a bottle, a knob, a speaker, a lens barrel, a bezel. The
    profile runs from the bottom to the top; when it starts and ends on the axis (r = 0) the solid is closed, otherwise
    it is a ring (a bezel, a washer) whose outline closes on itself."""
    pts = [(float(r), float(z)) for r, z in profile]
    bm = bmesh.new()
    verts = [bm.verts.new((r, 0.0, z)) for r, z in pts]
    edges = [bm.edges.new((a, b)) for a, b in zip(verts, verts[1:])]
    on_axis = abs(pts[0][0]) < 1e-9 and abs(pts[-1][0]) < 1e-9
    if not on_axis:
        edges.append(bm.edges.new((verts[-1], verts[0])))
    bmesh.ops.spin(bm, geom=verts + edges, cent=(0, 0, 0), axis=(0, 0, 1), angle=2 * math.pi, steps=int(steps), use_merge=True)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-7)
    bmesh.ops.delete(bm, geom=[e for e in bm.edges if not e.link_faces], context='EDGES')
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if bm.calc_volume(signed=True) < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
    o = mesh_object(name, bm, material, location)
    return smooth(o, smooth_angle)


def extrude(outline, depth=0.01, bevel=0.0, segments=4, material=None, name='Extrusion', location=(0, 0, 0), smooth_angle=30.0):
    """A closed outline (profiles.rounded_rect, capsule, circle, polygon, or your own points) pulled up by `depth`:
    a plate, a keycap, a button, a tile, a card. bevel > 0 rounds every edge by that much (a bevel modifier with
    hardened normals, kept live so it can still be tuned)."""
    pts = P.ccw(P.dedupe(outline))
    bm = bmesh.new()
    vs = [bm.verts.new((x, y, 0.0)) for x, y in pts]
    f = bm.faces.new(vs)
    r = bmesh.ops.extrude_face_region(bm, geom=[f])
    top = [g for g in r['geom'] if isinstance(g, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=top, vec=(0, 0, depth))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    o = mesh_object(name, bm, material, location)
    if bevel > 0:
        fillet(o, bevel, segments)
    return smooth(o, smooth_angle)


def shell(w=0.15, d=0.075, h=0.008, r_plan=0.012, r_edge=0.002, wall=0.0, open=None, corner='squircle', segments=5,
          material=None, name='Shell', location=(0, 0, 0)):
    """A device slab: a rounded rectangle in plan (`r_plan`, 'round' or 'squircle' corners) with its top and bottom
    rims rounded by `r_edge`. wall > 0 hollows it to that wall thickness; open = 'top' | 'bottom' leaves that face off
    (a tray, a lid). The origin is at the bottom centre."""
    o = extrude(P.rounded_rect(w, d, r_plan, n=12, corner=corner), h, 0.0, segments, material, name, location, smooth_angle=None)
    if open or wall > 0:
        bm = bmesh.new(); bm.from_mesh(o.data)
        if open:
            want = 1.0 if open == 'top' else -1.0
            kill = [f for f in bm.faces if abs(f.normal.z - want) < 1e-3 and len(f.verts) > 4]
            bmesh.ops.delete(bm, geom=kill, context='FACES')
        bm.to_mesh(o.data); bm.free()
        if wall > 0:
            so = o.modifiers.new('Wall', 'SOLIDIFY'); so.thickness = wall; so.offset = -1.0; so.use_even_offset = True
            so.use_rim = True
    if open and wall > 0:
        r_edge = min(r_edge, wall * 0.45)                                 # a rim can only round by less than half its width
    if r_edge > 0:
        fillet(o, r_edge, segments)
    return smooth(o, 30.0)


def sweep(points, radius=0.003, profile=None, closed=False, resolution=12, material=None, name='Sweep', smooth_path=True,
          caps=True, mesh=False):
    """A tube along a path of 3D points — a cable, a pipe, a handle, a wire frame, a trim bead. radius is the tube's
    radius; `profile` (a closed outline) instead sweeps that cross-section. smooth_path rounds the path through the
    points (Bezier, auto handles) — False keeps straight runs with corners. mesh=True returns a mesh (for booleans)."""
    cu = bpy.data.curves.new(name, 'CURVE'); cu.dimensions = '3D'; cu.resolution_u = 12; cu.fill_mode = 'FULL'
    cu.use_fill_caps = caps
    if smooth_path:
        sp = cu.splines.new('BEZIER'); sp.bezier_points.add(len(points) - 1)
        for bp, p in zip(sp.bezier_points, points):
            bp.co = p; bp.handle_left_type = bp.handle_right_type = 'AUTO'
    else:
        sp = cu.splines.new('POLY'); sp.points.add(len(points) - 1)
        for pp, p in zip(sp.points, points):
            pp.co = (*p, 1.0)
    sp.use_cyclic_u = closed
    if profile is not None:
        pc = bpy.data.curves.new(name + '.profile', 'CURVE'); pc.dimensions = '2D'
        ps = pc.splines.new('POLY'); pts = P.ccw(P.dedupe(profile)); ps.points.add(len(pts) - 1)
        for pp, (x, y) in zip(ps.points, pts):
            pp.co = (x, y, 0.0, 1.0)
        ps.use_cyclic_u = True
        po = link(bpy.data.objects.new(name + '.profile', pc)); po.hide_render = True; po.hide_viewport = True
        cu.bevel_mode = 'OBJECT'; cu.bevel_object = po
    else:
        cu.bevel_mode = 'ROUND'; cu.bevel_depth = radius; cu.bevel_resolution = max(1, resolution // 2)
    o = link(bpy.data.objects.new(name, cu))
    if material is not None:
        cu.materials.append(material)
    if mesh:
        return to_mesh(o)
    return o


def to_mesh(obj, smooth_angle=30.0, weld=True):
    """A curve or text object as a mesh (what it renders as). weld joins coincident vertices (a tube's caps and seam
    into one skin); off for type, whose glyphs are separate shells that may touch."""
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(obj.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
    mats = [m for m in obj.data.materials]
    o = link(bpy.data.objects.new(obj.name, me)); o.matrix_world = obj.matrix_world.copy()
    for m in mats:
        me.materials.append(m)
    remove(obj)
    bm = bmesh.new(); bm.from_mesh(me)
    if weld:
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)             # a tube's caps and seams become one skin
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if not any(not e.is_manifold for e in bm.edges) and bm.faces and bm.calc_volume(signed=True) < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
    bm.to_mesh(me); bm.free()
    return smooth(o, smooth_angle)


# ─────────────────────────────── edges ───────────────────────────────


def fillet(obj, width=0.002, segments=4, angle=30.0, harden=True, apply=False):
    """Round the object's sharp edges (sharper than `angle`) by `width`: a bevel modifier with hardened normals plus a
    weighted-normal modifier, so the flat faces stay flat and the highlight runs along the bevel. Kept live unless
    apply=True (needed before booleans that must see the rounded edge)."""
    b = obj.modifiers.new('Fillet', 'BEVEL'); b.width = width; b.segments = int(segments); b.limit_method = 'ANGLE'
    b.angle_limit = math.radians(angle); b.harden_normals = harden; b.miter_outer = 'MITER_ARC'; b.use_clamp_overlap = True
    if harden:
        wn = obj.modifiers.new('Normals', 'WEIGHTED_NORMAL'); wn.keep_sharp = True
    if apply:
        apply_all(obj)
    return obj


def subd(obj, levels=2, crease_angle=None, crease=1.0):
    """Subdivision surface (Catmull-Clark). crease_angle: edges sharper than this stay sharp by `crease` (0–1) —
    the way a cast or moulded part keeps its design lines. For meshes made of quads (a cube, an imported model, a
    box-modelled form): an extruded outline's big n-gon caps pinch under subdivision — round those with `fillet`."""
    if crease_angle is not None and obj.type == 'MESH':
        me = obj.data
        cr = me.edge_creases_ensure() if hasattr(me, 'edge_creases_ensure') else None
        if cr is not None:
            bm = bmesh.new(); bm.from_mesh(me); bm.edges.ensure_lookup_table()
            sharp = set()
            for e in bm.edges:
                if len(e.link_faces) == 2 and e.calc_face_angle(0.0) > math.radians(crease_angle):
                    sharp.add(e.index)
            bm.free()
            for i in sharp:
                cr.data[i].value = crease
    s = obj.modifiers.new('Subdivision', 'SUBSURF'); s.levels = s.render_levels = int(levels)
    smooth(obj, None)
    return obj


# ─────────────────────────────── booleans: cuts, holes, seams ───────────────────────────────


def cut(obj, cutters, operation='DIFFERENCE', solver='EXACT', keep=False, self_overlap=False, sequential=False):
    """Boolean the cutter object(s) into obj and bake it: DIFFERENCE removes them, UNION adds, INTERSECT keeps the
    overlap. The cutters are joined into one and removed (keep=True leaves them, hidden). self_overlap=True when the
    cutter's pieces cross each other (knurl grooves, crossing slots): the exact solver then resolves them first.
    sequential=True cuts them one after another instead (closed pieces that touch, like the glyphs of a word)."""
    cutters = [c for c in (cutters if isinstance(cutters, (list, tuple)) else [cutters]) if c is not None]
    if not cutters:
        return obj
    if sequential and len(cutters) > 1:
        for c in cutters:
            cut(obj, c, operation, solver, keep, self_overlap)
        return obj
    tool = cutters[0] if len(cutters) == 1 else join(cutters, obj.name + '.cutter')
    if tool.type != 'MESH':
        tool = to_mesh(tool)
    apply_all(obj)
    m = obj.modifiers.new('Cut', 'BOOLEAN'); m.operation = operation; m.object = tool; m.solver = solver
    if solver == 'EXACT':
        m.use_hole_tolerant = True; m.use_self = bool(self_overlap)
    apply_all(obj)
    bm = bmesh.new(); bm.from_mesh(obj.data)                              # slivers a boolean leaves behind
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-7)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=1e-7)
    bm.to_mesh(obj.data); bm.free()
    harden(obj)
    if keep:
        tool.hide_render = True; tool.hide_viewport = True
    else:
        remove(tool)
    return obj


def cutter_cylinders(centres, radius, depth, segments=24, name='Holes'):
    """One mesh of cylinders (axis +Z, from −depth to +depth) at the 2D centres: the tool for `holes`."""
    bm = bmesh.new()
    for x, y in centres:
        r = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segments, radius1=radius, radius2=radius, depth=depth * 2)
        bmesh.ops.translate(bm, verts=r['verts'], vec=(x, y, 0.0))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return mesh_object(name, bm)


def cutter_outlines(outlines, depth, name='Slots'):
    """One mesh of prisms (from −depth to +depth) from closed 2D outlines: the tool for `slots` and shaped openings."""
    bm = bmesh.new()
    for pts in outlines:
        pts = P.ccw(P.dedupe(pts))
        vs = [bm.verts.new((x, y, -depth)) for x, y in pts]
        f = bm.faces.new(vs)
        r = bmesh.ops.extrude_face_region(bm, geom=[f])
        bmesh.ops.translate(bm, verts=[g for g in r['geom'] if isinstance(g, bmesh.types.BMVert)], vec=(0, 0, 2 * depth))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return mesh_object(name, bm)


def holes(obj, centres, radius=0.001, depth=0.002, at=(0, 0, 0), normal=(0, 0, 1), segments=24, through=False):
    """Round holes into a face of obj: `centres` are 2D points in the face's plane (profiles.grid / hex_grid / ring /
    your own), `at` the point on the surface they are centred on, `normal` the surface's outward normal there.
    depth is how deep they go (through=True drills across the whole part)."""
    if through:
        lo, hi = world_bounds(obj); depth = (hi - lo).length
    tool = cutter_cylinders(centres, radius, depth, segments, obj.name + '.holes')
    tool.matrix_world = frame(at, normal)
    return cut(obj, tool)


def grille(obj, at, normal, w=0.06, h=0.02, pitch=0.003, hole=0.0011, depth=0.002, pattern='hex', clip=None, segments=20):
    """A speaker / vent grille: holes on a hex (or square) grid over a w × h patch of the surface at `at`. clip: a
    closed outline (in the patch's plane) that keeps only the holes inside it — a round grille, a shaped one."""
    pts = P.hex_grid(w, h, pitch) if pattern == 'hex' else P.grid(w, h, pitch)
    if clip is not None:
        pts = P.inside(pts, clip)
    return holes(obj, pts, hole / 2, depth, at, normal, segments)


def slots(obj, at, normal, n=6, length=0.02, width=0.0025, pitch=0.005, depth=0.002, along='x', through=False):
    """Parallel slots (vents, a speaker slit, a grip): n capsules `length` long, `pitch` apart, across the patch at
    `at`. along = 'x' (slots run horizontally, stacked vertically) or 'y'."""
    outs = []
    for i in range(n):
        off = (i - (n - 1) / 2) * pitch
        cap = P.capsule(length, width)
        outs.append([(x, y + off) for x, y in cap] if along == 'x' else [(y + off, x) for x, y in cap])
    if through:
        lo, hi = world_bounds(obj); depth = (hi - lo).length
    tool = cutter_outlines(outs, depth, obj.name + '.slots'); tool.matrix_world = frame(at, normal)
    return cut(obj, tool)


def opening(obj, outline, at, normal, depth=0.01, through=False):
    """One shaped opening (a port, a window, a camera cut-out) from a closed outline in the face's plane."""
    if through:
        lo, hi = world_bounds(obj); depth = (hi - lo).length
    tool = cutter_outlines([outline], depth, obj.name + '.opening'); tool.matrix_world = frame(at, normal)
    return cut(obj, tool)


def groove(obj, z, width=0.0012, depth=0.0006):
    """A seam cut all the way around the body at height z (world): the split line between two housings, a design
    line, the gap a lid sits in. Works on any shape: the band of the body between z ± width/2 is pushed in by depth."""
    apply_all(obj)
    inner = duplicate(obj, obj.name + '.inner')
    inner.modifiers.clear()
    smooth(inner, None)
    d = inner.modifiers.new('In', 'DISPLACE'); d.direction = 'NORMAL'; d.strength = -depth; d.mid_level = 0.0
    apply_all(inner)
    lo, hi = world_bounds(obj); size = (hi - lo)
    bm = bmesh.new(); r = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, verts=r['verts'], vec=(size.x * 2 + 1, size.y * 2 + 1, width))
    bmesh.ops.translate(bm, verts=r['verts'], vec=((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, z))
    slab = mesh_object(obj.name + '.slab', bm)
    cut(slab, inner, 'DIFFERENCE')
    return cut(obj, slab, 'DIFFERENCE')


def split(obj, z, gap=0.0, names=('Lower', 'Upper')):
    """Two parts from one body, cut at height z with `gap` between them (a lid and a base, an upper and a lower
    housing). → (lower, upper); obj itself becomes the lower part."""
    apply_all(obj)
    upper = duplicate(obj, names[1]); obj.name = names[0]
    lo, hi = world_bounds(obj); size = hi - lo; big = size.length * 2 + 1

    def box(z0, z1, name):
        bm = bmesh.new(); r = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, verts=r['verts'], vec=(big, big, z1 - z0))
        bmesh.ops.translate(bm, verts=r['verts'], vec=((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, (z0 + z1) / 2))
        return mesh_object(name, bm)
    cut(obj, box(lo.z - 1, z - gap / 2, 'low'), 'INTERSECT')
    cut(upper, box(z + gap / 2, hi.z + 1, 'high'), 'INTERSECT')
    return obj, upper


# ─────────────────────────────── surface detail: ribs, knurl, type ───────────────────────────────


def ribs(n=120, radius=0.075, z=(0.02, 0.045), size=(0.001, 0.0014), sink=0.0004, bevel=0.0003, material=None, name='Ribs',
         location=(0, 0, 0), gap=None):
    """n ribs standing around a cylinder of `radius` between heights z: the machined band on a speaker, a dial's grip,
    a lens ring. size = (width along the circumference, how far they stand proud); sink keeps them seated in the body.
    gap = (angle0, angle1) in degrees leaves that arc empty (for a wordmark): 0° is the front (−Y), counter-clockwise
    seen from above, so gap=(-20, 20) clears the front."""
    w, proud = size; h = z[1] - z[0]; zc = (z[0] + z[1]) / 2
    bm = bmesh.new()
    for i in range(n):
        a = 360.0 * i / n
        if gap and _in_arc(a, gap):
            continue
        r = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, verts=r['verts'], vec=(w, proud + sink, h))
        bmesh.ops.translate(bm, verts=r['verts'], vec=(0, -(radius - sink + (proud + sink) / 2), zc))
        bmesh.ops.rotate(bm, verts=r['verts'], cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(a), 3, 'Z'))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    o = mesh_object(name, bm, material, location)
    if bevel > 0:
        fillet(o, bevel, 2, angle=40)
    return smooth(o, 40)


def _in_arc(a, arc):
    a0, a1 = arc
    a = a % 360; a0 %= 360; a1 %= 360
    return a0 <= a <= a1 if a0 <= a1 else (a >= a0 or a <= a1)


def knurl(obj, radius, z=(0.0, 0.01), pitch=0.0012, depth=0.0003, angle=30.0, location=(0, 0, 0)):
    """Diamond knurling cut into a cylindrical band of obj (a knob, a crown, a grip): two helical families of thin
    grooves at ±angle, `pitch` apart around the circumference, `depth` deep."""
    n = max(8, int(round(2 * math.pi * radius / pitch)))
    h = (z[1] - z[0]) / math.cos(math.radians(angle)) + pitch
    zc = (z[0] + z[1]) / 2
    bm = bmesh.new()
    for sign in (1, -1):
        for i in range(n):
            a = 360.0 * i / n
            r = bmesh.ops.create_cube(bm, size=1.0)
            bmesh.ops.scale(bm, verts=r['verts'], vec=(pitch * 0.45, depth * 2, h))
            bmesh.ops.rotate(bm, verts=r['verts'], cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(sign * angle), 3, 'Y'))
            bmesh.ops.translate(bm, verts=r['verts'], vec=(0, -radius, zc))
            bmesh.ops.rotate(bm, verts=r['verts'], cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(a), 3, 'Z'))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    tool = mesh_object(obj.name + '.knurl', bm); tool.location = location
    return cut(obj, tool, self_overlap=True)


def type_mesh(text, font=None, size=0.01, depth=0.001, bevel=0.0, name='Type', material=None, align='CENTER'):
    """Text as a mesh lying in XY (facing +Z, bottom at z = −depth, top at z = +depth... no: the glyphs sit on z = 0
    and rise to `depth`), centred. font: a .ttf/.otf path (the film's look face)."""
    cu = bpy.data.curves.new(name, 'FONT'); cu.body = text; cu.size = size; cu.extrude = depth / 2
    cu.bevel_depth = bevel; cu.bevel_resolution = 3 if bevel else 0; cu.resolution_u = 8
    cu.align_x = align; cu.align_y = 'CENTER'; cu.fill_mode = 'BOTH'
    if font:
        from pathlib import Path
        if Path(font).exists():
            cu.font = bpy.data.fonts.load(str(font), check_existing=True)
    o = link(bpy.data.objects.new(name, cu))
    if material is not None:
        cu.materials.append(material)
    o = to_mesh(o, 40)
    o.location.z = depth / 2                                              # glyph bottoms on z = 0
    bpy.context.view_layer.update()
    return o


def svg_mesh(path, size=0.02, depth=0.001, bevel=0.0, name='Mark', material=None):
    """An SVG mark as a mesh lying in XY, `size` wide, centred, bottom on z = 0."""
    before = set(bpy.data.objects)
    bpy.ops.import_curve.svg(filepath=str(path))
    parts = [o for o in bpy.data.objects if o not in before and o.type == 'CURVE']
    if not parts:
        raise ValueError(f'no curves in {path}')
    xs, ys = [], []
    for o in parts:
        o.data.extrude = depth / 2; o.data.bevel_depth = bevel; o.data.bevel_resolution = 3 if bevel else 0; o.data.dimensions = '2D'
        for s in o.data.splines:
            for p in list(s.bezier_points) + list(s.points):
                xs.append(p.co.x); ys.append(p.co.y)
        for coll in list(o.users_collection):
            coll.objects.unlink(o)
        link(o)
    w = max(xs) - min(xs) or 1.0; cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    k = size / w
    meshes = []
    for o in parts:
        o.location = (-cx * k, -cy * k, 0); o.scale = (k, k, k)
        meshes.append(to_mesh(o, 40))
    o = join(meshes, name, material); o.location.z = depth / 2
    return o


def emboss(obj, text=None, svg=None, at=(0, 0, 0), normal=(0, 0, 1), size=0.01, depth=0.0006, font=None, raised=True,
           material=None, sink=0.0002, name=None):
    """A wordmark or mark on a surface of obj: raised (standing proud by `depth`, sunk `sink` into the surface so it
    seats on curves) or engraved (cut `depth` deep). → the mark object (raised) or obj (engraved)."""
    mark = type_mesh(text, font, size, depth, name=name or f'{obj.name}.mark', material=material) if text else \
        svg_mesh(svg, size, depth, name=name or f'{obj.name}.mark', material=material)
    if raised:
        mark.matrix_world = frame(at, normal) @ Matrix.Translation((0, 0, -sink)) @ mark.matrix_world
        set_parent(mark, obj)
        return mark
    mark.matrix_world = frame(at, normal) @ Matrix.Translation((0, 0, -depth)) @ mark.matrix_world
    glyphs = [inflate(g) for g in islands(mark)]                          # letters that touch overlap by 2 µm instead
    tool = glyphs[0]
    for g in glyphs[1:]:
        cut(tool, g, 'UNION')                                             # one clean cutter, then one cut
    return cut(obj, tool)


def decal_plane(obj, at, normal, size=(0.02, 0.01), lift=0.00015, name=None):
    """A plane hovering `lift` above the surface at `at`, wrapped onto obj (shrinkwrap) — give it a material with an
    image and alpha (shading.decal) for a printed label, a logo, a legend."""
    bm = bmesh.new(); r = bmesh.ops.create_grid(bm, x_segments=8, y_segments=8, size=1.0)
    bmesh.ops.scale(bm, verts=r['verts'], vec=(size[0] / 2, size[1] / 2, 1.0))
    p = mesh_object(name or f'{obj.name}.decal', bm)
    p.matrix_world = frame(at, normal) @ Matrix.Translation((0, 0, lift))
    sw = p.modifiers.new('Wrap', 'SHRINKWRAP'); sw.target = obj; sw.wrap_method = 'NEAREST_SURFACEPOINT'; sw.offset = lift
    sw.wrap_mode = 'ABOVE_SURFACE'
    smooth(p, None)
    set_parent(p, obj)
    return p


# ─────────────────────────────── placing on surfaces ───────────────────────────────


def on_surface(obj, from_point, direction):
    """Where a ray from `from_point` along `direction` meets obj: → (point, normal) in world space, or None."""
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get(); ev = obj.evaluated_get(dg)
    inv = ev.matrix_world.inverted()
    o = inv @ Vector(from_point); d = (inv.to_3x3() @ Vector(direction)).normalized()
    hit, loc, nrm, _ = ev.ray_cast(o, d)
    if not hit:
        return None
    return ev.matrix_world @ loc, (ev.matrix_world.to_3x3().inverted().transposed() @ nrm).normalized()


def place(part, at, normal, sink=0.0, up=None):
    """Move a part built flat in XY (facing +Z) onto a surface point so its +Z follows the normal."""
    part.matrix_world = frame(at, normal, up) @ Matrix.Translation((0, 0, -sink))
    return part


def paint(obj, material, where):
    """Give the faces of obj for which where(centre, normal) is true a second material — the metal band on a plastic
    body, the glass area of a lid. Both arguments are world-space Vectors."""
    me = obj.data
    if material not in me.materials.values():
        me.materials.append(material)
    idx = me.materials.values().index(material)
    mw = obj.matrix_world; nm = mw.to_3x3().inverted().transposed()
    for p in me.polygons:
        if where(mw @ p.center, (nm @ p.normal).normalized()):
            p.material_index = idx
    return obj


def mirror(obj, axis='X', merge=0.0001):
    m = obj.modifiers.new('Mirror', 'MIRROR'); m.use_axis = tuple(a == axis for a in 'XYZ'); m.use_clip = True
    m.merge_threshold = merge
    return obj


def array(obj, count=4, offset=(0.02, 0, 0), relative=False):
    a = obj.modifiers.new('Array', 'ARRAY'); a.count = int(count)
    a.use_relative_offset = relative; a.use_constant_offset = not relative
    if relative:
        a.relative_offset_displace = offset
    else:
        a.constant_offset_displace = offset
    return obj


# ─────────────────────────────── checks ───────────────────────────────


def check_topology(obj, orientation=False):
    """What a modeller checks before shading: counts, non-manifold edges (a boolean that failed, an open seam),
    loose vertices, zero-area faces, inside-out normals, unapplied scale. → dict with a 'problems' list."""
    out = {'name': obj.name, 'type': obj.type, 'problems': []}
    if obj.type != 'MESH':
        return out
    bm = evaluated_mesh(obj)
    bm.edges.ensure_lookup_table(); bm.verts.ensure_lookup_table()
    out['verts'] = len(bm.verts); out['faces'] = len(bm.faces)
    out['ngons'] = sum(len(face.verts) > 4 for face in bm.faces)
    nm = [e for e in bm.edges if not e.is_manifold]
    out['non_manifold_edges'] = len(nm)
    boundary = [e for e in nm if e.is_boundary]
    out['boundary_edges'] = len(boundary)
    loose = [v for v in bm.verts if not v.link_edges]
    out['loose_verts'] = len(loose)
    zero = [f for f in bm.faces if f.calc_area() < 1e-12]
    out['zero_area_faces'] = len(zero)
    closed = not nm
    if closed and bm.faces:
        out['volume'] = bm.calc_volume(signed=True)
        if out['volume'] < 0 and not obj.get('wb3d_inverted'):           # a lining is meant to be seen from inside
            out['problems'].append('normals point inward (negative volume)')
    if nm and len(nm) > len(boundary):
        out['problems'].append(f'{len(nm) - len(boundary)} non-manifold edges (a boolean left a mess or faces overlap)')
    if loose:
        out['problems'].append(f'{len(loose)} loose vertices')
    if zero:
        out['problems'].append(f'{len(zero)} zero-area faces')
    if any(abs(s - 1.0) > 1e-6 for s in obj.scale) and obj.animation_data is None:       # animated scale is motion, not a mistake
        out['problems'].append(f'scale {tuple(round(s, 3) for s in obj.scale)} not applied (bevels and booleans go wrong)')
    if orientation:
        bm.normal_update()
        remaining = set(bm.faces)
        inward = assessed = 0
        while remaining:
            component = [remaining.pop()]
            closed_component = True
            for face in component:
                for edge in face.edges:
                    if not edge.is_manifold:
                        closed_component = False
                    for neighbor in edge.link_faces:
                        if neighbor in remaining:
                            remaining.remove(neighbor)
                            component.append(neighbor)
            if closed_component:
                before = [f.normal.copy() for f in component]
                bmesh.ops.recalc_face_normals(bm, faces=component)
                bm.normal_update()
                assessed += len(component)
                inward += sum(n.dot(f.normal) < 0 for n, f in zip(before, component))
        out['inward_faces_estimate'] = inward
        out['orientation_faces_assessed'] = assessed
    bm.free()
    return out
