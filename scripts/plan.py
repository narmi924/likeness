"""The plan to show the user before building: the route seen from above, which way it walks, every stop and what it
looks at, in the same metres as the scene. Over the map of `likeness place` when --place is given, otherwise on a
plain metre grid round the route.

    likeness plan route.json plan.png [--place <place dir>] [--around 60] [--lang zh]

One sheet: the title and length; with --place, the whole site on the left (where the walk is) and the route at full
detail on the right; one card a stop (what it is called, when the walk gets there, what it must show); the totals and
the map credits at the foot. A stop's card says `note` from its key when there is one, else its `must_see` list.
Write a first route.json (line and keys, see film/route.py) as the plan itself: the same file then drives the walk."""
import argparse
import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "film"))
import route as routes  # noqa: E402
from place import font, nice, safe_text  # noqa: E402

RED, DARK, PAPER = (0, 95, 210), (25, 25, 25), (250, 248, 243)   # the route is blue: red is the map's paths
MUTED, RULE = (82, 96, 88), (194, 195, 181)
WORDS = {
    "en": {"site": "Where the walk is", "route": "The route", "hint": "blue: the walk, wedges: what the camera sees",
           "s": "s", "fps": "fps", "m": "m", "at": "at {:.1f} s", "along": "{:.0f} m along", "stands": "stands {:g} s",
           "sees": "Must show: ", "total": "route {:.0f} m, {:.1f} s{}; eye {} m above the ground", "standing": " with {:g} s standing",
           "credit": "Map © OpenStreetMap contributors (ODbL); elevation Mapzen Terrain Tiles", "note": "note: "},
    "zh": {"site": "在哪里走", "route": "行走路线", "hint": "蓝线为行走路线，扇形为镜头视野",
           "s": "秒", "fps": "帧每秒", "m": "米", "at": "第 {:.1f} 秒", "along": "沿路 {:.0f} 米", "stands": "停 {:g} 秒",
           "sees": "要拍到：", "total": "全程 {:.0f} 米，{:.1f} 秒{}；视点离地 {} 米", "standing": "，其中停留 {:g} 秒",
           "credit": "底图 © OpenStreetMap 贡献者（ODbL），地形 Mapzen Terrain Tiles", "note": "注意："},
}


