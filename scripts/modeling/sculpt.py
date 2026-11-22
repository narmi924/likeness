"""Blender sculpt tools adapted from Scenario Blender Skills."""
# Source: https://github.com/scenario-labs/skills/blob/754d1ceea270f1bf59651671b3471f385a5a80f9/skills/dcc/blender/scenario-blender-sculpting/scripts/bx_sculpt.py
# Copyright 2026 Scenario Inc. Distributed under the accompanying Scenario-MIT license.

import math
import bmesh
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree


FALLOFF = {
    "smooth": lambda t: 3 * (1 - t) ** 2 - 2 * (1 - t) ** 3,
    "sphere": lambda t: np.sqrt(np.clip(1 - t * t, 0, 1)),
    "root": lambda t: np.sqrt(1 - t),
    "sharp": lambda t: (1 - t) ** 2,
    "linear": lambda t: 1 - t,
    "constant": lambda t: np.ones_like(t),
    "pow4": lambda t: (1 - t) ** 4,
    "smoother": lambda t: 1 - (6 * t ** 5 - 15 * t ** 4 + 10 * t ** 3),
}


FRONT_RAMP = 0.25


class Sculptor:
    def __init__(self, obj, symmetry_x=True, falloff="smooth", front_faces_only=False):
        if obj.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        self.obj, self.me = obj, obj.data
        self.symmetry_x, self.falloff = symmetry_x, falloff
        self.front_faces_only = front_faces_only
        self._load()

    # ---------- data plumbing ----------
    def _load(self):
        me = self.me
        n = len(me.vertices)
        self.co = np.empty(n * 3, dtype=np.float64)
        me.vertices.foreach_get("co", self.co)
        self.co = self.co.reshape(n, 3)
        me.calc_loop_triangles()
        t = np.empty(len(me.loop_triangles) * 3, dtype=np.int64)
        me.loop_triangles.foreach_get("vertices", t)
        self.tris = t.reshape(-1, 3)
        e = np.empty(len(me.edges) * 2, dtype=np.int64)
        me.edges.foreach_get("vertices", e)
        self.edges = e.reshape(-1, 2)
        self.deg = np.bincount(self.edges.ravel(), minlength=n).astype(np.float64)
        self.deg[self.deg == 0] = 1
        attr = me.attributes.get(".sculpt_mask")
        self.mask = np.zeros(n)
        if attr is not None:
            attr.data.foreach_get("value", self.mask)
        self._normals()
        self._bvh = None

    def _normals(self):
        a, b, c = (self.co[self.tris[:, i]] for i in range(3))
        fn = np.cross(b - a, c - a)
        vn = np.zeros_like(self.co)
        for i in range(3):
            np.add.at(vn, self.tris[:, i], fn)
        ln = np.linalg.norm(vn, axis=1)
        ln[ln == 0] = 1
        self.vn = vn / ln[:, None]
        self._bvh = None

    def commit(self, remember_mask=True):
        """Write coordinates (and mask) back to the mesh."""
        self.me.vertices.foreach_set("co", self.co.astype(np.float32).ravel())
        if remember_mask:
            attr = self.me.attributes.get(".sculpt_mask") or \
                self.me.attributes.new(".sculpt_mask", "FLOAT", "POINT")
            attr.data.foreach_set("value", self.mask.astype(np.float32))
        self.me.update()

    def bvh(self):
        if self._bvh is None:
            self._bvh = BVHTree.FromPolygons(self.co.tolist(), self.tris.tolist())
        return self._bvh

    # ---------- surface queries (object-local coordinates) ----------
    # All coordinates in this class are object-local. Blockouts built with
    # union_remesh() have an identity transform, so local == world there.
    def to_local(self, p_world):
        return np.array(self.obj.matrix_world.inverted() @ Vector(p_world))

    def to_world(self, p_local):
        return np.array(self.obj.matrix_world @ Vector(p_local))

    def surface_point(self, origin, direction):
        """First hit of a ray on the surface, or None. Use to place dabs on landmarks."""
        loc, nor, idx, dist = self.bvh().ray_cast(Vector(origin), Vector(direction).normalized())
        return None if loc is None else np.array(loc)

    def nearest(self, p):
        loc, nor, idx, dist = self.bvh().find_nearest(Vector(p))
        return np.array(loc)

    def on_surface(self, p, view=None):
        """Put a point on the surface the way a stroke drawn in a viewport lands.
        view=None: nearest surface point (fine for points already close to the surface).
        view=(0, 1, 0) etc.: cast a ray along the view direction through p (front view
        looks along +Y). Use a view for anything placed from outside the head: from a
        point in front of the mouth, the NEAREST surface is often the nose tip."""
        if view is None:
            return self.nearest(p)
        v = np.asarray(view, dtype=np.float64)
        v = v / np.linalg.norm(v)
        far = float(np.linalg.norm(self.co.max(0) - self.co.min(0))) * 2
        hit = self.surface_point(np.asarray(p, dtype=np.float64) - v * far, v)
        return hit if hit is not None else self.nearest(p)

    def extremes(self):
        """Handy landmarks: bbox min/max and the most forward (-Y) midline point."""
        lo, hi = self.co.min(0), self.co.max(0)
        mid = np.abs(self.co[:, 0]) < (hi[0] - lo[0]) * 0.02
        front = self.co[mid][np.argmin(self.co[mid][:, 1])] if mid.any() else self.co[np.argmin(self.co[:, 1])]
        return {"min": lo, "max": hi, "center": (lo + hi) / 2, "front_midline": front}

    # ---------- core ----------
    def _weights(self, center, radius, falloff=None, front=None):
        d = np.linalg.norm(self.co - center, axis=1)
        inside = d < radius
        w = np.zeros(len(self.co))
        t = d[inside] / radius
        w[inside] = FALLOFF[falloff or self.falloff](t)
        if (self.front_faces_only if front is None else front) and inside.any():
            # Front Faces Only: skip vertices facing away from the surface under the
            # dab centre (the other side of a lip, ear or fin inside the radius)
            ref = self.vn[np.argmin(d)]
            w[inside] *= np.clip((self.vn[inside] @ ref) / FRONT_RAMP, 0, 1)
        return w * (1 - self.mask)

    def _blur(self, w, iterations):
        """Average a per-vertex array over the edge graph (Smooth Mask equivalent)."""
        for _ in range(iterations):
            s = np.zeros_like(w)
            np.add.at(s, self.edges[:, 0], w[self.edges[:, 1]])
            np.add.at(s, self.edges[:, 1], w[self.edges[:, 0]])
            w = 0.5 * w + 0.5 * s / self.deg
        return w

    def _area(self, w):
        s = w.sum()
        if s <= 0:
            return None, None
        p = (self.co * w[:, None]).sum(0) / s
        n = (self.vn * w[:, None]).sum(0)
        ln = np.linalg.norm(n)
        return p, (n / ln if ln > 0 else np.array([0, 0, 1.0]))

    def _passes(self, center):
        yield np.asarray(center, dtype=np.float64), np.array([1.0, 1, 1])
        if self.symmetry_x:
            yield np.asarray(center) * np.array([-1.0, 1, 1]), np.array([-1.0, 1, 1])

    def dab(self, brush, center, radius, strength=0.5, height=None, falloff=None, front=None):
        """Apply one brush dab (plus its X mirror). Returns number of vertices moved."""
        center = np.asarray(center, dtype=np.float64)
        h = height if height is not None else strength * radius * 0.25
        delta = np.zeros_like(self.co)
        wmax = np.zeros(len(self.co))
        for c, flip in self._passes(center):
            w = self._weights(c, radius, falloff, front)
            if not w.any():
                continue
            d = self._brush_delta(brush, c, radius, strength, h, w)
            take = w > wmax          # where passes overlap, the stronger dab wins
            delta[take] = d[take]
            wmax = np.maximum(wmax, w)
        self.co += delta
        return int((np.abs(delta).sum(1) > 0).sum())

    def _brush_delta(self, brush, c, radius, strength, h, w):
        co, vn = self.co, self.vn
        p, n = self._area(w)
        if p is None:
            return np.zeros_like(co)
        sgn = 1.0 if strength >= 0 else -1.0
        if brush == "draw":
            return n * (h * w)[:, None]
        if brush == "inflate":
            return vn * (h * w)[:, None]
        if brush == "layer":
            dist = (co - p) @ n
            return n * (np.clip(h - dist, 0, None) * w * abs(strength))[:, None] * sgn
        if brush in ("clay", "flatten", "fill", "scrape"):
            offset = {"clay": abs(h) * sgn, "flatten": 0.0, "fill": 0.0, "scrape": 0.0}[brush]
            plane = p + n * offset
            dist = (co - plane) @ n                       # >0 above the plane
            if brush == "clay":
                move = np.where(dist * sgn < 0, -dist, 0.0)  # only toward the plane
            elif brush == "fill":
                move = np.where(dist < 0, -dist, 0.0)
            elif brush == "scrape":
                move = np.where(dist > 0, -dist, 0.0)
            else:
                move = -dist
            return n * (move * w * min(abs(strength), 1.0))[:, None]
        if brush in ("pinch", "crease"):
            to_c = c - co
            to_c -= np.outer(to_c @ n, n)                 # keep it on the tangent plane
            d = to_c * (w * abs(strength) * 0.5)[:, None]
            if brush == "crease":
                d += n * (-abs(h) * sgn * w)[:, None]
            return d
        raise ValueError(f"unknown brush {brush}")

    def stroke(self, brush, points, radius, strength=0.5, spacing=0.3, height=None,
               falloff=None, project=True, front=None):
        """Dabs every spacing*radius along a polyline. Points snap to the surface:
        project=True nearest point, project=(x, y, z) ray along that view direction."""
        pts = [np.asarray(p, dtype=np.float64) for p in points]
        path = [pts[0]]
        for a, b in zip(pts, pts[1:]):
            L = np.linalg.norm(b - a)
            k = max(1, int(L / (spacing * radius)))
            path += [a + (b - a) * (i / k) for i in range(1, k + 1)]
        moved = 0
        for i, q in enumerate(path):
            if project is not False and project is not None:
                q = self.on_surface(q, None if project is True else project)
            moved += self.dab(brush, q, radius, strength, height, falloff, front)
            if i % 4 == 3:
                self._normals()
        self._normals()
        return moved

    def grab(self, center, radius, delta, falloff=None, front=None):
        """Move a region by `delta` (mirrored in X when symmetry is on)."""
        delta = np.asarray(delta, dtype=np.float64)
        total = np.zeros_like(self.co)
        wmax = np.zeros(len(self.co))
        for c, flip in self._passes(np.asarray(center)):
            w = self._weights(c, radius, falloff, front)
            take = w > wmax
            total[take] = np.outer(w, delta * flip)[take]
            wmax = np.maximum(wmax, w)
        self.co += total
        self._normals()

    def smooth(self, center=None, radius=None, strength=0.5, iterations=1, falloff=None,
               preserve_volume=False):
        """Laplacian relax. center=None smooths the whole mesh (respecting the mask).
        preserve_volume=True uses Taubin lambda/mu passes so forms do not shrink
        (use it for global relax after a voxel remesh; plain Laplacian for polishing)."""
        if center is None:
            w = (1 - self.mask) * strength
        else:
            w = np.zeros(len(self.co))
            for c, _f in self._passes(np.asarray(center)):
                w = np.maximum(w, self._weights(c, radius, falloff))
            w *= strength
        w = np.clip(w, 0, 1)[:, None]
        factors = [1.0, -1.06] if preserve_volume else [1.0]
        for _ in range(iterations):
            for f in factors:
                s = np.zeros_like(self.co)
                np.add.at(s, self.edges[:, 0], self.co[self.edges[:, 1]])
                np.add.at(s, self.edges[:, 1], self.co[self.edges[:, 0]])
                avg = s / self.deg[:, None]
                self.co += (avg - self.co) * w * f
        self._normals()

    def paint_mask(self, center, radius, value=1.0, falloff=None, mode="max"):
        for c, _f in self._passes(np.asarray(center)):
            w = FALLOFF[falloff or "smooth"](np.clip(np.linalg.norm(self.co - c, axis=1) / radius, 0, 1))
            w[np.linalg.norm(self.co - c, axis=1) >= radius] = 0
            self.mask = np.maximum(self.mask, w * value) if mode == "max" else np.clip(self.mask - w * value, 0, 1)

    def clear_mask(self):
        self.mask[:] = 0

    def mask_by(self, predicate):
        """Mask with a numpy predicate on coordinates, e.g. lambda co: co[:, 2] < 1.4."""
        self.mask = np.where(predicate(self.co), 1.0, self.mask)

    # ---------- mask pie substitutes (sculpt.mask_filter crashes headless) ----------
    def blur_mask(self, iterations=3):
        """Smooth Mask (Kaspar/Reinhardt use 3 iterations before moving a feature)."""
        self.mask = np.clip(self._blur(self.mask, iterations), 0, 1)

    def grow_mask(self, steps=1):
        """Grow Mask: dilate the mask over edges `steps` rings."""
        for _ in range(steps):
            m = self.mask.copy()
            np.maximum.at(m, self.edges[:, 0], self.mask[self.edges[:, 1]])
            np.maximum.at(m, self.edges[:, 1], self.mask[self.edges[:, 0]])
            self.mask = m

    def invert_mask(self):
        self.mask = 1.0 - self.mask

    # ---------- line tools ----------
    def _resample_on_surface(self, points, step, project=True):
        pts = [np.asarray(p, dtype=np.float64) for p in points]
        path = [pts[0]]
        for a, b in zip(pts, pts[1:]):
            k = max(1, int(np.linalg.norm(b - a) / step))
            path += [a + (b - a) * (i / k) for i in range(1, k + 1)]
        if project is not False and project is not None:
            view = None if project is True else project
            path = [self.on_surface(q, view) for q in path]
        return np.array(path)

    def _line_field(self, P, radius, falloff, front):
        """Per vertex: weight from the distance to polyline P, nearest point on P, and
        the surface normal there (area normal sampled along the line)."""
        co = self.co
        ns = []
        for s in P:                                   # area normal under each sample
            near = np.linalg.norm(co - s, axis=1) < radius
            n = self.vn[near].sum(0) if near.any() else self.vn[np.argmin(np.linalg.norm(co - s, axis=1))]
            ns.append(n / (np.linalg.norm(n) or 1.0))
        ns = np.array(ns)
        best_d = np.full(len(co), np.inf)
        best_q = np.zeros_like(co)
        best_n = np.zeros_like(co)
        for i in range(max(1, len(P) - 1)):
            a, b = P[i], P[min(i + 1, len(P) - 1)]
            ab = b - a
            L2 = ab @ ab
            t = np.clip(((co - a) @ ab) / L2, 0, 1) if L2 > 0 else np.zeros(len(co))
            q = a + t[:, None] * ab
            d = np.linalg.norm(co - q, axis=1)
            m = d < best_d
            if m.any():
                n = ns[i] * (1 - t[m, None]) + ns[min(i + 1, len(P) - 1)] * t[m, None]
                best_d[m], best_q[m], best_n[m] = d[m], q[m], n
        best_n /= np.maximum(np.linalg.norm(best_n, axis=1), 1e-12)[:, None]
        inside = best_d < radius
        w = np.zeros(len(co))
        w[inside] = FALLOFF[falloff](best_d[inside] / radius)
        if self.front_faces_only if front is None else front:
            w *= np.clip(np.einsum("ij,ij->i", self.vn, best_n) / FRONT_RAMP, 0, 1)
        return w * (1 - self.mask), best_q, best_n

    def crease_line(self, points, radius, depth, pinch=0.5, falloff="pow4", project=True,
                    front=None):
        """Crease / Draw Sharp along a polyline in one pass.
        depth > 0 cuts a V groove (mouth line, lid fold, behind the ear); depth < 0 raises a
        pinched knife ridge (the Ctrl-crease: lid rim, lip border, ear rim).
        pinch pulls the flanks toward the line: 0 = plain Draw Sharp cut or Kaspar's
        'landmark pencil' (Morren: crease with pinch reduced), 0.5 = Crease Polish,
        0.8 = Crease Sharp. Falloff is measured from the line itself, so the groove is even
        along its length. project: True = nearest point, or a view direction such as
        (0, 1, 0) to drop the line onto the face as seen from the front (safer).
        A crease only reads with 3 to 4 vertices across `radius`:
        remesh finer first (Ryan King). Mirrored in X when symmetry is on."""
        P = self._resample_on_surface(points, radius * 0.25, project)
        paths = [P]
        if self.symmetry_x:
            paths.append(P * np.array([-1.0, 1, 1]))
        delta = np.zeros_like(self.co)
        wmax = np.zeros(len(self.co))
        for Q in paths:
            w, q, n = self._line_field(Q, radius, falloff, front)
            d = -n * (depth * w)[:, None]
            to = q - self.co
            to -= np.einsum("ij,ij->i", to, n)[:, None] * n      # tangential part only
            d += to * (w * pinch * 0.5)[:, None]
            take = w > wmax
            delta[take] = d[take]
            wmax = np.maximum(wmax, w)
        self.co += delta
        self._normals()
        return int((wmax > 0).sum())

    def move_feature(self, center, radius, translate=(0, 0, 0), scale=1.0, pivot=None,
                     falloff="smooth", blur=3, front=None):
        """Move or resize a whole feature instead of re-sculpting it (Morren's mask ->
        Set Pivot Unmasked -> Move/Scale; Reinhardt's lasso mask -> Smooth Mask x3 -> Move).
        Weight = falloff around `center` x (1 - mask), blurred `blur` times over the edge
        graph; pivot = weighted centroid unless given; scale is a float or (sx, sy, sz).
        Paint mask first on neighbours that must not follow (Morren erases the mask near
        the cheek before moving the ear). Remesh afterwards if faces stretched."""
        s = np.broadcast_to(np.asarray(scale, dtype=np.float64), (3,))
        t = np.asarray(translate, dtype=np.float64)
        total = np.zeros_like(self.co)
        wmax = np.zeros(len(self.co))
        for c, flip in self._passes(np.asarray(center, dtype=np.float64)):
            w = self._weights(c, radius, falloff, front)
            if not w.any():
                continue
            w = np.clip(self._blur(w, blur), 0, 1) * (1 - self.mask)
            pv = (self.co * w[:, None]).sum(0) / w.sum() if pivot is None \
                else np.asarray(pivot, dtype=np.float64) * flip
            target = pv + (self.co - pv) * s + t * flip
            d = (target - self.co) * w[:, None]
            take = w > wmax
            total[take] = d[take]
            wmax = np.maximum(wmax, w)
        self.co += total
        self._normals()
        return int((wmax > 1e-6).sum())

    def open_eyelids(self, center, radius, forward=(0, -1, 0), half_width_deg=40.0,
                     up_deg=16.0, down_deg=22.0, tilt_deg=8.0, recess=0.88, feather=0.35):
        """Open the eye in an 'eyelid mass' (Thelen gC3FG8-4lsU 00:15:43: a sphere a bit
        larger than the eyeball is joined into the head and remeshed; a copy becomes the
        eyeball). Skin inside an almond-shaped opening, measured as angles seen from the
        eye centre, is pushed back to `recess` x radius so the separate eyeball object
        shows through; the ring of skin left around it reads as the lids.
          up_deg < down_deg  the upper lid covers more of the iris (Abbitt 9N87-yRR5aE 00:26:25)
          tilt_deg           outer corner higher (Thelen 00:46:10: about 8 degrees)
        `radius` is the EYEBALL radius. Mirrored in X. Call again after a remesh to restate."""
        f0 = np.asarray(forward, dtype=np.float64)
        f0 = f0 / np.linalg.norm(f0)
        tt = math.radians(tilt_deg)
        total = np.zeros_like(self.co)
        wmax = np.zeros(len(self.co))
        for c, flip in self._passes(np.asarray(center, dtype=np.float64)):
            f = f0 * flip
            up = np.array([0.0, 0, 1]) - f * f[2]
            up /= np.linalg.norm(up)
            out = np.cross(up, f)
            if out @ np.array([np.sign(c[0]) or 1.0, 0, 0]) < 0:
                out = -out                                # +u points away from the midline
            d = self.co - c
            dist = np.linalg.norm(d, axis=1)
            dn = d / np.maximum(dist, 1e-12)[:, None]
            fz = dn @ f
            a_h = np.arctan2(dn @ out, fz)
            a_v = np.arctan2(dn @ up, fz)
            u = a_h * math.cos(tt) + a_v * math.sin(tt)   # opening rotated: outer corner up
            v = -a_h * math.sin(tt) + a_v * math.cos(tt)
            vv = np.where(v > 0, math.radians(up_deg), math.radians(down_deg))
            e = (u / math.radians(half_width_deg)) ** 2 + (v / vv) ** 2
            t = np.clip((1 - e) / feather, 0, 1)
            w = t * t * (3 - 2 * t)
            target = radius * recess
            w *= (fz > 0) & (dist < radius * 1.8) & (dist > target)
            w *= 1 - self.mask
            dd = dn * ((target - dist) * w)[:, None]
            take = w > wmax
            total[take] = dd[take]
            wmax = np.maximum(wmax, w)
        self.co += total
        self._normals()
        return int((wmax > 0).sum())

    def line_project(self, plane_co, plane_no):
        """Line Project (Keelan Jon's bust base, Reinhardt's flat ear sides): every vertex
        on the +normal side of the plane is projected onto it. Respects the mask.
        Voxel remesh afterwards: the flattened side walls leave degenerate faces."""
        n = np.asarray(plane_no, dtype=np.float64)
        n = n / np.linalg.norm(n)
        h = (self.co - np.asarray(plane_co, dtype=np.float64)) @ n
        move = np.where(h > 0, h, 0.0) * (1 - self.mask)
        self.co -= np.outer(move, n)
        self._normals()
        return int((move > 0).sum())

    # ---------- explicit planes: the executable form of "planes before lines" ----------
    # flatten/scrape dabs fit their plane to the surface under the brush, so on a convex
    # ball they follow the ball and barely change it (E2 with-skill run, 2026-09-24). Experts
    # cut planes against a STATED plane: Scrape with a locked plane, Trim / Line Project
    # (Reinhardt), Ctrl Clay Strips along the jaw side (Ryan King), Draw Sharp plane borders
    # re-cut whenever the face looks off (Thelen). These two methods take the plane as input.
    @staticmethod
    def _ramp(t, hardness):
        """1 inside hardness, smoothstep to 0 at t = 1: a flat core with a soft border."""
        r = np.clip((1 - t) / max(1 - hardness, 1e-6), 0, 1)
        return r * r * (3 - 2 * r)

    def plane_cut(self, center, normal, radius, offset=0.0, hardness=0.6, mode="scrape",
                  region="cylinder", front=True, depth=None, max_move=None, strength=1.0,
                  plane_point=None):
        """Bring everything above an explicit plane down onto it (Scrape with a fixed plane,
        Trim tools, Line Project limited to a region). The plane passes through
        center + normal * offset (offset < 0 cuts deeper than the stated point) with the given
        outward normal, or through plane_point when given (a landmark such as the gonion:
        then center only places the region). Region: vertices within `radius` of the axis
        through center along the
        normal ('cylinder', like a Trim lasso seen along the normal) or of center ('sphere').
        Weight 1 inside hardness * radius, smoothstep to 0 at radius. mode: 'scrape' lowers
        only what is above; 'flatten' also raises what is below, down to `depth`
        (default 0.3 x radius); 'fill' only raises. front: skip vertices facing away from the
        plane (the far side of an ear or lip). max_move: leave vertices that would travel
        further alone (protects a nose inside a wide cheek cut). Mirrored in X with symmetry.
        Returns (vertices moved, largest move)."""
        n0 = np.asarray(normal, dtype=np.float64)
        n0 = n0 / np.linalg.norm(n0)
        depth = radius * 0.3 if depth is None else depth
        delta = np.zeros_like(self.co)
        wmax = np.zeros(len(self.co))
        for c, flip in self._passes(np.asarray(center, dtype=np.float64)):
            n = n0 * flip
            q = c + n * offset if plane_point is None else np.asarray(plane_point, dtype=np.float64) * flip
            hgt = (self.co - q) @ n
            relc = self.co - c                              # the region is centred on center
            d = np.linalg.norm(relc - np.outer(relc @ n, n), axis=1) if region == "cylinder" \
                else np.linalg.norm(relc, axis=1)
            w = np.where(d < radius, self._ramp(d / radius, hardness), 0.0)
            if front:
                w *= np.clip((self.vn @ n) / FRONT_RAMP, 0, 1)
            w *= (1 - self.mask) * min(abs(strength), 1.0)
            if mode == "scrape":
                move = np.where(hgt > 0, hgt, 0.0)
            elif mode == "fill":
                move = np.where((hgt < 0) & (hgt > -depth), hgt, 0.0)
            elif mode == "flatten":
                move = np.where(hgt > -depth, hgt, 0.0)
            else:
                raise ValueError(f"unknown plane_cut mode {mode}")
            if max_move is not None:
                w *= np.clip((max_move - np.abs(move)) / (0.25 * max_move), 0, 1)
            dd = -n[None, :] * (move * w)[:, None]
            take = w > wmax
            delta[take] = dd[take]
            wmax = np.maximum(wmax, w)
        self.co += delta
        self._normals()
        mv = np.linalg.norm(delta, axis=1)
        return int((mv > 0).sum()), float(mv.max())

    def corner_cut(self, planes, center, radius, hardness=0.5, iterations=8, front=True,
                   max_move=None):
        """Hard corner where two or three stated planes meet: the gonial angle (jaw side +
        jaw underside + ramus back), the cheekbone edge (front plane + side plane of the
        zygoma), a square chin (front + underside). planes = [(point, outward_normal), ...].
        Every vertex in the sphere (center, radius) that lies outside the intersection of the
        half-spaces is projected onto it (alternating projections, exact for two planes),
        weighted like plane_cut. front: only vertices whose normal agrees with the plane
        they violate move (the neck under a jaw corner faces sideways or forward and stays;
        mask it too when in doubt). max_move: optional guard, vertices that would travel
        further are faded out (it can leave a ridge; prefer front and the mask). The edge is
        as sharp as the mesh allows: every
        remesh softens it, restate after each remesh (Reinhardt, Keelan Jon). Mirrored in X.
        Returns (vertices moved, largest move)."""
        pl0 = []
        for p, n in planes:
            n = np.asarray(n, dtype=np.float64)
            pl0.append((np.asarray(p, dtype=np.float64), n / np.linalg.norm(n)))
        delta = np.zeros_like(self.co)
        wmax = np.zeros(len(self.co))
        for c, flip in self._passes(np.asarray(center, dtype=np.float64)):
            pl = [(p * flip, n * flip) for p, n in pl0]
            d = np.linalg.norm(self.co - c, axis=1)
            w = np.where(d < radius, self._ramp(d / radius, hardness), 0.0) * (1 - self.mask)
            if front:
                viol = np.stack([(self.co - p) @ n for p, n in pl], 1)
                worst = np.argmax(viol, axis=1)
                nw = np.stack([n for _p, n in pl])[worst]
                w *= np.clip(np.einsum("ij,ij->i", self.vn, nw) / FRONT_RAMP, 0, 1)
            P = self.co.copy()
            for _ in range(iterations):
                for p, n in pl:
                    P -= np.outer(np.maximum((P - p) @ n, 0.0), n)
            move = P - self.co
            if max_move is not None:
                w *= np.clip((max_move - np.linalg.norm(move, axis=1)) / (0.25 * max_move), 0, 1)
            dd = move * w[:, None]
            take = w > wmax
            delta[take] = dd[take]
            wmax = np.maximum(wmax, w)
        self.co += delta
        self._normals()
        mv = np.linalg.norm(delta, axis=1)
        return int((mv > 0).sum()), float(mv.max())

    def planarity(self, center, radius, region="sphere", normal=None):
        """Flatness of the surface around center: best-fit plane RMS residual / radius
        (0 = a perfect plane; a sphere of radius R sampled over a disc of radius r gives about
        r / (6.9 R)). region 'cylinder' needs the axis `normal`. Returns (rms_over_radius,
        fitted normal, vertex count)."""
        c = np.asarray(center, dtype=np.float64)
        if region == "cylinder":
            n = np.asarray(normal, dtype=np.float64) / np.linalg.norm(normal)
            rel = self.co - c
            sel = np.linalg.norm(rel - np.outer(rel @ n, n), axis=1) < radius
            sel &= (self.vn @ n) > 0
        else:
            sel = np.linalg.norm(self.co - c, axis=1) < radius
        if sel.sum() < 4:
            return float("nan"), None, int(sel.sum())
        _c, nrm, rms = fit_plane(self.co[sel])
        return rms / radius, nrm, int(sel.sum())


