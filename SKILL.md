---
name: likeness
description: Build 3D scenes, buildings, products, and objects in Blender. Use for both imagined scenes and replicas of real places or objects based on photographs, maps, and elevation data. Deliver still renders, turntables, walk-through films, scroll-driven web pages, or GLB models. Choose the viewing experience first, build around the camera, and review a render after each change. Use for requests to model a place or product, create 3D for a website, render a scene, make a walk-through, or plan a local or cloud render.
---

# Likeness

When working in Chinese, follow the [Chinese edition](SKILL-zh.md).

Build a scene in Blender and take it through to something people can view or use. When the subject exists in the real world, preserve the features that make it recognizable at a glance. This workflow grew out of a website where visitors scroll around a lake past four real locations. The finished film runs for 114 seconds, or 6,840 frames at 2560 × 1440 and 60 fps.

All commands run through one entry point, from any directory:

```text
python <skill>/scripts/likeness.py setup        # Show what's missing and where it goes. Add --get to download Blender, FFmpeg, and starter assets.
python <skill>/scripts/likeness.py where        # Locate Blender, FFmpeg, and the local asset library.
python <skill>/scripts/likeness.py --help       # Commands for real places, overhead plans, camera routes, and film delivery.
python <skill>/scripts/likeness.py tools --help # Modelling tools.
```

This guide uses `likeness` as shorthand for that entry point. On a new machine, run `likeness setup` first. If anything is missing, tell the user the download size and destination before running `likeness setup --get`. Downloads go to the Likeness home directory shown by `likeness where`. Reuse the machine's existing Blender, FFmpeg, and Python installations. Install only what `setup` reports missing. There is no need to create a Python virtual environment, change PATH, or copy Blender.

Machine-specific settings go in `local.json` in the skill directory. For example, `{"home": "E:/likeness", "library": "...", "blender": "..."}`. All fields are optional. Keep this file local.

## 1. Choose how people will view the result

Decide what the viewer should be able to do before modelling. That choice determines what to build, how close the camera can get, and what needs to run on the viewer's device. See [Choosing the format](references/form.md).

| What the viewer wants | Format | What to plan for |
|---|---|---|
| See one well-composed image | Still render | Set the camera and lighting during blockout. |
| Inspect a product or object from every side | Turntable with drag-to-rotate controls | The back, underside, and seams will all be visible. |
| Walk through a photorealistic place smoothly on any device | **Pre-rendered film that the viewer scrubs by scrolling** | Build around the camera route. See section 4. |
| Move freely or see content change in real time | Real-time 3D exported as GLB | Explain the limits on visual fidelity and performance on less powerful devices. |
| Include the scene in a video | Rendered footage | Build for the planned shots. |
| Promote an existing 3D film or page | Return to the original scene and shoot new camera angles | Render the interface into the scene as a texture and time the shots to musical accents. Avoid reworking pixels from the finished film. See [Promo films](references/promo.md). |

If someone asks for “real-time 3D” because they want a smooth, realistic view, recommend a pre-rendered film and explain the trade-off. Changing part of the scene means rendering that segment again.

## 2. Follow this sequence for every project

