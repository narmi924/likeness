"""Weather on materials: snow that settles on whatever faces up, a film of rain and puddles on level ground, streaks
and grime down walls, mountain rock with a snow line, and trees made bare, snow-laden or retinted.

    from modeling import weathering as W
    W.cover(ground_material, snow=0.8)                 # snow on the up-facing parts of this material
    W.cover(path_material, rain=1.0)                   # wet, with puddles on the level parts
    W.cover(roof_material, snow=1.0, above=2600)       # only above 2600 m (scene z)
    W.set_weather(snow=0.3)                            # change every covered material at once, e.g. for a lighter fall
    W.streaks(wall_material, 0.5)                      # rain streaks, stains, a grubby band near the ground
    rock = W.mountain(zmax=1800, snowline=0.36)        # distant mountains: gullies, rock, forest band, snow above the line
    bare = W.bare(tree)                                # the same tree in winter: trunk and branches only
    W.snowy(fir, 0.8)                                  # needles that carry snow
    W.leaves(tree, hue=0.5, sat=1.15, val=2.6)         # leaf colour and light through the leaves, different per copy

Amounts are 0 to 1. Everything works in world metres, so it follows the scene's scale."""
import bpy
import bmesh

from .nodes import N, put, math_, mixc, mixf, smooth, noise_, new_mat, principled, has_alpha

SNOW_WHITE = (0.80, 0.83, 0.88, 1)


def _cover_group():
    g = bpy.data.node_groups.get("Likeness Cover")
    if g:
        return g
    g = bpy.data.node_groups.new("Likeness Cover", "ShaderNodeTree")
    g.interface.new_socket("Shader", in_out="INPUT", socket_type="NodeSocketShader")
    for name, default in (("Snow", 0.0), ("Rain", 0.0), ("Snow above", -1e6)):
        s = g.interface.new_socket(name, in_out="INPUT", socket_type="NodeSocketFloat")
        s.default_value = default
    g.interface.new_socket("Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
    gi, go = N(g, "NodeGroupInput"), N(g, "NodeGroupOutput")
    geo = N(g, "ShaderNodeNewGeometry")
    P = geo.outputs["Position"]
    nz = N(g, "ShaderNodeSeparateXYZ")
    put(nz.inputs[0], geo.outputs["Normal"])
    nz = nz.outputs["Z"]
    sp = N(g, "ShaderNodeSeparateXYZ")
    put(sp.inputs[0], P)
    # snow: on what faces up, ragged at its edge, and only above a height if one is given
    height_ok = smooth(g, math_(g, "SUBTRACT", sp.outputs["Z"], gi.outputs["Snow above"]), -6.0, 6.0)
    snow = math_(g, "MULTIPLY", gi.outputs["Snow"], height_ok)
    cap = math_(g, "MULTIPLY", math_(g, "MULTIPLY", snow, smooth(g, nz, 0.42, 0.78)), smooth(g, noise_(g, P, 0.4, 3), 0.18, 0.42), clamp=True)
    white = N(g, "ShaderNodeBsdfPrincipled")
    white.inputs["Base Color"].default_value = SNOW_WHITE
    white.inputs["Roughness"].default_value = 0.55
    white.inputs["Specular IOR Level"].default_value = 0.25
    bump = N(g, "ShaderNodeBump")
    put(bump.inputs["Height"], noise_(g, P, 45.0, 3))
    bump.inputs["Strength"].default_value = 0.15
    bump.inputs["Distance"].default_value = 0.02
    put(white.inputs["Normal"], bump.outputs[0])
    m1 = N(g, "ShaderNodeMixShader")
    put(m1.inputs[0], cap)
    put(m1.inputs[1], gi.outputs["Shader"])
    put(m1.inputs[2], white.outputs[0])
    # rain: a thin film of water on what faces up, puddles where the ground is level
    flat = N(g, "ShaderNodeCombineXYZ")
    put(flat.inputs[0], sp.outputs["X"])
    put(flat.inputs[1], sp.outputs["Y"])
    puddle = math_(g, "MULTIPLY", smooth(g, noise_(g, flat.outputs[0], 0.3, 2), 0.55, 0.64), smooth(g, nz, 0.985, 0.999))
    film = math_(g, "MULTIPLY", math_(g, "MULTIPLY", gi.outputs["Rain"], smooth(g, nz, 0.5, 0.85)),
                 math_(g, "ADD", 0.07, math_(g, "MULTIPLY", puddle, 0.85)), clamp=True)
    gloss = N(g, "ShaderNodeBsdfGlossy")
    gloss.inputs["Roughness"].default_value = 0.03
    gloss.inputs["Color"].default_value = (0.9, 0.92, 0.95, 1)
    m2 = N(g, "ShaderNodeMixShader")
    put(m2.inputs[0], film)
    put(m2.inputs[1], m1.outputs[0])
    put(m2.inputs[2], gloss.outputs[0])
    put(go.inputs[0], m2.outputs[0])
    return g


def cover(mat, snow=0.0, rain=0.0, above=None):
    """Snow and rain over a material. Calling it again on the same material changes the amounts."""
    nt = mat.node_tree
    node = nt.nodes.get("Likeness cover")
    if node is None:
        out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None)
        if out is None or not out.inputs["Surface"].links:
            return None
        src = out.inputs["Surface"].links[0].from_socket
        node = N(nt, "ShaderNodeGroup", node_tree=_cover_group(), name="Likeness cover")
        nt.links.new(src, node.inputs["Shader"])
        nt.links.new(node.outputs["Shader"], out.inputs["Surface"])
    node.inputs["Snow"].default_value = snow
    node.inputs["Rain"].default_value = rain
    node.inputs["Snow above"].default_value = -1e6 if above is None else above
    return node