def voxel_remesh(obj, voxel_size, fix_poles=True, preserve_volume=True):
    """Blender's voxel remesh (Ctrl+R in sculpt mode). Merges intersecting parts.
    Headless it must run in Object Mode: object.voxel_remesh CRASHES Blender 5.2.1 in
    background when the object is in Sculpt Mode (probe_headless_ops.py)."""
    if bpy.app.background and obj.mode != "OBJECT":
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.objects.active = obj
    me = obj.data
    me.remesh_voxel_size = voxel_size
    me.use_remesh_fix_poles = fix_poles
    me.use_remesh_preserve_volume = preserve_volume
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
        bpy.ops.object.voxel_remesh()
    me.shade_smooth()   # remesh output is flat-shaded: faceted banding in every render
    return len(me.vertices)


def symmetrize(obj, direction="POSITIVE_X", threshold=1e-4):
    """Make the mesh exactly X-symmetric (Keelan Jon symmetrizes after every remesh).
    Edit-mode mesh.symmetrize, headless-safe; sculpt.symmetrize CRASHES headless.
    POSITIVE_X keeps the +X half and mirrors it onto -X (verified 5.2.1)."""
    vl = bpy.context.view_layer
    # only this object may enter Edit Mode: every other selected mesh would be
    # symmetrized too (multi-object editing); selection is restored afterwards
    selected = [o for o in vl.objects if o.select_get()]
    active = vl.objects.active
    for o in selected:
        o.select_set(False)
    obj.select_set(True)
    vl.objects.active = obj
    try:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.symmetrize(direction=direction, threshold=threshold)
        bpy.ops.object.mode_set(mode="OBJECT")
    finally:
        obj.select_set(obj in selected)
        for o in selected:
            o.select_set(True)
        vl.objects.active = active or obj
    obj.data.shade_smooth()
    return len(obj.data.vertices)


