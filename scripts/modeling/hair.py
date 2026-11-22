"""Blender hair tools adapted from Scenario Blender Skills."""
# Source: https://github.com/scenario-labs/skills/blob/754d1ceea270f1bf59651671b3471f385a5a80f9/skills/dcc/blender/scenario-blender-hair/scripts/bx_hair.py
# Copyright 2026 Scenario Inc. Distributed under the accompanying Scenario-MIT license.

import math
import bpy
import numpy as np


HAIR_LIB = "nodes/procedural_hair_node_assets.blend"


DYN_LIB = "nodes/geometry_nodes_dynamics_assets.blend"


DYNAMICS_ASSETS = {"Hair Dynamics", "Cloth Dynamics (Experimental)", "Collider", "Custom Force"}


def _s(sockets, key):
    """Socket by identifier, else by name among enabled sockets (Mix, Capture Attribute
    and other dynamic nodes reuse names like 'A' or 'Result' across data types)."""
    for sk in sockets:
        if sk.identifier == key:
            return sk
    for sk in sockets:
        if sk.name == key and sk.enabled:
            return sk
    raise KeyError(key)


def _ctx_obj(obj):
    return bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj],
                                     selected_editable_objects=[obj])


def _object_mode():
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def new_groom(surface, name="Hair"):
    """Add an empty hair curves object on `surface` (Add > Curve > Empty Hair).
    Sets parent, data.surface, data.surface_uv_map (active UV map) and a pinned
    Hair Dynamics modifier in Animation mode (moves hair with the deforming surface)."""
    if surface.type != "MESH" or not surface.data.uv_layers:
        raise ValueError("surface must be a mesh with a UV map (hair binds to UV space)")
    _object_mode()
    for o in bpy.context.selected_objects:
        o.select_set(False)
    surface.select_set(True)
    bpy.context.view_layer.objects.active = surface
    bpy.ops.object.curves_empty_hair_add()
    hair = bpy.context.active_object
    hair.name = hair.data.name = name
    bpy.context.view_layer.update()
    return hair


def _mesh_arrays(surface):
    me = surface.data
    me.calc_loop_triangles()
    tris = np.empty(len(me.loop_triangles) * 3, np.int32)
    me.loop_triangles.foreach_get("vertices", tris)
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    vn = np.empty(len(me.vertices) * 3, np.float32)
    me.vertex_normals.foreach_get("vector", vn)
    return tris.reshape(-1, 3), co.reshape(-1, 3), vn.reshape(-1, 3)


def group_weights(obj, group):
    """Per-vertex weights of a vertex group as a numpy array (0 where unassigned)."""
    vg = obj.vertex_groups[group]
    w = np.zeros(len(obj.data.vertices), np.float32)
    for v in obj.data.vertices:
        for g in v.groups:
            if g.group == vg.index:
                w[v.index] = g.weight
    return w


