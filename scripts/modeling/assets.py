"""Append an editable Blender Studio base or put generated artwork on a named surface."""

from pathlib import Path

import bpy
from mathutils import Matrix, Vector

from .surfaces import pbr_material as pbr_material
from .baking import bake as bake, unwrap as unwrap
from .core import geometry_box


# The human bodies are not in the repository: they are downloaded into the library the first time they are used.
BASES_URL = "https://download.blender.org/demo/asset-bundles/human-base-meshes/human-base-meshes-bundle-v1.4.1.zip"   # Blender Studio, CC0, 51 MB
RIGGED_URL = "https://github.com/narmi924/likeness/releases/download/files/realistic_male_rigged.blend"   # the realistic male with a skeleton, 13 MB


def _library():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import home
    return home.library_root()


def bases_file():
    """Blender Studio's Human Base Meshes v1.4.1 (CC0), downloaded on first use."""
    target = _library() / "blender-studio" / "human-base-meshes-v1.4.1" / "human_base_meshes_bundle.blend"
    if not target.exists():
        import shutil, zipfile
        from setup import download
        print(f"LIKENESS downloading Human Base Meshes v1.4.1 (CC0, 51 MB) into {target.parent} ...", flush=True)
        archive = download(BASES_URL, target.parent / "bundle.zip", "Human Base Meshes")
        with zipfile.ZipFile(archive) as z:
            for name in ("human_base_meshes_bundle.blend", "blender_assets.cats.txt"):
                with z.open(f"human-base-meshes-bundle-v1.4.1/{name}") as src, open(target.parent / name, "wb") as out:
                    shutil.copyfileobj(src, out)
        archive.unlink()
        (target.parent / "source.md").write_text("- Human Base Meshes v1.4.1, Blender Studio, CC0. "
                                                 f"{BASES_URL}\n", encoding="utf-8")
    return target


def rigged_file():
    """The realistic male with a 51-bone deform skeleton, made from the CC0 base; downloaded on first use."""
    target = _library() / "likeness" / "realistic_male_rigged.blend"
    if not target.exists():
        from setup import download
        print(f"LIKENESS downloading the rigged realistic male (13 MB) into {target.parent} ...", flush=True)
        download(RIGGED_URL, target, "Realistic Male - Rigged")
    return target


def base_names():
    collections = []
    objects = []
    for library in (bases_file(), rigged_file()):
        with bpy.data.libraries.load(str(library), assets_only=True) as (catalog, _):
            collections.extend(catalog.collections)
            objects.extend(catalog.objects)
    return {"collections": sorted(set(collections)), "objects": sorted(set(objects))}


def _destination(collection):
    if collection is None:
        return bpy.context.collection
    if isinstance(collection, str):
        found = bpy.data.collections.get(collection)
        if found is None:
            found = bpy.data.collections.new(collection)
            bpy.context.collection.children.link(found)
        return found
    return collection


def append_base(name, parent=None):
    """Append only the named stock asset and its dependencies."""
    names = base_names()
    if name not in names["collections"] and name not in names["objects"]:
        raise ValueError(f"Unknown base mesh {name!r}. Inspect base_names() for exact names.")
    library = rigged_file() if name == "Realistic Male - Rigged" else bases_file()
    return append_from(library, [name], parent)[0]


def append_from(blend_path, names=None, collection=None, link=False):
    """Append or link named collections or objects. Preserve nested collections."""
    path = Path(blend_path).expanduser().resolve(strict=True)
    with bpy.data.libraries.load(str(path), link=link) as (src, dst):
        requested = [names] if isinstance(names, str) else names
        if requested is None:
            dst.collections, dst.objects = list(src.collections), list(src.objects)
        else:
            missing = set(requested) - set(src.collections) - set(src.objects)
            if missing:
                raise ValueError(f"Unknown assets in {path.name}: {sorted(missing)}")
            dst.collections = [n for n in requested if n in src.collections]
            dst.objects = [n for n in requested if n not in src.collections]
    colls = [c for c in dst.collections if c]
    children = {child for c in colls for child in c.children_recursive}
    roots = [c for c in colls if c not in children]
    parent = _destination(collection)
    for c in roots:
        if c.name not in parent.children:
            parent.children.link(c)
    members = {o for c in roots for o in c.all_objects}
    loose = [o for o in dst.objects if o and o not in members]
    for obj in loose:
        if obj.name not in parent.objects:
            parent.objects.link(obj)
    return [*roots, *loose]


