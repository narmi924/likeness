// A walk through a scene, played from a film rendered beforehand. These are the parts that need no browser: where the
// camera is at a frame, where a point of the world falls on the screen, how scrolling becomes motion, and which size of
// the film to fetch.

export const mod = (a, n) => ((a % n) + n) % n;
export const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
/** How far apart two positions are on a loop of n. */
export const ring = (a, b, n) => { const d = Math.abs(a - b) % n; return Math.min(d, n - d); };

/** The value in force at a frame, from a table of [first frame, value] rows. */
export function lookup(table, frame) {
  let value = 0;
  for (const [first, v] of table) if (frame >= first) value = v;
  return value;
}

/** The camera at a (fractional) frame: position, yaw and pitch in radians, lens in mm, upward lens shift. */
export function poseAt(frames, x, loop = true) {
  const n = frames.length, m = loop ? mod(x, n) : clamp(x, 0, n - 1), i = Math.floor(m), k = m - i, a = frames[i], b = frames[loop ? (i + 1) % n : Math.min(i + 1, n - 1)];
  const mix = (p, q) => p + (q - p) * k;
  let turn = b[3] - a[3];
  if (turn > 180) turn -= 360;
  if (turn < -180) turn += 360;
  return {
    pos: [mix(a[0], b[0]), mix(a[1], b[1]), mix(a[2], b[2])],
    yaw: ((a[3] + turn * k) * Math.PI) / 180,
    pitch: (mix(a[4], b[4]) * Math.PI) / 180,
    lens: mix(a[5], b[5]),
    shift: mix(a[6], b[6]),
  };
}

/** The camera's forward, right and up directions in the world. */
export function axes(c) {
  const cy = Math.cos(c.yaw), sy = Math.sin(c.yaw), cp = Math.cos(c.pitch), sp = Math.sin(c.pitch);
  return { fwd: [cp * cy, cp * sy, sp], rgt: [sy, -cy, 0], up: [-sp * cy, -sp * sy, cp] };
}

/**
 * Maps a point of the world to the window. The film covers the window, so part of it may be cropped.
 * aspect: the film's width over its height. Returns { z: distance ahead, x, y: window pixels, u, v: position in the
 * film's own picture, 0 to 1 }.
 */
export function projector(c, sensor, width, height, aspect = 16 / 9) {
  const { fwd, rgt, up } = axes(c);
  const W = 1920, H = W / aspect, focal = (c.lens / sensor) * W;
  const s = Math.max(width / W, height / H), ox = (width - W * s) / 2, oy = (height - H * s) / 2;
  return (p) => {
    const v = [p[0] - c.pos[0], p[1] - c.pos[1], p[2] - c.pos[2]];
    const z = v[0] * fwd[0] + v[1] * fwd[1] + v[2] * fwd[2];
    const x = W / 2 + (focal * (v[0] * rgt[0] + v[1] * rgt[1] + v[2] * rgt[2])) / z;
    const y = H / 2 - (focal * (v[0] * up[0] + v[1] * up[1] + v[2] * up[2])) / z + c.shift * W;
    return { z, x: x * s + ox, y: y * s + oy, u: x / W, v: y / H };
  };
}

/** Whether a window point lies inside a quadrilateral given by four corners in order. */
export function insideQuad(q, x, y) {
  let sign = 0;
  for (let i = 0; i < 4; i++) {
    const a = q[i], b = q[(i + 1) % 4], cross = (b.x - a.x) * (y - a.y) - (b.y - a.y) * (x - a.x);
    if (cross !== 0) {
      if (sign && Math.sign(cross) !== sign) return false;
      sign = Math.sign(cross);
    }
  }
  return true;
}

