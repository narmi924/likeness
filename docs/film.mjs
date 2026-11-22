// A walk through a scene, played from a film that was rendered beforehand. The film is cut into one-second pieces;
// the pieces round the walker are fetched and decoded a group of frames at a time, so that scrolling can move through
// them in both directions. What the page lays over the film (falling snow, a text on a board, sound) is the page's own:
// it gets the camera and the things in view on every screen refresh.
import { mod, clamp, poseAt, projector, insideQuad, createGait, createSizeChooser } from "./walk.mjs";

// Seconds of film kept fetched ahead of the walker, how many pieces are fetched at once, and how many seconds are
// fetched before the walk is shown at all: the first visit has no cache to lean on, and a visitor would rather wait a
// few seconds once than watch the walk stutter.
const AHEAD = 8, TOGETHER = 3, FIRST = 6;
const VERTEX = "attribute vec2 p; uniform vec2 s; varying vec2 uv; void main(){ uv = vec2(p.x * 0.5 + 0.5, 0.5 - p.y * 0.5); gl_Position = vec4(p * s, 0.0, 1.0); }";
const FRAGMENT = "precision highp float; uniform sampler2D t; varying vec2 uv; void main(){ gl_FragColor = vec4(texture2D(t, uv).rgb, 1.0); }";

export function filmSupported() {
  return typeof VideoDecoder !== "undefined" && typeof EncodedVideoChunk !== "undefined";
}

/**
 * Starts the film on a canvas. options: base (the folder with film.json's pieces), data (film.json), start (seconds),
 * mbps (a first guess at the connection), quality (a size held by the visitor, by width), and callbacks
 * progress(share of the first seconds fetched), ready(), error(e), frame(state, seconds since the last refresh).
 * Returns the controls the page needs.
 */
