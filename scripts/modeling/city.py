"""Buildings for real places: OpenStreetMap footprints raised to their heights with believable facades, a far city that
fills a skyline, and the facade materials themselves.

    from modeling import city, outdoor
    place = outdoor.place("<place dir>")
    homes = city.facade("Housing", "housing", floor_h=3.0, bay=3.6)                 # punched windows, curtains, some lit
    glass = city.facade("Offices", "curtain", floor_h=3.8, bay=1.8)                 # glass floor to floor, lit floors
    blocks = city.buildings(place.outlines("building"), place.height, [homes, glass], far_from=(0, 0), near=60)
    city.skyline((900, 400), 250, 900, 300, place.height, [homes, glass])           # a town on the horizon
    city.lights(0.6)                                                                # every lit window at once

The facades are laid out in each object's own metres (x along the longest wall), so the grid follows the building
however it is turned. Close buildings from photographs are still modelled by hand: these are for the middle distance
and beyond, where outline, height, window rhythm and a few lit rooms are what the eye reads."""
import math
import random

import bpy
from mathutils import Matrix, Vector

from .nodes import N, put, math_, mixc, mixf, smooth, noise_, new_mat
from .weathering import streak_mask

LIGHTS = "CITY_LIGHTS"


# ---------------------------------------------------------------- geometry
class Parts:
    """Faces gathered into one mesh, each with a material slot."""
    FACES = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))
    CORNERS = [Vector((x, y, z)) for x in (-0.5, 0.5) for y in (-0.5, 0.5) for z in (-0.5, 0.5)]

    def __init__(self):
        self.v, self.f, self.m = [], [], []

    def _add(self, p, M):
        self.v.append(M @ p if M else p)

    def box(self, centre, size, mat=0, M=None):
        base = len(self.v)
        for c in self.CORNERS:
            self._add(Vector((c.x * size[0], c.y * size[1], c.z * size[2])) + Vector(centre), M)
        for f in self.FACES:
            self.f.append([base + k for k in f])
            self.m.append(mat)

    def beam(self, a, b, w, mat=0, M=None):
        a, b = Vector(a), Vector(b)
        R = (b - a).to_track_quat("Z", "Y").to_matrix().to_4x4()
        T = Matrix.Translation((a + b) / 2) @ R
        self.box((0, 0, 0), (w, w, (b - a).length), mat, (M @ T) if M else T)

    def wall(self, outline, z0, z1, mat=0, M=None, closed=True):
        """Vertical faces along an outline of (x, y) points given counter-clockwise; they face outwards."""
        base, n = len(self.v), len(outline)
        for x, y in outline:
            for z in (z0, z1):
                self._add(Vector((x, y, z)), M)
        for i in range(n if closed else n - 1):
            j = (i + 1) % n
            self.f.append([base + 2 * i, base + 2 * j, base + 2 * j + 1, base + 2 * i + 1])
            self.m.append(mat)

    def slab(self, outline, z0, z1, mat=0, M=None):
        """A solid plate with the given outline: its edge, top and underside."""
        self.wall(outline, z0, z1, mat, M)
        for z, flip in ((z1, False), (z0, True)):
            base = len(self.v)
            for x, y in outline:
                self._add(Vector((x, y, z)), M)
            idx = list(range(base, base + len(outline)))
            self.f.append(idx[::-1] if flip else idx)
            self.m.append(mat)

    def frustum(self, lower, upper, z0, z1, mat=0, M=None, cap=True):
        """Sloping faces between two outlines with the same number of points, and a top."""
        base, n = len(self.v), len(lower)
        for (x0, y0), (x1, y1) in zip(lower, upper):
            self._add(Vector((x0, y0, z0)), M)
            self._add(Vector((x1, y1, z1)), M)
        for i in range(n):
            j = (i + 1) % n
            self.f.append([base + 2 * i, base + 2 * j, base + 2 * j + 1, base + 2 * i + 1])
            self.m.append(mat)
        if cap:
            self.f.append([base + 2 * i + 1 for i in range(n)])
            self.m.append(mat)

    def build(self, name, mats, collection=None, smooth_faces=False):
        me = bpy.data.meshes.new(name)
        me.from_pydata(self.v, [], self.f)
        for mt in mats:
            me.materials.append(mt)
        me.polygons.foreach_set("material_index", self.m)
        if smooth_faces:
            me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
        me.validate()
        me.update()
        ob = bpy.data.objects.new(name, me)
        (collection or bpy.context.scene.collection).objects.link(ob)
        return ob


