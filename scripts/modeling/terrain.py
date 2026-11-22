"""Editable terrain, stone, water and daylight in Blender units."""

import math
import random

import bmesh
import bpy
from mathutils import Vector, noise

from . import core, lighting, sculpt, shading


def ground(size=(100, 100), resolution=256, height=8.0, seed=0, octaves=5, roughness=0.5, flatten=None):
    if min(size) <= 0 or resolution < 2 or octaves < 1 or not 0 <= roughness <= 1:
        raise ValueError(
            "Use positive ground dimensions, at least two grid vertices and valid noise settings."
        )
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=resolution, y_segments=resolution, size=1)
    for v in bm.verts:
        v.co.x *= size[0] / 2
        v.co.y *= size[1] / 2
    obj = core.mesh_object("Terrain", bm)
    obj.data.shade_smooth()
    group = bpy.data.node_groups.new("Ground elevation", "GeometryNodeTree")
    group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    for name, typ, default in (
        ("Height", "NodeSocketFloat", height),
        ("Seed", "NodeSocketFloat", seed),
        ("Detail", "NodeSocketFloat", octaves - 1),
        ("Roughness", "NodeSocketFloat", roughness),
    ):
        group.interface.new_socket(name=name, in_out="INPUT", socket_type=typ).default_value = default

    def node(kind, x, y, label=""):
        n = group.nodes.new(kind)
        n.location = (x, y)
        n.label = label
        return n

    link = group.links.new

    def math_node(op, source, other, x, y):
        n = node("ShaderNodeMath", x, y)
        n.operation = op
        link(source, n.inputs[0])
        if isinstance(other, bpy.types.NodeSocket):
            link(other, n.inputs[1])
        else:
            n.inputs[1].default_value = other
        return n.outputs[0]

    inputs = node("NodeGroupInput", -1000, 420)
    pos = node("GeometryNodeInputPosition", -1000, 90)
    tex = node("ShaderNodeTexNoise", -740, 120, "Terrain relief")
    tex.noise_dimensions = "4D"
    tex.inputs["Scale"].default_value = 3 / max(size)
    link(pos.outputs[0], tex.inputs["Vector"])
    for name, target in (("Seed", "W"), ("Detail", "Detail"), ("Roughness", "Roughness")):
        link(inputs.outputs[name], tex.inputs[target])
    elev = math_node("SUBTRACT", tex.outputs["Fac"], 0.5, -500, 150)
    elev = math_node("MULTIPLY", elev, inputs.outputs["Height"], -310, 150)
    for i, (center, radius) in enumerate(flatten or []):
        if radius <= 0:
            raise ValueError("A ground clearing needs a positive radius.")
        y = -220 - 235 * i
        dist = node("ShaderNodeVectorMath", -750, y, "Clearing radius")
        dist.operation = "DISTANCE"
        link(pos.outputs[0], dist.inputs[0])
        dist.inputs[1].default_value = (*center[:2], 0)
        fall = node("ShaderNodeMapRange", -500, y, "Soft edge")
        fall.interpolation_type = "SMOOTHSTEP"
        fall.inputs["From Min"].default_value = radius
        fall.inputs["From Max"].default_value = 1.4 * radius
        link(dist.outputs["Value"], fall.inputs["Value"])
        elev = math_node("MULTIPLY", elev, fall.outputs["Result"], -100 + 145 * i, y)
    z = node("ShaderNodeCombineXYZ", 260, 150)
    link(elev, z.inputs["Z"])
    position = node("GeometryNodeSetPosition", 480, 420)
    link(inputs.outputs["Geometry"], position.inputs["Geometry"])
    link(z.outputs[0], position.inputs["Offset"])
    out = node("NodeGroupOutput", 730, 420)
    link(position.outputs[0], out.inputs["Geometry"])
    obj.modifiers.new("Terrain relief", "NODES").node_group = group
    mat, nt, bs = shading.new("Ground cover", Roughness=0.89)
    texture = nt.nodes.new("ShaderNodeTexNoise")
    texture.inputs["Scale"].default_value = 4
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = shading.col("#506441")
    ramp.color_ramp.elements[1].color = shading.col("#b2a371")
    nt.links.new(texture.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bs.inputs["Base Color"])
    core.assign(obj, mat)
    return obj


