"""Position assets on an evaluated surface and scatter editable collection instances."""

import math

import bpy
from mathutils import Vector


def seat(obj, surface, local_anchor=(0, 0, 0)):
    """Set the chosen local contact point onto the terrain below its current world XY."""
    bpy.context.view_layer.update()
    terrain = surface.evaluated_get(bpy.context.evaluated_depsgraph_get())
    contact = obj.matrix_world @ Vector(local_anchor)
    top = max((terrain.matrix_world @ Vector(corner)).z for corner in terrain.bound_box)
    inverse = terrain.matrix_world.inverted()
    origin = inverse @ Vector((contact.x, contact.y, top + max(1.0, terrain.dimensions.z * .01)))
    down = (inverse.to_3x3() @ Vector((0, 0, -1))).normalized()
    hit, point, _, _ = terrain.ray_cast(origin, down)
    if not hit:
        raise ValueError(f"{obj.name} is outside {surface.name} at the requested anchor.")
    world_point = terrain.matrix_world @ point
    matrix = obj.matrix_world.copy()
    matrix.translation += world_point - contact
    obj.matrix_world = matrix
    return tuple(world_point)


def scatter(surface, prototypes, density, seed=0, scale=(0.8, 1.2), mask=None, align_normal=False):
    """Scatter rooted variants from a collection. A named float attribute can paint out paths."""
    if surface.type != "MESH" or not prototypes.all_objects:
        raise ValueError("Scatter needs a mesh surface and a populated prototype collection.")
    if density < 0 or scale[0] <= 0 or scale[1] < scale[0]:
        raise ValueError("Choose a nonnegative density and positive ordered scale bounds.")
    if mask and mask not in surface.data.attributes and mask not in surface.vertex_groups:
        evaluated = surface.evaluated_get(bpy.context.evaluated_depsgraph_get())
        if mask not in evaluated.data.attributes:
            raise ValueError(f"No density field named {mask!r} on {surface.name}.")
    name = f"{prototypes.name} on {surface.name}"
    group = bpy.data.node_groups.new(name, "GeometryNodeTree")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    for label, typ, value in [("Density", "NodeSocketFloat", density), ("Seed", "NodeSocketInt", seed),
                              ("Min scale", "NodeSocketFloat", scale[0]), ("Max scale", "NodeSocketFloat", scale[1])]:
        socket = group.interface.new_socket(name=label, in_out="INPUT", socket_type=typ)
        socket.default_value = value
        socket.min_value = 0

    def node(kind, x, y):
        item = group.nodes.new(kind)
        item.location = (x, y)
        return item

    link = group.links.new
    inputs = node("NodeGroupInput", -840, -80)
    ground = node("GeometryNodeObjectInfo", -840, 210)
    ground.transform_space = "RELATIVE"
    ground.inputs["Object"].default_value = surface
    points = node("GeometryNodeDistributePointsOnFaces", -380, 210)
    link(ground.outputs["Geometry"], points.inputs["Mesh"])
    link(inputs.outputs["Seed"], points.inputs["Seed"])
    density_field = inputs.outputs["Density"]
    if mask:
        attr = node("GeometryNodeInputNamedAttribute", -840, -340)
        attr.data_type = "FLOAT"
        attr.inputs["Name"].default_value = mask
        mul = node("ShaderNodeMath", -600, -100)
        mul.operation = "MULTIPLY"
        link(attr.outputs["Attribute"], mul.inputs[0])
        link(density_field, mul.inputs[1])
        density_field = mul.outputs[0]
    link(density_field, points.inputs["Density"])
    variants = node("GeometryNodeCollectionInfo", -370, -30)
    variants.inputs["Collection"].default_value = prototypes
    variants.inputs["Separate Children"].default_value = True
    variants.inputs["Reset Children"].default_value = True
    random_scale = node("FunctionNodeRandomValue", -370, -290)
    random_scale.data_type = "FLOAT"
    def active_socket(sockets, label):
        return next(s for s in sockets if s.name == label and s.enabled)
    link(inputs.outputs["Min scale"], active_socket(random_scale.inputs, "Min"))
    link(inputs.outputs["Max scale"], active_socket(random_scale.inputs, "Max"))
    link(inputs.outputs["Seed"], random_scale.inputs["Seed"])
    inst = node("GeometryNodeInstanceOnPoints", -70, 210)
    inst.inputs["Pick Instance"].default_value = True
    link(points.outputs["Points"], inst.inputs["Points"])
    link(variants.outputs["Instances"], inst.inputs["Instance"])
    link(active_socket(random_scale.outputs, "Value"), inst.inputs["Scale"])
    if align_normal:
        link(points.outputs["Rotation"], inst.inputs["Rotation"])
    yaw = node("FunctionNodeRandomValue", -70, -190)
    yaw.data_type = "FLOAT_VECTOR"
    active_socket(yaw.inputs, "Min").default_value = (0, 0, 0)
    active_socket(yaw.inputs, "Max").default_value = (0, 0, math.tau)
    link(inputs.outputs["Seed"], yaw.inputs["Seed"])
    rotate = node("GeometryNodeRotateInstances", 180, 210)
    link(inst.outputs["Instances"], rotate.inputs["Instances"])
    link(active_socket(yaw.outputs, "Value"), rotate.inputs["Rotation"])
    out = node("NodeGroupOutput", 420, 210)
    link(rotate.outputs["Instances"], out.inputs["Geometry"])

    emitter = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    destination = surface.users_collection[0] if surface.users_collection else bpy.context.collection
    destination.objects.link(emitter)
    emitter.parent = surface
    emitter.modifiers.new(name, "NODES").node_group = group
    return emitter
