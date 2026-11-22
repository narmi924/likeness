# Blender modeling pitfalls

- UV layout PNG fails in background mode with a GPU initialization error → the UV exporter draws to an offscreen buffer → call `gpu.init()` before `bpy.ops.uv.export_layout`.
- A realistic stock body has no detectable crotch when fitting a rig → the base library lays its figures out at different world X positions and joint detection assumes a centered figure → move the whole figure to its symmetry plane at X = 0 before locating joints.
- Rig binding receives a tuple instead of an armature → `rig.generate(meta)` returns the rig and an error message → unpack the result and check the rig before binding.
- An EEVEE sampling setting has no effect → viewport and final render sampling are separate → set `scene.eevee.taa_render_samples` for the output movie.
- A study camera leaves space around narrow curves → Blender's curve bounds can include the full bevel envelope → use the evaluated surface vertices when framing.
- Instance bounds repeat another object's shape → an evaluated instance handle is valid only during iteration → cache coordinates by original object and evaluated geometry IDs.
- A glTF import stretches a posed character or displaces its bone-attached details → a static pose and the exported bind pose differ → bake the evaluated surfaces and bind the static skeleton in that pose before export.
- A daylight node rejects `NISHITA` or `dust_density` → Blender 5.2 uses named scattering models and aerosol density → choose `SINGLE_SCATTERING` and set `aerosol_density`.
- An offline surface reference mentions a missing temporary blend file → a live preview was recording its own intermediate copy as an external dependency → track the neighboring permanent scene by its revision and track its actual external assets separately.
- Packing a baked map reports a missing temporary image → moving the PNG changes the path Blender tries to reload → set the final raw filepath before packing the image.
- A fitted face leaves the eyeballs, teeth or brows behind → only the head mesh received the shape key → list them with `--transfer` (deform) or `--move` (rigid pieces) in `fit-face`.
- A profile photograph solves well but its landmark error stays high → the detector invents the far side of a turned head → `visible_points` drops that side before the solve; judge the fit by the visible landmarks.
- A camera solve returns the smallest focal length tried → face landmarks hardly constrain focal length → among near-equal fits the solver prefers a portrait lens; pass a known focal length when the photograph has one.
- A promoted file renders a magenta face → a texture was loaded by absolute path from a folder that later moved → promote with `--pack`, and run `check` which lists missing images.
- Cycles on the Arc graphics is slower than the CPU at small sizes but faster at portrait sizes and leaves the CPU free → fixed start-up costs dominate small renders → keep the GPU as the default, measure before changing it.