def rect(w, d, cx=0.0, cy=0.0):
    return [(cx - w / 2, cy - d / 2), (cx + w / 2, cy - d / 2), (cx + w / 2, cy + d / 2), (cx - w / 2, cy + d / 2)]


def rounded(w, d, r, steps=8, cx=0.0, cy=0.0):
    """A rectangle with rounded corners, counter-clockwise."""
    pts = []
    for qx, qy, a0 in ((w / 2 - r, d / 2 - r, 0), (-w / 2 + r, d / 2 - r, 90), (-w / 2 + r, -d / 2 + r, 180), (w / 2 - r, -d / 2 + r, 270)):
        for k in range(steps + 1):
            a = math.radians(a0 + 90 * k / steps)
            pts.append((cx + qx + r * math.cos(a), cy + qy + r * math.sin(a)))
    return pts


def circle(r, n=16, cx=0.0, cy=0.0):
    return [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def grow(outline, by):
    """The outline moved outwards (inwards when negative) along its vertex normals."""
    n, out = len(outline), []
    for i in range(n):
        x0, y0 = outline[i - 1]
        x1, y1 = outline[i]
        x2, y2 = outline[(i + 1) % n]
        ex, ey = x2 - x0, y2 - y0
        length = math.hypot(ex, ey) or 1.0
        out.append((x1 + ey / length * by, y1 - ex / length * by))
    return out


def curve(points, spacing=1.0):
    """A traced outline (from a plan or a photograph) resampled to even steps, for curved walls of glass or stone."""
    out = [points[0]]
    carry = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        t = spacing - carry
        while t <= seg:
            out.append((x0 + (x1 - x0) * t / seg, y0 + (y1 - y0) * t / seg))
            t += spacing
        carry = seg - (t - spacing)
    return out


def area(outline):
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(outline, outline[1:] + outline[:1]))


# ---------------------------------------------------------------- materials
def plain(name, colour, rough=0.8, metallic=0.0, grain=0.0, streaks=0.0, emit=0.0, emit_name=None):
    """One colour, with optional grain, weather streaks and glow (emit_name names a Value node to dim it later)."""
    m, nt, out = new_mat(name)
    bsdf = N(nt, "ShaderNodeBsdfPrincipled")
    P = N(nt, "ShaderNodeNewGeometry").outputs["Position"]
    c = (*colour, 1)
    if grain:
        c = mixc(nt, noise_(nt, P, 3.0, 5), (*[v * (1 - grain) for v in colour], 1), (*[min(1, v * (1 + grain)) for v in colour], 1))
        b = N(nt, "ShaderNodeBump")
        put(b.inputs["Height"], noise_(nt, P, 40.0, 4))
        b.inputs["Strength"].default_value = 0.12
        b.inputs["Distance"].default_value = 0.01
        put(bsdf.inputs["Normal"], b.outputs[0])
    if streaks:
        c = mixc(nt, streak_mask(nt, streaks), c, (0.30, 0.27, 0.23, 1), "MULTIPLY")
    put(bsdf.inputs["Base Color"], c)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metallic
    if emit:
        bsdf.inputs["Emission Color"].default_value = (*colour, 1)
        e = N(nt, "ShaderNodeValue", name=emit_name or "GLOW")
        e.outputs[0].default_value = emit
        put(bsdf.inputs["Emission Strength"], e.outputs[0])
    put(out.inputs["Surface"], bsdf.outputs[0])
    return m


