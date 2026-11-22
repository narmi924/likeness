"""Seeded branching plants and editable leaf or grass instances."""

import math
import random
from pathlib import Path

import bpy
from mathutils import Vector

from . import layout, shading


def branch_spline(curve, points, radii):
    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(len(points) - 1)
    for point, co, radius in zip(spline.bezier_points, points, radii):
        point.co, point.radius = co, max(0.001, radius)
        point.handle_left_type = point.handle_right_type = "AUTO"
    return spline


def leaf_mesh(card):
    mesh = bpy.data.meshes.new("Leaf blade")
    if card:
        verts = [(-0.36, 0, 0), (0.36, 0, 0), (0.36, 1, 0), (-0.36, 1, 0)]
        faces = [(0, 1, 2, 3)]
    else:
        verts = [
            (0, 0, 0),
            (-0.21, 0.24, -0.035),
            (-0.32, 0.53, -0.025),
            (-0.19, 0.78, -0.035),
            (0, 1, 0),
            (0.19, 0.78, -0.035),
            (0.32, 0.53, -0.025),
            (0.21, 0.24, -0.035),
            (0, 0.5, 0.06),
        ]
        faces = [(i, (i + 1) % 8, 8) for i in range(8)]
    mesh.from_pydata(verts, [], faces)
    uv = mesh.uv_layers.new()
    for loop in mesh.loops:
        x, y, _ = verts[loop.vertex_index]
        uv.data[loop.index].uv = (x / 0.72 + 0.5, y)
    mat, nt, bs = shading.new("Leaves", **{"Base Color": shading.col("#6b853f"), "Roughness": 0.68})
    if card:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(str(Path(card).resolve(strict=True)), check_existing=True)
        nt.links.new(tex.outputs["Color"], bs.inputs["Base Color"])
        nt.links.new(tex.outputs["Alpha"], bs.inputs["Alpha"])
    else:
        info = nt.nodes.new("ShaderNodeObjectInfo")
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        ramp.color_ramp.elements[0].color = shading.col("#486940")
        ramp.color_ramp.elements[1].color = shading.col("#b3ad55")
        nt.links.new(info.outputs["Random"], ramp.inputs["Fac"])
        nt.links.new(ramp.outputs["Color"], bs.inputs["Base Color"])
    mesh.materials.append(mat)
    return bpy.data.objects.new("Leaf prototype", mesh)


def leaf_instances(parent, rows, card):
    mesh = bpy.data.meshes.new("Leaf sockets")
    mesh.from_pydata([p for p, _, _ in rows], [], [])
    rotations = mesh.attributes.new("leaf_rotation", "FLOAT_VECTOR", "POINT")
    rotations.data.foreach_set("vector", [a for _, angles, _ in rows for a in angles])
    sizes = mesh.attributes.new("leaf_size", "FLOAT", "POINT")
    sizes.data.foreach_set("value", [s for _, _, s in rows])
    ob = bpy.data.objects.new("Canopy", mesh)
    parent.users_collection[0].objects.link(ob)
    ob.parent = parent
    group = bpy.data.node_groups.new("Leaf instances", "GeometryNodeTree")
    for mode in ("INPUT", "OUTPUT"):
        group.interface.new_socket(name="Geometry", in_out=mode, socket_type="NodeSocketGeometry")
    nodes, link = group.nodes, group.links.new
    source = nodes.new("NodeGroupInput")
    source.location = (-580, 220)
    info = nodes.new("GeometryNodeObjectInfo")
    info.location = (-580, 0)
    info.inputs["Object"].default_value = leaf_mesh(card)
    info.inputs["As Instance"].default_value = True
    inst = nodes.new("GeometryNodeInstanceOnPoints")
    inst.location = (0, 160)
    link(source.outputs[0], inst.inputs["Points"])
    link(info.outputs["Geometry"], inst.inputs["Instance"])
    for row, (attr, socket, kind) in enumerate(
        (("leaf_rotation", "Rotation", "FLOAT_VECTOR"), ("leaf_size", "Scale", "FLOAT"))
    ):
        field = nodes.new("GeometryNodeInputNamedAttribute")
        field.location = (-300, -row * 180)
        field.data_type = kind
        field.inputs["Name"].default_value = attr
        link(field.outputs["Attribute"], inst.inputs[socket])
    out = nodes.new("NodeGroupOutput")
    out.location = (250, 160)
    link(inst.outputs[0], out.inputs[0])
    ob.modifiers.new("Canopy leaves", "NODES").node_group = group


