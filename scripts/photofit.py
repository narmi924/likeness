"""Face landmarks in photographs and renders, and the camera that took a photograph.

Runs in the workspace Python (not inside Blender): mediapipe finds the landmarks, OpenCV solves the camera.
"""

import json
import math
import os
from pathlib import Path
import sys
import urllib.request

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import home  # noqa: E402
PROJECT = home.home()
MODEL = PROJECT / ".runtime" / "models" / "face_landmarker.task"
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"

# A stable subset of the 478 mediapipe points, named so results read like a measurement sheet.
NAMED = {
    "forehead": 10, "glabella": 9, "nasion": 168, "nose bridge": 6, "nose tip": 1, "subnasale": 2,
    "nose right ala": 129, "nose left ala": 358, "chin": 152, "chin front": 199,
    "right eye outer": 33, "right eye inner": 133, "right eye top": 159, "right eye bottom": 145,
    "left eye inner": 362, "left eye outer": 263, "left eye top": 386, "left eye bottom": 374,
    "right brow outer": 70, "right brow mid": 105, "right brow inner": 107,
    "left brow inner": 336, "left brow mid": 334, "left brow outer": 300,
    "mouth right": 61, "mouth left": 291, "upper lip top": 0, "upper lip bottom": 13,
    "lower lip top": 14, "lower lip bottom": 17,
    "right cheek": 234, "left cheek": 454, "right jaw": 172, "left jaw": 397,
    "right jaw low": 150, "left jaw low": 379, "right chin side": 176, "left chin side": 400,
    "right temple": 127, "left temple": 356,
}
# Points that stay put under expression: used for the camera solve.
RIGID = ["nasion", "nose bridge", "nose tip", "subnasale", "nose right ala", "nose left ala",
         "right eye outer", "right eye inner", "left eye inner", "left eye outer",
         "right temple", "left temple", "right cheek", "left cheek", "forehead", "glabella",
         "right jaw", "left jaw", "chin"]


def ensure_model():
    if not MODEL.is_file():
        MODEL.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, MODEL)
    return str(MODEL)


def detect(image_path, all_points=False):
    """Landmarks in pixels (x right, y down) for the largest face; None when no face is found."""
    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    image = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"Cannot read image: {image_path}")
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    elif image.shape[2] == 4:
        alpha = image[:, :, 3:4] / 255.0
        image = (image[:, :, :3] * alpha + 60 * (1 - alpha)).astype(np.uint8)
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    else:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    height, width = image.shape[:2]
    options = vision.FaceLandmarkerOptions(base_options=BaseOptions(model_asset_path=ensure_model()), num_faces=1)
    with vision.FaceLandmarker.create_from_options(options) as finder:
        result = finder.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(image)))
    if not result.face_landmarks:
        return None
    points = result.face_landmarks[0]
    pixels = {name: [points[i].x * width, points[i].y * height] for name, i in NAMED.items()}
    out = {"image": str(Path(image_path).resolve()), "width": width, "height": height, "points": pixels}
    if all_points:
        out["all"] = [[p.x * width, p.y * height] for p in points]
    return out


def head_turn(points):
    """How far the head is turned, from -1 (subject's left side hidden) to 1; 0 is a frontal view."""
    eyes = [points.get("right eye outer"), points.get("left eye outer")]
    nose = points.get("nose tip")
    if not all(eyes) and nose:
        return 0.0
    span = abs(eyes[1][0] - eyes[0][0]) or 1.0
    middle = (eyes[0][0] + eyes[1][0]) / 2
    return max(-1.0, min(1.0, (nose[0] - middle) / span))


def visible_points(points, threshold=0.45):
    """Drop the landmarks on the far side of a turned head; the detector guesses those."""
    turn = head_turn(points)
    if abs(turn) < threshold:
        return dict(points)
    # Nose toward image right means the subject's right side faces the camera.
    hidden = "left" if turn > 0 else "right"
    return {n: p for n, p in points.items() if not n.startswith(hidden)}


def _blender_pose(rotation, translation):
    # OpenCV looks down +Z with +Y down. Blender looks down -Z with +Y up.
    extrinsic = np.eye(4)
    extrinsic[:3, :3] = rotation
    extrinsic[:3, 3] = translation
    return (np.linalg.inv(extrinsic) @ np.diag([1.0, -1.0, -1.0, 1.0])).tolist()


