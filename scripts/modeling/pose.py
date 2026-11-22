"""Rebind an imported neutral pose and place knees or elbows using joint landmarks."""

import math

import bpy
import numpy as np
from mathutils import Matrix, Vector


def rebind_visible_pose(rig):
    """Bake the displayed neutral mesh and bones as the new bind pose of an unanimated import."""
    if rig.type != "ARMATURE" or bpy.context.mode != "OBJECT" or rig.library or rig.data.users != 1:
        raise ValueError("Select a local, single-user armature in Object Mode.")
    meshes = [obj for obj in bpy.context.scene.objects
              if obj.type == "MESH" and any(m.type == "ARMATURE" and m.object == rig for m in obj.modifiers)]
    if not meshes:
        raise ValueError("No skinned meshes use this armature.")
    if any(item.animation_data for item in [rig, rig.data, *meshes]) or any(p.constraints for p in rig.pose.bones):
        raise ValueError("Use an unanimated import with no bone constraints before rebinding.")
    dg = bpy.context.evaluated_depsgraph_get()
    surfaces = []
    for obj in meshes:
        enabled = [m for m in obj.modifiers if m.show_viewport]
        if obj.library or obj.data.shape_keys or len(enabled) != 1 or enabled[0].type != "ARMATURE":
            raise ValueError(f"{obj.name} needs a local mesh with only its armature modifier and no shape keys.")
        ev = obj.evaluated_get(dg)
        if len(ev.data.vertices) != len(obj.data.vertices):
            raise ValueError(f"Vertex count changed on {obj.name}.")
        positions = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
        ev.data.vertices.foreach_get("co", positions)
        surfaces.append((obj, positions, ev.matrix_world.copy()))
    previous, active = list(bpy.context.selected_objects), bpy.context.view_layer.objects.active
    try:
        bpy.ops.object.select_all(action="DESELECT")
        rig.select_set(True)
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode="POSE")
        bpy.ops.pose.armature_apply(selected=False)
        bpy.ops.object.mode_set(mode="OBJECT")
        for obj, positions, previous_world in surfaces:
            if obj.data.users > 1:
                obj.data = obj.data.copy()
            to_local = np.asarray(obj.matrix_world.inverted() @ previous_world)
            coords = positions.reshape(-1, 3) @ to_local[:3, :3].T + to_local[:3, 3]
            obj.data.vertices.foreach_set("co", coords.astype(np.float32).ravel())
            obj.data.update()
            if obj.data.has_custom_normals:
                bpy.context.view_layer.objects.active = obj
                bpy.ops.mesh.customdata_custom_splitnormals_clear()
    finally:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for obj in previous:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = active
    bpy.context.view_layer.update()
    return [obj.name for obj in meshes]


def aim_bone(rig, joint, child, direction):
    """Aim a real parent-to-child segment in world space without assuming a bone's local axis."""
    bone = rig.pose.bones[joint]
    current = rig.matrix_world.to_3x3() @ (rig.pose.bones[child].head - bone.head)
    desired = Vector(direction)
    if current.length < 1e-9 or desired.length < 1e-9:
        raise ValueError("Both the joint segment and the requested direction must have length.")
    world = rig.matrix_world @ bone.matrix
    origin = world.translation.copy()
    rotation = current.normalized().rotation_difference(desired.normalized()).to_matrix().to_4x4()
    bone.matrix = rig.matrix_world.inverted() @ Matrix.Translation(origin) @ rotation @ Matrix.Translation(-origin) @ world
    bpy.context.view_layer.update()


def reach(rig, upper, lower, end, target, pole):
    """Position a two-segment FK chain at a world-space target. Pole is a world-space point."""
    bones = rig.pose.bones
    if bones[upper] not in bones[lower].parent_recursive or bones[lower] not in bones[end].parent_recursive:
        raise ValueError("Supply a connected upper joint, lower joint and end landmark.")
    a, b, c = (rig.matrix_world @ bones[name].head for name in (upper, lower, end))
    target = Vector(target)
    direction = target - a
    distance = direction.length
    upper_length, lower_length = (b-a).length, (c-b).length
    if distance <= abs(upper_length-lower_length) or distance > upper_length+lower_length:
        raise ValueError("Target is outside this chain's reach.")
    axis = direction.normalized()
    bend = Vector(pole) - a
    bend -= axis * bend.dot(axis)
    if bend.length < 1e-8:
        raise ValueError("Place the pole away from the target axis.")
    along = (upper_length**2 - lower_length**2 + distance**2) / (2*distance)
    middle = a + axis*along + bend.normalized()*math.sqrt(max(0, upper_length**2 - along**2))
    aim_bone(rig, upper, lower, middle-a)
    aim_bone(rig, lower, end, target-(rig.matrix_world @ bones[lower].head))
    return tuple(rig.matrix_world @ bones[end].head)
