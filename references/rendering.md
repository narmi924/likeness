# Render planning and Runpod

## Measure before recommending a machine

For a short still, render locally when it fits. For expensive stills, sequences, tight deadlines or memory pressure, start in the intended output directory. Use the same camera, render engine, resolution, samples, motion blur, denoising and baked inputs as the delivery. Save experimental changes in a render copy. If the deliverable generates a camera or overrides scene settings, apply those settings to the copy before timing it.

```text
likeness tools hardware
likeness tools estimate scene.blend --frames 12 84 176 --count 240
```

`hardware` reports the Cycles devices Blender can select, Windows RAM and free disk on the current drive. NVIDIA devices also report VRAM when `nvidia-smi` is available. Integrated graphics share system memory. Check peak memory on a heavy frame and leave room for Blender, the operating system, caches and output frames. A GPU appearing in the device list does not establish that the scene fits it.

Pick sample frames by visible workload. Include fur, volumes, glass, motion blur or dense geometry where they occur. The estimate times the first frame cold, then samples in one process for a sequence. Its range extrapolates the fastest and slowest samples. For shots of very different lengths, time each shot and add its estimate weighted by frame count. Each fresh worker pays startup again. Allow separately for scene loading, simulation baking, saving, compositing file outputs, transfer and encoding. If a full frame is prohibitively expensive, use a representative border render to obtain a preliminary figure and state what it omits.

Recommend a rental when local memory or turnaround misses the brief. Obtain current inventory with `likeness tools cloud offers`. Compare **time to usable output** and **total USD**. Include provisioning, scene upload, the critical render path, download, disk charges and an allowance for failures. A small still can finish locally before the cloud upload completes. GPU model names and list prices alone cannot predict the scene's speed. If a cloud timing is missing, give the uncertainty and propose one **budgeted pilot** on the intended GPU. A pilot can render a real deliverable frame. Separate the pilot authorization from any expansion unless both fit the already agreed budget, machine count and duration.

## Run an authorized job

Use the local account key through `RUNPOD_API_KEY_FILE` (path to the chosen key) or `RUNPOD_API_KEY`. Keep its value out of the project, commands, logs and upload. The SSH keypair is separate. Agree which scene/assets can leave the machine, the total spend, data-center restrictions if any, and maximum elapsed time. The bundled command selects from secure-cloud NVIDIA capacity without a location restriction; for a location-bound job explicitly constrain placement through the provider before renting.

1. Save a portable render copy with project-relative paths or packed assets. The worker renders the active scene and its saved camera, using single-view Cycles and either PNG or OpenEXR. Bake simulations and include required external caches, linked libraries, fonts, tiled textures and frame sequences. Use a matching pipeline for a compositor with extra file outputs, third-party render engine or special add-on. Include only the dependency files or dedicated asset directories required for this job. Size the ephemeral disk for the uploaded archive, unpacked assets, Blender and the entire frame range.
2. Choose the GPU ID and rate from the live offers. Set the time limit, total budget and a dollar reserve for storage, transfers and termination delay. The controller checks the listed rate before renting and the actual compute rate at allocation. Provider charges and inventory can change. Keep the controller running until release is confirmed.
3. Run the command below with agreed values. Each invocation rents **one** single-GPU pod and uses a fresh local directory for its ID, SSH host key and retrieved frames. SSH uses the specified identity and sends its public half to the official image. The Linux worker downloads the official Blender build matching the local version. The example values are placeholders.

```text
likeness tools cloud offers
likeness tools cloud render scene.blend job-dir --frames FIRST LAST --gpu "GPU ID" --hours HOURS --budget USD --extra-cost USD --disk GB --key ssh-key --asset textures --asset caches
likeness tools cloud status job-dir
likeness tools cloud release job-dir
```

4. On long jobs, check the local frames directory and inspect a representative returned frame for materials, simulation state, framing and color before scaling. Multiple pods can render disjoint ranges into separate directories, with **portions of the same approved budget**. Size ranges from the measured rate. Independent frame ranges require baked or stateless animation. Compare a boundary frame across GPU types before mixing them if denoising or numerical variation matters.
5. The worker publishes each image after Blender finishes writing it. The controller retrieves and decodes frames locally and requests termination on completion, interruption, timeout or failure. Already retrieved frames remain in the job directory. After SSH starts, the controller also arms a pod-local deadline using the provider's pod-scoped key. A local crash during provisioning, loss of API access, or a failed termination can still leave charges. Recover using the printed job path and `cloud status`; `cloud release` terminates only the pod recorded for that job and permanently discards its remaining remote files. For a lost create reply, reconcile by the job's unique name before considering another rental.
6. Account for every requested frame locally, retain successful ranges and re-render only missing ones within the remaining authorization. Confirm each job reports terminated or its known ID is absent. Reconcile actual compute and storage charges in provider billing when available. Termination releases compute and ephemeral disk; any separately attached persistent resource has its own lifecycle. The bundled job requests no persistent storage.

Runpod documents the API, pod-scoped credentials and lifecycle operations in its [v2 specification](https://docs.runpod.io/api-reference-v2/openapi.json), [pod environment](https://docs.runpod.io/pods/templates/environment-variables.md) and [pod management guide](https://docs.runpod.io/pods/manage-pods.md).
