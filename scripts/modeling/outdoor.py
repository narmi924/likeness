"""Outdoor light and air, as they were set for the lakeside film: a sky picture with its own sun lamp, haze worked into
the materials, and the film's render settings.

    from modeling import outdoor
    light = outdoor.sky(hdri_path, sun_azimuth=250)        # dusk by default; preset="day" for a high sun
    outdoor.haze(light)                                    # after every material exists
    outdoor.film_settings(width=2560, samples=128)

Angles are degrees, counter-clockwise from +X (east), north is +Y."""
import math

import bpy
import numpy as np
from mathutils import Vector

LUM = np.array([0.2126, 0.7152, 0.0722], np.float32)
PRESETS = {
    # sun height, sun lamp strength, colour and angular size, the tint laid round the sun in the sky, how strongly the
    # sky lights the scene, exposure
    "dusk": dict(sun_elevation=6.0, sun_strength=9.5, sun_color=(1.0, 0.57, 0.28), sun_angle=1.5, glow=(1.5, 0.92, 0.52), strength=0.4, exposure=0.42),
    "day": dict(sun_elevation=42.0, sun_strength=4.5, sun_color=(1.0, 0.95, 0.88), sun_angle=0.6, glow=(1.0, 1.0, 1.0), strength=0.9, exposure=0.0),
    "overcast": dict(sun_elevation=50.0, sun_strength=0.0, sun_color=(1.0, 1.0, 1.0), sun_angle=10.0, glow=(1.0, 1.0, 1.0), strength=1.0, exposure=0.3),
}


def _small(image):
    copy = image.copy()
    copy.scale(720, 360)
    px = np.empty(720 * 360 * 4, np.float32)
    copy.pixels.foreach_get(px)
    bpy.data.images.remove(copy)
    return px.reshape(360, 720, 4)[..., :3]


def sky(hdri, sun_azimuth=250.0, preset="dusk", clamp=6.0, disc=45.0, look="AgX - Medium High Contrast", **changes):
    """Light the scene with a sky picture and a sun lamp that stands where the picture's sun is.

    The picture is turned so that its sun is at sun_azimuth. Its own sun is capped (clamp) so that it adds no noise; the
    lamp does the lighting and casts the shadows. disc: brightness of a sun the camera can see when it looks that way
    (it lights nothing); 0 for none. Returns what haze() needs: the direction to the sun and the horizon's colours."""
    p = dict(PRESETS[preset], **changes)
    scene = bpy.context.scene
    image = bpy.data.images.load(str(hdri), check_existing=True)
    px = _small(image)
    lum = px @ LUM
    blur = sum(np.roll(np.roll(lum, i, 0), j, 1) for i in range(-2, 3) for j in range(-2, 3))
    blur[:180] = 0   # rows run from the bottom of the picture: look for the sun in the upper half only
    _, su = np.unravel_index(np.argmax(blur), blur.shape)
    sun_tex_az = (0.5 - (su + 0.5) / 720) * 2 * math.pi
    az, el = math.radians(sun_azimuth), math.radians(p["sun_elevation"])
    rot = sun_tex_az - az

    def horizon(az_from, az_to):
        cols = []
        for daz in range(int(az_from), int(az_to) + 1, 5):
            u = (0.5 - (az + math.radians(daz) + rot) / (2 * math.pi)) % 1.0
            for up in (1.0, 2.5, 4.0):
                cols.append(px[int((0.5 + up / 180) * 360), int(u * 720) % 720])
        return np.clip(np.mean(cols, axis=0) * p["strength"], 0, 30)

    cool = horizon(70, 290) * 0.9                                               # the sky away from the sun
    warm = np.minimum(horizon(-25, 25), 4.0) * 0.8 * np.array(p["glow"]) * 0.9   # the sky towards it

    world = bpy.data.worlds.new("Sky")
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    new, link = nt.nodes.new, nt.links.new
    tc, mp, env = new("ShaderNodeTexCoord"), new("ShaderNodeMapping"), new("ShaderNodeTexEnvironment")
    env.image = image
    mp.inputs["Rotation"].default_value = (0, 0, rot)
    link(tc.outputs["Generated"], mp.inputs["Vector"])
    link(mp.outputs[0], env.inputs["Vector"])
    cap = new("ShaderNodeMix")
    cap.data_type, cap.blend_type = "RGBA", "DARKEN"
    cap.inputs[0].default_value = 1.0
    link(env.outputs[0], cap.inputs[6])
    cap.inputs[7].default_value = (clamp, clamp, clamp, 1)
    sun_dir = Vector((math.cos(az) * math.cos(el), math.sin(az) * math.cos(el), math.sin(el)))
    geo, dot, sv = new("ShaderNodeNewGeometry"), new("ShaderNodeVectorMath"), new("ShaderNodeCombineXYZ")
    dot.operation = "DOT_PRODUCT"
    for i in range(3):
        sv.inputs[i].default_value = sun_dir[i]
    link(geo.outputs["Incoming"], dot.inputs[0])
    link(sv.outputs[0], dot.inputs[1])
    near = new("ShaderNodeMapRange")
    near.interpolation_type = "SMOOTHERSTEP"
    for name, value in (("From Min", -1.0), ("From Max", -0.55), ("To Min", 1.0), ("To Max", 0.0)):
        near.inputs[name].default_value = value
    link(dot.outputs["Value"], near.inputs["Value"])
    tint = new("ShaderNodeMix")
    tint.data_type, tint.blend_type = "RGBA", "MULTIPLY"
    link(near.outputs["Result"], tint.inputs[0])
    link(cap.outputs[2], tint.inputs[6])
    tint.inputs[7].default_value = (*p["glow"], 1)
    bg, out = new("ShaderNodeBackground"), new("ShaderNodeOutputWorld")
    link(tint.outputs[2], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = p["strength"]
    link(bg.outputs[0], out.inputs["Surface"])

    for old in [o for o in bpy.data.objects if o.name in ("Sun", "Sun_disc")]:
        bpy.data.objects.remove(old)
    lamp = None
    if p["sun_strength"] > 0:
        data = bpy.data.lights.new("Sun", "SUN")
        data.energy, data.color, data.angle = p["sun_strength"], p["sun_color"], math.radians(p["sun_angle"])
        lamp = bpy.data.objects.new("Sun", data)
        scene.collection.objects.link(lamp)
        lamp.rotation_euler = sun_dir.to_track_quat("Z", "Y").to_euler()
        if disc > 0:
            mesh = bpy.data.meshes.new("Sun_disc")
            import bmesh
            bm = bmesh.new()
            bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=1.0)
            bm.to_mesh(mesh)
            bm.free()
            ball = bpy.data.objects.new("Sun_disc", mesh)
            scene.collection.objects.link(ball)
            m = bpy.data.materials.new("Sun_disc")
            m.use_nodes = True
            m.node_tree.nodes.clear()
            em, mo = m.node_tree.nodes.new("ShaderNodeEmission"), m.node_tree.nodes.new("ShaderNodeOutputMaterial")
            em.inputs["Color"].default_value = (*p["sun_color"], 1)
            em.inputs["Strength"].default_value = disc
            m.node_tree.links.new(em.outputs[0], mo.inputs["Surface"])
            mesh.materials.append(m)
            m["hazed"] = True   # haze() leaves it alone
            ball.visible_diffuse = ball.visible_shadow = ball.visible_volume_scatter = ball.visible_transmission = False
            ball.location, ball.scale = sun_dir * 20000, (120, 120, 120)
    scene.view_settings.view_transform = "AgX"
    try:
        scene.view_settings.look = look
    except TypeError:
        pass
    scene.view_settings.exposure = p["exposure"]
    return {"world": world, "sun": lamp, "sun_direction": tuple(sun_dir), "cool": tuple(float(v) for v in cool),
            "warm": tuple(float(v) for v in warm), "preset": preset}


