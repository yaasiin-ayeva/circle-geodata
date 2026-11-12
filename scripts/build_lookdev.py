"""Dress the first stretch of Oxford Street from Danquah Circle at target quality, for the look-dev test.

    python3 scripts/build_lookdev.py --slice data/work/slice --out data/work/osu-poc
    python3 scripts/build_district.py --slice data/work/slice --out data/work/osu-poc --exclude data/work/osu-poc/osu-lookdev-ids.txt

Writes, next to the district files (same axes and origin, see build_district.py):

  osu-lookdev.obj, .mtl   facades with recessed windows, burglar bars, AC units and lit or shuttered shopfronts,
                          shop signs, awnings, roof polytanks; raised sidewalks with painted kerbs, open gutters
                          and slab crossings; power poles and sagging cables; kiosks and vendor umbrellas
  osu-lookdev.json        shop and kiosk lights, the zone outline, counts
  osu-lookdev-ids.txt     building ids the detailed versions replace, for build_district.py --exclude
  signs-atlas.png         32 invented shop signs (4 x 8 cells), no real brand or phone number
  interiors-atlas.png     what a lit window or shopfront shows (4 x 4 cells): 8 rooms behind curtains,
                          8 shop interiors with shelves, goods and a counter

Everything is generated from the footprints and the street line; shapes, colours and sign choices are seeded
by building id, so reruns give the same street.
"""
import argparse
import json
import math
import os
import sys

import numpy as np
import shapely
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import linemerge, substring

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_district as bd  # noqa: E402

ZONE_LENGTH = 220.0     # metres of Oxford Street from Danquah Circle
ZONE_HALF_WIDTH = 42.0
ROAD_HALF = bd.ROAD_WIDTH["secondary"] / 2   # Oxford Street's class in OSM
GUTTER = 0.7
KERB = 0.3
SIDEWALK = 2.8
SIDEWALK_Z = 0.35
GUTTER_Z = 0.02
STOREY = 3.2

SIGNS = [
    ("GOD FIRST ELECTRICALS", "WHOLESALE & RETAIL"), ("SHARP SHARP PHONES", "REPAIRS WHILE YOU WAIT"),
    ("CHALE CHOP BAR", "WAAKYE  KENKEY  FUFU"), ("NYAME NHYIRA COSMETICS", "QUALITY ASSURED"),
    ("BLESSED HANDS SALON", "BRAIDS  WEAVES  NAILS"), ("OSU NIGHT PHARMACY", "OPEN 24 HOURS"),
    ("AYEKOO MOBILE MONEY", "CASH IN  CASH OUT"), ("ADOM FASHION HOUSE", "KENTE  BATIK  READY-MADE"),
    ("TRUST GOD SPARE PARTS", "GENUINE PARTS"), ("MAAME ESI'S KITCHEN", "LOCAL DISHES"),
    ("SANKOFA BOOKSHOP", "SCHOOL  CHURCH  NOVELS"), ("FAST FAST CARGO", "SEA & AIR FREIGHT"),
    ("OXFORD GRILL HOUSE", "KEBAB  KELEWELE  DRINKS"), ("CIRCLE FOREX BUREAU", "BUY & SELL"),
    ("PRAISE OPTICALS", "EYE TEST INSIDE"), ("AKWAABA MINI MART", "PROVISIONS  DRINKS"),
    ("GOLDEN STOOL BEAUTY", "PERFUMES  WIGS"), ("COUNTRY BOY FUFU", "LIGHT SOUP  PALM NUT"),
    ("ODO NE NKWA CHEMIST", "LICENSED CHEMICAL SELLER"), ("NO WAHALA PRINTING", "BANNERS  T-SHIRTS"),
    ("ABUSUA FURNITURE", "MADE IN GHANA"), ("EBENEZER ELECTRONICS", "TV  FRIDGE  AC"),
    ("SWEET MOTHER BAKERY", "TEA BREAD  MEAT PIE"), ("JUST BELIEVE BARBERING", "WELCOME"),
    ("HOPE CITY TAILORING", "SEWING & ALTERATIONS"), ("KOKOO NIGHT CLUB", "LIVE DJ FRIDAYS"),
    ("BRA FIE FOOTWEAR", "LADIES  GENTS  KIDS"), ("SIKA PA LOTTO KIOSK", "STAKE HERE"),
    ("ANANSE GAMING CENTRE", "PLAY  WIN  RELAX"), ("FIIFI'S CAR WASH", "WASH & GO"),
    ("ROYAL DIAMOND HOTEL", "ROOMS AVAILABLE"), ("SIKA DWA JEWELLERY", "GOLD & SILVER"),
]
SIGN_COLOURS = [((200, 30, 35), (255, 255, 255)), ((255, 205, 20), (25, 25, 25)), ((20, 120, 60), (255, 240, 200)),
                ((15, 60, 160), (255, 255, 255)), ((245, 245, 240), (190, 20, 30)), ((25, 25, 25), (255, 200, 30)),
                ((230, 90, 20), (255, 255, 255)), ((120, 30, 120), (255, 230, 120))]