def main(argv):
    from PIL import Image, ImageDraw
    p = argparse.ArgumentParser(prog="likeness plan", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("route")
    p.add_argument("out")
    p.add_argument("--place", help="folder made by `likeness place`: draw over its map")
    p.add_argument("--around", type=float, default=None, help="metres shown round the route (default: about half the "
                   "route's extent; at least 20 m on a place map, 8 m without one)")
    p.add_argument("--lang", choices=sorted(WORDS), default="en", help="language of the sheet (en, zh)")
    a = p.parse_args(argv)
    plan = json.loads(Path(a.route).read_text(encoding="utf-8"))
    line = routes.Line(plan["line"], bool(plan.get("loop")))
    xs, ys = [q[0] for q in line.pts], [q[1] for q in line.pts]
    if a.around is None:
        a.around = max(20.0 if a.place else 8.0, 0.6 * max(max(xs) - min(xs), max(ys) - min(ys)))
    try:                                               # the same timing the walk will have
        walk, report, _, _ = routes.build(plan)
        stop_at = {s["id"]: s["frame"] / walk["fps"] for s in walk["stops"]}
        timing = report
    except Exception as error:                         # a plan may be rough: draw it anyway
        stop_at, timing = {}, {"notes": [f"timing not worked out: {error}"]}
    inset = None

    if a.place:
        import numpy as np
        from place import draw_map
        folder = Path(a.place)
        info = json.loads((folder / "place.json").read_text(encoding="utf-8"))
        if a.around:                                   # redraw the neighbourhood of the route at full detail
            h = np.load(folder / "height.npy")
            cell, full = info["cell"], (h.shape[0] - 1) * info["cell"] / 2
            cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            want = max(max(xs) - min(xs), max(ys) - min(ys)) / 2 + a.around
            j0 = max(0, int((cx - want + full) / cell)); j1 = min(h.shape[1] - 1, int(math.ceil((cx + want + full) / cell)))
            i0 = max(0, int((full - cy - want) / cell)); i1 = min(h.shape[0] - 1, int(math.ceil((full - cy + want) / cell)))
            n = min(j1 - j0, i1 - i0)
            sub = h[i0:i0 + n + 1, j0:j0 + n + 1]
            half = n * cell / 2
            centre = (-full + j0 * cell + half, full - i0 * cell - half)
            features = json.loads((folder / "features.json").read_text(encoding="utf-8"))
            scratch = Path(a.out).with_suffix(".map.png")
            drawn = draw_map(sub, half, features, info.get("name") or "", scratch, px=920, centre=centre)
            base = Image.open(scratch).convert("RGB")
            scratch.unlink()
            inset = (info, centre, half)
        else:
            drawn = info["map"]
            drawn.setdefault("centre", [0.0, 0.0]); drawn.setdefault("half", info["size"] / 2)
            base = Image.open(folder / "map.png").convert("RGB")
        m, k = drawn["margin"], drawn["pixels_per_metre"]
        (mx, my), half = drawn["centre"], drawn["half"]

        def at(q, m=m, k=k, mx=mx, my=my, half=half):
            return (m + (q[0] - mx + half) * k, m + (half - q[1] + my) * k)
        img = base
    else:
        around = a.around
        x0, x1, y0, y1 = min(xs) - around, max(xs) + around, min(ys) - around, max(ys) + around
        k = 820 / max(x1 - x0, y1 - y0)
        img = Image.new("RGB", (int((x1 - x0) * k) + 200, int((y1 - y0) * k) + 200), PAPER)

        def at(q):
            return (100 + (q[0] - x0) * k, 100 + (y1 - q[1]) * k)
        g = nice((x1 - x0) / 8)
        dd = safe_text(ImageDraw.Draw(img))
        v = math.ceil(x0 / g) * g
        while v <= x1:
            dd.line((at((v, y0))[0], 100, at((v, y0))[0], img.height - 100), fill=(205, 205, 200))
            dd.text((at((v, y0))[0], 80), f"{v:g}", fill=(90, 90, 90), font=font(18), anchor="mm")
            v += g
        v = math.ceil(y0 / g) * g
        while v <= y1:
            dd.line((100, at((x0, v))[1], img.width - 100, at((x0, v))[1]), fill=(205, 205, 200))
            dd.text((92, at((x0, v))[1]), f"{v:g}", fill=(90, 90, 90), font=font(18), anchor="rm")
            v += g

    d = safe_text(ImageDraw.Draw(img))
    w = max(4, int(min(img.size) / 400))
    pts = [at(q) for q in line.pts]
    d.line(pts + (pts[:1] if line.closed else []), fill=(255, 255, 255), width=w + 4)
    d.line(pts + (pts[:1] if line.closed else []), fill=RED, width=w)
    step = max(line.length / 12, 1e-3)                  # arrows show which way it walks
    s = step / 2
    while s < line.length:
        (x, y, _), yaw = line.point(s)
        cx, cy = at((x, y))
        r, ang = 7 * w, math.radians(yaw)
        tip = (cx + r * math.cos(ang), cy - r * math.sin(ang))
        left = (cx + r * 0.8 * math.cos(ang + 2.5), cy - r * 0.8 * math.sin(ang + 2.5))
        right = (cx + r * 0.8 * math.cos(ang - 2.5), cy - r * 0.8 * math.sin(ang - 2.5))
        d.polygon([tip, left, right], fill=RED)
        s += step

    sensor, lens0 = float(plan.get("sensor", 36)), float(plan.get("lens", 28))
    reach = 0.09 * max(img.size) / k                    # metres a view wedge reaches on the drawing
    legend, number, stops = [], 0, []
    for key in plan.get("keys", []):
        s = line.nearest(key["near"]) if "near" in key else float(key.get("at", 0))
        (x, y, _), yaw = line.point(s)
        if "target" in key:
            tx, ty = key["target"][0], key["target"][1]
            look = math.degrees(math.atan2(ty - y, tx - x))
            d.line([at((x, y)), at((tx, ty))], fill=(40, 40, 40), width=1)
        else:
            look = yaw + float(key.get("look", 0))
        fov = 2 * math.degrees(math.atan(sensor / 2 / float(key.get("lens", lens0))))
        wedge = [at((x, y))]
        for t in range(13):
            ang = math.radians(look - fov / 2 + fov * t / 12)
            wedge.append(at((x + reach * math.cos(ang), y + reach * math.sin(ang))))
        stop = key.get("stop") is not None or key is plan["keys"][0]
        stop_id = key.get("stop") or "start"
        colour = (255, 196, 40) if stop else (255, 255, 255)
        overlay = Image.new("RGBA", img.size)
        ImageDraw.Draw(overlay).polygon(wedge, fill=colour + (70,), outline=(40, 40, 40, 200))
        img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))
        d = safe_text(ImageDraw.Draw(img))
        cx, cy = at((x, y))
        if stop:
            number += 1
            r = 5 * w
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=DARK, outline=(255, 255, 255), width=2)
            d.text((cx, cy), str(number), fill=(255, 255, 255), font=font(int(r * 1.3)), anchor="mm")
            name = key.get("name")
            name = " / ".join(name) if isinstance(name, list) else (name or key.get("stop") or "start")
            hold = key.get("hold")
            when = stop_at.get(stop_id)
            legend.append(f"{number}  {name}   {s:.0f} m along" + (f", at {when:.1f} s" if when is not None else "")
                          + (f", stands {hold:g} s" if hold else ""))
            names = key.get("name")
            if isinstance(names, list) and names:
                cjk = [n for n in names if any(ord(c) > 0x2E80 for c in n)]
                names = cjk[0] if cjk and a.lang == "zh" else names[0]
            stops.append({"n": number, "name": names or stop_id, "at": when, "along": s, "hold": hold, "note": key.get("note"),
                          "sees": (plan.get("must_see") or {}).get(stop_id, [])})
        else:
            d.ellipse((cx - 2 * w, cy - 2 * w, cx + 2 * w, cy + 2 * w), fill=DARK)

    W_ = WORDS[a.lang]
    speed = float(plan.get("speed", 1.3))
    holds = sum(float(key.get("hold", 0)) for key in plan.get("keys", []))
    seconds = timing.get("seconds") or (float(plan["seconds"]) if "seconds" in plan else (sum(plan["legs"]) + holds if "legs" in plan else line.length / speed + holds))
    legend.insert(0, f"route {line.length:.0f} m{' (a loop)' if line.closed else ''}, {seconds:.1f} s"
                     f"{f' with {holds:g} s standing' if holds else ''}; eye {plan.get('eye', 1.62)} m")
    legend += [f"note: {n}" for n in timing.get("notes", [])]
    if a.place:                                        # the panel shows the map square, not its margins
        img = img.crop((m, m, m + drawn["px"], m + drawn["px"]))
    site = overview(Path(a.place), line, xs, ys, inset[1], inset[2], 920) if inset and a.place else None
    title = plan.get("title") or (inset[0].get("name") if inset else None) or Path(a.route).resolve().parent.name
    img = sheet(img, site, stops, title, seconds, plan, line, holds, timing.get("notes", []), W_, bool(a.place))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(json.dumps({"plan": str(out.resolve()), "route_m": round(line.length, 1), "seconds": round(seconds, 1),
                      "stops": [t for t in legend[1:] if not t.startswith("note:")], "notes": timing.get("notes", [])},
                     ensure_ascii=False, indent=2))


