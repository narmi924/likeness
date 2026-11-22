"""wb3d.core — the few object-level helpers every other module uses: link an object into the scene, give it a
material, shade it, apply its modifiers without touching the UI context, duplicate, join, remove."""
import math
from itertools import product

import bpy
import bmesh
import numpy as np
from mathutils import Vector, Matrix


def link(obj):
    if obj.name not in bpy.context.scene.collection.objects:
        bpy.context.scene.collection.objects.link(obj)
    return obj


def assign(obj, material):
    """The object's one material (None leaves what it has)."""
    if material is not None and getattr(obj, 'data', None) is not None and hasattr(obj.data, 'materials'):
        obj.data.materials.clear(); obj.data.materials.append(material)
    return obj


def smooth(obj, angle=30.0):
    """Smooth shading with edges sharper than `angle` degrees kept crisp — the right look for hard surfaces with
    bevels: the bevel reads as a highlight, the flat faces stay flat."""
    me = getattr(obj, 'data', None)
    if me is None or obj.type != 'MESH':
        return obj
    me.shade_smooth()
    if angle is not None:
        me.set_sharp_from_angle(angle=math.radians(angle))
    return obj


def smooth_shade(obj):
    """Plain smooth shading on every face (curved things without sharp edges)."""
    me = getattr(obj, 'data', None)
    if me is not None and obj.type == 'MESH':
        me.shade_smooth()
    return obj


def mesh_object(name, bm, material=None, location=(0, 0, 0)):
    """A new mesh object from a bmesh (freed), linked, placed."""
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me); bm.free()
    o = link(bpy.data.objects.new(name, me)); o.location = location
    return assign(o, material)


def apply_all(obj):
    """Bake the object's modifiers into its mesh (no UI context needed: the evaluated mesh replaces the original)."""
    if obj.type != 'MESH' or not obj.modifiers:
        return obj
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=dg)
    me.name = obj.data.name + '.applied'
    mats = [m for m in obj.data.materials]
    old = obj.data
    obj.modifiers.clear()
    obj.data = me
    if not me.materials and mats:
        for m in mats:
            me.materials.append(m)
    if old.users == 0:
        bpy.data.meshes.remove(old)
    return obj


def evaluated_mesh(obj):
    """A bmesh of the object as it renders (modifiers applied), in world space."""
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    bm = bmesh.new()
    bm.from_mesh(ev.to_mesh())
    bm.transform(obj.matrix_world)
    ev.to_mesh_clear()
    return bm


def duplicate(obj, name=None, linked=False):
    o = obj.copy()
    if not linked and obj.data is not None:
        o.data = obj.data.copy()
    o.name = name or obj.name + '.copy'
    o.animation_data_clear()
    return link(o)


def remove(*objs):
    for o in objs:
        if o is None:
            continue
        data = getattr(o, 'data', None)
        try:
            bpy.data.objects.remove(o, do_unlink=True)
        except ReferenceError:
            continue
        if data is not None and data.users == 0:
            coll = {'MESH': bpy.data.meshes, 'CURVE': bpy.data.curves, 'FONT': bpy.data.curves}.get(getattr(data, 'bl_rna', None) and type(data).__name__.upper(), None)
            try:
                (bpy.data.meshes if isinstance(data, bpy.types.Mesh) else bpy.data.curves).remove(data)
            except Exception:
                pass


def join(objects, name=None, material=None):
    """One mesh object from several (modifiers applied first); the originals go."""
    objects = [o for o in objects if o is not None]
    if not objects:
        return None
    bm = bmesh.new()
    for o in objects:
        apply_all(o)
        tmp = bmesh.new(); tmp.from_mesh(o.data); tmp.transform(o.matrix_world)
        me = bpy.data.meshes.new('tmp'); tmp.to_mesh(me); tmp.free()
        bm.from_mesh(me); bpy.data.meshes.remove(me)
    out = mesh_object(name or objects[0].name, bm, material)
    if material is None:
        seen = []
        for o in objects:
            for m in o.data.materials:
                if m is not None and m not in seen:
                    seen.append(m)
        for m in seen:
            out.data.materials.append(m)
    remove(*objects)
    return out