MATERIALS = {
    "M_Facade_Cream": (0.86, 0.82, 0.70), "M_Facade_Ochre": (0.80, 0.62, 0.38), "M_Facade_Blue": (0.55, 0.70, 0.80),
    "M_Facade_Terracotta": (0.72, 0.42, 0.30), "M_Facade_Mint": (0.60, 0.78, 0.64), "M_Facade_Grey": (0.62, 0.62, 0.60),
    "M_Facade_Yellow": (0.92, 0.78, 0.30), "M_Facade_Pink": (0.86, 0.62, 0.62),
    "M_WindowDark": (0.03, 0.04, 0.05), "M_WindowLit_Warm": (1.0, 0.78, 0.48), "M_WindowLit_Cool": (0.82, 0.90, 1.0),
    "M_Grille": (0.85, 0.85, 0.82), "M_AC": (0.88, 0.88, 0.86),
    "M_ShopGlass_Warm": (1.0, 0.80, 0.55), "M_ShopGlass_Cool": (0.85, 0.93, 1.0), "M_Shutter": (0.45, 0.50, 0.55),
    "M_Sign_Lit": (1, 1, 1), "M_Sign_Painted": (1, 1, 1), "M_SignFrame": (0.2, 0.2, 0.22),
    "M_Awning_Red": (0.75, 0.12, 0.10), "M_Awning_Blue": (0.12, 0.30, 0.65), "M_Awning_Green": (0.15, 0.50, 0.25),
    "M_RoofTin": (0.55, 0.50, 0.45), "M_Parapet": (0.80, 0.78, 0.72), "M_Polytank": (0.05, 0.05, 0.05),
    "M_Sidewalk": (0.62, 0.60, 0.56), "M_KerbWhite": (0.90, 0.90, 0.88), "M_KerbBlack": (0.06, 0.06, 0.06),
    "M_Gutter": (0.12, 0.12, 0.11), "M_GutterWall": (0.45, 0.44, 0.40), "M_Slab": (0.55, 0.54, 0.50),
    "M_PowerPole": (0.50, 0.48, 0.45), "M_Cable": (0.02, 0.02, 0.02),
    "M_Kiosk_Blue": (0.15, 0.35, 0.70), "M_Kiosk_Yellow": (0.95, 0.75, 0.10), "M_Kiosk_Red": (0.75, 0.15, 0.12),
    "M_Umbrella_A": (0.95, 0.80, 0.10), "M_Umbrella_B": (0.10, 0.45, 0.20), "M_Umbrella_C": (0.80, 0.10, 0.12),
    "M_Table": (0.35, 0.25, 0.15),
}
FACADES = [m for m in MATERIALS if m.startswith("M_Facade_")]


class Builder:
    def __init__(self):
        self.mesh = bd.Mesh()
        self.lights = []
        self.counts = {}

    def count(self, key, n=1):
        self.counts[key] = self.counts.get(key, 0) + n

    def quad(self, obj, mat, a, b, c, d, uv=None):
        self.mesh.quad(obj, mat, a, b, c, d, uv)

    def box(self, obj, mat, centre, size, yaw=0.0, top=True, bottom=False):
        """Axis box at centre (e, n, u) with size (along, across, height), rotated by yaw (radians from east)."""
        ce, cn, cu = centre
        sx, sy, sz = size[0] / 2, size[1] / 2, size[2] / 2
        c, s = math.cos(yaw), math.sin(yaw)
        P = lambda x, y, z: (ce + x * c - y * s, cn + x * s + y * c, cu + z)
        v = [P(x, y, z) for z in (-sz, sz) for y in (-sy, sy) for x in (-sx, sx)]
        # v index: (x, y, z) bits -> 0:(-,-,-) 1:(+,-,-) 2:(-,+,-) 3:(+,+,-) 4..7 same with z+
        faces = [(0, 1, 5, 4), (1, 3, 7, 5), (3, 2, 6, 7), (2, 0, 4, 6)]
        if top:
            faces.append((4, 5, 7, 6))
        if bottom:
            faces.append((2, 3, 1, 0))
        for f in faces:
            self.quad(obj, mat, *(v[i] for i in f))

    def prism(self, obj, mat, centre, radius, z0, z1, sides=8, cap=True):
        ce, cn = centre
        pts = [(ce + radius * math.cos(2 * math.pi * k / sides), cn + radius * math.sin(2 * math.pi * k / sides)) for k in range(sides)]
        for k in range(sides):
            a, b = pts[k], pts[(k + 1) % sides]
            self.quad(obj, mat, (*a, z0), (*b, z0), (*b, z1), (*a, z1))
        if cap:
            for k in range(1, sides - 1):
                self.mesh.tri(obj, mat, (*pts[0], z1), (*pts[k], z1), (*pts[k + 1], z1))


def wall_point(a, t, n, s, depth):
    return (a[0] + t[0] * s + n[0] * depth, a[1] + t[1] * s + n[1] * depth)


def wall_rect(B, obj, mat, a, t, n, s0, s1, z0, z1, depth=0.0, uv_scale=3.0, flip=False):
    """Quad in the wall plane from s0 to s1 along the edge and z0 to z1, pushed out by depth along the outward normal."""
    p0, p1 = wall_point(a, t, n, s0, depth), wall_point(a, t, n, s1, depth)
    uv = ((s0 / uv_scale, z0 / uv_scale), (s1 / uv_scale, z0 / uv_scale), (s1 / uv_scale, z1 / uv_scale), (s0 / uv_scale, z1 / uv_scale))
    if flip:
        B.quad(obj, mat, (*p1, z0), (*p0, z0), (*p0, z1), (*p1, z1), uv)
    else:
        B.quad(obj, mat, (*p0, z0), (*p1, z0), (*p1, z1), (*p0, z1), uv)


