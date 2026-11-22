"""Blender rig tools adapted from Scenario Blender Skills."""
# Source: https://github.com/scenario-labs/skills/blob/754d1ceea270f1bf59651671b3471f385a5a80f9/skills/dcc/blender/scenario-blender-rigging/scripts/bx_rig.py
# Copyright 2026 Scenario Inc. Distributed under the accompanying Scenario-MIT license.

import math
import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector
from mathutils.kdtree import KDTree


def activate(obj, mode="OBJECT", select=()):
    """Make obj the only selected, active object and enter `mode`."""
    ctx_obj = bpy.context.view_layer.objects.active
    if ctx_obj is not None and ctx_obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.hide_set(False)
    obj.hide_viewport = False
    for o in select:
        o.hide_set(False)
        o.select_set(True)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    if mode != "OBJECT":
        bpy.ops.object.mode_set(mode=mode)
    return obj


def evaluate(obj=None):
    """Headless: re-evaluate after changing pose values, custom props or drivers."""
    if obj is not None:
        obj.update_tag()
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    return obj.evaluated_get(dg) if obj is not None else dg


def world_coords(obj, evaluated=True, no_subdiv=False):
    """(N,3) world-space vertex positions (evaluated = after modifiers, shape keys).
    no_subdiv=True hides Subdivision/Multires while evaluating so vertex indices
    match the mesh (needed for per-vertex measurements)."""
    if evaluated and no_subdiv:
        hidden = [m for m in obj.modifiers if m.type in ("SUBSURF", "MULTIRES") and m.show_viewport]
        for m in hidden:
            m.show_viewport = False
        try:
            return world_coords(obj, evaluated=True)
        finally:
            for m in hidden:
                m.show_viewport = True
    if evaluated:
        dg = evaluate()
        eo = obj.evaluated_get(dg)
        me = eo.to_mesh()
        a = np.empty(len(me.vertices) * 3)
        me.vertices.foreach_get("co", a)
        mw = np.array(eo.matrix_world)
        eo.to_mesh_clear()
    else:
        me = obj.data
        a = np.empty(len(me.vertices) * 3)
        me.vertices.foreach_get("co", a)
        mw = np.array(obj.matrix_world)
    a = a.reshape(-1, 3)
    return a @ mw[:3, :3].T + mw[:3, 3]


def _v(a):
    return Vector((float(a[0]), float(a[1]), float(a[2])))


def side_of(name):
    """'L', 'R' or '' from Blender's mirror naming (upper_arm.L, lid.T.L.001, f_index.01.R)."""
    import re
    m = re.search(r"[._-]([LlRr])(?=$|[._-]\d*$|\.)", name)
    return m.group(1).upper() if m else ""


class Sections:
    """Plane cuts through one or more meshes, split into closed loops by topology.

    loops = Sections([body]).cut(point, normal) -> list of dict(center, pts, radius,
    lo, hi) sorted by center x. Centroids of limb loops are joint positions "in the
    centre of the volume" (CGDive), the substitute for Snap to Volume."""

    def __init__(self, objs, evaluated=False):
        Vs, Es, LE, LP = [], [], [], []
        voff = eoff = poff = 0
        for o in objs:
            if evaluated:
                eo = o.evaluated_get(evaluate())
                me = eo.to_mesh()
            else:
                me = o.data
            nv, ne, nl, npol = len(me.vertices), len(me.edges), len(me.loops), len(me.polygons)
            co = np.empty(nv * 3)
            me.vertices.foreach_get("co", co)
            mw = np.array(o.matrix_world)
            Vs.append(co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3])
            ed = np.empty(ne * 2, dtype=np.int64)
            me.edges.foreach_get("vertices", ed)
            Es.append(ed.reshape(-1, 2) + voff)
            le = np.empty(nl, dtype=np.int64)
            me.loops.foreach_get("edge_index", le)
            lt = np.empty(npol, dtype=np.int64)
            me.polygons.foreach_get("loop_total", lt)
            LE.append(le + eoff)
            LP.append(np.repeat(np.arange(npol) + poff, lt))
            voff, eoff, poff = voff + nv, eoff + ne, poff + npol
            if evaluated:
                eo.to_mesh_clear()
        self.V = np.vstack(Vs)
        self.E = np.vstack(Es)
        self.loop_edge = np.concatenate(LE)
        self.loop_poly = np.concatenate(LP)

    def cut(self, point, normal, min_pts=4):
        n = np.asarray(normal, dtype=float)
        n = n / np.linalg.norm(n)
        d = (self.V - np.asarray(point, dtype=float)) @ n
        d[d == 0.0] = 1e-12
        s = d > 0
        E0, E1 = self.E[:, 0], self.E[:, 1]
        cross = s[E0] != s[E1]
        idx = np.nonzero(cross)[0]
        if len(idx) == 0:
            return []
        t = d[E0[idx]] / (d[E0[idx]] - d[E1[idx]])
        pts = self.V[E0[idx]] + (self.V[E1[idx]] - self.V[E0[idx]]) * t[:, None]
        emap = np.full(len(self.E), -1, dtype=np.int64)
        emap[idx] = np.arange(len(idx))
        m = cross[self.loop_edge]
        le = emap[self.loop_edge[m]]
        lp = self.loop_poly[m]
        parent = list(range(len(idx)))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        same = np.nonzero(lp[:-1] == lp[1:])[0]
        for i in same:
            ra, rb = find(int(le[i])), find(int(le[i + 1]))
            if ra != rb:
                parent[ra] = rb
        roots = np.array([find(i) for i in range(len(idx))])
        loops = []
        for r in np.unique(roots):
            p = pts[roots == r]
            if len(p) < min_pts:
                continue
            c = p.mean(0)
            loops.append({"center": c, "pts": p, "radius": float(np.linalg.norm(p - c, axis=1).mean()),
                          "lo": p.min(0), "hi": p.max(0), "n": len(p)})
        loops.sort(key=lambda L: L["center"][0])
        return loops