def set_weather(snow=None, rain=None, above=None):
    """Change the snow and rain on every covered material in the file."""
    for mat in bpy.data.materials:
        node = mat.node_tree.nodes.get("Likeness cover") if mat.use_nodes else None
        if node:
            if snow is not None:
                node.inputs["Snow"].default_value = snow
            if rain is not None:
                node.inputs["Rain"].default_value = rain
            if above is not None:
                node.inputs["Snow above"].default_value = above


def streak_mask(nt, amount):
    """Rain streaks down the walls, broad stains, and a grubby band near the ground (on vertical faces only)."""
    geo = N(nt, "ShaderNodeNewGeometry")
    P = geo.outputs["Position"]
    mp = N(nt, "ShaderNodeMapping")
    put(mp.inputs["Vector"], P)
    mp.inputs["Scale"].default_value = (2.2, 2.2, 0.07)
    streak = smooth(nt, noise_(nt, mp.outputs[0], 1.0, 5, 0.6), 0.5, 0.78)
    stain = smooth(nt, noise_(nt, P, 0.12, 4), 0.42, 0.72)
    oi = N(nt, "ShaderNodeTexCoord")
    z = N(nt, "ShaderNodeSeparateXYZ")
    put(z.inputs[0], oi.outputs["Object"])                     # the band sits at the foot of each object
    splash = smooth(nt, z.outputs["Z"], 2.4, 1.1)
    sn = N(nt, "ShaderNodeSeparateXYZ")
    put(sn.inputs[0], geo.outputs["Normal"])
    wall = math_(nt, "LESS_THAN", math_(nt, "ABSOLUTE", sn.outputs["Z"]), 0.5)
    m_ = math_(nt, "ADD", math_(nt, "ADD", math_(nt, "MULTIPLY", streak, 0.75), math_(nt, "MULTIPLY", stain, 0.45)), math_(nt, "MULTIPLY", splash, 0.5))
    return math_(nt, "MULTIPLY", math_(nt, "MULTIPLY", m_, wall), amount, clamp=True)


def streaks(mat, amount=0.4, tint=(1.0, 1.0, 1.0)):
    """Lay weathering over a material that came with a model (once per material)."""
    p = principled(mat)
    if not p or mat.get("likeness_streaks"):
        return
    nt = mat.node_tree
    sock = p.inputs["Base Color"]
    src = sock.links[0].from_socket if sock.links else tuple(sock.default_value)
    worn = mixc(nt, streak_mask(nt, amount), src, (0.30, 0.27, 0.23, 1), "MULTIPLY")
    put(sock, mixc(nt, 1.0, worn, (*tint, 1), "MULTIPLY"))
    mat["likeness_streaks"] = True


