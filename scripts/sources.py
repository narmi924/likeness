"""Find reference photographs and licensed 3D assets, and fetch a selected asset locally."""

import argparse
import concurrent.futures
from http.client import HTTPException
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from urllib import error, parse, request
import uuid
import zipfile

from studio import PROJECT


KIT = "https://www.blendkit.com"
SF = "https://api.sketchfab.com/v3"
PH = "https://api.polyhaven.com"
CG = "https://ambientcg.com/api/v2/full_json"
PROVIDERS = ("sketchfab", "blenderkit", "polyhaven", "ambientcg", "commons")
# Wikimedia asks every client to say who it is and where to find out more
AGENT = "Likeness/1.0 (https://github.com/narmi924/likeness; 3D reference research)"


def credential(provider):
    var, key = {"sketchfab": ("SKETCHFAB_TOKEN", "sketchfab_token"),
                "blenderkit": ("BLENDERKIT_API_KEY", "blenderkit_api_key"),
                "tripo": ("TRIPO_API_KEY", "tripo_api_key")}[provider]
    if os.environ.get(var):
        return os.environ[var].strip()
    path = os.environ.get("BLENDER_STUDIO_ACCOUNTS")
    pointer = PROJECT / ".runtime" / "asset-accounts.path"
    if not path and pointer.is_file():
        path = pointer.read_text(encoding="utf-8-sig").strip()
    if path and Path(path).is_file():
        return (json.loads(Path(path).read_text(encoding="utf-8-sig")).get(key) or "").strip()
    return ""


class AssetRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        nxt = super().redirect_request(req, fp, code, msg, headers, newurl)
        if nxt and parse.urlsplit(req.full_url).netloc != parse.urlsplit(newurl).netloc:
            nxt.remove_header("Authorization")
        return nxt


def response(url, provider=None):
    headers = {"User-Agent": AGENT}
    if provider:
        token = credential(provider)
        if token:
            headers["Authorization"] = ("Token " if provider == "sketchfab" else "Bearer ") + token
    for attempt in range(4):                     # busy servers (Wikimedia answers 429) ask us to wait: wait and retry
        try:
            return request.build_opener(AssetRedirect).open(request.Request(url, headers=headers), timeout=60)
        except error.HTTPError as failure:
            if failure.code not in (429, 503) or attempt == 3:
                raise
            wait = failure.headers.get("Retry-After", "")
            time.sleep(min(30.0, float(wait)) if wait.replace(".", "", 1).isdigit() else 5.0 * (attempt + 1))


def api(url, query=None, provider=None):
    if query:
        url += "?" + parse.urlencode(query)
    with response(url, provider) as r:
        return json.load(r)


