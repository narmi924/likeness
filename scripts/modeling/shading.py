"""wb3d.shading — materials built as node trees, the way a look-dev artist builds them: a base response plus the
micro-structure that makes it read as a real thing (brushed grain, blast texture, weave, flake, pixel grid), and
imperfection layers (fingerprints, dust, scratches, edge wear) that sit on top of any material at a low strength.

    body = W.lathe(profile, material=W.anodised('#8e949b', wear=0.15))
    cap = W.extrude(outline, material=W.fingerprints(W.gloss_plastic('#111'), 0.3))
    W.screen_material(screenshot, pixel_grid=0.12)

Textures use object-space coordinates by default, so nothing needs UVs and the pattern does not swim when the object
is animated. All colours are '#rrggbb' or RGBA tuples; sizes are in metres; strengths 0–1."""
import math

import bpy
from mathutils import Vector


# ─────────────────────────────── node helpers ───────────────────────────────


def col(c, alpha=1.0):
    """'#rrggbb' or a tuple → linear RGBA."""
    if isinstance(c, str):
        h = c.lstrip('#')
        srgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        lin = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in srgb]
        return (*lin, alpha)
    c = tuple(c)
    return c if len(c) == 4 else (*c[:3], alpha)


def new(name, **inputs):
    """A material with a Principled BSDF whose inputs are set from kwargs (by socket name). → (material, tree, bsdf)"""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes['Principled BSDF']
    for k, v in inputs.items():
        if k in b.inputs:
            b.inputs[k].default_value = v
    return m, nt, b


def node(nt, idname, **inputs):
    """A node with its inputs set by name (default values); properties such as noise_type via props=dict."""
    props = inputs.pop('props', None) or {}
    n = nt.nodes.new(idname)
    for k, v in props.items():
        setattr(n, k, v)
    for k, v in inputs.items():
        if k in n.inputs:
            n.inputs[k].default_value = v
    return n


def link(nt, a, b):
    """Connect: a and b are sockets, or (node, name/index) pairs."""
    a = a if isinstance(a, bpy.types.NodeSocket) else a[0].outputs[a[1]]
    b = b if isinstance(b, bpy.types.NodeSocket) else b[0].inputs[b[1]]
    return nt.links.new(a, b)


def coords(nt, scale=1.0, space='Object', rotation=(0, 0, 0)):
    """A vector for procedural textures: object space (stable under animation) scaled so `scale` repeats per metre."""
    tc = nt.nodes.new('ShaderNodeTexCoord'); mp = nt.nodes.new('ShaderNodeMapping')
    mp.inputs['Scale'].default_value = (scale, scale, scale)
    mp.inputs['Rotation'].default_value = tuple(math.radians(r) for r in rotation)
    nt.links.new(tc.outputs[space], mp.inputs['Vector'])
    return mp.outputs['Vector']


def mix(nt, a, b, fac=0.5, kind='RGBA', blend='MIX'):
    """A Mix node (colour or float): a, b and fac may be sockets or values. → the result socket."""
    n = nt.nodes.new('ShaderNodeMix'); n.data_type = kind; n.blend_type = blend; n.clamp_factor = True
    ia, ib, out = (6, 7, 2) if kind == 'RGBA' else (2, 3, 0)
    for idx, v in ((0, fac), (ia, a), (ib, b)):
        if isinstance(v, bpy.types.NodeSocket):
            nt.links.new(v, n.inputs[idx])
        else:
            n.inputs[idx].default_value = col(v) if (kind == 'RGBA' and idx != 0) else v
    return n.outputs[out]


def maprange(nt, value, from_min=0.0, from_max=1.0, to_min=0.0, to_max=1.0, smooth=False):
    n = nt.nodes.new('ShaderNodeMapRange'); n.clamp = True
    n.interpolation_type = 'SMOOTHSTEP' if smooth else 'LINEAR'
    for idx, v in ((1, from_min), (2, from_max), (3, to_min), (4, to_max)):
        n.inputs[idx].default_value = v
    if isinstance(value, bpy.types.NodeSocket):
        nt.links.new(value, n.inputs[0])
    else:
        n.inputs[0].default_value = value
    return n.outputs[0]