def facade(name, kind="housing", floor_h=3.0, bay=3.6, wall=None, tones=None, glass=(0.03, 0.04, 0.055), lit_share=0.2,
           base_h=0.0, z0=0.3, z1=0.84, m0=0.2, m1=0.8, pier=0.42, frame=(0.5, 0.5, 0.48), busy=0.28, snow=0.0,
           light_name=LIGHTS):
    """A facade material laid out in the object's own metres.

    kind "housing": windows set in the wall, each with a frame, a shaded head and a sill (which carries snow when
        snow > 0); curtains half drawn in some, lamps on in some, a television here and there. Objects made by
        buildings() carry half_x and half_y, which keep windows off the corners. wall: one colour, or None to take a
        tone per building from `tones`. z0..z1 and m0..m1: the opening's share of a floor and of a bay.
    kind "curtain": glass from floor to floor with a spandrel band and thin mullions; every pane leans a hair its own
        way so the sky breaks up in it; some whole floors are lit, the rest sparsely.
    kind "stone": the same glass between stone piers; pier is the share of each bay that is stone, wall its colour.
    A Value node named light_name scales every lit window: lights() changes them all."""
    m, nt, out = new_mat(name)
    P = N(nt, "ShaderNodeTexCoord").outputs["Object"]
    geo = N(nt, "ShaderNodeNewGeometry")
    sn = N(nt, "ShaderNodeVectorTransform", vector_type="NORMAL", convert_from="WORLD", convert_to="OBJECT")
    put(sn.inputs[0], geo.outputs["Normal"])
    nrm = N(nt, "ShaderNodeSeparateXYZ")
    put(nrm.inputs[0], sn.outputs[0])
    sp = N(nt, "ShaderNodeSeparateXYZ")
    put(sp.inputs[0], P)
    facing_x = math_(nt, "GREATER_THAN", math_(nt, "ABSOLUTE", nrm.outputs["X"]), 0.5)
    along = mixf(nt, facing_x, sp.outputs["X"], sp.outputs["Y"])
    side = mixf(nt, facing_x, math_(nt, "SIGN", nrm.outputs["Y"]), math_(nt, "MULTIPLY", math_(nt, "SIGN", nrm.outputs["X"]), 3.0))
    u = math_(nt, "ADD", math_(nt, "DIVIDE", along, bay), 0.5)
    v = math_(nt, "DIVIDE", math_(nt, "SUBTRACT", sp.outputs["Z"], base_h), floor_h)
    fu, fv = math_(nt, "FRACT", u), math_(nt, "FRACT", v)
    cell = N(nt, "ShaderNodeCombineXYZ")
    put(cell.inputs[0], math_(nt, "FLOOR", u))
    put(cell.inputs[1], math_(nt, "FLOOR", v))
    put(cell.inputs[2], side)
    wn = N(nt, "ShaderNodeTexWhiteNoise", noise_dimensions="3D")
    put(wn.inputs["Vector"], cell.outputs[0])
    oi = N(nt, "ShaderNodeObjectInfo")
    salt = N(nt, "ShaderNodeVectorMath", operation="ADD")             # every building its own lit rooms
    put(salt.inputs[0], wn.outputs["Color"])
    put(salt.inputs[1], oi.outputs["Location"])
    wn2 = N(nt, "ShaderNodeTexWhiteNoise", noise_dimensions="3D")
    put(wn2.inputs["Vector"], salt.outputs[0])
    rnd = N(nt, "ShaderNodeSeparateColor")
    put(rnd.inputs[0], wn2.outputs["Color"])
    r0, r1, r2 = rnd.outputs[0], rnd.outputs[1], rnd.outputs[2]
    row = N(nt, "ShaderNodeCombineXYZ")
    put(row.inputs[1], math_(nt, "FLOOR", v))
    put(row.inputs[2], side)
    wrow = N(nt, "ShaderNodeTexWhiteNoise", noise_dimensions="3D")
    put(wrow.inputs["Vector"], row.outputs[0])
    vertical = math_(nt, "LESS_THAN", math_(nt, "ABSOLUTE", nrm.outputs["Z"]), 0.5)
    upright = math_(nt, "MULTIPLY", vertical, math_(nt, "GREATER_THAN", sp.outputs["Z"], base_h))
    lights = nt.nodes.new("ShaderNodeValue")
    lights.name = lights.label = light_name
    lights.outputs[0].default_value = 1.0
    bsdf = N(nt, "ShaderNodeBsdfPrincipled")

    def between(x, a, b):
        return math_(nt, "MULTIPLY", math_(nt, "GREATER_THAN", x, a), math_(nt, "LESS_THAN", x, b))

    if kind == "housing":
        if wall is None:
            ramp = N(nt, "ShaderNodeValToRGB")
            ramp.color_ramp.interpolation = "CONSTANT"
            tones = tones or [(0.40, 0.37, 0.33), (0.31, 0.24, 0.20), (0.43, 0.42, 0.40), (0.36, 0.33, 0.28), (0.27, 0.25, 0.24), (0.45, 0.40, 0.33)]
            el = ramp.color_ramp.elements
            el[0].position, el[0].color = 0.0, (*tones[0], 1)
            el[1].position, el[1].color = 1.0 / len(tones), (*tones[1], 1)
            for i in range(2, len(tones)):
                el.new(i / len(tones)).color = (*tones[i], 1)
            put(ramp.inputs[0], oi.outputs["Random"])
            wallc = ramp.outputs[0]
        else:
            wallc = (*wall, 1)
        wallc = mixc(nt, math_(nt, "MULTIPLY", math_(nt, "LESS_THAN", fv, 0.035), 0.3), wallc, (0.1, 0.1, 0.1, 1), "MULTIPLY")
        wallc = mixc(nt, math_(nt, "MULTIPLY", math_(nt, "SUBTRACT", wrow.outputs["Value"], 0.5), 0.16), wallc, (1, 1, 1, 1), "ADD")
        wallc = mixc(nt, streak_mask(nt, 0.4), wallc, (0.35, 0.33, 0.31, 1), "MULTIPLY")
        hx = N(nt, "ShaderNodeAttribute", attribute_type="OBJECT", attribute_name="half_x").outputs["Fac"]
        hy = N(nt, "ShaderNodeAttribute", attribute_type="OBJECT", attribute_name="half_y").outputs["Fac"]
        centre = math_(nt, "ABSOLUTE", math_(nt, "MULTIPLY", math_(nt, "FLOOR", u), bay))
        room = math_(nt, "LESS_THAN", math_(nt, "ADD", centre, bay * 0.5), mixf(nt, facing_x, hx, hy))
        wu = math_(nt, "DIVIDE", math_(nt, "SUBTRACT", fu, m0), m1 - m0)
        wv = math_(nt, "DIVIDE", math_(nt, "SUBTRACT", fv, z0), z1 - z0)
        win = math_(nt, "MULTIPLY", math_(nt, "MULTIPLY", between(wu, 0.0, 1.0), between(wv, 0.0, 1.0)), math_(nt, "MULTIPLY", upright, room))
        edge = math_(nt, "MAXIMUM", math_(nt, "MAXIMUM", math_(nt, "LESS_THAN", wu, 0.07), math_(nt, "GREATER_THAN", wu, 0.93)),
                     math_(nt, "MAXIMUM", math_(nt, "MAXIMUM", math_(nt, "LESS_THAN", wv, 0.06), math_(nt, "GREATER_THAN", wv, 0.94)),
                           math_(nt, "LESS_THAN", math_(nt, "ABSOLUTE", math_(nt, "SUBTRACT", wu, 0.5)), 0.028)))
        pane = math_(nt, "MULTIPLY", win, math_(nt, "SUBTRACT", 1.0, edge))
        head = math_(nt, "MULTIPLY", pane, smooth(nt, wv, 0.74, 0.92))
        sill = math_(nt, "MULTIPLY", math_(nt, "MULTIPLY", between(wv, -0.11, 0.0), between(wu, -0.06, 1.06)), math_(nt, "MULTIPLY", upright, room))
        drawn = math_(nt, "MULTIPLY", math_(nt, "GREATER_THAN", r1, 0.5), math_(nt, "LESS_THAN", wu, math_(nt, "ADD", 0.15, math_(nt, "MULTIPLY", r2, 0.8))))
        cur = N(nt, "ShaderNodeValToRGB")
        cur.color_ramp.interpolation = "CONSTANT"
        cel = cur.color_ramp.elements
        ctones = [(0.55, 0.50, 0.42), (0.62, 0.60, 0.56), (0.45, 0.30, 0.26), (0.36, 0.42, 0.40), (0.6, 0.52, 0.36)]
        cel[0].position, cel[0].color = 0.0, (*ctones[0], 1)
        cel[1].position, cel[1].color = 0.2, (*ctones[1], 1)
        for i in range(2, 5):
            cel.new(i / 5).color = (*ctones[i], 1)
        put(cur.inputs[0], math_(nt, "FRACT", math_(nt, "MULTIPLY", r0, 7.31)))
        glassc = mixc(nt, math_(nt, "MULTIPLY", drawn, 0.85), (*glass, 1), cur.outputs[0])
        glassc = mixc(nt, math_(nt, "MULTIPLY", head, 0.6), glassc, (0, 0, 0, 1))
        col = mixc(nt, win, wallc, mixc(nt, pane, (*frame, 1), glassc))
        col = mixc(nt, sill, col, mixc(nt, snow * 0.92, (0.42, 0.42, 0.40, 1), (0.80, 0.83, 0.88, 1)))
        put(bsdf.inputs["Base Color"], col)
        clear = math_(nt, "MULTIPLY", pane, math_(nt, "SUBTRACT", 1.0, drawn))
        put(bsdf.inputs["Roughness"], mixf(nt, clear, 0.82, 0.07))
        put(bsdf.inputs["Specular IOR Level"], mixf(nt, clear, 0.4, 1.0))
        lit = math_(nt, "MULTIPLY", pane, math_(nt, "GREATER_THAN", r0, 1 - lit_share))
        lit = math_(nt, "MULTIPLY", lit, math_(nt, "ADD", 0.3, math_(nt, "MULTIPLY", r2, 0.7)))
        lit = math_(nt, "MULTIPLY", lit, mixf(nt, drawn, math_(nt, "ADD", 0.55, math_(nt, "MULTIPLY", wv, 0.7)), 0.55))
        lamp = mixc(nt, r1, (1.0, 0.50, 0.17, 1), (1.0, 0.80, 0.52, 1))
        lamp = mixc(nt, math_(nt, "GREATER_THAN", math_(nt, "FRACT", math_(nt, "MULTIPLY", r2, 13.7)), 0.93), lamp, (0.50, 0.68, 1.0, 1))
        lamp = mixc(nt, math_(nt, "MULTIPLY", drawn, 0.6), lamp, mixc(nt, 1.0, lamp, cur.outputs[0], "MULTIPLY"))
        put(bsdf.inputs["Emission Color"], lamp)
        put(bsdf.inputs["Emission Strength"], math_(nt, "MULTIPLY", lit, lights.outputs[0]))
        put(out.inputs["Surface"], bsdf.outputs[0])
        m["likeness_facade"] = kind
        return m

    band = 0.24
    stone = math_(nt, "MULTIPLY", math_(nt, "LESS_THAN", fu, pier), upright) if kind == "stone" else 0.0
    line = math_(nt, "MAXIMUM", math_(nt, "LESS_THAN", fv, 0.04), math_(nt, "LESS_THAN", math_(nt, "ABSOLUTE", math_(nt, "SUBTRACT", fv, band)), 0.014))
    mull = math_(nt, "LESS_THAN", fu, 0.05 * 1.5 / bay)
    span = math_(nt, "LESS_THAN", fv, band)
    vision = math_(nt, "MULTIPLY", math_(nt, "MULTIPLY", math_(nt, "SUBTRACT", 1.0, span), math_(nt, "SUBTRACT", 1.0, mull)), upright)
    if kind == "stone":
        vision = math_(nt, "MULTIPLY", vision, math_(nt, "SUBTRACT", 1.0, stone))
    col = mixc(nt, span, (*glass, 1), (glass[0] * 1.9 + 0.01, glass[1] * 1.9 + 0.012, glass[2] * 1.9 + 0.015, 1))
    col = mixc(nt, math_(nt, "MULTIPLY", r1, 0.28), col, (0, 0, 0, 1))
    col = mixc(nt, math_(nt, "MULTIPLY", line, upright), col, (0.30, 0.32, 0.35, 1))
    col = mixc(nt, math_(nt, "MULTIPLY", mull, upright), col, (0.06, 0.065, 0.07, 1))
    rough = mixf(nt, vision, 0.3, 0.03)
    if kind == "stone":
        w = wall or (0.40, 0.36, 0.30)
        stonec = mixc(nt, streak_mask(nt, 0.35), (*w, 1), (0.35, 0.33, 0.31, 1), "MULTIPLY")
        stonec = mixc(nt, math_(nt, "MULTIPLY", math_(nt, "LESS_THAN", fv, 0.03), 0.35), stonec, (0.1, 0.1, 0.1, 1), "MULTIPLY")
        col = mixc(nt, stone, col, stonec)
        rough = mixf(nt, stone, rough, 0.75)
    jit = N(nt, "ShaderNodeVectorMath", operation="SUBTRACT")
    put(jit.inputs[0], wn.outputs["Color"])
    jit.inputs[1].default_value = (0.5, 0.5, 0.5)
    sc = N(nt, "ShaderNodeVectorMath", operation="SCALE")
    put(sc.inputs[0], jit.outputs[0])
    put(sc.inputs["Scale"], math_(nt, "MULTIPLY", vision, 0.035))
    add = N(nt, "ShaderNodeVectorMath", operation="ADD")
    put(add.inputs[0], geo.outputs["Normal"])
    put(add.inputs[1], sc.outputs[0])
    nn = N(nt, "ShaderNodeVectorMath", operation="NORMALIZE")
    put(nn.inputs[0], add.outputs[0])
    put(bsdf.inputs["Base Color"], col)
    put(bsdf.inputs["Roughness"], rough)
    put(bsdf.inputs["Normal"], nn.outputs[0])
    put(bsdf.inputs["Specular IOR Level"], mixf(nt, vision, 0.5, 1.0))
    late = math_(nt, "GREATER_THAN", wrow.outputs["Value"], 1 - busy)
    share = mixf(nt, late, lit_share, 0.62)
    lit = math_(nt, "MULTIPLY", vision, math_(nt, "GREATER_THAN", r0, math_(nt, "SUBTRACT", 1.0, share)))
    lit = math_(nt, "MULTIPLY", lit, math_(nt, "ADD", 0.35, math_(nt, "MULTIPLY", r2, 0.65)))
    lit = math_(nt, "MULTIPLY", lit, smooth(nt, fv, band, band + 0.5, 0.55, 1.0))
    put(bsdf.inputs["Emission Color"], mixc(nt, r1, (1.0, 0.72, 0.42, 1), (1.0, 0.88, 0.70, 1)))
    put(bsdf.inputs["Emission Strength"], math_(nt, "MULTIPLY", lit, lights.outputs[0]))
    lw = N(nt, "ShaderNodeLayerWeight")
    lw.inputs["Blend"].default_value = 0.3
    put(lw.inputs["Normal"], nn.outputs[0])
    gl = N(nt, "ShaderNodeBsdfGlossy")
    gl.inputs["Roughness"].default_value = 0.035
    gl.inputs["Color"].default_value = (0.78, 0.86, 0.96, 1)
    put(gl.inputs["Normal"], nn.outputs[0])
    mx = N(nt, "ShaderNodeMixShader")
    put(mx.inputs[0], math_(nt, "MULTIPLY", vision, math_(nt, "ADD", 0.16, math_(nt, "MULTIPLY", lw.outputs["Fresnel"], 0.6)), clamp=True))
    put(mx.inputs[1], bsdf.outputs[0])
    put(mx.inputs[2], gl.outputs[0])
    put(out.inputs["Surface"], mx.outputs[0])
    m["likeness_facade"] = kind
    return m


