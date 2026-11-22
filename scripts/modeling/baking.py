"""Unwrap an editable mesh and bake maps from a high-resolution surface."""

from contextlib import contextmanager
import math
import os
from pathlib import Path
import re
import tempfile

import bpy
import bmesh


@contextmanager
def object_selection(active, selected):
    layer = bpy.context.view_layer
    previous = layer.objects.active
    old_mode = previous.mode if previous else "OBJECT"
    old_selection = list(bpy.context.selected_objects)
    if previous and old_mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in selected:
        obj.select_set(True)
    layer.objects.active = active
    try:
        yield
    finally:
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for obj in old_selection:
            obj.select_set(True)
        layer.objects.active = previous
        if previous and old_mode != "OBJECT":
            bpy.ops.object.mode_set(mode=old_mode)


def unwrap(obj, method="smart", margin=0.02, seams_from_sharp=True, uv_name="UVMap"):
    if (
        obj.type != "MESH"
        or not obj.data.polygons
        or method not in {"smart", "angle"}
        or not 0 <= margin < 0.5
    ):
        raise ValueError("Choose a mesh with faces, smart or angle unwrap, and a UV margin below 0.5.")
    with object_selection(obj, [obj]):
        uv = obj.data.uv_layers.get(uv_name) or obj.data.uv_layers.new(name=uv_name)
        obj.data.uv_layers.active = uv
        uv.active_render = True
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        if method == "smart":
            done = bpy.ops.uv.smart_project(island_margin=margin, margin_method="FRACTION")
        else:
            if seams_from_sharp:
                bm = bmesh.from_edit_mesh(obj.data)
                for edge in bm.edges:
                    if not edge.smooth or not edge.is_manifold or edge.calc_face_angle(0) > math.radians(45):
                        edge.seam = True
                bmesh.update_edit_mesh(obj.data)
            done = bpy.ops.uv.unwrap(method="ANGLE_BASED", margin=margin, margin_method="FRACTION")
        if "FINISHED" not in done:
            raise ValueError(f"Could not unwrap {obj.name}.")
    return obj.data.uv_layers[uv_name]


def bake(high, low, maps=("normal", "ao", "diffuse"), size=2048, cage_extrusion=0.02, folder=None):
    high = [high] if isinstance(high, bpy.types.Object) else list(high)
    maps = tuple(dict.fromkeys(maps))
    if low.type != "MESH" or not low.data.uv_layers.active or not high or low in high:
        raise ValueError("Choose distinct high-resolution surfaces and a low mesh with an active UV map.")
    if not maps or set(maps) - {"normal", "ao", "diffuse"} or size < 4 or cage_extrusion < 0:
        raise ValueError(
            "Choose normal, ao or diffuse, a positive image size and a nonnegative cage extrusion."
        )
    folder = Path(
        folder or (Path(bpy.data.filepath or Path.cwd() / "untitled.blend").parent / "bakes")
    ).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w.-]+", "-", low.name).strip(".-") or "surface"
    outputs = {m: folder / (stem + "-" + m + ".png") for m in maps}
    if any(p.exists() for p in outputs.values()):
        raise FileExistsError("Choose a new folder for the bake or a new mesh name.")
    scene = bpy.context.scene
    engine, device = scene.render.engine, scene.cycles.device
    original_mesh = low.data
    slots = [(s.link, s.material) for s in low.material_slots]
    materials, images, published = [], [], []
    try:
        if not slots:
            if low.data.users > 1:
                low.data = low.data.copy()
            low.data.materials.append(None)
        local = {}
        for slot in low.material_slots:
            old = slot.material
            if old not in local:
                mat = old.copy() if old else bpy.data.materials.new(low.name + " surface")
                mat.use_nodes = True
                local[old] = mat
                materials.append(mat)
            slot.link, slot.material = "OBJECT", local[old]
        scene.render.engine = "CYCLES"
        from worker import render_device

        render_device(scene)
        with tempfile.TemporaryDirectory(dir=folder) as tmp, object_selection(low, [*high, low]):
            for index, kind in enumerate(maps):
                image = bpy.data.images.new(stem + " " + kind, size, size, alpha=False)
                images.append(image)
                if kind != "diffuse":
                    image.colorspace_settings.name = "Non-Color"
                for mat in materials:
                    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
                    node.image, node.label, node.location = image, "Baked " + kind, (-600, -300 - index * 260)
                    mat.node_tree.nodes.active = node
                opts = {
                    "type": kind.upper(),
                    "use_selected_to_active": True,
                    "cage_extrusion": cage_extrusion,
                    "margin": max(2, min(16, size // 64)),
                    "uv_layer": low.data.uv_layers.active.name,
                }
                if kind == "diffuse":
                    opts["pass_filter"] = {"COLOR"}
                done = bpy.ops.object.bake(**opts)
                if "FINISHED" not in done:
                    raise RuntimeError(f"Could not bake {kind} for {low.name}.")
                image.file_format = "PNG"
                image.filepath_raw = str(Path(tmp) / outputs[kind].name)
                image.save()
            for kind, image in zip(maps, images):
                dest = outputs[kind]
                staged = Path(tmp) / dest.name
                if os.name == "nt":
                    staged.rename(dest)
                else:
                    os.link(staged, dest)
                published.append(dest)
                image.filepath_raw = str(dest)
                image.pack()
        return {kind: str(path) for kind, path in outputs.items()}
    except BaseException:
        for path in published:
            path.unlink(missing_ok=True)
        if original_mesh != low.data:
            copy = low.data
            low.data = original_mesh
            bpy.data.meshes.remove(copy)
        elif not slots:
            low.data.materials.clear()
        for slot, (link, material) in zip(low.material_slots, slots):
            slot.link, slot.material = link, material
        for mat in materials:
            bpy.data.materials.remove(mat)
        for image in images:
            bpy.data.images.remove(image)
        raise
    finally:
        scene.render.engine, scene.cycles.device = engine, device
