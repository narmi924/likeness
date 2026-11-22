"""The parts of a walk that need the scene: run inside Blender by likeness.py.

    blender -b scene.blend --python inblender.py -- params.json result.json

params: {"action": ..., ...}
    ground   z of the named ground objects under each [x, y]
    bake     put the walk on a camera as one key a frame, mark the stops, save as a new file
    sight    is what each stop is there to show inside the picture and unhidden; does the walker pass through anything
    spots    the four corners of each clickable thing, in the world
    shoot    render frames of the baked walk into a folder; frames already there are skipped"""
import json
import math
import os
import sys
import time

import bpy
from mathutils import Euler, Vector

PARAMS, RESULT = sys.argv[sys.argv.index("--") + 1:][:2]
P = json.load(open(PARAMS, encoding="utf-8"))
scene = bpy.context.scene


def finish(value):
    json.dump(value, open(RESULT, "w", encoding="utf-8"), ensure_ascii=False)


def load_walk():
    return json.load(open(P["walk"], encoding="utf-8"))


def named(name):
    ob = bpy.data.objects.get(name)
    if ob is None:
        close = [o.name for o in bpy.data.objects if name.lower() in o.name.lower()][:6]
        raise ValueError(f"No object named {name!r}." + (f" Close names: {close}" if close else ""))
    return ob


def family(ob):
    """The object and everything parented under it."""
    out = {ob.name}
    for child in ob.children_recursive:
        out.add(child.name)
    return out


# ---------------------------------------------------------------- ground
def ground():
    dg = bpy.context.evaluated_depsgraph_get()
    grounds = [named(n).evaluated_get(dg) for n in P["objects"]]
    out, missed = [], 0
    for x, y in P["points"]:
        best = None
        for ob in grounds:
            inv = ob.matrix_world.inverted()
            a, b = inv @ Vector((x, y, 1e5)), inv @ Vector((x, y, -1e5))
            hit, loc, _, _ = ob.ray_cast(a, (b - a).normalized())
            if hit:
                z = (ob.matrix_world @ loc).z
                best = z if best is None else max(best, z)
        missed += best is None
        out.append(best)
    finish({"z": out, "missed": missed})


# ---------------------------------------------------------------- bake
def curves_of(idblock):
    ad = idblock.animation_data
    try:
        from bpy_extras import anim_utils
        bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
        if bag is not None:
            return bag.fcurves
    except Exception:
        pass
    return ad.action.fcurves


def key_every_frame(idblock, path, columns):
    """One key a frame on idblock.path; columns is a list of value lists, one per array index."""
    count = len(columns[0])
    idblock.keyframe_insert(path, frame=0)
    for curve in [c for c in curves_of(idblock) if c.data_path == path]:
        values = columns[curve.array_index]
        points = curve.keyframe_points
        points.add(count - len(points))
        flat = [0.0] * (2 * count)
        flat[0::2] = [float(i) for i in range(count)]
        flat[1::2] = values
        points.foreach_set("co", flat)
        for point in points:
            point.interpolation = "LINEAR"
        curve.update()