def rock(size=1.0, seed=0, detail=3):
    if size <= 0 or not 1 <= detail <= 8:
        raise ValueError("Choose a positive rock size and detail from 1 to 8.")
    rng = random.Random(seed)
    shift = Vector([rng.uniform(-50, 50) for _ in range(3)])
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    bmesh.ops.subdivide_edges(bm, edges=list(bm.edges), cuts=2 * detail, use_grid_fill=True)
    for v in bm.verts:
        p = v.co.lerp(v.co.normalized() * 0.59, 0.65)
        p *= 1 + 0.28 * noise.noise(p * 1.9 + shift)
        v.co = (p.x * size, p.y * size * 0.88, p.z * size * 0.69)
    obj = core.mesh_object("Fieldstone", bm)
    sculpt.voxel_remesh(obj, size / (12 + detail * 8))
    texture = bpy.data.textures.new("Stone grain", type="CLOUDS")
    texture.noise_scale = size * 0.21
    mod = obj.modifiers.new("Stone surface", "DISPLACE")
    mod.texture, mod.strength = texture, size * 0.055
    core.apply_all(obj)
    lowest = min(v.co.z for v in obj.data.vertices)
    for v in obj.data.vertices:
        v.co.z -= lowest
    obj.data.shade_smooth()
    mat, _, _ = shading.new("Fieldstone", **{"Base Color": shading.col("#877f71"), "Roughness": 0.86})
    return core.assign(obj, mat)


def sky(
    kind="nishita",
    sun_elevation=35,
    sun_rotation=0,
    altitude=0,
    air=1.0,
    dust=1.0,
    ozone=1.0,
    strength=1.0,
    hdri=None,
):
    if kind == "hdri":
        if not hdri:
            raise ValueError("Supply an HDRI path with hdri=.")
        return lighting.environment(hdri=hdri, strength=strength, rotation=sun_rotation)
    if kind != "nishita":
        raise ValueError("Choose nishita or hdri.")
    world = bpy.data.worlds.new("Daylight")
    bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    light = nt.nodes.new("ShaderNodeTexSky")
    light.sky_type = "SINGLE_SCATTERING"
    light.sun_elevation, light.sun_rotation = map(math.radians, (sun_elevation, sun_rotation))
    light.altitude, light.air_density, light.aerosol_density, light.ozone_density = altitude, air, dust, ozone
    bg = nt.nodes.get("Background")
    bg.inputs["Strength"].default_value = strength
    nt.links.new(light.outputs["Color"], bg.inputs["Color"])
    return world


def water(size, level=0, wave_scale=5, color="#467c7b"):
    x, y = (size, size) if isinstance(size, (int, float)) else size
    if min(x, y, wave_scale) <= 0:
        raise ValueError("Water dimensions and wave scale must be positive.")
    bm = bmesh.new()
    bm.faces.new([bm.verts.new((i * x / 2, j * y / 2, 0)) for i, j in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
    obj = core.mesh_object("Water", bm, location=(0, 0, level))
    mat, nt, bs = shading.new(
        "Water",
        **{"Base Color": shading.col(color), "Roughness": 0.105, "Transmission Weight": 0.83, "IOR": 1.333},
    )
    tex = nt.nodes.new("ShaderNodeTexNoise")
    tex.inputs["Scale"].default_value = wave_scale
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.2
    bump.inputs["Distance"].default_value = 0.04
    nt.links.new(tex.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bs.inputs["Normal"])
    return core.assign(obj, mat)