def interior_uv(cell):
    """UVs of one cell of the interiors atlas (4 x 4), inset a little so mipmaps don't bleed."""
    col, row, e = cell % 4, cell // 4, 0.004
    u0, u1, v0, v1 = col / 4 + e, (col + 1) / 4 - e, 1 - (row + 1) / 4 + e, 1 - row / 4 - e
    return ((u0, v0), (u1, v0), (u1, v1), (u0, v1))


def opening(B, obj, wall, back, a, t, n, s0, s1, z0, z1, depth, back_uv=None):
    """A recessed opening: four reveal faces into the wall and a back face at -depth."""
    p = lambda s, d: wall_point(a, t, n, s, d)
    l0, l1, r0, r1 = p(s0, 0), p(s0, -depth), p(s1, 0), p(s1, -depth)
    B.quad(obj, wall, (*l0, z0), (*l1, z0), (*l1, z1), (*l0, z1))            # left reveal, faces +t
    B.quad(obj, wall, (*r1, z0), (*r0, z0), (*r0, z1), (*r1, z1))            # right reveal, faces -t
    B.quad(obj, wall, (*r0, z0), (*r1, z0), (*l1, z0), (*l0, z0))            # sill, faces up
    B.quad(obj, wall, (*r1, z1), (*r0, z1), (*l0, z1), (*l1, z1))            # head, faces down
    if back and back_uv:
        b0, b1 = p(s0, -depth), p(s1, -depth)
        B.quad(obj, back, (*b0, z0), (*b1, z0), (*b1, z1), (*b0, z1), back_uv)
    elif back:
        wall_rect(B, obj, back, a, t, n, s0, s1, z0, z1, depth=-depth)


def facade(B, bid, poly, height, storeys, street, street_dir, rng):
    obj = bd.cell_name("LookDev_Buildings", poly.centroid.x, poly.centroid.y)
    wall = FACADES[int(bd.unit_hash(bid, "paint") * len(FACADES))]
    rings = [poly.exterior] + list(poly.interiors)
    for ring_index, ring in enumerate(rings):
        exterior = ring_index == 0
        pts = list(ring.coords)[:-1]
        for i in range(len(pts)):
            a, b = np.array(pts[i]), np.array(pts[(i + 1) % len(pts)])
            L = float(np.linalg.norm(b - a))
            if L < 0.05:
                continue
            t = (b - a) / L
            n = np.array([t[1], -t[0]])          # outward for a counter-clockwise exterior
            mid = (a + b) / 2
            to_street = np.array(street.interpolate(street.project(Point(*mid))).coords[0]) - mid
            dist = float(np.linalg.norm(to_street))
            faces_street = exterior and dist < 22 and abs(float(np.dot(t, street_dir(mid)))) > 0.75 \
                and float(np.dot(n, to_street)) > 0
            if L < 2.4 or not exterior:
                wall_rect(B, obj, wall, a, t, n, 0, L, 0, height)
                continue
            bays = max(1, int(round(L / 3.6)))
            bw = L / bays
            for k in range(bays):
                s0, s1 = k * bw, (k + 1) * bw
                for f in range(storeys):
                    z0, z1 = f * STOREY, (f + 1) * STOREY
                    r = bd.unit_hash(bid, i, k, f)
                    if f == 0 and faces_street:
                        shopfront(B, obj, wall, bid, a, t, n, s0, s1, z0, z1, r, (i, k))
                    elif f > 0 and bw > 2.2:
                        window(B, obj, wall, a, t, n, s0, s1, z0, z1, r)
                    else:
                        wall_rect(B, obj, wall, a, t, n, s0, s1, z0, z1)
                wall_rect(B, obj, wall, a, t, n, s0, s1, storeys * STOREY, height)
    # Parapet cap and roof
    rooftop(B, bid, poly, height)


def window(B, obj, wall, a, t, n, s0, s1, z0, z1, r):
    w = min(1.4, (s1 - s0) - 1.0)
    c = (s0 + s1) / 2
    w0, w1, h0, h1 = c - w / 2, c + w / 2, z0 + 0.95, z0 + 2.35
    wall_rect(B, obj, wall, a, t, n, s0, w0, z0, z1)
    wall_rect(B, obj, wall, a, t, n, w1, s1, z0, z1)
    wall_rect(B, obj, wall, a, t, n, w0, w1, z0, h0)
    wall_rect(B, obj, wall, a, t, n, w0, w1, h1, z1)
    glass = "M_WindowLit_Warm" if r < 0.09 else ("M_WindowLit_Cool" if r < 0.28 else "M_WindowDark")   # mostly white tubes and LED
    room = interior_uv(int(r * 1000) % 8) if glass != "M_WindowDark" else None
    opening(B, obj, wall, glass, a, t, n, w0, w1, h0, h1, 0.18, room)
    B.count("windows")
    if glass != "M_WindowDark":
        B.count("lit_windows")
    if r > 0.12:  # burglar-proof bars on most windows
        for j in range(1, 5):
            s = w0 + (w1 - w0) * j / 5
            p = wall_point(a, t, n, s, -0.06)
            B.box(obj, "M_Grille", (p[0], p[1], (h0 + h1) / 2), (0.03, 0.03, h1 - h0), yaw=math.atan2(t[1], t[0]))
    if 0.55 < r < 0.7:  # an AC unit below the window
        p = wall_point(a, t, n, c, 0.2)
        B.box(obj, "M_AC", (p[0], p[1], h0 - 0.35), (0.8, 0.4, 0.5), yaw=math.atan2(t[1], t[0]))
        B.count("ac_units")