def haze(light, density=0.00019, cap=0.8, thin_above=700.0, strength=1.0, skip=()):
    """Aerial perspective inside the materials: the farther a surface, the more of the sky's colour it takes, warm
    towards the sun and cool away from it. Because it is in the materials, reflections in water carry it too, and it
    costs no render time. Call it after every material exists; it wraps each one once.

    density: per metre (0.00019 leaves hills a kilometre away clearly bluish; raise it for mist). cap: the most a
    surface can fade. thin_above: metres over which the haze thins with height, so peaks stay crisp; 0 for none."""
    group = bpy.data.node_groups.get("Haze")
    if group is None:
        group = bpy.data.node_groups.new("Haze", "ShaderNodeTree")
        group.interface.new_socket("Shader", in_out="INPUT", socket_type="NodeSocketShader")
        group.interface.new_socket("Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
        new, link = group.nodes.new, group.links.new

        def value(name, v):
            n = new("ShaderNodeValue")
            n.name = n.label = name
            n.outputs[0].default_value = v
            return n.outputs[0]

        def colour(name, v):
            n = new("ShaderNodeRGB")
            n.name = n.label = name
            n.outputs[0].default_value = (*v, 1)
            return n.outputs[0]

        def calc(op, a, b=None, clamp=False):
            n = new("ShaderNodeMath")
            n.operation, n.use_clamp = op, clamp
            for sock, v in ((n.inputs[0], a), (n.inputs[1], b)):
                if v is None:
                    continue
                if isinstance(v, bpy.types.NodeSocket):
                    link(v, sock)
                else:
                    sock.default_value = v
            return n.outputs[0]

        gi, go = new("NodeGroupInput"), new("NodeGroupOutput")
        geo, cam = new("ShaderNodeNewGeometry"), new("ShaderNodeCameraData")
        to_world = new("ShaderNodeVectorTransform")
        to_world.vector_type, to_world.convert_from, to_world.convert_to = "VECTOR", "CAMERA", "WORLD"
        link(cam.outputs["View Vector"], to_world.inputs[0])
        fade = calc("SUBTRACT", 1.0, calc("EXPONENT", calc("MULTIPLY", calc("MULTIPLY", cam.outputs["View Distance"], value("K", density)), -1.0)))
        z = new("ShaderNodeSeparateXYZ")
        link(geo.outputs["Position"], z.inputs[0])
        thin = calc("EXPONENT", calc("MULTIPLY", calc("DIVIDE", calc("MAXIMUM", z.outputs["Z"], 0.0), value("HEIGHT", 700.0)), -1.0))
        with_height = new("ShaderNodeMix")
        with_height.data_type = "FLOAT"
        link(value("USE_HEIGHT", 1.0), with_height.inputs[0])
        with_height.inputs[2].default_value = 1.0
        link(thin, with_height.inputs[3])
        fade = calc("MINIMUM", calc("MULTIPLY", fade, with_height.outputs[0], clamp=True), value("CAP", cap))
        sun = new("ShaderNodeCombineXYZ")
        sun.name = "SUN_DIR"
        dot = new("ShaderNodeVectorMath")
        dot.operation = "DOT_PRODUCT"
        link(to_world.outputs[0], dot.inputs[0])
        link(sun.outputs[0], dot.inputs[1])
        toward = new("ShaderNodeMapRange")
        toward.interpolation_type = "SMOOTHSTEP"
        toward.inputs["From Min"].default_value, toward.inputs["From Max"].default_value = 0.2, 0.98
        link(dot.outputs["Value"], toward.inputs["Value"])
        tone = new("ShaderNodeMix")
        tone.data_type = "RGBA"
        link(toward.outputs["Result"], tone.inputs[0])
        link(colour("COOL", (0.6, 0.65, 0.75)), tone.inputs[6])
        link(colour("WARM", (1.2, 0.8, 0.5)), tone.inputs[7])
        em = new("ShaderNodeEmission")
        link(tone.outputs[2], em.inputs["Color"])
        link(value("STRENGTH", 1.0), em.inputs["Strength"])
        mx = new("ShaderNodeMixShader")
        link(fade, mx.inputs[0])
        link(gi.outputs[0], mx.inputs[1])
        link(em.outputs[0], mx.inputs[2])
        link(mx.outputs[0], go.inputs[0])
    g = group.nodes
    g["K"].outputs[0].default_value, g["CAP"].outputs[0].default_value, g["STRENGTH"].outputs[0].default_value = density, cap, strength
    g["USE_HEIGHT"].outputs[0].default_value, g["HEIGHT"].outputs[0].default_value = (1.0 if thin_above > 0 else 0.0), max(1.0, thin_above)
    g["COOL"].outputs[0].default_value, g["WARM"].outputs[0].default_value = (*light["cool"], 1), (*light["warm"], 1)
    for i in range(3):
        g["SUN_DIR"].inputs[i].default_value = light["sun_direction"][i]
    wrapped = 0
    for mat in bpy.data.materials:
        if not mat.use_nodes or mat.get("hazed") or mat.name in skip:
            continue
        nt = mat.node_tree
        out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None)
        if not out or not out.inputs["Surface"].links:
            continue
        src = out.inputs["Surface"].links[0].from_socket
        node = nt.nodes.new("ShaderNodeGroup")
        node.node_tree = group
        node.name = "Haze"
        nt.links.new(src, node.inputs[0])
        nt.links.new(node.outputs[0], out.inputs["Surface"])
        mat["hazed"] = True
        mat.cycles.emission_sampling = "NONE"   # the haze glows a little; it must not be searched as a light source
        wrapped += 1
    return {"materials": wrapped, "density": density}