def solve_camera(points3d, points2d, width, height, fov_range=(8, 95), preferred_fov=22.0):
    """Camera position, rotation and focal length (pixels) from matching 3D points and photo pixels.

    Returns a dict with a Blender-style camera-to-world matrix (camera looks down -Z, +Y up),
    the focal length in pixels and the reprojection RMS in pixels."""
    import cv2

    object_points = np.asarray(points3d, dtype=np.float64)
    image_points = np.asarray(points2d, dtype=np.float64)
    if len(object_points) < 6:
        raise ValueError("At least six matching landmarks are needed to solve the camera.")
    cx, cy = width / 2, height / 2
    candidates = []
    for fov in np.linspace(fov_range[0], fov_range[1], 88):
        f = width / (2 * math.tan(math.radians(fov) / 2))
        camera = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1]], dtype=np.float64)
        ok, rvec, tvec = cv2.solvePnP(object_points, image_points, camera, None, flags=cv2.SOLVEPNP_EPNP)
        if not ok:
            continue
        ok, rvec, tvec = cv2.solvePnP(object_points, image_points, camera, None, rvec, tvec, True, cv2.SOLVEPNP_ITERATIVE)
        projected, _ = cv2.projectPoints(object_points, rvec, tvec, camera, None)
        error = np.sqrt(((projected[:, 0, :] - image_points) ** 2).sum(1).mean())
        candidates.append({"error": float(error), "f": float(f), "fov": float(fov), "rvec": rvec, "tvec": tvec})
    if not candidates:
        raise ValueError("The camera solve failed for every focal length.")
    # Face landmarks barely constrain focal length; among near-equal fits prefer a portrait lens.
    floor = min(c["error"] for c in candidates)
    close = [c for c in candidates if c["error"] <= floor * 1.03 + 0.2]
    best = min(close, key=lambda c: abs(c["fov"] - preferred_fov))
    rotation, _ = cv2.Rodrigues(best["rvec"])
    return {"matrix": _blender_pose(rotation, best["tvec"][:, 0]), "focal_px": best["f"], "fov": best["fov"], "error_px": best["error"],
            "sensor_width": 36.0, "lens_mm": best["f"] * 36.0 / width, "width": width, "height": height}


def feature_pairs(reference, photo):
    """Match distinctive details in two photographs, in original top-left pixel coordinates."""
    import cv2
    from PIL import Image

    sift = cv2.SIFT_create(nfeatures=7000)

    def features(path):
        with Image.open(path) as im:
            gray = im.convert("L")
            gray.thumbnail((1600, 1600))
            points, desc = sift.detectAndCompute(np.asarray(gray), None)
            if desc is None or len(points) < 8:
                raise ValueError(f"Too few distinctive details in {path}.")
            xy = np.array([p.pt for p in points]) * np.array(im.size) / np.array(gray.size)
            return xy, desc

    a, da = features(reference)
    b, db = features(photo)
    matcher = cv2.BFMatcher()

    def distinct(src, dst):
        return {m.queryIdx: m.trainIdx for m, n in matcher.knnMatch(src, dst, k=2)
                if m.distance < 0.78 * n.distance}
    reverse = distinct(db, da)
    pairs = [(i, j) for i, j in distinct(da, db).items() if reverse.get(j) == i]
    return [(a[i].tolist(), b[j].tolist()) for i, j in pairs]


