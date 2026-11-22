"""Fit a head to photographs: cameras that match photos, landmark errors in pixels, and landmark-driven shape keys.

The workspace-side `photofit.py` detects landmarks and solves the camera; these helpers run inside Blender.

    cam = F.photo_camera(scene, "Photo | front", matrix, lens_mm, photo, (w, h), landmarks_px)
    F.store_landmarks(head, {"nose tip": 1234, ...})            # landmark name → vertex index on the head mesh
    F.landmark_error(scene, cam, head)                           # RMS pixels between photo and projected mesh
    key = F.fit_to_photos(head, [cam_front, cam_side], width=0.02)
"""
import json
import math

import numpy as np
import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector

from . import mesh as M
from . import rigged

LANDMARKS = "face_landmarks"
PHOTO = "photo"
PHOTO_SIZE = "photo_size"
PHOTO_POINTS = "photo_landmarks"


def photo_camera(scene, name, matrix, lens_mm, photo_path, size, landmarks_px, sensor_width=36.0, error_px=None):
    """A camera that reproduces a photograph; the photo shows as its background and in the viewport."""
    data = bpy.data.cameras.new(name)
    data.type = "PERSP"
    data.sensor_fit = "HORIZONTAL"
    data.sensor_width = sensor_width
    data.lens = lens_mm
    data.clip_start, data.clip_end = 0.01, 100
    data.show_background_images = True
    image = bpy.data.images.load(str(photo_path), check_existing=True)
    background = data.background_images.new()
    background.image = image
    background.alpha = 0.5
    background.display_depth = "FRONT"
    cam = bpy.data.objects.new(name, data)
    scene.collection.objects.link(cam)
    cam.matrix_world = Matrix(matrix)
    cam[PHOTO] = str(photo_path)
    cam[PHOTO_SIZE] = [int(size[0]), int(size[1])]
    cam[PHOTO_POINTS] = json.dumps(landmarks_px)
    if error_px is not None:
        cam["landmark_error_px"] = float(error_px)
    return cam


def photo_size(cam):
    size = cam.get(PHOTO_SIZE)
    return (int(size[0]), int(size[1])) if size else None


def photo_points(cam):
    raw = cam.get(PHOTO_POINTS)
    return json.loads(raw) if raw else {}


def store_landmarks(obj, vertex_by_name):
    obj[LANDMARKS] = json.dumps({k: int(v) for k, v in vertex_by_name.items()})


def landmarks(obj):
    raw = obj.get(LANDMARKS)
    if not raw:
        raise ValueError(f"{obj.name} carries no face landmarks. Run match-photo first.")
    return json.loads(raw)


def landmark_world(obj):
    """Current world position of every stored landmark, after modifiers and shape keys."""
    pos, _ = M.world_geometry(obj)
    return {name: pos[index] for name, index in landmarks(obj).items() if index < len(pos)}


def _with_resolution(scene, size):
    render = scene.render
    saved = (render.resolution_x, render.resolution_y, render.resolution_percentage,
             render.pixel_aspect_x, render.pixel_aspect_y)
    if size:
        render.resolution_x, render.resolution_y = size
        render.resolution_percentage = 100
        render.pixel_aspect_x = render.pixel_aspect_y = 1
    return saved


def _restore_resolution(scene, saved):
    render = scene.render
    (render.resolution_x, render.resolution_y, render.resolution_percentage,
     render.pixel_aspect_x, render.pixel_aspect_y) = saved


def project(scene, cam, points_world):
    """Pixel positions (x right, y down) of world points through a photo camera."""
    size = photo_size(cam) or (scene.render.resolution_x, scene.render.resolution_y)
    saved = _with_resolution(scene, size)
    try:
        out = {}
        for name, p in points_world.items():
            v = world_to_camera_view(scene, cam, Vector(p))
            out[name] = [v.x * size[0], (1 - v.y) * size[1]]
        return out
    finally:
        _restore_resolution(scene, saved)


