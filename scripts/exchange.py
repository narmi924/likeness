"""Export a selection from a disposable Blender process."""

from pathlib import Path

import bpy
from mathutils import Matrix

from modeling.parts import check_topology


def select(objects):
    bpy.ops.object.select_all(action="DESELECT")
    layer = bpy.context.view_layer
    for o in objects:
        if o.name in layer.objects:
            o.hide_set(False)
            o.select_set(True)


def stl_meshes(focus):
    layer = bpy.context.view_layer
    deps = bpy.context.evaluated_depsgraph_get()
    copies = []
    for inst in deps.object_instances:
        obj = inst.object
        if obj.hide_render or obj.type not in {"MESH", "CURVE", "FONT", "SURFACE", "META"}:
            continue
        if (
            focus is not None
            and obj.original not in focus
            and not (inst.parent and inst.parent.original in focus)
        ):
            continue
        mesh = bpy.data.meshes.new_from_object(obj, depsgraph=deps)
        if not len(mesh.polygons):
            bpy.data.meshes.remove(mesh)
            continue
        copies.append((obj.name, mesh, inst.matrix_world.copy()))
    objects = []
    for name, mesh, matrix in copies:
        ob = bpy.data.objects.new(name + " print", mesh)
        bpy.context.scene.collection.objects.link(ob)
        ob.matrix_world = matrix
        objects.append(ob)
    layer.update()
    if not objects:
        raise ValueError("No printable surfaces in the selection.")
    defects = []
    for ob in objects:
        info = check_topology(ob)
        if info["non_manifold_edges"] or info["loose_verts"]:
            defects.append(
                f"{ob.name}: {info['non_manifold_edges']} non-manifold edges, {info['loose_verts']} loose vertices"
            )
    if defects:
        raise ValueError("STL requires closed manifold surfaces. " + ", ".join(defects[:10]))
    return objects


def bind_static_pose(objects):
    """Bind unanimated posed skeletons and rigid attachments in their visible pose."""
    rigs = [
        o
        for o in objects
        if o.type == "ARMATURE"
        and not o.animation_data
        and not o.data.animation_data
        and not any(b.constraints for b in o.pose.bones)
        and any(not b.matrix_basis.is_identity for b in o.pose.bones)
    ]
    for rig in rigs:
        meshes = [
            o
            for o in objects
            if o.type == "MESH" and any(m.type == "ARMATURE" and m.object == rig for m in o.modifiers)
        ]
        rigid = [
            o
            for o in objects
            if o.parent == rig
            and o.parent_type == "BONE"
            and o.type in {"MESH", "CURVE", "SURFACE", "FONT"}
            and o not in meshes
        ]
        if not (meshes or rigid) or any(
            o.animation_data or getattr(o.data, "shape_keys", None) for o in [*meshes, *rigid]
        ):
            continue
        deps = bpy.context.evaluated_depsgraph_get()
        shapes = []
        for o in [*meshes, *rigid]:
            evaluated = o.evaluated_get(deps)
            mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=deps)
            shapes.append((o, mesh, evaluated.matrix_world.copy(), o.parent_bone if o in rigid else None))
        for o in meshes:
            o.modifiers.clear()
        select({rig})
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode="POSE")
        bpy.ops.pose.armature_apply(selected=False)
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.view_layer.update()
        for old, mesh, world, bone in shapes:
            if old.type == "MESH":
                obj = old
                obj.modifiers.clear()
                obj.data = mesh
            else:
                name = old.name
                old.name = name + " source curve"
                obj = bpy.data.objects.new(name, mesh)
                for c in old.users_collection:
                    c.objects.link(obj)
                objects.discard(old)
                objects.add(obj)
            obj.parent, obj.parent_type = rig, "OBJECT"
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_world = world
            if bone:
                obj.vertex_groups.clear()
                group = obj.vertex_groups.new(name=bone)
                group.add(list(range(len(mesh.vertices))), 1.0, "REPLACE")
            obj.modifiers.new("Skin", "ARMATURE").object = rig
        bpy.context.view_layer.update()


