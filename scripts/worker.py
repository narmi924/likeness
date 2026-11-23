"""Blender-side operations. Saved projects are opened by studio.py in a separate process."""

import json
import math
import os
from pathlib import Path
import re
import runpy
import sys
import tempfile
import uuid

import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

SCRIPTS = str(Path(__file__).resolve().parent)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


def inspect_scene(name=None):
    scene = bpy.context.scene
    if name is not None:
        obj = scene.objects.get(name)
        if obj is None:
            raise ValueError(f"Object is outside the active scene: {name}")
        keys = getattr(obj.data, "shape_keys", None)
        return {
            "name": obj.name, "type": obj.type, "dimensions": list(obj.dimensions),
            "parent": obj.parent.name if obj.parent else None,
            "children": [child.name for child in obj.children],
            "collections": [item.name for item in obj.users_collection],
            "materials": [slot.name for slot in obj.material_slots],
            "modifiers": [[item.name, item.type] for item in obj.modifiers],
            "shape_keys": list(keys.key_blocks.keys()) if keys else [],
            "bones": list(obj.data.bones.keys()) if obj.type == "ARMATURE" else [],
        }
    active = bpy.context.view_layer.objects.active
    return {
        "file": bpy.data.filepath, "scene": scene.name, "frame": scene.frame_current,
        "camera": scene.camera.name if scene.camera else None,
        "cameras": scene_cameras(scene),
        "armatures": [{"name": o.name, "bones": len(o.data.bones),
                       "animated": bool(o.animation_data and o.animation_data.action)}
                      for o in scene.objects if o.type == "ARMATURE"],
        "active": active.name if active else None, "selected": [o.name for o in bpy.context.selected_objects],
        "units": [scene.unit_settings.system, scene.unit_settings.scale_length],
        "collections": [c.name for c in scene.collection.children_recursive],
        "object_count": len(scene.objects), "objects": sorted(scene.objects.keys())[:120],
        "build_log": build_log_lines(),
    }


def is_animated(obj):
    """Keyframes, drivers or constraints on the object or any parent move it over time."""
    while obj:
        data = obj.animation_data
        if data and (data.action or data.drivers) or obj.constraints:
            return True
        obj = obj.parent
    return False


def scene_cameras(scene):
    return [{"name": o.name, "type": o.data.type,
             "lens": round(o.data.lens, 2) if o.data.type == "PERSP" else None,
             "ortho_scale": round(o.data.ortho_scale, 4) if o.data.type == "ORTHO" else None,
             "animated": is_animated(o)}
            for o in scene.objects if o.type == "CAMERA"]


BUILD_LOG = "Build log"


def build_log_lines(limit=20):
    text = bpy.data.texts.get(BUILD_LOG)
    if not text:
        return []
    lines = [line.body for line in text.lines if line.body.strip()]
    return lines[-limit:]


def record_build(script, source, output):
    """Append one line per accepted edit so any later session can see what the file contains."""
    import datetime
    text = bpy.data.texts.get(BUILD_LOG) or bpy.data.texts.new(BUILD_LOG)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    line = "\t".join([stamp, script, Path(source).name if source not in {"new", ""} and not source.startswith("live") else source,
                      Path(output).name])
    body = text.as_string()
    text.from_string((body.rstrip("\n") + "\n" if body.strip() else "") + line + "\n")


def edit_scene(params):
    if params["new"]:
        # Only the explicit 'new' source starts from an empty scene.
        bpy.ops.wm.read_factory_settings(use_empty=True)
    with_script = str(Path(__file__).resolve().parent)
    sys.path.insert(0, with_script)
    # A build script may import helpers that live beside it.
    sys.path.insert(0, str(Path(params["script"]).resolve().parent))
    runpy.run_path(params["script"], run_name="__main__")
    record_build(params.get("script_label") or params["script"], params["source"],
                 params.get("final") or params["output"])
    packed = {}
    if params["pack"]:
        bpy.ops.file.pack_all()
        occupied = set()
        for image in bpy.data.images:
            if not image.users or image.library or image.source not in {"FILE", "TILED"}:
                continue
            if not image.packed_files:
                raise ValueError(f"Cannot pack image {image.name!r}: {image.filepath}")
            if image.source == "TILED" or len(image.packed_files) != 1:
                continue
            old = image.filepath.replace("\\", "/")
            if old.startswith("//") and ".." not in old.split("/") and old.casefold() not in occupied:
                portable = old
            else:
                name = re.sub(r'[^\w.-]+', "-", image.name, flags=re.UNICODE).strip(".-") or "image"
                ext = {"JPEG": "jpg", "OPEN_EXR": "exr"}.get(image.file_format, image.file_format.lower())
                if not name.lower().endswith("." + ext):
                    name += "." + ext
                portable = "//textures/" + name
                counter = 2
                while portable.casefold() in occupied:
                    portable = f"//textures/{Path(name).stem}-{counter}.{ext}"
                    counter += 1
            occupied.add(portable.casefold())
            packed[image] = portable
    same_folder = not bpy.data.filepath or Path(bpy.data.filepath).parent == Path(params["output"]).parent

    def set_unpack_paths():
        for image, path in packed.items():
            image.filepath = path
            image.packed_files[0].filepath = path

    if same_folder:
        set_unpack_paths()
    bpy.context.preferences.filepaths.save_version = 0
    result = bpy.ops.wm.save_as_mainfile(filepath=params["output"], relative_remap=True, compress=True)
    if "FINISHED" not in result:
        raise RuntimeError("Blender could not save the working copy.")
    if packed and not same_folder:
        # Blender has now rebased any unpacked dependencies into the new folder.
        set_unpack_paths()
        result = bpy.ops.wm.save_as_mainfile(filepath=params["output"], relative_remap=False, compress=True)
        if "FINISHED" not in result:
            raise RuntimeError("Blender could not save portable texture paths.")
    return {"scene": bpy.context.scene.name, "objects": len(bpy.context.scene.objects)}


