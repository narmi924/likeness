"""Project aligned photographs onto visible surfaces of a static mesh in Blender."""
import bpy
import numpy as np
from mathutils import Vector
from modeling.baking import unwrap


def _pixels(image):
    w, h = image.size
    pixels = np.empty(w*h*4, np.float32)
    image.pixels.foreach_get(pixels)
    return pixels.reshape(h, w, 4)


def _footprint(uv, size):
    """Pixel centers inside a UV triangle, and their barycentric weights."""
    a, b, c = uv * size
    low = np.maximum(0, np.ceil(np.minimum(np.minimum(a,b),c) - 0.5).astype(int))
    high = np.minimum(size-1, np.floor(np.maximum(np.maximum(a,b),c) - 0.5).astype(int))
    matrix = np.column_stack((b-a, c-a))
    if np.any(high < low) or abs(np.linalg.det(matrix)) < 1e-8:
        return None
    y, x = np.mgrid[low[1]:high[1]+1, low[0]:high[0]+1]
    points = np.column_stack((x.ravel(), y.ravel())) + 0.5
    bc = (points-a) @ np.linalg.inv(matrix).T
    weights = np.column_stack((1-bc.sum(axis=1), bc))
    inside = (weights >= -1e-8).all(axis=1)
    return y.ravel()[inside], x.ravel()[inside], weights[inside]


def _photo(cam, mask, graph):
    if cam.type != "CAMERA" or not cam.get("photo_size"):
        raise ValueError(f"{cam.name} needs a camera made by match-photo.")
    image = cam.data.background_images[0].image
    w, h = image.size
    pixels = _pixels(image)
    if mask:
        exclusion = bpy.data.images.load(str(mask), check_existing=False)
        try:
            if tuple(exclusion.size) != (w, h):
                raise ValueError(f"Mask for {cam.name} needs the photo dimensions.")
            pixels[:, :, 3] *= _pixels(exclusion)[:, :, 0]
        finally:
            bpy.data.images.remove(exclusion)
    projection = cam.calc_matrix_camera(graph, x=w, y=h) @ cam.matrix_world.inverted()
    return cam.name, pixels, np.array(projection), np.array(cam.matrix_world.translation)


