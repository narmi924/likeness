"""Exercise modeling operations inside Blender. Run through studio.py selftest."""

import time
import traceback
import tempfile
from pathlib import Path
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from modeling import (
    assets,
    building,
    core,
    gui,
    hair,
    layout,
    lighting,
    parts,
    plants,
    pose,
    profiles,
    retopo,
    rig,
    sculpt,
    shading,
    terrain,
)


def baked_sphere(folder):
    import numpy as np

    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=16)
    low = bpy.context.object
    low.name = "Baked sphere"
    low.data.shade_smooth()
    # The source uses its own denser topology and a real geometric displacement.
    high = low.copy()
    high.data = low.data.copy()
    bpy.context.collection.objects.link(high)
    high.name = "High detail sphere"
    sub = high.modifiers.new("Subdivision", "SUBSURF")
    sub.levels = sub.render_levels = 2
    noise = bpy.data.textures.new("Relief", type="CLOUDS")
    noise.noise_scale = 0.22
    disp = high.modifiers.new("Relief", "DISPLACE")
    disp.texture, disp.strength = noise, 0.065
    scene = bpy.context.scene
    scene.cycles.samples = 4
    paths = assets.bake(high, low, maps=("normal",), size=256, cage_extrusion=0.09, folder=folder)
    mat = low.active_material
    texture = next(n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE")
    data = np.empty(len(texture.image.pixels), dtype=np.float32)
    texture.image.pixels.foreach_get(data)
    assert data.reshape(-1, 4)[:, 0].std() > 0.025 and texture.image.packed_files
    normal = mat.node_tree.nodes.new("ShaderNodeNormalMap")
    mat.node_tree.links.new(texture.outputs["Color"], normal.inputs["Color"])
    mat.node_tree.links.new(normal.outputs["Normal"], mat.node_tree.nodes["Principled BSDF"].inputs["Normal"])
    high.hide_render = True
    high.hide_set(True)
    return low, paths


def run(quick=False):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    results, state = [], {}

    def exercise(name, fn):
        start = time.perf_counter()
        try:
            fn()
            status, detail = "OK", ""
        except Exception:
            status, detail = "FAIL", traceback.format_exc()
        results.append(
            {
                "module": name,
                "status": status,
                "seconds": round(time.perf_counter() - start, 3),
                "detail": detail,
            }
        )

    def geometry():
        cup = parts.lathe(profiles.cup(), steps=44)
        assert parts.check_topology(cup)["faces"]
        shell = parts.shell()
        parts.holes(shell, [(0, 0)], radius=0.003, through=True)
        parts.knurl(cup, 0.04, z=(0.02, 0.035), pitch=0.021)
        assert parts.check_topology(shell)["faces"] and parts.check_topology(cup)["faces"]

    def materials():
        for factory in (shading.brushed_steel, shading.optical_glass, shading.fabric):
            bpy.ops.mesh.primitive_cube_add()
            obj = bpy.context.object
            mat = factory()
            core.assign(obj, mat)
            assert obj.active_material == mat

    def lights():
        bpy.ops.mesh.primitive_cube_add()
        assert lighting.studio_rig(bpy.context.object)["key"].type == "LIGHT"

    def body():
        asset = assets.append_base("Body Male - Realistic")
        state["body"] = max(asset.all_objects, key=lambda o: len(o.data.vertices))
        bpy.context.view_layer.update()
        obj = state["body"]
        xs = [(obj.matrix_world @ Vector(p)).x for p in obj.bound_box]
        offset = (max(xs) + min(xs)) / 2
        for part in asset.all_objects:
            if not part.parent:
                part.location.x -= offset
        bpy.context.view_layer.update()
        assert len(state["body"].data.vertices) > 0

    def sculpt_body():
        obj = state["body"]
        tool = sculpt.Sculptor(obj)
        point = tuple(obj.data.vertices[0].co)
        tool.grab(point, 0.08, (0.003, 0, 0))
        tool.smooth(point, 0.08, strength=0.1)
        tool.commit()

    def cage():
        obj = state["body"]
        mesh = bpy.data.meshes.new("Cage patch")
        mesh.from_pydata([(0.01, 0, 1.2), (0.05, 0, 1.22), (0.04, 0.01, 1.3)], [], [(0, 1, 2)])
        geo = retopo.setup_cage(obj, mesh=mesh, mirror=False)
        retopo.project(geo, obj)
        assert len(geo.data.vertices) == 3

    def skeleton():
        obj = state["body"]
        meta = rig.add_metarig("basic")
        rig.fit_metarig(meta, rig.landmarks(obj))
        state["meta"] = meta
        problems = rig.check_metarig(meta, obj)
        assert not problems, problems
        if not quick:
            generated, error = rig.generate(meta)
            assert generated, error
            report = rig.bind(generated, obj)
            assert rig.weight_report(obj, generated)["ok"], report

    def reach():
        meta = state["meta"]
        a, b, c = ("upper_arm.L", "forearm.L", "hand.L")
        top, hand = [meta.matrix_world @ meta.pose.bones[n].head for n in (a, c)]
        target = top + (hand - top) * 0.83
        actual = Vector(pose.reach(meta, a, b, c, target, top + Vector((0, -1, 0))))
        assert (actual - target).length < 1e-4

    def groom():
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8)
        surface = bpy.context.object
        groom = hair.new_groom(surface)
        roots, normals = hair.sample_roots(surface, 0.45, max_roots=30)
        assert hair.add_guides(groom, roots, normals, 0.15) == len(roots) > 0

    def scatter():
        bpy.ops.mesh.primitive_plane_add(size=4)
        plane = bpy.context.object
        protos = bpy.data.collections.new("Scatter prototypes")
        for n in range(3):
            bpy.ops.mesh.primitive_uv_sphere_add(segments=8, ring_count=4, radius=0.1 + n * 0.02)
            obj = bpy.context.object
            for coll in list(obj.users_collection):
                coll.objects.unlink(obj)
            protos.objects.link(obj)
        emitter = layout.scatter(plane, protos, 3)
        bpy.context.view_layer.update()
        assert any(
            i.is_instance and i.parent and i.parent.original == emitter
            for i in bpy.context.evaluated_depsgraph_get().object_instances
        )

    def window():
        assert not gui.has_window()

    def bake_maps():
        with tempfile.TemporaryDirectory() as folder:
            baked_sphere(folder)

    def land():
        obj = terrain.ground((8, 8), 16, 2, seed=8, flatten=[((0, 0), 1.5)])
        bpy.context.view_layer.update()
        verts = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices
        assert all(abs(v.co.z) < 0.0001 for v in verts if v.co.x**2 + v.co.y**2 <= 2.25)
        assert max(v.co.z for v in verts) > 0.01
        assert terrain.rock(0.6, 1, 2).data.polygons
        assert terrain.water((2, 3)).data.polygons and terrain.sky().node_tree
        state["ground"] = obj

    def vegetation():
        obj = plants.tree(3, 0.09, levels=2, branches=(3, 4), seed=4)
        assert len(obj.data.splines) > 5 and obj.children[0].modifiers
        assert plants.bush(0.8, 2).children
        assert plants.grass(state["ground"], 0.6, 0.15).modifiers

    def characters():
        import numpy as np
        from modeling import fit, mesh, rigged

        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.1, segments=48, ring_count=32)
        head = bpy.context.object
        head.name = "Fit head"
        core.assign(head, shading.skin(pores=0.4))
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.012, location=(0.035, -0.09, 0.02))
        eye = bpy.context.object
        core.assign(eye, shading.eye())
        bpy.ops.mesh.primitive_cube_add(size=0.02, location=(0, -0.1, -0.04))
        core.assign(bpy.context.object, shading.teeth())
        pos, _ = mesh.world_geometry(head)
        local, tris, tree, uv = mesh.surface(head)
        assert tree.ray_cast((0, -1, 0), (0, 1, 0))[0] is not None and uv is not None
        paths = np.array([[[x, 0, 0.1], [x, 0, 0.12]] for x in np.linspace(-0.02, 0.02, 5)])
        assert hair.from_paths("Fuzz", paths, parent=head).type == "CURVES"
        # Landmarks on the front of the sphere; a photo camera that sees them, then a fit toward displaced targets.
        front = {n: int(i) for n, i in zip(("nose tip", "right eye outer", "left eye outer", "chin", "forehead",
                                             "mouth right", "mouth left", "glabella"),
                                            np.argsort(pos[:, 1])[:8])}
        fit.store_landmarks(head, front)
        scene = bpy.context.scene
        cam = bpy.data.objects.new("Fit cam", bpy.data.cameras.new("Fit cam"))
        scene.collection.objects.link(cam)
        cam.location, cam.rotation_euler = (0, -0.8, 0), (1.5708, 0, 0)
        cam.data.lens = 85
        cam["photo_size"] = [800, 800]
        seen = fit.project(scene, cam, fit.landmark_world(head))
        shifted = {n: [p[0] + 12.0, p[1]] for n, p in seen.items()}
        cam["photo_landmarks"] = __import__("json").dumps(shifted)
        before = fit.landmark_error(scene, cam, head)["rms_px"]
        fit.fit_to_photos(head, [cam], width=0.03)
        after = fit.landmark_error(scene, cam, head)["rms_px"]
        assert before > 10 and after < before * 0.4, (before, after)
        assert fit.transfer_delta(eye, head, rigid=True) >= 0
        rigged.shape_from_world_delta(head, "Plain", np.zeros_like(pos))
        assert rigged.find_bone.__name__ == "find_bone"

    def architecture():
        wall = building.wall([(0, 0), (4, 0), (4, 3)], openings=[(1.4, 0, 1, 2)])
        assert parts.check_topology(wall)["non_manifold_edges"] == 0
        assert building.window(1.2, 1.4).data.polygons and building.door(1, 2.2).data.polygons
        plan = [(0, 0), (4, 0), (4, 2), (2, 2), (2, 3), (0, 3)]
        for kind in ("flat", "gable", "hip"):
            roof = building.roof(plan, kind=kind)
            info = parts.check_topology(roof)
            assert info["non_manifold_edges"] == 0, (kind, info)
        for part in (building.stairs(4), building.column(0.2, 2, style="fluted"), building.slab(plan, 0.2)):
            assert parts.check_topology(part)["non_manifold_edges"] == 0
        assert building.railing([(0, 0), (3, 0)], 1, 1.2).data.splines

    for name, fn in (
        ("profiles / parts / core", geometry),
        ("shading", materials),
        ("lighting", lights),
        ("assets", body),
        ("sculpt", sculpt_body),
        ("retopo", cage),
        ("rig", skeleton),
        ("pose", reach),
        ("hair", groom),
        ("layout", scatter),
        ("gui", window),
        ("bake", bake_maps),
        ("terrain", land),
        ("plants", vegetation),
        ("building", architecture),
        ("mesh / rigged / fit / character shading", characters),
    ):
        exercise(name, fn)
    return {"ok": all(r["status"] == "OK" for r in results), "quick": quick, "modules": results}


if __name__ == "__main__":
    result = run("--quick" in sys.argv)
    for row in result["modules"]:
        print(f"{row['status']:4} {row['module']} ({row['seconds']:.3f} s)")
        if row["detail"]:
            print(row["detail"])
    if not result["ok"]:
        raise RuntimeError("A modeling operation failed.")