GEOMETRY = {"MESH", "CURVE", "SURFACE", "CURVES", "FONT", "META", "VOLUME", "POINTCLOUD", "GREASEPENCIL"}
DIRECTIONS = {
    "three-quarter": (1, -1.6, 0.85), "front": (0, -1, 0), "right": (1, 0, 0),
    "back": (0, 1, 0), "left": (-1, 0, 0), "top": (0, 0, 1),
}


def subject_objects(scene, name):
    if not name:
        return None
    obj = scene.objects.get(name)
    if obj:
        return {obj, *obj.children_recursive}
    collections = [scene.collection, *scene.collection.children_recursive]
    collection = next((item for item in collections if item.name == name), None)
    if not collection:
        raise ValueError(f"Cannot frame object or collection: {name}")
    return set(collection.all_objects)


def geometry_corners(scene, subject, space=None):
    from modeling.core import geometry_box
    focus = subject_objects(scene, subject)
    deps = bpy.context.evaluated_depsgraph_get()
    cache = {}
    for instance in deps.object_instances:
        obj = instance.object
        if obj.type not in GEOMETRY or obj.hide_render:
            continue
        parent = instance.parent.original if instance.parent else None
        if focus is not None and obj.original not in focus and parent not in focus:
            continue
        matrix = instance.matrix_world if space is None else space @ instance.matrix_world
        yield from geometry_box(obj, matrix, cache)


def bounds(points):
    low = Vector((float("inf"),) * 3)
    high = -low
    for p in points:
        for axis in range(3):
            low[axis] = min(low[axis], p[axis])
            high[axis] = max(high[axis], p[axis])
    if low.x > high.x:
        raise ValueError("No visible geometry to frame. Inspect the scene or choose another subject.")
    return low, high


def study_bounds(scene, angle, subject):
    direction = Vector(DIRECTIONS[angle]).normalized()
    inverse = (-direction).to_track_quat("-Z", "Y").inverted()
    return bounds(geometry_corners(scene, subject, inverse.to_matrix().to_4x4()))


def study_camera(scene, angle, subject, framing=None):
    direction = Vector(DIRECTIONS[angle]).normalized()
    rotation = (-direction).to_track_quat("-Z", "Y")
    low, high = framing if framing is not None else study_bounds(scene, angle, subject)
    center = rotation @ ((low + high) * 0.5)
    size = max(high.x - low.x, high.y - low.y, 0.0001) * 1.18
    depth = max(high.z - low.z, size)
    data = bpy.data.cameras.new("Studio review")
    data.type = "ORTHO"
    data.ortho_scale = size
    data.clip_start, data.clip_end = max(depth * 0.00001, 0.000001), depth * 6
    cam = bpy.data.objects.new("Studio review", data)
    scene.collection.objects.link(cam)
    cam.location = center + direction * depth * 2
    cam.rotation_euler = rotation.to_euler()
    scene.camera = cam


def workbench(scene, identifiers=False):
    scene.render.engine = "BLENDER_WORKBENCH"
    shading = scene.display.shading
    shading.color_type = "OBJECT" if identifiers else "SINGLE"
    shading.light = "FLAT" if identifiers else "STUDIO"
    shading.single_color = (0.74, 0.74, 0.74)
    shading.show_shadows = shading.show_cavity = not identifiers
    shading.show_xray = shading.show_object_outline = False
    shading.show_specular_highlight = not identifiers
    shading.background_type = "VIEWPORT"
    shading.background_color = (0.075, 0.075, 0.075)
    scene.render.film_transparent = identifiers
    if identifiers:
        scene.display.render_aa = "OFF"
        scene.display_settings.display_device = "sRGB"
        scene.view_settings.view_transform = "Raw"
        scene.view_settings.look = "None"
        scene.view_settings.use_curve_mapping = False
        scene.view_settings.exposure, scene.view_settings.gamma = 0.0, 1.0
        scene.render.dither_intensity = 0.0


def render_png(scene, path, viewport=False):
    scene.render.filepath = path
    if viewport and bpy.ops.render.opengl.poll():
        result = bpy.ops.render.opengl(write_still=True, view_context=False)
    else:
        result = bpy.ops.render.render(write_still=True, scene=scene.name)
    if "FINISHED" not in result or not Path(path).is_file():
        raise RuntimeError("Blender could not render this view.")


ENGINES = {"eevee": "BLENDER_EEVEE", "cycles": "CYCLES", "workbench": "BLENDER_WORKBENCH"}


def render_quality(scene, params):
    """Draft previews render quickly; final uses the scene's own settings."""
    engine = params.get("engine") or "scene"
    if engine in ENGINES:
        scene.render.engine = ENGINES[engine]
    render_device(scene)
    if params.get("quality", "draft") != "draft":
        return
    if scene.render.engine == "CYCLES":
        scene.cycles.samples = 16
        scene.cycles.use_adaptive_sampling = True
        scene.cycles.use_denoising = True
        scene.cycles.denoiser = "OPENIMAGEDENOISE"
        scene.cycles.denoising_use_gpu = False
        scene.render.use_persistent_data = True
    elif scene.render.engine == "BLENDER_EEVEE":
        scene.eevee.taa_render_samples = 16


def scene_is_lit(scene):
    deps = bpy.context.evaluated_depsgraph_get()
    if any(i.object.type == "LIGHT" and not i.object.hide_render for i in deps.object_instances):
        return True
    world = scene.world
    if world is None:
        return False
    if world.use_nodes and world.node_tree:
        out = next((n for n in world.node_tree.nodes if n.type == "OUTPUT_WORLD" and n.is_active_output), None)
        return out is not None and out.inputs["Surface"].is_linked
    return max(world.color[:3]) > 0.02


