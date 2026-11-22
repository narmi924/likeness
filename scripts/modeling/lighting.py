"""wb3d.lighting — a studio's lights as named tools, placed relative to the subject so the same rig works for a
15 cm speaker and a 2 m installation: softboxes, strips, top light, bounce cards and black flags, a gradient
backdrop, HDRI environments that light without being seen, light linking (a rim that touches only the hero),
atmosphere. Energies are watts as Blender's lights take them, scaled by the rig to the subject's size.

    rig = W.studio_rig(body, kind='product')              # key softbox, two strips, top fill, a card, a backdrop
    W.only_lights(rig['rim_l'], [body])                   # the rim draws the hero's edge, not the floor
    W.environment(hdri_path, strength=0.6, seen=False)    # reflections from a real room, background stays the film's
"""
import math

import bpy
from mathutils import Vector

from .core import link, world_bounds
from .shading import col, node, maprange


# ─────────────────────────────── single lights ───────────────────────────────


def _aim(o, target):
    d = Vector(target) - Vector(o.location)
    o.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()
    o['wb3d_target'] = list(target)
    return o


def kelvin(temp):
    """Colour of a light at a temperature in kelvin (linear RGB, 1000–12000 K) — 3200 tungsten, 5600 daylight."""
    t = max(1000.0, min(12000.0, float(temp))) / 100.0
    r = 255.0 if t <= 66 else 329.698727446 * ((t - 60) ** -0.1332047592)
    g = 99.4708025861 * math.log(t) - 161.1195681661 if t <= 66 else 288.1221695283 * ((t - 60) ** -0.0755148492)
    b = 255.0 if t >= 66 else (0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307)
    srgb = [max(0.0, min(255.0, v)) / 255.0 for v in (r, g, b)]
    return tuple(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in srgb)


def _color(c, temp):
    if temp is not None:
        return kelvin(temp)
    return col(c)[:3]


def softbox(name, at, target, size=(1.0, 1.0), energy=300.0, color='#ffffff', temp=None, spread=120.0):
    """A rectangular soft source. spread (degrees) narrows the beam like a grid on a softbox: 180 = bare panel,
    60–90 = gridded, directional."""
    l = bpy.data.lights.new(name, 'AREA'); l.shape = 'RECTANGLE'; l.size, l.size_y = size
    l.energy = energy; l.color = _color(color, temp); l.spread = math.radians(spread)
    o = link(bpy.data.objects.new(name, l)); o.location = at
    return _aim(o, target)


def strip(name, at, target, length=1.0, width=0.1, energy=150.0, color='#ffffff', temp=None, spread=150.0):
    """A long, narrow source: the rim that draws a thin bright line along an edge, a reflection stripe on glass."""
    return softbox(name, at, target, (width, length), energy, color, temp, spread)


def disk(name, at, target, diameter=0.6, energy=200.0, color='#ffffff', temp=None, spread=180.0):
    l = bpy.data.lights.new(name, 'AREA'); l.shape = 'DISK'; l.size = diameter
    l.energy = energy; l.color = _color(color, temp); l.spread = math.radians(spread)
    o = link(bpy.data.objects.new(name, l)); o.location = at
    return _aim(o, target)


def spot(name, at, target, angle=40.0, blend=0.3, energy=400.0, radius=0.05, color='#ffffff', temp=None):
    l = bpy.data.lights.new(name, 'SPOT'); l.spot_size = math.radians(angle); l.spot_blend = blend
    l.energy = energy; l.shadow_soft_size = radius; l.color = _color(color, temp)
    o = link(bpy.data.objects.new(name, l)); o.location = at
    return _aim(o, target)


def point(name, at, energy=100.0, radius=0.05, color='#ffffff', temp=None):
    l = bpy.data.lights.new(name, 'POINT'); l.energy = energy; l.shadow_soft_size = radius; l.color = _color(color, temp)
    o = link(bpy.data.objects.new(name, l)); o.location = at
    return o


def sun(direction=(-35, 0, 40), strength=3.5, angle=3.0, color='#fff1dc', temp=None, name='Sun'):
    """Sunlight: parallel rays from (elevation tilt, roll, azimuth) in degrees; angle = how much sky the sun covers
    (bigger = softer shadows)."""
    l = bpy.data.lights.new(name, 'SUN'); l.energy = strength; l.angle = math.radians(angle); l.color = _color(color, temp)
    o = link(bpy.data.objects.new(name, l)); o.rotation_euler = tuple(math.radians(v) for v in direction)
    return o


# ─────────────────────────────── cards, flags, backdrops ───────────────────────────────


