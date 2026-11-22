"""Edit meshes that are already bound to an armature.

    bone = R.find_bone(rig, "DEF-spine.006")            # exact, prefix or regex
    R.attach(cap, rig, "head")                          # parent to a bone without moving it
    R.shape_from_world_delta(head, "Fit", delta_world)  # sculpt deltas measured in the posed world go into a shape key
"""
import re

import numpy as np
import bpy
from mathutils import Matrix

from .mesh import local_vertices, set_vertices


def armature_of(obj):
    """The armature deforming obj through its first Armature modifier, or None."""
    for mod in obj.modifiers:
        if mod.type == "ARMATURE" and mod.object:
            return mod.object
    return obj.parent if obj.parent and obj.parent.type == "ARMATURE" else None


def find_bone(rig, pattern):
    """A pose bone by exact name, then by prefix (glTF rigs add suffixes), then by regular expression."""
    bones = rig.pose.bones
    if pattern in bones:
        return bones[pattern]
    prefixed = [b for b in bones if b.name.startswith(pattern)]
    if len(prefixed) == 1:
        return prefixed[0]
    if prefixed:
        exact = [b for b in prefixed if re.fullmatch(re.escape(pattern) + r"[._-]?\d*", b.name)]
        if len(exact) == 1:
            return exact[0]
    matches = [b for b in bones if re.search(pattern, b.name)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"No bone matches {pattern!r} in {rig.name}.")
    raise ValueError(f"Several bones match {pattern!r}: {[b.name for b in matches][:8]}")


def bone_world(rig, pattern):
    """(pose bone, world matrix of its current pose)."""
    bone = find_bone(rig, pattern)
    return bone, rig.matrix_world @ bone.matrix


def attach(obj, rig, pattern):
    """Parent obj to a bone and keep its current world placement."""
    bone = find_bone(rig, pattern)
    world = obj.matrix_world.copy()
    obj.parent = rig
    obj.parent_type = "BONE"
    obj.parent_bone = bone.name
    bpy.context.view_layer.update()
    obj.matrix_world = world
    bpy.context.view_layer.update()
    return obj


def deform_matrices(obj, rig):
    """Per bone-index 4x4 matrices taking rest-space mesh points to the posed world, like the Armature modifier."""
    object_matrix = np.asarray(obj.matrix_world, dtype=np.float64)
    rig_matrix = np.asarray(rig.matrix_world, dtype=np.float64)
    result = {}
    for group in obj.vertex_groups:
        bone = rig.pose.bones.get(group.name)
        if bone is None or not bone.bone.use_deform:
            continue
        posed = rig_matrix @ np.asarray(bone.matrix) @ np.linalg.inv(np.asarray(bone.bone.matrix_local))
        result[group.index] = posed @ np.linalg.inv(rig_matrix) @ object_matrix
    return result


def skin_matrices(obj, rig):
    """(N,4,4) blended deform matrices per vertex; vertices without deforming weights use the object matrix."""
    matrices = deform_matrices(obj, rig)
    object_matrix = np.asarray(obj.matrix_world, dtype=np.float64)
    result = np.tile(object_matrix, (len(obj.data.vertices), 1, 1))
    for v in obj.data.vertices:
        total = 0.0
        blend = np.zeros((4, 4))
        for g in v.groups:
            m = matrices.get(g.group)
            if m is not None and g.weight > 1e-6:
                blend += g.weight * m
                total += g.weight
        if total > 1e-6:
            result[v.index] = blend / total
    return result


def shape_from_world_delta(obj, name, delta, rig=None):
    """Turn world-space vertex displacements measured on the posed mesh into a rest-space shape key."""
    rig = rig or armature_of(obj)
    delta = np.asarray(delta, dtype=np.float64)
    base = local_vertices(obj)
    if rig is None:
        inverse = np.linalg.inv(np.asarray(obj.matrix_world)[:3, :3])
        local = delta @ inverse.T
    else:
        linear = skin_matrices(obj, rig)[:, :3, :3]
        local = np.zeros_like(base)
        moving = np.flatnonzero((delta * delta).sum(1) > 1e-18)
        if len(moving):
            local[moving] = np.linalg.solve(linear[moving], delta[moving][:, :, None])[:, :, 0]
    set_vertices(obj, base + local, shape_key=name)
    return obj.data.shape_keys.key_blocks[name]


def posed_to_rest(obj, rig, world_points, weights):
    """Rest-space positions for world points that should follow the given per-point bone weights.

    weights: (N, K) array over the vertex groups named in `groups`."""
    raise NotImplementedError("Use deform_matrices and solve per point; see shape_from_world_delta.")
