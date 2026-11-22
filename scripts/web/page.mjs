// Looks at a page the way a visitor's browser shows it, in headless Chrome.
//   node page.mjs shot   <url> <out.png> [--wait 6] [--size 1600x900] [--run "script"] [--probe "expression"]
//   node page.mjs record <url> <out.mp4> [--seconds 8] [--size 1600x900] [--run "script"]
// --run is executed once the page has loaded (for example "walk.setAuto(true)" or "walk.go(12)"); --probe is evaluated
// at the end and its value printed. Console messages and page errors are printed too.
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

const [mode, url, outArg, ...rest] = process.argv.slice(2);
if (!["shot", "record"].includes(mode) || !url || !outArg) { console.error("usage: page.mjs shot|record <url> <out> [options]"); process.exit(2); }
const option = (name, fallback) => { const i = rest.indexOf("--" + name); return i >= 0 ? rest[i + 1] : fallback; };
const out = resolve(outArg);
const [width, height] = option("size", "1600x900").split("x").map(Number);
const chromePath = [process.env.CHROME, "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium"].find((p) => p && existsSync(p));
if (!chromePath) { console.error("Chrome was not found. Set CHROME to its executable."); process.exit(2); }

const work = mkdtempSync(join(dirname(out), ".page-"));   // beside the output, so nothing lands on the system drive
const port = 9300 + Math.floor(Math.random() * 400);
const chrome = spawn(chromePath, ["--headless=new", `--remote-debugging-port=${port}`, `--window-size=${width},${height}`, "--hide-scrollbars",
  "--autoplay-policy=no-user-gesture-required", `--user-data-dir=${join(work, "profile")}`, "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const finish = async (code) => { chrome.kill(); await sleep(400); try { rmSync(work, { recursive: true, force: true }); } catch {} process.exit(code); };

let target = null;
for (let i = 0; i < 60 && !target; i++) { await sleep(250); try { target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((t) => t.type === "page"); } catch {} }
if (!target) { console.error("Chrome did not start."); await finish(1); }
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r));
let id = 0;
const waiting = new Map(), logs = [], shots = [];
const send = (method, params = {}) => new Promise((r) => { waiting.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
ws.addEventListener("message", (e) => {
  const m = JSON.parse(e.data);
  if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m.result); waiting.delete(m.id); }
  else if (m.method === "Runtime.consoleAPICalled") logs.push(m.params.args.map((a) => a.value ?? a.description).join(" "));
  else if (m.method === "Runtime.exceptionThrown") logs.push("EXCEPTION " + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text));
  else if (m.method === "Page.screencastFrame") { shots.push([m.params.metadata.timestamp, m.params.data]); send("Page.screencastFrameAck", { sessionId: m.params.sessionId }); }
});
await send("Runtime.enable"); await send("Page.enable");
await send("Page.navigate", { url });
await sleep(2500);
const run = option("run", "");

if (mode === "shot") {
  if (run) await send("Runtime.evaluate", { expression: run, userGesture: true, awaitPromise: true });
  await sleep(parseFloat(option("wait", "6")) * 1000);
  const probe = option("probe", "");
  if (probe) console.log("PROBE", JSON.stringify((await send("Runtime.evaluate", { expression: probe, returnByValue: true, awaitPromise: true })).result?.value));
  const shot = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(out, Buffer.from(shot.data, "base64"));
  console.log("saved", out);
} else {
  // Frames are taken as screenshots as fast as they come and laid on a steady 60 a second by their time stamps.
  await send("Page.startScreencast", { format: "jpeg", quality: 85, everyNthFrame: 1 });
  if (run) await send("Runtime.evaluate", { expression: run, userGesture: true });
  await sleep(parseFloat(option("seconds", "8")) * 1000);
  await send("Page.stopScreencast");
  if (shots.length < 2) { console.error("The page drew nothing to record."); await finish(1); }
  const t0 = shots[0][0], fps = 60, total = Math.floor((shots[shots.length - 1][0] - t0) * fps);
  let k = 0;
  for (let f = 0; f < total; f++) { while (k + 1 < shots.length && shots[k + 1][0] - t0 <= f / fps) k++; writeFileSync(join(work, `f${String(f).padStart(5, "0")}.jpg`), Buffer.from(shots[k][1], "base64")); }
  spawnSync("ffmpeg", ["-y", "-v", "error", "-framerate", String(fps), "-i", join(work, "f%05d.jpg"), "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-crf", "21", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out], { stdio: "inherit" });
  console.log("recorded", shots.length, "pictures in", (shots[shots.length - 1][0] - t0).toFixed(1), "s ->", out);
}
if (logs.length) console.log(logs.slice(0, 20).join("\n"));
ws.close();
await finish(0);
