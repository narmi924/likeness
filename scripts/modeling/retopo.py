"""Blender retopo tools adapted from Scenario Blender Skills."""
# Source: https://github.com/scenario-labs/skills/blob/754d1ceea270f1bf59651671b3471f385a5a80f9/skills/dcc/blender/scenario-blender-retopology/scripts/bx_retopo.py
# Copyright 2026 Scenario Inc. Distributed under the accompanying Scenario-MIT license.

import math
import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree


_BVH_CACHE = {}


def _local_size(target):
    me = target.data
    if not len(me.vertices):
        return 1.0
    co = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    return float((co.max(0) - co.min(0)).max()) or 1.0


def target_bvh(target):
    """BVH of the evaluated target in its LOCAL space (cached per target object)."""
    key = (target.name, len(target.data.vertices), len(target.data.polygons))
    hit = _BVH_CACHE.get(key)
    if hit is None:
        dg = bpy.context.evaluated_depsgraph_get()
        hit = BVHTree.FromObject(target, dg)
        _BVH_CACHE.clear()
        _BVH_CACHE[key] = hit
    return hit


def _ctx(obj):
    return bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj],
                                     selected_editable_objects=[obj])


def _object_mode():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def _local_size_of_mesh(me):
    if not len(me.vertices):
        return 1.0
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    return float((co.max(0) - co.min(0)).max()) or 1.0


def _has_mirror(obj):
    return any(m.type == "MIRROR" and m.show_viewport for m in obj.modifiers)


def setup_cage(target, name="GEO-retopo", mirror=True, wrap="TARGET_PROJECT", subsurf=2,
               offset=0.0, project_limit=None, mesh=None, collection=None):
    """Create the retopo cage object with the expert modifier stack.

    wrap: 'TARGET_PROJECT' (Kaspar's default for skin, On Surface), 'NEAREST_SURFACEPOINT'
    (Dikko, simplest), or 'PROJECT' (clothing shells or double-sided sculpts: both
    directions, Above Surface and a Limit so it cannot grab the inner side; Kaspar Live #7).
    The cage copies the target's matrix_world so Mirror X = the target's local X plane.
    Returns the object. Subsurf is hidden in edit mode (Kaspar, Jamie Dunbar: never build
    with subdivision showing on the cage).
    """
    me = mesh or bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, me)
    coll = collection or (target.users_collection[0] if target.users_collection
                          else bpy.context.scene.collection)
    coll.objects.link(obj)
    obj.matrix_world = target.matrix_world.copy()
    size = _local_size(target)
    if mirror:
        m = obj.modifiers.new("Mirror", "MIRROR")
        m.use_axis = (True, False, False)
        m.use_clip = True
        m.use_mirror_merge = True
        m.merge_threshold = size * 1e-3
    sw = obj.modifiers.new("Shrinkwrap", "SHRINKWRAP")
    sw.target = target
    sw.wrap_method = wrap
    sw.offset = offset
    if wrap == "PROJECT":
        sw.use_negative_direction = True
        sw.use_positive_direction = True
        sw.wrap_mode = "ABOVE_SURFACE"
        sw.project_limit = size * 0.05 if project_limit is None else project_limit
    else:
        sw.wrap_mode = "ON_SURFACE"
    if subsurf:
        s = obj.modifiers.new("Subdivision", "SUBSURF")
        s.levels = subsurf
        s.render_levels = 2
        s.show_in_editmode = False
        s.show_on_cage = False
    obj.show_wire = True
    obj.show_in_front = True
    return obj


