# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==1.30.0"]
# ///
"""Read-only Codex panel for a saved Blender scene or a connected Blender Lab window."""

import asyncio
from collections import OrderedDict
from pathlib import Path
import tempfile
import uuid

from mcp import types
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server

from sources import PROVIDERS, catalog
from studio import ANGLES, STYLES, capture, file_revision, locate


URI = "ui://likeness/viewport"
MIME = "text/html;profile=mcp-app"
server = Server("blender_studio")
views = OrderedDict()
render_lock = asyncio.Lock()


def dependencies_current(files):
    if files is None:
        return False
    try:
        return all(file_revision(path) == tuple(revision) for path, revision in files.items())
    except OSError:
        return False


@server.list_tools()
async def list_tools():
    return [types.Tool(
        name="show_model", title="View and select Blender objects",
        description="Show a selectable view of an absolute local .blend path, or 'live' for a connected Blender Lab session. Click a visible surface to attach that object and location to the conversation.",
        inputSchema={"type": "object", "additionalProperties": False, "required": ["source"],
                     "properties": {
                         "source": {"type": "string"},
                         "angle": {"enum": list(ANGLES), "description": "Defaults to the named camera when supplied, otherwise three-quarter."},
                         "camera": {"type": "string", "minLength": 1, "description": "Exact scene camera name for a fixed composition. Omit angle or use 'camera'."},
                         "subject": {"type": ["string", "null"], "description": "Object or collection to frame in a study view."},
                         "style": {"enum": list(STYLES), "default": "clay"},
                         "size": {"type": "integer", "minimum": 64, "maximum": 1600, "default": 900},
                         "frame": {"type": ["integer", "null"]},
                         "fresh": {"type": "boolean", "description": "Re-render even if this saved view is cached."},
                     }},
        annotations=types.ToolAnnotations(readOnlyHint=True, openWorldHint=False),
        _meta={"ui": {"resourceUri": URI}},
    ), types.Tool(
        name="locate_surface", title="Locate the selected mesh surface",
        description="Locate the visible mesh in a show_model image. Supply the capture ID and a normalized [x,y] from the upper left. An attached selection can also supply its object key.",
        inputSchema={"type":"object","additionalProperties":False,"required":["capture_id","point"],
                     "properties":{"capture_id":{"type":"string"},"object_key":{"type":"string"},
                                   "point":{"type":"array","minItems":2,"maxItems":2,"items":{"type":"number","minimum":0,"maximum":1}}}},
        annotations=types.ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    ), types.Tool(
        name="find_references", title="Search photographs and 3D assets",
        description="Find real reference photographs, models and materials in selected public or connected catalogs. Returns source links, thumbnails when available, author and license.",
        inputSchema={"type":"object","additionalProperties":False,"required":["query","providers"],
                     "properties":{"query":{"type":"string","minLength":1},
                                   "providers":{"type":"array","minItems":1,"uniqueItems":True,"items":{"enum":list(PROVIDERS)}},
                                   "limit":{"type":"integer","minimum":1,"maximum":12,"default":4}}},
        annotations=types.ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    )]


@server.call_tool()
async def call_tool(name, arguments):
    if name == "find_references":
        result = await asyncio.to_thread(catalog, arguments["query"], arguments["providers"], arguments.get("limit", 4))
        return {"providers": result}
    if name == "locate_surface":
        async with render_lock:
            record = views.get(arguments["capture_id"])
            if not record:
                raise ValueError("This view expired. Reopen it before choosing a surface point.")
            view = record["result"].meta["modelView"]
            key = arguments.get("object_key")
            target = view["objects"].get(key)
            if key is not None and target is None:
                raise ValueError("This object is outside the captured view.")
            if not record["live"] and record["revision"] != file_revision(record["path"]):
                raise ValueError("The Blender file changed after selection. Refresh the view.")
            if record["deps"] is not None and not dependencies_current(record["deps"]):
                raise ValueError("An external asset changed after selection. Refresh the view.")
            selection = dict(object=target, point=arguments["point"], scene=view["scene"], frame=view["frame"],
                             projection=view["projection"])
            pick_layer = None if key is not None else (view["pickMap"], view["objects"])
            return await asyncio.to_thread(locate, record["path"], selection, pick_layer=pick_layer)
    if name != "show_model":
        raise ValueError(f"Unknown tool: {name}")
    args = dict(arguments)
    fresh = args.pop("fresh", False)
    source = args.pop("source")
    if source != "live" and not Path(source).is_absolute():
        raise ValueError("Supply an absolute .blend path or 'live'.")
    async with render_lock:
        live = source == "live"
        path = str(Path(source).resolve(strict=True)) if not live else None
        revision = file_revision(path) if path else None
        key = (path, revision, args.get("angle", "camera" if args.get("camera") else "three-quarter"), args.get("camera"), args.get("subject"),
               args.get("style","clay"), args.get("size",900), args.get("frame"))
        if not live and not fresh:
            cached = next((i for i, entry in reversed(views.items()) if entry["key"] == key
                           and dependencies_current(entry["deps"])), None)
            if cached:
                views.move_to_end(cached)
                return views[cached]["result"]
        folder = tempfile.TemporaryDirectory(prefix="blender-live-") if live else None
        try:
            if folder:
                path = str(Path(folder.name) / "current.blend")
            image, view = await asyncio.to_thread(capture, "live" if live else path, timeout=250,
                                                  frozen=path if live else None, **args)
            if live:
                view["source"] = "live: " + view["source"]
                view["input"]["source"] = "live"
            elif file_revision(path) != revision:
                raise ValueError("The Blender file changed during capture. Refresh the view.")
            token = uuid.uuid4().hex
            dependencies = view.pop("dependencies", None)
            view["capture_id"], view["revision"] = token, revision
            result = types.CallToolResult(
                content=[types.ImageContent(type="image", data=image, mimeType="image/png")],
                structuredContent={"source": view["source"], "scene": view["scene"], "frame": view["frame"],
                                   "angle": view["angle"], "camera": view["camera"], "visibleObjects": len(view["objects"]), "capture_id": token},
                _meta={"modelView": view})
            views[token] = dict(key=key, path=path, revision=revision, live=live, folder=folder, deps=dependencies, result=result)
        except BaseException:
            if folder:
                folder.cleanup()
            raise
        while len(views) > 6:
            _, oldest = views.popitem(last=False)
            if oldest["folder"]:
                oldest["folder"].cleanup()
        return result


@server.list_resources()
async def list_resources():
    return [types.Resource(uri=URI, name="Blender object picker", mimeType=MIME)]


@server.read_resource()
async def read_resource(uri):
    if str(uri) != URI:
        raise ValueError(f"Unknown resource: {uri}")
    return [ReadResourceContents((Path(__file__).with_name("panel.html")).read_text(encoding="utf-8"), MIME)]


async def main():
    try:
        async with stdio_server() as (reader, writer):
            await server.run(reader, writer, server.create_initialization_options())
    finally:
        for entry in views.values():
            if entry["folder"]:
                entry["folder"].cleanup()


if __name__ == "__main__":
    asyncio.run(main())
