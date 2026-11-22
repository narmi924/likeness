// The sound of a walk: wind over sand, rain, a hush under snow, and each place's own music, following where the walker
// is (not the clock), crossfading between places, and going on where it stopped when the walker comes back.
// Nothing sounds before the visitor's first click or key press; browsers do not allow it.
//
//   const sound = createSound({ tracks: [null, "/audio/harbour.opus", "/audio/old-town.opus"], rainUrl: "/audio/rain.mp3" });
//   addEventListener("pointerdown", sound.unlock, { once: true });
//   every refresh: sound.update({ levels: weather.levels, gust: weather.gust, track: placeIndex }, dt);
//
// tracks: one recording per stretch of the walk (film.json can carry which stretch each frame is in); null is silence.
// rainUrl: a recording of rain, looped (the first 20 s). Wind is made here, no file needed.

// Music files mastered at about -18 LUFS and played at 0.45 land near -25 LUFS: clearly heard at an ordinary volume,
// some way under speech in a video. Judge the levels against ordinary web video, not against silence.
// snow: the same wind as the sand, muffled and low; 0 turns it off.
export const LEVEL = { master: 1, sand: 2.04, rain: 1.8, snow: 0.72, music: 0.45 };
export const STRENGTH = { sand: 0.55, rain: 0.65 };   // how hard it blows and rains
export const FADE = 3;                                 // seconds from one place's music to the next

export function seededRandom(seed) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function windData(rate, random) {
  const data = new Float32Array(rate * 4);
  let low = 0, brown = 0;
  for (let i = 0; i < data.length; i++) {
    const noise = random() * 2 - 1;
    low += (noise - low) * 0.065;
    brown = (brown + noise * 0.018) / 1.018;
    data[i] = brown * 1.5 + low * 0.22;
  }
  // fade the loop's seam, so it can start anywhere
  const seam = Math.floor(rate * 0.12);
  for (let i = 0; i < seam; i++) {
    const mix = i / seam, tail = data.length - seam + i;
    data[tail] = data[tail] * (1 - mix) + data[i] * mix;
  }
  return data.subarray(seam);
}

/** The level a place's music moves to in one step: towards 1 where the walker is, towards 0 elsewhere, at a fixed rate. */
export function musicLevel(level, playing, dt) {
  return level + Math.max(-dt / FADE, Math.min(dt / FADE, (playing ? 1 : 0) - level));
}