def film_settings(width=2560, height=None, samples=128, scene=None):
    """Cycles as the lakeside film was rendered: adaptive sampling, denoised, few bounces, the same grain every frame."""
    scene = scene or bpy.context.scene
    r, c = scene.render, scene.cycles
    r.engine = "CYCLES"
    r.resolution_x = int(width) // 2 * 2
    r.resolution_y = int(height or round(width * 9 / 16)) // 2 * 2
    r.resolution_percentage = 100
    c.samples, c.use_adaptive_sampling, c.adaptive_threshold, c.adaptive_min_samples = samples, True, 0.05, max(8, samples // 4)
    c.use_denoising, c.denoiser = True, "OPENIMAGEDENOISE"
    c.max_bounces, c.diffuse_bounces, c.glossy_bounces, c.transmission_bounces = 6, 2, 3, 6
    c.transparent_max_bounces, c.volume_bounces = 40, 0
    c.caustics_reflective = c.caustics_refractive = False
    c.sample_clamp_indirect = 8.0
    c.seed, c.use_animated_seed = 3, False
    r.use_persistent_data = True
    r.film_transparent = False
    return {"size": [r.resolution_x, r.resolution_y], "samples": samples}


def library(asset, resolution="2k"):
    """Path of an asset in the library, by the id `likeness library` lists, e.g. "polyhaven:island_tree_01":
    the model or sky file itself, or the folder of a texture set. A Poly Haven asset that is not there yet is
    downloaded into the library first (CC0, a few to a few hundred MB each)."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import home
    root = home.library_root()
    folder = root.joinpath(*asset.split(":"))
    if not folder.is_dir() and asset.startswith("polyhaven:"):
        import sources
        print(f"LIKENESS downloading {asset} ({resolution}) into {folder} ...", flush=True)
        item = sources.fetch_library(asset, folder, resolution)
        print(f"LIKENESS downloaded {asset}: {item['source_line']}", flush=True)
    if not folder.is_dir():
        raise ValueError(f"{asset} is not in {root}. Run `likeness library` to see what is, and how to add to it.")
    for kinds in ((".hdr", ".exr"), (".gltf", ".glb", ".blend", ".fbx", ".obj")):
        found = sorted(f for f in folder.rglob("*") if f.suffix.lower() in kinds)
        if found:
            return found[0]
    return folder


class Place:
    """A real place made by `likeness place`, in the scene's metres: x east, y north, origin at the place's centre.

        place = outdoor.place("<place dir>")                  # heights relative to the centre point (base)
        ground = outdoor.land(place.extent, place.height, cell=2)
        ground = outdoor.land((-60, -260, 60, -140), place.height, cell=0.5)     # finer where the lens passes
        for f in place.outlines("building"): f["points"]      # OpenStreetMap footprints, [[x, y], ...]

    The heights are about 30 m apart in the source data: terraces, walls and cliffs are not in them. Build those on
    top, from photographs."""

    def __init__(self, folder, base=None):
        import json
        from pathlib import Path
        folder = Path(folder)
        self.info = json.loads((folder / "place.json").read_text(encoding="utf-8"))
        self.grid = np.load(folder / "height.npy").astype(np.float64)
        self.cell = float(self.info["cell"])
        self.half = (self.grid.shape[0] - 1) * self.cell / 2
        self.extent = (-self.half, -self.half, self.half, self.half)
        self.base = float(self.info["centre_height"] if base is None else base)
        self.features = json.loads((folder / "features.json").read_text(encoding="utf-8"))

    def height(self, x, y):
        """Ground height at (x, y) in scene metres, relative to `base`; bilinear between samples."""
        n = self.grid.shape[0] - 1
        fx = min(max((x + self.half) / self.cell, 0.0), n - 1e-6)
        fy = min(max((self.half - y) / self.cell, 0.0), n - 1e-6)
        j, i = int(fx), int(fy)
        ax, ay = fx - j, fy - i
        g = self.grid
        z = (g[i, j] * (1 - ax) * (1 - ay) + g[i, j + 1] * ax * (1 - ay) + g[i + 1, j] * (1 - ax) * ay + g[i + 1, j + 1] * ax * ay)
        return float(z) - self.base

    def outlines(self, kind=None, **tags):
        """OpenStreetMap features of one kind (building, wall, path, road, water, cliff, area, line, point), optionally
        with given tags, e.g. outlines("area", landuse="farmland")."""
        return [f for f in self.features if (kind is None or f["kind"] == kind)
                and all(f["tags"].get(k) == v for k, v in tags.items())]


def place(folder, base=None):
    """The real place in a folder made by `likeness place`: see Place."""
    return Place(folder, base)


def land(extent, height, cell=1.0, name="Terrain"):
    """A ground mesh from a height function: the way to make a shore, a bank, a valley or a far hill where you want it.

    extent: (x0, y0, x1, y1) in metres. height: a function (x, y) -> z. cell: metres between vertices; a metre is
    enough where the lens passes, a far landscape can take 2 or 3. Water is a plane at its level (terrain.water); the
    ground simply goes below it.

        def height(x, y):                       # a bank falling into a lake to the north, a far shore 120 m away
            d = y - 3.0
            z = 0.6 * smooth(0, -5, d) if d < 0 else -1.7 * smooth(0, 11, d)
            return max(z, -1.7 + 2.3 * smooth(106, 123, y) + 0.16 * max(0.0, y - 120))"""
    import bmesh
    x0, y0, x1, y1 = extent
    nx, ny = max(2, int(round((x1 - x0) / cell))), max(2, int(round((y1 - y0) / cell)))
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=nx, y_segments=ny, size=0.5)
    for v in bm.verts:
        v.co.x = x0 + (v.co.x + 0.5) * (x1 - x0)
        v.co.y = y0 + (v.co.y + 0.5) * (y1 - y0)
        v.co.z = height(v.co.x, v.co.y)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    for p in mesh.polygons:
        p.use_smooth = True
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def smooth(a, b, v):
    """0 at a, 1 at b, eased; a may be the larger. For height functions."""
    t = max(0.0, min(1.0, (v - a) / (b - a)))
    return t * t * (3 - 2 * t)


def ground(obj, base, size=3.0, patches=None, path=None, path_line=None, path_width=3.0, verge=10.0, relief=0.6, name="Ground",
           tint=None, path_tint=None, shore=None, water_level=0.0, shore_height=0.5):
    """Cover a ground mesh with scanned textures laid from above in real metres, so nothing stretches.

    base, patches, path: texture folders (see library()). size: metres one repeat of a texture covers. patches is a
    second ground that takes over in large irregular areas, which hides the repeat. tint: a colour (r, g, b) the
    turf is multiplied by, the path left as it is; scanned grass often reads as dry straw under a warm sky, and
    (0.5, 0.66, 0.36) makes it green. shore: a texture folder (sand, pebbles) that takes over within shore_height
    metres above water_level, darker and smoother at the waterline, with a ragged edge. path_tint: a colour the path
    is multiplied by; pale gravel under a low sun burns out to a white patch, (0.6, 0.56, 0.5) keeps its grain. path is laid along path_line
    ([[x, y], ...]) path_width metres wide, with a ragged edge; the mesh should have a vertex every metre or closer
    there. The base is also sampled a second time, larger, and the two are blended, so no tile edge shows."""
    import re
    from pathlib import Path
    from .surfaces import MAP_NAMES
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    new, link = nt.nodes.new, nt.links.new
    bsdf = nt.nodes["Principled BSDF"]
    geo = new("ShaderNodeNewGeometry")

    def calc(op, a, b):
        n = new("ShaderNodeMath")
        n.operation = op
        for sock, v in ((n.inputs[0], a), (n.inputs[1], b)):
            if isinstance(v, bpy.types.NodeSocket):
                link(v, sock)
            else:
                sock.default_value = v
        return n.outputs[0]

    def layer(folder, metres, turn=0.0):
        maps = {}
        for file in sorted(Path(folder).rglob("*")):
            if file.suffix.lower() not in {".png", ".jpg", ".jpeg", ".exr", ".tif", ".webp"}:
                continue
            stem = file.stem.lower().replace("-", "_")
            for role in ("color", "roughness", "height"):
                if re.search(r"(?:^|_)(?:" + MAP_NAMES[role] + r")(?:_|$)", stem):
                    maps.setdefault(role, file)
        if "color" not in maps:
            raise ValueError(f"No colour map in {folder}.")
        mp = new("ShaderNodeMapping")
        mp.inputs["Scale"].default_value = (1 / metres, 1 / metres, 1)
        mp.inputs["Rotation"].default_value = (0, 0, turn)
        link(geo.outputs["Position"], mp.inputs["Vector"])
        out = {}
        for role, file in maps.items():
            image = bpy.data.images.load(str(file), check_existing=True)
            if role != "color":
                image.colorspace_settings.name = "Non-Color"
            tex = new("ShaderNodeTexImage")
            tex.image, tex.label = image, role
            link(mp.outputs[0], tex.inputs["Vector"])
            out[role] = tex.outputs["Color"]
        return out

    def blend(a, b, fac):
        out = {}
        for role in ("color", "roughness", "height"):
            if role in a and role in b:
                n = new("ShaderNodeMix")
                n.data_type = "RGBA"
                link(fac, n.inputs[0]) if isinstance(fac, bpy.types.NodeSocket) else setattr(n.inputs[0], "default_value", fac)
                link(a[role], n.inputs[6])
                link(b[role], n.inputs[7])
                out[role] = n.outputs[2]
            elif role in a:
                out[role] = a[role]
        return out

    def patchy(scale, low, high):
        n = new("ShaderNodeTexNoise")
        n.inputs["Scale"].default_value, n.inputs["Detail"].default_value = scale, 5.0
        link(geo.outputs["Position"], n.inputs["Vector"])
        r = new("ShaderNodeMapRange")
        r.interpolation_type = "SMOOTHSTEP"
        r.inputs["From Min"].default_value, r.inputs["From Max"].default_value = low, high
        link(n.outputs["Fac"], r.inputs["Value"])
        return r.outputs["Result"]

    cover = blend(layer(base, size), layer(base, size * 3.7, 0.9), patchy(0.35, 0.35, 0.65))
    if patches:
        cover = blend(cover, layer(patches, size * 1.3, 0.4), patchy(0.06, 0.48, 0.6))
    if tint:
        n = new("ShaderNodeMix")
        n.data_type, n.blend_type = "RGBA", "MULTIPLY"
        n.inputs[0].default_value = 1.0
        link(cover["color"], n.inputs[6])
        n.inputs[7].default_value = (*tint, 1)
        cover["color"] = n.outputs[2]
    wet = None
    if shore:
        z = new("ShaderNodeSeparateXYZ")
        link(geo.outputs["Position"], z.inputs[0])
        lifted = calc("ADD", z.outputs["Z"], calc("MULTIPLY", calc("SUBTRACT", patchy(0.9, 0.0, 1.0), 0.5), shore_height * 0.45))

        def below(top, bottom):
            r = new("ShaderNodeMapRange")
            r.interpolation_type = "SMOOTHSTEP"
            r.inputs["From Min"].default_value, r.inputs["From Max"].default_value = water_level + top, water_level + bottom
            link(lifted, r.inputs["Value"])
            return r.outputs["Result"]
        sand = layer(shore, max(1.5, size * 0.8), 0.6)
        wet = below(shore_height * 0.45, shore_height * 0.08)
        dark = new("ShaderNodeMix")
        dark.data_type, dark.blend_type = "RGBA", "MULTIPLY"
        link(wet, dark.inputs[0])
        link(sand["color"], dark.inputs[6])
        dark.inputs[7].default_value = (0.42, 0.42, 0.44, 1)
        sand["color"] = dark.outputs[2]
        cover = blend(cover, sand, below(shore_height, shore_height * 0.5))
    if path and path_line:
        # one line [[x, y], ...] or several [[[x, y], ...], [[x, y], ...]]: a path with a branch is two lines
        lines = path_line if isinstance(path_line[0][0], (list, tuple)) else [path_line]
        lines = [[Vector((p[0], p[1])) for p in one] for one in lines]

        def away(x, y):
            p, best = Vector((x, y)), 1e9
            for pts in lines:
                for a, b in zip(pts, pts[1:]):
                    ab = b - a
                    t = max(0.0, min(1.0, (p - a).dot(ab) / max(1e-9, ab.length_squared)))
                    best = min(best, (p - (a + ab * t)).length)
            return best
        mesh = obj.data
        attr = mesh.attributes.get("path") or mesh.attributes.new("path", "FLOAT", "POINT")
        # "verge": 1 beside the path, fading out over `verge` metres, 0 on it. A density field for scattering what
        # grows along a path (layout.scatter(..., mask="verge")), so that the far ground is not filled with it.
        beside = mesh.attributes.get("verge") or mesh.attributes.new("verge", "FLOAT", "POINT")
        half = path_width / 2
        for v in mesh.vertices:
            w = obj.matrix_world @ v.co
            d = away(w.x, w.y)
            attr.data[v.index].value = max(0.0, min(1.0, 0.5 + (half - d) / 1.2))
            beside.data[v.index].value = 0.0 if d < half + 0.2 else max(0.0, min(1.0, (half + verge - d) / (verge * 0.6)))
        mask = new("ShaderNodeAttribute")
        mask.attribute_name = "path"
        ragged = calc("ADD", mask.outputs["Fac"], calc("MULTIPLY", calc("SUBTRACT", patchy(1.6, 0.0, 1.0), 0.5), 0.35))
        edge = new("ShaderNodeMapRange")
        edge.interpolation_type = "SMOOTHSTEP"
        edge.inputs["From Min"].default_value, edge.inputs["From Max"].default_value = 0.38, 0.62
        link(ragged, edge.inputs["Value"])
        gravel = layer(path, max(1.2, size * 0.5), 0.2)
        if path_tint:
            n = new("ShaderNodeMix")
            n.data_type, n.blend_type = "RGBA", "MULTIPLY"
            n.inputs[0].default_value = 1.0
            link(gravel["color"], n.inputs[6])
            n.inputs[7].default_value = (*path_tint, 1)
            gravel["color"] = n.outputs[2]
        cover = blend(cover, gravel, edge.outputs["Result"])
    link(cover["color"], bsdf.inputs["Base Color"])
    if "roughness" in cover:
        if wet is not None:   # wet ground at the waterline is smooth
            n = new("ShaderNodeMix")
            n.data_type = "RGBA"
            link(wet, n.inputs[0])
            link(cover["roughness"], n.inputs[6])
            n.inputs[7].default_value = (0.22, 0.22, 0.22, 1)
            cover["roughness"] = n.outputs[2]
        link(cover["roughness"], bsdf.inputs["Roughness"])
    bsdf.inputs["Specular IOR Level"].default_value = 0.2   # dry ground: without this a low sun lays a wet sheen on it
    if "height" in cover and relief > 0:
        bump = new("ShaderNodeBump")
        bump.inputs["Strength"].default_value, bump.inputs["Distance"].default_value = relief, 0.04
        link(cover["height"], bump.inputs["Height"])
        link(bump.outputs["Normal"], bsdf.inputs["Normal"])
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    return mat


def clear_view(eye, aim, far, half=14.0, wide=34.0, near=26.0):
    """A test for scattering: False inside the cone a camera at `eye` (x, y) looks down towards `aim` (x, y), so no
    tree or rock lands between a stop and what it has come to see. half: degrees either side kept clear out to `far`
    metres; wide: a wider cone kept clear close to the eye, out to `near` metres.

        clear = [outdoor.clear_view(stop_xy, landmark_xy, 120) for stop_xy, landmark_xy in stops]
        spots = [p for p in spots if all(c(*p[:2]) for c in clear)]"""
    vx, vy = aim[0] - eye[0], aim[1] - eye[1]
    length = math.hypot(vx, vy) or 1.0
    vx, vy = vx / length, vy / length

    def keep(x, y):
        dx, dy = x - eye[0], y - eye[1]
        dist = math.hypot(dx, dy)
        if dist < 2.0:
            return False
        ang = math.degrees(math.acos(max(-1.0, min(1.0, (dx * vx + dy * vy) / dist))))
        return not ((ang < half and dist < far) or (ang < wide and dist < near))
    return keep


def scatter(extent, count, keep=None, spacing=3.0, sizes=(6.0, 11.0), seed=0):
    """Spots for plant(): `count` random (x, y, size) inside extent (x0, y0, x1, y1), at least `spacing` metres
    apart, each passing every test in keep (one function or a list, e.g. clear_view tests)."""
    import random
    rng = random.Random(seed)
    tests = keep if isinstance(keep, (list, tuple)) else ([keep] if keep else [])
    out, tries = [], 0
    while len(out) < count and tries < count * 80:
        tries += 1
        x, y = rng.uniform(extent[0], extent[2]), rng.uniform(extent[1], extent[3])
        if all(t(x, y) for t in tests) and all(math.hypot(x - a, y - b) >= spacing for a, b, _ in out[-400:]):
            out.append((x, y, rng.uniform(*sizes)))
    return out


def plant(model, spots, size, ground_object=None, vary=0.15, sink=0.15, seed=0, name=None, group=None):
    """Stand copies of one scanned model (a tree, a rock, a shrub) at the given places.

    model: a model file (see library()). spots: [(x, y), ...]; a third number sets that copy's own size. size: the
    largest dimension in metres (for a tree, usually its height). Each copy is turned at random, varies in size by
    +-vary, stands on ground_object and is sunk a little so that it does not float. The copies share one mesh.
    group: a name; the copies are gathered under one empty of that name (made if need be), so that many of them can
    be named as one thing, e.g. "must_see": {"shore": ["Far wood"]} for a whole wood.
    Returns the placed objects, named name.000, name.001, ... (name defaults to the file's)."""
    import random
    from pathlib import Path
    from . import assets
    rng = random.Random(seed)
    scene = bpy.context.scene
    name = name or Path(model).stem
    source = bpy.data.collections.get("Sources") or bpy.data.collections.new("Sources")
    if source.name not in scene.collection.children:
        scene.collection.children.link(source)

    def shut(layer, state):   # the originals stay in the file but are not rendered; their copies are
        if layer.collection == source:
            layer.exclude = state
            return True
        return any(shut(child, state) for child in layer.children)
    shut(bpy.context.view_layer.layer_collection, False)   # open while importing: a model is measured as it stands in the scene
    made = assets.import_model(model, target_size=size, name=name + " source", collection=source)
    target = None
    if ground_object is not None:
        bpy.context.view_layer.update()
        target = ground_object.evaluated_get(bpy.context.evaluated_depsgraph_get())
        inverse = target.matrix_world.inverted()
    placed = []
    for i, spot in enumerate(spots):
        x, y = float(spot[0]), float(spot[1])
        z = 0.0
        if target is not None:
            hit, point, _, _ = target.ray_cast(inverse @ Vector((x, y, 1e4)), (inverse.to_3x3() @ Vector((0, 0, -1))).normalized())
            if not hit:
                continue
            z = (target.matrix_world @ point).z
        copy = bpy.data.objects.new(f"{name}.{i:03d}", None)
        copy.instance_type, copy.instance_collection = "COLLECTION", made["collection"]
        scene.collection.objects.link(copy)
        scale = (float(spot[2]) / size if len(spot) > 2 else 1.0) * (1 + rng.uniform(-vary, vary))
        copy.location, copy.scale = (x, y, z - sink * scale), (scale, scale, scale)
        copy.rotation_euler = (0, 0, rng.uniform(0, math.tau))
        copy.empty_display_size = 0.5
        placed.append(copy)
    if group:
        holder = bpy.data.objects.get(group)
        if holder is None:
            holder = bpy.data.objects.new(group, None)
            scene.collection.objects.link(holder)
        for copy in placed:
            copy.parent = holder
    shut(bpy.context.view_layer.layer_collection, True)
    return placed


def strew(ground_object, models, density, mask="verge", scale=(0.7, 1.3), seed=0, name="Undergrowth"):
    """Scatter small scanned plants or stones over the ground: grass tufts, ferns, pebbles.

    models: [(model file, largest dimension in metres), ...]. density: copies per square metre where the mask is 1.
    mask: a density field on the ground mesh; ground() writes "verge" (beside the path, none on it). Scatter only
    where the lens comes close: a whole landscape of tufts costs memory and shows nowhere."""
    from pathlib import Path
    from . import assets, layout
    scene = bpy.context.scene
    source = bpy.data.collections.get("Sources") or bpy.data.collections.new("Sources")
    if source.name not in scene.collection.children:
        scene.collection.children.link(source)

    def shut(layer, state):
        if layer.collection == source:
            layer.exclude = state
            return True
        return any(shut(child, state) for child in layer.children)
    shut(bpy.context.view_layer.layer_collection, False)
    kinds = bpy.data.collections.new(name + " kinds")
    source.children.link(kinds)
    for model, size in models:   # each model becomes one child collection: one variant for the scatter to pick from
        assets.import_model(model, target_size=size, name=Path(model).stem + " source", collection=kinds)
    bpy.context.view_layer.update()
    emitter = layout.scatter(ground_object, kinds, density=density, seed=seed, scale=scale, mask=mask)
    emitter.name = name
    shut(bpy.context.view_layer.layer_collection, True)
    return emitter


def water(size=(700, 700), level=0.0, colour=(0.03, 0.07, 0.075), ripple=0.16, ripple_metres=(2.2, 0.6), at=(0, 0), name="Water"):
    """Still water for a lake or a pond: a plane at its level, the ground simply going below it.

    It mirrors the sky and what stands on the shore, and shows the bed where it is shallow. ripple: how strong the
    small waves are (0 for a mirror; 0.16 is an evening calm). ripple_metres: their length along x and y, in metres;
    keep them under a few metres however large the water is. Things standing in the water (piles, a boat, reeds) read
    as stuck into glass unless something breaks the surface round them: stones, reeds, a darker wet line."""
    import bmesh
    bm = bmesh.new()
    x, y = size
    bm.faces.new([bm.verts.new((at[0] + i * x / 2, at[1] + j * y / 2, 0)) for i, j in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location.z = level
    bpy.context.scene.collection.objects.link(obj)
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    b = nt.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*colour, 1)
    b.inputs["Roughness"].default_value, b.inputs["IOR"].default_value = 0.04, 1.333
    b.inputs["Transmission Weight"].default_value = 0.6
    if ripple > 0:
        geo, mp, tex, bump = (nt.nodes.new(k) for k in ("ShaderNodeNewGeometry", "ShaderNodeMapping", "ShaderNodeTexNoise", "ShaderNodeBump"))
        mp.inputs["Scale"].default_value = (1 / ripple_metres[0], 1 / ripple_metres[1], 1)
        tex.inputs["Scale"].default_value, tex.inputs["Detail"].default_value = 1.0, 3.0
        bump.inputs["Strength"].default_value, bump.inputs["Distance"].default_value = ripple, 0.03
        nt.links.new(geo.outputs["Position"], mp.inputs["Vector"])
        nt.links.new(mp.outputs[0], tex.inputs["Vector"])
        nt.links.new(tex.outputs["Fac"], bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], b.inputs["Normal"])
    mesh.materials.append(mat)
    return obj


def material(folder, metres=1.0, tint=None, vary=0.12, rough_add=0.0, relief=0.7, name=None):
    """A scanned texture set as a material for things you build yourself (planks, posts, walls, a hull): it needs no
    UVs. The pictures are wrapped round each object from three sides in real metres, and every object comes out a
    little lighter or darker than its neighbour (vary), so a row of planks is not one flat sheet.

    folder: a texture folder (see library()). metres: what one repeat covers. tint: a colour to multiply by, for paint
    or for darker, older wood. rough_add: added to the roughness (negative for a worn sheen)."""
    import re
    from pathlib import Path
    from .surfaces import MAP_NAMES
    found = {}
    for file in sorted(Path(folder).rglob("*")):
        if file.suffix.lower() not in {".png", ".jpg", ".jpeg", ".exr", ".tif", ".webp"}:
            continue
        stem = file.stem.lower().replace("-", "_")
        for role in ("color", "roughness", "height"):
            if re.search(r"(?:^|_)(?:" + MAP_NAMES[role] + r")(?:_|$)", stem):
                found.setdefault(role, file)
    if "color" not in found:
        raise ValueError(f"No colour map in {folder}.")
    mat = bpy.data.materials.new(name or Path(folder).name)
    mat.use_nodes = True
    nt = mat.node_tree
    new, link = nt.nodes.new, nt.links.new
    b = nt.nodes["Principled BSDF"]
    tc, mp, info = new("ShaderNodeTexCoord"), new("ShaderNodeMapping"), new("ShaderNodeObjectInfo")
    mp.inputs["Scale"].default_value = (1 / metres,) * 3
    link(tc.outputs["Object"], mp.inputs["Vector"])
    apart, spread, shift = new("ShaderNodeCombineXYZ"), new("ShaderNodeVectorMath"), new("ShaderNodeVectorMath")
    for i in range(3):
        link(info.outputs["Random"], apart.inputs[i])
    spread.operation = "SCALE"
    spread.inputs["Scale"].default_value = 7.3   # each object takes its own part of the picture
    link(apart.outputs[0], spread.inputs[0])
    shift.operation = "ADD"
    link(mp.outputs[0], shift.inputs[0])
    link(spread.outputs[0], shift.inputs[1])
    maps = {}
    for role, file in found.items():
        tex = new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(str(file), check_existing=True)
        tex.projection, tex.projection_blend = "BOX", 0.15
        if role != "color":
            tex.image.colorspace_settings.name = "Non-Color"
        link(shift.outputs[0], tex.inputs["Vector"])
        maps[role] = tex.outputs["Color"]
    colour = maps["color"]
    if vary > 0:
        value, hsv = new("ShaderNodeMapRange"), new("ShaderNodeHueSaturation")
        value.inputs["To Min"].default_value, value.inputs["To Max"].default_value = 1 - vary, 1 + vary
        link(info.outputs["Random"], value.inputs["Value"])
        link(colour, hsv.inputs["Color"])
        link(value.outputs["Result"], hsv.inputs["Value"])
        colour = hsv.outputs["Color"]
    if tint:
        mul = new("ShaderNodeMix")
        mul.data_type, mul.blend_type = "RGBA", "MULTIPLY"
        mul.inputs[0].default_value = 1.0
        link(colour, mul.inputs[6])
        mul.inputs[7].default_value = (*tint, 1)
        colour = mul.outputs[2]
    link(colour, b.inputs["Base Color"])
    if "roughness" in maps:
        add = new("ShaderNodeMath")
        add.operation, add.use_clamp = "ADD", True
        link(maps["roughness"], add.inputs[0])
        add.inputs[1].default_value = rough_add
        link(add.outputs[0], b.inputs["Roughness"])
    if "height" in maps and relief > 0:
        bump = new("ShaderNodeBump")
        bump.inputs["Strength"].default_value, bump.inputs["Distance"].default_value = relief, 0.01
        link(maps["height"], bump.inputs["Height"])
        link(bump.outputs["Normal"], b.inputs["Normal"])
    return mat