def tree(
    height=8, trunk_radius=0.25, levels=3, branches=(4, 6, 10), seed=0, leaf_card=None, leaf_density=1.0
):
    if (
        height <= 0
        or trunk_radius <= 0
        or levels < 1
        or not branches
        or min(branches) < 1
        or leaf_density < 0
    ):
        raise ValueError(
            "Use positive tree dimensions, levels and branch counts, and nonnegative leaf density."
        )
    rng = random.Random(seed)
    curve = bpy.data.curves.new("Branches", "CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 3
    curve.bevel_depth = trunk_radius
    curve.bevel_resolution = 2
    curve.use_fill_caps = True
    tree_obj = bpy.data.objects.new("Tree", curve)
    bpy.context.collection.objects.link(tree_obj)
    bark, _, _ = shading.new("Bark", **{"Base Color": shading.col("#69503c"), "Roughness": 0.9})
    curve.materials.append(bark)
    trunk = [
        Vector((height * x, height * y, height * z))
        for x, y, z in [
            (0, 0, 0),
            (0.008, 0.006, 0.17),
            (-0.018, 0.009, 0.36),
            (0.008, -0.015, 0.56),
            (0.035, 0.014, 0.72),
            (0.018, 0.024, 0.89),
            (0.015, 0, 0.98),
        ]
    ]
    branch_spline(curve, trunk, [1.24, 1, 0.79, 0.55, 0.32, 0.16, 0.025])
    leaves = []

    def foliage(a, b, amount):
        for _ in range(amount):
            t = rng.uniform(0.22, 1.02)
            p = (
                a.lerp(b, t)
                + Vector((rng.uniform(-0.018, 0.018), rng.uniform(-0.018, 0.018), rng.uniform(-0.01, 0.014)))
                * height
            )
            rot = (rng.uniform(-0.6, 0.95), rng.uniform(-0.65, 0.65), rng.uniform(0, math.tau))
            leaves.append((p, rot, height * rng.uniform(0.033, 0.061)))

    def limb(start, azimuth, length, lift, radius, depth):
        horizontal = Vector((math.cos(azimuth), math.sin(azimuth), 0))
        mid = start + horizontal * length * 0.54 + Vector((0, 0, length * lift * 0.32))
        end = start + horizontal * length + Vector((0, 0, length * lift))
        mid += Vector((-horizontal.y, horizontal.x, 0)) * rng.uniform(-0.09, 0.09) * length
        branch_spline(curve, [start, mid, end], [radius, radius * 0.59, radius * 0.11])
        if depth == levels:
            foliage(mid, end, round(10 * leaf_density))
            return
        count = branches[min(depth, len(branches) - 1)]
        for i in range(count):
            t = 0.25 + 0.7 * (i + 0.5) / count
            pos = start.lerp(mid, 2 * t) if t < 0.5 else mid.lerp(end, 2 * t - 1)
            sign = 1 if i % 2 else -1
            limb(
                pos,
                azimuth + sign * rng.uniform(0.48, 1.5),
                length * rng.uniform(0.36, 0.57),
                min(1.7, lift + rng.uniform(0.04, 0.38)),
                radius * rng.uniform(0.32, 0.52),
                depth + 1,
            )
        foliage(mid, end, round(4 * leaf_density))

    phase = rng.uniform(0, math.tau)
    for i in range(branches[0]):
        t = 0.32 + 0.45 * (i + 0.5) / branches[0]
        a, b = next((a, b) for a, b in zip(trunk, trunk[1:]) if a.z <= t * height <= b.z)
        root = a.lerp(b, (t * height - a.z) / (b.z - a.z))
        limb(
            root,
            phase + i * 2.39996,
            height * (0.31 - 0.12 * (t - 0.32)) * rng.uniform(0.92, 1.08),
            rng.uniform(0.68, 1.05),
            0.47 * (1 - t * 0.55),
            1,
        )
    for a, b in zip(trunk[3:], trunk[4:]):
        foliage(a, b, round(13 * leaf_density))
    if leaves:
        leaf_instances(tree_obj, leaves, leaf_card)
    return tree_obj


def grass(surface, density, height, mask=None):
    if height <= 0:
        raise ValueError("Grass height must be positive.")
    variants = bpy.data.collections.new("Grass blades")
    material, _, _ = shading.new("Grass", **{"Base Color": shading.col("#73804a"), "Roughness": 0.86})
    for i in range(3):
        curve = bpy.data.curves.new("Grass tuft", "CURVE")
        curve.dimensions = "3D"
        curve.resolution_u = 2
        curve.bevel_depth = height * 0.025
        curve.materials.append(material)
        for j in range(4):
            angle = j * 2.4 + i * 0.7
            dx, dy = math.cos(angle) * height, math.sin(angle) * height
            branch_spline(
                curve,
                [
                    (0, 0, 0),
                    (dx * 0.11, dy * 0.11, height * 0.55),
                    (dx * 0.32, dy * 0.32, height * (0.8 + 0.06 * j)),
                ],
                [1, 0.67, 0.009],
            )
        variants.objects.link(bpy.data.objects.new("Tuft", curve))
    return layout.scatter(surface, variants, density, mask=mask, align_normal=True)


def bush(size, seed=0):
    obj = tree(size * 0.95, size * 0.042, levels=2, branches=(8, 9), seed=seed, leaf_density=1.5)
    obj.name = "Shrub"
    obj.scale = (1.3, 1.3, 0.82)
    return obj