def _apply_layer(obj, uv, image, slots):
    if not obj.material_slots:
        obj.data.materials.append(None)
    copies = {}
    for index in slots:
        slot = obj.material_slots[index]
        original = slot.material
        if original not in copies:
            mat = original.copy() if original else bpy.data.materials.new(obj.name + " surface")
            mat.use_nodes = True
            tree = mat.node_tree
            shaders = [n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"]
            if not shaders:
                raise ValueError(f"{mat.name} needs a Principled BSDF to layer photo color.")
            coords = tree.nodes.new("ShaderNodeUVMap")
            coords.uv_map = uv
            tex = tree.nodes.new("ShaderNodeTexImage")
            tex.image = image
            tree.links.new(coords.outputs["UV"], tex.inputs["Vector"])
            for shader in shaders:
                base = shader.inputs["Base Color"]
                mix = tree.nodes.new("ShaderNodeMixRGB")
                mix.label = "Visible photo color"
                mix.inputs[1].default_value = (original.diffuse_color if original and not original.use_nodes
                                               else base.default_value)
                if base.is_linked:
                    tree.links.new(base.links[0].from_socket, mix.inputs[1])
                tree.links.new(tex.outputs["Alpha"], mix.inputs[0])
                tree.links.new(tex.outputs["Color"], mix.inputs[2])
                tree.links.new(mix.outputs[0], base)
            copies[original] = mat
        slot.material = copies[original]


def project(obj, cameras, *, masks=None, materials=None, size=1024):
    """Bake an editable photo-color layer. White mask pixels allow paint; black excludes it.

    Cameras are match-photo objects. Optionally restrict painting to named materials.
    Unobserved pixels keep the base material.
    Use on static geometry, before adding topology-changing modifiers.
    """
    if obj.type != "MESH" or not cameras or size < 16:
        raise ValueError("Choose a static mesh, one or more photo cameras and a texture size of at least 16.")
    if obj.modifiers:
        raise ValueError(f"Project onto an evaluated static copy of {obj.name} with its modifiers applied.")
    if obj.data.users > 1:
        obj.data = obj.data.copy()
    scene = bpy.context.scene
    graph = bpy.context.evaluated_depsgraph_get()
    photos = [_photo(c, (masks or {}).get(c.name), graph) for c in cameras]
    mesh = obj.data
    slots = {p.material_index for p in mesh.polygons}
    if materials is not None:
        slots &= {i for i, slot in enumerate(obj.material_slots) if slot.name in materials}
        if not slots:
            raise ValueError(f"No faces on {obj.name} use the chosen materials.")
    active_uv = mesh.uv_layers.active
    render_uv = next((u.name for u in mesh.uv_layers if u.active_render), None)
    active_name = active_uv.name if active_uv else None
    uv_name = mesh.uv_layers.new(name="Photo color").name
    unwrap(obj, margin=2/size, uv_name=uv_name)
    if render_uv:
        mesh.uv_layers[render_uv].active_render = True
    if active_name:
        mesh.uv_layers.active = mesh.uv_layers[active_name]
    bpy.context.view_layer.update()
    mesh.calc_loop_triangles()
    uv = np.array([loop.uv[:] for loop in mesh.uv_layers[uv_name].data])
    evaluated = obj.evaluated_get(graph)
    vertices = np.array([evaluated.matrix_world @ v.co for v in evaluated.data.vertices])
    color = np.zeros((size, size, 4), np.float32)
    owners = np.full((size, size), -1, np.int32)
    occupied = np.zeros((size, size), bool)
    for tri in mesh.loop_triangles:
        if tri.material_index not in slots:
            continue
        footprint = _footprint(uv[list(tri.loops)], size)
        if footprint is None:
            continue
        y, x, weights = footprint
        corners = vertices[list(tri.vertices)]
        points = weights @ corners
        normal = np.cross(corners[1]-corners[0], corners[2]-corners[0])
        normal /= max(np.linalg.norm(normal), 1e-12)
        best = np.zeros(len(y))
        occupied[y,x] = True
        for index, (_, pixels, proj, origin) in enumerate(photos):
            h, w = pixels.shape[:2]
            clip = points @ proj[:,:3].T + proj[:,3]
            ndc = clip[:,:2] / np.maximum(clip[:,3:], 1e-8)
            screen = (ndc+1) * [w/2,h/2]
            delta = points-origin
            distance = np.linalg.norm(delta, axis=1)
            cosine = np.abs(delta @ normal) / np.maximum(distance, 1e-8)
            score = cosine * (w*proj[0,:3].dot(proj[0,:3])**0.5 / np.maximum(clip[:,3], 1e-8))**2
            valid = (clip[:,3] > 0) & (cosine > 0.18) & (score > best)
            valid &= ((screen >= 0.5) & (screen < [w-0.5, h-0.5])).all(axis=1)
            for i in np.flatnonzero(valid):
                px, py = np.floor(screen[i]).astype(int)
                rgba = pixels[py, px]
                if rgba[3] < 0.99:
                    continue
                direction = Vector(delta[i])
                hit, location, _, _, hit_obj, _ = scene.ray_cast(graph, Vector(origin), direction.normalized())
                if not hit or hit_obj != obj or (location-Vector(points[i])).length > max(0.0001, distance[i]*1e-5):
                    continue
                color[y[i], x[i]] = rgba
                best[i] = score[i]
                owners[y[i], x[i]] = index
    if not np.any(owners >= 0):
        raise ValueError(f"No visible photo samples on {obj.name}. Check the camera match, masks and occluders.")
    # Extend color into the UV gutter only; keep uncovered surface pixels transparent.
    for _ in range(2):
        filled = color[:,:,3] > 0
        border = np.pad(color, ((1,1),(1,1),(0,0)))
        for dy, dx in ((0,1),(1,0),(1,2),(2,1)):
            neighbor = border[dy:dy+size, dx:dx+size]
            take = ~occupied & ~filled & (neighbor[:,:,3] > 0)
            color[take] = neighbor[take]
    image = bpy.data.images.new(obj.name + " | Photo color", size, size, alpha=True)
    image.pixels.foreach_set(color.ravel())
    image.pack()
    _apply_layer(obj, uv_name, image, slots)
    return {"surface": obj.name, "image": image.name,
            "visible_texels": {p[0]: int(np.sum(owners == i)) for i, p in enumerate(photos)},
            "unobserved_texels": int(np.sum(occupied & (owners < 0)))}