def landmarks(body, eyes=None, overrides=None, symmetric=True):
    """Joint positions for a humanoid in T or A pose, facing -Y, feet near Z=min.

    Method (agent substitute for volume snapping, CGDive/Demeter placement rules):
    horizontal cuts find legs, crotch, ankle, spine mid-line and the neck (narrowest
    section between shoulders and head); vertical cuts then cuts normal to the arm axis
    find armpit, wrist (narrowest section of the outer arm) and hand tip. Joints sit
    at section centroids; elbow/knee at proportional positions along the limb.
    Returns {name: Vector} for the LEFT (+X) side plus centre joints, 'H', 'floor',
    'pose' ('T'/'A'), 'arm_angle' (deg below horizontal) and 'notes'. Pass
    overrides={name: (x,y,z)} to correct any joint; always check the result with
    check_metarig() and a render."""
    S = Sections([body])
    V = S.V
    zmin, zmax = float(V[:, 2].min()), float(V[:, 2].max())
    H = zmax - zmin
    notes = []
    lm = {"H": H, "floor": zmin}

    def hcut(z):
        return S.cut((0, 0, z), (0, 0, 1))

    # --- legs and crotch: scan upward until one loop spans x = 0
    crotch = None
    leg_lo = {}
    for z in np.linspace(zmin + 0.03 * H, zmin + 0.7 * H, 90):
        loops = hcut(z)
        span = [L for L in loops if L["lo"][0] < 0 < L["hi"][0] and L["hi"][0] - L["lo"][0] < 0.6 * H]
        if span and z > zmin + 0.15 * H:
            crotch = float(z)
            break
        left = [L for L in loops if L["lo"][0] > 0]
        right = [L for L in loops if L["hi"][0] < 0]
        if left:
            leg_lo[float(z)] = (min(left, key=lambda L: L["center"][0]),
                                max(right, key=lambda L: L["center"][0]) if right else None)
    if crotch is None:
        raise RuntimeError("landmarks: no crotch found (legs not separated, or not a biped)")
    zs = sorted(leg_lo)
    below = [z for z in zs if z < crotch - 0.02 * H] or zs
    lm["crotch"] = Vector((0, 0, crotch))

    def leg_loop(z, side=0):
        loops = hcut(z)
        cand = [L for L in loops if (L["lo"][0] > 0 if side == 0 else L["hi"][0] < 0)]
        if not cand:
            return None
        return min(cand, key=lambda L: abs(L["center"][0]))

    def mirror_avg(Lc, Rc):
        a = np.array(Lc, dtype=float)
        if Rc is not None and symmetric:
            b = np.array(Rc, dtype=float) * np.array([-1, 1, 1])
            a = (a + b) / 2
        return a

    # hip joint: middle of the hip mass, a little above the crotch
    zl = below[-1]
    Lh, Rh = leg_lo[zl]
    hip_c = mirror_avg(Lh["center"], Rh["center"] if Rh else None)
    hip_z = crotch + 0.13 * (crotch - zmin)
    lm["hip"] = Vector((hip_c[0], hip_c[1], hip_z))

    # ankle: first cut (from the floor up) where the leg section becomes round
    ankle_z = None
    for z in np.linspace(zmin + 0.02 * H, zmin + 0.14 * H, 40):
        L = leg_loop(z)
        if L is None:
            continue
        ex, ey = L["hi"][0] - L["lo"][0], L["hi"][1] - L["lo"][1]
        if ey < 1.3 * ex:
            ankle_z = float(z)
            break
    if ankle_z is None:
        ankle_z = zmin + 0.05 * H
        notes.append("ankle: fallback 0.05 H")
    ankle_z = min(max(ankle_z, zmin + 0.03 * H), zmin + 0.12 * H)
    La, Ra = leg_loop(ankle_z), leg_loop(ankle_z, 1)
    a_c = mirror_avg(La["center"], Ra["center"] if Ra else None)
    lm["ankle"] = Vector((a_c[0], a_c[1], ankle_z))

    # foot: toe tip, heel, ball from the vertices below the ankle on the left side
    fv = V[(V[:, 2] < ankle_z) & (V[:, 0] > 0)]
    tip = fv[fv[:, 1].argmin()]
    heel_y = float(fv[:, 1].max())
    foot_len = heel_y - tip[1]
    ball_y = tip[1] + 0.33 * foot_len
    near = fv[np.abs(fv[:, 1] - ball_y) < 0.05 * foot_len]
    ball_x = float(near[:, 0].mean()) if len(near) else float(a_c[0])
    ball_z = zmin + 0.22 * (ankle_z - zmin)
    lm["ball"] = Vector((ball_x, ball_y, ball_z))
    lm["toe_tip"] = Vector((ball_x, float(tip[1]), ball_z))
    wv = fv[np.abs(fv[:, 1] - (heel_y - 0.1 * foot_len)) < 0.08 * foot_len]
    hx0, hx1 = (float(wv[:, 0].min()), float(wv[:, 0].max())) if len(wv) else (a_c[0] - 0.02 * H, a_c[0] + 0.02 * H)
    lm["heel_in"] = Vector((hx0, heel_y, zmin))
    lm["heel_out"] = Vector((hx1, heel_y, zmin))

    # knee: halfway hip -> ankle, section centre; chain straight in front view
    kz = ankle_z + 0.5 * (hip_z - ankle_z)
    Lk, Rk = leg_loop(kz), leg_loop(kz, 1)
    k_c = mirror_avg(Lk["center"], Rk["center"] if Rk else None) if Lk else np.array(lm["ankle"])
    t = (kz - ankle_z) / (hip_z - ankle_z)
    line = lm["ankle"].lerp(lm["hip"], t)
    lm["knee"] = Vector((line.x, float(k_c[1]), kz))

    # --- torso mid-line (front/back surface midpoint at x ~ 0) and the neck
    def central(z):
        loops = hcut(z)
        c = [L for L in loops if L["lo"][0] <= 0.0 <= L["hi"][0]]
        return max(c, key=lambda L: L["n"]) if c else None

    def midline_y(L, band):
        p = L["pts"]
        m = p[np.abs(p[:, 0]) < band]
        if len(m) < 2:
            m = p
        return float((m[:, 1].min() + m[:, 1].max()) / 2), float(m[:, 1].min()), float(m[:, 1].max())

    widths = []
    for z in np.linspace(zmax - 0.01 * H, crotch + 0.15 * H, 120):
        L = central(float(z))
        if L is not None:
            widths.append((float(z), float(L["hi"][0] - L["lo"][0]), L))
    head_max = 0.0
    neck = None
    passed_head = False
    for z, w, L in widths:
        if not passed_head:
            if w >= head_max:
                head_max = w
            elif w < 0.85 * head_max:
                passed_head = True
                neck = (z, w, L)
        else:
            if w < neck[1]:
                neck = (z, w, L)
            elif w > 1.6 * neck[1]:
                break
    if neck is None:
        raise RuntimeError("landmarks: neck not found")
    z_nk, w_nk = neck[0], neck[1]
    neck_base_z = None
    for z, w, L in widths:
        if z < z_nk and w > 1.5 * w_nk:
            neck_base_z = z
            break
    if neck_base_z is None:
        neck_base_z = z_nk - 0.04 * H
        notes.append("neck base: fallback")
    neck_base_z = min(max(neck_base_z, z_nk - 0.07 * H), z_nk - 0.01 * H)
    pivot_z = z_nk + 0.2 * (zmax - z_nk)
    Lp = central(pivot_z)
    py = midline_y(Lp, 0.02 * H)[0] if Lp else neck[2]["center"][1]
    lm["head_pivot"] = Vector((0, py, pivot_z))
    lm["head_top"] = Vector((0, py, zmax))
    Lnb = central(neck_base_z)
    nby = midline_y(Lnb, 0.02 * H)[0] if Lnb else py
    lm["neck_base"] = Vector((0, nby, neck_base_z))
    lm["neck_mid"] = Vector((0, (nby + py) / 2, (neck_base_z + pivot_z) / 2))
    pelvis_z = hip_z - 0.03 * H
    Lpv = central(pelvis_z)
    lm["pelvis"] = Vector((0, midline_y(Lpv, 0.03 * H)[0] if Lpv else hip_c[1], pelvis_z))
    for i, f in enumerate((0.226, 0.437, 0.703)):
        z = pelvis_z + f * (neck_base_z - pelvis_z)
        Ls = central(z)
        lm[f"spine{i + 1}"] = Vector((0, midline_y(Ls, 0.03 * H)[0] if Ls else 0.0, z))
    chest_z = lm["spine3"].z
    Lc = central(pelvis_z + 0.55 * (neck_base_z - pelvis_z))
    my, fy, by = midline_y(Lc, 0.03 * H)
    lm["chest_front"] = Vector((0, fy, chest_z))
    lm["chest_back"] = Vector((0, by, chest_z))

    # --- arms (left side): start on the forearm (65% of the way to the farthest +X
    # vertex, clear of both torso and fingers), then trace the arm loop inward with
    # x-planes until it merges with the torso: the size jumps (the armpit).
    # Tracing from the fingertip gets lost between the fingers.
    tipv = V[V[:, 0].argmax()]
    lm["hand_tip"] = _v(tipv)
    x0 = 0.65 * float(tipv[0])
    loops = S.cut((x0, 0, 0), (1, 0, 0))
    if not loops:
        raise RuntimeError("landmarks: no arm section at 65% of the arm span")
    start = min(loops, key=lambda L: np.linalg.norm(L["center"][1:] - tipv[1:]))
    arm_loops = []
    prev, prev_size = start["center"], None
    armpit_x = None
    for x in np.linspace(x0, 0.04 * H, 160):
        loops = S.cut((x, 0, 0), (1, 0, 0))
        if not loops:
            continue
        L = min(loops, key=lambda L: np.linalg.norm(L["center"][1:] - prev[1:]))
        size = max(L["hi"][2] - L["lo"][2], L["hi"][1] - L["lo"][1])
        if prev_size is not None and len(arm_loops) > 4 and (
                size > 1.5 * prev_size or np.linalg.norm(L["center"][1:] - prev[1:]) > 0.5 * prev_size):
            armpit_x = float(x)
            break
        arm_loops.append((float(x), L["center"].copy(), size))
        prev, prev_size = L["center"], size
    if armpit_x is None or len(arm_loops) < 5:
        raise RuntimeError("landmarks: arm does not separate from the torso (arms too close?)")
    # pass 2: cuts normal to the arm axis
    pts = np.array([c for _, c, _ in arm_loops])
    a0, a1 = pts[-1], tipv
    axis = (a1 - a0) / np.linalg.norm(a1 - a0)
    arm_len = float(np.linalg.norm(a1 - a0))
    prof = []
    for f in np.linspace(0.02, 0.97, 80):
        p = a0 + axis * f * arm_len
        loops = S.cut(p, axis)
        near = [L for L in loops if np.linalg.norm(L["center"] - p) < 0.12 * H]
        if not near:
            continue
        allp = np.vstack([L["pts"] for L in near])
        c = allp.mean(0) if len(near) > 1 else min(near, key=lambda L: np.linalg.norm(L["center"] - p))["center"]
        u = np.cross(axis, [0, 0, 1.0])
        if np.linalg.norm(u) < 1e-6:
            u = np.array([0, 1.0, 0])
        u /= np.linalg.norm(u)
        w2 = np.cross(axis, u)
        ext = max(np.ptp(allp @ u), np.ptp(allp @ w2))
        prof.append((float(f), c, float(ext)))
    outer = [q for q in prof if 0.6 <= q[0] <= 0.9]
    wf, wc, _ = min(outer, key=lambda q: q[2])
    wrist = _v(wc)
    inner = [q for q in prof if q[0] < 0.12]
    r_arm = float(np.median([q[2] for q in inner])) / 2 if inner else 0.03 * H
    sj = a0 + axis * (-0.6 * r_arm)
    lm["shoulder"] = Vector((sj[0], sj[1], sj[2] + 0.25 * r_arm))
    lm["wrist"] = wrist
    lm["armpit_x"] = armpit_x
    up = lm["wrist"] - lm["shoulder"]
    lm["elbow"] = lm["shoulder"] + up * 0.53
    hand_dir = (lm["hand_tip"] - lm["wrist"])
    lm["hand_end"] = lm["wrist"] + hand_dir * 0.45
    ang = math.degrees(math.atan2(-(lm["wrist"].z - lm["shoulder"].z),
                                  lm["wrist"].x - lm["shoulder"].x))
    lm["arm_angle"] = ang
    lm["pose"] = "T" if ang < 25 else "A"
    # clavicle: high and deep in the shoulder volume (CGDive), from near the mid-line
    depth = lm["chest_back"].y - lm["chest_front"].y
    cz = lm["shoulder"].z + 0.02 * H
    lm["clav_head"] = Vector((0.1 * lm["shoulder"].x, lm["chest_front"].y + 0.3 * depth, cz))
    lm["clav_tail"] = Vector((0.85 * lm["shoulder"].x, lm["shoulder"].y, cz))
    # eyes: centre of each island of the eye object
    if eyes is not None:
        EV = world_coords(eyes, evaluated=True)
        for side, m in (("eye", EV[:, 0] > 0), ("eye_r", EV[:, 0] < 0)):
            if m.any():
                lm[side] = _v(EV[m].mean(0))
                lm[side + "_radius"] = float(np.ptp(EV[m][:, 2]) / 2)
    head = V[V[:, 2] > z_nk]
    lm["head_front_y"] = float(head[np.abs(head[:, 0]) < 0.03 * H][:, 1].min())
    band = head[np.abs(head[:, 2] - (pivot_z + 0.35 * (zmax - pivot_z))) < 0.02 * H]
    lm["head_half_width"] = float(np.abs(band[:, 0]).max()) if len(band) else None
    # front profile of the face (x ~ 0): nose tip = most forward point above the neck,
    # chin bottom = where the profile retreats past halfway back to the neck front
    mid = V[(np.abs(V[:, 0]) < 0.012 * H) & (V[:, 2] > neck_base_z - 0.01 * H)]
    if len(mid) > 20:
        nose = mid[mid[:, 2] > z_nk][mid[mid[:, 2] > z_nk][:, 1].argmin()]
        zs_ = np.linspace(nose[2], neck_base_z, 80)
        prof = []
        for zz in zs_:
            sl = mid[np.abs(mid[:, 2] - zz) < 0.006 * H]
            if len(sl):
                prof.append((float(zz), float(sl[:, 1].min())))
        if prof:
            neck_front = prof[-1][1]
            half = (nose[1] + neck_front) / 2
            fwd = [zz for zz, yy in prof if zz < nose[2] - 0.02 * H and yy < half]
            chin = min(fwd) if fwd else None      # lowest point still forward = under the chin
            if chin is not None:
                if nose[1] > lm["head_pivot"].y:
                    notes.append("nose tip behind the head pivot: does the character face -Y?")
                lm["nose_tip"] = _v(nose)
                lm["chin_bottom"] = Vector((0.0, float(half), chin))
    lm["notes"] = notes
    for k, v in (overrides or {}).items():
        lm[k] = Vector(v) if not isinstance(v, (int, float, str)) else v
    return lm