// A flat w × h element laid onto four window points, as a CSS matrix3d.
const adjugate = (m) => [m[4] * m[8] - m[5] * m[7], m[2] * m[7] - m[1] * m[8], m[1] * m[5] - m[2] * m[4], m[5] * m[6] - m[3] * m[8], m[0] * m[8] - m[2] * m[6], m[2] * m[3] - m[0] * m[5], m[3] * m[7] - m[4] * m[6], m[1] * m[6] - m[0] * m[7], m[0] * m[4] - m[1] * m[3]];
const multiply = (a, b) => { const c = Array(9); for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) { let s = 0; for (let k = 0; k < 3; k++) s += a[3 * i + k] * b[3 * k + j]; c[3 * i + j] = s; } return c; };
const basis = (p) => {
  const m = [p[0].x, p[1].x, p[2].x, p[0].y, p[1].y, p[2].y, 1, 1, 1], a = adjugate(m);
  const v = [a[0] * p[3].x + a[1] * p[3].y + a[2], a[3] * p[3].x + a[4] * p[3].y + a[5], a[6] * p[3].x + a[7] * p[3].y + a[8]];
  return multiply(m, [v[0], 0, 0, 0, v[1], 0, 0, 0, v[2]]);
};
export function quadMatrix(w, h, corners) {
  const t = multiply(basis(corners), adjugate(basis([{ x: 0, y: 0 }, { x: w, y: 0 }, { x: w, y: h }, { x: 0, y: h }])));
  const n = t.map((v) => v / t[8]);
  return `matrix3d(${n[0]},${n[3]},0,${n[6]},${n[1]},${n[4]},0,${n[7]},0,0,1,0,${n[2]},${n[5]},0,${n[8]})`;
}

/**
 * How scrolling moves the walk. Scrolling does not drag the picture about: what is scrolled is owed, and the walk pays
 * it off at a steady pace (the film's own, or twice or three times that when scrolled hard), easing in and out. A place
 * chosen from the line of stops is covered quickly instead. Positions are seconds of film and keep counting through loops.
 */
export function createGait(fps, start = 0, loop = true) {
  const HEED = 0.35, BRAKE = 0.22, EASE = 0.12;   // seconds: how long scrolling is remembered, stopping distance, how fast the speed changes
  let current = start, owed = 0, asked = 0, pace = 1, speed = 0, leap = 0, beat = 1 / 60, direction = 1;
  return {
    get current() { return current; },
    get direction() { return direction; },
    get moving() { return Math.abs(speed) > 0.02 || leap !== 0; },
    set(seconds) { current = seconds; owed = 0; leap = 0; speed = 0; },
    /** The wheel, a drag or a key asks for this much more of the walk. */
    push(seconds, most) {
      leap = 0;
      asked += Math.abs(seconds) / HEED;
      if (owed * seconds < 0) owed = 0;   // turning round: forget what was still owed the other way
      const cap = most || 0.55 * pace;
      owed = clamp(owed + seconds, -cap, cap);
    },
    /** Go on to a time in the film. Round a loop it is always forwards, so that the walk keeps its order. */
    leapTo(seconds, duration) {
      owed = 0;
      leap = loop ? mod(seconds - mod(current, duration), duration) : seconds - current;
    },
    halt() { owed = 0; leap = 0; },
    /** One screen refresh. raw: seconds since the last one. Returns the frame to show. */
    step(raw, auto, count) {
      // The screen's own beat: when it is steady the walk moves by exactly that much each time, so that no frame is
      // shown twice or skipped by accident.
      beat += (raw - beat) * 0.04;
      const dt = Math.abs(raw - beat) < beat * 0.3 ? beat : raw;
      asked *= Math.exp(-dt / HEED);
      pace = asked > (pace >= 3 ? 2.7 : 3.2) ? 3 : asked > (pace >= 2 ? 1.3 : 1.6) ? 2 : 1;
      const before = current;
      if (leap !== 0) {
        const part = leap * (1 - Math.exp(-dt * 7));
        current += part; leap -= part; speed = 0;
        if (Math.abs(leap) < 0.5 / fps) { current += leap; leap = 0; }
      } else {
        const want = auto ? 1 : Math.sign(owed) * Math.min(pace, Math.abs(owed) / BRAKE);
        speed += (want - speed) * (1 - Math.exp(-dt / EASE));
        let move = speed * dt;
        const per = speed * fps * dt, whole = Math.round(per);
        if (whole !== 0 && Math.abs(per - whole) < 0.06) move = (Math.round(current * fps) + whole) / fps - current;   // whole frames, landing exactly on one
        if (!auto) {
          if (Math.abs(move) >= Math.abs(owed)) { move = owed; speed *= 0.5; }
          owed -= move;
        }
        current += move;
      }
      if (!loop) {   // a walk with two ends stops at them
        const end = (count - 1) / fps;
        if (current <= 0 || current >= end) { current = clamp(current, 0, end); if (owed * (current ? 1 : -1) > 0) owed = 0; if (auto && current) speed = 0; }
      }
      if (Math.abs(current - before) > 1e-6) direction = current > before ? 1 : -1;
      return loop ? Math.floor(mod(current * fps + 1e-4, count)) : clamp(Math.floor(current * fps + 1e-4), 0, count - 1);
    },
  };
}