def _co_of(obj):
    me = obj.data
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def mirror_error(obj):
    """Distance from each vertex mirrored in local X to the nearest vertex (max, mean)."""
    from mathutils.kdtree import KDTree
    co = _co_of(obj)
    kd = KDTree(len(co))
    for i, v in enumerate(co):
        kd.insert(v, i)
    kd.balance()
    d = np.array([kd.find((-v[0], v[1], v[2]))[2] for v in co])
    return {"max": float(d.max()), "mean": float(d.mean())}


def surface_area(obj):
    a = np.empty(len(obj.data.polygons))
    obj.data.polygons.foreach_get("area", a)
    return float(a.sum())


def voxel_for_faces(obj, target_faces):
    """Voxel size that gives about target_faces on this surface: voxel remesh output has
    faces ~ 1.5 * area / voxel^2 (measured on spheres and ellipsoids in 5.2.1, within 3%)."""
    return math.sqrt(1.5 * surface_area(obj) / target_faces)


def _labels(n, edges):
    """Connected-component label per vertex (numpy min-propagation + pointer jumping)."""
    lab = np.arange(n)
    if len(edges) == 0:
        return lab
    a, b = edges[:, 0], edges[:, 1]
    while True:
        m = np.minimum(lab[a], lab[b])
        new = lab.copy()
        np.minimum.at(new, a, m)
        np.minimum.at(new, b, m)
        new = new[new]
        while True:
            nxt = new[new]
            if np.array_equal(nxt, new):
                break
            new = nxt
        if np.array_equal(new, lab):
            return lab
        lab = new