def overview(folder, line, xs, ys, box_centre, box_half, side):
    """The site round the walk, drawn afresh at the panel's size so names stay readable, with the route and the box the
    detail panel shows."""
    import numpy as np
    from PIL import Image, ImageDraw
    from place import draw_map
    info = json.loads((folder / "place.json").read_text(encoding="utf-8"))
    h = np.load(folder / "height.npy")
    cell, full = info["cell"], (h.shape[0] - 1) * info["cell"] / 2
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    want = min(full, max(200.0, box_half * 5))
    j0 = max(0, int((cx - want + full) / cell)); j1 = min(h.shape[1] - 1, int(math.ceil((cx + want + full) / cell)))
    i0 = max(0, int((full - cy - want) / cell)); i1 = min(h.shape[0] - 1, int(math.ceil((full - cy + want) / cell)))
    n = min(j1 - j0, i1 - i0)
    half = n * cell / 2
    centre = (-full + j0 * cell + half, full - i0 * cell - half)
    features = json.loads((folder / "features.json").read_text(encoding="utf-8"))
    scratch = folder / ".overview.png"
    drawn = draw_map(h[i0:i0 + n + 1, j0:j0 + n + 1], half, features, "", scratch, px=side, centre=centre)
    m, k = drawn["margin"], drawn["pixels_per_metre"]
    part = Image.open(scratch).convert("RGB").crop((m, m, m + side, m + side))
    scratch.unlink()

    def at(q):
        return ((q[0] - centre[0] + half) * k, (half - q[1] + centre[1]) * k)
    pd = safe_text(ImageDraw.Draw(part))
    tr = [at(q) for q in line.pts]
    pd.line(tr, fill=(255, 255, 255), width=8)
    pd.line(tr, fill=RED, width=4)
    (dcx, dcy), dhalf = box_centre, box_half
    pd.rectangle((*at((dcx - dhalf, dcy + dhalf)), *at((dcx + dhalf, dcy - dhalf))), outline=RED, width=3)
    return part


