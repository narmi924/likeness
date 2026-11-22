"""A small wooden jetty on a lake at dusk, reached along a gravel path on the shore. A rowing boat is tied beside the
jetty; the far shore is a line of conifers under low hills.

    likeness edit new build/01-scene.py tmp/01.blend"""
import math
import random

import bmesh
import bpy
from mathutils import Vector, noise

from modeling import assets, outdoor, terrain

random.seed(11)
lib = outdoor.library
scene = bpy.context.scene
WATER = 0.0
DECK = 0.58                                    # top of the jetty's planks above the water
JETTY_X, JETTY_Y0, JETTY_Y1, JETTY_W = 2.0, 2.2, 15.4, 1.7
PATH = [[-46, -5.5], [-30, -4.2], [-18, -3.2], [-8.8, -2.6], [-4.4, -1.9], [-1.6, -0.6], [0.4, 0.9], [2.0, 2.7]]   # ends on the jetty's first planks


def smooth(a, b, v):
    t = max(0.0, min(1.0, (v - a) / (b - a)))
    return t * t * (3 - 2 * t)


def n2(x, y, s, seed=0.0):
    return noise.noise(Vector((x * s + seed, y * s - seed, seed * 0.37)))


def height(x, y):
    """Near shore: a low bank falling into the water. Far shore, 115 m off: a beach, then hills."""
    shore = 3.0 + 1.6 * n2(x, 0, 0.045, 3.0)                 # the waterline wanders
    d = y - shore
    if d < 0:
        z = 0.58 * smooth(0, -5.5, d) + 0.02 * min(40.0, -d) + 0.10 * n2(x, y, 0.12, 7.0)
    else:
        z = -1.7 * smooth(0, 11, d)
    far = y - (118 + 9 * n2(x, 0, 0.02, 11.0))
    if far > -14:
        z = max(z, -1.7 + 2.3 * smooth(-14, 3, far) + 0.16 * max(0.0, far) * (1 + 0.5 * n2(x, y, 0.03, 5.0)))
    east = x - (88 + 10 * n2(0, y, 0.025, 17.0))             # the east end of the lake: a wooded shore that joins the two
    if east > -14 and y > shore - 6:
        z = max(z, -1.7 + 2.3 * smooth(-14, 3, east) + 0.14 * max(0.0, east) * (1 + 0.5 * n2(x, y, 0.03, 9.0)))
    return z


# ---------------------------------------------------------------- ground and water
ground = outdoor.land((-150, -70, 150, 210), height, cell=0.9)
outdoor.ground(ground, lib("polyhaven:leafy_grass"), size=3.2, patches=lib("polyhaven:forest_leaves_02"),
               path=lib("polyhaven:gravel_floor_02"), path_line=PATH, path_width=2.0, verge=7.0,
               tint=(0.50, 0.66, 0.36), path_tint=(0.60, 0.56, 0.50), shore=lib("polyhaven:coast_sand_rocks_02"), water_level=WATER, shore_height=0.5)

water = terrain.water((700, 700), level=WATER, wave_scale=0.55, color="#2f4a4c")
wm = water.active_material.node_tree
wb = wm.nodes["Principled BSDF"]
wb.inputs["Roughness"].default_value = 0.04
wb.inputs["Transmission Weight"].default_value = 0.6
for node in wm.nodes:
    if node.type == "BUMP":
        node.inputs["Strength"].default_value, node.inputs["Distance"].default_value = 0.16, 0.03
    if node.type == "TEX_NOISE":   # long low ripples running along the shore, not round blobs
        stretch = wm.nodes.new("ShaderNodeMapping")
        coords = wm.nodes.new("ShaderNodeNewGeometry")
        stretch.inputs["Scale"].default_value = (0.45, 1.6, 1.0)
        wm.links.new(coords.outputs["Position"], stretch.inputs["Vector"])
        wm.links.new(stretch.outputs[0], node.inputs["Vector"])
        node.inputs["Detail"].default_value = 3.0