def math_node(nt, op, a, b=None, clamp=False):
    n = nt.nodes.new('ShaderNodeMath'); n.operation = op; n.use_clamp = clamp
    for idx, v in ((0, a), (1, b)):
        if v is None:
            continue
        if isinstance(v, bpy.types.NodeSocket):
            nt.links.new(v, n.inputs[idx])
        else:
            n.inputs[idx].default_value = v
    return n.outputs[0]


def noise(nt, vector, scale=10.0, detail=2.0, roughness=0.5, distortion=0.0, kind='FBM'):
    n = node(nt, 'ShaderNodeTexNoise', Scale=scale, Detail=detail, Roughness=roughness, Distortion=distortion, props={'noise_type': kind})
    nt.links.new(vector, n.inputs['Vector'])
    return n.outputs['Fac']


def voronoi(nt, vector, scale=10.0, feature='F1', randomness=1.0, output='Distance'):
    n = node(nt, 'ShaderNodeTexVoronoi', Scale=scale, Randomness=randomness, props={'feature': feature})
    nt.links.new(vector, n.inputs['Vector'])
    return n.outputs[output]


def bump(nt, height, strength=0.1, distance=0.001, normal=None):
    """A Bump node from a height signal → normal socket (chain: pass the previous normal socket as `normal`)."""
    n = node(nt, 'ShaderNodeBump', Strength=strength, Distance=distance)
    nt.links.new(height, n.inputs['Height'])
    if normal is not None:
        nt.links.new(normal, n.inputs['Normal'])
    return n.outputs['Normal']


def tap(nt, b, name):
    """The current value of a BSDF input as a socket (the linked output, or a Value / RGB node holding its default),
    so a layer can combine with whatever is already there."""
    s = b.inputs[name]
    if s.is_linked:
        return s.links[0].from_socket
    if s.type == 'RGBA':
        n = nt.nodes.new('ShaderNodeRGB'); n.outputs[0].default_value = s.default_value
        return n.outputs[0]
    n = nt.nodes.new('ShaderNodeValue'); n.outputs[0].default_value = float(s.default_value)
    return n.outputs[0]


def set_input(nt, b, name, socket):
    """Replace whatever feeds a BSDF input with this socket."""
    s = b.inputs[name]
    for l in list(s.links):
        nt.links.remove(l)
    nt.links.new(socket, s)


def tangent(nt, axis='Z', mode='RADIAL'):
    n = nt.nodes.new('ShaderNodeTangent'); n.direction_type = mode
    if mode == 'RADIAL':
        n.axis = axis
    return n.outputs['Tangent']


def _bsdf(m):
    return m.node_tree.nodes['Principled BSDF']


# ─────────────────────────────── metals ───────────────────────────────


def anodised(color='#8e949b', roughness=0.32, anisotropy=0.6, axis='Z', grain=3000.0, grain_amount=0.08, wear=0.0, name='Anodised'):
    """Anodised aluminium (a phone frame, a laptop lid, a speaker band): metal with the highlight stretched along
    the machining direction (radial about `axis` for turned parts), a satin that breathes very slightly with a fine
    noise (grain_amount 0–0.3: 0.08 is a smooth anodised deck, 0.25 reads bead-blasted), and optional edge wear
    (bare metal showing where the anodising rubbed off; Cycles only, 0–1)."""
    m, nt, b = new(name, **{'Base Color': col(color), 'Metallic': 1.0, 'Roughness': roughness, 'Anisotropic': anisotropy})
    nt.links.new(tangent(nt, axis), b.inputs['Tangent'])
    v = coords(nt, 1.0)
    r = maprange(nt, noise(nt, v, grain, 2.0, 0.6), 0.35, 0.65, roughness * (1 - grain_amount), roughness * (1 + grain_amount))
    nt.links.new(r, b.inputs['Roughness'])
    if wear > 0:
        edge_wear(m, wear, color='#d9dde2')
    return m