def ensure_lighting(scene):
    """A neutral world and a sun from the camera's side when a scene would otherwise render black."""
    if scene_is_lit(scene):
        return None
    previous = scene.world
    world = bpy.data.worlds.new("Studio review world")
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (0.22, 0.22, 0.22, 1)
    background.inputs["Strength"].default_value = 1.0
    scene.world = world
    cam = scene.camera
    forward = (cam.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized() if cam else Vector((0, 1, 0))
    up = Vector((0, 0, 1))
    tilt = math.radians(35)
    direction = (forward * math.cos(tilt) - up * math.sin(tilt)).normalized()
    direction.rotate(Matrix.Rotation(math.radians(30), 3, "Z"))
    light = bpy.data.lights.new("Studio review sun", "SUN")
    light.energy, light.angle = 4.0, math.radians(6)
    sun = bpy.data.objects.new("Studio review sun", light)
    sun.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.collection.objects.link(sun)
    return {"world": world, "previous": previous, "sun": sun}


def release_lighting(scene, temporary):
    if not temporary:
        return
    scene.world = temporary["previous"]
    bpy.data.objects.remove(temporary["sun"])
    bpy.data.worlds.remove(temporary["world"])


def render_device(scene):
    """Cycles on the GPU when one exists (it also leaves the CPU free); CPU otherwise, keeping two cores spare.

    BLENDER_STUDIO_GPU=0 forces the CPU. BLENDER_STUDIO_THREADS sets the CPU thread count."""
    if scene.render.engine != "CYCLES":
        return
    scene.cycles.device = "CPU"
    threads = os.environ.get("BLENDER_STUDIO_THREADS")
    scene.render.threads_mode = "FIXED"
    scene.render.threads = int(threads) if threads else max(1, (os.cpu_count() or 4) - 2)
    if os.environ.get("BLENDER_STUDIO_GPU", "1") == "0":
        return
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.refresh_devices()
    available = {device.type for device in prefs.devices}
    choices = [prefs.compute_device_type, "OPTIX", "METAL", "HIP", "ONEAPI", "CUDA"]
    backend = next((kind for kind in choices if kind in available and kind not in {"CPU", "NONE"}), None)
    if backend:
        prefs.compute_device_type = backend
        for device in prefs.devices:
            device.use = device.type == backend
        scene.cycles.device = "GPU"


def estimate_render(params):
    import platform
    from statistics import mean
    from time import perf_counter

    scene = bpy.context.scene
    render_device(scene)
    frames = list(dict.fromkeys(params["frames"] or [scene.frame_current]))
    count = params["count"]
    if count < len(frames):
        raise ValueError("The job frame count must cover the sampled frames.")
    if not scene.camera and not scene.sequence_editor:
        raise ValueError("Set a render camera or provide an edited sequence first.")
    # Standalone File Output nodes can write outside the project even with write_still=False.
    groups = list(bpy.data.node_groups)
    if getattr(scene, "node_tree", None):
        groups.append(scene.node_tree)
    for tree in groups:
        for node in tree.nodes:
            if node.type == "OUTPUT_FILE":
                node.mute = True
    def timed(frame):
        start = perf_counter()
        scene.frame_set(frame)
        if "FINISHED" not in bpy.ops.render.render(write_still=False):
            raise ValueError(f"Frame {frame} did not render.")
        return perf_counter() - start

    cold = timed(frames[0])
    timings = [timed(frame) for frame in frames] if count > 1 else [cold]
    warmup = max(0.0, cold - timings[0])
    device = scene.render.engine
    if device == "CYCLES":
        device = scene.cycles.device
        if device == "GPU":
            prefs = bpy.context.preferences.addons["cycles"].preferences
            device = [d.name for d in prefs.devices if d.use and d.type == prefs.compute_device_type]
    return {"blender": bpy.app.version_string, "engine": scene.render.engine, "device": device,
            "cpu": platform.processor(), "cpu_threads_available": os.cpu_count(),
            "resolution": [v * scene.render.resolution_percentage // 100 for v in
                           (scene.render.resolution_x, scene.render.resolution_y)],
            "cold_first_frame_seconds": round(cold, 3),
            "seconds_by_frame": {f: round(t, 3) for f, t in zip(frames, timings)},
            "job_frames": count, "estimated_render_hours": round((warmup + mean(timings) * count) / 3600, 5),
            "sample_based_hours_range": [round((warmup + t * count) / 3600, 5) for t in (min(timings), max(timings))]}


def hardware():
    import platform
    import shutil
    import subprocess

    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.refresh_devices()
    result = {"blender": bpy.app.version_string, "cpu": platform.processor(), "threads": os.cpu_count(),
              "cycles_devices": sorted({(d.name, d.type) for d in prefs.devices}),
              "free_disk_gib": round(shutil.disk_usage(os.getcwd()).free / 2**30, 1),
              "disk_path": os.getcwd()}
    if os.name == "nt":
        import ctypes
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_uint32), ("load", ctypes.c_uint32)] + [
                (k, ctypes.c_uint64) for k in ("total", "available", "page", "page_free", "virtual", "virtual_free", "extended")]
        memory = MemoryStatus()
        memory.length = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            result.update(ram_gib=round(memory.total / 2**30, 1), available_ram_gib=round(memory.available / 2**30, 1))
    if shutil.which("nvidia-smi"):
        stats = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
                                "--format=csv,noheader"], capture_output=True, text=True, timeout=15)
        if stats.returncode == 0:
            result["nvidia_vram_total_free"] = stats.stdout.strip().splitlines()
    return result


def projection(scene):
    cam = scene.camera.evaluated_get(bpy.context.evaluated_depsgraph_get())
    corners = cam.data.view_frame(scene=scene)
    return {"type": cam.data.type, "matrix": [list(row) for row in cam.matrix_world.normalized()],
            "x": [min(v.x for v in corners), max(v.x for v in corners)],
            "y": [min(v.y for v in corners), max(v.y for v in corners)],
            "depth": corners[0].z, "near": cam.data.clip_start, "far": cam.data.clip_end}