def bake():
    walk = load_walk()
    frames = walk["frames"]
    name = P.get("camera", "Walk")
    old = bpy.data.objects.get(name)
    before = scene.camera.data if scene.camera and scene.camera.type == "CAMERA" else None
    if old:
        bpy.data.objects.remove(old)
    data = bpy.data.cameras.new(name)
    data.sensor_width = walk["sensor"]
    data.clip_start = before.clip_start if before else 0.1
    data.clip_end = max(before.clip_end if before else 0.0, 30000.0)
    cam = bpy.data.objects.new(name, data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.rotation_mode = "XYZ"
    key_every_frame(cam, "location", [[f["pos"][i] for f in frames] for i in range(3)])
    key_every_frame(cam, "rotation_euler", [[math.radians(90 + f["pitch"]) for f in frames], [0.0] * len(frames),
                                            [math.radians(f["yaw"] - 90) for f in frames]])
    key_every_frame(data, "lens", [[f["lens"] for f in frames]])
    key_every_frame(data, "shift_y", [[f.get("shift", 0.0) for f in frames]])
    if any(abs(f.get("ev", 0.0)) > 1e-6 for f in frames):
        base = scene.view_settings.exposure
        key_every_frame(scene, "view_settings.exposure", [[base + f.get("ev", 0.0) for f in frames]])
    scene.render.fps = int(round(walk["fps"]))
    scene.render.fps_base = scene.render.fps / walk["fps"]
    scene.frame_start, scene.frame_end = 0, len(frames) - 1
    for marker in list(scene.timeline_markers):
        if marker.name.startswith("stop:"):
            scene.timeline_markers.remove(marker)
    for stop in walk["stops"]:
        scene.timeline_markers.new("stop:" + stop["id"], frame=stop["frame"])
    scene["lake_walk"] = os.path.basename(P["walk"])
    scene.frame_set(0)
    bpy.ops.wm.save_as_mainfile(filepath=P["output"], relative_remap=True)
    finish({"file": P["output"], "camera": name, "frames": len(frames), "fps": walk["fps"],
            "stops": {s["id"]: s["frame"] for s in walk["stops"]}})


# ---------------------------------------------------------------- sight
def camera_axes(f):
    yaw, pitch = math.radians(f["yaw"]), math.radians(f["pitch"])
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    return Vector((cp * cy, cp * sy, sp)), Vector((sy, -cy, 0.0)), Vector((-sp * cy, -sp * sy, cp))


def project(p, f, sensor, aspect):
    """Where a world point falls in the picture: (u, v) from the upper left, 0 to 1, or None behind the camera."""
    fwd, rgt, up = camera_axes(f)
    v = Vector(p) - Vector(f["pos"])
    z = v.dot(fwd)
    if z <= 0.1:
        return None
    fx = f["lens"] / sensor
    return 0.5 + fx * v.dot(rgt) / z, 0.5 - (fx * v.dot(up) / z - f.get("shift", 0.0)) * aspect   # an upward shift lowers the picture


def samples_of(target, dg, limit=120):
    """Points on what must be seen: spread over an object's surface (its children and the copies it instances
    included), or the one point given."""
    if not isinstance(target, str):
        return [Vector(target)], None, "point"
    import numpy as np
    ob = named(target)
    members = family(ob)
    local = {}    # mesh name -> points on it in its own space, found once however many copies there are
    placed = []   # (mesh name, world matrix)
    for inst in dg.object_instances:   # the object itself, its children, and the copies any of them instance
        base = inst.parent.original.name if inst.is_instance and inst.parent else inst.object.original.name
        if base not in members or inst.object.type not in {"MESH", "CURVE", "SURFACE", "FONT", "META"}:
            continue
        key = inst.object.original.name
        if key not in local:
            try:
                mesh = inst.object.to_mesh()
            except RuntimeError:
                mesh = None
            pts = []
            if mesh and len(mesh.vertices):
                mesh.calc_loop_triangles()
                if len(mesh.loop_triangles) <= 4000:   # few, large faces: walk the surface in equal steps of area
                    tris = []
                    for tri in mesh.loop_triangles:
                        a, b, c = (mesh.vertices[i].co.copy() for i in tri.vertices)
                        area = (b - a).cross(c - a).length / 2
                        if area > 0:
                            tris.append((area, a, b, c))
                    total, run, k = sum(t[0] for t in tris), 0.0, 0
                    for area, a, b, c in tris:
                        run += area
                        while k < limit and (k + 0.5) / limit * total <= run:
                            u, v = (k * 0.61803398875) % 1.0, (k * 0.75487766625) % 1.0
                            if u + v > 1:
                                u, v = 1 - u, 1 - v
                            pts.append(a + (b - a) * u + (c - a) * v)
                            k += 1
                else:                                   # a dense scan: its vertices are spread evenly enough
                    co = np.empty(len(mesh.vertices) * 3, np.float32)
                    mesh.vertices.foreach_get("co", co)
                    co = co.reshape(-1, 3)
                    pts = [Vector(v) for v in co[::max(1, len(co) // limit)][:limit].tolist()]
            if mesh:
                inst.object.to_mesh_clear()
            local[key] = pts
        placed.append((key, inst.matrix_world.copy()))
    count = sum(len(local[k]) for k, _ in placed)
    if not count:
        pts = [ob.matrix_world @ Vector(c) for c in ob.bound_box] if ob.type != "EMPTY" else [ob.matrix_world.translation.copy()]
        return pts, members, target
    step = max(1, count // limit)   # the same share of every copy
    pts, n = [], 0
    for key, matrix in placed:
        for p in local[key]:
            if n % step == 0:
                pts.append(matrix @ p)
            n += 1
    return pts, members, target


def sight():
    walk = load_walk()
    frames, fps, n = walk["frames"], walk["fps"], len(walk["frames"])
    aspect = scene.render.resolution_x / scene.render.resolution_y
    dg = bpy.context.evaluated_depsgraph_get()
    sensor = walk["sensor"]
    # A ray that meets a copy reports the model it is a copy of. This table gives back the placed copy's own name.
    copies = {}
    for inst in dg.object_instances:
        if inst.is_instance and inst.parent:
            copies[(inst.object.original.name, tuple(round(v, 2) for v in inst.matrix_world.translation))] = inst.parent.original.name

    def owner(ob, matrix):
        return copies.get((ob.name, tuple(round(v, 2) for v in matrix.translation)), ob.name)
    pick = (lambda i: i % n) if walk.get("loop") else (lambda i: max(0, min(n - 1, i)))
    report = {"stops": [], "walk": {}, "problems": []}

    for stop in walk["stops"]:
        wanted = (P.get("must_see") or walk.get("must_see") or {}).get(stop["id"])
        if not wanted:
            continue
        entry = {"stop": stop["id"], "frame": stop["frame"], "targets": []}
        for target in wanted:
            pts, own, label = samples_of(target, dg)
            item = {"target": label, "around": {}}
            for seconds in (-2, -1, 0, 1, 2):
                f = frames[pick(stop["frame"] + int(round(seconds * fps)))]
                eye = Vector(f["pos"])
                inside, clear, blockers, us, vs = 0, 0, {}, [], []
                for p in pts:
                    uv = project(p, f, sensor, aspect)
                    if uv is None:
                        continue
                    us.append(uv[0]); vs.append(uv[1])
                    if not (0.03 < uv[0] < 0.97 and 0.03 < uv[1] < 0.97):
                        continue
                    inside += 1
                    d = p - eye
                    hit, _, _, _, ob, matrix = scene.ray_cast(dg, eye, d.normalized(), distance=max(0.0, d.length - 0.05))
                    by = owner(ob, matrix) if hit else None
                    if not hit or (own and by in own):
                        clear += 1
                    else:
                        blockers[by] = blockers.get(by, 0) + 1
                share = 0.0
                if us:
                    share = max(0.0, min(1.0, max(us)) - max(0.0, min(us))) * max(0.0, min(1.0, max(vs)) - max(0.0, min(vs)))
                t = {"in_picture": round(inside / len(pts), 2), "unhidden": round(clear / max(1, inside), 2),
                     "picture_share": round(share, 3), "hidden_by": sorted(blockers, key=blockers.get, reverse=True)[:3]}
                if seconds == 0:
                    item["at_stop"] = t
                    # Cut off and small is a miss. Cut off but filling much of the picture (a tower, a far shore) is a choice.
                    if t["in_picture"] < 0.9 and t["picture_share"] < 0.15:
                        report["problems"].append(f"{stop['id']}: {label} is only {t['in_picture']:.0%} inside the picture at the stop.")
                    if t["in_picture"] > 0 and t["unhidden"] < 0.6:
                        report["problems"].append(f"{stop['id']}: {label} is {1 - t['unhidden']:.0%} hidden at the stop, mostly by {t['hidden_by'][:2]}.")
                    if isinstance(target, str) and t["picture_share"] < 0.01:
                        report["problems"].append(f"{stop['id']}: {label} fills {t['picture_share']:.1%} of the picture, too small to notice.")
                else:   # [inside the picture, unhidden] a second or two before and after the stop
                    item["around"][f"{seconds:+d}s"] = [t["in_picture"], t["unhidden"]]
            entry["targets"].append(item)
        report["stops"].append(entry)

    # along the whole walk: height of the eye over the ground, walking through things, something right at the lens
    step = max(1, int(round(fps / 2)))
    heights, through, crowding = [], {}, {}
    for i in range(0, n, step):
        f = frames[i]
        eye = Vector(f["pos"])
        hit, loc, _, _, ob, _ = scene.ray_cast(dg, eye, Vector((0, 0, -1)), distance=50.0)
        if hit:
            heights.append((round(eye.z - loc.z, 2), i))
        nxt = Vector(frames[pick(i + step)]["pos"])
        d = nxt - eye
        if d.length > 0.02 and (walk.get("loop") or i + step < n):
            hit, _, _, _, ob, matrix = scene.ray_cast(dg, eye, d.normalized(), distance=d.length)
            if hit:
                through.setdefault(owner(ob, matrix), []).append(i)
        fwd, rgt, up = camera_axes(f)
        half = 0.5 * sensor / f["lens"]
        for a, b in ((0, 0), (-1, -1), (1, -1), (-1, 1), (1, 1)):
            ray = (fwd + rgt * (a * half * 0.8) + up * (b * half * 0.8 / aspect)).normalized()
            hit, _, _, _, ob, matrix = scene.ray_cast(dg, eye, ray, distance=0.6)
            if hit:
                crowding.setdefault(owner(ob, matrix), []).append(i)
                break
    if heights:
        low, high = min(heights), max(heights)
        report["walk"]["eye_height"] = {"lowest": low[0], "at_frame": low[1], "highest": high[0], "at_frame_": high[1]}
        if low[0] < 1.0 or high[0] > 2.4:
            report["problems"].append(f"The eye is between {low[0]} m and {high[0]} m above the ground (frames {low[1]} and {high[1]}); a walker's is about 1.6 m.")
    else:
        report["problems"].append("There is no ground under the walk.")
    for name, at in through.items():
        report["problems"].append(f"The walk passes through {name} near frame {at[0]}" + (f" to {at[-1]}." if len(at) > 1 else "."))
    for name, at in crowding.items():
        report["problems"].append(f"{name} is within 0.6 m of the lens near frame {at[0]}" + (f" to {at[-1]}." if len(at) > 1 else "."))
    report["walk"]["checked_every"] = step
    finish(report)


# ---------------------------------------------------------------- spots
def spots():
    walk = load_walk() if P.get("walk") else {"spots": []}
    dg = bpy.context.evaluated_depsgraph_get()
    out = []
    for spot in P.get("spots") or walk.get("spots") or []:
        ob = named(spot["object"])
        ev = ob.evaluated_get(dg)
        box = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
        centre = sum(box, Vector()) / 8
        m = ob.matrix_world.to_3x3()
        axes = [m.col[i].normalized() for i in range(3)]
        size = [ob.dimensions[i] for i in range(3)]
        thin = min(range(3), key=lambda i: size[i])
        normal = axes[thin]
        try:   # which way the face looks: the side its polygons face
            mesh = ev.to_mesh()
            total = Vector()
            for poly in mesh.polygons:
                total += (m @ poly.normal) * poly.area
            ev.to_mesh_clear()
            if total.length > 1e-6 and total.normalized().dot(normal) < 0:
                normal = -normal
        except RuntimeError:
            pass
        # Its front is the side the walker passes: the camera position that comes closest decides. "facing": [x, y, z]
        # in the route names another side.
        seen_from = None
        if "facing" in spot:
            seen_from = Vector(spot["facing"])
        elif walk.get("frames"):
            seen_from = Vector(min(walk["frames"][::5], key=lambda f: (Vector(f["pos"]) - centre).length)["pos"])
        if seen_from is not None and (seen_from - centre).dot(normal) < 0:
            normal = -normal
        others = [i for i in range(3) if i != thin]
        up = max((axes[i] for i in others), key=lambda a: abs(a.z))
        up = up if up.z >= 0 else -up
        if abs(up.z) < 0.3:   # lying flat: "up" is the longer side
            up = axes[max(others, key=lambda i: size[i])]
        up_i = max(others, key=lambda i: abs(axes[i].dot(up)))
        right_i = next(i for i in others if i != up_i)
        right = up.cross(normal).normalized()
        w, h = size[right_i] / 2, size[up_i] / 2
        corners = [centre - right * w + up * h, centre + right * w + up * h, centre + right * w - up * h, centre - right * w - up * h]
        out.append({**{k: v for k, v in spot.items() if k not in ("object", "facing")}, "object": ob.name,
                    "corners": [[round(v, 4) for v in c] for c in corners]})
    finish({"spots": out})


# ---------------------------------------------------------------- shoot
def shoot():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import worker
    worker.render_device(scene)
    out = P["output"]
    os.makedirs(out, exist_ok=True)
    r = scene.render
    if P.get("width"):
        aspect = r.resolution_x / r.resolution_y
        r.resolution_x = int(P["width"]) // 2 * 2
        r.resolution_y = int(round(r.resolution_x / aspect)) // 2 * 2
    r.resolution_percentage = 100
    if P.get("samples") and r.engine == "CYCLES":
        scene.cycles.samples = int(P["samples"])
    if r.engine == "CYCLES":
        scene.cycles.use_animated_seed = False   # the same grain on every frame: what the denoiser leaves does not flicker
        r.use_persistent_data = True
    ext = P.get("ext", "png")
    r.image_settings.file_format = "JPEG" if ext == "jpg" else "PNG"
    if ext == "jpg":
        r.image_settings.quality = 92
    else:
        r.image_settings.color_mode = "RGB"
        r.image_settings.compression = 30
    if scene.camera is None:
        raise ValueError("The scene has no camera. Bake the walk first: likeness bake.")
    done, times, t_all = 0, [], time.time()
    for f in P["frames"]:
        if f < scene.frame_start or f > scene.frame_end:
            continue
        target = os.path.join(out, f"f_{f:05d}.{ext}")
        if os.path.exists(target) and not P.get("again"):
            continue
        scene.frame_set(f)
        r.filepath = target + ".part." + ext
        t = time.time()
        bpy.ops.render.render(write_still=True)
        os.replace(r.filepath, target)
        times.append(time.time() - t)
        done += 1
        print(f"FRAME {f} {times[-1]:.1f}s", flush=True)
    device = scene.cycles.device if r.engine == "CYCLES" else r.engine
    finish({"folder": out, "rendered": done, "skipped": len(P["frames"]) - done, "seconds": round(time.time() - t_all, 1),
            "first_frame_s": round(times[0], 1) if times else None,
            "later_frames_s": round(sum(times[1:]) / len(times[1:]), 1) if len(times) > 1 else None,
            "size": [r.resolution_x, r.resolution_y], "engine": r.engine, "device": device})


try:
    {"ground": ground, "bake": bake, "sight": sight, "spots": spots, "shoot": shoot}[P["action"]]()
except Exception as error:   # likeness reports it; Blender's own exit code does not carry the message
    finish({"error": f"{type(error).__name__}: {error}"})
    raise