def brushed_steel(color='#c9ced4', roughness=0.35, anisotropy=0.75, axis='Z', mode='RADIAL', name='BrushedSteel'):
    """Brushed stainless steel: a stronger anisotropy than anodised aluminium, visible brush lines as a fine bump.
    mode 'RADIAL' about `axis` for turned parts (a watch bezel), 'UV_MAP' for flat sheet brushed along the UV U
    direction (needs UVs)."""
    m, nt, b = new(name, **{'Base Color': col(color), 'Metallic': 1.0, 'Roughness': roughness, 'Anisotropic': anisotropy})
    nt.links.new(tangent(nt, axis, mode), b.inputs['Tangent'])
    v = coords(nt, 1.0, rotation=(0, 0, 0))
    lines = node(nt, 'ShaderNodeTexWave', Scale=600.0, Distortion=0.6, Detail=1.0, props={'wave_type': 'BANDS', 'bands_direction': 'X' if axis == 'Z' else 'Z'})
    nt.links.new(v, lines.inputs['Vector'])
    nt.links.new(bump(nt, lines.outputs['Fac'], 0.04, 0.0003), b.inputs['Normal'])
    return m


def sandblasted(color='#b7bcc3', roughness=0.48, name='Sandblasted'):
    """Bead-blasted metal (titanium, raw aluminium): an even, matte metal whose roughness is a dense micro-noise —
    reads as soft and expensive under a big light."""
    m, nt, b = new(name, **{'Base Color': col(color), 'Metallic': 1.0, 'Roughness': roughness, 'Specular Tint': (1, 1, 1, 1)})
    v = coords(nt, 1.0)
    n = noise(nt, v, 2500.0, 3.0, 0.7)
    nt.links.new(maprange(nt, n, 0.3, 0.7, roughness * 0.8, roughness * 1.15), b.inputs['Roughness'])
    nt.links.new(bump(nt, n, 0.08, 0.0001), b.inputs['Normal'])
    return m


def chrome(color='#e8eaec', roughness=0.05, name='Chrome'):
    m, nt, b = new(name, **{'Base Color': col(color), 'Metallic': 1.0, 'Roughness': roughness})
    return m


def gold(color='#e2b36a', roughness=0.22, name='Gold'):
    m, nt, b = new(name, **{'Base Color': col(color), 'Metallic': 1.0, 'Roughness': roughness})
    return m


# ─────────────────────────────── plastics, paint, ceramics ───────────────────────────────


def soft_touch(color='#1b1d21', roughness=0.55, sheen=0.25, texture=0.03, name='SoftTouch'):
    """Soft-touch (rubberised) plastic: matte, a faint sheen at grazing angles, a fine grain in the normal."""
    m, nt, b = new(name, **{'Base Color': col(color), 'Roughness': roughness, 'Sheen Weight': sheen, 'Sheen Roughness': 0.5,
                            'Specular IOR Level': 0.4})
    if texture > 0:
        v = coords(nt, 1.0)
        nt.links.new(bump(nt, noise(nt, v, 1800.0, 2.0, 0.6), texture, 0.0001), b.inputs['Normal'])
    return m


def gloss_plastic(color='#f2f2f0', roughness=0.12, coat=0.7, name='GlossPlastic'):
    """Injection-moulded glossy plastic (a white earbud case, a keycap): a clear coat over a coloured base."""
    m, nt, b = new(name, **{'Base Color': col(color), 'Roughness': roughness, 'Coat Weight': coat, 'Coat Roughness': 0.04,
                            'Specular IOR Level': 0.5})
    return m


def matte_plastic(color='#2a2d33', roughness=0.5, specular=0.45, name='MattePlastic'):
    """Matte moulded plastic (a keycap, a hinge cover): specular 0.3 is a dull part, 0.45 a slightly satin one."""
    m, nt, b = new(name, **{'Base Color': col(color), 'Roughness': roughness, 'Specular IOR Level': specular})
    return m


def car_paint(color='#0f3d8a', flake=0.4, flake_scale=4000.0, coat=1.0, name='CarPaint'):
    """Metallic paint: a coloured base with tiny flakes that sparkle (a normal perturbed by a dense Voronoi), under
    a deep clear coat."""
    m, nt, b = new(name, **{'Base Color': col(color), 'Metallic': 0.55, 'Roughness': 0.4, 'Coat Weight': coat, 'Coat Roughness': 0.03})
    v = coords(nt, 1.0)
    cells = voronoi(nt, v, flake_scale, 'F1', 1.0, 'Color')
    sep = nt.nodes.new('ShaderNodeSeparateColor'); nt.links.new(cells, sep.inputs['Color'])
    nt.links.new(bump(nt, sep.outputs['Red'], flake * 0.5, 0.00005), b.inputs['Normal'])
    return m