def shopfront(B, obj, wall, bid, a, t, n, s0, s1, z0, z1, r, key):
    yaw = math.atan2(t[1], t[0])
    o0, o1, top = s0 + 0.25, s1 - 0.25, z0 + 2.75
    wall_rect(B, obj, wall, a, t, n, s0, o0, z0, z1)
    wall_rect(B, obj, wall, a, t, n, o1, s1, z0, z1)
    wall_rect(B, obj, wall, a, t, n, o0, o1, top, z1)
    lit = r < 0.62
    back = ("M_ShopGlass_Warm" if r < 0.16 else "M_ShopGlass_Cool") if lit else "M_Shutter"
    shop = interior_uv(8 + int(bd.unit_hash(bid, key, "interior") * 8)) if lit else None
    opening(B, obj, wall, back, a, t, n, o0, o1, z0, top, 0.45, shop)
    B.count("shops_lit" if lit else "shops_shuttered")
    # Sign board above the opening, front face mapped to one atlas cell.
    cell = int(bd.unit_hash(bid, key, "sign") * len(SIGNS))
    col, row = cell % 4, cell // 4
    u0, u1, v0, v1 = col / 4, (col + 1) / 4, 1 - (row + 1) / 8, 1 - row / 8
    d = 0.18
    sz0, sz1 = top + 0.05, min(z1 - 0.1, top + 0.95)
    p0, p1 = wall_point(a, t, n, o0, d), wall_point(a, t, n, o1, d)
    sign = "M_Sign_Lit" if lit or r > 0.85 else "M_Sign_Painted"
    B.quad(obj, sign, (*p0, sz0), (*p1, sz0), (*p1, sz1), (*p0, sz1), ((u0, v0), (u1, v0), (u1, v1), (u0, v1)))
    q0, q1 = wall_point(a, t, n, o0, 0), wall_point(a, t, n, o1, 0)
    B.quad(obj, "M_SignFrame", (*q0, sz1), (*p0, sz1), (*p1, sz1), (*q1, sz1))   # top
    B.quad(obj, "M_SignFrame", (*q0, sz0), (*q1, sz0), (*p1, sz0), (*p0, sz0))   # underside
    B.count("signs")
    if 0.25 < bd.unit_hash(bid, key, "awning") < 0.6:
        colour = ["M_Awning_Red", "M_Awning_Blue", "M_Awning_Green"][int(bd.unit_hash(bid, key, "awcol") * 3)]
        h = top - 0.05
        w0, w1 = wall_point(a, t, n, o0 - 0.1, 0.05), wall_point(a, t, n, o1 + 0.1, 0.05)
        f0, f1 = wall_point(a, t, n, o0 - 0.1, 1.4), wall_point(a, t, n, o1 + 0.1, 1.4)
        B.quad(obj, colour, (*w0, h), (*f0, h - 0.45), (*f1, h - 0.45), (*w1, h))     # top
        B.quad(obj, colour, (*w1, h), (*f1, h - 0.45), (*f0, h - 0.45), (*w0, h))     # underside
        B.count("awnings")
    if lit and int(key[1]) % 2 == 0:
        p = wall_point(a, t, n, (o0 + o1) / 2, 1.3)
        B.lights.append({"x": round(float(p[0]), 2), "y": 2.6, "z": round(float(p[1]), 2),
                         "kind": "shop_warm" if back == "M_ShopGlass_Warm" else "shop_cool"})


def rooftop(B, bid, poly, height):
    obj = bd.cell_name("LookDev_Roofs", poly.centroid.x, poly.centroid.y)
    rings = [poly.exterior] + list(poly.interiors)
    verts = np.array([p for r in rings for p in list(r.coords)[:-1]], dtype=np.float64)
    ends = np.cumsum([len(r.coords) - 1 for r in rings]).astype(np.uint32)
    idx = bd.earcut.triangulate_float64(verts, ends)
    roof_z = height - 0.6
    for k in range(0, len(idx), 3):
        a, b, c = (tuple(verts[idx[k + j]]) for j in range(3))
        if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) < 0:
            b, c = c, b
        B.mesh.tri(obj, "M_RoofTin", (*a, roof_z), (*b, roof_z), (*c, roof_z))
    # Parapet: inner face and cap along the exterior.
    inner = poly.buffer(-0.2, join_style=2)
    if isinstance(inner, Polygon) and not inner.is_empty:
        inner = orient(inner, 1.0)
        pts = list(inner.exterior.coords)[:-1]
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            B.quad(obj, "M_Parapet", (*b, roof_z), (*a, roof_z), (*a, height), (*b, height))
    if bd.unit_hash(bid, "tank") < 0.45 and poly.area > 25:
        p = poly.buffer(-1.2).representative_point() if poly.buffer(-1.2).area > 1 else poly.representative_point()
        B.box(obj, "M_SignFrame", (p.x, p.y, roof_z + 0.3), (1.6, 1.6, 0.6))
        B.prism(obj, "M_Polytank", (p.x, p.y), 0.75, roof_z + 0.6, roof_z + 2.2, sides=10)
        B.count("polytanks")