def _plane(name, at, target, size):
    me = bpy.data.meshes.new(name)
    w, h = size
    me.from_pydata([(-w / 2, -h / 2, 0), (w / 2, -h / 2, 0), (w / 2, h / 2, 0), (-w / 2, h / 2, 0)], [], [(0, 1, 2, 3)])
    o = link(bpy.data.objects.new(name, me)); o.location = at
    d = Vector(target) - Vector(at); o.rotation_euler = d.to_track_quat('Z', 'Y').to_euler()
    return o


def bounce_card(name, at, target, size=(1.0, 1.0), strength=2.0, color='#ffffff', feather=0.0):
    """A white card: a long soft reflection in metal and glass, and a gentle fill — seen by reflections, not the
    camera. Two things so both engines agree: an emissive plane that only reflections see, and an area light of the
    same size and brightness that only diffuse shading sees (EEVEE cannot be lit by an emissive plane). feather
    (0–1) fades the card out towards its edges, so glass and glossy plastic reflect a soft glow, not a hard-edged
    rectangle (0.35 is a scrim with a soft rim, 0.6 a pool of light)."""
    o = _plane(name, at, target, size)
    m = bpy.data.materials.new(name); m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes['Principled BSDF']; b.inputs['Base Color'].default_value = (0, 0, 0, 1)
    b.inputs['Emission Color'].default_value = col(color); b.inputs['Emission Strength'].default_value = strength
    if feather > 0:
        tc = nt.nodes.new('ShaderNodeTexCoord')
        mp = nt.nodes.new('ShaderNodeMapping'); mp.inputs['Location'].default_value = (-1.0, -1.0, 0.0)
        mp.inputs['Scale'].default_value = (2.0, 2.0, 0.0)                     # the plane's bounds as −1..1
        nt.links.new(tc.outputs['Generated'], mp.inputs['Vector'])
        ln = nt.nodes.new('ShaderNodeVectorMath'); ln.operation = 'LENGTH'; nt.links.new(mp.outputs['Vector'], ln.inputs[0])
        fac = maprange(nt, ln.outputs['Value'], 1.0 - min(0.999, feather), 1.0, 1.0, 0.0, smooth=True)
        mul = nt.nodes.new('ShaderNodeMath'); mul.operation = 'MULTIPLY'; mul.inputs[1].default_value = strength
        nt.links.new(fac, mul.inputs[0]); nt.links.new(mul.outputs[0], b.inputs['Emission Strength'])
    o.data.materials.append(m); o.visible_camera = False; o.visible_shadow = False; o.visible_diffuse = False
    l = bpy.data.lights.new(name + ' fill', 'AREA'); l.shape = 'RECTANGLE'; l.size, l.size_y = size
    l.energy = strength * size[0] * size[1] * math.pi; l.color = col(color)[:3]; l.spread = math.radians(180)
    lo = link(bpy.data.objects.new(name + ' fill', l)); lo.location = at; _aim(lo, target)
    lo.visible_glossy = False; lo.visible_camera = False; lo.parent = o; lo.matrix_parent_inverse = o.matrix_world.inverted()
    return o


def black_flag(name, at, target, size=(1.0, 1.0)):
    """A black flag: kills a reflection or a fill from one side (negative fill) — the dark edge that gives chrome and
    glass their shape. Seen by reflections, not the camera."""
    o = _plane(name, at, target, size)
    m = bpy.data.materials.new(name); m.use_nodes = True
    b = m.node_tree.nodes['Principled BSDF']; b.inputs['Base Color'].default_value = (0.005, 0.005, 0.005, 1)
    b.inputs['Roughness'].default_value = 0.9; b.inputs['Specular IOR Level'].default_value = 0.0
    o.data.materials.append(m); o.visible_camera = False; o.visible_shadow = False
    return o