export function createSound({ tracks: urls = [], rainUrl = null, enabled = true, contextFactory, fetchAudio = (...args) => fetch(...args), audioFactory = (src) => new Audio(src) } = {}) {
  let context, master, wind = null, rain = null, loadingRain = null, retryRainAt = 0, unlocked = false, disposed = false;
  const loops = new Map();
  const tracks = urls.map((src) => src && { src, el: null, level: 0, starting: false });

  function stopLoops() {
    for (const loop of loops.values()) { loop.source.stop(); loop.source.disconnect(); loop.gain.disconnect(); }
    loops.clear();
  }
  function quiet() {
    stopLoops();
    for (const t of tracks) if (t?.el) { t.el.pause(); t.el.volume = 0; t.level = 0; }
  }
  function sync() {
    if (!context || disposed) return;
    if (!enabled) { quiet(); loadingRain?.abort(); loadingRain = null; context.suspend().catch(() => {}); }
    else context.resume().catch(() => {});
  }
  function unlock() {
    if (!enabled || disposed) return;
    try {
      if (!context) {
        const Context = globalThis.AudioContext || globalThis.webkitAudioContext;
        if (!contextFactory && !Context) return;
        context = contextFactory ? contextFactory() : new Context({ latencyHint: "interactive" });
        master = context.createGain(); master.gain.value = LEVEL.master;
        master.connect(context.destination);
        const channels = [0, 1].map((channel) => windData(24000, seededRandom(115 * 9127 + channel * 1777)));
        wind = context.createBuffer(2, channels[0].length, 24000);
        channels.forEach((data, i) => wind.copyToChannel(data, i));
        for (const t of tracks) if (t) { t.el = audioFactory(t.src); t.el.preload = "auto"; t.el.loop = true; t.el.volume = 0; }
      }
      unlocked = true;
      sync();
    } catch { /* without audio the walk still works */ }
  }
  function rainBuffer() {
    if (rain) return rain;
    if (!rainUrl) return null;
    if (loadingRain || context.currentTime < retryRainAt) return null;
    const controller = new AbortController();
    loadingRain = controller;
    // Decoded once, then Web Audio loops the recording without a new request or a timer at the seam.
    Promise.resolve().then(async () => {
      const response = await fetchAudio(rainUrl, { signal: controller.signal });
      if (!response.ok) throw new Error("Rain recording unavailable");
      const decoded = await context.decodeAudioData(await response.arrayBuffer());
      if (!disposed && loadingRain === controller) rain = decoded;
    }).catch(() => {
      if (!disposed && !controller.signal.aborted) retryRainAt = context.currentTime + 10;
    }).finally(() => { if (loadingRain === controller) loadingRain = null; });
    return null;
  }
  function ambience(kind, intensity) {
    let loop = loops.get(kind);
    if (intensity < 0.003) {
      if (loop) { loop.source.stop(); loop.source.disconnect(); loop.gain.disconnect(); loops.delete(kind); }
      return;
    }
    if (!loop) {
      const buffer = kind === "rain" ? rainBuffer() : wind;
      if (!buffer) return;
      const source = context.createBufferSource(), gain = context.createGain();
      source.buffer = buffer; source.loop = true; gain.gain.value = 0;
      if (kind === "rain") { source.loopStart = 0; source.loopEnd = Math.min(20, buffer.duration); }
      if (kind === "snow") {
        const soft = context.createBiquadFilter(); soft.type = "lowpass"; soft.frequency.value = 380;
        source.playbackRate.value = 0.8;
        source.connect(soft).connect(gain).connect(master);
      } else source.connect(gain).connect(master);
      // The rain recording keeps its own opening fade; the wind starts anywhere.
      source.start(0, kind === "rain" ? 0 : Math.random() * buffer.duration);
      loop = { source, gain }; loops.set(kind, loop);
    }
    loop.gain.gain.setTargetAtTime(intensity * LEVEL[kind], context.currentTime, 0.18);
  }
  function music(track, dt) {
    tracks.forEach((t, i) => {
      if (!t?.el) return;
      t.level = musicLevel(t.level, i === track, dt);
      // The music follows the place, not the clock: a recording that is left stays where it was and goes on from there.
      t.el.volume = Math.max(0, Math.min(1, LEVEL.music * Math.sin((t.level * Math.PI) / 2)));
      if (t.level > 0.001) {
        if (t.el.paused && !t.starting) { t.starting = true; Promise.resolve(t.el.play()).catch(() => {}).finally(() => { t.starting = false; }); }
      } else if (!t.el.paused) t.el.pause();
    });
  }
  return {
    unlock,
    setEnabled(value) { enabled = Boolean(value); if (enabled) unlock(); else sync(); },
    /** levels: [snow, rain, sand] 0 to 1; gust: how hard the wind blows now; track: whose music; dt: seconds since the last call. */
    update({ levels, gust, track }, dt) {
      if (!enabled || !unlocked || context?.state !== "running") return;
      ambience("sand", levels[2] * STRENGTH.sand * (gust + 0.13));
      ambience("rain", levels[1] * STRENGTH.rain);
      if (LEVEL.snow) ambience("snow", levels[0]);
      music(track, dt);
    },
    snapshot() { return { enabled, unlocked, state: context?.state || "locked", loops: [...loops.keys()], music: tracks.map((t) => (t ? Number(t.level.toFixed(3)) : null)) }; },
    dispose() {
      if (disposed) return;
      quiet();
      disposed = true;
      loadingRain?.abort();
      for (const t of tracks) if (t?.el) { t.el.removeAttribute?.("src"); t.el.load?.(); }
      master?.disconnect(); context?.close().catch(() => {});
    },
  };
}
