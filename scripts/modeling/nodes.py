"""Short helpers for building shader node trees by hand: one call per node, sockets or plain values as inputs."""
import bpy


def put(sock, value):
    """Link a socket into `sock`, or set its value."""
    if isinstance(value, bpy.types.NodeSocket):
        sock.id_data.links.new(value, sock)
    else:
        sock.default_value = value


def N(nt, kind, **props):
    n = nt.nodes.new(kind)
    for k, v in props.items():
        setattr(n, k, v)
    return n


def math_(nt, op, a, b=None, clamp=False):
    n = N(nt, "ShaderNodeMath", operation=op, use_clamp=clamp)
    put(n.inputs[0], a)
    if b is not None:
        put(n.inputs[1], b)
    return n.outputs[0]


def mixc(nt, fac, a, b, blend="MIX"):
    """Colours: a where fac is 0, b where it is 1."""
    n = N(nt, "ShaderNodeMix", data_type="RGBA", blend_type=blend)
    put(n.inputs[0], fac)
    put(n.inputs[6], a)
    put(n.inputs[7], b)
    return n.outputs[2]


def mixf(nt, fac, a, b):
    n = N(nt, "ShaderNodeMix", data_type="FLOAT")
    put(n.inputs[0], fac)
    put(n.inputs[2], a)
    put(n.inputs[3], b)
    return n.outputs[0]


def smooth(nt, v, a, b, lo=0.0, hi=1.0):
    """0 below a, 1 above b (or the other way round when b < a), smoothly between."""
    n = N(nt, "ShaderNodeMapRange", interpolation_type="SMOOTHSTEP")
    put(n.inputs["Value"], v)
    put(n.inputs["From Min"], a)
    put(n.inputs["From Max"], b)
    put(n.inputs["To Min"], lo)
    put(n.inputs["To Max"], hi)
    return n.outputs["Result"]


def noise_(nt, vec, scale, detail=4.0, rough=0.55):
    n = N(nt, "ShaderNodeTexNoise")
    if vec is not None:
        put(n.inputs["Vector"], vec)
    put(n.inputs["Scale"], scale)
    put(n.inputs["Detail"], detail)
    put(n.inputs["Roughness"], rough)
    return n.outputs["Fac"]


def new_mat(name):
    """An empty material: (material, node tree, output node)."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    return m, nt, N(nt, "ShaderNodeOutputMaterial")


def principled(mat):
    return next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None) if mat and mat.use_nodes else None


def has_alpha(mat):
    p = principled(mat)
    return bool(p and p.inputs["Alpha"].is_linked)