def gradient_backdrop(top='#2a2f38', bottom='#0b0d10', at=(0, 3.0, 1.0), size=(12.0, 6.0), strength=1.0, name='Backdrop', seen=True):
    """A backdrop wall with a vertical gradient (lit top, dark bottom or the reverse): the long soft reflection that
    runs across a product's top surfaces, and the background the camera sees when `seen`."""
    o = _plane(name, at, (at[0], at[1] - 1.0, at[2]), size)
    m = bpy.data.materials.new(name); m.use_nodes = True; nt = m.node_tree
    b = nt.nodes['Principled BSDF']; b.inputs['Base Color'].default_value = (0, 0, 0, 1)
    tc = nt.nodes.new('ShaderNodeTexCoord'); g = node(nt, 'ShaderNodeTexGradient', props={'gradient_type': 'LINEAR'})
    mp = nt.nodes.new('ShaderNodeMapping'); mp.inputs['Rotation'].default_value = (0, 0, math.radians(90))
    mp.inputs['Location'].default_value = (0.5, 0, 0)
    nt.links.new(tc.outputs['Generated'], mp.inputs['Vector']); nt.links.new(mp.outputs['Vector'], g.inputs['Vector'])
    ramp = nt.nodes.new('ShaderNodeValToRGB'); ramp.color_ramp.elements[0].color = col(bottom); ramp.color_ramp.elements[1].color = col(top)
    nt.links.new(g.outputs['Fac'], ramp.inputs['Fac']); nt.links.new(ramp.outputs['Color'], b.inputs['Emission Color'])
    b.inputs['Emission Strength'].default_value = strength
    o.data.materials.append(m); o.visible_camera = bool(seen); o.visible_shadow = False
    return o


# ─────────────────────────────── environment and atmosphere ───────────────────────────────


def environment(hdri=None, strength=1.0, rotation=0.0, color='#101216', seen=None, seen_strength=1.0):
    """The world: an HDRI (or a flat colour) that lights and reflects. seen: a colour the camera sees instead
    (the film's background), so the HDRI gives the reflections while the frame stays clean; None = seen as is."""
    w = bpy.data.worlds.new('World'); bpy.context.scene.world = w; w.use_nodes = True
    nt = w.node_tree; bg = nt.nodes['Background']; out = nt.nodes['World Output']
    if hdri:
        env = nt.nodes.new('ShaderNodeTexEnvironment'); env.image = bpy.data.images.load(str(hdri), check_existing=True)
        mp = nt.nodes.new('ShaderNodeMapping'); tc = nt.nodes.new('ShaderNodeTexCoord')
        mp.inputs['Rotation'].default_value[2] = math.radians(rotation)
        nt.links.new(tc.outputs['Generated'], mp.inputs['Vector']); nt.links.new(mp.outputs['Vector'], env.inputs['Vector'])
        nt.links.new(env.outputs['Color'], bg.inputs['Color'])
    else:
        bg.inputs['Color'].default_value = col(color)
    bg.inputs['Strength'].default_value = strength
    if seen is not None:
        seen_bg = nt.nodes.new('ShaderNodeBackground'); seen_bg.inputs['Color'].default_value = col(seen)
        seen_bg.inputs['Strength'].default_value = seen_strength
        lp = nt.nodes.new('ShaderNodeLightPath'); mx = nt.nodes.new('ShaderNodeMixShader')
        nt.links.new(lp.outputs['Is Camera Ray'], mx.inputs['Fac'])
        nt.links.new(bg.outputs['Background'], mx.inputs[1]); nt.links.new(seen_bg.outputs['Background'], mx.inputs[2])
        for l in list(out.inputs['Surface'].links):
            nt.links.remove(l)
        nt.links.new(mx.outputs['Shader'], out.inputs['Surface'])
    return w


def atmosphere(density=0.02, anisotropy=0.3, color='#ffffff'):
    """Haze in the air (volume scatter on the world): light beams become visible, distance fades. Cycles renders it
    properly; EEVEE approximates. Keep density small (0.005–0.05 for a room-sized scene)."""
    w = bpy.context.scene.world
    if w is None:
        w = environment()
    nt = w.node_tree
    vol = node(nt, 'ShaderNodeVolumePrincipled', Color=col(color), Density=density, Anisotropy=anisotropy)
    nt.links.new(vol.outputs['Volume'], nt.nodes['World Output'].inputs['Volume'])
    return w


# ─────────────────────────────── light linking ───────────────────────────────


def only_lights(light, objects, exclude=None):
    """Light linking: this light lights only `objects` (a rim for the hero, not the floor); `exclude` instead lists
    objects it must not light while everything else is lit as usual."""
    name = f'{light.name} receivers'
    coll = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    if coll.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(coll)
    for o in list(objects or []) + list(exclude or []):
        if o.name not in coll.objects:
            coll.objects.link(o)
    light.light_linking.receiver_collection = coll
    for co in coll.collection_objects:
        co.light_linking.link_state = 'EXCLUDE' if (exclude and co.object in exclude) else 'INCLUDE'
    return coll


def no_shadow_from(light, objects):
    """Shadow linking: these objects cast no shadow from this light (a card that would otherwise shade the hero)."""
    name = f'{light.name} blockers'
    coll = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    if coll.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(coll)
    for o in objects:
        if o.name not in coll.objects:
            coll.objects.link(o)
    light.light_linking.blocker_collection = coll
    for co in coll.collection_objects:
        co.light_linking.link_state = 'EXCLUDE'
    return coll