def lit_glass(mat, share=0.22, strength=0.7, light_name="ROOM_LIGHTS"):
    """Turn the glass of a model that came from elsewhere into dark reflective windows with some rooms lit."""
    nt = mat.node_tree
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None)
    if not out:
        return
    geo = N(nt, "ShaderNodeNewGeometry")
    mp = N(nt, "ShaderNodeMapping")
    put(mp.inputs["Vector"], geo.outputs["Position"])
    mp.inputs["Scale"].default_value = (1 / 3.6, 1 / 3.6, 1 / 3.6)
    cell = N(nt, "ShaderNodeVectorMath", operation="FLOOR")
    put(cell.inputs[0], mp.outputs[0])
    wn = N(nt, "ShaderNodeTexWhiteNoise", noise_dimensions="3D")
    put(wn.inputs["Vector"], cell.outputs[0])
    lit = math_(nt, "GREATER_THAN", wn.outputs["Value"], 1 - share)
    part = N(nt, "ShaderNodeSeparateColor")
    put(part.inputs[0], wn.outputs["Color"])
    lit = math_(nt, "MULTIPLY", lit, math_(nt, "ADD", 0.25, math_(nt, "MULTIPLY", part.outputs[1], 0.75)))
    bsdf = N(nt, "ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (0.03, 0.04, 0.05, 1)
    bsdf.inputs["Roughness"].default_value = 0.04
    bsdf.inputs["Specular IOR Level"].default_value = 1.0
    put(bsdf.inputs["Emission Color"], mixc(nt, part.outputs[2], (1.0, 0.40, 0.10, 1), (1.0, 0.62, 0.28, 1)))
    lights = nt.nodes.new("ShaderNodeValue")
    lights.name = lights.label = light_name
    lights.outputs[0].default_value = strength
    put(bsdf.inputs["Emission Strength"], math_(nt, "MULTIPLY", lit, lights.outputs[0]))
    for link in list(out.inputs["Surface"].links):
        nt.links.remove(link)
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])