METARIGS = {
    "basic": "armature_basic_human_metarig_add",      # no fingers, no face: first rigs
    "human": "armature_human_metarig_add",            # fingers + legacy face (upgrade it)
    "basic_quadruped": "armature_basic_quadruped_metarig_add",
    "wolf": "armature_wolf_metarig_add", "horse": "armature_horse_metarig_add",
    "cat": "armature_cat_metarig_add", "bird": "armature_bird_metarig_add",
    "shark": "armature_shark_metarig_add",
}


def enable_rigify():
    import addon_utils
    if not addon_utils.check("rigify")[1]:
        bpy.ops.preferences.addon_enable(module="rigify")


def add_metarig(kind="basic", name=None):
    """Add a Rigify metarig at the world origin (Rigify is off on factory startup)."""
    enable_rigify()
    if bpy.context.view_layer.objects.active and bpy.context.view_layer.objects.active.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.scene.cursor.location = (0, 0, 0)
    getattr(bpy.ops.object, METARIGS[kind])()
    meta = bpy.context.active_object
    if name:
        meta.name = name
    return meta


def _descendants(eb, root):
    return [b.name for b in eb if b.name == root or any(p.name == root for p in b.parent_recursive)]


def _frame(d, n):
    d = d.normalized()
    n = (n - d * n.dot(d)).normalized()
    return Matrix((d, n, d.cross(n))).transposed()


def _set_limb_roll(eb, names):
    """Local X = hinge axis = normal of the chain plane (Rigify's own convention)."""
    a, b = eb[names[0]], eb[names[1]]
    n = (a.tail - a.head).cross(b.tail - b.head)
    if n.length < 1e-9:
        return None
    n.normalize()
    for nm in names:
        e = eb[nm]
        e.align_roll(n.cross((e.tail - e.head).normalized()))
    return n