export async function createFilm(canvas, options) {
  const { base, data } = options;
  const N = data.count, FPS = data.fps, D = N / FPS, LOOP = data.loop !== false, ASPECT = data.aspect || 16 / 9;
  const gl = canvas.getContext("webgl", { alpha: false, antialias: false, depth: false, stencil: false });
  if (!gl || !filmSupported()) throw new Error("This browser cannot play the film");
  // A position on a loop goes round; on a film with two ends, a position outside it is nowhere (-1).
  const turn = (i, n) => (LOOP ? mod(i, n) : i >= 0 && i < n ? i : -1);

  // ---- drawing
  const shader = (type, source) => { const o = gl.createShader(type); gl.shaderSource(o, source); gl.compileShader(o); return o; };
  const program = gl.createProgram();
  gl.attachShader(program, shader(gl.VERTEX_SHADER, VERTEX)); gl.attachShader(program, shader(gl.FRAGMENT_SHADER, FRAGMENT));
  gl.linkProgram(program); gl.useProgram(program);
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  const at = gl.getAttribLocation(program, "p"); gl.enableVertexAttribArray(at); gl.vertexAttribPointer(at, 2, gl.FLOAT, false, 0, 0);
  const scale = gl.getUniformLocation(program, "s");
  const spare = [];   // textures waiting to be used again
  const texture = () => {
    const t = spare.pop() || gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    if (!t.set) { for (const [k, v] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR], [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, k, v); t.set = true; }
    return t;
  };
  // closeUp: by how much the page shows the picture enlarged. ratio: canvas pixels to one pixel of the page.
  let width = 1, height = 1, closeUp = 1, ratio = 1;
  function fit() {
    // An enlarged picture is drawn with more pixels, up to a little over what the film itself has.
    const plain = Math.min(devicePixelRatio || 1, 2);
    ratio = Math.max(plain, Math.min(plain * closeUp, 3200 / width));
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
    const a = canvas.width / canvas.height / ASPECT;   // the film covers the window; what does not fit is cropped evenly
    gl.viewport(0, 0, canvas.width, canvas.height); gl.uniform2f(scale, a > 1 ? 1 : 1 / a, a > 1 ? a : 1);
    shown = -1;
  }
  function resize() {
    width = canvas.clientWidth || innerWidth; height = canvas.clientHeight || innerHeight;
    fit();
  }

  // ---- the pieces, and the size of film in use
  // How many pixels across the film must have to look sharp: as many as it is drawn with.
  const sharp = () => Math.max(width, height * ASPECT) * ratio;
  let variant = null, offsets = null, GOP = 12, SEG = 60, GROUPS = 0, PIECES = 0;
  const pieces = new Map();   // piece index -> Promise<ArrayBuffer>
  const frames = new Map();   // frame index -> texture
  const groups = new Map();   // group index -> { fed, got }: handed to the decoder, pictures back so far
  const waiting = [];         // pictures not handed to the decoder yet: [group state, group, frame, piece data]
  let decoder = null, flushing = false, fedAt = 0, shown = -1, shownTexture = null, failed = 0, disposed = false;
  const gait = createGait(FPS, options.start || 0, LOOP);
  let auto = false, held = false, last = performance.now(), quads = [], raf = 0, ready = false, drawn = false, jumped = -1e9;

  function useVariant(v) {
    variant = v; GOP = v.gop; SEG = v.segment; GROUPS = N / GOP; PIECES = N / SEG;
    offsets = new Uint32Array(N);
    for (let f = 0, o = 0; f < N; f++) { if (f % SEG === 0) o = 0; offsets[f] = o; o += v.sizes[f]; }
    for (const g of [...groups.keys()]) drop(g);
    waiting.length = 0; forget();
    if (decoder.state === "configured") decoder.reset();
    decoder.configure({ codec: v.codec, codedWidth: v.width, codedHeight: v.height, optimizeForLatency: true });
  }
  const fetching = new Map();   // piece index -> the way to call off a fetch that is still on its way
  // The connection is timed over every stretch in which something is being fetched: the bytes of all the pieces that
  // came in, over the time from the first request to the last answer. Pieces fetched side by side share the line.
  let inFlight = 0, busySince = 0, busyBytes = 0, standing = 0, heading = 1;   // standing: the piece the walker is in; heading: which way it walks
  function piece(i) {
    if (!pieces.has(i)) {
      const rung = variant, stop = new AbortController();
      if (inFlight++ === 0) { busySince = performance.now(); busyBytes = 0; }
      fetching.set(i, stop);
      const asked = fetch(`${base}/${rung.path}/${String(i).padStart(3, "0")}.h264`, { signal: stop.signal })
        .then((r) => { if (!r.ok) throw new Error(`piece ${i}: ${r.status}`); return r.arrayBuffer(); })
        .then((buf) => { busyBytes += buf.byteLength; failed = 0; return buf; })
        .finally(() => {
          if (fetching.get(i) === stop) fetching.delete(i);
          if (--inFlight === 0 && busyBytes) chooser.arrived(busyBytes, (performance.now() - busySince) / 1000);
        });
      pieces.set(i, asked);
      asked.catch((e) => {
        if (pieces.get(i) === asked) pieces.delete(i);
        if (e?.name !== "AbortError" && ++failed > 6 && !disposed) options.error?.(new Error("The film could not be fetched"));
      });
      // When too many are held, the one furthest behind the walker goes first (those ahead are about to be needed), and
      // a fetch still on its way for it is called off: the visitor's data is not spent on it.
      if (pieces.size > AHEAD + 6) {
        const room = (k) => { const ahead = mod(heading * (k - standing), PIECES); return ahead <= AHEAD ? ahead - AHEAD : PIECES - ahead; };
        const far = [...pieces.keys()].filter((k) => k !== i).sort((a, b) => room(b) - room(a))[0];
        fetching.get(far)?.abort(); pieces.delete(far);
      }
    }
    return pieces.get(i);
  }
  function forget() { for (const stop of fetching.values()) stop.abort(); fetching.clear(); pieces.clear(); }
  function feed(g) {   // the frames of one group are lined up; pump() hands them over a few at a time
    const first = g * GOP, state = { got: 0, fed: false };
    groups.set(g, state);
    piece(Math.floor(first / SEG)).then((buf) => {
      if (groups.get(g) !== state) return;
      for (let f = first; f < first + GOP; f++) waiting.push([state, g, f, buf]);
    }).catch(() => { if (groups.get(g) === state) groups.delete(g); });
  }
  function pump(urgent) {
    let room = urgent ? GOP : 2;
    while (waiting.length && room > 0 && decoder.state === "configured" && decoder.decodeQueueSize < (urgent ? GOP : 3)) {
      const [state, g, f, buf] = waiting.shift();
      if (groups.get(g) !== state) continue;
      decoder.decode(new EncodedVideoChunk({ type: f % GOP === 0 ? "key" : "delta", timestamp: f * 1000, data: new Uint8Array(buf, offsets[f], variant.sizes[f]) }));
      room--;
      if (f % GOP === GOP - 1) { state.fed = true; fedAt = performance.now(); }
    }
  }
  function drop(g) {
    for (let f = g * GOP; f < (g + 1) * GOP; f++) { const t = frames.get(f); if (t) { if (t !== shownTexture) spare.push(t); frames.delete(f); } }
    groups.delete(g);
  }
  function schedule(f, direction) {
    const g = Math.floor(f / GOP), along = (f % GOP) / GOP, late = direction > 0 ? along > 0.4 : along < 0.6;
    const ahead = turn(g + direction, GROUPS), behind = turn(g - direction, GROUPS), further = turn(g + 2 * direction, GROUPS);
    const keep = (late ? [g, ahead, further] : [behind, g, ahead]).filter((k) => k >= 0);
    for (const k of [...groups.keys()]) if (!keep.includes(k)) drop(k);
    const urgent = !frames.has(f);
    if (!groups.has(g)) {   // a jump: whatever was lined up for elsewhere gives way
      for (const [, other] of waiting) drop(other);
      waiting.length = 0;
      feed(g);
    } else if (!waiting.length) for (const w of late ? [ahead, further] : [ahead]) if (w >= 0 && !groups.has(w)) { feed(w); break; }
    pump(urgent);
    // a decoder may hold the last picture or two back until more arrives; when nothing else is waiting, ask for them
    const here = groups.get(g);
    if (here && here.fed && here.got < GOP && !flushing && !waiting.length && decoder.decodeQueueSize === 0 && performance.now() - fedAt > 70) {
      flushing = true;
      decoder.flush().catch(() => {}).finally(() => { flushing = false; });
    }
    // The film is fetched well ahead of the walker, nearest piece first and a few at a time, so that one slow answer
    // never shows; the piece just behind is kept too, for turning round.
    standing = Math.floor(f / SEG); heading = direction;
    for (let k = 1; k <= AHEAD && inFlight < TOGETHER; k++) { const n = turn(standing + direction * k, PIECES); if (n >= 0 && !pieces.has(n)) piece(n).catch(() => {}); }
    const back = turn(standing - direction, PIECES);
    if (inFlight < TOGETHER && back >= 0 && !pieces.has(back)) piece(back).catch(() => {});
  }
  decoder = new VideoDecoder({
    output: (frame) => {   // straight onto the graphics card; the decoder gets its picture back at once
      const i = Math.round(frame.timestamp / 1000), state = groups.get(Math.floor(i / GOP));
      if (state && !disposed) {
        if (!frames.has(i)) state.got++;
        const t = frames.get(i) || texture();
        gl.bindTexture(gl.TEXTURE_2D, t);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, frame);
        frames.set(i, t);
      }
      frame.close();
    },
    error: (e) => { if (!disposed) options.error?.(e); },
  });
  resize();
  // A size the visitor has chosen by hand stays in use; otherwise the window and the connection decide.
  let locked = data.video.find((v) => v.width === options.quality) || null;
  // Until the first pieces have been timed the connection is taken for an ordinary one, unless the page says it is slow.
  const chooser = createSizeChooser(data.video, sharp(), options.mbps || 30);
  useVariant(locked || chooser.current);
  // Before the walk is shown its first seconds are fetched, so that it does not begin by waiting for them.
  let primed = false;
  {
    const first = Math.floor(clamp(Math.floor(mod(gait.current, D) * FPS), 0, N - 1) / SEG);
    const wanted = Array.from({ length: FIRST }, (_, k) => turn(first + k, PIECES)).filter((k) => k >= 0);
    let got = 0;
    Promise.allSettled(wanted.map((k) => piece(k).then(() => options.progress?.(++got / wanted.length)))).then(() => { primed = true; });
    setTimeout(() => { primed = true; }, 15000);   // a slow connection is not kept waiting; the size in use will follow it down
  }

  // ---- what stands in the picture: flat things the visitor can click, or the page lays something onto
  const normal = (q) => { const a = q[1].map((v, k) => v - q[0][k]), b = q[3].map((v, k) => v - q[0][k]); return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; };
  // Corners run upper left, upper right, lower right, lower left as seen from the front. A thing is clickable from its
  // front only, unless its spot says both: true.
  const things = (data.spots || []).map((s, index) => ({ index, id: s.id, corners: s.corners, reach: s.reach || 45, both: !!s.both }));
  for (const o of things) { o.n = normal(o.corners); o.c = [0, 1, 2].map((k) => (o.corners[0][k] + o.corners[2][k]) / 2); }
  const towards = (o, c) => { const v = c.pos.map((p, k) => p - o.c[k]), d = -(v[0] * o.n[0] + v[1] * o.n[1] + v[2] * o.n[2]) / Math.hypot(...v) / Math.hypot(...o.n); return o.both ? Math.abs(d) : d; };
  let camera = null;
  function place(frame) {
    camera = poseAt(data.frames, frame, LOOP);
    const P = projector(camera, data.sensor, width, height, ASPECT);
    quads = [];
    for (const o of things) {
      const away = Math.hypot(camera.pos[0] - o.c[0], camera.pos[1] - o.c[1]);
      if (away > o.reach || towards(o, camera) < 0.12) continue;   // too far to make out, or seen nearly edge on
      const q = o.corners.map(P);
      if (q.every((p) => p.z > 0.3) && q.some((p) => p.x > 0 && p.x < width && p.y > 0 && p.y < height)) quads.push({ index: o.index, id: o.id, q, far: away, near: Math.min(1, (o.reach - away) / 12) });
    }
    quads.sort((a, b) => a.far - b.far);
  }
  // Which stop the walker has reached: the last one at or before the frame shown.
  const stopAt = (frame) => { let k = 0; (data.stops || []).forEach((s, i) => { if (frame >= s.frame) k = i; }); return k; };

  // ---- every screen refresh
  function step(now) {
    const raw = Math.min(0.1, (now - last) / 1000); last = now;
    if (held) gait.halt();
    const before = gait.current;
    const i = gait.step(raw, auto && !held, N);
    const pace = Math.abs(gait.current - before) / Math.max(raw, 1e-3);   // seconds of film per second
    if (pace > 3.5) jumped = now;   // a place chosen from the line of stops
    // On the way to a place chosen from the line of stops nothing in between is fetched or decoded.
    if (pace <= 3.5) schedule(i, gait.direction);
    let pick = i;
    if (!frames.has(pick)) { pick = -1; for (let k = 1; k < GOP * 3; k++) { const alt = turn(i - gait.direction * k, N); if (alt >= 0 && frames.has(alt)) { pick = alt; break; } } }
    const fresh = pick >= 0 && pick !== shown;
    if (fresh) {
      place(pick);
      const t = frames.get(pick);
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      if (shownTexture && shownTexture !== t && ![...frames.values()].includes(shownTexture)) spare.push(shownTexture);
      shownTexture = t; shown = pick;
      drawn = true;
    }
    // Only pictures missing while walking at the film's own pace count against the size in use. A hard scroll outruns
    // any connection for a moment, and after a jump or at a standstill the wait is for one piece, not a sign of a slow line.
    if (!ready && drawn && primed) { ready = true; options.ready?.(); }
    const underrun = ready && pick !== i && pace > 0.3 && pace <= 1.3 && now - jumped > 2500;
    if (!locked) { const next = chooser.refresh(now, underrun, sharp(), held || !gait.moving); if (next) useVariant(next); }
    if (underrun) canvas.dispatchEvent(new Event("filmgap"));   // for measuring from outside how often the walk runs dry
    const frame = pick >= 0 ? pick : i;
    if (!LOOP && auto && i >= N - 1) auto = false;   // a walk with an end stops walking on its own there
    options.frame?.({
      frame, seconds: frame / FPS, progress: frame / N, stop: stopAt(frame), camera, spots: quads, auto, moving: gait.moving,
      width: variant.width, height: variant.height,
    }, raw);
  }
  // A fault while drawing must not leave a frozen picture behind: the page then offers to start again.
  function tick(now) {
    if (disposed) return;
    try { step(now); } catch (e) { options.error?.(e); return; }
    raf = requestAnimationFrame(tick);
  }
  raf = requestAnimationFrame(tick);
  addEventListener("resize", resize);
  // The graphics card can take its memory back (a driver restart, a long sleep). The page then offers to start again.
  const lost = (e) => { e.preventDefault(); if (!disposed) options.error?.(new Error("The drawing context was lost")); };
  canvas.addEventListener("webglcontextlost", lost);

  return {
    duration: D,
    get seconds() { return LOOP ? mod(gait.current, D) : clamp(gait.current, 0, D); },
    /** The wheel, a drag or a key asks for this much more of the walk. */
    push(seconds, most) { if (!held) { auto = false; gait.push(seconds, most); } },
    /** Go on to a time in the film. */
    go(seconds) { if (!held) { auto = false; gait.leapTo(seconds, D); } },
    setAuto(on) { auto = on; if (on) gait.halt(); },
    /** While something lies open over the film (a dialogue, an enlarged picture) the walk stands still. */
    hold(on) { held = on; },
    /** The page shows the picture enlarged by this much (1 for as it is); it is then drawn that much sharper. */
    closeUp(factor) { if (factor !== closeUp) { closeUp = factor; fit(); } },
    /** Hold one size of the film (by its width), or 0 to let the window and the connection decide again. */
    quality(width) {
      locked = data.video.find((v) => v.width === width) || null;
      const want = locked || chooser.restart(sharp());
      if (want !== variant) useVariant(want);
    },
    /** The clickable thing under a point of the window, nearest first: { id, index, q } or null. */
    hit(x, y) { return quads.find((k) => insideQuad(k.q, x, y)) || null; },
    dispose() {
      disposed = true;
      cancelAnimationFrame(raf); removeEventListener("resize", resize); canvas.removeEventListener("webglcontextlost", lost);
      try { decoder.close(); } catch {}
      for (const t of new Set([...frames.values(), ...spare, shownTexture])) if (t) gl.deleteTexture(t);
      frames.clear(); groups.clear(); forget(); waiting.length = 0;
      gl.getExtension("WEBGL_lose_context")?.loseContext();
    },
  };
}
