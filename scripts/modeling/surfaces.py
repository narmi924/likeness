"""Build a UV-mapped PBR material from the downloaded image maps."""

import re
from pathlib import Path

import bpy

MAP_NAMES = {
    "color": r"color|albedo|diffuse|diff|base_?color",
    "roughness": r"roughness|rough",
    "metallic": r"metalness|metallic|metal",
    "normal_gl": r"normal_?gl|nor_?gl|normal|nor",
    "normal_dx": r"normal_?dx|nor_?dx",
    "height": r"displacement|disp|height",
    "ao": r"ambient_?occlusion|occlusion|ao",
    "alpha": r"opacity|alpha|transparency",
}


def pbr_material(folder, name=None, scale=1.0, displacement=False):
    folder = Path(folder).expanduser().resolve(strict=True)
    if not folder.is_dir() or scale <= 0:
        raise ValueError("Choose a material folder and a positive UV scale.")
    maps = {}
    for path in sorted(folder.rglob("*")):
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".webp"}:
            continue
        stem = path.stem.lower().replace("-", "_").replace(" ", "_")
        for role, pattern in MAP_NAMES.items():
            if re.search(r"(?:^|_)(?:" + pattern + r")(?:_|$)", stem):
                if role == "normal_gl" and re.search(r"(?:normal|nor)_?dx", stem):
                    continue
                maps.setdefault(role, path)
                break
    if not maps:
        raise ValueError(f"No recognized PBR maps in {folder.name}.")
    if "normal_gl" in maps:
        maps.pop("normal_dx", None)
    mat = bpy.data.materials.new(name or folder.name)
    mat.use_nodes = True
    nodes, link = mat.node_tree.nodes, mat.node_tree.links.new
    shader = nodes.get("Principled BSDF")
    shader.location = (400, 200)
    out = nodes.get("Material Output")
    out.location = (710, 200)
    uv = nodes.new("ShaderNodeTexCoord")
    uv.location = (-1050, 260)
    mapping = nodes.new("ShaderNodeMapping")
    mapping.location = (-820, 260)
    mapping.inputs["Scale"].default_value = (scale, scale, scale)
    link(uv.outputs["UV"], mapping.inputs["Vector"])
    textures = {}
    for i, (role, path) in enumerate(maps.items()):
        if role == "height" and not displacement:
            continue
        image = bpy.data.images.load(str(path), check_existing=True)
        expected = "sRGB" if role == "color" else "Non-Color"
        if image.colorspace_settings.name != expected:
            if image.users:
                image = image.copy()
            image.colorspace_settings.name = expected
        tex = nodes.new("ShaderNodeTexImage")
        tex.location, tex.image, tex.label = (-560, 420 - i * 275), image, role.replace("_", " ").title()
        link(mapping.outputs["Vector"], tex.inputs["Vector"])
        textures[role] = tex.outputs["Color"]
    color = textures.get("color")
    if "ao" in textures:
        mix = nodes.new("ShaderNodeMixRGB")
        mix.blend_type, mix.location = "MULTIPLY", (-90, 430)
        mix.inputs[0].default_value = 1
        mix.inputs[1].default_value = (1, 1, 1, 1)
        if color is not None:
            link(color, mix.inputs[1])
        link(textures["ao"], mix.inputs[2])
        color = mix.outputs[0]
    if color is not None:
        link(color, shader.inputs["Base Color"])
    for role, socket in (("roughness", "Roughness"), ("metallic", "Metallic"), ("alpha", "Alpha")):
        if role in textures:
            link(textures[role], shader.inputs[socket])
    normal = textures.get("normal_gl")
    if "normal_dx" in textures:
        # Tangent-space DirectX normals have the opposite green component.
        inv = nodes.new("ShaderNodeVectorMath")
        inv.operation, inv.location = "MULTIPLY_ADD", (-320, -400)
        inv.inputs[1].default_value, inv.inputs[2].default_value = (1, -1, 1), (0, 1, 0)
        link(textures["normal_dx"], inv.inputs[0])
        normal = inv.outputs["Vector"]
    if normal is not None:
        node = nodes.new("ShaderNodeNormalMap")
        node.location = (-40, -300)
        link(normal, node.inputs["Color"])
        link(node.outputs["Normal"], shader.inputs["Normal"])
    if "height" in textures:
        disp = nodes.new("ShaderNodeDisplacement")
        disp.location, disp.inputs["Scale"].default_value = (365, -420), 0.05
        link(textures["height"], disp.inputs["Height"])
        link(disp.outputs["Displacement"], out.inputs["Displacement"])
    return mat
