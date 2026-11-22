"""A real place from open data, to plan on and to build on.

    likeness place <lat> <lon> <out dir> [--size 1200] [--cell 2] [--zoom 14] [--name "..."]

Heights come from the Terrarium elevation tiles on AWS Open Data (mostly SRTM, one sample about every 30 m outside
the US, so cliffs and terraces are smoothed away: build those yourself). Outlines come from OpenStreetMap through
the Overpass API. Everything is in metres: x east, y north, origin at the centre point, heights above sea level.
The out dir gets:

    height.npy     float32 heights, one every --cell metres, row 0 the north edge
    place.json     centre, size, cell, height range, how the map's pixels map to metres, sources
    features.json  OpenStreetMap outlines: kind, name, tags, closed, points [[x, y], ...]
    map.png        shaded relief, contours, outlines, a metre grid labelled from the centre, scale bar, north arrow
    sources.md     one line per source, to copy into the work's own sources.md

In a build script: place = outdoor.place("<out dir>"); outdoor.land(place.extent, place.height, cell=2).
Draw the route and stops over the map with `likeness plan route.json plan.png --place <out dir>`."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import home  # noqa: E402

TILE = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            "https://overpass.private.coffee/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]
AGENT = {"User-Agent": "likeness-place/1.0"}
M_PER_DEG = 111320.0
PATHS = {"footway", "path", "steps", "pedestrian", "track", "bridleway", "cycleway", "living_street"}


def need():
    try:
        import numpy  # noqa: F401
        import PIL  # noqa: F401
    except ImportError:
        raise SystemExit("`likeness place` needs numpy and Pillow: python -m pip install numpy pillow")


def to_local(lat0, lon0):
    k = M_PER_DEG * math.cos(math.radians(lat0))
    return lambda lat, lon: ((lon - lon0) * k, (lat - lat0) * M_PER_DEG)


def to_latlon(lat0, lon0):
    k = M_PER_DEG * math.cos(math.radians(lat0))
    return lambda x, y: (lat0 + y / M_PER_DEG, lon0 + x / k)


# ---------------------------------------------------------------- heights
def heights(lat0, lon0, size, cell, zoom):
    import numpy as np
    from PIL import Image
    import io
    n = int(round(size / cell)) + 1
    half = (n - 1) * cell / 2
    xs = -half + np.arange(n) * cell
    ys = half - np.arange(n) * cell
    X, Y = np.meshgrid(xs, ys)
    lat = lat0 + Y / M_PER_DEG
    lon = lon0 + X / (M_PER_DEG * math.cos(math.radians(lat0)))
    world = 256 * 2 ** zoom
    px = (lon + 180) / 360 * world
    s = np.sin(np.radians(lat))
    py = (0.5 - np.log((1 + s) / (1 - s)) / (4 * math.pi)) * world
    tx0, tx1, ty0, ty1 = int(px.min() // 256), int(px.max() // 256), int(py.min() // 256), int(py.max() // 256)
    tiles = [(x, y) for y in range(ty0, ty1 + 1) for x in range(tx0, tx1 + 1)]
    if len(tiles) > 400:
        raise SystemExit(f"{len(tiles)} elevation tiles: use a smaller --size or a lower --zoom.")
    cache = home.home() / "cache" / "terrarium"
    cache.mkdir(parents=True, exist_ok=True)

    def get(t):
        f = cache / f"{zoom}-{t[0]}-{t[1]}.png"
        if not f.is_file():
            data = urlopen(Request(TILE.format(z=zoom, x=t[0], y=t[1]), headers=AGENT), timeout=60).read()
            f.write_bytes(data)
        rgb = np.asarray(Image.open(io.BytesIO(f.read_bytes())).convert("RGB"), np.float32)
        return rgb[..., 0] * 256 + rgb[..., 1] + rgb[..., 2] / 256 - 32768

    mosaic = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256), np.float32)
    with ThreadPoolExecutor(8) as pool:
        for (x, y), h in zip(tiles, pool.map(get, tiles)):
            mosaic[(y - ty0) * 256:(y - ty0 + 1) * 256, (x - tx0) * 256:(x - tx0 + 1) * 256] = h
    fx = np.clip(px - tx0 * 256 - 0.5, 0, mosaic.shape[1] - 1.001)
    fy = np.clip(py - ty0 * 256 - 0.5, 0, mosaic.shape[0] - 1.001)
    x0, y0 = fx.astype(int), fy.astype(int)
    ax, ay = fx - x0, fy - y0
    h = (mosaic[y0, x0] * (1 - ax) * (1 - ay) + mosaic[y0, x0 + 1] * ax * (1 - ay)
         + mosaic[y0 + 1, x0] * (1 - ax) * ay + mosaic[y0 + 1, x0 + 1] * ax * ay)
    metres_per_sample = 40075016.7 * math.cos(math.radians(lat0)) / world
    return h.astype(np.float32), half, len(tiles), metres_per_sample


# ---------------------------------------------------------------- outlines
def kind_of(tags, closed):
    if "building" in tags or tags.get("historic") in ("ruins", "building"):
        return "building"
    barrier = tags.get("barrier")
    if barrier in ("wall", "retaining_wall", "city_wall"):
        return "wall"
    if barrier:
        return "barrier"
    if "highway" in tags:
        return "path" if tags["highway"] in PATHS else "road"
    if "waterway" in tags or tags.get("natural") in ("water", "spring") or "water" in tags:
        return "water"
    if tags.get("natural") in ("cliff", "ridge", "arete", "earth_bank"):
        return "cliff"
    if "railway" in tags:
        return "rail"
    return "area" if closed else "line"


def outlines(lat0, lon0, half):
    ll = to_latlon(lat0, lon0)
    s, w = ll(-half, -half)
    n, e = ll(half, half)
    box = f"({s:.6f},{w:.6f},{n:.6f},{e:.6f})"
    keys = ["building", "barrier", "highway", "landuse", "natural", "leisure", "waterway", "water", "historic",
            "tourism", "man_made", "amenity", "railway", "place"]
    query = "[out:json][timeout:120];(" + "".join(f'nwr["{k}"]{box};' for k in keys) + ");out geom;"
    errors = []
    for url in OVERPASS:
        try:
            raw = urlopen(Request(url, data=urlencode({"data": query}).encode(), headers=AGENT), timeout=150).read()
            elements = json.loads(raw)["elements"]
            break
        except Exception as error:  # a busy or unreachable mirror: try the next
            errors.append(f"{url}: {error}")
    else:
        return [], None, errors
    xy = to_local(lat0, lon0)
    out = []

    def add(tags, geometry, eid):
        pts = [[round(v, 2) for v in xy(g["lat"], g["lon"])] for g in geometry if g]
        if not pts:
            return
        closed = len(pts) > 3 and pts[0] == pts[-1]
        if closed:
            pts = pts[:-1]
        kind = "point" if len(pts) == 1 else kind_of(tags, closed)
        out.append({"kind": kind, "name": tags.get("name:en") or tags.get("name"), "closed": closed, "osm": eid,
                    "tags": tags, "points": pts})

    for el in elements:
        tags = el.get("tags", {})
        if el["type"] == "node":
            if tags:
                add(tags, [{"lat": el["lat"], "lon": el["lon"]}], f"node/{el['id']}")
        elif el["type"] == "way":
            add(tags, el.get("geometry", []), f"way/{el['id']}")
        else:
            for m in el.get("members", []):
                if m.get("type") == "way" and m.get("role", "outer") in ("outer", "") and m.get("geometry"):
                    add(tags, m["geometry"], f"relation/{el['id']}")
    return out, url, errors


# ---------------------------------------------------------------- map
def font(size):
    from PIL import ImageFont
    for name in ("msyh.ttc", "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
                 "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
                 "arial.ttf", "DejaVuSans.ttf", "/System/Library/Fonts/Helvetica.ttc",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def safe_text(draw):
    """Bitmap fonts take no anchor: drop it rather than fail."""
    plain = draw.text

    def text(xy, s, anchor=None, **kw):
        try:
            plain(xy, s, anchor=anchor, **kw)
        except (ValueError, TypeError):
            plain(xy, s, **kw)
    draw.text = text
    return draw


def nice(value, steps=(1, 2, 2.5, 5)):
    p = 10 ** math.floor(math.log10(value))
    return min((s * p for s in steps + tuple(10 * s for s in steps)), key=lambda c: abs(c - value))


def draw_map(h, half, features, title, path, px=2000, margin=80, centre=(0.0, 0.0)):
    """The map of a square of heights `h` (row 0 north) whose centre is at `centre` and half side `half`, in metres."""
    import numpy as np
    from PIL import Image, ImageDraw
    n = h.shape[0]
    cell = 2 * half / (n - 1)
    gy, gx = np.gradient(h, cell)
    dzdx, dzdy = gx, -gy                                  # rows run south
    norm = np.sqrt(dzdx ** 2 + dzdy ** 2 + 1)
    light = np.array([-0.5, 0.5, 0.7071])
    light /= np.linalg.norm(light)
    shade = np.clip((-dzdx * light[0] - dzdy * light[1] + light[2]) / norm, 0, 1)
    lo, hi = float(h.min()), float(h.max())
    t = (h - lo) / max(1e-6, hi - lo)
    low, high = np.array([178, 190, 160], np.float32), np.array([236, 230, 214], np.float32)
    colour = (low[None, None] * (1 - t[..., None]) + high[None, None] * t[..., None]) * (0.55 + 0.45 * shade[..., None])
    relief = Image.fromarray(np.clip(colour, 0, 255).astype(np.uint8)).resize((px, px), Image.BICUBIC)
    big = np.asarray(Image.fromarray(h).resize((px, px), Image.BILINEAR))
    step = nice(max(1.0, (hi - lo) / 25))
    band = np.floor(big / step)
    edge = np.zeros_like(band, bool)
    edge[:, 1:] |= band[:, 1:] != band[:, :-1]
    edge[1:, :] |= band[1:, :] != band[:-1, :]
    major = edge & (np.floor(big / (step * 5)) != np.floor(np.roll(big, 1, 0) / (step * 5)))
    rel = np.asarray(relief).astype(np.float32)
    rel[edge] = rel[edge] * 0.72 + np.array([110, 90, 70]) * 0.28
    rel[major] = rel[major] * 0.45 + np.array([110, 90, 70]) * 0.55
    layer = Image.fromarray(rel.astype(np.uint8))
    d = safe_text(ImageDraw.Draw(layer))           # drawn on the map itself, so nothing runs past its edge
    k = px / (2 * half)

    cx, cy = centre

    def at(p):
        return ((p[0] - cx + half) * k, (half - p[1] + cy) * k)

    styles = {"area": ((120, 150, 100), None, 1), "water": ((70, 120, 170), (150, 190, 220), 2),
              "road": ((130, 130, 130), None, 4), "path": ((190, 70, 40), None, 2), "rail": ((60, 60, 60), None, 2),
              "cliff": ((70, 60, 55), None, 2), "barrier": ((110, 90, 80), None, 1), "line": ((120, 120, 120), None, 1),
              "wall": ((60, 42, 32), None, 3), "building": ((55, 40, 32), (120, 92, 74), 1)}
    order = ["area", "water", "line", "barrier", "cliff", "road", "rail", "path", "wall", "building"]
    for kind in order:
        line, fill, width = styles[kind]
        for f in features:
            if f["kind"] != kind:
                continue
            pts = [at(p) for p in f["points"]]
            if f["closed"] and fill:
                d.polygon(pts, fill=fill, outline=line)
            elif f["closed"]:
                d.line(pts + pts[:1], fill=line, width=width)
            else:
                d.line(pts, fill=line, width=width)
    small, mid = font(18), font(24)
    taken = []
    named = [f for f in features if f["kind"] == "point" and f["name"]]
    named.sort(key=lambda f: ("ele" not in f["tags"], len(f["name"])))     # peaks first, then short names
    for f in named:
        x, y = at(f["points"][0])
        if not (0 <= x < px and 0 <= y < px):
            continue
        ele = f["tags"].get("ele")
        label = f["name"] + (f" {ele} m" if ele else "")
        box = d.textbbox((x + 8, y - 10), label, font=small) if hasattr(d, "textbbox") else (x, y - 10, x + 9 * len(label), y + 10)
        if any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3] for b in taken):
            d.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(30, 30, 30))
            continue
        taken.append(box)
        d.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(30, 30, 30))
        d.text((x + 8, y - 10), label, fill=(20, 20, 20), font=small)
    canvas = Image.new("RGB", (px + 2 * margin, px + 2 * margin + 60), (250, 248, 243))
    canvas.paste(layer, (margin, margin))
    d = safe_text(ImageDraw.Draw(canvas))

    def at(p):
        return (margin + (p[0] - cx + half) * k, margin + (half - p[1] + cy) * k)

    grid = nice(2 * half / 10)
    for axis, c in ((0, cx), (1, cy)):
        g = math.ceil((c - half) / grid) * grid
        while g <= c + half + 1e-6:
            if axis == 0:
                x, _ = at((g, cy))
                d.line((x, margin, x, margin + px), fill=(90, 90, 90), width=1)
                d.text((x, margin - 26), f"{g:g}", fill=(40, 40, 40), font=small, anchor="mm")
            else:
                _, y = at((cx, g))
                d.line((margin, y, margin + px, y), fill=(90, 90, 90), width=1)
                d.text((margin - 8, y), f"{g:g}", fill=(40, 40, 40), font=small, anchor="rm")
            g += grid
    d.rectangle((margin, margin, margin + px, margin + px), outline=(40, 40, 40), width=2)
    bar = nice(2 * half / 5)
    bx, by = margin + 30, margin + px - 40
    d.rectangle((bx - 10, by - 34, bx + bar * k + 70, by + 14), fill=(250, 248, 243))
    d.line((bx, by, bx + bar * k, by), fill=(20, 20, 20), width=6)
    d.text((bx + bar * k + 10, by), f"{bar:g} m", fill=(20, 20, 20), font=mid, anchor="lm")
    nx, ny = margin + px - 60, margin + 70
    d.polygon([(nx, ny - 45), (nx - 18, ny + 10), (nx, ny), (nx + 18, ny + 10)], fill=(20, 20, 20))
    d.text((nx, ny + 30), "N", fill=(20, 20, 20), font=mid, anchor="mm")
    d.text((margin, margin + px + 30), f"{title}   grid {grid:g} m, metres from the place centre (x east, y north)   contours every {step:g} m, "
           f"thick every {step * 5:g} m   heights {lo:.0f} to {hi:.0f} m", fill=(20, 20, 20), font=small, anchor="lm")
    canvas.save(path)
    return {"px": px, "margin": margin, "pixels_per_metre": k, "centre": [cx, cy], "half": half, "grid": grid, "contours": step}


def main(argv):
    need()
    import numpy as np
    p = argparse.ArgumentParser(prog="likeness place", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("lat", type=float)
    p.add_argument("lon", type=float)
    p.add_argument("out")
    p.add_argument("--size", type=float, default=1200, help="side of the square, metres (1200)")
    p.add_argument("--cell", type=float, help="metres between height samples (size / 600, at least 1)")
    p.add_argument("--zoom", type=int, default=14, help="elevation tile zoom, 10 to 15 (14: about 9 m a tile pixel)")
    p.add_argument("--name", default="")
    a = p.parse_args(argv)
    out = Path(a.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    cell = a.cell or max(1.0, a.size / 600)
    h, half, tiles, sample = heights(a.lat, a.lon, a.size, cell, a.zoom)
    np.save(out / "height.npy", h)
    features, mirror, errors = outlines(a.lat, a.lon, half)
    (out / "features.json").write_text(json.dumps(features, ensure_ascii=False), encoding="utf-8")
    n = h.shape[0]
    centre = float(h[n // 2, n // 2])
    title = (a.name + "   " if a.name else "") + f"{a.lat:.5f}, {a.lon:.5f}"
    drawn = draw_map(h, half, features, title, out / "map.png")
    counts = {}
    for f in features:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    info = {"name": a.name, "lat": a.lat, "lon": a.lon, "size": 2 * half, "cell": cell, "samples": n,
            "centre_height": round(centre, 2), "lowest": round(float(h.min()), 2), "highest": round(float(h.max()), 2),
            "elevation": {"source": TILE, "zoom": a.zoom, "tiles": tiles, "metres_per_tile_pixel": round(sample, 2)},
            "outlines": {"source": mirror, "counts": counts}, "map": drawn,
            "frame": "metres, x east, y north, origin at lat/lon; heights above sea level"}
    (out / "place.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "sources.md").write_text(
        "- Elevation: Terrarium tiles, Mapzen and Tilezen on AWS Open Data (SRTM and others). "
        "https://registry.opendata.aws/terrain-tiles/\n"
        + ("- Outlines: © OpenStreetMap contributors, ODbL. https://www.openstreetmap.org/copyright\n" if mirror else ""),
        encoding="utf-8")
    result = {"folder": str(out), "map": str(out / "map.png"), "size_m": 2 * half, "cell_m": cell,
              "heights_m": [info["lowest"], info["highest"]], "centre_height_m": info["centre_height"],
              "tile_pixel_m": round(sample, 1), "elevation_source_detail_m": 30,
              "note": "the elevation data has a sample about every 30 m: ridges, cliffs and terraces are smoothed, take them from photographs",
              "outlines": counts}
    if errors and not mirror:
        result["outlines_failed"] = errors
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main(sys.argv[1:])