def pick_ray(view, x, y):
    if not (0 <= x <= 1 and 0 <= y <= 1):
        raise ValueError("Pick coordinates must be in [0, 1], measured from the image's upper left.")
    camera = Matrix(view["matrix"])
    vx = view["x"][0] + x*(view["x"][1]-view["x"][0])
    vy = view["y"][1] - y*(view["y"][1]-view["y"][0])
    near, far = view["near"], view["far"]
    if view["type"] == "ORTHO":
        return camera @ Vector((vx, vy, -near)), (camera.to_3x3() @ Vector((0, 0, -1))).normalized(), far-near
    if view["type"] != "PERSP":
        raise ValueError("Point selection needs a perspective or orthographic camera.")
    local = Vector((vx, vy, view["depth"])).normalized()
    direction = (camera.to_3x3() @ local).normalized()
    return camera.translation + direction*near/(-local.z), direction, (far-near)/(-local.z)


def triangle_uvs(mesh, triangle, point):
    a, b, c = [mesh.vertices[i].co for i in triangle.vertices]
    ab, ac, ap = b-a, c-a, point-a
    aa, bb, cc = ab.dot(ab), ab.dot(ac), ac.dot(ac)
    det = aa*cc - bb*bb
    if abs(det) < 1e-24:
        return {}
    u, v = (cc*ap.dot(ab)-bb*ap.dot(ac))/det, (aa*ap.dot(ac)-bb*ap.dot(ab))/det
    weights = (1-u-v, u, v)
    return {layer.name: [sum(weights[j]*layer.data[loop].uv[axis] for j, loop in enumerate(triangle.loops))
                         for axis in (0, 1)] for layer in mesh.uv_layers}


def object_at_pixel(layer, point):
    import base64
    encoded, objects = layer
    with tempfile.TemporaryDirectory(prefix="blender-pick-") as folder:
        path = Path(folder) / "objects.png"
        path.write_bytes(base64.b64decode(encoded))
        image = bpy.data.images.load(str(path), check_existing=False)
        try:
            image.colorspace_settings.name = "Non-Color"
            width, height = image.size
            x = min(width - 1, int(point[0] * width))
            y = height - 1 - min(height - 1, int(point[1] * height))
            index = 4 * (y * width + x)
            r, g, b, alpha = (round(c * 255) for c in image.pixels[index:index + 4])
        finally:
            bpy.data.images.remove(image)
    target = objects.get(str((r << 16) | (g << 8) | b))
    if not alpha or target is None:
        raise ValueError("This pixel is background. Pick inside a visible mesh.")
    return target


def locate_surface(params):
    scene = bpy.context.scene
    if params["scene"] != scene.name:
        raise ValueError("The active scene differs from the selected view. Refresh the model.")
    scene.frame_set(params["frame"])
    origin, direction, distance = pick_ray(params["projection"], *params["point"])
    target = params["object"] or object_at_pixel(params["pick_layer"], params["point"])
    nearest = None
    trees = {}
    for item in bpy.context.evaluated_depsgraph_get().object_instances:
        obj = item.object
        if obj.original.name != target["name"] or obj.type != "MESH":
            continue
        lib = bpy.path.abspath(obj.original.library.filepath) if obj.original.library else None
        if lib != target.get("library"):
            continue
        data = obj.data
        if data not in trees:
            data.calc_loop_triangles()
            trees[data] = BVHTree.FromPolygons([v.co for v in data.vertices],
                                               [t.vertices for t in data.loop_triangles], all_triangles=True)
        matrix = item.matrix_world.copy()
        inverse = matrix.inverted_safe()
        local_dir = inverse.to_3x3() @ direction
        hit, normal, idx, _ = trees[data].ray_cast(inverse @ origin, local_dir.normalized(), distance*local_dir.length)
        if hit is None:
            continue
        world = matrix @ hit
        distance = (world-origin).length
        tri = data.loop_triangles[idx]
        slot_index = data.polygons[tri.polygon_index].material_index
        mat = obj.material_slots[slot_index].material if slot_index < len(obj.material_slots) else None
        nearest = {"object": target["name"], "library": lib, "frame": scene.frame_current,
                   "world": list(world), "local": list(hit),
                   "normal": list((inverse.to_3x3().transposed() @ normal).normalized()),
                   "evaluated_face": tri.polygon_index, "material_slot": slot_index,
                   "material": mat.name if mat else None, "uv": triangle_uvs(data, tri, hit),
                   "instance_matrix": [list(row) for row in matrix]}
    if nearest is None:
        raise ValueError("No mesh surface of that object was hit. Pick inside its visible silhouette.")
    return nearest


def locate_many(params):
    """Nearest mesh vertex of one object under each of several image points; None where nothing is hit."""
    scene = bpy.context.scene
    if params.get("frame") is not None:
        scene.frame_set(params["frame"])
    target = scene.objects.get(params["object"])
    if target is None or target.type != "MESH":
        raise ValueError(f"Choose a mesh object: {params['object']}")
    evaluated = target.evaluated_get(bpy.context.evaluated_depsgraph_get())
    data = evaluated.data
    data.calc_loop_triangles()
    tree = BVHTree.FromPolygons([v.co for v in data.vertices], [t.vertices for t in data.loop_triangles], all_triangles=True)
    matrix = evaluated.matrix_world.copy()
    inverse = matrix.inverted_safe()
    result = {}
    for name, point in params["points"].items():
        origin, direction, distance = pick_ray(params["projection"], *point)
        local_dir = inverse.to_3x3() @ direction
        hit, normal, index, _ = tree.ray_cast(inverse @ origin, local_dir.normalized(), distance * local_dir.length)
        if hit is None:
            result[name] = None
            continue
        tri = data.loop_triangles[index]
        vertex = min(tri.vertices, key=lambda i: (data.vertices[i].co - hit).length)
        result[name] = {"vertex": int(vertex), "world": list(matrix @ hit)}
    if len(data.vertices) != len(target.data.vertices):
        raise ValueError(f"{target.name} changes topology through its modifiers; disable subdivision before fitting.")
    return {"object": target.name, "points": result}


