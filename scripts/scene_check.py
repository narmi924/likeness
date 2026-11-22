"""Inspect authored objects and evaluated surfaces without changing them."""

import re
from pathlib import Path

import bpy

from modeling.parts import check_topology
from worker import build_log_lines, scene_cameras


def inspect_images():
    missing, unpacked = [], []
    for image in bpy.data.images:
        if not image.users or image.source not in {"FILE", "TILED", "SEQUENCE", "MOVIE"}:
            continue
        if image.packed_files:
            continue
        unpacked.append(image.name)
        path = bpy.path.abspath(image.filepath, library=image.library)
        paths = (
            [path.replace("<UDIM>", str(t.number)) for t in image.tiles]
            if image.source == "TILED"
            else [path]
        )
        for candidate in paths:
            if not Path(candidate).is_file():
                missing.append({"image": image.name, "path": candidate})
    return {"missing": missing, "unpacked": sorted(unpacked)}


def check_scene(params, subject_objects, geometry_corners, bounds):
    scene = bpy.context.scene
    focus = subject_objects(scene, params["subject"])
    objects = sorted(scene.objects if focus is None else focus, key=lambda o: o.name)
    meshes = [o for o in objects if o.type == "MESH"]
    rows = [check_topology(o, orientation=True) for o in meshes]
    columns = ("faces", "non_manifold_edges", "loose_verts", "ngons", "inward_faces_estimate")
    rank = {
        col: [{"object": r["name"], "count": r[col]} for r in sorted(rows, key=lambda v: -v[col]) if r[col]][
            :10
        ]
        for col in columns
    }
    totals = {col: sum(r[col] for r in rows) for col in (*columns, "orientation_faces_assessed")}
    totals["editable_mesh_faces_evaluated"] = totals["faces"]
    counts, cache = {}, {}
    for instance in bpy.context.evaluated_depsgraph_get().object_instances:
        obj = instance.object
        if obj.type not in {"MESH", "CURVE", "SURFACE", "FONT", "META"} or obj.hide_render:
            continue
        if (
            focus is not None
            and obj.original not in focus
            and not (instance.parent and instance.parent.original in focus)
        ):
            continue
        key = (obj.original.as_pointer(), obj.data.as_pointer())
        if key not in cache:
            if obj.type == "MESH":
                cache[key] = len(obj.data.polygons)
            else:
                mesh = obj.to_mesh()
                try:
                    cache[key] = len(mesh.polygons)
                finally:
                    obj.to_mesh_clear()
        name = obj.original.name_full
        counts[name] = counts.get(name, 0) + cache[key]
    totals["faces"] = sum(counts.values())
    rank["faces"] = [
        {"object": name, "count": n} for name, n in sorted(counts.items(), key=lambda kv: -kv[1])[:10]
    ]
    points = list(geometry_corners(scene, params["subject"]))
    box = None
    if points:
        low, high = bounds(points)
        box = {"min": list(low), "max": list(high), "dimensions": list(high - low)}
    budget = params.get("budget")
    collections = [
        c
        for c in scene.collection.children_recursive
        if focus is None or c.name == params["subject"] or any(o in focus for o in c.all_objects)
    ]
    res = {
        "scene": scene.name,
        "subject": params["subject"],
        "objects": len(objects),
        "meshes": len(meshes),
        "units": {"system": scene.unit_settings.system, "scale": scene.unit_settings.scale_length},
        "bounds": box,
        "totals": totals,
        "top": rank,
        "budget": budget,
        "over_budget": budget is not None and totals["faces"] > budget,
        "unapplied_scale": [o.name for o in objects if any(abs(s - 1) > 1e-5 for s in o.scale)],
        "negative_scale": [o.name for o in objects if min(o.scale) < 0],
        "unassigned_material": [
            o.name
            for o in meshes
            if not any(s.material for s in o.material_slots)
            or any(
                p.material_index >= len(o.material_slots) or not o.material_slots[p.material_index].material
                for p in o.data.polygons
            )
        ],
        "default_names": [
            o.name
            for o in objects
            if re.fullmatch(
                r"(?:Cube|Plane|Sphere|Cylinder|Cone|Torus|Icosphere|Suzanne|Circle|Mesh|Empty|Camera|Light|Armature)(?:\.\d+)?",
                o.name,
            )
        ],
        "empty_collections": [c.name for c in collections if not c.all_objects],
        "booleans": [
            {"object": o.name, "modifier": m.name} for o in meshes for m in o.modifiers if m.type == "BOOLEAN"
        ],
        "images": inspect_images(),
        "cameras": scene_cameras(scene),
        "armatures": [
            {"name": o.name, "bones": len(o.data.bones), "animated": bool(o.animation_data and o.animation_data.action)}
            for o in objects
            if o.type == "ARMATURE"
        ],
        "build_log": build_log_lines(),
    }
    res["summary"] = (
        f"{scene.name}: {len(meshes)} mesh objects, {totals['faces']} evaluated surface faces, "
        f"{totals['non_manifold_edges']} non-manifold edges, {len(res['unapplied_scale'])} scaled objects"
        + (", over face budget" if res["over_budget"] else "")
        + "."
    )
    return res