# ---------------------------------------------------------------- wood
def wood(name, folder, metres, tint=(1, 1, 1), vary=0.16, rough_add=0.0):
    """Planks from a scanned set, wrapped round each piece from three sides; every piece a little lighter or darker."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    t = m.node_tree
    b = t.nodes["Principled BSDF"]
    tc, mapping, info = t.nodes.new("ShaderNodeTexCoord"), t.nodes.new("ShaderNodeMapping"), t.nodes.new("ShaderNodeObjectInfo")
    mapping.inputs["Scale"].default_value = (1 / metres,) * 3
    t.links.new(tc.outputs["Object"], mapping.inputs["Vector"])
    shift = t.nodes.new("ShaderNodeVectorMath")
    shift.operation = "ADD"
    t.links.new(mapping.outputs[0], shift.inputs[0])
    spread = t.nodes.new("ShaderNodeCombineXYZ")
    for i in range(3):
        t.links.new(info.outputs["Random"], spread.inputs[i])
    scale_up = t.nodes.new("ShaderNodeVectorMath")
    scale_up.operation = "SCALE"
    t.links.new(spread.outputs[0], scale_up.inputs[0])
    scale_up.inputs["Scale"].default_value = 7.3
    t.links.new(scale_up.outputs[0], shift.inputs[1])
    maps = {}
    for key, file in (("col", "Diffuse.jpg"), ("rough", "Rough.jpg"), ("disp", "Displacement.png")):
        tex = t.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(str(folder / file), check_existing=True)
        tex.projection, tex.projection_blend = "BOX", 0.15
        if key != "col":
            tex.image.colorspace_settings.name = "Non-Color"
        t.links.new(shift.outputs[0], tex.inputs["Vector"])
        maps[key] = tex.outputs["Color"]
    value = t.nodes.new("ShaderNodeMapRange")
    value.inputs["To Min"].default_value, value.inputs["To Max"].default_value = 1 - vary, 1 + vary
    t.links.new(info.outputs["Random"], value.inputs["Value"])
    hsv = t.nodes.new("ShaderNodeHueSaturation")
    t.links.new(maps["col"], hsv.inputs["Color"])
    t.links.new(value.outputs["Result"], hsv.inputs["Value"])
    tinted = t.nodes.new("ShaderNodeMix")
    tinted.data_type, tinted.blend_type = "RGBA", "MULTIPLY"
    tinted.inputs[0].default_value = 1.0
    t.links.new(hsv.outputs["Color"], tinted.inputs[6])
    tinted.inputs[7].default_value = (*tint, 1)
    t.links.new(tinted.outputs[2], b.inputs["Base Color"])
    r = t.nodes.new("ShaderNodeMath")
    r.operation, r.use_clamp = "ADD", True
    t.links.new(maps["rough"], r.inputs[0])
    r.inputs[1].default_value = rough_add
    t.links.new(r.outputs[0], b.inputs["Roughness"])
    bump = t.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value, bump.inputs["Distance"].default_value = 0.7, 0.01
    t.links.new(maps["disp"], bump.inputs["Height"])
    t.links.new(bump.outputs["Normal"], b.inputs["Normal"])
    return m


GREY = wood("Jetty planks", lib("polyhaven:wood_planks_grey"), 1.4, tint=(0.86, 0.84, 0.80))
DARK = wood("Jetty piles", lib("polyhaven:weathered_brown_planks"), 1.2, tint=(0.50, 0.46, 0.42), rough_add=0.05)


def box(name, size, at, material, parent, turn=(0, 0, 0), bevel=0.006):
    b = bmesh.new()
    bmesh.ops.create_cube(b, size=1.0)
    for v in b.verts:
        v.co.x *= size[0]
        v.co.y *= size[1]
        v.co.z *= size[2]
    if bevel:
        bmesh.ops.bevel(b, geom=list(b.edges), offset=bevel, segments=1, affect="EDGES")
    me = bpy.data.meshes.new(name)
    b.to_mesh(me)
    b.free()
    o = bpy.data.objects.new(name, me)
    scene.collection.objects.link(o)
    o.location, o.rotation_euler, o.parent = at, turn, parent
    me.materials.append(material)
    return o


def pole(name, radius, top, bottom, at, material, parent, lean=(0, 0)):
    b = bmesh.new()
    bmesh.ops.create_cone(b, cap_ends=True, segments=14, radius1=radius * 1.08, radius2=radius, depth=top - bottom)
    bmesh.ops.bevel(b, geom=[e for e in b.edges if all(v.co.z > 0 for v in e.verts)], offset=radius * 0.18, segments=2, affect="EDGES")
    me = bpy.data.meshes.new(name)
    b.to_mesh(me)
    b.free()
    for p in me.polygons:
        p.use_smooth = len(p.vertices) == 4
    o = bpy.data.objects.new(name, me)
    scene.collection.objects.link(o)
    o.location, o.rotation_euler, o.parent = (at[0], at[1], (top + bottom) / 2), (lean[0], lean[1], random.uniform(0, 6.28)), parent
    me.materials.append(material)
    return o


# ---------------------------------------------------------------- the jetty: piles, two beams, cross planks
jetty = bpy.data.objects.new("Jetty", None)
scene.collection.objects.link(jetty)
length = JETTY_Y1 - JETTY_Y0
y = JETTY_Y0
k = 0
while y < JETTY_Y1 - 0.08:
    w = random.uniform(0.125, 0.155)
    over = random.uniform(-0.03, 0.05)   # the ends are not sawn to one line
    sag = 0.012 * math.sin(k * 1.7) + random.uniform(-0.004, 0.004)
    box(f"Plank.{k:03d}", (JETTY_W + over, w, 0.036), (JETTY_X + random.uniform(-0.012, 0.012), y + w / 2, DECK - 0.018 + sag), GREY, jetty,
        turn=(random.uniform(-0.012, 0.012), random.uniform(-0.01, 0.01), random.uniform(-0.012, 0.012)))
    y += w + random.uniform(0.006, 0.016)
    k += 1
for side in (-1, 1):
    box(f"Beam.{side}", (0.09, length, 0.16), (JETTY_X + side * (JETTY_W / 2 - 0.22), (JETTY_Y0 + JETTY_Y1) / 2, DECK - 0.036 - 0.08), DARK, jetty)
row = 0
py = JETTY_Y0 + 0.5
while py < JETTY_Y1 + 0.01:
    for side in (-1, 1):
        top = DECK + (random.uniform(0.32, 0.5) if row % 2 == 0 or py > JETTY_Y1 - 0.6 else -0.02)
        pole(f"Pile.{row}.{side}", random.uniform(0.075, 0.09), top, height(JETTY_X, py) - 0.6, (JETTY_X + side * (JETTY_W / 2 - 0.05), py), DARK, jetty,
             lean=(random.uniform(-0.03, 0.03), side * random.uniform(0.0, 0.035)))
    box(f"Tie.{row}", (JETTY_W - 0.1, 0.07, 0.12), (JETTY_X, py + 0.1, DECK - 0.036 - 0.16 - 0.06), DARK, jetty)
    row += 1
    py += 2.55
# where the planks meet the bank: two flat stones as a step, so the deck does not simply stop on the grass
outdoor.plant(lib("polyhaven:namaqualand_stones_01"), [(JETTY_X - 0.2, JETTY_Y0 - 0.55), (JETTY_X + 1.35, JETTY_Y0 + 0.2), (JETTY_X - 1.5, JETTY_Y0 + 0.5)],
              size=1.5, ground_object=ground, sink=0.22, seed=3, name="Step stones")

# ---------------------------------------------------------------- the boat: a clinker-built rowing boat, 3.7 m
boat = bpy.data.objects.new("Boat", None)
scene.collection.objects.link(boat)
L, BEAM, DEPTH = 3.7, 1.34, 0.46
STATIONS, RIBS = 30, 12


def section(u):
    """Half breadth, keel depth and sheer height at u from the stern (-1) to the bow (1)."""
    full = (1 - abs(u) ** 2.3) ** 0.62 if u > 0 else (1 - abs(u) ** 3.2) ** 0.5 * 0.94 + 0.06
    half = BEAM / 2 * max(0.02, full)
    keel = -DEPTH * (0.72 + 0.28 * (1 - abs(u) ** 2.5))
    sheer = 0.06 * u * u + (0.07 * u ** 3 if u > 0 else 0.0)
    return half, keel, sheer


b = bmesh.new()
rings = []
for i in range(STATIONS + 1):
    u = -1 + 2 * i / STATIONS
    half, keel, sheer = section(u)
    ring = []
    for j in range(-RIBS, RIBS + 1):
        a = j / RIBS                               # -1 port gunwale, 0 keel, 1 starboard gunwale
        side = abs(a)
        yy = half * math.copysign(side ** 0.72, a)
        zz = keel + (sheer - keel) * side ** 2.1
        zz += 0.006 * (math.floor(side * 6) - side * 6)   # the overlapping strakes of clinker planking
        ring.append(b.verts.new((u * L / 2, yy, zz)))
    rings.append(ring)
for r0, r1 in zip(rings, rings[1:]):
    for j in range(2 * RIBS):
        b.faces.new((r0[j], r0[j + 1], r1[j + 1], r1[j]))
stern = b.verts.new((-L / 2, 0, section(-1)[2] - 0.12))
for j in range(2 * RIBS):
    b.faces.new((rings[0][j + 1], rings[0][j], stern))
bmesh.ops.recalc_face_normals(b, faces=list(b.faces))
hull_mesh = bpy.data.meshes.new("Hull")
b.to_mesh(hull_mesh)
b.free()
for p in hull_mesh.polygons:
    p.use_smooth = True
hull = bpy.data.objects.new("Hull", hull_mesh)
scene.collection.objects.link(hull)
hull.parent = boat
solid = hull.modifiers.new("Planking", "SOLIDIFY")
solid.thickness, solid.offset = 0.022, 1.0
solid.use_rim = True
solid.material_offset = 1          # the inside takes the second material
solid.material_offset_rim = 1
PAINT = wood("Boat paint", lib("polyhaven:weathered_brown_planks"), 0.9, tint=(0.36, 0.52, 0.47), vary=0.05, rough_add=-0.12)
INSIDE = wood("Boat inside", lib("polyhaven:wood_planks_grey"), 0.8, tint=(0.92, 0.80, 0.62), vary=0.08)
hull_mesh.materials.append(PAINT)
hull_mesh.materials.append(INSIDE)
# gunwale: a rail along each sheer line
for side in (-1, 1):
    curve = bpy.data.curves.new(f"Gunwale.{side}", "CURVE")
    curve.dimensions, curve.bevel_depth, curve.bevel_resolution = "3D", 0.021, 3
    sp = curve.splines.new("POLY")
    pts = []
    for i in range(STATIONS + 1):
        u = -1 + 2 * i / STATIONS
        half, keel, sheer = section(u)
        pts.append((u * L / 2, side * (half + 0.006), sheer + 0.004))
    sp.points.add(len(pts) - 1)
    for p, co in zip(sp.points, pts):
        p.co = (*co, 1)
    rail = bpy.data.objects.new(f"Gunwale.{side}", curve)
    scene.collection.objects.link(rail)
    rail.parent = boat
    curve.materials.append(INSIDE)
# thwarts (seats), a stern seat, bottom boards, two oars laid across the thwarts
for name, x, width in (("Thwart.mid", -0.15, 0.24), ("Thwart.bow", 0.95, 0.2), ("Seat.stern", -1.5, 0.34)):
    half = section(2 * x / L)[0]
    box(name, (width, 2 * half - 0.03, 0.028), (x, 0, -0.13), INSIDE, boat, bevel=0.004)
for i, yy in enumerate((-0.22, 0.0, 0.22)):
    box(f"Board.{i}", (2.4, 0.19, 0.018), (-0.1, yy, -DEPTH + 0.085), INSIDE, boat, bevel=0.003)
for i, (yy, turn) in enumerate(((-0.27, 0.035), (0.3, -0.05))):
    shaft = pole(f"Oar.{i}", 0.021, 1.15, -1.15, (0, 0), INSIDE, boat)
    shaft.location, shaft.rotation_euler = (0.25, yy, -0.095), (0, math.pi / 2, turn)
    box(f"Blade.{i}", (0.62, 0.14, 0.012), (1.6 + 0.02 * i, yy + turn * 1.4, -0.095), INSIDE, boat, turn=(0.5, 0, turn), bevel=0.004)

# moored on the west side of the jetty, the side the walker comes from, bow towards the shore and a little out of true
BOAT_AT = Vector((JETTY_X - JETTY_W / 2 - BEAM / 2 - 0.22, 8.3, WATER + DEPTH * 0.72 - 0.05))
boat.location = BOAT_AT
boat.rotation_euler = (math.radians(-2.5), math.radians(-1.5), math.radians(-83))
bpy.context.view_layer.update()
# the painter: from the bow to a pile, hanging slack and dipping towards the water
bow = boat.matrix_world @ Vector((L / 2 - 0.05, 0, section(1)[2] + 0.02))
pile_top = Vector((JETTY_X - JETTY_W / 2 + 0.05, JETTY_Y0 + 0.5 + 2.55 * 2, DECK + 0.3))
rope = bpy.data.curves.new("Painter", "CURVE")
rope.dimensions, rope.bevel_depth, rope.bevel_resolution = "3D", 0.011, 2
sp = rope.splines.new("POLY")
pts = []
for i in range(25):
    t = i / 24
    p = bow.lerp(pile_top, t)
    p.z -= 0.32 * math.sin(math.pi * t) * (1 - 0.3 * t)
    pts.append(p)
for turn_i in range(13):   # two turns round the pile
    a = turn_i / 12 * 4 * math.pi
    pts.append(pile_top + Vector((0.1 * math.cos(a) - 0.1, 0.1 * math.sin(a), -0.03 - 0.004 * turn_i)))
sp.points.add(len(pts) - 1)
for p, co in zip(sp.points, pts):
    p.co = (*co, 1)
painter = bpy.data.objects.new("Painter", rope)
scene.collection.objects.link(painter)
painter.parent = boat
painter.matrix_parent_inverse = boat.matrix_world.inverted()
hemp = bpy.data.materials.new("Rope")
hemp.use_nodes = True
hb = hemp.node_tree.nodes["Principled BSDF"]
hb.inputs["Base Color"].default_value, hb.inputs["Roughness"].default_value = (0.43, 0.34, 0.22, 1), 0.9
rope.materials.append(hemp)

# ---------------------------------------------------------------- planting
far = bpy.data.objects.new("Far shore", None)
scene.collection.objects.link(far)
belt = []
while len(belt) < 520:   # dense at the water's edge, where the wood shows its face; thinner up the hills behind
    x, yy = random.uniform(-150, 150), random.uniform(108, 108 + random.choice((14, 30, 75)))
    if height(x, yy) > 0.25 and all((x - s[0]) ** 2 + (yy - s[1]) ** 2 > 5 for s in belt[-60:]):
        belt.append((x, yy, random.uniform(11, 19)))
while len(belt) < 760:   # and round the east end
    x, yy = random.uniform(80, 150), random.uniform(-2, 125)
    if height(x, yy) > 0.25 and all((x - s[0]) ** 2 + (yy - s[1]) ** 2 > 6 for s in belt[-60:]):
        belt.append((x, yy, random.uniform(11, 19)))
random.shuffle(belt)
for o in outdoor.plant(lib("polyhaven:fir_sapling_medium"), belt[:600], size=14, ground_object=ground, sink=0.4, vary=0.25, seed=21, name="Far fir"):
    o.parent = far
for o in outdoor.plant(lib("polyhaven:island_tree_01"), belt[600:], size=12, ground_object=ground, sink=0.3, seed=22, name="Far broadleaf"):
    o.parent = far

# this shore: trees to the left of the path and behind the walker, none between the path and the jetty or the water
near = []
while len(near) < 16:
    x, yy = random.uniform(-70, 45), random.uniform(-50, -6.5)
    if all((x - s[0]) ** 2 + (yy - s[1]) ** 2 > 49 for s in near):
        near.append((x, yy, random.uniform(8, 13)))
outdoor.plant(lib("polyhaven:island_tree_01"), near[:8], size=10, ground_object=ground, seed=23, name="Shore tree A")
outdoor.plant(lib("polyhaven:island_tree_02"), near[8:], size=10, ground_object=ground, seed=24, name="Shore tree B")
stand = []
while len(stand) < 34:
    x, yy = random.uniform(26, 120), random.uniform(-45, -1)
    if all((x - s[0]) ** 2 + (yy - s[1]) ** 2 > 30 for s in stand):
        stand.append((x, yy, random.uniform(9, 15)))
outdoor.plant(lib("polyhaven:island_tree_01"), stand[:17], size=11, ground_object=ground, seed=31, name="East stand A")
outdoor.plant(lib("polyhaven:fir_sapling_medium"), stand[17:], size=12, ground_object=ground, vary=0.25, seed=32, name="East stand B")
outdoor.plant(lib("polyhaven:shrub_03"), [(9.5, -1.5, 1.6), (12.5, 0.6, 2.0), (15, -2.2, 1.7), (20, 0.2, 2.2), (-15.5, -0.8, 1.8)], size=1.8, ground_object=ground, seed=25, name="Bank shrub")
outdoor.plant(lib("polyhaven:coast_land_rocks_02"), [(7.4, 2.9, 2.6), (-7.5, 2.6, 3.4)], size=3, ground_object=ground, sink=0.3, seed=26, name="Shore rocks")
outdoor.plant(lib("polyhaven:namaqualand_stones_01"), [(-3.2, 2.4), (4.6, 3.4), (-10.5, 1.9), (11.5, 3.3), (0.4, 3.6), (6.2, 2.5), (8.8, 3.6), (13.6, 2.9),
                                                       (-1.3, 3.0), (-5.6, 3.2), (17.5, 3.4), (3.5, 2.2)], size=1.1, ground_object=ground, sink=0.12, seed=27, name="Waterline stones")
outdoor.plant(lib("polyhaven:shrub_02"), [(-6.2, -4.6), (4.8, -0.9), (-11.5, -5.2), (8.2, 0.4)], size=1.5, ground_object=ground, seed=28, name="Shrub")
outdoor.strew(ground, [(lib("polyhaven:grass_medium_02"), 0.6), (lib("polyhaven:grass_medium_02"), 0.85), (lib("polyhaven:grass_medium_02"), 1.1),
                       (lib("polyhaven:grass_medium_02"), 0.95), (lib("polyhaven:fern_02"), 0.45)],
              density=4.2, scale=(0.45, 1.5), seed=29)
# reeds standing in the shallows either side of the jetty
reeds = [(JETTY_X + dx + random.uniform(-0.3, 0.3), 3.3 + 1.6 * n2(JETTY_X + dx, 0, 0.045, 3.0) + random.uniform(-0.2, 1.3), random.uniform(1.0, 1.6))
         for dx in (-9.5, -8.6, -7.9, -6.4, -5.6, -4.9, -4.1, -3.0, -2.4, 2.0, 2.7, 3.5, 4.4, 5.6, 6.3, 8.4, 9.3, 10.6)]
outdoor.plant(lib("polyhaven:grass_medium_02"), reeds, size=1.3, ground_object=ground, sink=0.05, seed=30, name="Reeds")

# ---------------------------------------------------------------- light and air
# The walk heads north-east to the jetty. The sun stands low in the north-west, over the far shore to the left of the
# last picture: the jetty and the boat are lit from the side, and the water in front of them carries the sky.
light = outdoor.sky(lib("polyhaven:evening_road_01_puresky"), sun_azimuth=104, sun_elevation=6.5, exposure=0.12, strength=0.45)
outdoor.haze(light, density=0.0017, thin_above=90.0)
outdoor.film_settings(width=1920, samples=64)
scene.cycles.transmission_bounces = 8