def transform_bones(eb, names, M):
    """Edit Mode: apply matrix M to a group of bones (heads, tails, rolls) safely.

    EditBone.transform() on connected chains moves shared ends twice (a parent's tail
    is also the child's head); this writes every end once from a snapshot."""
    names = [n for n in names if n in eb]
    R3 = M.to_3x3().normalized()
    snap = {n: (eb[n].head.copy(), eb[n].tail.copy(), eb[n].z_axis.copy(), eb[n].use_connect,
                eb[n].parent.name if eb[n].parent else None) for n in names}
    for n in names:
        eb[n].tail = M @ snap[n][1]
    for n in names:
        if not (snap[n][3] and snap[n][4] in snap):
            eb[n].head = M @ snap[n][0]
    for n in names:
        eb[n].align_roll(R3 @ snap[n][2])


def move_points(eb, moves, eps=1e-5):
    """Edit Mode: move every bone end sitting on an old point to its new point.

    moves = [(old, new), ...]. This is CGDive's "box-select the joint and move all
    ends together": coincident ends stay coincident (Rigify chains, face glue, lid
    corners). Tails first, then heads of disconnected bones only, because setting a
    parent's tail already moves a connected child's head."""
    def target(co):
        for old, new in moves:
            if (co - old).length < eps:
                return new
        return None
    snap = {b.name: (b.head.copy(), b.tail.copy(), b.use_connect) for b in eb}
    for b in eb:
        t = target(snap[b.name][1])
        if t is not None:
            b.tail = t
    for b in eb:
        if not snap[b.name][2]:
            h = target(snap[b.name][0])
            if h is not None:
                b.head = h


def fit_metarig(meta, lm, knee_bend=0.012, elbow_bend=0.012, support_bones=True, move_eyes=True):
    """Place a Rigify human/basic-human metarig on landmarks() output.

    Joints are written as shared points so coincident ends stay coincident (CGDive:
    move joints as units). Knees get a forward and elbows a backward pre-bend of
    `*_bend` x limb length at mid-chain (about 2.7 deg between segments), chains stay
    straight in the other view, limb rolls put the hinge on local X, and the left side
    is symmetrized to the right. Human metarig: hand+fingers and face are carried by a
    similarity transform (coarse: check the fist test and face renders), eye bones
    and lids moved onto the eye centres. Call before rigify_upgrade_face()."""
    activate(meta, "EDIT")
    eb = meta.data.edit_bones
    P = {k: Vector(v) for k, v in lm.items() if isinstance(v, Vector)}
    human = "palm.01.L" in eb
    face = "face" in eb
    # defaults captured before editing
    if human:
        hand_grp = _descendants(eb, "hand.L")
        W0, T0 = eb["hand.L"].head.copy(), eb["f_middle.03.L"].tail.copy()
        n0 = (eb["upper_arm.L"].tail - eb["upper_arm.L"].head).cross(eb["forearm.L"].tail - eb["forearm.L"].head)
    if face:
        face_grp = _descendants(eb, "face")
        piv0, top0 = eb["spine.006"].head.copy(), eb["spine.006"].tail.copy()
        eye0 = eb["eye.L"].head.copy() if "eye.L" in eb else None
    # spine, neck, head (centre line)
    chain = ["pelvis", "spine1", "spine2", "spine3", "neck_base"]
    for i, nm in enumerate(("spine", "spine.001", "spine.002", "spine.003")):
        eb[nm].head, eb[nm].tail = P[chain[i]], P[chain[i + 1]]
    eb["spine.004"].head, eb["spine.004"].tail = P["neck_base"], P["neck_mid"]
    eb["spine.005"].head, eb["spine.005"].tail = P["neck_mid"], P["head_pivot"]
    eb["spine.006"].head, eb["spine.006"].tail = P["head_pivot"], P["head_top"]
    for nm in ("spine", "spine.001", "spine.002", "spine.003", "spine.004", "spine.005", "spine.006"):
        eb[nm].align_roll(Vector((0, -1, 0)))
    # leg with forward knee pre-bend, straight from the front
    hip, knee, ankle = P["hip"], P["knee"].copy(), P["ankle"]
    L = (ankle - hip).length
    t = (knee.z - hip.z) / (ankle.z - hip.z)
    line = hip.lerp(ankle, t)
    knee.x = line.x
    knee.y = min(knee.y, line.y - knee_bend * L)
    eb["thigh.L"].head, eb["thigh.L"].tail = hip, knee
    eb["shin.L"].head, eb["shin.L"].tail = knee, ankle
    eb["foot.L"].head, eb["foot.L"].tail = ankle, P["ball"]
    eb["toe.L"].head, eb["toe.L"].tail = P["ball"], P["toe_tip"]
    eb["heel.02.L"].head, eb["heel.02.L"].tail = P["heel_in"], P["heel_out"]
    _set_limb_roll(eb, ["thigh.L", "shin.L"])
    f = eb["foot.L"]
    f.align_roll(Vector((1, 0, 0)).cross((f.tail - f.head).normalized()))
    eb["toe.L"].align_roll(Vector((0, 0, 1)))
    eb["heel.02.L"].align_roll(Vector((0, 0, 1)))
    # arm with backward elbow pre-bend (hinge axis from the bend), shoulder high and deep
    sh, wr = P["shoulder"], P["wrist"]
    el = sh.lerp(wr, 0.53)
    el.y += elbow_bend * (wr - sh).length
    eb["shoulder.L"].head, eb["shoulder.L"].tail = P["clav_head"], P["clav_tail"]
    eb["shoulder.L"].align_roll(Vector((0, 0, 1)))
    eb["upper_arm.L"].head, eb["upper_arm.L"].tail = sh, el
    eb["forearm.L"].head, eb["forearm.L"].tail = el, wr
    if human:
        n1 = (el - sh).cross(wr - el)
        T = P["hand_tip"]
        R = _frame(T - wr, n1) @ _frame(T0 - W0, n0).inverted()
        s = (T - wr).length / (T0 - W0).length
        M = Matrix.Translation(wr) @ R.to_4x4() @ Matrix.Scale(s, 4) @ Matrix.Translation(-W0)
        transform_bones(eb, hand_grp, M)
        eb["forearm.L"].tail = wr
        _set_limb_roll(eb, ["upper_arm.L", "forearm.L"])
    else:
        eb["hand.L"].head, eb["hand.L"].tail = wr, P["hand_end"]
        _set_limb_roll(eb, ["upper_arm.L", "forearm.L"])
        n = (el - sh).cross(wr - el).normalized()
        h = eb["hand.L"]
        h.align_roll(n.cross((h.tail - h.head).normalized()))
    # support bones for automatic weights (CGDive: delete them if you paint the chest)
    if support_bones and "breast.L" in eb:
        depth = P["chest_back"].y - P["chest_front"].y
        z = P["spine3"].z
        x = 0.6 * sh.x
        eb["breast.L"].head = Vector((x, P["chest_front"].y + 0.7 * depth, z))
        eb["breast.L"].tail = Vector((x, P["chest_front"].y + 0.05 * depth, z))
        eb["pelvis.L"].head = P["pelvis"]
        eb["pelvis.L"].tail = Vector((1.13 * hip.x, hip.y - 0.03 * lm["H"], hip.z + 0.04 * lm["H"]))
    elif not support_bones:
        for nm in ("breast.L", "breast.R", "pelvis.L", "pelvis.R"):
            if nm in eb:
                eb.remove(eb[nm])
    # face: similarity from the metarig head to the mesh head, then eyes onto the eyeballs
    if face:
        piv, top = P["head_pivot"], P["head_top"]
        s = (top.z - piv.z) / (top0.z - piv0.z)
        w0 = max(max(abs(eb[n].head.x), abs(eb[n].tail.x)) for n in face_grp)
        sx = (lm["head_half_width"] / w0) if lm.get("head_half_width") else s
        if all(k in P for k in ("nose_tip", "chin_bottom", "eye")) and P["chin_bottom"].z < P["nose_tip"].z < P["eye"].z:
            # piecewise vertical map through chin bottom, nose tip, eye line, head top
            pts0 = [(n, e) for n in face_grp for e in (eb[n].head, eb[n].tail)]
            nose0 = min(pts0, key=lambda t: t[1].y)[1].copy()
            chin0 = eb["chin"].head.copy() if "chin" in eb else None
            k0 = [chin0.z, nose0.z, eye0.z, top0.z]
            k1 = [P["chin_bottom"].z, P["nose_tip"].z, P["eye"].z, top.z]
            sy = (k1[2] - k1[0]) / (k0[2] - k0[0])

            def fz(z):
                if z <= k0[0]:
                    return k1[0] + (z - k0[0]) * (k1[1] - k1[0]) / (k0[1] - k0[0])
                if z >= k0[3]:
                    return k1[3] + (z - k0[3]) * (k1[3] - k1[2]) / (k0[3] - k0[2])
                return float(np.interp(z, k0, k1))
            snap = {n: (eb[n].head.copy(), eb[n].tail.copy(), eb[n].z_axis.copy(), eb[n].use_connect,
                        eb[n].parent.name if eb[n].parent else None) for n in face_grp}

            def f(p):
                return Vector((p.x * sx, P["nose_tip"].y + (p.y - nose0.y) * sy, fz(p.z)))
            for n in face_grp:
                eb[n].tail = f(snap[n][1])
            for n in face_grp:
                if not (snap[n][3] and snap[n][4] in snap):
                    eb[n].head = f(snap[n][0])
            for n in face_grp:
                eb[n].align_roll(snap[n][2])
        else:
            M = Matrix.Translation(piv) @ Matrix.Diagonal((sx, s, s, 1.0)) @ Matrix.Translation(-piv0)
            transform_bones(eb, face_grp, M)
            front = min(min(eb[n].head.y, eb[n].tail.y) for n in face_grp)
            dy = lm.get("head_front_y", front) - front
            transform_bones(eb, face_grp, Matrix.Translation((0, dy, 0)))
        if move_eyes and "eye" in P and eye0 is not None:
            d = P["eye"] - eb["eye.L"].head
            moves = []
            for nm in [n for n in face_grp if side_of(n) == "L" and n.split(".")[0] in ("eye", "lid")]:
                moves += [(eb[nm].head.copy(), eb[nm].head + d), (eb[nm].tail.copy(), eb[nm].tail + d)]
            move_points(eb, moves)
    # mirror the left side onto the right (rig types and parameters included)
    for b in eb:
        b.select = b.select_head = b.select_tail = side_of(b.name) == "L"
    bpy.ops.armature.symmetrize(direction="NEGATIVE_X")
    for b in eb:
        b.select = b.select_head = b.select_tail = False
    bpy.ops.object.mode_set(mode="OBJECT")
    return meta