def ceramic(color='#f4f1ea', roughness=0.1, name='Ceramic'):
    """Glazed ceramic: a gloss coat with a little subsurface softness underneath."""
    m, nt, b = new(name, **{'Base Color': col(color), 'Roughness': roughness, 'Coat Weight': 0.8, 'Coat Roughness': 0.05,
                            'Subsurface Weight': 0.15, 'Subsurface Radius': (0.004, 0.003, 0.002), 'Subsurface Scale': 0.01})
    return m


def rubber(color='#202224', roughness=0.72, name='Rubber'):
    m, nt, b = new(name, **{'Base Color': col(color), 'Roughness': roughness, 'Specular IOR Level': 0.3})
    v = coords(nt, 1.0)
    nt.links.new(bump(nt, noise(nt, v, 900.0, 2.0, 0.6), 0.05, 0.0001), b.inputs['Normal'])
    return m


# ─────────────────────────────── glass ───────────────────────────────


def frosted_glass(tint='#f6f7fa', roughness=0.42, ior=1.49, absorption=0.0, name='FrostedGlass'):
    """Frosted / sandblasted glass or acrylic: light passes, what is behind blurs. absorption > 0 tints thick parts
    (a volume absorption with the tint's colour)."""
    m, nt, b = new(name, **{'Base Color': col(tint), 'Roughness': roughness, 'Transmission Weight': 1.0, 'IOR': ior})
    if absorption > 0:
        va = node(nt, 'ShaderNodeVolumeAbsorption', Color=col(tint), Density=absorption)
        nt.links.new(va.outputs['Volume'], nt.nodes['Material Output'].inputs['Volume'])
    _refraction(m)
    return m


def optical_glass(tint='#ffffff', ior=1.5, thin_film=0.0, roughness=0.0, name='Glass'):
    """Clear glass: a lens, a cover glass, a bottle. thin_film (nanometres, e.g. 300–500) adds the faint
    iridescence of a coated lens."""
    m, nt, b = new(name, **{'Base Color': col(tint), 'Roughness': roughness, 'Transmission Weight': 1.0, 'IOR': ior,
                            'Thin Film Thickness': thin_film})
    _refraction(m)
    return m


def _refraction(m):
    """EEVEE needs to be told a material refracts (ray-traced), and that it is not a thin wall."""
    for attr, v in (('use_raytrace_refraction', True), ('use_screen_refraction', True), ('thickness_mode', 'SPHERE')):
        try:
            setattr(m, attr, v)
        except Exception:
            pass


# ─────────────────────────────── fabric, leather ───────────────────────────────


def fabric(color='#3a3f47', weave=1000.0, sheen=0.7, roughness=0.75, name='Fabric'):
    """Woven textile (a speaker grille cloth, a strap): two crossed band patterns as a weave bump, a strong sheen."""
    m, nt, b = new(name, **{'Base Color': col(color), 'Roughness': roughness, 'Sheen Weight': sheen, 'Sheen Roughness': 0.4,
                            'Specular IOR Level': 0.3})
    v = coords(nt, 1.0)
    wx = node(nt, 'ShaderNodeTexWave', Scale=weave, Distortion=0.0, Detail=0.0, props={'wave_type': 'BANDS', 'bands_direction': 'X'})
    wy = node(nt, 'ShaderNodeTexWave', Scale=weave, Distortion=0.0, Detail=0.0, props={'wave_type': 'BANDS', 'bands_direction': 'Y'})
    nt.links.new(v, wx.inputs['Vector']); nt.links.new(v, wy.inputs['Vector'])
    h = math_node(nt, 'MULTIPLY', wx.outputs['Fac'], wy.outputs['Fac'])
    nt.links.new(bump(nt, h, 0.5, 0.0003), b.inputs['Normal'])
    return m