1. **Identify what must be recognizable.** Establish scale, closest viewing distance and identifying features. Use any supplied photos. [Find the missing views](references/modeling.md#reference-and-asset-sources) yourself by place and feature or exact product model. Inspect the candidate photos together, follow a verified subject's Commons category, and look elsewhere for angles it lacks. Choose each photo for a modeling decision it can settle. Set scale from a known dimension such as door height or a product specification. For a real place, run `likeness place <latitude> <longitude> <directory>` and match the photos to its elevation and OpenStreetMap grid in metres.
2. **Draw an overhead plan and wait for approval.** This is the cheapest point to change direction. For a walk-through, draft `route.json` with the path, stops, and subject at each stop. Run `likeness plan route.json plan.png --place <place-directory> --lang en` to draw it on the real map. The sheet shows the whole site on the left, route detail on the right, and a card below for each stop with its arrival time and intended view. Set `note` on a stop's key to supply its caption. For a still or turntable, draw a plan showing the camera and subject. Show the plan, explain what should be recognizable at each stop, and give the total duration where applicable. **Wait for the user's reply before building.**
3. **Block out the scene, camera, and lighting together.** Use simple shapes to establish the silhouette, proportions, framing, and direction of light. For a replica, match reference cameras to the blockout and compare visible edges. The agent finds the views, picks the points, masks obstructions and checks the result. Start with whatever references are available. See [Fitting to photographs](references/modeling.md#fit-a-place-or-object-to-photographs). Judge the blockout from the intended final camera.
4. **Render, inspect, and revise.** In each pass, fix the most visible issue in the image. Compare the same frame from the same camera under the same lighting before and after the change. See [Modelling](references/modeling.md) and [Lighting, materials, and visual review](references/look.md).
5. **Get an independent review of the pictures.** Even when you can no longer see a problem, give a reviewer who has no production context only the images. Ask what looks wrong, revise, and repeat for at least two review rounds. See [Reviewing the renders](references/look.md#看图把关).
6. **Estimate the final render.** Measure render time and memory use, estimate the cost, then choose between local and cloud rendering. See [Rendering and delivery](references/film.md).
7. **Deliver the result and its editable source.** Include the finished output, the `.blend` file, the scripts that generated it, and one source credit per asset. `likeness place` records the map and elevation sources.

Build scenes with `bpy` scripts and save each step to a new file:

```text
likeness edit new build/01-blockout.py tmp/01.blend         # Run a script in an empty scene and save the result.
likeness edit tmp/01.blend build/02-shape.py tmp/02.blend   # Continue from the previous version.
likeness view tmp/02.blend tmp/02.png --camera "Review" --style render   # Render a view. Use --style clay to inspect form or --angle sheet for four views.
likeness compare tmp/02.blend references/a.jpg tmp/cmp.png --angle camera --camera "Review"   # Place the camera view beside the reference.
likeness inspect tmp/02.blend                               # List the scene contents and their names.
```

## 3. Start an outdoor scene

`examples/starter/` contains a runnable scene with scanned ground textured at real-world scale, a gravel path, hand-placed scanned trees and rocks, roadside grass, a distant tree line, and evening light and haze. See `preview.jpg` in that directory. Copy `build.py` and `route.json` into the project and adapt them.

```python
from modeling import outdoor, terrain
lib = outdoor.library  # lib("polyhaven:island_tree_01") resolves a local asset path. Run `likeness library` to list assets.
ground = terrain.ground(size=(160, 160), resolution=200, height=1.4)
outdoor.ground(ground, lib("polyhaven:leafy_grass"), patches=lib("polyhaven:forest_leaves_02"),
               path=lib("polyhaven:gravel_floor_02"), path_line=PATH, path_width=2.6)  # Project ground textures from above at metre scale.
outdoor.plant(lib("polyhaven:island_tree_02"), [(22, 12, 11), (27, 4, 9)], size=10, ground_object=ground)  # Place scanned trees.
outdoor.strew(ground, [(lib("polyhaven:grass_medium_02"), 0.9)], density=1.3)  # Scatter grass only along the path.
light = outdoor.sky(lib("polyhaven:evening_road_01_puresky"), sun_azimuth=115)  # Sky map with a matching sun light, at dusk by default.
outdoor.haze(light)  # Fade distant objects. Call after creating all materials.
outdoor.film_settings(width=1920, samples=96)  # Render settings used for the lake film.
```

For a landscape with water, shoreline, and distant mountains, define the terrain with a height function. Place a flat water surface at the water level:

```python
ground = outdoor.land((-150, -70, 150, 210), height, cell=0.9)  # Define height(x, y) -> z for the near shore, lake bed, far shore, and mountains.
outdoor.ground(ground, lib("polyhaven:leafy_grass"), path=..., path_line=PATH, tint=(0.5, 0.66, 0.36), path_tint=(0.6, 0.56, 0.5),
               shore=lib("polyhaven:coast_sand_rocks_02"), water_level=0.0)  # Greener grass, darker gravel, and a strip of wet sand at the waterline.
outdoor.water(level=0.0)  # Still water reflects the sky and banks. The lake bed shows through in the shallows.
outdoor.plant(lib("polyhaven:fir_sapling_medium"), spots, size=14, ground_object=ground, group="Far wood")  # Group the stand of trees under one name.
```

For real locations, the elevation and outlines from `likeness place` use the same metre coordinates as the map and overhead plan:

```python
place = outdoor.place("<place-directory>")  # X points east, Y north. The map centre is the origin. Its elevation is place.base, and its local height is zero.
near = (-60, -260, 60, -140)  # Build the patch along the camera path in more detail.
inside = lambda x, y: near[0] < x < near[2] and near[1] < y < near[3]
outdoor.land(place.extent, lambda x, y: place.height(x, y) - (0.4 if inside(x, y) else 0), cell=4, name="Far land")
ground = outdoor.land(near, place.height, cell=0.5)
for f in place.outlines("building"): f["points"]  # OpenStreetMap building footprints as [[x, y], ...]. Other outlines include wall, path, and area.
```

The elevation samples are roughly 30 metres apart. Model terraces, walls, cliffs, and steps from photographs. Use OpenStreetMap outlines for position and orientation, and photographs for height, floor count, and the appearance of the stonework.

Use the built-in helpers for towns, weather, and distant scenery:

```python
from modeling import city, weathering as W
homes = city.facade("Housing", "housing", floor_h=3.0, bay=3.6)  # Windows, frames, sills, half-drawn curtains, and a few lights.
glass = city.facade("Offices", "curtain", floor_h=3.8, bay=1.8)  # Full-height curtain wall. Use "stone" for stone piers alternating with glass.
city.buildings(place.outlines("building"), place.height, [homes, glass], keep=lambda x, y: abs(x) > 60)  # Extrude the mapped footprints using tagged height data.
city.skyline((0, 0), 500, 1400, 300, place.height, [homes, glass], bearing=(20, 110))  # A cluster of buildings on the horizon.
city.lights(0.8)  # Adjust all lit windows together. Use 0 for daylight.
W.cover(ground.active_material, snow=0.7)  # Snow on upward-facing surfaces. rain=1 adds wet ground and puddles. above=2600 limits coverage by elevation.
W.streaks(wall_material, 0.4)  # Rain streaks and grime at the foot of the wall.
far = W.mountain(zmax=1800, snowline=0.36)  # Distant gullies, rock, forest, and snow line.
winter = W.bare(tree)  # Leafless tree. W.snowy(fir) adds snow to a conifer. W.leaves(tree, hue=0.52) adjusts leaf colour and transmits light.
keep = outdoor.clear_view(stop_xy, landmark_xy, far=120)  # Leave the sightline from this stop to its landmark clear of planting.
spots = outdoor.scatter((-80, -80, 80, 80), 200, keep=[keep], spacing=4)
```

Use these procedural buildings in the middle and far distance, where the silhouette, height, window spacing, and a few lights are what the viewer can see. Model buildings within a few metres of the camera individually from photographs.

For things you build yourself, such as planks, posts, walls, and boat hulls, use `outdoor.material(lib("polyhaven:wood_planks_grey"), metres=1.4, tint=(0.86, 0.84, 0.8))`. It projects textures from three directions at real-world scale, with slight variations in shade between objects. No UV unwrapping is needed.

`outdoor.haze` affects materials that exist at the time of the call and skips any it has already processed. Call `outdoor.haze(light)` again after adding objects. Call `outdoor.sky` again to change the lighting.

See `scripts/modeling/outdoor.py` for the function parameters. For buildings, products, and other modules, see [Modelling](references/modeling.md).

## 4. Make a walk-through film

The camera follows a route and stops at selected viewpoints. Build what it can see along that route. See [Camera routes and staging](references/route.md).

```text
likeness walk route.json walk.json --ground scene.blend --poses poses.json   # Generate a camera pose for every frame along the route.
likeness bake scene.blend walk.json walk.blend                               # Bake the camera into the scene for timeline scrubbing.
likeness sight walk.blend walk.json                                          # Check that each stop's subject is in frame and unobstructed.
likeness shoot walk.blend tmp/drafts --frames check --walk walk.json --jpg --width 960   # Render a draft at each key position.
likeness sheet tmp/drafts.jpg tmp/drafts --walk walk.json                    # Make a contact sheet for you and the reviewer. In a large frame directory, add --frames check to show just the key positions.
likeness shoot walk.blend tmp/frames --frames 0-239                          # Render final frames. Resuming skips existing frames. Add --again to re-render them.
likeness pieces tmp/frames tmp/film --fps 60                                 # Make one-second master segments for later output formats.
likeness web tmp/film tmp/site walk.json --scene walk.blend                  # Generate web resolutions, film.json, and the player page.
likeness filmcheck tmp/site tmp/overview                                     # Check for jumps between frames and at the loop seam, and produce an overview.
likeness serve tmp/site                                                      # Scroll through the page locally. Run in the background and stop when done, or use --for 120 to stop after two minutes.
likeness fleet up tmp/job 5                                                  # Cloud GPUs: up rents workers and test-renders, go renders segments, watch retrieves results and shuts down workers, and frames unpacks PNGs. See references/film.md.
likeness page shot "http://127.0.0.1:8765/?debug" tmp/page.png --run "walk.setAuto(true)" --wait 8
```

The start of `scripts/film/route.py` documents the `route.json` format. For site integration, scroll behaviour, resolution, sound, and weather, see [Putting the film on the web](references/web.md).

## 5. Avoid common mistakes

Each of these caused rework on a project. The reference guides contain the full checklists.

- **If the image looks flat, inspect the models, materials, and lighting first.** Switching renderers leaves the same underlying scene.
- **Use scans and real geometry close to the camera.** This matters for trees, rocks, and ground within a few metres of the lens. Keep procedural buildings in the distance, where their simpler details hold up.
- **Keep terrain true to the site.** An isolated mound built to hide an object looks artificial. Use planting or change the camera direction to screen it.
- **List what must be in frame at every stop.** Run `likeness sight` before rendering. A single nearby tree can hide a landmark for much of the route.
- **Keep the subject well inside the frame.** Give it more than a glimpse at the edge. Make the opening frame the strongest image in the film.
- **Keep the camera close to level when shooting architecture.** Use vertical lens shift (`shift`) to include the roof while keeping vertical lines straight.
- **Judge movement at walking speed.** A leisurely walk is about 1.3 m/s. The lake film moved at 3.2 m/s and felt rushed.
- **Review key frames before a long render.** Render the full route after review. Check that cloud machines are actually available before proposing them, and arrange automatic shutdown when rendering finishes.
- **Choose weather treatment for the playback format.** On a scroll-driven page, overlay moving snow, rain, or sand in real time. It keeps falling when the viewer stops and moves naturally when they scroll backwards. For a conventional video such as a promo, render the weather into the footage. List the weather at each location before rendering, including airborne sand and dust.
- **Prioritize smooth playback over a quick first image.** Buffer several seconds of footage before revealing the page on first load.

Keep the user's standing preferences in `references/taste.md`. This optional file stays on the local machine. If present, read it before starting work for that user. When the user gives a preference that should apply to future projects, record it there.

## 6. Organize project files

- Keep deliverables in `<project>/3d/<work>/`. Include `<work>.blend`, numbered scripts in `build/`, selected reference images in `references/`, `route.json`, and `sources.md` with one line per external asset giving its creator, licence, and URL. Without a project directory, use `projects/<work>/` under the Likeness home directory shown by `likeness where`.
- Keep intermediate files in `<project>/tmp/<work>/`, including drafts, frames, contact sheets, and film segments. Keep them out of the skill directory and off the Windows C: drive.
- Save scene edits to a new file. The `likeness` commands protect existing files. Promote an approved version with `likeness edit … --promote`.
- Number files by iteration. For example, `build/03-scene.py` corresponds to `tmp/03.blend`, `tmp/03w.blend` after baking the camera, and `tmp/d3/` for drafts. If a command reports that the file already exists, rerun it with the next number. **Continuing with the old file renders the previous iteration.**
- With scanned assets and packed textures, each saved scene can take 0.5 to 1 GB. Once a new iteration is visibly better, remove older intermediate `.blend` files and retain the scripts needed to rebuild them.
- Write scripts to files using a file-writing tool, then run them. Assembling scripts through multiline shell input can break quoting and Chinese text.

## 7. References

- [Choosing the format](references/form.md) covers real-time scenes and pre-rendered films, with trade-offs and figures.
- [Camera routes and staging](references/route.md) covers route files, stops, and placement along the path.
- [Lighting, materials, and visual review](references/look.md) covers lighting, skies, materials, assets, and how to review a render.
- [Rendering and delivery](references/film.md) covers render settings, estimates, segmentation, and multi-machine cloud rendering with `likeness fleet`.
- [Promo films](references/promo.md) explains how to shoot a short film from a finished scene, cut it to music, and render a web interface into the scene as a texture.
- [Putting the film on the web](references/web.md) covers the player, scroll behaviour, resolution, sound, weather, and hosting.
- `references/taste.md` holds the local user's standing preferences, if present.
- [Modelling](references/modeling.md) covers modelling from observation, finding references and assets, architecture, products, environments, and people (in English).
- [Blender pitfalls](references/pitfalls.md), [Single-machine cloud rendering](references/rendering.md), and [Texture prompts](references/image-prompts.md) are also in English.
- `examples/starter/` provides an outdoor starting scene. `examples/jetty/` contains the complete scripts and route for a dusk walk to a lakeside jetty. `examples/machu-picchu/` follows a real site from map and elevation data through photo projection, a stone gate traced from photographs, cloud rendering, and web delivery. Some local installations also have `examples/lakeside/` with the original lake scripts, cloud render, and web code, and `examples/promo/` with the promo film. These two examples are local only.

When available in the session, use `show_model`, `locate_surface`, and `find_references` to pick points in a view and find assets. Otherwise, the same work can be done with `likeness view` and `likeness locate`.