def drop_islands(obj, keep_ratio=0.01):
    """Delete loose shells smaller than keep_ratio x the vertex count (the largest always
    stays). Voxel remesh seals tiny internal voids where 3 or more primitives meet (E2 run:
    a 7-vertex shell between nose, muzzle and alae made stage_report say 2 components).
    Called by union_remesh and remesh_stage. Returns the number of vertices removed."""
    me = obj.data
    n = len(me.vertices)
    e = np.empty(len(me.edges) * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", e)
    lab = _labels(n, e.reshape(-1, 2))
    ids, counts = np.unique(lab, return_counts=True)
    small = ids[(counts < keep_ratio * n) & (counts < counts.max())]
    if len(small) == 0:
        return 0
    kill = np.isin(lab, small)
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.verts.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.verts[i] for i in np.flatnonzero(kill)], context="VERTS")
    bm.to_mesh(me)
    bm.free()
    me.update()
    return int(kill.sum())


def remesh_stage(obj, voxel_size, relax=2, symmetric=True, direction="POSITIVE_X",
                 clear_mask=True, keep_ratio=0.01):
    """One resolution step of the ladder: voxel remesh, drop sealed internal shells
    (drop_islands), symmetrize (remesh is not symmetric: its grid is not centred on x=0),
    then a volume-preserving relax that removes voxel jaggies (Kaspar runs the Smooth mesh
    filter after remeshing finer). clear_mask: the mask survives voxel remesh (preserve
    attributes is on by default) and would silently protect regions in the next stage.
    The report gains 'islands_dropped'."""
    voxel_remesh(obj, voxel_size)
    dropped = drop_islands(obj, keep_ratio) if keep_ratio else 0
    if symmetric:
        symmetrize(obj, direction)
    sc = Sculptor(obj, symmetry_x=False)
    if clear_mask:
        sc.clear_mask()
    if relax:
        sc.smooth(None, None, strength=0.5, iterations=relax, preserve_volume=True)
    sc.commit()
    rep = stage_report(obj, voxel_size)
    rep["islands_dropped"] = dropped
    return rep