_BVH = {}


def _bvh(obj):
    from mathutils.bvhtree import BVHTree
    key = (obj.name, len(obj.data.vertices), tuple(round(v, 6) for v in obj.matrix_world.translation))
    if key not in _BVH:
        V = world_coords(obj, evaluated=False)
        faces = [tuple(p.vertices) for p in obj.data.polygons]
        _BVH[key] = BVHTree.FromPolygons([tuple(v) for v in V], faces)
    return _BVH[key]


_DIRS = [Vector(d).normalized() for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1),
                                          (1, 1, 1), (1, 1, -1), (1, -1, 1), (1, -1, -1), (-1, 1, 1),
                                          (-1, 1, -1), (-1, -1, 1), (-1, -1, -1))]


def inside(obj, p, need=12):
    """True if point p (world) is enclosed by the mesh: rays in >= need of 14 directions
    hit it. Tolerates open holes (eye sockets, neck cuts) that break parity tests."""
    t = _bvh(obj)
    p = Vector(p)
    hits = sum(1 for d in _DIRS if t.ray_cast(p, d)[0] is not None)
    return hits >= need


def bend_angle(meta_or_rig, a, b):
    """Angle in degrees between two consecutive bones at rest."""
    bones = meta_or_rig.data.bones
    u = (bones[a].tail_local - bones[a].head_local).normalized()
    v = (bones[b].tail_local - bones[b].head_local).normalized()
    return math.degrees(u.angle(v))


def check_metarig(meta, body=None):
    """Problems that break Rigify generation or deformation. [] = ready to generate."""
    probs = []
    if any(abs(s - 1) > 1e-5 for s in meta.scale) or any(abs(r) > 1e-5 for r in meta.rotation_euler):
        probs.append("metarig scale/rotation not applied (generated rig differs in size or pose)")
    if any(pb.matrix_basis != Matrix.Identity(4) for pb in meta.pose.bones):
        probs.append("metarig pose != rest: Apply Pose as Rest Pose (pose.armature_apply)")
    for pb in meta.pose.bones:
        b, p = pb.bone, pb.bone.parent
        if p is None:
            continue
        need = b.use_connect or bool(pb.rigify_type and getattr(pb.rigify_parameters, "connect_chain", False))
        if need and (b.head_local - p.tail_local).length > 1e-4:
            probs.append(f"disjoint chain at {b.name} (Rigify: 'bone position is disjoint')")
    bones = meta.data.bones
    for s in ("L", "R"):
        if f"thigh.{s}" in bones:
            ang = bend_angle(meta, f"thigh.{s}", f"shin.{s}")
            th, sh = bones[f"thigh.{s}"], bones[f"shin.{s}"]
            t = (sh.head_local.z - th.head_local.z) / (sh.tail_local.z - th.head_local.z)
            liney = th.head_local.lerp(sh.tail_local, t).y
            if not (1.0 <= ang <= 15.0) or sh.head_local.y > liney:
                probs.append(f"knee.{s}: bend {ang:.1f} deg (want 1-15, forward): IK direction ambiguous")
        if f"upper_arm.{s}" in bones:
            ang = bend_angle(meta, f"upper_arm.{s}", f"forearm.{s}")
            if not (1.0 <= ang <= 15.0):
                probs.append(f"elbow.{s}: bend {ang:.1f} deg (want 1-15, backward)")
        for a, b in (("upper_arm", "forearm"), ("thigh", "shin")):
            if f"{a}.{s}" in bones:
                u, v = bones[f"{a}.{s}"], bones[f"{b}.{s}"]
                n = (u.tail_local - u.head_local).cross(v.tail_local - v.head_local)
                if n.length > 1e-9:
                    n.normalize()
                    for bb in (u, v):
                        x = bb.matrix_local.col[0].xyz
                        if abs(x.dot(n)) < 0.98:
                            probs.append(f"{bb.name}: hinge not on local X (roll), dot={x.dot(n):.2f}")
    if not any(getattr(c, "rigify_ui_row", 0) > 0 for c in meta.data.collections_all):
        probs.append("no bone collection has a Rigify UI row ('No bone collections have UI buttons')")
    upgraded = [pb for pb in meta.pose.bones if pb.rigify_type == "skin.glue"]
    ends = [(b.head_local, b.tail_local) for b in bones]
    for pb in upgraded:
        for e in (pb.bone.head_local, pb.bone.tail_local):
            if sum(1 for h, t in ends if (h - e).length < 1e-5 or (t - e).length < 1e-5) < 2:
                probs.append(f"glue {pb.name} end not coincident with another bone")
                break
    if body is not None:
        for nm in ("spine", "spine.002", "spine.004", "spine.006", "thigh.L", "shin.L", "foot.L",
                   "upper_arm.L", "forearm.L", "hand.L"):
            if nm in bones:
                b = bones[nm]
                mid = meta.matrix_world @ ((b.head_local + b.tail_local) / 2)
                if not inside(body, mid):
                    probs.append(f"{nm}: bone middle outside the mesh volume")
    return probs