def mountain(zmax, snowline=0.36, forest=True, name="Mountain"):
    """Far mountains from an elevation mesh: gullies running down the slopes, dark rock, a forest band low down and
    snow above `snowline` (a share of zmax, the highest z in the scene) that follows the gullies. A node called
    SNOWLINE holds the height, to move the line without rebuilding."""
    m, nt, out = new_mat(name)
    geo = N(nt, "ShaderNodeNewGeometry")
    P = geo.outputs["Position"]
    sp = N(nt, "ShaderNodeSeparateXYZ")
    put(sp.inputs[0], P)
    gm = N(nt, "ShaderNodeMapping")
    put(gm.inputs["Vector"], P)
    gm.inputs["Scale"].default_value = (1.0, 1.0, 0.16)
    gully = noise_(nt, gm.outputs[0], 0.03, 8, 0.7)
    b1 = N(nt, "ShaderNodeBump")
    put(b1.inputs["Height"], noise_(nt, P, 0.006, 9, 0.72))
    b1.inputs["Strength"].default_value = 0.8
    b1.inputs["Distance"].default_value = 30.0
    b2 = N(nt, "ShaderNodeBump")
    put(b2.inputs["Height"], gully)
    b2.inputs["Strength"].default_value = 0.9
    b2.inputs["Distance"].default_value = 8.0
    put(b2.inputs["Normal"], b1.outputs[0])
    b3 = N(nt, "ShaderNodeBump")
    put(b3.inputs["Height"], noise_(nt, P, 0.15, 6, 0.7))
    b3.inputs["Strength"].default_value = 0.45
    b3.inputs["Distance"].default_value = 1.2
    put(b3.inputs["Normal"], b2.outputs[0])
    st = N(nt, "ShaderNodeSeparateXYZ")
    put(st.inputs[0], geo.outputs["True Normal"])
    sb = N(nt, "ShaderNodeSeparateXYZ")
    put(sb.inputs[0], b2.outputs[0])
    z = sp.outputs["Z"]
    nz = math_(nt, "ADD", math_(nt, "MULTIPLY", st.outputs["Z"], 0.55), math_(nt, "MULTIPLY", sb.outputs["Z"], 0.45))
    wob = math_(nt, "MULTIPLY", math_(nt, "SUBTRACT", noise_(nt, P, 0.004, 5, 0.6), 0.5), zmax * 0.22)
    zs = N(nt, "ShaderNodeValue", name="SNOWLINE")
    zs.outputs[0].default_value = zmax * snowline
    zz = math_(nt, "SUBTRACT", math_(nt, "ADD", z, wob), zs.outputs[0])
    snow = math_(nt, "MULTIPLY", smooth(nt, zz, -zmax * 0.04, zmax * 0.06), smooth(nt, nz, 0.26, 0.58))
    snow = math_(nt, "MAXIMUM", snow, math_(nt, "MULTIPLY", smooth(nt, zz, zmax * 0.16, zmax * 0.34), smooth(nt, nz, 0.10, 0.36)))
    snow = math_(nt, "MULTIPLY", snow, smooth(nt, gully, 0.30, 0.52, 0.35, 1.0))
    rock = mixc(nt, gully, (0.04, 0.036, 0.034, 1), (0.15, 0.12, 0.10, 1))
    rock = mixc(nt, math_(nt, "MULTIPLY", noise_(nt, P, 0.01, 4), 0.55), rock, (0.19, 0.14, 0.10, 1))
    low = mixc(nt, noise_(nt, P, 0.008, 4), (0.17, 0.14, 0.075, 1), (0.26, 0.21, 0.11, 1))
    col = mixc(nt, smooth(nt, z, zmax * 0.07, zmax * 0.18), low, rock)
    if forest:
        band = math_(nt, "MULTIPLY", smooth(nt, math_(nt, "ADD", z, wob), zmax * 0.09, zmax * 0.16), smooth(nt, zz, -zmax * 0.08, -zmax * 0.18))
        band = math_(nt, "MULTIPLY", band, smooth(nt, noise_(nt, P, 0.012, 4), 0.38, 0.55))
        col = mixc(nt, band, col, (0.016, 0.03, 0.018, 1))
    col = mixc(nt, snow, col, SNOW_WHITE)
    bsdf = N(nt, "ShaderNodeBsdfPrincipled")
    put(bsdf.inputs["Base Color"], col)
    put(bsdf.inputs["Roughness"], mixf(nt, snow, 0.92, 0.6))
    put(bsdf.inputs["Normal"], b3.outputs[0])
    put(out.inputs["Surface"], bsdf.outputs[0])
    return m


# ---------------------------------------------------------------- trees
def _meshes(source):
    """The mesh objects behind a tree: a mesh object, a collection, or a copy made by outdoor.plant (an instance)."""
    if isinstance(source, bpy.types.Collection):
        return [o for o in source.all_objects if o.type == "MESH"]
    if getattr(source, "instance_collection", None):
        return [o for o in source.instance_collection.all_objects if o.type == "MESH"]
    return [source] if source.type == "MESH" else []