def lights(strength=1.0, name=LIGHTS):
    """Set every lit window (every Value node with this name) at once: 0 for daytime, about 1 at dusk."""
    for mat in bpy.data.materials:
        if mat.use_nodes and mat.node_tree.nodes.get(name):
            mat.node_tree.nodes[name].outputs[0].default_value = strength


# ---------------------------------------------------------------- footprints and skylines
def _number(text):
    try:
        return float(str(text).replace(",", ".").split()[0].rstrip("m"))
    except (ValueError, IndexError):
        return None


def height_of(tags, floor_h=3.2, default=None):
    """A building's height in metres from OpenStreetMap tags: height, else building:levels (plus roof:levels), else
    `default`."""
    h = _number(tags.get("height", ""))
    if h:
        return h
    levels = _number(tags.get("building:levels", ""))
    if levels:
        return (levels + 0.5 * (_number(tags.get("roof:levels", "")) or 0)) * floor_h + 1.0
    return default


def buildings(features, ground, materials, floor_h=3.2, default_levels=(2, 5), base=None, footing=4.0,
              keep=None, collection=None, seed=1, name="Building"):
    """Raise footprints into buildings standing on the ground.

    features: OpenStreetMap features with closed outlines (place.outlines("building")) or plain lists of (x, y).
    ground: a height function (place.height). materials: facades to choose from; the tallest buildings take the
    later ones, so pass housing first and glass last. default_levels: the range used when the tags say nothing.
    keep(x, y): optional, False drops a building (e.g. near the path, where real models go instead).
    Each object's own x runs along its longest wall, so facades line up with it; a footing reaches below the
    lowest corner so nothing hangs over falling ground. Returns the objects."""
    rnd = random.Random(seed)
    coll = collection or bpy.data.collections.get("Buildings") or bpy.data.collections.new("Buildings")
    if coll.name not in bpy.context.scene.collection.children and collection is None:
        bpy.context.scene.collection.children.link(coll)
    made = []
    for k, f in enumerate(features):
        pts = f["points"] if isinstance(f, dict) else f
        tags = f.get("tags", {}) if isinstance(f, dict) else {}
        if len(pts) < 3 or (isinstance(f, dict) and not f.get("closed", True)):
            continue
        pts = [tuple(p[:2]) for p in pts]
        if area(pts) < 0:
            pts = pts[::-1]
        if abs(area(pts)) < 4.0:
            continue
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        if keep and not keep(cx, cy):
            continue
        i = max(range(len(pts)), key=lambda i: math.dist(pts[i], pts[(i + 1) % len(pts)]))
        (ax, ay), (bx, by) = pts[i], pts[(i + 1) % len(pts)]
        turn = math.atan2(by - ay, bx - ax)
        c, s = math.cos(-turn), math.sin(-turn)
        local = [((x - cx) * c - (y - cy) * s, (x - cx) * s + (y - cy) * c) for x, y in pts]
        h = height_of(tags, floor_h) or rnd.randint(*default_levels) * floor_h + 1.0
        lows = [ground(x, y) for x, y in pts]
        z = base if base is not None else max(lows)
        P = Parts()
        P.slab(local, -(z - min(lows)) - footing, h, 0)
        mat = materials[min(len(materials) - 1, int(len(materials) * min(0.999, h / 60.0)))]
        ob = P.build(f"{name}.{k:04d}", [mat], coll)
        ob.matrix_world = Matrix.Translation((cx, cy, z)) @ Matrix.Rotation(turn, 4, "Z")
        ob["half_x"] = max(abs(p[0]) for p in local)
        ob["half_y"] = max(abs(p[1]) for p in local)
        ob["height"] = h
        if tags.get("name"):
            ob["osm_name"] = tags["name"]
        made.append(ob)
    return made