def download(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with response(url) as remote, target.open("xb") as out:
        shutil.copyfileobj(remote, out)


def account_status(provider):
    if not credential(provider):
        return "not configured"
    try:
        api(SF + "/me" if provider == "sketchfab" else KIT + "/api/v1/me/", provider=provider)
        return "accepted"
    except error.HTTPError as exc:
        return f"HTTP {exc.code}"


def sketch_item(item):
    pictures = (item.get("thumbnails") or {}).get("images", [])
    cover = min(pictures, key=lambda p: abs(p["width"]-480))["url"] if pictures else None
    return dict(id="sketchfab:" + item["uid"], name=item["name"], author=item["user"]["displayName"],
                license=(item.get("license") or {}).get("label"), url=item["viewerUrl"],
                faces=item.get("faceCount"), animations=item.get("animationCount"), thumbnail=cover)


def kit_item(item):
    return dict(id="blenderkit:" + item["assetBaseId"], name=item.get("displayName") or item["name"],
                author=(item.get("author") or {}).get("fullName"), license=item.get("license"),
                free=item.get("isFree"), available=item.get("canDownload"),
                thumbnail=item.get("thumbnailMiddleUrl") or item.get("thumbnailSmallUrl"),
                url=KIT + "/asset-gallery-detail/" + item["assetBaseId"] + "/")


def polyhaven_assets():
    cache = PROJECT / ".runtime" / "cache" / "polyhaven-assets.json"
    try:
        if time.time() - cache.stat().st_mtime < 86400:
            with cache.open(encoding="utf-8") as stream:
                return json.load(stream)
    except (FileNotFoundError, ValueError):
        pass
    data = api(PH + "/assets")
    cache.parent.mkdir(parents=True, exist_ok=True)
    staged = cache.with_name(uuid.uuid4().hex + ".tmp")
    try:
        staged.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        staged.replace(cache)
    finally:
        staged.unlink(missing_ok=True)
    return data


def search(provider, term, limit, kind=None):
    if provider == "sketchfab":
        data = api(SF + "/search", dict(type="models", q=term, downloadable="true", count=limit))
        return [sketch_item(item) for item in data["results"]]
    if provider == "blenderkit":
        query = term + (f" asset_type:{kind}" if kind else "")
        data = api(KIT + "/api/v1/search/", {"query": query, "page_size": limit}, "blenderkit")
        return [kit_item(item) for item in data["results"][:limit]]
    if provider == "polyhaven":
        data = polyhaven_assets()
        types = {"hdris": 0, "textures": 1, "models": 2}
        if kind and kind not in types:
            raise ValueError("Choose hdris, textures or models.")
        items = []
        for key, item in data.items():
            if kind and item.get("type") != types[kind]:
                continue
            words = " ".join([key, item["name"], item.get("category",""), *item.get("tags", []), *item.get("categories", [])]).lower()
            if all(w in words for w in term.lower().split()):
                items.append(dict(id="polyhaven:" + key, name=item["name"], license="CC0",
                                  author=", ".join(item.get("authors", {})), thumbnail=item.get("thumbnail_url"),
                                  url="https://polyhaven.com/a/" + key))
        return items[:limit]
    if provider == "ambientcg":
        data = api(CG, {"q": term, "limit": limit, "type": "Material"})
        return [dict(id="ambientcg:" + a["assetId"], name=a.get("displayName"), author="ambientCG",
                     license="CC0", url="https://ambientcg.com/view?id=" + a["assetId"]) for a in data.get("foundAssets", [])]
    if term.startswith("Category:"):
        category = term.removeprefix("Category:").strip()
        term = "incategory:" + json.dumps(category, ensure_ascii=False)
    data = api("https://commons.wikimedia.org/w/api.php", dict(action="query", generator="search",
               gsrsearch=term + " filetype:bitmap", gsrnamespace=6, gsrlimit=limit, prop="imageinfo|categories",
               clshow="!hidden", cllimit="max", iiprop="url|size|extmetadata", iiextmetadatafilter="Artist|LicenseShortName",
               iiurlwidth=330, format="json", formatversion=2))
    if "error" in data:
        raise ValueError(data["error"]["info"])
    items = []
    for page in sorted(data.get("query", {}).get("pages", []), key=lambda p: p["index"]):
        if not page.get("imageinfo"):
            continue
        info = page["imageinfo"][0]
        meta = info.get("extmetadata", {})
        items.append(dict(number=len(items) + 1, id="commons:" + page["title"], name=page["title"], url=info["descriptionurl"],
                          categories=[c["title"] for c in page.get("categories", [])], width=info["width"], height=info["height"],
                          image=info["url"], thumbnail=info.get("thumburl"), author=re.sub("<[^>]+>", "", meta.get("Artist", {}).get("value", "")),
                          license=meta.get("LicenseShortName", {}).get("value")))
    return items


def photo_sheet(matches):
    """Render a numbered JPEG from the Commons thumbnails, preserving the full frame."""
    try:
        from PIL import Image, ImageDraw, ImageOps
    except ImportError as exc:
        raise ValueError("Reference preview sheets require Pillow (python -m pip install Pillow).") from exc

    def preview(item):
        if item.get("thumbnail"):
            try:
                req = request.Request(item["thumbnail"], headers={"User-Agent": AGENT})
                with request.urlopen(req, timeout=8) as remote:
                    with Image.open(io.BytesIO(remote.read(2 * 1024 * 1024))) as original:
                        tile = ImageOps.exif_transpose(original).convert("RGB")
                        tile.thumbnail((236, 166), Image.Resampling.LANCZOS)
                        return tile
            except (OSError, HTTPException, ValueError, Image.DecompressionBombError):
                pass
        return None

    columns = min(4, len(matches))
    rows = (len(matches) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * 244, rows * 192), "#f2f2f2")
    draw = ImageDraw.Draw(sheet)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for i, tile in enumerate(pool.map(preview, matches)):
            x, y = (i % columns) * 244, (i // columns) * 192
            if tile is None:
                draw.text((x + 8, y + 76), "Preview unavailable", fill="black")
            else:
                sheet.paste(tile, (x + (244 - tile.width) // 2, y + (170 - tile.height) // 2))
            draw.text((x + 8, y + 171), str(matches[i]["number"]), fill="black")
    out = io.BytesIO()
    sheet.save(out, format="JPEG", quality=88)
    return out.getvalue()


def catalog(term, providers, limit=4, kind=None):
    """Query independent catalogs together; keep a provider error separate from real zero matches."""
    def one(provider):
        try:
            return {"matches": search(provider, term, limit, kind)}
        except (OSError, ValueError, KeyError) as exc:
            # HTTPError may include a private signed query string. Return only its status.
            problem = f"HTTP {exc.code}" if isinstance(exc, error.HTTPError) else type(exc).__name__
            return {"error": problem}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(providers)) as pool:
        return dict(zip(providers, pool.map(one, providers)))


def unpack(archive, root):
    with zipfile.ZipFile(archive) as zipped:
        for name in zipped.namelist():
            if not (root / name).resolve().is_relative_to(root):
                raise ValueError("Unsafe path inside the downloaded asset.")
        zipped.extractall(root)
    archive.unlink()


def fetch_into(provider, ident, root, resolution):
    if provider == "sketchfab":
        item = sketch_item(api(SF + "/models/" + ident))
        formats = api(SF + "/models/" + ident + "/download", provider=provider)
        if formats.get("glb"):
            download(formats["glb"]["url"], root / "model.glb")
        elif formats.get("gltf"):
            download(formats["gltf"]["url"], root / "model.zip")
            unpack(root / "model.zip", root)
        else:
            raise ValueError("No downloadable glTF or GLB is offered for this asset.")
    elif provider == "blenderkit":
        matches = api(KIT + "/api/v1/search/", {"query": "asset_base_id:" + ident}, provider)["results"]
        asset = next((a for a in matches if a["assetBaseId"] == ident), None)
        if not asset or not asset.get("canDownload"):
            raise ValueError("This asset is unavailable to the connected account.")
        item = kit_item(asset)
        file = next(f for f in asset["files"] if f["fileType"] == "blend")
        url = file["downloadUrl"]
        if parse.urlsplit(url).hostname not in {"www.blendkit.com", "www.blenderkit.com", "api.blenderkit.com"}:
            raise ValueError("Unexpected BlenderKit download host.")
        ticket = api(url, {"scene_uuid": str(uuid.uuid4())}, provider)
        download(ticket["filePath"], root / "asset.blend")
    elif provider == "polyhaven":
        info, files = api(PH + "/info/" + ident), api(PH + "/files/" + ident)
        item = dict(id="polyhaven:" + ident, name=info["name"], license="CC0",
                    author=", ".join(info.get("authors", {})), url="https://polyhaven.com/a/" + ident)
        if "blend" in files:
            entry = files["blend"][resolution]["blend"]
            download(entry["url"], root / Path(parse.urlsplit(entry["url"]).path).name)
            for rel, extra in entry.get("include", {}).items():
                target = (root / rel).resolve()
                if not target.is_relative_to(root):
                    raise ValueError("Unexpected Poly Haven asset path.")
                download(extra["url"], target)
        elif "hdri" in files:
            entry = files["hdri"][resolution]["hdr"]
            download(entry["url"], root / Path(parse.urlsplit(entry["url"]).path).name)
        else:
            for kind, options in files.items():
                if kind not in {"gltf", "mtlx"} and resolution in options:
                    options = options[resolution]
                    entry = options.get("png") or options.get("jpg") or next(iter(options.values()))
                    download(entry["url"], root / Path(parse.urlsplit(entry["url"]).path).name)
    elif provider == "ambientcg":
        a = api(CG, {"id": ident, "include": "downloadData"})["foundAssets"][0]
        files = a["downloadFolders"]["default"]["downloadFiletypeCategories"]["zip"]["downloads"]
        entry = next((d for d in files if d["attribute"].lower().startswith(resolution.lower())), None)
        if not entry:
            raise ValueError(f"No {resolution} material is available.")
        download(entry["downloadLink"], root / "material.zip")
        unpack(root / "material.zip", root)
        item = dict(id="ambientcg:" + ident, name=a.get("displayName"), license="CC0",
                    author="ambientCG", url="https://ambientcg.com/view?id=" + ident)
    else:
        data = api("https://commons.wikimedia.org/w/api.php", dict(action="query", titles=ident,
                   prop="imageinfo", iiprop="url|extmetadata", format="json", formatversion=2))
        info = data["query"]["pages"][0]["imageinfo"][0]
        meta = info.get("extmetadata", {})
        item = dict(id="commons:" + ident, name=ident, url=info["descriptionurl"],
                    author=re.sub("<[^>]+>", "", meta.get("Artist", {}).get("value", "")),
                    license=meta.get("LicenseShortName", {}).get("value"))
        download(info["url"], root / Path(parse.urlsplit(info["url"]).path).name)
    return item


TRIPO = "https://openapi.tripo3d.ai/v3"
MESH_PROVIDERS = ("tripo", "local")


def tripo_request(path, payload=None, files=None):
    """A JSON request against Tripo's v3 API, with multipart upload when files are given."""
    token = credential("tripo")
    if not token:
        raise ValueError("Set TRIPO_API_KEY, or tripo_api_key in the credential file, for image-to-model.")
    headers = {"Authorization": "Bearer " + token, "User-Agent": AGENT}
    if files:
        boundary = "----BlenderStudio" + uuid.uuid4().hex
        body = bytearray()
        for field, (name, data, mime) in files.items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{name}\"\r\n"
                     f"Content-Type: {mime}\r\n\r\n").encode()
            body += data + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        req = request.Request(TRIPO + path, data=bytes(body), headers=headers, method="POST")
    elif payload is not None:
        headers["Content-Type"] = "application/json"
        req = request.Request(TRIPO + path, data=json.dumps(payload).encode(), headers=headers, method="POST")
    else:
        req = request.Request(TRIPO + path, headers=headers)
    with request.urlopen(req, timeout=120) as r:
        data = json.load(r)
    if data.get("code", 0) != 0:
        raise ValueError(f"Tripo refused the request: {data.get('message') or data}")
    return data["data"]


def mesh_from_image(image, destination, provider="tripo", texture=True, pbr=True, face_limit=None,
                    model="v3.1-20260211", dry=False, poll=3.0, limit=900):
    """Generate a textured GLB from one photograph or concept image and save it in a new directory."""
    import time

    image = Path(image).expanduser().resolve(strict=True)
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"Choose a new model directory: {destination}")
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(image.suffix.lower())
    if not mime or image.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Use a PNG, JPEG or WebP image under 20 MB.")
    if provider == "local":
        raise ValueError("No local image-to-model backend is installed on this workstation; use --provider tripo.")
    if provider != "tripo":
        raise ValueError(f"Unknown provider: {provider}")
    task = {"input": "<file_token>", "model": model, "texture": texture, "pbr": pbr}
    if face_limit:
        task["face_limit"] = int(face_limit)
    plan = {"upload": {"method": "POST", "url": TRIPO + "/files", "field": "file", "file": str(image)},
            "create": {"method": "POST", "url": TRIPO + "/generation/image-to-model", "body": task},
            "poll": {"method": "GET", "url": TRIPO + "/tasks/<task_id>", "every_seconds": poll},
            "download": "data.output.model_url → " + str(destination / "model.glb")}
    if dry:
        return {"provider": provider, "dry": True, "plan": plan, "credentials": "configured" if credential("tripo") else "missing"}
    uploaded = tripo_request("/files", files={"file": (image.name, image.read_bytes(), mime)})
    task["input"] = uploaded["file_token"]
    created = tripo_request("/generation/image-to-model", payload=task)
    task_id = created["task_id"]
    started = time.time()
    while True:
        state = tripo_request(f"/tasks/{task_id}")
        status = state.get("status")
        if status == "success":
            break
        if status in {"failed", "cancelled", "banned", "expired"}:
            raise ValueError(f"Tripo task {task_id} ended as {status}: {state.get('error_message') or state.get('error_code')}")
        if time.time() - started > limit:
            raise ValueError(f"Tripo task {task_id} is still {status} after {limit} seconds.")
        time.sleep(poll)
    output = state.get("output") or {}
    url = output.get("pbr_model") or output.get("model") or output.get("model_url")
    if not url:
        raise ValueError(f"Tripo returned no model URL: {output}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as scratch:
        root = Path(scratch) / "model"
        root.mkdir()
        download(url, root / "model.glb")
        preview = output.get("rendered_image") or output.get("rendered_image_url")
        if preview:
            try:
                download(preview, root / "preview.webp")
            except (OSError, ValueError):
                pass
        (root / "source.json").write_text(json.dumps({"provider": "tripo", "task_id": task_id, "model": model,
                                                        "image": str(image), "credits": state.get("credits_consumed")},
                                                       indent=2), encoding="utf-8")
        root.rename(destination)
    return {"provider": provider, "task_id": task_id, "file": str(destination / "model.glb"),
            "credits": state.get("credits_consumed"),
            "next": [f"assets.import_model({str(destination / 'model.glb')!r}, target_size=…, credit=dict(name=…, author='Tripo', license='own', url=''))",
                     "sculpt.voxel_remesh(obj, size) or retopo.setup_cage(obj)", "assets.unwrap(low)", "assets.bake(high, low)"]}


def fetch_library(asset, destination, resolution="2k", sky="8k"):
    """A Poly Haven asset laid out the way the library keeps it (what `outdoor.library` and the scene helpers read):
    a model as glTF with its textures, a sky as one .hdr, a texture set as Diffuse, nor_gl, Rough and Displacement
    images. Writes source.md beside it, one line to copy into the work's sources."""
    provider, _, ident = asset.partition(":")
    if provider != "polyhaven" or not re.fullmatch(r"[A-Za-z0-9_.-]+", ident):
        raise ValueError("The library downloads Poly Haven assets by id, e.g. polyhaven:leafy_grass.")
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"Choose a new asset directory: {destination}")
    info, files = api(PH + "/info/" + ident), api(PH + "/files/" + ident)
    destination.parent.mkdir(parents=True, exist_ok=True)

    def pick(options, wanted):
        sizes = sorted(options, key=lambda r: int(re.sub(r"\D", "", r) or 0))
        return wanted if wanted in options else next((r for r in reversed(sizes) if int(re.sub(r"\D", "", r) or 0) <= int(re.sub(r"\D", "", wanted))), sizes[0])

    with tempfile.TemporaryDirectory(dir=destination.parent) as scratch:
        root = Path(scratch) / "asset"
        root.mkdir()
        if info.get("type") == 0:
            entry = files["hdri"][pick(files["hdri"], sky)]["hdr"]
            download(entry["url"], root / Path(parse.urlsplit(entry["url"]).path).name)
        elif info.get("type") == 2:
            entry = files["gltf"][pick(files["gltf"], resolution)]["gltf"]
            download(entry["url"], root / Path(parse.urlsplit(entry["url"]).path).name)
            for rel, extra in entry.get("include", {}).items():
                target = (root / rel).resolve()
                if not target.is_relative_to(root):
                    raise ValueError("Unexpected Poly Haven asset path.")
                download(extra["url"], target)
        else:
            for key, fmt in (("Diffuse", "jpg"), ("nor_gl", "jpg"), ("Rough", "jpg"), ("Displacement", "png")):
                if key in files:
                    options = files[key][pick(files[key], resolution)]
                    entry = options.get(fmt) or next(iter(options.values()))
                    download(entry["url"], root / f"{key}.{entry['url'].rsplit('.', 1)[1]}")
        line = f"- {info['name']}, {', '.join(info.get('authors', {}))}, CC0. https://polyhaven.com/a/{ident}"
        (root / "source.md").write_text(line + chr(10), encoding="utf-8")
        root.rename(destination)
    return {"id": asset, "folder": str(destination), "source_line": line}


