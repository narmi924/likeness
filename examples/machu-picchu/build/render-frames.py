"""Render frames FIRST..LAST of the baked walk on every GPU of the machine (OptiX).
    blender -b walk.blend --python render-frames.py -- <frames dir> FIRST LAST [trial]
The fleet's render command (see fleet.json); `trial` renders at half size with 4 samples."""
import sys
from pathlib import Path
import bpy

folder, first, last, *trial = sys.argv[sys.argv.index("--") + 1:]
prefs = bpy.context.preferences.addons["cycles"].preferences
prefs.compute_device_type = "OPTIX"
prefs.refresh_devices()
cards = [d for d in prefs.devices if d.type == "OPTIX"]
if not cards:
    raise RuntimeError("No OptiX GPU on this machine")
for device in prefs.devices:
    device.use = device in cards
scene = bpy.context.scene
scene.render.engine = "CYCLES"; scene.cycles.device = "GPU"
scene.cycles.samples = 4 if trial else 32
scene.cycles.use_animated_seed = False
scene.render.use_persistent_data = True
scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
scene.render.resolution_percentage = 50 if trial else 100
settings = scene.render.image_settings
settings.file_format, settings.color_mode, settings.color_depth, settings.compression = "PNG", "RGB", "8", 25
scene.render.filepath = str(Path(folder) / "f_#####")
scene.frame_start, scene.frame_end, scene.frame_step = int(first), int(last), 1
bpy.ops.render.render(animation=True)