def freeze(obj, modifier="Shrinkwrap"):
    """Kaspar's "duplicate and apply": bake the Shrinkwrap into the vertices, keep a live copy.

    Stops twitching, gives relax/bmesh steps real on-surface positions. Re-pins the mirror
    seam afterwards (the projection can pull centre vertices off x = 0).
    """
    _object_mode()
    if modifier not in obj.modifiers:
        raise KeyError(f"{obj.name} has no modifier '{modifier}'")
    with _ctx(obj):
        bpy.ops.object.modifier_copy(modifier=modifier)
        bpy.ops.object.modifier_apply(modifier=modifier)  # "not first" info is expected
    # the copy is named "<name>.001": give it the original name back
    for m in obj.modifiers:
        if m.type == "SHRINKWRAP" and m.name.startswith(modifier + "."):
            m.name = modifier
            break
    if _has_mirror(obj):
        snap_center(obj)
    return obj


def snap_center(obj, eps=None):
    """Put every vertex within eps of x = 0 exactly on the mirror plane. Returns the count."""
    eps = eps if eps is not None else _local_size_of_mesh(obj.data) * 2e-3
    n = 0
    for v in obj.data.vertices:
        if abs(v.co.x) < eps and v.co.x != 0.0:
            v.co.x = 0.0
            n += 1
    obj.data.update()
    return n


def project(obj, target, indices=None, method="nearest", keep_center=None):
    """Move cage vertices (all, or `indices`) onto the target surface.

    method 'nearest' = Shrinkwrap Nearest Surface Point; 'normal' casts along the vertex
    normal both ways first (closer to Target Normal Project), falling back to nearest.
    """
    bvh = target_bvh(target)
    me = obj.data
    keep_center = _has_mirror(obj) if keep_center is None else keep_center
    eps = _local_size_of_mesh(me) * 1e-5
    idx = range(len(me.vertices)) if indices is None else indices
    for i in idx:
        v = me.vertices[i]
        on_center = keep_center and abs(v.co.x) < eps
        p = None
        if method == "normal" and v.normal.length > 0:
            best = None
            for d in (v.normal, -v.normal):
                h = bvh.ray_cast(v.co, d)
                if h[0] is not None and (best is None or h[3] < best[3]):
                    best = h
            p = best[0] if best else None
        if p is None:
            p = bvh.find_nearest(v.co)[0]
        if p is not None:
            v.co = p
            if on_center:
                v.co.x = 0.0
    me.update()


def _adjacency(bm):
    nbr = [[e.other_vert(v).index for e in v.link_edges] for v in bm.verts]
    return nbr