def street_furniture(B, line, rng):
    """Sidewalks, painted kerbs, open gutters with slab crossings, power poles and cables, kiosks, umbrellas."""
    step = 1.0
    ds = np.arange(0, line.length, step)
    P = np.array([line.interpolate(d).coords[0] for d in ds])
    T = np.gradient(P, axis=0)
    T /= np.linalg.norm(T, axis=1)[:, None]
    N = np.column_stack([-T[:, 1], T[:, 0]])
    obj_of = lambda p: bd.cell_name("LookDev_Street", p[0], p[1])

    def strip(i, off0, off1, z0, z1, mat):
        a0, a1 = P[i] + N[i] * off0, P[i + 1] + N[i + 1] * off0
        b0, b1 = P[i] + N[i] * off1, P[i + 1] + N[i + 1] * off1
        uv = ((ds[i] / 3, off0 / 3), (ds[i + 1] / 3, off0 / 3), (ds[i + 1] / 3, off1 / 3), (ds[i] / 3, off1 / 3))
        B.quad(obj_of(P[i]), mat, (*a0, z0), (*a1, z0), (*b1, z1), (*b0, z1), uv)

    def side_wall(i, off, z0, z1, mat, face_left):
        a, b = P[i] + N[i] * off, P[i + 1] + N[i + 1] * off
        uv = ((ds[i] / 3, z0 / 3), (ds[i + 1] / 3, z0 / 3), (ds[i + 1] / 3, z1 / 3), (ds[i] / 3, z1 / 3))
        if face_left:
            B.quad(obj_of(P[i]), mat, (*b, z0), (*a, z0), (*a, z1), (*b, z1), uv)
        else:
            B.quad(obj_of(P[i]), mat, (*a, z0), (*b, z0), (*b, z1), (*a, z1), uv)

    g0, g1 = ROAD_HALF, ROAD_HALF + GUTTER
    k1, w1 = g1 + KERB, g1 + KERB + SIDEWALK
    for side in (1, -1):
        lo = lambda x, y: (min(side * x, side * y), max(side * x, side * y))
        for i in range(len(P) - 1):
            slab = int(ds[i]) % 9 in (0, 1) and bd.unit_hash("slab", side, int(ds[i]) // 9) < 0.5
            o0, o1 = lo(g0, g1)
            if slab:
                strip(i, o0, o1, SIDEWALK_Z, SIDEWALK_Z, "M_Slab")
            else:
                strip(i, o0, o1, GUTTER_Z, GUTTER_Z, "M_Gutter")
                side_wall(i, side * g0, GUTTER_Z, 0.07, "M_GutterWall", face_left=side > 0)
                side_wall(i, side * g1, GUTTER_Z, SIDEWALK_Z, "M_GutterWall", face_left=side < 0)
            o0, o1 = lo(g1, k1)
            strip(i, o0, o1, SIDEWALK_Z, SIDEWALK_Z, "M_KerbWhite" if int(ds[i]) % 2 == 0 else "M_KerbBlack")
            o0, o1 = lo(k1, w1)
            strip(i, o0, o1, SIDEWALK_Z, SIDEWALK_Z, "M_Sidewalk")
            side_wall(i, side * w1, 0.0, SIDEWALK_Z, "M_Slab", face_left=side > 0)
        B.count("sidewalk_m", int(line.length))

    # Power poles on the west side every 32 m, three sagging cables between them, a service drop at each pole.
    poles = []
    for d in np.arange(6, line.length, 32):
        i = min(int(d / step), len(P) - 1)
        base = P[i] + N[i] * (-(k1 + 0.6))
        poles.append((base, T[i], N[i]))
        obj = obj_of(base)
        B.prism(obj, "M_PowerPole", tuple(base), 0.14, 0, 9.5, sides=6)
        B.box(obj, "M_PowerPole", (base[0], base[1], 8.9), (0.12, 1.8, 0.12), yaw=math.atan2(T[i][1], T[i][0]))
        B.count("power_poles")
    for (p0, t0, n0), (p1, t1, n1) in zip(poles, poles[1:]):
        for off in (-0.75, 0.0, 0.75):
            a = np.array([*(p0 + n0 * off), 8.95])
            b = np.array([*(p1 + n1 * off), 8.95])
            cable(B, obj_of(p0), a, b, sag=0.7)
    for k, (p, t, n) in enumerate(poles):
        if bd.unit_hash("drop", k) < 0.8:
            a = np.array([*(p + n * 0.75), 8.9])
            b = np.array([*(p - n * (6.5 + 3 * bd.unit_hash("droplen", k)) + t * 3), 6.2])
            cable(B, obj_of(p), a, b, sag=0.4)

    # Kiosks and vendor umbrellas on the sidewalks.
    mid = (k1 + w1) / 2
    for d in np.arange(15, line.length - 5, 11):
        i = min(int(d / step), len(P) - 1)
        side = 1 if bd.unit_hash("side", int(d)) < 0.5 else -1
        roll = bd.unit_hash("furniture", int(d))
        yaw = math.atan2(T[i][1], T[i][0])
        c = P[i] + N[i] * side * mid
        obj = obj_of(c)
        if roll < 0.22:
            colour = ["M_Kiosk_Blue", "M_Kiosk_Yellow", "M_Kiosk_Red"][int(bd.unit_hash("kc", int(d)) * 3)]
            B.box(obj, colour, (c[0], c[1], SIDEWALK_Z + 1.25), (2.2, 1.9, 2.5), yaw=yaw)
            front = c - N[i] * side * 0.97
            B.box(obj, "M_ShopGlass_Warm", (front[0], front[1], SIDEWALK_Z + 1.3), (1.4, 0.05, 1.0), yaw=yaw)
            B.lights.append({"x": round(float(front[0] - N[i][0] * side * 1.0), 2), "y": 2.4,
                             "z": round(float(front[1] - N[i][1] * side * 1.0), 2), "kind": "kiosk"})
            B.count("kiosks")
        elif roll < 0.6:
            B.prism(obj, "M_SignFrame", tuple(c), 0.03, SIDEWALK_Z, SIDEWALK_Z + 2.3, sides=4, cap=False)
            umbrella(B, obj, c, SIDEWALK_Z + 2.3, 1.15, int(d))
            t = c + N[i] * side * 0.2
            B.box(obj, "M_Table", (t[0], t[1], SIDEWALK_Z + 0.4), (1.1, 0.6, 0.8), yaw=yaw)
            B.count("umbrellas")


def cable(B, obj, a, b, sag, segments=10, r=0.018):
    pts = []
    for k in range(segments + 1):
        u = k / segments
        p = a + (b - a) * u
        p[2] -= sag * 4 * u * (1 - u)
        pts.append(p)
    for p, q in zip(pts, pts[1:]):
        c = (p + q) / 2
        d = q - p
        horiz = math.hypot(d[0], d[1])
        yaw = math.atan2(d[1], d[0])
        # a short horizontal box is enough at this thickness; the sag comes from the segment heights
        B.box(obj, "M_Cable", (c[0], c[1], c[2]), (max(horiz, 0.05), r, r + abs(d[2])), yaw=yaw, top=True)
    B.count("cable_spans")


def umbrella(B, obj, c, z, radius, seed, sides=8):
    apex = (c[0], c[1], z + 0.45)
    pts = [(c[0] + radius * math.cos(2 * math.pi * k / sides), c[1] + radius * math.sin(2 * math.pi * k / sides)) for k in range(sides)]
    mats = ["M_Umbrella_A", "M_Umbrella_B", "M_Umbrella_C"]
    first = int(bd.unit_hash("um", seed) * 3)
    for k in range(sides):
        a, b = pts[k], pts[(k + 1) % sides]
        m = mats[(first + k) % 3] if k % 2 == 0 else mats[(first + 1) % 3]
        B.mesh.tri(obj, m, (*a, z), (*b, z), apex)
        B.mesh.tri(obj, m, (*b, z), (*a, z), apex)  # underside


def sign_atlas(path):
    W, H = 2048, 1024
    cw, ch = W // 4, H // 8
    img = Image.new("RGB", (W, H), (0, 0, 0))
    dr = ImageDraw.Draw(img)
    bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    cond = "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"
    for k, (title, sub) in enumerate(SIGNS):
        col, row = k % 4, k // 4
        x0, y0 = col * cw, row * ch
        bg, fg = SIGN_COLOURS[int(bd.unit_hash("signcol", k) * len(SIGN_COLOURS))]
        dr.rectangle([x0, y0, x0 + cw - 1, y0 + ch - 1], fill=bg)
        dr.rectangle([x0 + 6, y0 + 6, x0 + cw - 7, y0 + ch - 7], outline=fg, width=4)
        size = 64
        while size > 14:
            f = ImageFont.truetype(cond if len(title) > 16 else bold, size)
            if dr.textlength(title, font=f) < cw - 40:
                break
            size -= 2
        dr.text((x0 + cw / 2, y0 + ch * 0.40), title, font=f, fill=fg, anchor="mm")
        fs = ImageFont.truetype(cond, 22)
        dr.text((x0 + cw / 2, y0 + ch * 0.78), sub, font=fs, fill=fg, anchor="mm")
    # Weathering: soft noise and a little dirt toward the bottom of each board.
    rng = np.random.default_rng(7)
    arr = np.asarray(img).astype(np.float32)
    noise = rng.normal(0, 6, arr.shape[:2])[..., None]
    grad = np.tile(np.linspace(0, 1, ch), H // ch)[:, None, None] ** 3 * 28
    arr = np.clip(arr + noise - grad, 0, 255).astype(np.uint8)
    Image.fromarray(arr).filter(ImageFilter.GaussianBlur(0.6)).save(path)


ROOM_WALLS = [(222, 210, 180), (196, 214, 190), (190, 205, 222), (228, 196, 196), (236, 232, 220), (232, 214, 150)]
CURTAINS = [(150, 40, 40), (40, 70, 140), (200, 150, 40), (60, 120, 70), (120, 60, 120), (210, 120, 60), (230, 225, 210)]
GOODS = [(220, 40, 40), (240, 190, 30), (30, 110, 200), (40, 160, 80), (250, 250, 245), (240, 120, 30),
         (130, 40, 140), (20, 20, 20), (200, 60, 120), (90, 190, 210)]


def interiors_atlas(path):
    """Interiors seen through lit windows (cells 0-7) and lit shopfronts (8-15), 512 px each.

    Drawn bright enough to read as lit but with real dark areas, so the glass stops blowing out to white;
    the material tints them warm or cool. Pure invention: no brand, no text."""
    S, N = 512, 4
    img = Image.new("RGB", (S * N, S * N), (0, 0, 0))
    rng = np.random.default_rng(11)
    for cell in range(16):
        x0, y0 = (cell % N) * S, (cell // N) * S
        tile = Image.new("RGB", (S, S))
        dr = ImageDraw.Draw(tile)
        if cell < 8:
            wall = ROOM_WALLS[cell % len(ROOM_WALLS)]
            dr.rectangle([0, 0, S, S], fill=wall)
            dr.rectangle([0, int(S * 0.82), S, S], fill=tuple(int(c * 0.45) for c in wall))      # floor
            if cell % 3 == 0:   # a framed picture or a calendar on the back wall
                fx = int(rng.uniform(0.3, 0.6) * S)
                dr.rectangle([fx, int(S * 0.3), fx + 90, int(S * 0.45)], fill=(60, 50, 40))
                dr.rectangle([fx + 8, int(S * 0.3) + 8, fx + 82, int(S * 0.45) - 8], fill=tuple(int(v) for v in rng.integers(80, 200, 3)))
            if cell % 4 == 1:   # a television's blue glow low in the room
                dr.rectangle([int(S * 0.38), int(S * 0.55), int(S * 0.62), int(S * 0.72)], fill=(150, 190, 255))
                dr.rectangle([int(S * 0.45), int(S * 0.72), int(S * 0.55), int(S * 0.82)], fill=(30, 25, 25))
            if cell % 2 == 0:   # a ceiling fan
                cx = S // 2
                dr.line([cx, 0, cx, 50], fill=(40, 40, 40), width=6)
                dr.polygon([(cx - 150, 58), (cx + 150, 52), (cx + 150, 62), (cx - 150, 68)], fill=(50, 45, 40))
            # Curtains: one or two panels with folds, sometimes a sheer across the whole window.
            colour = CURTAINS[(cell * 3) % len(CURTAINS)]
            arr = np.asarray(tile).astype(np.float32)
            xs = np.arange(S)
            folds = 0.75 + 0.25 * np.sin(xs / S * math.pi * 18)
            left, right = int(S * rng.uniform(0.15, 0.4)), int(S * rng.uniform(0.6, 0.9))
            for x in list(range(0, left)) + list(range(right, S)):
                arr[:, x] = np.array(colour) * folds[x]
            if cell in (3, 6):
                arr = arr * 0.55 + np.array(CURTAINS[6]) * 0.45 * folds[None, :, None]
            # Light from a bulb near the ceiling, falling off to the corners.
            yy, xx = np.mgrid[0:S, 0:S] / S
            light = np.clip(1.15 - 1.3 * np.hypot(xx - 0.5, (yy - 0.12) * 1.2), 0.25, 1.0)
        else:
            wall = [(235, 235, 228), (200, 225, 240), (240, 220, 180), (215, 230, 205)][cell % 4]
            dr.rectangle([0, 0, S, S], fill=wall)
            kind = cell % 4
            shelf_rows = [0.18, 0.36, 0.54] if kind != 3 else []
            for y in shelf_rows:
                ys = int(y * S)
                dr.rectangle([12, ys + 70, S - 12, ys + 78], fill=(110, 85, 60))
                x = 18
                while x < S - 30:   # goods packed on the shelf
                    w = int(rng.integers(14, 42 if kind == 0 else 26))
                    h = int(rng.integers(30, 66))
                    col = GOODS[int(rng.integers(0, len(GOODS)))]
                    dr.rectangle([x, ys + 70 - h, x + w, ys + 69], fill=col)
                    dr.rectangle([x, ys + 70 - h, x + w, ys + 70 - h + 5], fill=tuple(min(255, c + 40) for c in col))
                    x += w + int(rng.integers(2, 7))
            if kind == 3:   # cloth and wax-print stalls: fabrics hanging in stripes
                x = 10
                while x < S - 10:
                    w = int(rng.integers(30, 60))
                    c1, c2 = GOODS[int(rng.integers(0, 10))], GOODS[int(rng.integers(0, 10))]
                    dr.rectangle([x, 40, x + w, int(S * 0.7)], fill=c1)
                    for yb in range(60, int(S * 0.7), 36):
                        dr.ellipse([x + 6, yb, x + w - 6, yb + 18], fill=c2)
                    x += w + 6
            # Counter across the front, a fluorescent tube on the ceiling.
            dr.rectangle([0, int(S * 0.74), S, S], fill=(70, 60, 55))
            dr.rectangle([0, int(S * 0.74), S, int(S * 0.76)], fill=(150, 140, 130))
            dr.rectangle([int(S * 0.2), 10, int(S * 0.8), 22], fill=(255, 255, 250))
            arr = np.asarray(tile).astype(np.float32)
            yy, xx = np.mgrid[0:S, 0:S] / S
            light = np.clip(1.1 - 0.9 * np.hypot((xx - 0.5) * 0.8, yy - 0.05), 0.3, 1.0)
        arr = arr * light[..., None] + rng.normal(0, 4, (S, S, 1))
        img.paste(Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2)), (x0, y0))
    img.save(path)


def build(args):
    area = bd.AREAS[args.name]
    to_utm = bd.Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True)
    oe, on = to_utm.transform(area["origin"][1], area["origin"][0])

    def local(geom):
        return shapely.transform(geom, lambda xy: np.column_stack(to_utm.transform(xy[:, 0], xy[:, 1])) - (oe, on))

    con = bd.duckdb.connect()
    oxford = [local(shapely.from_wkb(bytes(blob)))
              for _, cls, name, blob in bd.load(con, os.path.join(args.slice, "roads.parquet"), "id, class, name")
              if name == "Oxford Street"]
    merged = linemerge(oxford)
    line = max(list(merged.geoms) if hasattr(merged, "geoms") else [merged], key=lambda g: g.length)
    if Point(line.coords[-1]).distance(Point(0, 0)) < Point(line.coords[0]).distance(Point(0, 0)):
        line = LineString(list(line.coords)[::-1])
    d0 = line.project(Point(0, 0)) + 18.0   # start just south of the circle's ring
    street = substring(line, d0, d0 + ZONE_LENGTH)
    zone = street.buffer(ZONE_HALF_WIDTH, cap_style=2)

    def street_dir(p):
        d = street.project(Point(*p))
        a = np.array(street.interpolate(max(0, d - 2)).coords[0])
        b = np.array(street.interpolate(min(street.length, d + 2)).coords[0])
        v = b - a
        return v / (np.linalg.norm(v) + 1e-9)

    B = Builder()
    ids = []
    rng = np.random.default_rng(11)
    for bid, src, height, floors, blob in bd.load(con, os.path.join(args.slice, "buildings.parquet"), "id, source, height, num_floors"):
        g = local(shapely.from_wkb(bytes(blob)))
        if not zone.contains(g.centroid):
            continue
        g = g.buffer(0).simplify(0.3)
        polys = list(g.geoms) if isinstance(g, MultiPolygon) else [g]
        done = False
        for poly in polys:
            if not isinstance(poly, Polygon) or poly.area < 8:
                continue
            poly = orient(poly, 1.0)
            # Keep footprints off the new sidewalk.
            poly = poly.difference(street.buffer(ROAD_HALF + GUTTER + KERB + SIDEWALK, cap_style=2))
            if poly.is_empty or poly.area < 8:
                continue
            cands = [p for p in getattr(poly, "geoms", [poly]) if isinstance(p, Polygon)]
            if not cands:
                continue
            poly = max(cands, key=lambda p: p.area)
            poly = orient(poly, 1.0)
            r = bd.unit_hash(bid)
            n = int(floors) if floors else max(2, bd.storeys(poly.area, r, True))
            if poly.area < 40:
                n = 1
            facade(B, bid, poly, n * STOREY + 0.6, n, street, street_dir, rng)
            B.count("buildings")
            done = True
        if done:
            ids.append(bid)

    street_furniture(B, street, rng)

    os.makedirs(args.out, exist_ok=True)
    base = os.path.join(args.out, "osu-lookdev")
    with open(base + ".mtl", "w") as f:
        for name, (r, g, b) in MATERIALS.items():
            f.write(f"newmtl {name}\nKd {r:.3f} {g:.3f} {b:.3f}\nKs 0 0 0\nNs 10\nd 1\nillum 1\n\n")
    verts = B.mesh.write(base + ".obj", "osu-lookdev.mtl")
    tris, objs = B.mesh.stats()
    with open(base + "-ids.txt", "w") as f:
        f.write("\n".join(ids) + "\n")
    sign_atlas(os.path.join(args.out, "signs-atlas.png"))
    interiors_atlas(os.path.join(args.out, "interiors-atlas.png"))
    meta = {"zone_m": ZONE_LENGTH, "street_start": list(map(lambda v: round(v, 2), street.coords[0])),
            "triangles": tris, "vertices": verts, "objects": objs, "counts": B.counts, "lights": B.lights,
            "outline": [[round(x, 1), round(z, 1)] for x, z in zone.exterior.coords]}
    with open(base + ".json", "w") as f:
        json.dump(meta, f, indent=1)
    print(json.dumps({"triangles": tris, "lights": len(B.lights), **B.counts}))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slice", default="data/work/slice")
    ap.add_argument("--name", default="osu-poc", choices=sorted(bd.AREAS))
    ap.add_argument("--out", default="data/work/osu-poc")
    build(ap.parse_args())


if __name__ == "__main__":
    main()