def pixel_rays(scene, cam, pixels):
    """World-space (origin, unit direction) for photo pixels; needs the camera's photo size."""
    size = photo_size(cam) or (scene.render.resolution_x, scene.render.resolution_y)
    saved = _with_resolution(scene, size)
    try:
        corners = cam.data.view_frame(scene=scene)
    finally:
        _restore_resolution(scene, saved)
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    depth = corners[0].z
    matrix = cam.matrix_world
    origin = matrix.translation.copy()
    rays = {}
    for name, (px, py) in pixels.items():
        vx = min(xs) + (px / size[0]) * (max(xs) - min(xs))
        vy = max(ys) - (py / size[1]) * (max(ys) - min(ys))
        local = Vector((vx, vy, depth))
        if cam.data.type == "ORTHO":
            rays[name] = (matrix @ Vector((vx, vy, 0)), (matrix.to_3x3() @ Vector((0, 0, -1))).normalized())
        else:
            rays[name] = (origin, (matrix.to_3x3() @ local).normalized())
    return rays


def landmark_error(scene, cam, obj, names=None):
    """RMS and per-landmark pixel distance between the photograph's landmarks and the projected mesh."""
    photo = photo_points(cam)
    current = landmark_world(obj)
    shared = [n for n in (names or photo) if n in photo and n in current]
    if not shared:
        raise ValueError("No landmarks are shared between the photograph and the mesh.")
    projected = project(scene, cam, {n: current[n] for n in shared})
    errors = {n: float(math.dist(projected[n], photo[n])) for n in shared}
    rms = float(math.sqrt(sum(e * e for e in errors.values()) / len(errors)))
    return {"camera": cam.name, "rms_px": rms, "points": errors}


def landmark_targets(scene, cameras, obj, names=None):
    """Where each landmark should move so every photo sees it in the right place.

    Each camera constrains the two directions across its ray; the move along the ray is left alone,
    so one photo adjusts width and height, a second photo from the side adds depth."""
    current = landmark_world(obj)
    normal_eq = {n: (np.zeros((3, 3)), np.zeros(3)) for n in current}
    used = set()
    for cam in cameras:
        photo = photo_points(cam)
        shared = [n for n in (names or photo) if n in photo and n in current]
        rays = pixel_rays(scene, cam, {n: photo[n] for n in shared})
        for n in shared:
            origin, direction = rays[n]
            o = np.asarray(origin, dtype=np.float64)
            d = np.asarray(direction, dtype=np.float64)
            p = current[n]
            closest = o + d * np.dot(p - o, d)
            projector = np.eye(3) - np.outer(d, d)
            a, b = normal_eq[n]
            a += projector
            b += projector @ (closest - p)
            used.add(n)
    targets = {}
    for n in used:
        a, b = normal_eq[n]
        delta, *_ = np.linalg.lstsq(a, b, rcond=None)
        targets[n] = current[n] + delta
    return targets


def fit_to_photos(obj, cameras, names=None, width=None, reach=None, strength=1.0, key_name=None, scene=None):
    """Move the stored landmarks toward their photographed positions and interpolate smoothly between them.

    width: Gaussian radius of the interpolation (default: median spacing of the landmarks).
    reach: distance from the landmark cloud beyond which vertices stay still (default: 3 × width).
    The result is a shape key, converted through the armature when the mesh is bound."""
    scene = scene or bpy.context.scene
    current = landmark_world(obj)
    targets = landmark_targets(scene, cameras, obj, names)
    if len(targets) < 4:
        raise ValueError("Too few landmarks are visible in the chosen photographs.")
    anchors = np.array([current[n] for n in targets])
    moves = np.array([targets[n] - current[n] for n in targets]) * strength
    if width is None:
        d = np.linalg.norm(anchors[:, None, :] - anchors[None, :, :], axis=2)
        np.fill_diagonal(d, np.inf)
        width = float(np.median(d.min(1))) * 1.5
    reach = reach or 3 * width
    pos, _ = M.world_geometry(obj)
    distance = np.linalg.norm(pos[:, None, :] - anchors[None, :, :], axis=2).min(1)
    inside = np.flatnonzero(distance < reach)
    delta = np.zeros_like(pos)
    if len(inside):
        field = M.rbf_field(anchors, moves, pos[inside], width, regularization=0.02)
        falloff = 1 - M.smoothstep(reach * 0.6, reach, distance[inside])
        delta[inside] = field * falloff[:, None]
    name = key_name or f"Photo fit {len(obj.data.shape_keys.key_blocks) if obj.data.shape_keys else 1:02}"
    key = rigged.shape_from_world_delta(obj, name, delta)
    bpy.context.view_layer.update()
    return {"shape_key": key.name, "landmarks": len(targets), "width": width, "reach": reach,
            "max_move": float(np.linalg.norm(moves, axis=1).max())}


