"""Evaluated geometry as numpy arrays, surfaces to cast against, and per-vertex fields.

    pos, nrm = M.world_geometry(head)                     # after modifiers and shape keys, in world space
    local, tris, tree, uv = M.surface(head, origin, axes) # a BVH in the subject's own frame with per-corner UVs
    M.write_field(head, "mask", values)                   # a float attribute other tools can read
"""
import numpy as np
import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree


def world_geometry(obj, evaluated=True):
    """(N,3) world positions and (N,3) world normals of the object's vertices as float64 arrays."""
    source = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()) if evaluated else obj
    data = source.data
    matrix = np.asarray(source.matrix_world, dtype=np.float64)
    count = len(data.vertices)
    positions = np.empty(count * 3, np.float32)
    normals = np.empty(count * 3, np.float32)
    data.vertices.foreach_get("co", positions)
    data.vertices.foreach_get("normal", normals)
    pos = positions.reshape(-1, 3).astype(np.float64) @ matrix[:3, :3].T + matrix[:3, 3]
    nrm = normals.reshape(-1, 3).astype(np.float64) @ np.linalg.inv(matrix[:3, :3])
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    return pos, nrm


def frame(origin=None, axes=None):
    """A (origin, 3x3 axes) pair; columns of axes are the local X, Y and Z directions in world space."""
    origin = np.zeros(3) if origin is None else np.asarray(origin, dtype=np.float64)
    axes = np.eye(3) if axes is None else np.asarray(axes, dtype=np.float64)
    return origin, axes


def to_local(points, origin=None, axes=None):
    origin, axes = frame(origin, axes)
    return (np.asarray(points, dtype=np.float64) - origin) @ axes


def to_world(points, origin=None, axes=None):
    origin, axes = frame(origin, axes)
    return np.asarray(points, dtype=np.float64) @ axes.T + origin


def surface(obj, origin=None, axes=None, evaluated=True):
    """Local-space vertices, (T,3) triangle vertex indices, a BVH tree and (T,3,2) UVs per triangle corner."""
    pos, _ = world_geometry(obj, evaluated)
    local = to_local(pos, origin, axes)
    source = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()) if evaluated else obj
    data = source.data
    data.calc_loop_triangles()
    tris = np.empty(len(data.loop_triangles) * 3, np.int32)
    data.loop_triangles.foreach_get("vertices", tris)
    tris = tris.reshape(-1, 3)
    tree = BVHTree.FromPolygons(local.tolist(), tris.tolist(), all_triangles=True)
    uv = None
    if data.uv_layers.active:
        loops = np.empty(len(data.loop_triangles) * 3, np.int32)
        data.loop_triangles.foreach_get("loops", loops)
        coords = np.empty(len(data.uv_layers.active.data) * 2, np.float32)
        data.uv_layers.active.data.foreach_get("uv", coords)
        uv = coords.reshape(-1, 2)[loops.reshape(-1, 3)]
    return local, tris, tree, uv


def write_field(obj, name, values, domain="POINT", kind="FLOAT"):
    """Store per-element values as a named attribute; FLOAT, FLOAT_VECTOR and FLOAT_COLOR are supported."""
    attr = obj.data.attributes.get(name)
    if attr is None or attr.domain != domain or attr.data_type != kind:
        if attr is not None:
            obj.data.attributes.remove(attr)
        attr = obj.data.attributes.new(name, kind, domain)
    key = {"FLOAT": "value", "FLOAT_VECTOR": "vector", "FLOAT_COLOR": "color"}[kind]
    attr.data.foreach_set(key, np.asarray(values, dtype=np.float32).ravel())
    return attr


def read_field(obj, name):
    attr = obj.data.attributes[name]
    key = {"FLOAT": "value", "FLOAT_VECTOR": "vector", "FLOAT_COLOR": "color", "INT": "value", "BOOLEAN": "value"}[attr.data_type]
    width = {"FLOAT_VECTOR": 3, "FLOAT_COLOR": 4}.get(attr.data_type, 1)
    values = np.empty(len(attr.data) * width, np.float32 if attr.data_type.startswith("FLOAT") else np.int32)
    attr.data.foreach_get(key, values)
    return values.reshape(-1, width) if width > 1 else values


def smoothstep(a, b, x):
    """0 below a, 1 above b, a smooth ramp between; works on arrays."""
    t = np.clip((np.asarray(x, dtype=np.float64) - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def gaussian(distance, width):
    return np.exp(-0.5 * (np.asarray(distance, dtype=np.float64) / width) ** 2)


def rbf_field(anchors, values, points, width, regularization=0.05):
    """Interpolate anchor values smoothly to points with a Gaussian radial basis; width in the anchors' units."""
    anchors = np.asarray(anchors, dtype=np.float64)
    values = np.asarray(values, dtype=np.float64)
    points = np.asarray(points, dtype=np.float64)

    def kernel(a, b):
        d = a[:, None, :] - b[None, :, :]
        return np.exp(-0.5 * (d * d).sum(2) / (width * width))

    weights = np.linalg.solve(kernel(anchors, anchors) + np.eye(len(anchors)) * regularization, values)
    return kernel(points, anchors) @ weights


def set_vertices(obj, positions, shape_key=None):
    """Write local-space positions to the mesh or to a named shape key."""
    flat = np.asarray(positions, dtype=np.float32).ravel()
    if shape_key is None:
        obj.data.vertices.foreach_set("co", flat)
        obj.data.update()
    else:
        if obj.data.shape_keys is None:
            obj.shape_key_add(name="Basis")
        key = obj.data.shape_keys.key_blocks.get(shape_key) or obj.shape_key_add(name=shape_key, from_mix=False)
        key.data.foreach_set("co", flat)
        key.value = 1.0
    bpy.context.view_layer.update()


def local_vertices(obj):
    count = len(obj.data.vertices)
    flat = np.empty(count * 3, np.float32)
    obj.data.vertices.foreach_get("co", flat)
    return flat.reshape(-1, 3).astype(np.float64)