def leather(color='#4a2f22', grain=350.0, roughness=0.5, name='Leather'):
    """Leather: a cellular grain (Voronoi edge distance) in the normal and in the roughness."""
    m, nt, b = new(name, **{'Base Color': col(color), 'Roughness': roughness, 'Specular IOR Level': 0.45, 'Sheen Weight': 0.15})
    v = coords(nt, 1.0)
    cells = voronoi(nt, v, grain, 'DISTANCE_TO_EDGE')
    nt.links.new(bump(nt, cells, 0.3, 0.0003), b.inputs['Normal'])
    nt.links.new(maprange(nt, cells, 0.0, 0.08, roughness * 0.8, roughness * 1.1), b.inputs['Roughness'])
    return m


# ─────────────────────────────── screens, decals, emitters ───────────────────────────────


def screen_material(image_path, brightness=1.0, pixel_grid=0.0, pixels=(2560, 1600), reflection=0.5, name='Screen'):
    """A display: the screenshot as emission (brightness in nits-ish multiples), a faint pixel grid when the camera
    comes close (0–1), and a glossy cover glass that reflects the studio at grazing angles."""
    m, nt, b = new(name, **{'Base Color': (0.005, 0.005, 0.006, 1), 'Roughness': 0.08, 'Specular IOR Level': reflection,
                            'Coat Weight': 1.0, 'Coat Roughness': 0.02, 'Emission Strength': brightness})
    tex = nt.nodes.new('ShaderNodeTexImage'); tex.image = bpy.data.images.load(str(image_path), check_existing=True)
    tex.extension = 'CLIP'; tex.interpolation = 'Cubic'
    out = tex.outputs['Color']
    if pixel_grid > 0:
        uv = nt.nodes.new('ShaderNodeTexCoord')
        grid = node(nt, 'ShaderNodeTexBrick', Scale=1.0, props={'offset': 0.0, 'squash': 1.0})
        mp = nt.nodes.new('ShaderNodeMapping'); mp.inputs['Scale'].default_value = (pixels[0], pixels[1], 1.0)
        nt.links.new(uv.outputs['UV'], mp.inputs['Vector']); nt.links.new(mp.outputs['Vector'], grid.inputs['Vector'])
        grid.inputs['Mortar Size'].default_value = 0.08; grid.inputs['Color1'].default_value = (1, 1, 1, 1)
        grid.inputs['Color2'].default_value = (1, 1, 1, 1); grid.inputs['Mortar'].default_value = (1 - pixel_grid,) * 3 + (1,)
        out = mix(nt, out, grid.outputs['Color'], 1.0, 'RGBA', 'MULTIPLY')
    nt.links.new(out, b.inputs['Emission Color'])
    return m


def decal(image_path, name='Decal'):
    """For parts.decal_plane: the image's colour with its alpha as transparency (a printed label, a logo)."""
    m, nt, b = new(name, **{'Roughness': 0.35, 'Specular IOR Level': 0.4})
    tex = nt.nodes.new('ShaderNodeTexImage'); tex.image = bpy.data.images.load(str(image_path), check_existing=True)
    tex.extension = 'CLIP'
    nt.links.new(tex.outputs['Color'], b.inputs['Base Color']); nt.links.new(tex.outputs['Alpha'], b.inputs['Alpha'])
    for attr, v in (('surface_render_method', 'DITHERED'), ('blend_method', 'HASHED')):
        try:
            setattr(m, attr, v)
        except Exception:
            pass
    return m


def led(color='#e8ff5a', strength=30.0, name='LED'):
    """A light-emitting part (a status LED, a light ring, a backlit legend)."""
    m, nt, b = new(name, **{'Base Color': (0, 0, 0, 1), 'Emission Color': col(color), 'Emission Strength': strength})
    return m


# ─────────────────────────────── imperfection layers ───────────────────────────────