def stage_report(obj, voxel_size=None):
    """Numbers to gate a sculpt stage on: faces, height H, voxel as a fraction of H,
    connected components (1 after a union remesh), non-manifold edges, stretch
    (p95 / median edge length: above ~2.5 means remesh), X-mirror error, applied scale."""
    me = obj.data
    co = _co_of(obj)
    e = np.empty(len(me.edges) * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", e)
    e = e.reshape(-1, 2)
    L = np.linalg.norm(co[e[:, 0]] - co[e[:, 1]], axis=1)
    le = np.empty(len(me.loops), dtype=np.int64)
    me.loops.foreach_get("edge_index", le)
    per_edge = np.bincount(le, minlength=len(me.edges))
    H = float(co[:, 2].max() - co[:, 2].min())
    m = mirror_error(obj)
    return {
        "faces": len(me.polygons), "verts": len(me.vertices), "height": round(H, 5),
        "voxel": voxel_size, "voxel_over_H": round(voxel_size / H, 5) if voxel_size else None,
        "components": int(len(np.unique(_labels(len(co), e)))), "non_manifold_edges": int((per_edge != 2).sum()),
        "stretch_p95_over_median": round(float(np.percentile(L, 95) / np.median(L)), 3),
        "mirror_max": round(m["max"], 6), "mirror_mean": round(m["mean"], 6),
        "scale_applied": all(abs(s - 1) < 1e-6 for s in obj.scale),
    }


def fit_plane(points):
    """Least-squares plane: (centroid, unit normal, RMS residual)."""
    P = np.asarray(points, dtype=np.float64)
    c = P.mean(0)
    _u, s, vt = np.linalg.svd(P - c, full_matrices=False)
    return c, vt[2], float(s[2] / math.sqrt(len(P)))