# ─────────────────────────────── rigs ───────────────────────────────


def studio_rig(subject, kind='product', key=1.0, temp=None, backdrop=True, card=True, probe=True, tweaks=None):
    """A product studio around the subject (an object or a list), every distance and size a multiple of the
    subject's size so it works at any scale: a big softbox key from upper left, two strip rims from behind, a top
    fill, a bounce card camera-left, and a gradient backdrop. kinds: 'product' (clean, neutral), 'dark' (black
    backdrop, rims do the drawing), 'highkey' (white, airy), 'warm' (tungsten key, cool rims). `tweaks` overrides
    the kind's numbers: key_t / rim_t (kelvin), key_e / rim_e / top_e (relative energies), card_s (card strength),
    card_feather (0–1, soft-edged card), top_c / bot_c (backdrop colours). → {name: object}"""
    objs = subject if isinstance(subject, (list, tuple)) else [subject]
    los, his = zip(*[world_bounds(o) for o in objs])
    lo = Vector((min(v.x for v in los), min(v.y for v in los), min(v.z for v in los)))
    hi = Vector((max(v.x for v in his), max(v.y for v in his), max(v.z for v in his)))
    c = (lo + hi) / 2; s = max((hi - lo).length, 0.05)
    t = (c.x, c.y, c.z)
    presets = {
        'product': dict(key_t=temp or 5200, rim_t=6500, key_e=1.0, rim_e=0.45, top_e=0.25, card_s=1.5, top_c='#3a3f47', bot_c='#0c0e11'),
        'dark': dict(key_t=temp or 4800, rim_t=7000, key_e=0.6, rim_e=0.9, top_e=0.1, card_s=0.8, top_c='#15171b', bot_c='#030405'),
        'highkey': dict(key_t=temp or 5600, rim_t=5600, key_e=1.4, rim_e=0.3, top_e=0.6, card_s=2.5, top_c='#f4f4f2', bot_c='#dcdcd8'),
        'warm': dict(key_t=temp or 3400, rim_t=8000, key_e=1.0, rim_e=0.6, top_e=0.2, card_s=1.2, top_c='#3b2f28', bot_c='#0e0b09'),
    }[kind]
    presets.setdefault('card_feather', 0.0)
    presets.update(tweaks or {})
    # energies: an area light's watts scale with distance² — the rig keeps the subject at the same exposure at any size
    e = 52.0 * s * s * key
    rig = {}
    rig['key'] = softbox('Key', (c.x - 2.2 * s, c.y - 2.0 * s, c.z + 2.6 * s), t, (3.0 * s, 3.0 * s), e * presets['key_e'], temp=presets['key_t'], spread=110)
    rig['rim_l'] = strip('Rim L', (c.x - 2.6 * s, c.y + 2.0 * s, c.z + 1.0 * s), t, 3.0 * s, 0.25 * s, e * presets['rim_e'], temp=presets['rim_t'])
    rig['rim_r'] = strip('Rim R', (c.x + 2.8 * s, c.y + 1.7 * s, c.z + 0.9 * s), t, 3.0 * s, 0.25 * s, e * presets['rim_e'] * 0.7, temp=presets['rim_t'])
    rig['top'] = softbox('Top', (c.x, c.y + 0.3 * s, c.z + 4.0 * s), t, (4.0 * s, 4.0 * s), e * presets['top_e'], temp=presets['key_t'], spread=160)
    if card:
        rig['card'] = bounce_card('Card', (c.x - 2.0 * s, c.y - 0.6 * s, c.z + 1.0 * s), t, (3.0 * s, 3.0 * s), presets['card_s'],
                                  feather=presets['card_feather'])
    if backdrop:
        rig['backdrop'] = gradient_backdrop(presets['top_c'], presets['bot_c'], at=(c.x, c.y + 8.0 * s, c.z + 2.5 * s), size=(40 * s, 16 * s))
    if probe:
        rig['probe'] = reflection_probe((c.x, c.y, c.z), 12.0 * s)
    return rig


def reflection_probe(at, distance=5.0, name='Reflections'):
    """A sphere reflection probe: what EEVEE's metals and glass reflect when the card, backdrop and lights are off
    screen (Cycles sees them anyway). Covers `distance` metres around `at`."""
    probe = bpy.data.lightprobes.new(name, 'SPHERE')
    try:
        probe.influence_distance = distance; probe.falloff = 0.5
    except Exception:
        pass
    o = link(bpy.data.objects.new(name, probe)); o.location = at
    return o