def import_model(path, target_size=None, name=None, collection=None, credit=None):
    """Import an exchange file into a collection, centered on its bottom at world zero."""
    path = Path(path).expanduser().resolve(strict=True)
    operators = {
        ".glb": bpy.ops.import_scene.gltf,
        ".gltf": bpy.ops.import_scene.gltf,
        ".fbx": bpy.ops.import_scene.fbx,
        ".obj": bpy.ops.wm.obj_import,
        ".stl": bpy.ops.wm.stl_import,
        **dict.fromkeys((".usd", ".usda", ".usdc", ".usdz"), bpy.ops.wm.usd_import),
    }
    if path.suffix.lower() not in operators or (target_size is not None and target_size <= 0):
        raise ValueError("Choose a supported model file and a positive target size in meters.")
    previous = set(bpy.data.user_map())
    try:
        parent = _destination(collection)
        before = set(bpy.data.objects)
        existing = set(bpy.data.collections)
        with bpy.context.temp_override(scene=bpy.context.scene, view_layer=bpy.context.view_layer):
            result = operators[path.suffix.lower()](filepath=str(path))
        imported = set(bpy.data.objects) - before
        if "FINISHED" not in result or not imported:
            raise ValueError(f"Blender imported no objects from {path.name}.")
        scene = bpy.context.scene
        dest = bpy.data.collections.new(name or path.stem)
        parent.children.link(dest)
        new_colls = set(bpy.data.collections) - existing - {dest}
        for host in [scene.collection, *existing]:
            for child in list(host.children):
                if child in new_colls:
                    if child.name not in dest.children:
                        dest.children.link(child)
                    host.children.unlink(child)
        for obj in imported:
            for host in list(obj.users_collection):
                if host not in new_colls:
                    if obj.name not in dest.objects:
                        dest.objects.link(obj)
                    host.objects.unlink(obj)
        bpy.context.view_layer.update()
        points, cache = [], {}
        for i in bpy.context.evaluated_depsgraph_get().object_instances:
            if i.object.type not in {"MESH", "CURVE", "SURFACE", "FONT", "META", "CURVES", "POINTCLOUD"}:
                continue
            if i.object.original in imported or (i.parent and i.parent.original in imported):
                points.extend(geometry_box(i.object, i.matrix_world, cache))
        if not points:
            raise ValueError(f"No visible geometry in {path.name}.")
        low, high = (Vector([fn(p[i] for p in points) for i in range(3)]) for fn in (min, max))
        size = high - low
        unit = scene.unit_settings.scale_length if scene.unit_settings.system != "NONE" else 1.0
        if target_size and max(size) <= 0:
            raise ValueError("Cannot size geometry with zero extent.")
        factor = target_size / (max(size) * unit) if target_size else 1.0
        bottom = Vector(((low.x + high.x) / 2, (low.y + high.y) / 2, low.z))
        adjust = Matrix.Scale(factor, 4) @ Matrix.Translation(-bottom)
        origin = bpy.data.objects.new(dest.name + " origin", None)
        dest.objects.link(origin)
        visible = set(dest.all_objects) & imported
        static = all(
            o.type in {"EMPTY", "MESH"}
            and not o.animation_data
            and not o.constraints
            and not o.modifiers
            and not getattr(o.data, "shape_keys", None)
            and not o.instance_collection
            for o in visible
        )
        if static:
            worlds = {o: adjust @ o.matrix_world for o in visible}
            ordered = [o for o in visible if o.parent not in visible]
            for o in ordered:
                ordered.extend(child for child in o.children if child in visible)
                world = worlds[o]
                rigid = Matrix.LocRotScale(world.translation, world.to_quaternion(), Vector((1, 1, 1)))
                if o.type == "MESH":
                    if o.data.users > 1:
                        o.data = o.data.copy()
                    baked = rigid.inverted() @ world
                    o.data.transform(baked, shape_keys=True)
                    if baked.to_3x3().determinant() < 0:
                        o.data.flip_normals()
                if o.parent not in visible:
                    o.parent = origin
                o.matrix_parent_inverse = Matrix.Identity(4)
                o.matrix_world = rigid
                bpy.context.view_layer.update()
        else:
            # Parent inverse carries placement without rewriting bind matrices or animated channels.
            for o in visible:
                if o.parent not in visible:
                    o.parent = origin
                    o.matrix_parent_inverse = adjust
        if credit:
            dest["credit"] = {k: str(credit[k]) for k in ("name", "author", "license", "url")}
        bpy.context.view_layer.update()
        materials = {slot.material for o in imported for slot in o.material_slots if slot.material}
        return {
            "objects": sorted(imported, key=lambda o: o.name),
            "collection": dest,
            "origin": origin,
            "materials": len(materials),
            "faces": sum(len(o.data.polygons) for o in imported if o.type == "MESH"),
            "dimensions": list(size * factor * unit),
        }
    except BaseException:
        bpy.data.batch_remove(set(bpy.data.user_map()) - previous)
        raise