def relax(obj, target, iterations=10, factor=0.5, pin_boundary=True, pin_attr="retopo_crease",
          pin_indices=None, keep_center=None, tangential=True):
    """Relax Slide substitute: tangential Laplacian smoothing + reprojection onto the target.

    Evens out quad sizes without changing the form (Kaspar: "Slide Relax keeps the form
    alive", plain Smooth shrinks it). Pinned: boundary verts (Mesh Boundary auto-masking),
    verts on edges flagged by the boolean edge attribute `pin_attr` (crease loops you
    placed on purpose), explicit `pin_indices`. Centre-line verts stay on x = 0.
    Returns the mean vertex displacement of the last iteration (convergence hint).
    """
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.verts.ensure_lookup_table()
    keep_center = _has_mirror(obj) if keep_center is None else keep_center
    pinned = set(pin_indices or [])
    if pin_boundary:
        pinned |= {v.index for v in bm.verts if v.is_boundary and
                   not (keep_center and abs(v.co.x) < _local_size_of_mesh(me) * 1e-5)}
    attr = me.attributes.get(pin_attr) if pin_attr else None
    if attr is not None and attr.domain == "EDGE":
        flags = [False] * len(me.edges)
        attr.data.foreach_get("value", flags)
        for e in me.edges:
            if flags[e.index]:
                pinned.update(e.vertices)
    nbr = _adjacency(bm)
    bm.free()
    n = len(me.vertices)
    co = np.empty(n * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    eps = _local_size_of_mesh(me) * 1e-5
    center = np.abs(co[:, 0]) < eps if keep_center else np.zeros(n, bool)
    movable = np.array([i not in pinned and len(nbr[i]) > 0 for i in range(n)])
    bvh = target_bvh(target)
    last = 0.0
    for _ in range(iterations):
        lap = np.array([co[nb].mean(0) if nb else co[i] for i, nb in enumerate(nbr)]) - co
        if tangential:
            nrm = np.zeros_like(co)
            for i in range(n):
                if movable[i]:
                    hit = bvh.find_nearest(Vector(co[i]))
                    if hit[1] is not None:
                        nrm[i] = hit[1]
            lap -= (lap * nrm).sum(1)[:, None] * nrm
        new = co + factor * lap
        new[~movable] = co[~movable]
        for i in np.nonzero(movable)[0]:
            p, pn, _, _ = bvh.find_nearest(Vector(new[i]))
            if p is None:
                new[i] = co[i]
            elif tangential and nrm[i].any() and Vector(nrm[i]).dot(pn) < 0.2:
                new[i] = co[i]  # would jump to the other side of a thin part (ear, lip, finger)
            else:
                new[i] = p
        new[center, 0] = 0.0
        last = float(np.linalg.norm(new - co, axis=1)[movable].mean()) if movable.any() else 0.0
        co = new
    me.vertices.foreach_set("co", co.ravel())
    me.update()
    return last


def _mesh_arrays(target):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = target.evaluated_get(dg)
    me = ev.to_mesh()
    nv, ne, nl, npoly = len(me.vertices), len(me.edges), len(me.loops), len(me.polygons)
    co = np.empty(nv * 3)
    me.vertices.foreach_get("co", co)
    ed = np.empty(ne * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", ed)
    le = np.empty(nl, dtype=np.int64)
    me.loops.foreach_get("edge_index", le)
    ls = np.empty(npoly, dtype=np.int64)
    me.polygons.foreach_get("loop_start", ls)
    lt = np.empty(npoly, dtype=np.int64)
    me.polygons.foreach_get("loop_total", lt)
    ev.to_mesh_clear()
    return co.reshape(-1, 3), ed.reshape(-1, 2), le, ls, lt


def _field_contours(co, ed, le, ls, lt, d):
    """Zero-level contours of a per-vertex scalar `d` (non-finite values never cross).
    Returns [(points ndarray (n, 3), closed bool)] ordered along each contour."""
    d = np.array(d, dtype=np.float64)
    d[d == 0] = 1e-12
    da, db = d[ed[:, 0]], d[ed[:, 1]]
    ok = np.isfinite(da) & np.isfinite(db)
    cross = np.nonzero(ok & (da * db < 0))[0]
    if not len(cross):
        return []
    t = da[cross] / (da[cross] - db[cross])
    pts = co[ed[cross, 0]] + (co[ed[cross, 1]] - co[ed[cross, 0]]) * t[:, None]
    slot = {int(e): k for k, e in enumerate(cross)}
    poly_of_loop = np.repeat(np.arange(len(ls)), lt)
    is_cross = np.zeros(len(ed), bool)
    is_cross[cross] = True
    mask = is_cross[le]
    faces = {}
    for f, e in zip(poly_of_loop[mask], le[mask]):
        faces.setdefault(int(f), []).append(slot[int(e)])
    links = [[] for _ in range(len(cross))]
    for lst in faces.values():
        for a in range(0, len(lst) - 1, 2):
            links[lst[a]].append(lst[a + 1])
            links[lst[a + 1]].append(lst[a])
    seen = np.zeros(len(cross), bool)
    out = []
    for s in range(len(cross)):
        if seen[s]:
            continue
        # walk to one end first if open
        start = s
        prev, cur = None, s
        guard = 0
        while guard < len(cross) + 2:
            guard += 1
            nxt = [x for x in links[cur] if x != prev]
            if not nxt or nxt[0] == s:
                break
            prev, cur = cur, nxt[0]
            if len(links[cur]) < 2:
                start = cur
                break
        chain = [start]
        seen[start] = True
        prev, cur = None, start
        closed = False
        while True:
            nxt = [x for x in links[cur] if x != prev]
            if not nxt:
                break
            if nxt[0] == start:
                closed = True
                break
            if seen[nxt[0]]:
                break
            prev, cur = cur, nxt[0]
            chain.append(cur)
            seen[cur] = True
        ci = cross[chain]
        near = np.where(t[chain] < 0.5, ed[ci, 0], ed[ci, 1])
        out.append((pts[chain], closed, near))
    return out


def cross_section(target, plane_co, plane_no):
    """Cut the target with a plane. Returns a list of (points, closed) contours, points as
    Vectors in target-local space, ordered along the contour. Plane sections fold at bends
    (a knee, a curled ear): for limbs prefer geodesic contours (limb_profile)."""
    co, ed, le, ls, lt = _mesh_arrays(target)
    p = np.asarray(plane_co, float)
    n = np.asarray(plane_no, float)
    n = n / np.linalg.norm(n)
    return [([Vector(q) for q in pts], closed)
            for pts, closed, _ in _field_contours(co, ed, le, ls, lt, (co - p) @ n)]


def _resample(points, n, closed=True):
    pts = [Vector(p) for p in points]
    if closed:
        pts = pts + [pts[0]]
    seg = [(pts[i + 1] - pts[i]).length for i in range(len(pts) - 1)]
    total = sum(seg)
    count = n if closed else n - 1
    step = total / count
    out, acc, i = [pts[0].copy()], 0.0, 0
    for k in range(1, count if closed else n):
        goal = k * step
        while i < len(seg) - 1 and acc + seg[i] < goal:
            acc += seg[i]
            i += 1
        t = (goal - acc) / seg[i] if seg[i] else 0.0
        out.append(pts[i].lerp(pts[i + 1], min(max(t, 0.0), 1.0)))
    if not closed:
        out[-1] = pts[-1].copy()
    return out


def ring(target, center, normal, n=8, start_dir=(0, 0, 1)):
    """n evenly spaced surface points around the target's section through `center`.

    Kaspar's limb start: an 8-vertex circle inside the arm, "go as low as possible"
    (Live #4 [00:06:54]); legs 8 then 12 (Live #5 [01:21:00]). Picks the closed contour
    that encircles `center` (smallest one if several); for thin curved parts (ears,
    folds) whose section does not contain its own centroid, falls back to the closed
    contour whose centroid is nearest. Starts at the point furthest along `start_dir`
    and winds counter-clockwise around `normal`, so consecutive rings bridge untwisted.
    """
    c0 = Vector(center)
    nrm = Vector(normal).normalized()
    u = Vector(start_dir) - nrm * Vector(start_dir).dot(nrm)
    if u.length < 1e-9:
        u = nrm.orthogonal()
    u.normalize()
    w = nrm.cross(u)

    def winding(pts, c):
        ang = [math.atan2((p - c).dot(w), (p - c).dot(u)) for p in pts]
        return sum(((ang[(i + 1) % len(ang)] - ang[i] + math.pi) % (2 * math.pi)) - math.pi
                   for i in range(len(ang)))

    enc, near = [], []
    for pts, closed in cross_section(target, c0, nrm):
        if not closed or len(pts) < 3:
            continue
        cc = sum(pts, Vector()) / len(pts)
        radius = sum((p - cc).length for p in pts) / len(pts)
        if abs(winding(pts, c0)) > math.pi:
            enc.append((radius, pts, c0))
        elif (cc - c0).length < radius:
            near.append(((cc - c0).length, pts, cc))
    if enc:
        _, pts, c = min(enc, key=lambda x: x[0])
    elif near:
        _, pts, c = min(near, key=lambda x: x[0])
    else:
        raise ValueError("no closed section at this centre; move the centre inside the part")
    if winding(pts, c) < 0:
        pts = pts[::-1]
    k = max(range(len(pts)), key=lambda i: (pts[i] - c).dot(u))
    pts = pts[k:] + pts[:k]
    return _resample(pts, n, closed=True)


def add_ring(obj, points, closed=True):
    """Append vertices (and edges between them) to the cage mesh. Returns vertex indices."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    vs = [bm.verts.new(Vector(p)) for p in points]
    rng = range(len(vs)) if closed else range(len(vs) - 1)
    for i in rng:
        bm.edges.new((vs[i], vs[(i + 1) % len(vs)]))
    bm.verts.index_update()
    idx = [v.index for v in vs]
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return idx


def _loop_edges(bm, idx, closed=True):
    bm.verts.ensure_lookup_table()
    es = []
    rng = range(len(idx)) if closed else range(len(idx) - 1)
    for i in rng:
        a, b = bm.verts[idx[i]], bm.verts[idx[(i + 1) % len(idx)]]
        e = bm.edges.get((a, b))
        if e is None:
            raise ValueError(f"vertices {idx[i]} and {idx[(i + 1) % len(idx)]} are not connected")
        es.append(e)
    return es


def bridge(obj, loop_a, loop_b, align=True):
    """Quad strip between two closed vertex loops of EQUAL count (Kaspar: count and match
    before connecting). align=True picks the cyclic shift and direction of loop_b that
    minimises total edge length, so loops built independently connect without a twist.
    Returns the number of faces created."""
    if len(loop_a) != len(loop_b):
        raise ValueError(f"ring counts differ ({len(loop_a)} vs {len(loop_b)}): add or remove "
                         "loops on one side first, do not let bridge make triangles")
    me = obj.data
    n = len(loop_a)
    lb = list(loop_b)
    if align:
        A = [me.vertices[i].co.copy() for i in loop_a]
        best = None
        for cand in (lb, lb[::-1]):
            B = [me.vertices[i].co.copy() for i in cand]
            for sft in range(n):
                cost = sum((A[i] - B[(i + sft) % n]).length for i in range(n))
                if best is None or cost < best[0]:
                    best = (cost, cand[sft:] + cand[:sft])
        lb = best[1]
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.verts.ensure_lookup_table()
    faces = []
    for i in range(n):
        q = [bm.verts[loop_a[i]], bm.verts[loop_a[(i + 1) % n]],
             bm.verts[lb[(i + 1) % n]], bm.verts[lb[i]]]
        faces.append(bm.faces.new(q))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(me)
    bm.free()
    me.update()
    return len(faces)


def grid_fill(obj, loop, span=None, offset=0, target=None):
    """Fill a closed boundary loop with a quad grid (Edit Mode Grid Fill, headless-safe).

    Needs an EVEN vertex count: an odd loop "finishes" with zero faces (silent failure in
    5.2.1), so this raises instead. Dikko and Kenny count before every fill.
    target: project the new interior vertices onto it (a cap that follows a tip or dome).
    Returns the number of faces created.
    """
    if len(loop) % 2:
        raise ValueError(f"grid fill needs an even loop, got {len(loop)}: add or dissolve one vertex")
    _object_mode()
    before = len(obj.data.polygons)
    nv0 = len(obj.data.vertices)
    vl = bpy.context.view_layer
    prev_active = vl.objects.active
    vl.objects.active = obj
    obj.select_set(True)
    with _ctx(obj):
        bpy.ops.object.mode_set(mode="EDIT")
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        for v in bm.verts:
            v.select = False
        for e in bm.edges:
            e.select = False
        for f in bm.faces:
            f.select = False
        for e in _loop_edges(bm, loop):
            e.select = True
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)
        kw = {"offset": offset}
        if span:
            kw["span"] = span
        bpy.ops.mesh.fill_grid(**kw)
        bpy.ops.object.mode_set(mode="OBJECT")
    if prev_active is not None and prev_active is not obj:
        vl.objects.active = prev_active
    made = len(obj.data.polygons) - before
    if made <= 0:
        raise RuntimeError("grid fill created no faces (loop not closed or shape too irregular)")
    if target is not None and len(obj.data.vertices) > nv0:
        project(obj, target, indices=range(nv0, len(obj.data.vertices)))
    return made