def world_bounds(obj):
    """(lo, hi) corners of the object's bounding box in world space, modifiers included."""
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    pts = geometry_box(ev, ev.matrix_world, {})
    if not pts:
        return ev.matrix_world.translation.copy(), ev.matrix_world.translation.copy()
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def geometry_box(obj, matrix, cache):
    """Return tight bounds for an evaluated instance, including beveled curves."""
    if obj.type not in {"MESH","CURVE","SURFACE","FONT","META"}:
        return [matrix @ Vector(p) for p in obj.bound_box]
    key = (obj.original.as_pointer(), obj.data.as_pointer())
    if key not in cache:
        mesh = obj.data if obj.type == "MESH" else obj.to_mesh()
        try:
            coords = np.empty(len(mesh.vertices)*3 if mesh else 0, dtype=np.float32)
            if mesh: mesh.vertices.foreach_get("co", coords)
            cache[key] = coords.reshape(-1,3)
        finally:
            if obj.type != "MESH":
                obj.to_mesh_clear()
    if not len(cache[key]):
        return []
    transform = np.asarray(matrix)
    points = cache[key] @ transform[:3,:3].T + transform[:3,3]
    return [Vector(p) for p in product(*zip(points.min(axis=0), points.max(axis=0)))]


def frame(at, normal, up=None):
    """A 4×4 matrix whose Z axis is `normal` at `at`: parts built flat in XY (facing +Z) go onto a surface with it."""
    n = Vector(normal).normalized()
    if n.z > 0.9999:                                                   # a top face: the part's Y stays world +Y
        rot = Matrix.Identity(3)
    elif n.z < -0.9999:                                                # a bottom face: Y stays +Y, Z flips down
        rot = Matrix.Rotation(math.pi, 3, 'Y')
    else:                                                              # a side: the part's Y points up the surface
        rot = n.to_track_quat('Z', up or 'Y').to_matrix()
    return Matrix.Translation(Vector(at)) @ rot.to_4x4()


def set_parent(child, parent):
    """Parent without moving: the child keeps its world transform and follows the parent from here on (so place a
    part in world space first, then parent it to the piece that will carry it)."""
    bpy.context.view_layer.update()
    child.parent = parent
    child.matrix_parent_inverse = parent.matrix_world.inverted()
    return child


def islands(obj, name=None):
    """The object split into its connected pieces (glyphs of a word, parts of an imported model), each a closed
    object of its own; pieces that only touch along an edge are separated first. The original goes. → [objects]"""
    bm = bmesh.new(); bm.from_mesh(obj.data)
    nm = [e for e in bm.edges if not e.is_manifold and not e.is_boundary]
    if nm:
        bmesh.ops.split_edges(bm, edges=nm)
    bm.verts.ensure_lookup_table(); bm.faces.ensure_lookup_table()
    seen = set(); groups = []
    for f in bm.faces:
        if f.index in seen:
            continue
        stack = [f]; group = []
        while stack:
            g = stack.pop()
            if g.index in seen:
                continue
            seen.add(g.index); group.append(g)
            for e in g.edges:
                for n in e.link_faces:
                    if n.index not in seen:
                        stack.append(n)
        groups.append(group)
    out = []
    mats = [m for m in obj.data.materials]
    for i, group in enumerate(groups):
        part = bmesh.new()
        vmap = {}
        for f in group:
            vs = []
            for v in f.verts:
                if v.index not in vmap:
                    vmap[v.index] = part.verts.new(v.co)
                vs.append(vmap[v.index])
            try:
                nf = part.faces.new(vs); nf.material_index = f.material_index; nf.smooth = f.smooth
            except ValueError:
                continue
        o = mesh_object(f"{name or obj.name}.{i:02d}", part)
        for m in mats:
            o.data.materials.append(m)
        o.matrix_world = obj.matrix_world.copy()
        out.append(o)
    bm.free()
    remove(obj)
    return out


def inflate(obj, amount=2e-6):
    """Push every vertex out along its normal by `amount` (metres): pieces that merely touch come to overlap by a
    hair, which the exact boolean solver unions cleanly where coincident walls would leave a non-manifold edge."""
    bm = bmesh.new(); bm.from_mesh(obj.data); bm.verts.ensure_lookup_table()
    for v in bm.verts:
        v.co += v.normal * amount
    bm.to_mesh(obj.data); bm.free()
    return obj


def harden(obj, angle=30.0):
    """Hard-surface shading after geometry changed: smooth with sharp edges by angle, plus a live weighted-normal
    modifier so a big flat face stays flat right up to its bevel (without it, the long triangles a boolean leaves in
    an n-gon shade as dark wedges)."""
    smooth(obj, angle)
    if obj.type == 'MESH' and not any(m.type == 'WEIGHTED_NORMAL' for m in obj.modifiers):
        wn = obj.modifiers.new('Normals', 'WEIGHTED_NORMAL'); wn.keep_sharp = True
    return obj