def fetch(asset, destination, resolution):
    provider, sep, ident = asset.partition(":")
    if not sep or provider not in PROVIDERS or not ident:
        raise ValueError("Use a provider-qualified ID returned by search.")
    if provider != "commons" and not re.fullmatch(r"[A-Za-z0-9_.-]+", ident):
        raise ValueError("Unexpected asset identifier.")
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"Choose a new asset directory: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as scratch:
        root = Path(scratch) / "asset"
        root.mkdir()
        item = fetch_into(provider, ident, root, resolution)
        item["files"] = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]
        if not item["files"]:
            raise ValueError("The provider supplied no files at the requested resolution.")
        root.rename(destination)
    fields = [str(item.get(key) or "unspecified").replace("\n", " ").strip() for key in ("name", "author", "license")]
    item["source_line"] = "- " + ", ".join(fields) + ". " + item["url"]
    folder = destination.as_posix()
    if provider == "ambientcg" or (provider == "polyhaven" and any(re.search(r"_(?:diff|rough|nor_gl)[_.]", f, re.I) for f in item["files"])):
        item["next_step"] = f"assets.pbr_material({folder!r})"
    else:
        entry = next((f for f in item["files"] if Path(f).suffix.lower() in {".blend", ".glb", ".gltf", ".hdr", ".exr"}), None)
        if entry:
            path = (destination / entry).as_posix()
            method = {".blend":"assets.append_from", ".hdr":"lighting.environment", ".exr":"lighting.environment"}.get(Path(entry).suffix.lower(), "assets.import_model")
            item["next_step"] = f"{method}({path!r})"
    return item


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    cli = argparse.ArgumentParser(description=__doc__)
    cmds = cli.add_subparsers(dest="op", required=True)
    cmds.add_parser("accounts", help="Show which accounts accept their configured token.")
    s = cmds.add_parser("search")
    s.add_argument("provider", choices=(*PROVIDERS, "all"))
    s.add_argument("query")
    s.add_argument("--kind")
    s.add_argument("--limit", type=int, default=8)
    s.add_argument("--sheet", type=Path, help="Save a numbered JPEG of the Commons results.")
    marks = cmds.add_parser("landmarks", help="Face landmarks of a photograph in pixels.")
    marks.add_argument("image")
    marks.add_argument("output", nargs="?")
    f = cmds.add_parser("fetch")
    f.add_argument("asset")
    f.add_argument("directory")
    f.add_argument("--resolution", default="2k")
    gen = cmds.add_parser("mesh-from-image", help="Generate a textured GLB from one image through a hosted model.")
    gen.add_argument("image")
    gen.add_argument("directory")
    gen.add_argument("--provider", choices=MESH_PROVIDERS, default="tripo")
    gen.add_argument("--no-texture", action="store_true")
    gen.add_argument("--no-pbr", action="store_true")
    gen.add_argument("--face-limit", type=int)
    gen.add_argument("--model", default="v3.1-20260211")
    gen.add_argument("--dry", action="store_true", help="Show the requests without sending them.")
    opt = cli.parse_args()
    try:
        if opt.op == "accounts":
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                names = ("sketchfab", "blenderkit")
                result = dict(zip(names, pool.map(account_status, names)))
        elif opt.op == "mesh-from-image":
            result = mesh_from_image(opt.image, opt.directory, opt.provider, texture=not opt.no_texture,
                                     pbr=not opt.no_pbr, face_limit=opt.face_limit, model=opt.model, dry=opt.dry)
        elif opt.op == "landmarks":
            import photofit
            result = photofit.detect(opt.image)
            if result is None:
                raise ValueError("No face found in the image.")
            if opt.output:
                out = Path(opt.output)
                if out.exists():
                    raise FileExistsError(f"Choose a new landmark file: {out}")
                out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
                result = {"file": str(out.resolve()), "points": len(result["points"])}
        elif opt.op == "search":
            limit = min(50, max(1, opt.limit))
            if opt.sheet and (opt.provider != "commons" or opt.sheet.suffix.lower() not in {".jpg", ".jpeg"}):
                cli.error("--sheet expects a .jpg output path and the commons provider.")
            if opt.sheet and opt.sheet.exists():
                raise FileExistsError(f"Choose a new preview sheet path: {opt.sheet}")
            result = catalog(opt.query, PROVIDERS, limit) if opt.provider == "all" else search(opt.provider, opt.query, limit, opt.kind)
            if opt.sheet and result:
                sheet = photo_sheet(result)
                opt.sheet.parent.mkdir(parents=True, exist_ok=True)
                with opt.sheet.open("xb") as output:
                    output.write(sheet)
        else:
            result = fetch(opt.asset, opt.directory, opt.resolution)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (ValueError, OSError, LookupError, StopIteration) as exc:
        sys.exit(str(exc))