def generate(meta):
    """Generate (or regenerate in place) the rig. Returns (rig, None) or (None, message).

    On failure Rigify leaves a partial rig active; this re-activates the metarig so
    the named bone can be fixed and generate() called again."""
    activate(meta)
    try:
        bpy.ops.pose.rigify_generate()
    except RuntimeError as e:
        lines = [ln for ln in str(e).splitlines() if ln.strip()]
        msg = next((ln for ln in lines if "RIGIFY ERROR" in ln), lines[0] if lines else str(e))
        activate(meta)
        return None, msg.strip()
    rig = meta.data.rigify_target_rig
    meta.hide_set(True)
    return rig, None


INTERNAL_PARTS = ("teeth", "tongue", "eye")


def deform_names(rig):
    return {b.name for b in rig.data.bones if b.use_deform}


def weight_matrix(obj, groups=None):
    """(n_verts, n_groups) weight array and the group names (all groups by default)."""
    names = [g.name for g in obj.vertex_groups] if groups is None else list(groups)
    col = {obj.vertex_groups[n].index: j for j, n in enumerate(names) if n in obj.vertex_groups}
    W = np.zeros((len(obj.data.vertices), len(names)), dtype=np.float64)
    for v in obj.data.vertices:
        for g in v.groups:
            j = col.get(g.group)
            if j is not None:
                W[v.index, j] = g.weight
    return W, names


def write_weights(obj, W, names, eps=1e-6):
    """Replace the weights of `names` with the columns of W (zeros are removed)."""
    allv = list(range(len(obj.data.vertices)))
    for j, n in enumerate(names):
        vg = obj.vertex_groups.get(n) or obj.vertex_groups.new(name=n)
        vg.remove(allv)
        nz = np.nonzero(W[:, j] > eps)[0]
        if len(nz) == 0:
            continue
        # vg.add takes one weight per call: batch equal weights
        vals = np.round(W[nz, j], 6)
        order = np.argsort(vals)
        vs, idx = vals[order], nz[order]
        cuts = np.nonzero(np.diff(vs))[0] + 1
        for a, b in zip(np.r_[0, cuts], np.r_[cuts, len(vs)]):
            vg.add(idx[a:b].tolist(), float(vs[a]), "REPLACE")


def weight_report(obj, rig, expect_inside=True):
    """Numbers that catch silent auto-weight failure and export problems.

    unweighted: vertices with total deform weight < 1e-4 (they will not move)
    empty_inside: deform groups with no weight although the bone lies inside the mesh
    non_deform_groups: groups for bones that do not deform (or no bone at all)
    max_influences / over4: influences per vertex (game engines usually take 4)
    sum_dev: vertices whose deform weights do not sum to 1 within 0.01
    mirror_err: worst L/R weight mismatch on mirrored vertex pairs (symmetric meshes)"""
    dn = deform_names(rig)
    W, names = weight_matrix(obj)
    is_def = np.array([n in dn for n in names], dtype=bool)
    Wd = W[:, is_def] if is_def.any() else np.zeros((len(W), 0))
    tot = Wd.sum(1) if Wd.size else np.zeros(len(W))
    rep = {"verts": len(W), "groups": len(names)}
    rep["unweighted"] = int((tot < 1e-4).sum())
    rep["sum_dev"] = int((np.abs(tot - 1) > 0.01).sum())
    cnt = (Wd > 1e-4).sum(1) if Wd.size else np.zeros(len(W), int)
    rep["max_influences"] = int(cnt.max()) if len(cnt) else 0
    rep["over4"] = int((cnt > 4).sum())
    rep["non_deform_groups"] = [n for n in names if n not in dn]
    empty = []
    if expect_inside and obj.type == "MESH":
        dnames = [n for n in names if n in dn]
        gsum = dict(zip(dnames, Wd.sum(0))) if Wd.size else {}
        for n in dnames:
            if gsum.get(n, 0) < 1e-3:
                b = rig.data.bones[n]
                mid = rig.matrix_world @ ((b.head_local + b.tail_local) / 2)
                if inside(obj, mid):
                    empty.append(n)
    rep["empty_inside"] = empty
    # heat failure signature: unweighted vertices, or a MAJOR bone inside the mesh with
    # nothing. Small face bones and internal parts (teeth, tongue, eyes) can be empty
    # on the skin legitimately.
    Hm = float(max(rig.dimensions.z, 1e-6))
    major = [n for n in empty if rig.data.bones[n].length >= 0.05 * Hm
             and not any(k in n for k in INTERNAL_PARTS)]
    rep["empty_major"] = major
    # mirror consistency (only meaningful for symmetric meshes)
    V = world_coords(obj, evaluated=False)
    kd = KDTree(len(V))
    for i, co in enumerate(V):
        kd.insert(co, i)
    kd.balance()
    col = {n: j for j, n in enumerate(names)}
    worst, pairs = 0.0, 0
    for i in np.nonzero(V[:, 0] > 1e-4)[0][::7]:
        co, j, dist = kd.find((-V[i, 0], V[i, 1], V[i, 2]))
        if dist > 1e-4:
            continue
        pairs += 1
        for n, c in col.items():
            s = side_of(n)
            if not s or W[i, c] < 1e-4:
                continue
            m = n.replace(".L", ".R") if s == "L" else n.replace(".R", ".L")
            if m in col:
                worst = max(worst, abs(W[i, c] - W[j, col[m]]))
    rep["mirror_pairs_sampled"] = pairs
    rep["mirror_err"] = round(float(worst), 4) if pairs else None
    rep["ok"] = rep["unweighted"] == 0 and not major
    return rep


def clean_weights(obj, rig, limit=0.01, max_influences=None, normalize=True):
    """Remove weights below `limit`, optionally keep the N largest, normalize deform
    weights to 1 (Demeter: clean near-zero weights before blurring; games: 4)."""
    dn = deform_names(rig)
    W, names = weight_matrix(obj)
    d = np.array([n in dn for n in names], dtype=bool)
    Wd = W[:, d]
    Wd[Wd < limit] = 0.0
    if max_influences:
        if Wd.shape[1] > max_influences:
            kth = np.sort(Wd, axis=1)[:, -max_influences][:, None]
            Wd[Wd < kth] = 0.0
            # ties can leave more than N: keep first N
            over = (Wd > 0).sum(1) > max_influences
            for i in np.nonzero(over)[0]:
                keep = np.argsort(-Wd[i])[:max_influences]
                row = np.zeros_like(Wd[i])
                row[keep] = Wd[i, keep]
                Wd[i] = row
    if normalize:
        s = Wd.sum(1, keepdims=True)
        Wd = np.where(s > 1e-9, Wd / np.maximum(s, 1e-12), Wd)
    W[:, d] = Wd
    write_weights(obj, W[:, d], [n for n, k in zip(names, d) if k])
    return weight_report(obj, rig, expect_inside=False)


def remove_non_deform_groups(obj, rig):
    """CGDive's lock-DEF-then-delete-unlocked cleanup, done directly."""
    dn = deform_names(rig)
    gone = [g.name for g in obj.vertex_groups if g.name not in dn]
    for n in gone:
        obj.vertex_groups.remove(obj.vertex_groups[n])
    return gone