def sheet(detail, site, stops, title, seconds, plan, line, holds, notes, W_, with_place):
    """Title, the two panels side by side (or the route alone), a card a stop, totals and credits."""
    from PIL import Image, ImageDraw
    import textwrap
    side, margin, gap = 920, 64, 40
    panels = [(W_["site"], site), (W_["route"], detail)] if site is not None else [(W_["route"], detail)]
    width = margin * 2 + side * len(panels) + gap * (len(panels) - 1)
    per_row = max(1, min(len(stops), 4 if len(panels) > 1 else 2))
    card_w = (width - 2 * margin - (per_row - 1) * gap) // per_row
    wrap = max(16, int(card_w / 13))

    def card_lines(st):
        text = st["note"] or ((W_["sees"] + ", ".join(st["sees"])) if st["sees"] else "")
        wide = sum(ord(c) > 0x2E80 for c in text) > len(text) / 3
        return textwrap.wrap(text, wrap // 2 if wide else wrap) if text else []
    rows = [stops[i:i + per_row] for i in range(0, len(stops), per_row)]
    row_h = [150 + 34 * max([len(card_lines(st)) for st in row] + [0]) for row in rows]
    height = 170 + 60 + side + 40 + sum(row_h) + 30 + 90 + 36 * len(notes)
    img = Image.new("RGB", (width, height), PAPER)
    d = safe_text(ImageDraw.Draw(img))
    d.text((margin, 40), str(title), fill=DARK, font=font(54))
    fps = plan.get("fps", 60)
    d.text((width - margin, 66), f"{seconds:.1f} {W_['s']}    {fps} {W_['fps']}    {line.length:.0f} {W_['m']}",
           fill=MUTED, font=font(26), anchor="ra")
    d.line((margin, 150, width - margin, 150), fill=RULE, width=2)
    y = 175
    for i, (name, picture) in enumerate(panels):
        x = margin + i * (side + gap)
        d.text((x, y), name, fill=DARK, font=font(30))
        if i == len(panels) - 1:
            d.text((x + side, y + 6), W_["hint"], fill=MUTED, font=font(21), anchor="ra")
        fitted = picture.copy()
        fitted.thumbnail((side, side), Image.LANCZOS)
        img.paste(fitted, (x + (side - fitted.width) // 2, y + 55 + (side - fitted.height) // 2))
        d.rectangle((x, y + 55, x + side, y + 55 + side), outline=(181, 184, 171), width=1)
    y += 55 + side + 40
    for row, h in zip(rows, row_h):
        for j, st in enumerate(row):
            x = margin + j * (card_w + gap)
            d.ellipse((x, y, x + 44, y + 44), fill=DARK)
            d.text((x + 22, y + 22), str(st["n"]), fill=PAPER, font=font(24), anchor="mm")
            d.text((x + 60, y - 2), str(st["name"]), fill=DARK, font=font(30))
            when = [W_["at"].format(st["at"])] if st["at"] is not None else []
            when += [W_["along"].format(st["along"])] + ([W_["stands"].format(st["hold"])] if st["hold"] else [])
            d.text((x + 60, y + 44), "    ".join(when), fill=MUTED, font=font(23))
            for k, text in enumerate(card_lines(st)):
                d.text((x, y + 96 + 34 * k), text, fill=DARK, font=font(24))
        y += h
    d.line((margin, y, width - margin, y), fill=RULE, width=2)
    total = W_["total"].format(line.length, seconds, W_["standing"].format(holds) if holds else "", plan.get("eye", 1.62))
    d.text((margin, y + 26), total, fill=DARK, font=font(26))
    if with_place:
        d.text((width - margin, y + 30), W_["credit"], fill=MUTED, font=font(21), anchor="ra")
    for k, n in enumerate(notes):
        d.text((margin, y + 76 + 36 * k), W_["note"] + n, fill=(160, 60, 30), font=font(23))
    return img


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main(sys.argv[1:])