def pick_pass(scene, path, aliases=None):
    workbench(scene, identifiers=True)
    deps = bpy.context.evaluated_depsgraph_get()
    objects = {i.object.original for i in deps.object_instances if i.object.type in GEOMETRY}
    palette = {}
    for index, obj in enumerate(sorted(objects, key=lambda o: o.name_full), start=1):
        if index >= 1 << 24:
            raise ValueError("Select a smaller view layer for object picking.")
        color = (index * 0x9E3779) & 0xFFFFFF
        channels = [(color >> shift & 255) / 255 for shift in (16, 8, 0)]
        obj.color = (*channels, 1)
        original = (aliases or {}).get(obj, obj)
        palette[str(color)] = {
            "name": original.name, "type": obj.type,
            "library": bpy.path.abspath(original.library.filepath) if original.library else None,
            "materials": [slot.name for slot in original.material_slots if slot.material],
        }
    bpy.context.view_layer.update()
    render_png(scene, path)
    image = bpy.data.images.load(path)
    image.colorspace_settings.name = "Non-Color"
    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    rgb = np.rint(pixels.reshape(-1, 4)[:, :3] * 255).astype(np.int32)
    visible = np.unique((rgb[:, 0] << 16) | (rgb[:, 1] << 8) | rgb[:, 2])
    return {str(code): palette[str(code)] for code in visible if str(code) in palette}


def wire_overlay(scene):
    base = bpy.data.materials.new("Inspection surface")
    line = bpy.data.materials.new("Inspection edges")
    line.diffuse_color = (0.03, 0.045, 0.065, 1)
    mods = []
    for obj in scene.objects:
        if obj.type != "MESH" or obj.hide_render or len(obj.data.polygons) >= 200000:
            continue
        if not obj.data.materials:
            obj.data.materials.append(base)
        for mat in obj.data.materials:
            if mat and mat != line:
                mat.diffuse_color = (0.69, 0.73, 0.78, 1)
        if line.name not in obj.data.materials:
            obj.data.materials.append(line)
        mod = obj.modifiers.new("Inspection edges", "WIREFRAME")
        mod.use_replace, mod.use_relative_offset, mod.use_even_offset = False, True, False
        mod.thickness, mod.offset = 0.025, 1.0
        mod.material_offset = obj.data.materials.find(line.name)
        mods.append((obj, mod))
    scene.display.shading.color_type = "MATERIAL"
    return mods