def _variant(source, tag, change):
    """A changed copy: a mesh for a mesh object, otherwise a new collection to give the copies as instance_collection."""
    if getattr(source, "type", None) == "MESH" and not getattr(source, "instance_collection", None):
        return change(source.data.copy())
    base = source if isinstance(source, bpy.types.Collection) else source.instance_collection
    coll = bpy.data.collections.new(f"{base.name} {tag}")
    keep = bpy.data.collections.get("Sources")
    (keep.children if keep else bpy.context.scene.collection.children).link(coll)
    copies = {}
    for o in base.all_objects:
        c = o.copy()
        if o.type == "MESH":
            c.data = change(o.data.copy())
        copies[o] = c
        coll.objects.link(c)
    for o, c in copies.items():
        if o.parent in copies:
            c.parent = copies[o.parent]
    return coll


def bare(tree):
    """The same tree after the leaves have fallen (the faces whose material has transparency are gone).
    tree: a mesh object, or a copy from outdoor.plant. Returns a mesh to give as obj.data, or for plant's copies a
    collection to give as copy.instance_collection:

        winter = W.bare(trees[0])
        for t in trees[::2]: t.instance_collection = winter"""
    def change(me):
        leafy = [i for i, mt in enumerate(me.materials) if has_alpha(mt)]
        bm = bmesh.new()
        bm.from_mesh(me)
        bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index in leafy], context="FACES")
        bm.to_mesh(me)
        bm.free()
        me.name = me.name.split(".")[0] + " bare"
        return me
    return _variant(tree, "bare", change)


def snowy(tree, amount=0.8):
    """The same tree with snow on its branches and needles, patchy, paler on top. Returns like bare()."""
    def change(me):
        for i, mt in enumerate(me.materials):
            c = mt.copy()
            c.name = mt.name + " snowy"
            me.materials[i] = c
            p = principled(c)
            if not p:
                continue
            nt = c.node_tree
            sock = p.inputs["Base Color"]
            src = sock.links[0].from_socket if sock.links else tuple(sock.default_value)
            geo = N(nt, "ShaderNodeNewGeometry")
            nz = N(nt, "ShaderNodeSeparateXYZ")
            put(nz.inputs[0], geo.outputs["Normal"])
            f = math_(nt, "MULTIPLY", smooth(nt, noise_(nt, geo.outputs["Position"], 5.0, 3), 0.28, 0.6), smooth(nt, nz.outputs["Z"], -0.2, 0.6))
            put(sock, mixc(nt, math_(nt, "MULTIPLY", f, amount), src, (0.78, 0.81, 0.86, 1)))
        return me
    return _variant(tree, "snowy", change)


def leaves(obj_or_mat, hue=0.5, sat=1.0, val=1.0, translucency=0.35):
    """Retint the leaves of a tree (materials with transparency) and let light through them. Each copy of the tree
    gets a slightly different tone, so a grove does not look stamped out. hue 0.5 keeps the colour."""
    if isinstance(obj_or_mat, bpy.types.Material):
        mats = [obj_or_mat]
    else:
        mats = [m for o in _meshes(obj_or_mat) for m in o.data.materials if m]
    for mat in mats:
        p = principled(mat)
        if not p or not p.inputs["Base Color"].links or not has_alpha(mat) or mat.get("likeness_leaves"):
            continue
        nt = mat.node_tree
        src = p.inputs["Base Color"].links[0].from_socket
        hsv = N(nt, "ShaderNodeHueSaturation")
        rnd = N(nt, "ShaderNodeObjectInfo").outputs["Random"]
        put(hsv.inputs["Hue"], math_(nt, "ADD", hue - 0.022, math_(nt, "MULTIPLY", rnd, 0.044)))
        hsv.inputs["Saturation"].default_value = sat
        put(hsv.inputs["Value"], math_(nt, "MULTIPLY", val, math_(nt, "ADD", 0.82, math_(nt, "MULTIPLY", rnd, 0.36))))
        nt.links.new(src, hsv.inputs["Color"])
        nt.links.new(hsv.outputs[0], p.inputs["Base Color"])
        out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
        surf = out.inputs["Surface"].links[0].from_socket
        tl = N(nt, "ShaderNodeBsdfTranslucent")
        nt.links.new(hsv.outputs[0], tl.inputs["Color"])
        alpha = p.inputs["Alpha"].links[0].from_socket
        mix = N(nt, "ShaderNodeMixShader")
        mix.inputs[0].default_value = translucency
        nt.links.new(surf, mix.inputs[1])
        tr = N(nt, "ShaderNodeBsdfTransparent")
        cut = N(nt, "ShaderNodeMixShader")
        nt.links.new(alpha, cut.inputs[0])
        nt.links.new(tr.outputs[0], cut.inputs[1])
        nt.links.new(tl.outputs[0], cut.inputs[2])
        nt.links.new(cut.outputs[0], mix.inputs[2])
        nt.links.new(mix.outputs[0], out.inputs["Surface"])
        mat["likeness_leaves"] = True
