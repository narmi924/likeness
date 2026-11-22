"""Blender gui tools adapted from Scenario Blender Skills."""
# Source: https://github.com/scenario-labs/skills/blob/754d1ceea270f1bf59651671b3471f385a5a80f9/skills/dcc/blender/scenario-blender-expert/scripts/bx_gui.py
# Copyright 2026 Scenario Inc. Distributed under the accompanying Scenario-MIT license.

import bpy
from bpy_extras import view3d_utils
from mathutils import Vector


BRUSH_FILES = {
    "SCULPT": "essentials_brushes-mesh_sculpt.blend",
    "TEXTURE_PAINT": "essentials_brushes-mesh_texture.blend",
    "VERTEX_PAINT": "essentials_brushes-mesh_vertex.blend",
    "WEIGHT_PAINT": "essentials_brushes-mesh_weight.blend",
    "SCULPT_CURVES": "essentials_brushes-curve_sculpt.blend",
}


STROKE_OPS = {
    "SCULPT": "sculpt.brush_stroke",
    "TEXTURE_PAINT": "paint.image_paint",
    "VERTEX_PAINT": "paint.vertex_paint",
    "WEIGHT_PAINT": "paint.weight_paint",
    "SCULPT_CURVES": "sculpt_curves.brush_stroke",
}


PAINT_SETTINGS = {"SCULPT": "sculpt", "TEXTURE_PAINT": "image_paint",
                  "VERTEX_PAINT": "vertex_paint", "WEIGHT_PAINT": "weight_paint"}


def has_window():
    return not bpy.app.background and bool(bpy.context.window_manager.windows)


def view3d():
    """(window, area, region, region_3d) of the first 3D viewport."""
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                region = next(r for r in area.regions if r.type == "WINDOW")
                return win, area, region, area.spaces.active.region_3d
    raise RuntimeError("no 3D viewport: bx_gui needs a GUI session (use bx_sculpt headless)")


def run(op_path, **kwargs):
    """Call any operator with a 3D-view context, e.g. run('sculpt.face_sets_init', mode='LOOSE_PARTS')."""
    win, area, region, _ = view3d()
    mod, name = op_path.split(".")
    with bpy.context.temp_override(window=win, area=area, region=region):
        return getattr(getattr(bpy.ops, mod), name)(**kwargs)


def set_view(axis="FRONT", frame=None):
    """axis: FRONT BACK LEFT RIGHT TOP BOTTOM. frame=object to zoom on (selected alone).
    view3d.view_selected does not frame the object from Sculpt/paint modes (found
    2026-09-24: a 30 px stroke then covered the whole head and flattened it), so the
    framing happens in Object Mode and the previous mode is restored afterwards."""
    if frame is not None:
        mode = frame.mode
        if mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for o in bpy.context.selected_objects:
            o.select_set(False)
        frame.select_set(True)
        bpy.context.view_layer.objects.active = frame
    # Smooth View animates the camera over ~200 ms; strokes projected before it ends
    # land in the old view and miss. Disable it while framing.
    prefs = bpy.context.preferences.view
    smooth = prefs.smooth_view
    prefs.smooth_view = 0
    try:
        run("view3d.view_axis", type=axis)
        if frame is not None:
            run("view3d.view_selected")
    finally:
        prefs.smooth_view = smooth
    if frame is not None and mode != "OBJECT":
        bpy.ops.object.mode_set(mode=mode)
    redraw()


def redraw():
    """Force a viewport redraw: region_3d projection matrices (used by strokes and by
    location_3d_to_region_2d) only refresh when the region draws."""
    win, area, region, _ = view3d()
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)


def activate_brush(mode, name):
    """Activate an Essentials brush asset for a paint mode (object must be in that mode)."""
    return bpy.ops.brush.asset_activate(
        asset_library_type="ESSENTIALS", asset_library_identifier="",
        relative_asset_identifier=f"brushes/{BRUSH_FILES[mode]}/Brush/{name}")


def brush(mode):
    return getattr(bpy.context.tool_settings, PAINT_SETTINGS[mode]).brush


def stroke(mode, world_points, size=60, pressure=1.0, strength=None, invert=False,
           snap=True, **op_kwargs):
    """Run one brush stroke through world-space points (object must be in `mode`).
    snap=True casts a ray from the view through each point onto the active object, as a
    mouse would, and uses the hit as the dab location (the exec path uses `location`
    literally, so a point 5 cm off the surface affects nothing). Points whose ray misses
    are skipped and returned. Returns (operator result, missed points)."""
    redraw()   # region matrices and paint canvases refresh on draw (texture paint needs it)
    win, area, region, rv3d = view3d()
    ob = bpy.context.active_object
    to_local = ob.matrix_world.inverted() if ob is not None else None
    b = brush(mode) if mode in PAINT_SETTINGS else None
    if b is not None and strength is not None:
        b.strength = strength
    elems, missed = [], []
    for i, p in enumerate(world_points):
        xy = view3d_utils.location_3d_to_region_2d(region, rv3d, Vector(p))
        if xy is None:
            missed.append(p)
            continue
        loc_world = Vector(p)
        if snap and ob is not None and ob.type == "MESH":
            origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, xy)
            direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, xy)
            o_l = to_local @ origin
            d_l = (to_local.to_3x3() @ direction).normalized()
            hit, loc_l, _n, _idx = ob.ray_cast(o_l, d_l)
            if not hit:
                missed.append(p)
                continue
            loc_world = ob.matrix_world @ loc_l
        # 'location' is read in OBJECT space by the exec path; 'mouse' is region space
        loc = tuple(to_local @ loc_world) if to_local is not None else tuple(loc_world)
        elems.append({"name": "", "location": loc, "mouse": (xy.x, xy.y),
                      "mouse_event": (xy.x, xy.y), "pressure": pressure, "size": size,
                      "time": float(i), "x_tilt": 0.0, "y_tilt": 0.0, "is_start": not elems})
    if not elems:
        return {"CANCELLED"}, missed
    if mode == "SCULPT":
        op_kwargs.setdefault("mode", "INVERT" if invert else "NORMAL")
    mod, name = STROKE_OPS[mode].split(".")
    with bpy.context.temp_override(window=win, area=area, region=region):
        r = getattr(getattr(bpy.ops, mod), name)(stroke=elems, **op_kwargs)
    return r, missed