def apply_color_art(obj, slot_index, image_path, uv_name, mask_path=None, shader_name=None,
                    *, center_uv=None, size_uv=None, angle=0.0):
    """Overlay a UV atlas or a local stamp. The optional mask stays in the material's original UVs."""
    if obj.type != "MESH" or uv_name not in obj.data.uv_layers:
        raise ValueError(f"Select an existing UV map on the target mesh: {uv_name}")
    if (center_uv is None) != (size_uv is None):
        raise ValueError("Specify both the stamp center and its width/height in UV units.")
    if size_uv is not None and (len(size_uv) != 2 or len(center_uv) != 2 or min(size_uv) <= 0):
        raise ValueError("Specify two coordinates and two positive UV dimensions.")
    if not 0 <= slot_index < len(obj.material_slots):
        raise ValueError("Choose an existing material slot.")
    slot = obj.material_slots[slot_index]
    base = slot.material
    if not base or not base.node_tree:
        raise ValueError("The selected slot needs a node material.")
    shaders = [n for n in base.node_tree.nodes if n.type == "BSDF_PRINCIPLED"]
    shader = base.node_tree.nodes.get(shader_name) if shader_name else (shaders[0] if len(shaders) == 1 else None)
    if shader is None or shader.type != "BSDF_PRINCIPLED":
        raise ValueError("Name the Principled BSDF that should receive the artwork.")
    artwork = bpy.data.images.load(str(Path(image_path).resolve(strict=True)), check_existing=True)
    mask = None
    if mask_path:
        mask = bpy.data.images.load(str(Path(mask_path).resolve(strict=True)), check_existing=False)
        mask.colorspace_settings.name = "Non-Color"

    # Object-linked slots isolate the edit even when multiple objects share the mesh.
    mat = base.copy()
    mat.name = f"{obj.name} artwork ({base.name})"
    tree = mat.node_tree
    color = tree.nodes[shader.name].inputs["Base Color"]
    uv = tree.nodes.new("ShaderNodeUVMap")
    uv.uv_map = uv_name
    uv.location = (-1220, 190)
    artwork_uv = uv.outputs["UV"]
    if center_uv is not None:
        shift = tree.nodes.new("ShaderNodeVectorMath")
        shift.operation = "SUBTRACT"
        shift.inputs[1].default_value = (*center_uv, 0)
        shift.location, shift.label = (-1000, 220), "Stamp center"
        tree.links.new(artwork_uv, shift.inputs[0])
        rotate = tree.nodes.new("ShaderNodeVectorRotate")
        rotate.rotation_type = "Z_AXIS"
        rotate.inputs["Angle"].default_value = -angle
        rotate.location = (-800, 220)
        tree.links.new(shift.outputs["Vector"], rotate.inputs["Vector"])
        scale = tree.nodes.new("ShaderNodeVectorMath")
        scale.operation = "MULTIPLY"
        scale.inputs[1].default_value = (1/size_uv[0], 1/size_uv[1], 1)
        scale.location, scale.label = (-590, 220), "Stamp size"
        tree.links.new(rotate.outputs["Vector"], scale.inputs[0])
        offset = tree.nodes.new("ShaderNodeVectorMath")
        offset.operation = "ADD"
        offset.inputs[1].default_value = (.5, .5, 0)
        offset.location = (-380, 220)
        tree.links.new(scale.outputs["Vector"], offset.inputs[0])
        artwork_uv = offset.outputs["Vector"]
    paint = tree.nodes.new("ShaderNodeTexImage")
    paint.label, paint.image, paint.extension = "Artwork", artwork, "CLIP"
    shader_node = tree.nodes[shader.name]
    paint.location = (shader_node.location.x-610, shader_node.location.y+200)
    tree.links.new(artwork_uv, paint.inputs["Vector"])
    alpha = paint.outputs["Alpha"]
    if mask:
        stencil = tree.nodes.new("ShaderNodeTexImage")
        stencil.label, stencil.image, stencil.extension = "Paint mask", mask, "CLIP"
        stencil.location = (paint.location.x, paint.location.y-320)
        tree.links.new(uv.outputs["UV"], stencil.inputs["Vector"])
        multiply = tree.nodes.new("ShaderNodeMath")
        multiply.operation = "MULTIPLY"
        multiply.location = (paint.location.x+260, paint.location.y-240)
        tree.links.new(alpha, multiply.inputs[0])
        tree.links.new(stencil.outputs["Color"], multiply.inputs[1])
        alpha = multiply.outputs[0]
    mix = tree.nodes.new("ShaderNodeMixRGB")
    mix.label = "Artwork over original color"
    mix.location = (shader_node.location.x-215, shader_node.location.y+95)
    mix.inputs[1].default_value = color.default_value
    if color.is_linked:
        tree.links.new(color.links[0].from_socket, mix.inputs[1])
    tree.links.new(alpha, mix.inputs[0])
    tree.links.new(paint.outputs["Color"], mix.inputs[2])
    tree.links.new(mix.outputs[0], color)
    slot.link, slot.material = "OBJECT", mat
    return mat