def strip_opposite_side(obj, rig, margin=None):
    """Remove .L bone weights from vertices clearly on the right half (x < -margin) and
    .R weights from the left half, then renormalize. Fixes the classic auto-weight leak
    (left thigh moving the right inner thigh at the crotch; CGDive: 'the leg bone
    should not affect ... the other leg'). Symmetric, -Y facing characters only.
    margin defaults to 1% of the height. Returns the number of vertices changed."""
    V = world_coords(obj, evaluated=False)
    margin = 0.01 * float(np.ptp(V[:, 2])) if margin is None else margin
    dn = deform_names(rig)
    W, names = weight_matrix(obj)
    sides = np.array([side_of(n) if n in dn else "-" for n in names])
    changed = 0
    for side, mask in (("L", V[:, 0] < -margin), ("R", V[:, 0] > margin)):
        cols = np.nonzero(sides == side)[0]
        rows = np.nonzero(mask & (W[:, cols] > 0).any(1))[0] if len(cols) else []
        for i in rows:
            keep = W[i].copy()
            keep[cols] = 0.0
            d = np.array([n in dn for n in names])
            if keep[d].sum() > 1e-6:
                W[i] = keep
                changed += 1
    d = np.array([n in dn for n in names])
    Wd = W[:, d]
    ssum = Wd.sum(1, keepdims=True)
    W[:, d] = np.where(ssum > 1e-9, Wd / np.maximum(ssum, 1e-12), Wd)
    write_weights(obj, W[:, d], [n for n, k in zip(names, d) if k])
    return changed


def armature_first(obj):
    """Armature modifier right after any Mirror modifier and before everything else
    (Subdivision, Solidify, Corrective Smooth). parent_set appends it last (verified
    5.2.1): deforming after Subdivision multiplies the cost (Demeter, Dikko), and an
    Armature placed before a Mirror makes the mirrored half copy the posed half."""
    mods = list(obj.modifiers)
    arm = next((m for m in mods if m.type == "ARMATURE"), None)
    if arm is None:
        return [m.type for m in mods]
    others = [m for m in mods if m is not arm]
    target = 0
    for i, m in enumerate(others):
        if m.type == "MIRROR":
            target = i + 1
    if mods.index(arm) != target:
        with bpy.context.temp_override(object=obj, active_object=obj):
            bpy.ops.object.modifier_move_to_index(modifier=arm.name, index=target)
    return [m.type for m in obj.modifiers]


def _parent(rig, objs, kind):
    activate(rig, "OBJECT", select=objs)
    bpy.ops.object.parent_set(type=kind)


def transfer_weights(src, dst, rig=None, apply=True, mapping="POLYINTERP_NEAREST"):
    """Copy vertex-group weights from src to dst with a Data Transfer modifier, applied.

    Dikko / CGDive: garments and accessories get weights from the SAME source as the
    body so they deform identically, and the transfer is applied so weights do not
    change while animating. dst keeps (or gets) an Armature modifier to rig."""
    for g in src.vertex_groups:
        if g.name not in dst.vertex_groups:
            dst.vertex_groups.new(name=g.name)
    m = dst.modifiers.new("BX_weights", "DATA_TRANSFER")
    m.object = src
    m.use_vert_data = True
    m.data_types_verts = {"VGROUP_WEIGHTS"}
    m.vert_mapping = mapping
    m.layers_vgroup_select_src = "ALL"
    m.layers_vgroup_select_dst = "NAME"
    with bpy.context.temp_override(object=dst, active_object=dst):
        bpy.ops.object.modifier_move_to_index(modifier=m.name, index=0)
        if apply:
            bpy.ops.object.modifier_apply(modifier=m.name)
    if rig is not None and not any(md.type == "ARMATURE" for md in dst.modifiers):
        am = dst.modifiers.new("Armature", "ARMATURE")
        am.object = rig
    if rig is not None:
        mw = dst.matrix_world.copy()
        dst.parent = rig
        dst.matrix_world = mw
        armature_first(dst)
    return dst


def islands(obj):
    """Vertex index arrays of the loose parts of a mesh."""
    me = obj.data
    n = len(me.vertices)
    ed = np.empty(len(me.edges) * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", ed)
    parent = np.arange(n)

    def find(a):
        root = a
        while parent[root] != root:
            root = parent[root]
        while parent[a] != root:
            parent[a], a = root, parent[a]
        return root
    for a, b in ed.reshape(-1, 2):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    roots = np.array([find(i) for i in range(n)])
    return [np.nonzero(roots == r)[0] for r in np.unique(roots)]


def nearest_deform_bone(rig, p, candidates=None):
    best, bd = None, 1e9
    mw = rig.matrix_world
    for b in rig.data.bones:
        if not b.use_deform or (candidates and b.name not in candidates):
            continue
        a, c = mw @ b.head_local, mw @ b.tail_local
        ab = c - a
        t = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.length_squared, 1e-12)))
        d = (a + ab * t - p).length
        if d < bd:
            best, bd = b.name, d
    return best


def assign_rigid(obj, rig, bone=None, candidates=None):
    """Rigid parts (eyes, teeth, hard-surface pieces): every loose island gets weight
    1.0 on ONE deform bone (the given one, or the nearest to the island centre).
    CGDive/BajO: assign, do not paint; auto weights make eyes and teeth drift."""
    V = world_coords(obj, evaluated=False)
    for g in list(obj.vertex_groups):
        obj.vertex_groups.remove(g)
    out = {}
    for isl in islands(obj):
        name = bone or nearest_deform_bone(rig, _v(V[isl].mean(0)), candidates)
        vg = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
        vg.add(isl.tolist(), 1.0, "REPLACE")
        out.setdefault(name, 0)
        out[name] += len(isl)
    if not any(m.type == "ARMATURE" for m in obj.modifiers):
        m = obj.modifiers.new("Armature", "ARMATURE")
        m.object = rig
    mw = obj.matrix_world.copy()
    obj.parent = rig
    obj.matrix_world = mw
    armature_first(obj)
    return out


def proxy_weights(rig, targets, voxel=None, keep_proxy=False):
    """Bone-heat fallback: voxel-remesh a joined copy of the targets (one watertight
    shell), auto-weight the proxy, transfer to each target, apply. Deterministic fix
    for heat failures and multi-piece characters (Dikko's cage idea, verified)."""
    copies = []
    for o in targets:
        c = o.copy()
        c.data = o.data.copy()
        bpy.context.scene.collection.objects.link(c)
        for md in list(c.modifiers):
            c.modifiers.remove(md)
        c.parent = None
        c.matrix_world = o.matrix_world.copy()
        for g in list(c.vertex_groups):
            c.vertex_groups.remove(g)
        copies.append(c)
    V = np.vstack([world_coords(c, evaluated=False) for c in copies])
    H = float(np.ptp(V[:, 2]))
    proxy = copies[0]
    if len(copies) > 1:
        with bpy.context.temp_override(active_object=proxy, selected_editable_objects=copies,
                                       selected_objects=copies):
            bpy.ops.object.join()
    proxy.name = "BX_weight_proxy"
    bpy.context.view_layer.update()
    proxy.data.transform(proxy.matrix_world)
    proxy.matrix_world = Matrix.Identity(4)
    base_me = proxy.data.copy()
    ok = False
    for vs in ([voxel] if voxel else [H / 180.0, H / 90.0, H / 45.0]):
        # an open mesh (eye holes, neck cut) voxelizes into a thin shell with the bones
        # OUTSIDE it and heat fails again: a coarser voxel closes the holes
        proxy.data = base_me.copy()
        for g in list(proxy.vertex_groups):
            proxy.vertex_groups.remove(g)
        for md in [m for m in proxy.modifiers if m.type == "ARMATURE"]:
            proxy.modifiers.remove(md)
        proxy.data.remesh_voxel_size = vs
        with bpy.context.temp_override(object=proxy, active_object=proxy, selected_objects=[proxy]):
            bpy.ops.object.voxel_remesh()
        _parent(rig, [proxy], "ARMATURE_AUTO")
        W, _ = weight_matrix(proxy)
        if len(W) and (W.sum(1) > 1e-4).all():
            ok = True
            break
    proxy["bx_proxy_ok"] = ok
    proxy["bx_proxy_voxel"] = vs
    for o in targets:
        for g in list(o.vertex_groups):
            o.vertex_groups.remove(g)
        transfer_weights(proxy, o, rig=rig)
    if not keep_proxy:
        me = proxy.data
        bpy.data.objects.remove(proxy)
        bpy.data.meshes.remove(me)
        proxy = None
    return proxy