def fingerprints(m, amount=0.3, scale=6.0, seed=0.0):
    """Smudges on a glossy surface: patches where the roughness rises (0–1). Keep it low: 0.2–0.4."""
    nt = m.node_tree; b = _bsdf(m)
    v = coords(nt, 1.0)
    mp = nt.nodes.new('ShaderNodeMapping'); mp.inputs['Location'].default_value = (seed, seed * 0.7, 0)
    nt.links.new(v, mp.inputs['Vector'])
    patches = maprange(nt, noise(nt, mp.outputs['Vector'], scale, 6.0, 0.75, 0.3), 0.45, 0.62, 0.0, 1.0, smooth=True)
    smear = noise(nt, mp.outputs['Vector'], scale * 40, 3.0, 0.5)
    mask = math_node(nt, 'MULTIPLY', patches, maprange(nt, smear, 0.3, 0.7, 0.3, 1.0))
    r = tap(nt, b, 'Roughness')
    set_input(nt, b, 'Roughness', math_node(nt, 'ADD', r, math_node(nt, 'MULTIPLY', mask, amount * 0.35), clamp=True))
    return m


def dust(m, amount=0.3, scale=3000.0, color='#d8d4cc'):
    """Fine dust settling on upward-facing surfaces: a soft veil (not speckles) that lifts the colour and roughness
    where the normal points up, thicker in some patches than others."""
    nt = m.node_tree; b = _bsdf(m)
    geo = nt.nodes.new('ShaderNodeNewGeometry')
    sep = nt.nodes.new('ShaderNodeSeparateXYZ'); nt.links.new(geo.outputs['Normal'], sep.inputs['Vector'])
    up = maprange(nt, sep.outputs['Z'], 0.2, 0.9, 0.0, 1.0, smooth=True)
    v = coords(nt, 1.0)
    grains = maprange(nt, noise(nt, v, scale, 3.0, 0.7), 0.3, 0.8, 0.1, 1.0)
    patches = maprange(nt, noise(nt, v, 8.0, 3.0, 0.6), 0.35, 0.7, 0.2, 1.0)
    mask = math_node(nt, 'MULTIPLY', math_node(nt, 'MULTIPLY', math_node(nt, 'MULTIPLY', up, grains), patches), amount * 0.6)
    c = tap(nt, b, 'Base Color')
    set_input(nt, b, 'Base Color', mix(nt, c, col(color), mask, 'RGBA'))
    r = tap(nt, b, 'Roughness')
    set_input(nt, b, 'Roughness', math_node(nt, 'ADD', r, math_node(nt, 'MULTIPLY', mask, 0.5), clamp=True))
    return m


def scratches(m, amount=0.25, scale=220.0, depth=0.00005):
    """Hairline scratches: thin, long, random lines that catch the light (roughness and a tiny bump)."""
    nt = m.node_tree; b = _bsdf(m)
    v = coords(nt, 1.0)
    lines = node(nt, 'ShaderNodeTexWave', Scale=scale, Distortion=12.0, Detail=3.0, props={'wave_type': 'BANDS', 'bands_direction': 'DIAGONAL'})
    nt.links.new(v, lines.inputs['Vector'])
    thin = maprange(nt, lines.outputs['Fac'], 0.96, 1.0, 0.0, 1.0)
    where = maprange(nt, noise(nt, v, 3.0, 2.0, 0.5), 0.4, 0.6, 0.0, 1.0)
    mask = math_node(nt, 'MULTIPLY', math_node(nt, 'MULTIPLY', thin, where), amount)
    r = tap(nt, b, 'Roughness')
    set_input(nt, b, 'Roughness', math_node(nt, 'ADD', r, math_node(nt, 'MULTIPLY', mask, 0.7), clamp=True))
    n = b.inputs['Normal'].links[0].from_socket if b.inputs['Normal'].is_linked else None
    set_input(nt, b, 'Normal', bump(nt, mask, 1.0, depth, n))
    return m