def scene_camera(world, pixels, photo, fov=None, automatic=False):
    """Robust centered pinhole fit. A flat elevation needs an assumed or known focal length."""
    import cv2
    from PIL import Image

    xyz, xy = np.asarray(world, np.float64), np.asarray(pixels, np.float64)
    minimum = 10 if automatic else 6
    if len(xyz) < minimum or xyz.shape != (len(xy), 3) or xy.shape != (len(xyz), 2):
        raise ValueError(f"Need at least {minimum} corresponding surface points spread across the subject.")
    if not (np.isfinite(xyz).all() and np.isfinite(xy).all()):
        raise ValueError("Photo and scene points must be finite.")
    center = xyz.mean(axis=0)
    xyz = xyz - center
    spans = np.linalg.svd(xyz, compute_uv=False)
    if spans[1] < max(1e-8, spans[0] * 0.02):
        raise ValueError("The anchors lie along a line. Use corners on different edges.")
    with Image.open(photo) as im:
        w, h = im.size
        equiv = im.getexif().get_ifd(34665).get(41989)
    if fov is not None and not 1 < fov < 175:
        raise ValueError("Horizontal field of view must be between 1 and 175 degrees.")
    if fov is not None:
        source = "specified horizontal field of view"
    elif equiv:
        fov = math.degrees(2 * math.atan(w * math.hypot(36, 24) / (2 * float(equiv) * math.hypot(w, h))))
        source = "35 mm equivalent in EXIF"
    elif spans[2] < spans[0]*0.025:
        fov = 50.0
        source = "assumed 50 degrees for shallow geometry"
    else:
        source = "estimated from scene points"
    angles = [fov] if fov is not None else np.arange(16, 105, 2)
    # Manually identified corners on a blockout are less exact than matched image features.
    limit = max(3.0, math.hypot(w, h) * (0.004 if automatic else 0.012))
    required = max(minimum, math.ceil(len(xy) * (0.25 if automatic else 0.65)))
    best = None
    for angle in angles:
        focal = w / (2 * math.tan(math.radians(angle)/2))
        k = np.array([[focal,0,w/2],[0,focal,h/2],[0,0,1]], dtype=np.float64)
        ok, r, t, ids = cv2.solvePnPRansac(xyz, xy, k, None, iterationsCount=350,
            reprojectionError=limit, confidence=.995, flags=cv2.SOLVEPNP_EPNP)
        if not ok or len(ids) < required:
            continue
        idx = ids.ravel()
        r, t = cv2.solvePnPRefineLM(xyz[idx], xy[idx], k, None, r, t)
        rotation = cv2.Rodrigues(r)[0]
        if np.min(xyz[idx] @ rotation[2] + t[2,0]) <= 0:
            continue
        projected = cv2.projectPoints(xyz, r, t, k, None)[0][:,0]
        error = np.linalg.norm(projected-xy, axis=1)
        idx = np.flatnonzero(error <= limit)
        if len(idx) < required:
            continue
        spread = cv2.contourArea(cv2.convexHull(xy[idx].astype(np.float32)))
        if spread < w*h*0.005:
            continue
        rms = float(np.sqrt(np.mean(error[idx]**2)))
        score = (len(idx), -rms)
        if best is None or score > best[0]:
            best = score, dict(matrix=_blender_pose(rotation, t[:, 0] - rotation @ center),
                lens_mm=focal * 36 / w, width=w, height=h, fov=float(angle), focal_source=source,
                error_px=rms, inliers=idx.tolist(), total=len(xy), residuals=error.tolist(),
                clip_end=max(1000.0, 5 * float(np.max(xyz[idx] @ rotation[2] + t[2,0]))))
    if best is None:
        raise ValueError("No supported photo pose. Use a closer reference view or recheck the scene anchors.")
    if source == "estimated from scene points":
        consensus = xyz[best[1]["inliers"]]
        axes = np.linalg.svd(consensus-consensus.mean(axis=0), compute_uv=False)
        if axes[2] < 0.025*axes[0]:
            fixed = scene_camera(world, pixels, photo, fov=50.0, automatic=automatic)
            fixed["focal_source"] = "assumed 50 degrees for shallow inliers"
            return fixed
    return best[1]


def main():
    import argparse

    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest="op", required=True)
    find = sub.add_parser("landmarks", help="Detect face landmarks in a photograph or render.")
    find.add_argument("image")
    find.add_argument("output", nargs="?")
    find.add_argument("--all", action="store_true", help="Include all 478 points.")
    args = cli.parse_args()
    if args.op == "landmarks":
        result = detect(args.image, args.all)
        if result is None:
            sys.exit("No face found in the image.")
        text = json.dumps(result, indent=2, ensure_ascii=False)
        if args.output:
            out = Path(args.output)
            if out.exists():
                sys.exit(f"Choose a new landmark file: {out}")
            out.write_text(text, encoding="utf-8")
            print(json.dumps({"file": str(out.resolve()), "points": len(result["points"]),
                              "width": result["width"], "height": result["height"]}, ensure_ascii=False))
        else:
            print(text)


if __name__ == "__main__":
    main()