def bind(rig, body, garments=(), rigid=None, fallback="proxy", voxel=None, clean=0.01,
         max_influences=None, subdivision_last=True, symmetric=True, route="auto"):
    """Bind a character to a (generated) rig and PROVE the weights are usable.

    1 body: automatic weights (bone heat); parent_set returns FINISHED even when heat
      fails, so the result is measured (unweighted verts, empty deform groups inside).
    2 on failure: proxy route (voxel-remeshed copy, auto weights, Data Transfer back).
    3 garments: weights transferred from the body and applied (identical deformation).
    4 rigid: {obj: bone_name or None}; islands assigned 1.0 (None = nearest bone).
    5 clean (< clean), optional max_influences, normalize; Armature modifier first.
    symmetric=True strips .L weights from the right half and .R from the left before
    the garments copy the body (auto weights leak across the crotch and armpits).
    route='proxy' skips direct bone heat (multi-piece or non-manifold bodies).
    Returns a report dict with 'route' and per-object weight reports."""
    rep = {"route": "auto"}
    for o in [body, *garments]:
        for g in list(o.vertex_groups):
            o.vertex_groups.remove(g)
        for md in [m for m in o.modifiers if m.type == "ARMATURE"]:
            o.modifiers.remove(md)
    if route == "proxy":
        r = {"ok": False, "unweighted": None, "empty_inside": None}
    else:
        _parent(rig, [body], "ARMATURE_AUTO")
        r = weight_report(body, rig)
    rep["auto"] = {k: r[k] for k in ("unweighted", "empty_inside")}
    if not r["ok"] and (fallback == "proxy" or route == "proxy"):
        rep["route"] = "proxy"
        for md in [m for m in body.modifiers if m.type == "ARMATURE"]:
            body.modifiers.remove(md)
        px = proxy_weights(rig, [body], voxel=voxel, keep_proxy=True)
        rep["proxy_ok"], rep["proxy_voxel"] = bool(px["bx_proxy_ok"]), float(px["bx_proxy_voxel"])
        me_ = px.data
        bpy.data.objects.remove(px)
        bpy.data.meshes.remove(me_)
    if symmetric:
        rep["stripped_cross_side"] = strip_opposite_side(body, rig)
    for g in garments:
        transfer_weights(body, g, rig=rig)
    for o, bone in (rigid or {}).items():
        assign_rigid(o, rig, bone)
    for o in [body, *garments]:
        remove_non_deform_groups(o, rig)
        clean_weights(o, rig, limit=clean, max_influences=max_influences)
        armature_first(o)
        if subdivision_last:
            for m in o.modifiers:
                if m.type == "SUBSURF":
                    m.use_pin_to_last = True
    rep["body"] = weight_report(body, rig)
    rep["garments"] = {g.name: weight_report(g, rig, expect_inside=False) for g in garments}
    rep["ok"] = rep["body"]["ok"] and all(v["unweighted"] == 0 for v in rep["garments"].values())
    return rep


_PROP_DEFAULTS = {}


def reset_pose(rig):
    """Rest pose + custom properties back to their UI defaults (e.g. Rigify IK_FK)."""
    for pb in rig.pose.bones:
        pb.location = (0, 0, 0)
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.rotation_euler = (0, 0, 0)
        pb.rotation_axis_angle = (0, 0, 1, 0)
        pb.scale = (1, 1, 1)
        for k in list(pb.keys()):
            key = (rig.name, pb.name, k)
            if key in _PROP_DEFAULTS:
                pb[k] = _PROP_DEFAULTS[key]
    evaluate(rig)


def _setprop(rig, bone, key, value):
    pb = rig.pose.bones.get(bone)
    if pb is None or key not in pb.keys():
        return False
    _PROP_DEFAULTS.setdefault((rig.name, bone, key), pb[key])
    pb[key] = value
    return True


def _world_axis(rig, pb, i):
    evaluate(rig)
    return (rig.matrix_world.to_3x3() @ pb.matrix.col[i].xyz).normalized()


def rotate_world(rig, bone, q):
    """Rotate a pose bone by world-space quaternion q about its own head."""
    pb = rig.pose.bones[bone]
    evaluate(rig)
    R = rig.matrix_world.to_3x3().normalized()
    qa = (R.inverted() @ q.to_matrix() @ R).to_4x4()
    M = pb.matrix.copy()
    t = M.translation.copy()
    pb.matrix = Matrix.Translation(t) @ qa @ Matrix.Translation(-t) @ M
    evaluate(rig)


def aim(rig, bone, direction):
    """Swing a bone so its Y axis points along a world direction."""
    y = _world_axis(rig, rig.pose.bones[bone], 1)
    rotate_world(rig, bone, y.rotation_difference(Vector(direction).normalized()))


def hinge(rig, bone, deg, toward):
    """Rotate about the bone's OWN local X (the hinge) so its tail moves toward a
    world direction. Uses the rig's real roll: a wrong roll shows up as a bad pose."""
    pb = rig.pose.bones[bone]
    x, y = _world_axis(rig, pb, 0), _world_axis(rig, pb, 1)
    sign = 1.0 if x.cross(y).dot(Vector(toward)) > 0 else -1.0
    rotate_world(rig, bone, Quaternion(x, math.radians(deg) * sign))


def twist(rig, bone, deg):
    rotate_world(rig, bone, Quaternion(_world_axis(rig, rig.pose.bones[bone], 1), math.radians(deg)))


def apply_pose(rig, spec, H=None):
    """Apply one pose spec: {'name', 'ops': [...]}. Ops:
    ('prop', bone, key, value)   custom property (Rigify 'IK_FK': 1 = FK)
    ('fk',) / ('ik', 'legs')     Rigify limbs to FK / IK ('all', 'arms', 'legs')
    ('aim', bone, dir)           ('hinge', bone, deg, toward)   ('twist', bone, deg)
    ('rot', bone, world_axis, deg)  ('move', bone, (dx, dy, dz) in units of height)
    ('scale', bone, s)
    Missing bones are skipped and listed in the returned list."""
    reset_pose(rig)
    H = H or max(rig.dimensions.z, 1e-3)
    skipped = []
    for op in spec["ops"]:
        kind = op[0]
        if kind in ("fk", "ik"):
            limbs = op[1] if len(op) > 1 else "all"
            names = {"arms": ("upper_arm_parent.L", "upper_arm_parent.R"),
                     "legs": ("thigh_parent.L", "thigh_parent.R")}
            targets = names.get(limbs, names["arms"] + names["legs"])
            for b in targets:
                _setprop(rig, b, "IK_FK", 1.0 if kind == "fk" else 0.0)
            evaluate(rig)
            continue
        bone = op[1]
        if bone not in rig.pose.bones:
            skipped.append(bone)
            continue
        if kind == "prop":
            _setprop(rig, bone, op[2], op[3])
            evaluate(rig)
        elif kind == "aim":
            aim(rig, bone, op[2])
        elif kind == "hinge":
            hinge(rig, bone, op[2], op[3])
        elif kind == "twist":
            twist(rig, bone, op[2])
        elif kind == "rot":
            rotate_world(rig, bone, Quaternion(Vector(op[2]).normalized(), math.radians(op[3])))
        elif kind == "move":
            pb = rig.pose.bones[bone]
            evaluate(rig)
            d = rig.matrix_world.to_3x3().inverted() @ (Vector(op[2]) * H)
            M = pb.matrix.copy()
            pb.matrix = Matrix.Translation(d) @ M
            evaluate(rig)
        elif kind == "scale":
            rig.pose.bones[bone].scale = (op[2],) * 3
            evaluate(rig)
    return skipped