def skyline(centre, near, far, count, ground, materials, bearing=None, seed=7, tall=110.0, collection=None, name="Far city"):
    """A town on the horizon: `count` blocks between `near` and `far` metres from `centre` (an (x, y) in the scene),
    taller and denser towards the middle of the band, a mix of long housing slabs and towers. bearing: optional
    (from_deg, to_deg), counter-clockwise from +x, to keep it to one side. Only for distances where outline and lit
    windows are all that reads; put landmarks in by hand. Returns the objects."""
    rnd = random.Random(seed)
    coll = collection or bpy.data.collections.get(name) or bpy.data.collections.new(name)
    if coll.name not in bpy.context.scene.collection.children and collection is None:
        bpy.context.scene.collection.children.link(coll)
    made, tries = [], 0
    mid = (near + far) / 2
    while len(made) < count and tries < count * 60:
        tries += 1
        r = rnd.uniform(near, far)
        a = rnd.uniform(*(bearing or (0.0, 360.0)))
        x, y = centre[0] + r * math.cos(math.radians(a)), centre[1] + r * math.sin(math.radians(a))
        core = math.exp(-((r - mid) / (far - near) * 2.2) ** 2)
        if rnd.random() < 0.55:
            h = min(rnd.lognormvariate(3.3, 0.45) * (0.55 + 0.9 * core), tall)
            w, d = (rnd.uniform(34, 60), rnd.uniform(12, 16)) if rnd.random() < 0.45 else (rnd.uniform(18, 30), rnd.uniform(16, 26))
        else:
            h, w, d = rnd.uniform(7, 22), rnd.uniform(16, 44), rnd.uniform(10, 18)
        turn = math.radians(a) + rnd.choice([0.0, 0.0, 0.14, -0.22, math.pi / 2])
        P = Parts()
        P.box((0, 0, h / 2 - 4), (w, d, h + 8), 0)
        mat = materials[min(len(materials) - 1, int(len(materials) * min(0.999, h / tall)))]
        ob = P.build(name, [mat], coll)
        ob.matrix_world = Matrix.Translation((x, y, ground(x, y))) @ Matrix.Rotation(turn, 4, "Z")
        ob["half_x"], ob["half_y"] = w / 2, d / 2
        made.append(ob)
    return made