def sample_roots(surface, spacing, mask=None, threshold=0.5, seed=0, max_roots=200_000):
    """Evenly spaced guide roots on the surface (Poisson-disk dart throwing).
    Kerstin: even spacing matters more than count; gaps become bald spots.
    Returns (P, N) world-space positions and smooth normals, numpy (n, 3)."""
    tris, co, vn = _mesh_arrays(surface)
    M = np.array(surface.matrix_world, dtype=np.float64)
    R = M[:3, :3]
    cw = co @ R.T + M[:3, 3]
    nrm_m = np.linalg.inv(R).T
    nw = vn @ nrm_m.T
    a, b, c = cw[tris[:, 0]], cw[tris[:, 1]], cw[tris[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    total = float(area.sum())
    n_cand = int(min(max_roots * 6, max(64, 6 * total / (spacing * spacing))))
    rng = np.random.default_rng(seed)
    ti = rng.choice(len(tris), n_cand, p=area / total)
    s = np.sqrt(rng.random(n_cand))
    r2 = rng.random(n_cand)
    u, v, w = 1 - s, s * (1 - r2), s * r2
    t = tris[ti]
    P = a[ti] * u[:, None] + b[ti] * v[:, None] + c[ti] * w[:, None]
    N = nw[t[:, 0]] * u[:, None] + nw[t[:, 1]] * v[:, None] + nw[t[:, 2]] * w[:, None]
    N /= np.linalg.norm(N, axis=1)[:, None] + 1e-12
    if mask:
        wv = group_weights(surface, mask)
        wp = wv[t[:, 0]] * u + wv[t[:, 1]] * v + wv[t[:, 2]] * w
        keep = wp >= threshold
        P, N = P[keep], N[keep]
    # dart throwing on a hash grid, cell = spacing
    grid, keep = {}, []
    inv = 1.0 / spacing
    s2 = spacing * spacing
    for i, p in enumerate(P):
        k = (int(math.floor(p[0] * inv)), int(math.floor(p[1] * inv)), int(math.floor(p[2] * inv)))
        ok = True
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in grid.get((k[0] + dx, k[1] + dy, k[2] + dz), ()):
                        d = P[j] - p
                        if d @ d < s2:
                            ok = False
                            break
                    if not ok:
                        break
                if not ok:
                    break
            if not ok:
                break
        if ok:
            grid.setdefault(k, []).append(i)
            keep.append(i)
            if len(keep) >= max_roots:
                break
    keep = np.array(keep, dtype=np.int64)
    return P[keep], N[keep]


def add_guides(hair, roots, normals, length, points=2, flow=None, flow_blend=0.0,
               droop=0.0, snap=True):
    """Write guide curves (the tablet-free replacement for the Add brush).

    roots, normals: (n, 3) world space (from sample_roots). length: float or (n,).
    points: Kerstin grooms with 2 points until every direction is right, 3 for short
    fur, up to 8 for long hair; Bystedt resamples long human hair to 15.
    flow: None, a world vector, or callable(P, N) -> (n, 3) flow directions; it is
    projected on the surface tangent plane and blended with the normal by flow_blend
    (0 = straight out, 1 = lying on the surface). droop: tip sag along -Z as a fraction
    of length (needs points >= 3 to bend). snap: snap_curves_to_surface(NEAREST), which
    writes surface_uv_coordinate (the attachment). Returns the number of curves added."""
    _object_mode()
    P = np.asarray(roots, np.float64)
    N = np.asarray(normals, np.float64)
    n = len(P)
    if n == 0:
        return 0
    L = np.broadcast_to(np.asarray(length, np.float64), (n,))
    d = N.copy()
    if flow is not None and flow_blend > 0:
        F = flow(P, N) if callable(flow) else np.broadcast_to(np.asarray(flow, np.float64), (n, 3))
        T = F - (F * N).sum(1)[:, None] * N
        T /= np.linalg.norm(T, axis=1)[:, None] + 1e-12
        d = (1 - flow_blend) * N + flow_blend * T
        d /= np.linalg.norm(d, axis=1)[:, None] + 1e-12
    t = np.linspace(0.0, 1.0, points)
    pts = P[:, None, :] + d[:, None, :] * (L[:, None, None] * t[None, :, None])
    if droop:
        pts[:, :, 2] -= droop * L[:, None] * t[None, :] ** 2
    inv = np.array(hair.matrix_world.inverted(), dtype=np.float64)
    loc = pts.reshape(-1, 3) @ inv[:3, :3].T + inv[:3, 3]
    cv = hair.data
    old = len(cv.points)
    cv.add_curves([points] * n)
    buf = np.empty(len(cv.points) * 3, np.float32)
    cv.attributes["position"].data.foreach_get("vector", buf)
    buf[old * 3:] = loc.ravel()
    cv.attributes["position"].data.foreach_set("vector", buf)
    cv.update_tag()
    if snap:
        snap_to_surface(hair, "NEAREST")
    return n


def snap_to_surface(hair, mode="NEAREST"):
    """NEAREST: move each curve so its root sits on the closest surface point and store
    the attachment UV. DEFORM: re-place curves from their stored UV after the surface
    was edited (Simon: Snap to Deformed Surface)."""
    _object_mode()
    with _ctx_obj(hair):
        return bpy.ops.curves.snap_curves_to_surface(attach_mode=mode)


def add_asset(hair, name, label=None, index=None):
    """Add an Essentials hair (or dynamics) node-group asset as a modifier. New modifiers
    land above the pinned Hair Dynamics. Returns the modifier."""
    _object_mode()
    lib = DYN_LIB if name in DYNAMICS_ASSETS else HAIR_LIB
    with _ctx_obj(hair):
        bpy.ops.object.modifier_add_node_group(
            asset_library_type="ESSENTIALS", asset_library_identifier="",
            relative_asset_identifier=f"{lib}/NodeTree/{name}")
    md = hair.modifiers.active
    if label:
        md.name = label
    if index is not None:
        move(hair, md, index)
    return md


def move(hair, md, index):
    with _ctx_obj(hair):
        bpy.ops.object.modifier_move_to_index(modifier=md.name, index=index)


def _input_items(md):
    out = {}
    for it in md.node_group.interface.items_tree:
        if it.item_type == "SOCKET" and it.in_out == "INPUT" and it.socket_type != "NodeSocketGeometry":
            out.setdefault(it.name, []).append(it)
    return out


def _resolve(md, key, value):
    ins = md.properties.inputs
    if key.startswith(("Input_", "Socket_")) and hasattr(ins, key):
        return key
    items = _input_items(md).get(key)
    if not items:
        raise KeyError(f"{md.name}: no input '{key}'. Inputs: {sorted(_input_items(md))}")
    if len(items) > 1:  # e.g. Displace/Shrinkwrap 'Surface' exists as Object and Geometry
        want = "NodeSocketObject" if isinstance(value, bpy.types.Object) else None
        for it in items:
            if (want and it.socket_type == want) or (not want and it.socket_type != "NodeSocketObject"):
                return it.identifier
    return items[0].identifier


def set_inputs(md, values):
    """Set modifier inputs by their UI name (or Input_/Socket_ identifier).
    "attr:NAME" binds the input to an attribute (a surface vertex group reaches the
    interpolated children as an attribute). Tags the object: in background the
    evaluated result stays stale without update_tag()."""
    ins = md.properties.inputs
    for key, val in values.items():
        p = getattr(ins, _resolve(md, key, val))
        if isinstance(val, str) and val.startswith("attr:"):
            p.type = "ATTRIBUTE"
            p.attribute_name = val[5:]
        else:
            if hasattr(p, "type") and p.type == "ATTRIBUTE":
                p.type = "VALUE"
            p.value = val
    md.id_data.update_tag()
    return md


def build_stack(hair, spec, masks=True):
    """Add a list of (asset name, {inputs}) in order. With masks=True, the random curve
    masks referenced as attr:stray_mask / attr:frizz_mask are created first."""
    mods = []
    used = {v[5:] for _, vals in spec for v in vals.values() if isinstance(v, str) and v.startswith("attr:")}
    for name, vals in spec:
        md = add_asset(hair, name)
        set_inputs(md, vals)
        mods.append(md)
        if name == "Interpolate Hair Curves" and masks:
            if "stray_mask" in used:
                mods.append(random_curve_mask(hair, "stray_mask", threshold=0.9, hard=True, seed=3))
            if "frizz_mask" in used:
                mods.append(random_curve_mask(hair, "frizz_mask", threshold=0.8, seed=7))
    return mods


def _attr_group(kind):
    name = {"random": "BX Random Curve Mask", "along": "BX Along Curve Ramp"}[kind]
    ng = bpy.data.node_groups.get(name)
    if ng:
        return ng
    ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    I = ng.interface
    I.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    I.new_socket("Name", in_out="INPUT", socket_type="NodeSocketString")
    gi, go = ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput")
    st = ng.nodes.new("GeometryNodeStoreNamedAttribute")
    st.data_type = "FLOAT"
    if kind == "random":
        I.new_socket("Threshold", in_out="INPUT", socket_type="NodeSocketFloat").default_value = 0.8
        I.new_socket("Seed", in_out="INPUT", socket_type="NodeSocketInt")
        I.new_socket("Hard", in_out="INPUT", socket_type="NodeSocketBool")
        st.domain = "CURVE"
        rnd = ng.nodes.new("FunctionNodeRandomValue")
        rnd.data_type = "FLOAT"
        idn = ng.nodes.new("GeometryNodeInputID")
        ng.links.new(idn.outputs["ID"], rnd.inputs["ID"])
        ng.links.new(gi.outputs["Seed"], rnd.inputs["Seed"])
        mr = ng.nodes.new("ShaderNodeMapRange")          # soft: 0 below threshold, ramps to 1
        ng.links.new(rnd.outputs["Value"], mr.inputs["Value"])
        ng.links.new(gi.outputs["Threshold"], mr.inputs["From Min"])
        cmp = ng.nodes.new("FunctionNodeCompare")        # hard: 1 above threshold
        cmp.data_type, cmp.operation = "FLOAT", "GREATER_THAN"
        ng.links.new(rnd.outputs["Value"], cmp.inputs[0])
        ng.links.new(gi.outputs["Threshold"], cmp.inputs[1])
        sw = ng.nodes.new("GeometryNodeSwitch")
        sw.input_type = "FLOAT"
        ng.links.new(gi.outputs["Hard"], sw.inputs["Switch"])
        ng.links.new(mr.outputs["Result"], sw.inputs["False"])
        ng.links.new(cmp.outputs[0], sw.inputs["True"])
        val = sw.outputs["Output"]
    else:
        I.new_socket("Root", in_out="INPUT", socket_type="NodeSocketFloat").default_value = 0.0
        I.new_socket("Tip", in_out="INPUT", socket_type="NodeSocketFloat").default_value = 1.0
        st.domain = "POINT"
        sp = ng.nodes.new("GeometryNodeSplineParameter")
        mr = ng.nodes.new("ShaderNodeMapRange")
        ng.links.new(sp.outputs["Factor"], mr.inputs["Value"])
        ng.links.new(gi.outputs["Root"], mr.inputs["To Min"])
        ng.links.new(gi.outputs["Tip"], mr.inputs["To Max"])
        val = mr.outputs["Result"]
    I.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    ng.links.new(gi.outputs["Geometry"], st.inputs["Geometry"])
    ng.links.new(gi.outputs["Name"], st.inputs["Name"])
    ng.links.new(val, st.inputs["Value"])
    ng.links.new(st.outputs["Geometry"], go.inputs["Geometry"])
    return ng


def random_curve_mask(hair, attr, threshold=0.8, seed=0, hard=False, after="Interpolate Hair Curves"):
    """Store a per-CURVE random float attribute to drive an asset input with "attr:<attr>".
    Simon: randomize per curve, not per point. Soft: 0 for `threshold` of the curves,
    ramping to 1 (frizz on ~20% of strands). Hard: 1 on (1 - threshold) of the curves
    (stray hairs: threshold 0.9). Placed right after `after` (the children exist there)."""
    md = hair.modifiers.new(f"BX {attr}", "NODES")
    md.node_group = _attr_group("random")
    set_inputs(md, {"Name": attr, "Threshold": float(threshold), "Seed": int(seed), "Hard": bool(hard)})
    _place_after(hair, md, after)
    return md


def _place_after(hair, md, after):
    names = [m.name for m in hair.modifiers]
    idx = next((i for i, n in enumerate(names) if after and n.startswith(after)), None)
    if idx is not None:
        move(hair, md, idx + 1)
    elif "Hair Dynamics" in names:
        move(hair, md, max(0, names.index("Hair Dynamics")))


def hair_material(name="Hair", engine="CYCLES", root_color=(0.05, 0.025, 0.012), tip_color=(0.35, 0.17, 0.07),
                  melanin=None, roughness=(0.25, 0.4), random_color=0.1, uv_noise=0.0):
    """Strand material with a root-to-tip gradient (Curves Info Intercept) and per-strand
    roughness variation (Curves Info Random). engine CYCLES: Principled Hair BSDF (Chiang);
    melanin=(root, tip) switches to melanin parametrization. engine EEVEE: Principled BSDF
    with explicit colors (Simon: Principled Hair BSDF is a Cycles shader).
    uv_noise > 0 adds Simon's surface-UV noise (Attribute surface_uv_coordinate)."""
    mat = bpy.data.materials.new(name)
    nt = mat.node_tree
    for n in list(nt.nodes):
        if n.type != "OUTPUT_MATERIAL":
            nt.nodes.remove(n)
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    info = nt.nodes.new("ShaderNodeHairInfo")
    rough = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(info.outputs["Random"], rough.inputs["Value"])
    rough.inputs["To Min"].default_value, rough.inputs["To Max"].default_value = roughness
    col = nt.nodes.new("ShaderNodeMix")
    col.data_type = "RGBA"
    _s(col.inputs, "A_Color").default_value = (*root_color, 1.0)
    _s(col.inputs, "B_Color").default_value = (*tip_color, 1.0)
    nt.links.new(info.outputs["Intercept"], _s(col.inputs, "Factor_Float"))
    color_out = _s(col.outputs, "Result_Color")
    if uv_noise > 0:
        attr = nt.nodes.new("ShaderNodeAttribute")
        attr.attribute_name = "surface_uv_coordinate"
        noise = nt.nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = 8.0
        nt.links.new(attr.outputs["Vector"], noise.inputs["Vector"])
        soft = nt.nodes.new("ShaderNodeMix")
        soft.data_type, soft.blend_type = "RGBA", "SOFT_LIGHT"
        _s(soft.inputs, "Factor_Float").default_value = uv_noise
        nt.links.new(color_out, _s(soft.inputs, "A_Color"))
        nt.links.new(noise.outputs["Color"], _s(soft.inputs, "B_Color"))
        color_out = _s(soft.outputs, "Result_Color")
    if engine == "CYCLES":
        b = nt.nodes.new("ShaderNodeBsdfHairPrincipled")
        b.model = "CHIANG"
        if melanin is not None:
            b.parametrization = "MELANIN"
            mm = nt.nodes.new("ShaderNodeMapRange")
            nt.links.new(info.outputs["Intercept"], mm.inputs["Value"])
            mm.inputs["To Min"].default_value, mm.inputs["To Max"].default_value = melanin
            nt.links.new(mm.outputs["Result"], b.inputs["Melanin"])
            b.inputs["Random Color"].default_value = random_color
        else:
            b.parametrization = "COLOR"
            nt.links.new(color_out, b.inputs["Color"])
        nt.links.new(rough.outputs["Result"], b.inputs["Roughness"])
    else:
        b = nt.nodes.new("ShaderNodeBsdfPrincipled")
        nt.links.new(color_out, b.inputs["Base Color"])
        nt.links.new(rough.outputs["Result"], b.inputs["Roughness"])
    nt.links.new(b.outputs[0], out.inputs["Surface"])
    return mat


def from_paths(name, paths, radii=None, material=None, parent=None, collection=None):
    """A hair curves object from an (S, P, 3) array of world-space strand points and optional (S, P) radii."""
    points = np.asarray(paths, dtype=np.float32)
    if points.ndim != 3 or points.shape[2] != 3 or points.shape[1] < 2:
        raise ValueError("paths must be a (strands, points, 3) array with at least two points per strand.")
    strands, per_strand = points.shape[:2]
    data = bpy.data.hair_curves.new(name)
    data.add_curves([per_strand] * strands)
    data.attributes["position"].data.foreach_set("vector", points.ravel())
    if radii is not None:
        radius = data.attributes.get("radius") or data.attributes.new("radius", "FLOAT", "POINT")
        radius.data.foreach_set("value", np.asarray(radii, dtype=np.float32).ravel())
    obj = bpy.data.objects.new(name, data)
    (collection or bpy.context.collection).objects.link(obj)
    if material is not None:
        data.materials.append(material)
    if parent is not None:
        obj.parent = parent
        obj.matrix_parent_inverse = parent.matrix_world.inverted()
    return obj


def assign_material(obj, mat):
    obj.data.materials.clear()
    obj.data.materials.append(mat)