def edge_wear(m, amount=0.3, color='#d9dde2', radius=0.004, roughness=0.3):
    """Worn edges (Cycles): its Bevel node rounds the geometry by `radius` — wider than the mesh's own bevels — and
    where that rounded normal leaves the shading normal is an edge; convex corners (pointiness) add to it. There a
    bare colour and a smoother roughness show through, broken up by a fine noise. Flat and gently curved surfaces
    stay untouched; EEVEE has neither node, so there the wear is invisible."""
    nt = m.node_tree; b = _bsdf(m)
    geo = nt.nodes.new('ShaderNodeNewGeometry')
    bev = node(nt, 'ShaderNodeBevel', Radius=radius); bev.samples = 8
    dot2 = nt.nodes.new('ShaderNodeVectorMath'); dot2.operation = 'DOT_PRODUCT'
    nt.links.new(bev.outputs['Normal'], dot2.inputs[0]); nt.links.new(geo.outputs['Normal'], dot2.inputs[1])
    creases = maprange(nt, dot2.outputs['Value'], 0.998, 0.93, 0.0, 1.0)
    pointy = maprange(nt, geo.outputs['Pointiness'], 0.52, 0.62, 0.0, 1.0)
    edge = math_node(nt, 'MAXIMUM', creases, pointy)
    v = coords(nt, 1.0)
    breakup = maprange(nt, noise(nt, v, 300.0, 3.0, 0.6), 0.3, 0.6, 0.0, 1.0)
    mask = math_node(nt, 'MULTIPLY', math_node(nt, 'MULTIPLY', edge, breakup), amount * 2.0, clamp=True)
    c = tap(nt, b, 'Base Color')
    set_input(nt, b, 'Base Color', mix(nt, c, col(color), mask, 'RGBA'))
    r = tap(nt, b, 'Roughness')
    set_input(nt, b, 'Roughness', mix(nt, r, roughness, mask, 'FLOAT'))
    return m


def tint(m, color, amount=1.0):
    """Shift an existing material's base colour towards `color` (reuse one recipe in the film's palette)."""
    nt = m.node_tree; b = _bsdf(m)
    c = tap(nt, b, 'Base Color')
    set_input(nt, b, 'Base Color', mix(nt, c, col(color), amount, 'RGBA'))
    return m


def simple(name, color='#808080', roughness=0.5, metallic=0.0):
    """A plain Principled material. → (material, bsdf)"""
    m, nt, b = new(name, Roughness=roughness, Metallic=metallic)
    b.inputs['Base Color'].default_value = col(color)
    m.diffuse_color = col(color)
    return m, b


# ─────────────────────────────── characters ───────────────────────────────


def _image(nt, path, label, color=True):
    tex = nt.nodes.new('ShaderNodeTexImage')
    tex.image = bpy.data.images.load(str(path), check_existing=True)
    if not color:
        tex.image.colorspace_settings.name = 'Non-Color'
    tex.label = label
    return tex


def skin(name='Skin', color='#c9a083', color_image=None, roughness=0.42, roughness_image=None,
         normal_image=None, normal_strength=0.6, sss_scale=0.012, sss_radius=(1.0, 0.25, 0.1), pores=0.35):
    """Skin: random-walk subsurface, optional painted colour / roughness / relief maps, fine procedural pores.
    sss_scale is in metres: 0.012 suits a head, about 0.005 a hand; raise it for stylized characters."""
    m, nt, b = new(name, Roughness=roughness)
    b.subsurface_method = 'RANDOM_WALK_SKIN'
    b.inputs['Subsurface Weight'].default_value = 1.0
    b.inputs['Subsurface Scale'].default_value = sss_scale
    b.inputs['Subsurface Radius'].default_value = sss_radius
    b.inputs['Subsurface IOR'].default_value = 1.4
    b.inputs['Base Color'].default_value = col(color)
    b.inputs['Specular IOR Level'].default_value = 0.45
    b.inputs['Coat Weight'].default_value = 0.08
    b.inputs['Coat Roughness'].default_value = 0.35
    if color_image:
        nt.links.new(_image(nt, color_image, 'Skin color').outputs['Color'], b.inputs['Base Color'])
    if roughness_image:
        nt.links.new(_image(nt, roughness_image, 'Skin roughness', color=False).outputs['Color'], b.inputs['Roughness'])
    normal_socket = None
    if normal_image:
        nm = node(nt, 'ShaderNodeNormalMap', Strength=normal_strength)
        nt.links.new(_image(nt, normal_image, 'Skin relief', color=False).outputs['Color'], nm.inputs['Color'])
        normal_socket = nm.outputs['Normal']
    if pores > 0:
        grain = noise(nt, coords(nt, scale=1.0), scale=2200.0, detail=3.0, roughness=0.6)
        normal_socket = bump(nt, grain, strength=0.08 * pores, distance=0.0004, normal=normal_socket)
    if normal_socket is not None:
        nt.links.new(normal_socket, b.inputs['Normal'])
    return m