def render_view(params):
    scene = bpy.context.scene
    if params["angle"] == "sheet":
        with tempfile.TemporaryDirectory(prefix="blender-sheet-") as folder:
            tiles = []
            for angle in ("front", "right", "back", "three-quarter"):
                path = Path(folder) / (angle + ".png")
                render_view(dict(params, angle=angle, output=str(path), size=params["size"] // 2))
                tiles.append(image_pixels(path))
            # Blender image buffers start at the lower left.
            sheet = np.vstack((np.hstack(tiles[2:]), np.hstack(tiles[:2])))
            size = save_pixels(params["output"], sheet)
        return {"image": params["output"], "size": size, "angle": "sheet", "scene": scene.name}
    if params["frame"] is not None:
        scene.frame_set(params["frame"])
    if params.get("camera"):
        chosen = scene.objects.get(params["camera"])
        if not chosen or chosen.type != "CAMERA":
            raise ValueError(f"Unknown scene camera: {params['camera']}")
        # Rendering evaluates the frame again and reapplies camera cuts in this preview scene.
        for marker in scene.timeline_markers:
            if marker.camera:
                marker.camera = None
        scene.camera = chosen
        # A camera that reproduces a photograph renders at the photograph's proportions.
        if chosen.get("photo_size"):
            scene.render.resolution_x, scene.render.resolution_y = [int(v) for v in chosen["photo_size"]]
            scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
    if params["angle"] == "camera":
        if not scene.camera:
            raise ValueError("The scene has no active camera. Choose a study angle.")
        if params["subject"]:
            raise ValueError("Subject framing applies only to study angles.")
        if params["pick_image"] and scene.camera.data.type not in {"PERSP", "ORTHO"}:
            raise ValueError("Object picking requires a perspective or orthographic view.")
    else:
        study_camera(scene, params["angle"], params["subject"], params.get("study_bounds",{}).get(params["angle"]))
        scene.render.resolution_x = scene.render.resolution_y = params["size"]
        scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
    scale = params["size"] / max(scene.render.resolution_x, scene.render.resolution_y)
    scene.render.resolution_x = max(1, round(scene.render.resolution_x * scale))
    scene.render.resolution_y = max(1, round(scene.render.resolution_y * scale))
    scene.render.resolution_percentage = 100
    scene.render.use_border = scene.render.use_compositing = scene.render.use_sequencer = False
    scene.render.use_multiview = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    # Match the geometric pick layer at the current frame in a selectable view.
    if params["pick_image"]:
        scene.camera.data.dof.use_dof = False
        scene.render.use_motion_blur = False
    mods = []
    if params["style"] != "render":
        workbench(scene)
        if params["style"] == "wire":
            mods = wire_overlay(scene)
        elif params["style"] == "normals":
            shading = scene.display.shading
            shading.light, shading.studio_light = "MATCAP", "check_normal+y.exr"
    temporary = None
    if params["style"] == "render":
        render_quality(scene, params)
        temporary = ensure_lighting(scene)
    viewport = params.get("window_preview") and params["style"]=="clay" and not params["pick_image"]
    try:
        render_png(scene, params["output"], viewport=viewport)
    finally:
        release_lighting(scene, temporary)
    for obj, mod in mods:
        obj.modifiers.remove(mod)
    info = {"source": params["source"], "scene": scene.name, "frame": scene.frame_current,
            "angle": params["angle"], "camera": scene.camera.name, "subject": params["subject"],
            "size": [scene.render.resolution_x, scene.render.resolution_y], "image": params["output"]}
    if params["style"] == "render":
        info["engine"] = scene.render.engine
        info["quality"] = params.get("quality", "draft")
        if temporary:
            info["lighting"] = "temporary"
    if params.get("projection"):
        info["projection"] = projection(scene)
    if params["pick_image"]:
        info["projection"] = projection(scene)
        paths = set(bpy.utils.blend_paths(absolute=True, packed=False))
        dependencies = {}
        for path in paths:
            item = Path(path)
            # Sequences, simulation folders and missing external files require a fresh view.
            if not item.is_file():
                dependencies = None
                break
            stat = item.stat()
            dependencies[str(item)] = [str(stat.st_mtime_ns), stat.st_size]
        if any(i.source in {"SEQUENCE", "TILED"} and not i.packed_files for i in bpy.data.images):
            dependencies = None
        info["dependencies"] = dependencies
        info["objects"] = pick_pass(scene, params["pick_image"], params.get("aliases"))
    return info


def live_view(params, frozen=None):
    original = bpy.context.scene
    if params["angle"] != "camera" and (params["frame"] is None or
            (params["frame"] == original.frame_current and original.frame_subframe==0)):
        angles = ("front","right","back","three-quarter") if params["angle"]=="sheet" else (params["angle"],)
        # The open window has already evaluated the artist's current pose and modifiers.
        # Camera framing only reads that geometry. Render settings remain private to the copy.
        params["study_bounds"] = {a:study_bounds(original,a,params["subject"]) for a in angles}
    before = set(bpy.data.user_map())
    layer_name = bpy.context.view_layer.name
    preferences = bpy.context.preferences.addons["cycles"].preferences
    restore_device = params["style"]=="render" and os.environ.get("BLENDER_STUDIO_GPU")=="1"
    if restore_device:
        backend = preferences.compute_device_type
        device_states = {d.id:d.use for d in preferences.devices}
    try:
        with tempfile.TemporaryDirectory(prefix="blender-live-view-") as scratch:
            snapshot = None
            # ID colors, wire modifiers and file-based point selection need independent data.
            if frozen or params["pick_image"] or params["style"] == "wire":
                snapshot = str(frozen or Path(scratch) / "view.blend")
                result = bpy.ops.wm.save_as_mainfile(filepath=snapshot, copy=True, relative_remap=True, compress=False)
                if "FINISHED" not in result:
                    raise RuntimeError("Blender could not capture the current edit.")
                with bpy.data.libraries.load(snapshot, recursive=True) as (_, target):
                    target.scenes = [original.name]
                scene = target.scenes[0]
            else:
                scene = original.copy()

            aliases, collections = {}, {}
            def pair(source, dest):
                if dest in collections:
                    return
                collections[dest] = source
                aliases.update(zip(dest.objects, source.objects, strict=True))
                for a,b in zip(source.children, dest.children, strict=True):
                    pair(a,b)
            pair(original.collection, scene.collection)
            subject = params["subject"]
            remapped = next((o.name for o, src in (*aliases.items(), *collections.items()) if src.name == subject), subject)
            camera = params.get("camera")
            if camera:
                original_camera = original.objects.get(camera)
                if original_camera is None or original_camera.type != "CAMERA":
                    raise ValueError(f"Unknown scene camera: {camera}")
                camera = next(obj.name for obj, src in aliases.items() if src == original_camera)
            layer = scene.view_layers.get(layer_name) or scene.view_layers[0]
            with bpy.context.temp_override(scene=scene, view_layer=layer):
                info = render_view(dict(params, subject=remapped, camera=camera, aliases=aliases, window_preview=True))
            info.update(scene=original.name, subject=subject, source=bpy.data.filepath or "(unsaved)")
            if snapshot and info.get("dependencies") is not None:
                info["dependencies"].pop(str(Path(snapshot).resolve()),None)
            if params["angle"] == "camera":
                info["camera"] = aliases[scene.camera].name
            return info
    finally:
        if restore_device:
            preferences.compute_device_type=backend
            for d in preferences.devices:
                if d.id in device_states: d.use=device_states[d.id]
        created = set(bpy.data.user_map()) - before
        bpy.data.batch_remove([item for item in created if not (isinstance(item, bpy.types.Image) and item.type=="RENDER_RESULT")])


def image_pixels(path, height=None):
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        image.colorspace_settings.name = "Non-Color"
        width, original_height = image.size
        if height and height != original_height:
            image.scale(max(1, round(width * height / original_height)), height)
        width, height = image.size
        pixels = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        return pixels.reshape(height, width, 4)
    finally:
        bpy.data.images.remove(image)


def save_pixels(path, pixels):
    height, width = pixels.shape[:2]
    image = bpy.data.images.new("Studio composite", width, height, alpha=True)
    try:
        image.colorspace_settings.name = "Non-Color"
        image.pixels.foreach_set(np.ascontiguousarray(pixels).ravel())
        image.file_format, image.filepath_raw = "PNG", str(path)
        image.save()
    finally:
        bpy.data.images.remove(image)
    return [width, height]


def compare_view(params):
    with tempfile.TemporaryDirectory(prefix="blender-compare-") as directory:
        rendered = Path(directory) / "view.png"
        info = render_view(dict(params, output=str(rendered)))
        right = image_pixels(rendered)
        left = image_pixels(params["reference"], height=right.shape[0])
        panels = [left, right]
        if params.get("overlay"):
            # Half photograph, half render, in the render's frame: the silhouettes either coincide or they do not.
            width = min(left.shape[1], right.shape[1])
            blend = left[:, :width] * 0.5 + right[:, :width] * 0.5
            blend[:, :, 3] = 1.0
            panels.append(blend)
        size = save_pixels(params["output"], np.concatenate(panels, axis=1))
    result = {"size": size, "left_width": left.shape[1], "right_width": right.shape[1], "panels": len(panels)}
    if params.get("camera"):
        cam = bpy.context.scene.objects.get(params["camera"])
        from modeling import fit
        if cam and fit.photo_points(cam):
            errors = []
            for obj in bpy.context.scene.objects:
                if obj.type == "MESH" and obj.get("face_landmarks"):
                    errors.append(fit.landmark_error(bpy.context.scene, cam, obj))
            if errors:
                result["landmark_error"] = errors
    return result


def uv_layout(params):
    import gpu
    gpu.init()
    obj = bpy.context.scene.objects.get(params["object"])
    if not obj or obj.type != "MESH" or not obj.data.uv_layers.active:
        raise ValueError(f"Choose a mesh with an active UV map: {params['object']}")
    bpy.ops.object.select_all(action="DESELECT")
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    with bpy.context.temp_override(active_object=obj, object=obj, selected_objects=[obj]):
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        result = bpy.ops.uv.export_layout(filepath=params["output"], mode="PNG", export_all=True,
                                          size=(params["size"], params["size"]), opacity=.18)
        bpy.ops.object.mode_set(mode="OBJECT")
    if "FINISHED" not in result:
        raise ValueError("Blender could not write this UV layout.")
    return {"object": obj.name, "uv": obj.data.uv_layers.active.name, "size": params["size"]}


def render_orbit_angles(scene, camera, poses, directory):
    """Render fixed-time EEVEE views together so Blender can reuse scene evaluation."""
    render = scene.render
    if render.engine != "BLENDER_EEVEE" or len(poses)<2:
        for i,(location,rotation) in enumerate(poses,1):
            camera.location, camera.rotation_euler = location,rotation
            render_png(scene,str(directory/f"frame-{i:04}.png"))
        return
    # Limit simultaneous image buffers on integrated graphics at large output sizes.
    batch = min(len(poses), max(1,min(8,8*1024**2 // (render.resolution_x*render.resolution_y))))
    prefix = "Studio " + uuid.uuid4().hex[:10]
    render.use_multiview,render.views_format=True,"MULTIVIEW"
    render.image_settings.views_format="INDIVIDUAL"
    for view in render.views: view.use=False
    slots = []
    for j in range(batch):
        suffix = f"_v{j}"
        view = render.views.new(prefix+suffix)
        view.camera_suffix=suffix
        if j:
            cam=bpy.data.objects.new(prefix+suffix,camera.data)
            scene.collection.objects.link(cam)
        else:
            cam=camera
            cam.name=prefix+suffix
        slots.append((view,cam))
    render.filepath=str(directory/"frame.png")
    for start in range(0,len(poses),batch):
        for j,(view,cam) in enumerate(slots):
            i=start+j
            view.use=i<len(poses)
            if view.use:
                cam.location,cam.rotation_euler=poses[i]
                view.file_suffix=f"-{i+1:04}"
        if "FINISHED" not in bpy.ops.render.render(write_still=True,scene=scene.name):
            raise RuntimeError("Blender could not render the turntable views.")


def render_orbit(params):
    scene = bpy.context.scene
    width, height = params["size"]
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
    scene.render.use_multiview = scene.render.use_border = False
    scene.render.use_compositing = scene.render.use_sequencer = False
    scene.render.use_persistent_data = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    temporary = None
    if params["style"] == "clay":
        workbench(scene)
    else:
        if params["engine"] in ENGINES:
            scene.render.engine = ENGINES[params["engine"]]
        render_device(scene)
        if params.get("quality", "draft") == "draft":
            if scene.render.engine == "CYCLES":
                scene.cycles.samples = params["samples"]
                scene.cycles.use_denoising = True
                scene.cycles.denoiser = "OPENIMAGEDENOISE"
                scene.cycles.denoising_use_gpu = False
            elif scene.render.engine == "BLENDER_EEVEE":
                scene.eevee.taa_render_samples = params["samples"]
    directory = Path(params["directory"])
    fps = params["fps"] or scene.render.fps / scene.render.fps_base
    if params["camera"]:
        cam = scene.objects.get(params["camera"])
        if not cam or cam.type != "CAMERA":
            raise ValueError(f"Unknown scene camera: {params['camera']}")
        scene.camera = cam
        start = scene.frame_start if params["frame"] is None else params["frame"]
        span = scene.frame_end - start + 1
        count = params["frames"]
        if span < 1:
            raise ValueError("The selected camera frame range is empty.")
        # Timeline camera markers must not switch away from the requested camera.
        for marker in scene.timeline_markers:
            marker.camera = None
        if params["style"] != "clay":
            temporary = ensure_lighting(scene)
        for i in range(count):
            t = min(scene.frame_end, start + i * span / count)
            scene.frame_set(math.floor(t), subframe=t-math.floor(t))
            render_png(scene, str(directory / f"frame-{i+1:04}.png"))
        release_lighting(scene, temporary)
        start = 1
    else:
        if params["frame"] is not None:
            scene.frame_set(params["frame"])
        low, high = bounds(geometry_corners(scene, params["subject"]))
        center = (low+high)*.5
        extent = high-low
        horizontal = max(math.hypot(extent.x, extent.y), .0001)
        elevation = math.radians(14)
        vertical = extent.z*math.cos(elevation)+horizontal*math.sin(elevation)
        aspect = width/height
        cam_data = bpy.data.cameras.new("Studio orbit")
        cam_data.type = "ORTHO"
        cam_data.ortho_scale = 1.12*(max(horizontal, vertical*aspect) if aspect >= 1 else max(vertical, horizontal/aspect))
        distance = max(extent.length, .001)*2.5
        cam_data.clip_start, cam_data.clip_end = max(distance*.00001, .000001), distance*2
        cam = bpy.data.objects.new("Studio orbit", cam_data)
        scene.collection.objects.link(cam)
        scene.camera = cam
        start, count = 1, params["frames"] or round(fps*6)
        poses = []
        for i in range(count):
            theta = math.tau*i/count
            axis = Vector((math.cos(elevation)*math.sin(theta), -math.cos(elevation)*math.cos(theta), math.sin(elevation)))
            poses.append((center+axis*distance,(-axis).to_track_quat("-Z","Y").to_euler()))
        if params["style"] != "clay":
            temporary = ensure_lighting(scene)
        render_orbit_angles(scene,cam,poses,directory)
        release_lighting(scene, temporary)
    return {"start": start, "frames": count, "fps": fps, "lighting": "temporary" if temporary else "scene"}


def file_revision(path):
    stat = Path(path).stat()
    return (str(stat.st_mtime_ns), stat.st_size)


def serve(params):
    """Keep this Blender open and answer read-only requests; the file is reloaded before every request."""
    import socket
    import time
    path = bpy.data.filepath
    if not path:
        raise ValueError("A session needs a saved file.")
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(2)
    server.settimeout(5)
    port = server.getsockname()[1]
    port_file = Path(params["port_file"])
    port_file.write_text(json.dumps({"port": port, "pid": os.getpid(), "file": path}), encoding="utf-8")
    last = time.time()
    try:
        while time.time() - last < params.get("idle", 1800):
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            last = time.time()
            with conn:
                conn.settimeout(600)
                raw = bytearray()
                while b"\0" not in raw:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    raw.extend(chunk)
                try:
                    request = json.loads(raw.partition(b"\0")[0] or b"{}")
                    if request.get("action") == "stop":
                        conn.sendall(json.dumps({"stopped": True}).encode() + b"\0")
                        break
                    if "FINISHED" not in bpy.ops.wm.open_mainfile(filepath=path, load_ui=False):
                        raise RuntimeError("Could not reload the session file.")
                    reply = run(request)
                except Exception as exc:  # noqa: BLE001 - every failure must reach the client
                    reply = {"error": f"{type(exc).__name__}: {exc}"}
                conn.sendall(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\0")
    finally:
        server.close()
        port_file.unlink(missing_ok=True)
    return {"stopped": True, "port": port}


def run(params):
    if params["action"] == "edit":
        return edit_scene(params)
    elif params["action"] == "inspect":
        result = inspect_scene(params["name"])
    elif params["action"] == "estimate":
        result = estimate_render(params)
    elif params["action"] == "hardware":
        result = hardware()
    elif params["action"] == "locate":
        result = locate_surface(params)
    elif params["action"] == "locate_many":
        result = locate_many(params)
    elif params["action"] == "photo_reference":
        import shutil
        cam = bpy.context.scene.objects.get(params["camera"])
        if not cam or cam.type != "CAMERA" or not cam.get("photo_size") or not cam.data.background_images:
            raise ValueError("Choose an existing match-photo camera.")
        image = cam.data.background_images[0].image
        if image.packed_file:
            Path(params["output"]).write_bytes(image.packed_file.data)
        else:
            shutil.copyfile(bpy.path.abspath(image.filepath), params["output"])
        result = {"size": list(image.size)}
    elif params["action"] == "photo_hits":
        from modeling import fit
        scene = bpy.context.scene
        cam = scene.objects[params["camera"]]
        surfaces = {scene.objects.get(name) for name in params["surfaces"]}
        if None in surfaces or any(o.type != "MESH" for o in surfaces):
            raise ValueError("Each --surface must name a mesh in the scene.")
        pixels = json.loads(Path(params["pixels"]).read_text(encoding="utf-8"))
        rays = fit.pixel_rays(scene, cam, dict(enumerate(pixels)))
        graph = bpy.context.evaluated_depsgraph_get()
        result = []
        for origin, direction in rays.values():
            hit, location, _, _, obj, _ = scene.ray_cast(graph, origin, direction)
            result.append(list(location) if hit and obj in surfaces else None)
    elif params["action"] == "landmark_world":
        from modeling import fit
        head = bpy.context.scene.objects[params["object"]]
        points = fit.landmark_world(head) if head.get(fit.LANDMARKS) else {}
        result = {"object": head.name, "points": {n: [float(v) for v in p] for n, p in points.items()}}
    elif params["action"] == "landmark_error":
        from modeling import fit
        scene = bpy.context.scene
        head = scene.objects[params["object"]]
        result = {"errors": [fit.landmark_error(scene, scene.objects[c], head) for c in params["cameras"]]}
    elif params["action"] == "orbit":
        result = render_orbit(params)
    elif params["action"] == "compare":
        result = compare_view(params)
    elif params["action"] == "uv":
        result = uv_layout(params)
    elif params["action"] == "selftest":
        from selftest import run as run_selftest
        result = run_selftest(params["quick"])
    elif params["action"] == "export":
        from exchange import export_scene
        result = export_scene(params, subject_objects, geometry_corners, bounds)
    elif params["action"] == "check":
        from scene_check import check_scene
        result = check_scene(params, subject_objects, geometry_corners, bounds)
    elif params["action"] == "serve":
        result = serve(params)
    else:
        result = render_view(params)
    return result


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    result_path, payload = args
    result = run(json.loads(payload))
    Path(result_path).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        result_path = sys.argv[sys.argv.index("--")+1]
        Path(result_path).write_text(json.dumps({"error":str(exc)}), encoding="utf-8")