def shape_key_delta(source, key_name=None):
    """World-space displacement of every source vertex caused by one shape key (default: the last one)."""
    keys = source.data.shape_keys
    if not keys or len(keys.key_blocks) < 2:
        raise ValueError("The source needs the shape key produced by fit_to_photos.")
    block = keys.key_blocks[key_name] if key_name else keys.key_blocks[-1]
    value = block.value
    block.value = 0.0
    bpy.context.view_layer.update()
    before, _ = M.world_geometry(source)
    block.value = value
    bpy.context.view_layer.update()
    after, _ = M.world_geometry(source)
    return before, after - before


def transfer_delta(obj, source, key_name=None, rigid=False, neighbours=6):
    """Make a detail follow the fitted source: hair, lashes and brows deform with the nearest skin vertices;
    an eyeball or a denture (rigid=True) is translated as one piece by the skin motion around it."""
    from mathutils.kdtree import KDTree
    src_pos, world_delta = shape_key_delta(source, key_name)
    tree = KDTree(len(src_pos))
    for i, p in enumerate(src_pos):
        tree.insert(p, i)
    tree.balance()

    def motion_at(points):
        moved = np.zeros_like(points)
        for i, p in enumerate(points):
            hits = tree.find_n(p, neighbours)
            weights = np.array([1 / (h[2] + 1e-6) for h in hits])
            moved[i] = sum(w * world_delta[h[1]] for w, h in zip(weights, hits)) / weights.sum()
        return moved

    own = np.asarray(obj.matrix_world, dtype=np.float64)
    if obj.type == "CURVES":
        positions = obj.data.attributes["position"].data
        raw = np.empty(len(positions) * 3, np.float32)
        positions.foreach_get("vector", raw)
    elif obj.type == "MESH":
        raw = np.empty(len(obj.data.vertices) * 3, np.float32)
        obj.data.vertices.foreach_get("co", raw)
    else:
        rigid = True
        raw = np.zeros(3, np.float32)
    world = raw.reshape(-1, 3) @ own[:3, :3].T + own[:3, 3]
    if rigid and obj.type == "MESH" and len(obj.data.vertices):
        # Two eyeballs in one mesh move separately: each connected piece shifts by the skin motion at its centre.
        import bmesh
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        unvisited = set(range(len(bm.verts)))
        pieces = []
        while unvisited:
            seed = unvisited.pop()
            stack, piece = [seed], [seed]
            while stack:
                for edge in bm.verts[stack.pop()].link_edges:
                    other = edge.other_vert(bm.verts[piece[-1]]) if False else None
                    for v in edge.verts:
                        if v.index in unvisited:
                            unvisited.discard(v.index)
                            stack.append(v.index)
                            piece.append(v.index)
            pieces.append(np.array(piece))
        bm.free()
        moved = np.zeros_like(world)
        for piece in pieces:
            moved[piece] = motion_at(world[piece].mean(0, keepdims=True))[0]
        new_local = (world + moved - own[:3, 3]) @ np.linalg.inv(own[:3, :3]).T
        if obj.data.shape_keys:
            obj.data.shape_keys.key_blocks[0].data.foreach_set("co", new_local.astype(np.float32).ravel())
        obj.data.vertices.foreach_set("co", new_local.astype(np.float32).ravel())
        obj.data.update()
        bpy.context.view_layer.update()
        return float(np.linalg.norm(moved, axis=1).max())
    if rigid:
        centre = world.mean(0, keepdims=True) if obj.type == "CURVES" else np.asarray(obj.matrix_world.translation)[None]
        shift = motion_at(centre)[0]
        matrix = obj.matrix_world.copy()
        matrix.translation = matrix.translation + Vector(shift)
        obj.matrix_world = matrix
        bpy.context.view_layer.update()
        return float(np.linalg.norm(shift))
    moved = motion_at(world)
    new_local = (world + moved - own[:3, 3]) @ np.linalg.inv(own[:3, :3]).T
    if obj.type == "CURVES":
        positions.foreach_set("vector", new_local.astype(np.float32).ravel())
    else:
        if obj.data.shape_keys:
            block = obj.data.shape_keys.key_blocks[0]
            block.data.foreach_set("co", new_local.astype(np.float32).ravel())
        obj.data.vertices.foreach_set("co", new_local.astype(np.float32).ravel())
        obj.data.update()
    bpy.context.view_layer.update()
    return float(np.linalg.norm(moved, axis=1).max())
