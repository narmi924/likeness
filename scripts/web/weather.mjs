// Snow, rain and blowing sand over the film. They are drawn live, so they keep falling when the walk stands still and
// never run backwards when it is scrolled back. The particles live in a box of air round the walker; when the walker
// moves or turns, the air stays where it is and everything in it slides the other way.
import { mod, poseAt } from "./walk.mjs";

export const WIND = [0.55, -0.35];   // which way the air moves over the ground
export const BLOW = 6.5;             // metres a second, the wind over the sand
const BOX = { x: 14, y0: -2.4, y1: 9.5, z0: 0.6, z1: 27 };   // metres: right, up, ahead
const EYE = 1.62;

export function createWeatherLayer(canvas, { frames, weather, sensor, calm = false, random = Math.random }) {
  const ctx = canvas.getContext("2d");
  const rnd = (a, b) => a + random() * (b - a);
  const flock = (n, make) => Array.from({ length: n }, () => ({ x: rnd(-BOX.x, BOX.x), y: rnd(BOX.y0, BOX.y1), z: rnd(BOX.z0, BOX.z1), ...make() }));
  const few = calm ? 0.3 : 1;
  const snow = flock(Math.round(1800 * few), () => ({ fall: rnd(0.7, 1.5), size: rnd(0.008, 0.025), sway: rnd(0.2, 0.7), phase: rnd(0, 6.28), a: rnd(0.55, 0.95) }));
  const rain = flock(Math.round(1700 * few), () => ({ fall: rnd(7.5, 10.5), a: rnd(0.5, 1) }));
  // grains of sand: thin, short, and nearly all of them below the knee
  const sand = flock(Math.round(1700 * few), () => ({ x: rnd(-11, 11), z: rnd(BOX.z0, 20), y: -EYE + 0.05 + random() ** 3 * 2.2, speed: rnd(0.8, 1.1), phase: rnd(0, 6.28), a: rnd(0.3, 0.75) }));
  const flake = document.createElement("canvas");
  flake.width = flake.height = 32;
  {
    const c = flake.getContext("2d"), g = c.createRadialGradient(16, 16, 0, 16, 16, 16);
    g.addColorStop(0, "rgba(240,244,252,1)"); g.addColorStop(0.45, "rgba(240,244,252,.55)"); g.addColorStop(1, "rgba(240,244,252,0)");
    c.fillStyle = g; c.fillRect(0, 0, 32, 32);
  }
  let before = null, blown = 0, gust = 1, levels = [0, 0, 0];
  const count = frames.length;
  const levelsAt = (x) => {
    const m = mod(x, count), i = Math.floor(m), k = m - i, a = weather[i], b = weather[(i + 1) % count];
    return [0, 1, 2].map((n) => a[n] + (b[n] - a[n]) * k);
  };
  const wrap = (p) => {
    p.x = mod(p.x + BOX.x, 2 * BOX.x) - BOX.x; p.y = mod(p.y - BOX.y0, BOX.y1 - BOX.y0) + BOX.y0; p.z = mod(p.z - BOX.z0, BOX.z1 - BOX.z0) + BOX.z0;
  };

  return {
    get levels() { return levels; },   // snow, rain, sand: 0 to 1
    get gust() { return gust; },       // how hard the wind blows just now, about 0.45 to 1
    get blown() { return blown; },     // metres the dust has travelled down the wind
    resize(width, height) { canvas.width = width; canvas.height = height; },
    /** Draws the weather for a film frame. dt: seconds since the last call; time: seconds, for the gusts. keepOff: photographs not to draw sand across. */
    draw(frame, dt, time, keepOff, inside) {
      const c = poseAt(frames, frame), lv = (levels = levelsAt(frame));
      gust = 0.72 + 0.2 * Math.sin(time * 0.53) + 0.08 * Math.sin(time * 1.31 + 1.7);
      blown = (blown + BLOW * gust * dt) % 4000;
      const width = canvas.width, height = canvas.height;
      const W = 1920, H = 1080, focal = (c.lens / sensor) * W, s = Math.max(width / W, height / H), ox = (width - W * s) / 2, oy = (height - H * s) / 2;
      const cy = Math.cos(c.yaw), sy = Math.sin(c.yaw), tilt = Math.tan(c.pitch);
      let side = 0, ahead = 0, turn = 0;
      if (before) {
        const dx = c.pos[0] - before.pos[0], dy = c.pos[1] - before.pos[1];
        if (Math.hypot(dx, dy) < 4) { side = dx * sy - dy * cy; ahead = dx * cy + dy * sy; turn = Math.atan2(Math.sin(c.yaw - before.yaw), Math.cos(c.yaw - before.yaw)); }
      }
      before = c;
      const ct = Math.cos(turn), st = Math.sin(turn), windX = WIND[0] * sy - WIND[1] * cy, windZ = WIND[0] * cy + WIND[1] * sy;
      const slide = (p, vx, vz) => { const X = p.x - side + vx * dt, Z = p.z - ahead + vz * dt; p.x = X * ct + Z * st; p.z = Z * ct - X * st; };
      const move = (p, vx, vy, vz) => { slide(p, vx, vz); p.y += vy * dt; wrap(p); };
      const sx = (x, z) => (W / 2 + (focal * x) / z) * s + ox, syy = (y, z) => (H / 2 - focal * (y / z - tilt) + c.shift * W) * s + oy;
      ctx.clearRect(0, 0, width, height);
      if (lv[0] > 0.004) {
        const n = Math.round(snow.length * Math.min(1, lv[0]));
        ctx.fillStyle = "rgb(240,244,252)";
        for (let i = 0; i < n; i++) {
          const p = snow[i];
          move(p, windX * 1.1 + Math.sin(time * p.sway * 2 + p.phase) * 0.35, -p.fall, windZ * 1.1);
          const r = Math.max(0.7, ((p.size * focal) / p.z) * s), X = sx(p.x, p.z), Y = syy(p.y, p.z);
          if (X < -20 || X > width + 20 || Y < -20 || Y > height + 20) continue;
          const near = p.z < 2.2 ? (p.z - BOX.z0) / (2.2 - BOX.z0) : 1;   // a flake right before the lens is a soft blur
          ctx.globalAlpha = p.a * (0.35 + 0.65 * near) * Math.min(1, lv[0] * 1.6);
          if (r < 1.3) ctx.fillRect(X - r, Y - r, 2 * r, 2 * r);
          else ctx.drawImage(flake, X - r * 1.6, Y - r * 1.6, r * 3.2, r * 3.2);
        }
      }
      if (lv[1] > 0.004) {
        const n = Math.round(rain.length * Math.min(1, lv[1]));
        ctx.strokeStyle = "rgb(206,218,236)"; ctx.lineCap = "round";
        for (const [z0, z1, alpha, thick] of [[BOX.z0, 3, 0.34, 1.9], [3, 9, 0.42, 1.3], [9, BOX.z1, 0.34, 0.9]]) {
          ctx.globalAlpha = alpha * Math.min(1, lv[1] * 1.5); ctx.lineWidth = thick * Math.max(1, s); ctx.beginPath();
          for (let i = 0; i < n; i++) {
            const p = rain[i];
            if (z0 === BOX.z0) move(p, windX * 1.6, -p.fall, windZ * 1.6);
            if (p.z < z0 || p.z >= z1) continue;
            const X = sx(p.x, p.z), Y = syy(p.y, p.z);
            if (X < -40 || X > width + 40 || Y < -60 || Y > height + 60) continue;
            const tail = 0.042 * p.a;   // the streak a drop leaves while the eye takes it in
            ctx.moveTo(X, Y); ctx.lineTo(X - ((windX * 1.6 * tail * focal) / p.z) * s, Y - ((p.fall * tail * focal) / p.z) * s);
          }
          ctx.stroke();
        }
      }
      if (lv[2] > 0.004) {
        // grains skim the ground with the wind: each a hair-thin stroke the length it travels in a moment
        const n = Math.round(sand.length * Math.min(1, lv[2]) * (0.55 + 0.45 * gust));
        ctx.strokeStyle = "rgb(232,200,150)"; ctx.lineCap = "round";
        for (const [z0, z1, alpha, thick] of [[BOX.z0, 2.5, 0.42, 1.5], [2.5, 7, 0.36, 1.1], [7, 20, 0.28, 0.8]]) {
          ctx.globalAlpha = alpha * Math.min(1, lv[2] * 1.4); ctx.lineWidth = thick * Math.max(1, s * 0.9); ctx.beginPath();
          for (let i = 0; i < n; i++) {
            const p = sand[i], v = BLOW * gust * p.speed;
            if (z0 === BOX.z0) {
              slide(p, windX * v + Math.sin(time * 0.93 + p.phase) * 0.24, windZ * v + Math.cos(time * 0.57 + p.phase) * 0.24);
              p.x = mod(p.x + 11, 22) - 11; p.z = mod(p.z - BOX.z0, 20 - BOX.z0) + BOX.z0;
            }
            if (p.z < z0 || p.z >= z1) continue;
            const y = p.y + Math.sin(time * 1.7 + p.phase) * 0.04, X = sx(p.x, p.z), Y = syy(y, p.z);
            if (X < -30 || X > width + 30 || Y < -10 || Y > height + 10 || keepOff.some((k) => inside(k.q, X, Y))) continue;
            const tail = 0.016 * v, zt = Math.max(0.3, p.z - windZ * tail);
            ctx.moveTo(X, Y); ctx.lineTo(sx(p.x - windX * tail, zt), syy(y, zt));
          }
          ctx.stroke();
        }
      }
      ctx.globalAlpha = 1;
      return c;
    },
  };
}