/**
 * Which size of the film to fetch: the smallest that is sharp enough for the window, stepped down until the
 * connection can carry it with room to spare. need: device pixels across the film; mbps: measured or hinted speed.
 */
export function pickRung(video, need, mbps) {
  const sizes = video.slice().sort((a, b) => a.width - b.width);
  let i = sizes.findIndex((v) => v.width >= need * 0.9);
  if (i < 0) i = sizes.length - 1;
  while (i > 0 && mbps && sizes[i].rate * 1.4 > mbps * 1e6) i--;
  return sizes[i];
}

/**
 * Which size of the film to use as the walk goes on. It starts from what the window needs. When pictures stop arriving
 * in time at walking pace it steps down one size and leaves the size that failed alone for a while, longer after each
 * failure, so that a connection on the edge does not swing between two sizes. Changing size means fetching the place
 * where the walker stands again, so a sharper size is only taken while the walk stands still.
 */
export function createSizeChooser(video, need, mbps = 30) {
  const sizes = video.slice().sort((a, b) => a.width - b.width);
  let current = pickRung(sizes, need, mbps), late = 0, barred = Infinity, barredUntil = 0, wait = 20, checked = -Infinity, settling = 0;
  const allowed = () => { const open = sizes.filter((v) => v.width < barred); return open.length ? open : sizes.slice(0, 1); };
  return {
    get current() { return current; },
    get mbps() { return mbps; },
    /** A piece has arrived: so many bytes in so many seconds. */
    arrived(bytes, seconds) {
      const measured = Math.min(400, (bytes * 8) / Math.max(seconds, 0.015) / 1e6);
      mbps += (measured - mbps) * (measured < mbps ? 0.6 : 0.3);   // bad news is believed sooner than good
    },
    /**
     * Once per screen refresh. now: milliseconds. missing: the picture wanted was not there although the walk went no
     * faster than its own pace. still: nothing is moving, so a sharper size may be taken without being noticed.
     * Returns the size to change to, or null.
     */
    refresh(now, missing, need, still) {
      // Right after a change the new size has yet to arrive; that gap is not held against it.
      late = missing && now > settling ? late + 1 : Math.max(0, late - 0.02);
      if (now - checked < 1500) return null;
      checked = now;
      if (now > barredUntil) barred = Infinity;
      let want;
      if (late > 45) {   // about three quarters of a second without its picture
        const i = sizes.indexOf(current);
        late = 0;
        if (i === 0) return null;
        barred = current.width; barredUntil = now + wait * 1000; wait = Math.min(wait * 2, 160);
        want = sizes[i - 1];
      } else {
        want = pickRung(allowed(), need, mbps);
        if (want.width > current.width && !still) return null;
      }
      if (want === current) return null;
      current = want; late = 0; settling = now + 1500;
      return want;
    },
    /** Begin again from the window and what has been measured, as after a size held by hand is let go. */
    restart(need) { current = pickRung(allowed(), need, mbps); late = 0; return current; },
  };
}
