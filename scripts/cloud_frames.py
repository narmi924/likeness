"""Render a contiguous range on one Cycles GPU, publishing only finished frames."""

from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from worker import render_device


def render_frames(first, last, output):
    scene = bpy.context.scene
    if scene.render.engine != "CYCLES" or scene.render.use_multiview:
        raise ValueError("This worker takes single-view Cycles jobs.")
    if any(node.type == "OUTPUT_FILE" and not node.mute
           for group in bpy.data.node_groups for node in group.nodes):
        raise ValueError("File Output nodes need an explicit output collection plan.")
    render_device(scene)
    if scene.cycles.device != "GPU":
        raise RuntimeError("Blender found no usable Cycles GPU.")
    scene.render.use_file_extension = True
    if scene.render.image_settings.file_format not in {"PNG", "OPEN_EXR", "OPEN_EXR_MULTILAYER"}:
        raise ValueError("Save the render copy with PNG or OpenEXR image output.")
    # Blender also records workstation paths to brush presets. Check render inputs.
    for blocks in (bpy.data.libraries, bpy.data.images, bpy.data.cache_files, bpy.data.volumes,
                   bpy.data.movieclips, bpy.data.sounds, bpy.data.fonts):
        for block in blocks:
            value = block.filepath
            if not value or value == "<builtin>" or getattr(block, "packed_file", None):
                continue
            value = bpy.path.abspath(value, library=block.library)
            paths = [value.replace("<UDIM>", str(tile.number)) for tile in block.tiles] if "<UDIM>" in value else [value]
            for filename in paths:
                if not Path(filename).is_file():
                    raise FileNotFoundError(f"{block.name}: {filename}")
    for frame in range(first, last + 1):
        scene.frame_set(frame)
        target = output / f"{frame:06d}{scene.render.file_extension}"
        working = output / ("writing" + target.suffix)
        scene.render.filepath = str(working)
        if "FINISHED" not in bpy.ops.render.render(write_still=True):
            raise RuntimeError(f"Frame {frame} failed.")
        working.replace(target)


if __name__ == "__main__":
    start, end, folder = sys.argv[sys.argv.index("--") + 1:]
    render_frames(int(start), int(end), Path(folder))