def mesh_beveled_curves(objects):
    """Preserve the visible profile when USD would write Blender's point radii as widths."""
    deps = bpy.context.evaluated_depsgraph_get()
    samples = []
    for obj in objects:
        if obj.type not in {"CURVE", "FONT", "SURFACE"} or obj.animation_data or obj.data.animation_data:
            continue
        ev = obj.evaluated_get(deps)
        data = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=deps)
        if data.polygons:
            samples.append((obj, data, ev.matrix_world.copy()))
        else:
            bpy.data.meshes.remove(data)
    for old, data, world in samples:
        name = old.name
        old.name = name + " source curve"
        new = bpy.data.objects.new(name, data)
        for coll in old.users_collection:
            coll.objects.link(new)
        new.parent = old.parent
        new.matrix_world = world
        objects.remove(old)
        objects.add(new)
    bpy.context.view_layer.update()


def export_scene(params, subject_objects, geometry_corners, bounds):
    scene = bpy.context.scene
    path = Path(params["output"])
    ext = path.suffix.lower()
    scale = params["scale"]
    focus = subject_objects(scene, params["subject"])
    objects = set(scene.objects if focus is None else focus)
    low, high = bounds(geometry_corners(scene, params["subject"]))
    unit = scene.unit_settings.scale_length if scene.unit_settings.system != "NONE" else 1.0
    dims = list((high - low) * unit * scale)
    if ext == ".stl":
        select(stl_meshes(focus))
        done = bpy.ops.wm.stl_export(
            filepath=str(path), export_selected_objects=True, global_scale=scale * unit, apply_modifiers=False
        )
    else:
        for obj in list(objects):
            anc = obj.parent
            while anc:
                objects.add(anc)
                anc = anc.parent
            objects.update(m.object for m in obj.modifiers if m.type == "ARMATURE" and m.object)
        if scale != 1 and ext != ".fbx":
            origin = bpy.data.objects.new("Export scale", None)
            scene.collection.objects.link(origin)
            origin.scale = (scale,) * 3
            for obj in objects:
                if not obj.parent:
                    obj.parent = origin
            objects.add(origin)
        bind_static_pose(objects)
        if ext == ".usdc":
            mesh_beveled_curves(objects)
        select(objects)
        if ext == ".glb":
            done = bpy.ops.export_scene.gltf(
                filepath=str(path),
                export_format="GLB",
                use_selection=True,
                export_yup=True,
                export_apply=True,
                export_gn_mesh=any(
                    i.is_instance for i in bpy.context.evaluated_depsgraph_get().object_instances
                ),
                export_current_frame=True,
                export_rest_position_armature=True,
            )
        elif ext == ".fbx":
            done = bpy.ops.export_scene.fbx(
                filepath=str(path),
                use_selection=True,
                global_scale=scale,
                apply_scale_options="FBX_SCALE_ALL",
                bake_space_transform=True,
                use_mesh_modifiers=params["apply_modifiers"],
                path_mode="COPY",
                embed_textures=True,
            )
        else:
            done = bpy.ops.wm.usd_export(
                filepath=str(path),
                selected_objects_only=True,
                export_textures_mode="NEW",
                relative_paths=True,
                overwrite_textures=False,
            )
            textures = path.parent / "textures"
            if textures.is_dir():
                from pxr import Sdf, UsdUtils

                layer = Sdf.Layer.FindOrOpen(str(path))
                name = path.stem + "-textures"

                def rename_texture(asset):
                    rel = asset.replace("\\", "/").removeprefix("./")
                    return name + "/" + rel[9:] if rel.startswith("textures/") else asset

                UsdUtils.ModifyAssetPaths(layer, rename_texture)
                layer.Save()
                textures.rename(path.parent / name)
    if "FINISHED" not in done:
        raise RuntimeError(f"The {ext} exporter did not finish.")
    return {"dimensions_mm" if ext == ".stl" else "dimensions_m": dims, "bytes": path.stat().st_size}