def eye(name='Eye', iris_color='#5a3a1e', iris_image=None, sclera='#f1ece6', pupil=0.22, iris_size=0.46, veins=0.25):
    """One material for a whole eyeball, radial from the UV centre: pupil, iris, then sclera, with a wet coat.
    iris_size is the iris diameter in UV units. For a two-piece eye add optical_glass() on a larger cornea shell."""
    m, nt, b = new(name, Roughness=0.3)
    b.inputs['Coat Weight'].default_value = 1.0
    b.inputs['Coat Roughness'].default_value = 0.03
    b.inputs['Specular IOR Level'].default_value = 0.6
    tc = nt.nodes.new('ShaderNodeTexCoord')
    centred = node(nt, 'ShaderNodeVectorMath', props={'operation': 'SUBTRACT'})
    centred.inputs[1].default_value = (0.5, 0.5, 0.0)
    nt.links.new(tc.outputs['UV'], centred.inputs[0])
    length = node(nt, 'ShaderNodeVectorMath', props={'operation': 'LENGTH'})
    nt.links.new(centred.outputs['Vector'], length.inputs[0])
    radius = length.outputs['Value']
    iris_edge = iris_size * 0.5
    iris_mask = maprange(nt, radius, iris_edge - 0.012, iris_edge, 1.0, 0.0, smooth=True)
    pupil_mask = maprange(nt, radius, pupil * iris_edge - 0.006, pupil * iris_edge, 1.0, 0.0, smooth=True)
    if iris_image:
        scaled = node(nt, 'ShaderNodeVectorMath', props={'operation': 'SCALE'})
        scaled.inputs['Scale'].default_value = 1.0 / iris_size
        nt.links.new(centred.outputs['Vector'], scaled.inputs[0])
        recentre = node(nt, 'ShaderNodeVectorMath', props={'operation': 'ADD'})
        recentre.inputs[1].default_value = (0.5, 0.5, 0.0)
        nt.links.new(scaled.outputs['Vector'], recentre.inputs[0])
        tex = _image(nt, iris_image, 'Iris')
        nt.links.new(recentre.outputs['Vector'], tex.inputs['Vector'])
        iris = tex.outputs['Color']
    else:
        fibres = noise(nt, tc.outputs['UV'], scale=60.0, detail=4.0, roughness=0.7)
        iris = mix(nt, col(iris_color), (0.25, 0.2, 0.18, 1), fac=fibres, blend='MULTIPLY')
    white = col(sclera)
    if veins > 0:
        web = voronoi(nt, tc.outputs['UV'], scale=14.0, output='Distance')
        white = mix(nt, white, (0.78, 0.35, 0.33, 1), fac=maprange(nt, web, 0.0, 0.03, veins * 0.6, 0.0))
    surface = mix(nt, white, iris, fac=iris_mask)
    final = mix(nt, surface, (0.0, 0.0, 0.0, 1), fac=pupil_mask)
    nt.links.new(final, b.inputs['Base Color'])
    nt.links.new(maprange(nt, iris_mask, 0.0, 1.0, 0.25, 0.45), b.inputs['Roughness'])
    return m


def teeth(name='Teeth', color='#f0e8da', roughness=0.28):
    m, nt, b = new(name, Roughness=roughness)
    b.subsurface_method = 'RANDOM_WALK'
    b.inputs['Base Color'].default_value = col(color)
    b.inputs['Subsurface Weight'].default_value = 0.6
    b.inputs['Subsurface Scale'].default_value = 0.004
    b.inputs['Subsurface Radius'].default_value = (0.8, 0.7, 0.5)
    b.inputs['Coat Weight'].default_value = 0.5
    b.inputs['Coat Roughness'].default_value = 0.1
    return m


def gums(name='Gums', color='#b5565a'):
    m, nt, b = new(name, Roughness=0.35)
    b.subsurface_method = 'RANDOM_WALK'
    b.inputs['Base Color'].default_value = col(color)
    b.inputs['Subsurface Weight'].default_value = 0.8
    b.inputs['Subsurface Scale'].default_value = 0.006
    b.inputs['Coat Weight'].default_value = 0.4
    return m
