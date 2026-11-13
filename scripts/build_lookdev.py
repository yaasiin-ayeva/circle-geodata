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
    "M_Pavers": (0.58, 0.57, 0.55), "M_Sand": (0.70, 0.58, 0.44), "M_Hedge": (0.10, 0.22, 0.09),
    "M_Railing": (0.03, 0.03, 0.03), "M_Gate": (0.14, 0.14, 0.15), "M_WallCoping": (0.80, 0.79, 0.75),
    "M_Wall_White": (0.90, 0.89, 0.86), "M_Wall_Cream": (0.86, 0.80, 0.66), "M_Wall_Ochre": (0.78, 0.60, 0.38),
    "M_Wall_Grey": (0.66, 0.66, 0.64),
}
MATERIALS.update({
    # Step 2 palette, from research/look-reference.md: the main street is white, off-white, cream and light grey
    # with a dark fascia band; older houses off it are beige, ochre and cream under red roofs.
    "M_Facade_White": (0.90, 0.90, 0.87), "M_Facade_OffWhite": (0.86, 0.85, 0.80), "M_Facade_LightGrey": (0.74, 0.75, 0.74),
    "M_Facade_Beige": (0.82, 0.74, 0.58), "M_Facade_Orange": (0.90, 0.45, 0.16), "M_Fascia": (0.09, 0.09, 0.10),
    "M_Concrete_Raw": (0.58, 0.57, 0.54), "M_Rebar": (0.25, 0.15, 0.10), "M_BalconyGlass": (0.10, 0.13, 0.15),
    "M_RoofTile_Red": (0.55, 0.20, 0.13), "M_RoofSheet_Rust": (0.45, 0.25, 0.17), "M_Lattice": (0.86, 0.82, 0.70),
    "M_Hoarding": (0.15, 0.30, 0.55),
})
MATERIALS.update({
    # Step 3: billboards, pylon signs, cloth on the vendors' racks.
    "M_Billboard": (1, 1, 1), "M_BillboardBack": (0.30, 0.31, 0.32), "M_Steel": (0.40, 0.41, 0.42), "M_Floodlight": (0.9, 0.9, 0.9),
    "M_Cloth_1": (0.85, 0.20, 0.10), "M_Cloth_2": (0.95, 0.70, 0.05), "M_Cloth_3": (0.10, 0.35, 0.70),
    "M_Cloth_4": (0.10, 0.55, 0.25), "M_Cloth_5": (0.60, 0.10, 0.45), "M_Cloth_6": (0.95, 0.45, 0.10),
})
ADS = [   # invented advertisers only; names from the story bible where they exist (Zipp, Akuba Starr)
    ("SUNSET HEIGHTS", "SERVICED PLOTS - GATED CITY", (20, 70, 60), (240, 200, 90)),
    ("KOKO MALT", "THE TASTE OF HOME", (120, 30, 20), (255, 210, 120)),
    ("ZIPP", "YOUR RIDE IN 3 MINUTES", (15, 15, 20), (255, 200, 0)),
    ("CHRIST GLORY CHAPEL", "NIGHT OF MIRACLES - FRIDAY", (60, 20, 90), (255, 255, 255)),
    ("NKABOM BANK", "SAVE SMALL SMALL", (0, 90, 160), (255, 255, 255)),
    ("BLUE LAGOON WATER", "PURE. COLD. GHANAIAN.", (0, 120, 190), (255, 255, 255)),
    ("AKUBA STARR LIVE", "BLACK STAR SQUARE - DEC 31", (200, 30, 80), (255, 240, 220)),
    ("SHARP SHARP 5G", "BROWSE MORE, PAY LESS", (230, 90, 20), (255, 255, 255)),
]
MATERIALS.update({
    # Step 4: shade trees (trunk, branches, alpha-clipped leaf cards) and coconut palms (trunk, fronds).
    "M_Trunk": (0.30, 0.25, 0.20), "M_LeafCard": (0.20, 0.32, 0.14), "M_PalmTrunk": (0.45, 0.40, 0.33), "M_Frond": (0.30, 0.42, 0.16),
})
MATERIALS.update({
    # Step 6: the road surface. Step 5: bulbs at the stalls.
    "M_Patch": (0.16, 0.16, 0.16), "M_Manhole": (0.30, 0.30, 0.31), "M_Bulb": (1.0, 0.85, 0.6),
    "M_RoofSheet_Grey": (0.58, 0.58, 0.56),
})
MAIN_PALETTE = ["M_Facade_White"] * 3 + ["M_Facade_OffWhite"] * 2 + ["M_Facade_Cream", "M_Facade_LightGrey", "M_Facade_LightGrey"]
OLD_PALETTE = ["M_Facade_Beige", "M_Facade_Beige", "M_Facade_Cream", "M_Facade_Ochre", "M_Facade_OffWhite", "M_Facade_Terracotta"]
COMPOUND_WALLS = ["M_Wall_White", "M_Wall_Cream", "M_Wall_Ochre", "M_Wall_Grey"]
FORECOURT_MAX = 18.0    # deeper than this from the sidewalk and the frontage counts as a gap
FORECOURT_Z = 0.30
CAR_COLOURS = ["white"] * 6 + ["silver"] * 5 + ["grey"] * 3 + ["black"] * 4 + ["red"] * 2 + ["blue", "taxi", "taxi"]
FACADES = [m for m in MATERIALS if m.startswith("M_Facade_")]


# With --models, the trees, kiosks, container shops and bougainvillea that have a converted 3D model are not built
# as geometry: their places go to the JSON ("models") and the game puts the models there (circle-game
# art/models, the source models). A kind with no model yet keeps its procedural geometry.
MODEL_KINDS = set()
DEFAULT_MODEL_DIR = "../circle-game/art/models"


def has_model(kind):
    return kind in MODEL_KINDS


def model_kinds(arg):
    """The kinds named on the command line (comma list), or the converted models found in a folder (<kind>/<kind>.obj)."""
    if os.path.isdir(arg):
        return {k for k in os.listdir(arg) if os.path.exists(os.path.join(arg, k, k + ".obj"))}
    return {k.strip() for k in arg.split(",") if k.strip()}


def yaw_of(face):
    """Unity yaw in degrees for a facing direction (east, north): 0 faces north, 90 east."""
    return round(math.degrees(math.atan2(face[0], face[1])), 1)


class Builder:
    def __init__(self):
        self.mesh = bd.Mesh()
        self.lights = []
        self.counts = {}
        self.models = []

    def model(self, kind, p, y, yaw, scale=1.0):
        self.models.append({"kind": kind, "x": round(float(p[0]), 2), "y": round(float(y), 2), "z": round(float(p[1]), 2),
                            "yaw": round(float(yaw), 1), "scale": round(float(scale), 3)})

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


def building_type(bid, poly, street, s_along, side, n_storeys):
    """Pick a type for a footprint from its size and its place on the street (research/look-reference.md)."""
    d = poly.distance(street)
    main = d < 24
    if main and poly.area > 900 and side < 0 and s_along < 40:
        return "tower"                       # the white apartment tower by Danquah Circle (its footprint is not in the block yet)
    if poly.area < 40:
        return "shed"
    r = bd.unit_hash(bid, "type")
    if r < 0.12 and poly.area < 700:
        return "unfinished"
    if main and poly.area >= 100:
        return "commercial"
    return "house"


def facade(B, bid, poly, height, storeys, street, street_dir, rng, kind="commercial", wall=None):
    obj = bd.cell_name("LookDev_Buildings", poly.centroid.x, poly.centroid.y)
    wall = wall or FACADES[int(bd.unit_hash(bid, "paint") * len(FACADES))]
    fascia = kind in ("commercial", "tower")
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
                    if f == 0 and faces_street and kind != "house":
                        shopfront(B, obj, wall, bid, a, t, n, s0, s1, z0, z1, r, (i, k))
                    elif f > 0 and bw > 2.2:
                        window(B, obj, wall, a, t, n, s0, s1, z0, z1, r)
                    else:
                        wall_rect(B, obj, wall, a, t, n, s0, s1, z0, z1)
                wall_rect(B, obj, wall, a, t, n, s0, s1, storeys * STOREY, height)
            if faces_street and fascia:
                # The dark band over the ground floor that carries the shop signs.
                yaw = math.atan2(t[1], t[0])
                c = wall_point(a, t, n, L / 2, 0.11)
                B.box(obj, "M_Fascia", (c[0], c[1], 3.2), (L + 0.1, 0.22, 1.0), yaw=yaw, bottom=True)
                if storeys >= 2 and L >= 6 and bd.unit_hash(bid, i, "uplight") < 0.5:
                    # Cold white floodlights on the fascia washing the upper floors, as on the newer blocks at night.
                    for s in (L * 0.25, L * 0.75):
                        f = wall_point(a, t, n, s, 0.45)
                        B.box(obj, "M_Floodlight", (f[0], f[1], 3.75), (0.35, 0.2, 0.12), yaw=yaw)
                        aim = wall_point(a, t, n, s, 0.0)
                        B.lights.append({"x": round(float(f[0]), 2), "y": 3.8, "z": round(float(f[1]), 2), "kind": "uplight",
                                         "tx": round(float(aim[0]), 2), "ty": round(min(height, 3.8 + 5.0), 2), "tz": round(float(aim[1]), 2)})
                    B.count("facade_uplights", 2)
            if kind == "house" and faces_street and storeys >= 2 and L >= 5 and bd.unit_hash(bid, i, "balcony") < 0.7:
                lattice_balcony(B, obj, wall, a, t, n, L)
    if kind == "house" and hip_roof(B, bid, poly, storeys * STOREY):
        return
    # Parapet cap and roof
    rooftop(B, bid, poly, height)


def lattice_balcony(B, obj, wall, a, t, n, L):
    """A first-floor balcony with a concrete lattice parapet (breeze blocks with round openings), as on Osu's older houses."""
    w = min(L - 1.0, max(3.0, L * 0.6))
    s0, s1 = (L - w) / 2, (L + w) / 2
    yaw = math.atan2(t[1], t[0])
    c = wall_point(a, t, n, L / 2, 0.65)
    B.box(obj, wall, (c[0], c[1], STOREY - 0.08), (w, 1.3, 0.16), yaw=yaw, bottom=True)              # slab
    wall_rect(B, obj, "M_Lattice", a, t, n, s0, s1, STOREY, STOREY + 1.0, depth=1.3, uv_scale=1.0)   # lattice front
    for s in (s0, s1):                                                                                # returns
        p0, p1 = wall_point(a, t, n, s, 0), wall_point(a, t, n, s, 1.3)
        B.quad(obj, "M_Lattice", (*p0, STOREY), (*p1, STOREY), (*p1, STOREY + 1.0), (*p0, STOREY + 1.0))
        B.quad(obj, "M_Lattice", (*p1, STOREY), (*p0, STOREY), (*p0, STOREY + 1.0), (*p1, STOREY + 1.0))
    B.count("lattice_balconies")


def hip_roof(B, bid, poly, eave_z):
    """A hipped roof over the footprint's best-fit rectangle, 0.5 m overhang, red tile or rusty sheet."""
    rect = poly.minimum_rotated_rectangle
    if rect.area <= 0 or poly.area / rect.area < 0.78:
        return False
    c = list(rect.exterior.coords)[:4]
    e = [np.array(c[(k + 1) % 4]) - np.array(c[k]) for k in range(4)]
    k = 0 if np.linalg.norm(e[0]) >= np.linalg.norm(e[1]) else 1      # long side
    u = e[k] / np.linalg.norm(e[k]); v = e[(k + 1) % 4] / np.linalg.norm(e[(k + 1) % 4])
    Lu, Lv = np.linalg.norm(e[k]) + 1.0, np.linalg.norm(e[(k + 1) % 4]) + 1.0
    o = np.array(rect.centroid.coords[0])
    rise = min(2.2, 0.3 * Lv)
    corner = lambda su, sv: o + u * su * Lu / 2 + v * sv * Lv / 2
    r0, r1 = o - u * (Lu / 2 - Lv / 2), o + u * (Lu / 2 - Lv / 2)
    if Lu <= Lv:
        r0 = r1 = o
    # About 45% of roofs in the old city are grey asbestos-cement sheet (2010 census, research/landscape-look.md).
    rr = bd.unit_hash(bid, "roof")
    mat = "M_RoofSheet_Grey" if rr < 0.4 else ("M_RoofTile_Red" if rr < 0.75 else "M_RoofSheet_Rust")
    obj = bd.cell_name("LookDev_Roofs", o[0], o[1])
    z0, z1 = eave_z, eave_z + rise
    A, Bc, C, D = corner(-1, -1), corner(1, -1), corner(1, 1), corner(-1, 1)
    # Ensure counter-clockwise order seen from above.
    if (Bc[0] - A[0]) * (C[1] - A[1]) - (Bc[1] - A[1]) * (C[0] - A[0]) < 0:
        A, Bc, C, D = A, D, C, Bc
        r0, r1 = r1, r0 if False else r0
    ridge = sorted([r0, r1], key=lambda p: np.dot(p - o, (Bc - A)))
    q0, q1 = ridge
    B.quad(obj, mat, (*A, z0), (*Bc, z0), (*q1, z1), (*q0, z1))       # long slope
    B.quad(obj, mat, (*C, z0), (*D, z0), (*q0, z1), (*q1, z1))        # other long slope
    B.mesh.tri(obj, mat, (*Bc, z0), (*C, z0), (*q1, z1))              # hips
    B.mesh.tri(obj, mat, (*D, z0), (*A, z0), (*q0, z1))
    for a, b in ((A, Bc), (Bc, C), (C, D), (D, A)):                  # fascia board under the eaves
        B.quad(obj, "M_Fascia", (*b, z0 - 0.25), (*a, z0 - 0.25), (*a, z0), (*b, z0))
    B.count("hip_roofs")
    return True


def tower_balconies(B, bid, poly, storeys):
    """Continuous balcony slabs with rounded corners and dark glass balustrades on every floor above the ground."""
    obj = bd.cell_name("LookDev_Buildings", poly.centroid.x, poly.centroid.y)
    ring = poly.buffer(1.2, join_style=1, quad_segs=4)
    for f in range(1, storeys):
        z = f * STOREY
        slab = orient(ring.difference(poly.buffer(-0.05)), 1.0) if isinstance(ring.difference(poly.buffer(-0.05)), Polygon) else None
        if slab is None:
            continue
        rings = [slab.exterior] + list(slab.interiors)
        verts = np.array([p for r in rings for p in list(r.coords)[:-1]], dtype=np.float64)
        ends = np.cumsum([len(r.coords) - 1 for r in rings]).astype(np.uint32)
        idx = bd.earcut.triangulate_float64(verts, ends)
        for k in range(0, len(idx), 3):
            a, b, c = (tuple(verts[idx[k + j]]) for j in range(3))
            if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) < 0:
                b, c = c, b
            B.mesh.tri(obj, "M_Facade_White", (*a, z + 0.2), (*b, z + 0.2), (*c, z + 0.2))
            B.mesh.tri(obj, "M_Facade_White", (*a, z), (*c, z), (*b, z))
        pts = list(orient(ring, 1.0).exterior.coords)[:-1]
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            B.quad(obj, "M_Facade_White", (*a, z), (*b, z), (*b, z + 0.2), (*a, z + 0.2))
            B.quad(obj, "M_BalconyGlass", (*a, z + 0.2), (*b, z + 0.2), (*b, z + 1.15), (*a, z + 1.15))
            B.quad(obj, "M_BalconyGlass", (*b, z + 0.2), (*a, z + 0.2), (*a, z + 1.15), (*b, z + 1.15))
    B.count("tower_floors", storeys)


def unfinished_frame(B, bid, poly, storeys, street):
    """An unfinished concrete frame: columns, floor slabs, rebar stubs on top, a hoarding along the street."""
    obj = bd.cell_name("LookDev_Buildings", poly.centroid.x, poly.centroid.y)
    pts = list(poly.exterior.coords)[:-1]
    cols = []
    for i in range(len(pts)):
        a, b = np.array(pts[i]), np.array(pts[(i + 1) % len(pts)])
        L = float(np.linalg.norm(b - a))
        for k in range(max(1, int(L // 4))):
            cols.append(a + (b - a) * k / max(1, int(L // 4)))
    top = storeys * STOREY
    for c in cols:
        inner = np.array(poly.centroid.coords[0]) - c
        c = c + inner / (np.linalg.norm(inner) + 1e-9) * 0.2
        B.box(obj, "M_Concrete_Raw", (c[0], c[1], top / 2), (0.3, 0.3, top))
        for dx, dy in ((-0.08, -0.08), (0.08, 0.08), (-0.08, 0.08), (0.08, -0.08)):
            B.box(obj, "M_Rebar", (c[0] + dx, c[1] + dy, top + 0.35), (0.025, 0.025, 0.7), top=False)
    rings = [poly.exterior]
    verts = np.array(list(poly.exterior.coords)[:-1], dtype=np.float64)
    idx = bd.earcut.triangulate_float64(verts, np.array([len(verts)], dtype=np.uint32))
    for f in range(1, storeys + 1):
        z = f * STOREY
        for k in range(0, len(idx), 3):
            a, b, c = (tuple(verts[idx[k + j]]) for j in range(3))
            if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) < 0:
                b, c = c, b
            B.mesh.tri(obj, "M_Concrete_Raw", (*a, z), (*b, z), (*c, z))
            B.mesh.tri(obj, "M_Concrete_Raw", (*a, z - 0.2), (*c, z - 0.2), (*b, z - 0.2))
        for i in range(len(verts)):
            a, b = verts[i], verts[(i + 1) % len(verts)]
            B.quad(obj, "M_Concrete_Raw", (*a, z - 0.2), (*b, z - 0.2), (*b, z), (*a, z))
    # Hoarding on the street side.
    near = min(range(len(verts)), key=lambda i: Point(*(verts[i] + verts[(i + 1) % len(verts)]) / 2).distance(street))
    a, b = verts[near], verts[(near + 1) % len(verts)]
    L = float(np.linalg.norm(b - a))
    if L > 3 and poly.distance(street) < 26:
        t = (b - a) / L
        n = np.array([t[1], -t[0]])
        c = (a + b) / 2 + n * 1.5
        B.box(obj, "M_Hoarding", (c[0], c[1], 1.3), (L, 0.08, 2.6), yaw=math.atan2(t[1], t[0]))
    B.count("unfinished_frames")


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
    if r > 0.6:  # burglar-proof bars on some windows (on every window the commercial blocks read as housing)
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
    lit = r < 0.42   # most shops are shut at night; a few white-lit ones stand out in the dark
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


def side_street_mouths(street, roads, step=1.0):
    """Stations (metre marks) on each side of the street where another road joins: no sidewalk or forecourt there."""
    w1 = ROAD_HALF + GUTTER + KERB + SIDEWALK
    ds = np.arange(0, street.length, step)
    P = np.array([street.interpolate(d).coords[0] for d in ds])
    T = np.gradient(P, axis=0)
    T /= np.linalg.norm(T, axis=1)[:, None]
    N = np.column_stack([-T[:, 1], T[:, 0]])
    near = [(g, bd.ROAD_WIDTH.get(cls, 6.0) / 2 + 1.2) for cls, g in roads if g.distance(street) < w1 + 4]
    mouths = {1: set(), -1: set()}
    for side in (1, -1):
        for i, d in enumerate(ds):
            q = Point(*(P[i] + N[i] * side * (w1 + 1.0)))
            if any(g.distance(q) < half for g, half in near):
                mouths[side].add(int(d))
    return mouths


def frontage(B, street, footprints, mouths, step=1.0):
    """What stands between the sidewalk and the buildings: paver forecourts with nose-in parking, railings and hedges
    in front of the shallower ones, compound walls and sandy lots where there is no building, sand at side-street
    mouths. The footprints already sit at their real setbacks; this fills the ground in front of them."""
    w1 = ROAD_HALF + GUTTER + KERB + SIDEWALK
    ds = np.arange(0, street.length, step)
    P = np.array([street.interpolate(d).coords[0] for d in ds])
    T = np.gradient(P, axis=0)
    T /= np.linalg.norm(T, axis=1)[:, None]
    N = np.column_stack([-T[:, 1], T[:, 0]])
    built = shapely.union_all([f.buffer(0.05) for f in footprints])
    obj_of = lambda p: bd.cell_name("LookDev_Frontage", p[0], p[1])
    B.parked = getattr(B, "parked", [])

    for side in (1, -1):
        # Depth to the first building along a ray from the sidewalk edge, per station.
        depth = []
        for i in range(len(ds)):
            o = P[i] + N[i] * side * w1
            ray = LineString([o, o + N[i] * side * (FORECOURT_MAX + 6)])
            hit = ray.intersection(built)
            if int(ds[i]) in mouths[side] or hit.is_empty:
                depth.append(None)
                continue
            pts = np.array([c for g in getattr(hit, "geoms", [hit]) for c in g.coords])
            dmin = float(np.min(np.linalg.norm(pts - o, axis=1)))
            depth.append(dmin if dmin <= FORECOURT_MAX else None)

        def edge(i, off):
            return P[i] + N[i] * side * off

        def quad_ground(i, off0a, off1a, off0b, off1b, z, mat):
            a0, a1 = edge(i, off0a), edge(i + 1, off0b)
            b0, b1 = edge(i, off1a), edge(i + 1, off1b)
            uv = lambda p: (p[0] / 2.0, p[1] / 2.0)
            pts = [(*a0, z), (*a1, z), (*b1, z), (*b0, z)] if side > 0 else [(*a1, z), (*a0, z), (*b0, z), (*b1, z)]
            B.quad(obj_of(a0), mat, *pts, tuple(uv(p) for p in pts))

        # Runs of stations: forecourt (with a depth), gap (no building close enough), mouth.
        kinds = ["mouth" if int(d) in mouths[side] else ("court" if depth[i] is not None else "gap") for i, d in enumerate(ds)]
        runs, start = [], 0
        for i in range(1, len(ds) + 1):
            if i == len(ds) or kinds[i] != kinds[start]:
                runs.append((kinds[start], start, i - 1))
                start = i
        for kind, i0, i1 in runs:
            L = (i1 - i0) * step
            roll = bd.unit_hash("frontage", side, i0)
            if kind == "court":
                for i in range(i0, min(i1, len(ds) - 2) + 1):
                    quad_ground(i, w1, w1 + depth[i], w1, w1 + depth[min(i + 1, i1)], FORECOURT_Z, "M_Pavers")
                B.count("forecourt_m", int(L))
                mean = float(np.mean([depth[i] for i in range(i0, i1 + 1)]))
                if mean >= 6.5 and L >= 5:
                    # Nose-in parking, one bay every 2.8 m, about two bays in three taken.
                    for k, d in enumerate(np.arange(1.6, L - 1.2, 2.8)):
                        i = i0 + int(d / step)
                        if bd.unit_hash("park", side, i0, k) > 0.68 or depth[i] is None or depth[i] < 6.0:
                            continue
                        c = edge(i, w1 + depth[i] - 2.7)
                        face = N[i] * side
                        colour = CAR_COLOURS[int(bd.unit_hash("carcol", side, i0, k) * len(CAR_COLOURS))]
                        B.parked.append({"x": round(float(c[0]), 2), "z": round(float(c[1]), 2), "y": FORECOURT_Z,
                                         "yaw": round(math.degrees(math.atan2(face[0], face[1])), 1), "colour": colour})
                        B.count("parked_cars")
                elif L >= 4 and roll < 0.7:
                    railing(B, obj_of, edge, T, side, i0, i1, w1 + 0.15, FORECOURT_Z, hedge=roll < 0.45)
            elif kind == "gap" and L >= 2:
                if roll < 0.6:
                    compound_wall(B, obj_of, edge, T, side, i0, i1, w1 + 0.3, COMPOUND_WALLS[int(bd.unit_hash("wallcol", side, i0) * 4)])
                else:
                    for i in range(i0, min(i1, len(ds) - 2) + 1):
                        quad_ground(i, w1, w1 + 14, w1, w1 + 14, 0.03, "M_Sand")
                    B.count("sandy_lot_m", int(L))
                    if has_model("container_shop") and L >= 7:
                        # A container shop on the sand, its open side to the street.
                        im = (i0 + min(i1, len(ds) - 2)) // 2
                        B.model("container_shop", edge(im, w1 + 3.0), 0.05, yaw_of(-N[im] * side))
                        B.count("container_shops")
            elif kind == "mouth":
                # Sand where the side street's asphalt edge breaks up, on both flanks of the mouth.
                for i in list(range(max(0, i0 - 3), i0)) + list(range(i1 + 1, min(len(ds) - 2, i1 + 3) + 1)):
                    quad_ground(i, ROAD_HALF, w1 + 2.0, ROAD_HALF, w1 + 2.0, 0.045, "M_Sand")
                B.count("side_street_mouths")


def railing(B, obj_of, edge, T, side, i0, i1, off, z, hedge):
    """Black steel railing along a forecourt edge, bars every 0.15 m, a low hedge behind it; a 4 m opening every 12 m."""
    for i in range(i0, i1):
        if (i - i0) % 12 >= 8:
            continue
        a, b = edge(i, off), edge(i + 1, off)
        yaw = math.atan2(T[i][1], T[i][0])
        mid = (a + b) / 2
        obj = obj_of(a)
        B.box(obj, "M_Railing", (mid[0], mid[1], z + 1.05), (1.0, 0.04, 0.04), yaw=yaw)
        B.box(obj, "M_Railing", (mid[0], mid[1], z + 0.12), (1.0, 0.04, 0.04), yaw=yaw, top=False)
        for k in range(7):
            p = a + (b - a) * (k + 0.5) / 7
            B.box(obj, "M_Railing", (p[0], p[1], z + 0.58), (0.02, 0.02, 0.92), yaw=yaw, top=False)
        if hedge:
            h = edge(i, off + side * 0) + (edge(i + 1, off) - edge(i, off)) * 0.5
            n = edge(i, off + 0.45) - edge(i, off)
            c = h + n
            B.box(obj, "M_Hedge", (c[0], c[1], z + 0.35), (1.0, 0.55, 0.7), yaw=yaw)
    B.count("railing_m", i1 - i0)


def compound_wall(B, obj_of, edge, T, side, i0, i1, off, mat):
    """A 2.2 m painted block wall with pillars every 3 m, a coping and, on longer runs, a steel gate in the middle."""
    L = i1 - i0
    gate = (i0 + L // 2 - 2, i0 + L // 2 + 2) if L >= 9 else None
    for i in range(i0, i1):
        a, b = edge(i, off), edge(i + 1, off)
        yaw = math.atan2(T[i][1], T[i][0])
        mid = (a + b) / 2
        obj = obj_of(a)
        if gate and gate[0] <= i < gate[1]:
            B.box(obj, "M_Gate", (mid[0], mid[1], 1.05), (1.0, 0.06, 2.1), yaw=yaw)
            continue
        B.box(obj, mat, (mid[0], mid[1], 1.1), (1.0, 0.2, 2.2), yaw=yaw, top=False)
        B.box(obj, "M_WallCoping", (mid[0], mid[1], 2.25), (1.04, 0.28, 0.1), yaw=yaw)
        if (i - i0) % 3 == 0:
            B.box(obj, mat, (a[0], a[1], 1.2), (0.36, 0.36, 2.4), yaw=yaw)
        if has_model("bougainvillea") and (i - i0) % 7 == 3 and bd.unit_hash("bougain", side, i) < 0.6:
            # Bougainvillea planted inside the wall, spilling over the coping.
            B.model("bougainvillea", edge(i, off + 1.1), 0.0, 360 * bd.unit_hash("bgy", side, i), 0.9 + 0.3 * bd.unit_hash("bgs", side, i))
            B.count("bougainvilleas")
    B.count("compound_wall_m", L)


def street_furniture(B, line, rng, mouths=None):
    """Sidewalks, painted kerbs, open gutters with slab crossings, power poles and cables, kiosks, umbrellas."""
    mouths = mouths or {1: set(), -1: set()}
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
            if int(ds[i]) in mouths[side]:
                continue
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
        while int(d) in mouths[-1] and d < line.length - 1:
            d += 1.0
        i = min(int(d / step), len(P) - 1)
        base = P[i] + N[i] * (-(k1 + 0.6))
        poles.append((base, T[i], N[i]))
        obj = obj_of(base)
        B.prism(obj, "M_PowerPole", tuple(base), 0.14, 0, 9.5, sides=6)
        B.box(obj, "M_PowerPole", (base[0], base[1], 8.9), (0.12, 1.8, 0.12), yaw=math.atan2(T[i][1], T[i][0]))
        B.count("power_poles")
    for k, ((p0, t0, n0), (p1, t1, n1)) in enumerate(zip(poles, poles[1:])):
        for j, off in enumerate((-0.75, -0.35, 0.0, 0.4, 0.75)):
            z = 8.95 if j % 2 == 0 else 8.55
            a = np.array([*(p0 + n0 * off), z])
            b = np.array([*(p1 + n1 * off), z])
            cable(B, obj_of(p0), a, b, sag=0.6 + 0.35 * bd.unit_hash("sag", k, j))
        if k % 2 == 0:
            # A sagging bundle across the street to the shops on the east side.
            a = np.array([*(p0 + n0 * 0.5), 8.4])
            b = np.array([*(p0 + n0 * (2 * k1 + 1.2) + t0 * 4), 6.0])
            for j in range(3):
                cable(B, obj_of(p0), a + np.array([0, 0, -0.15 * j]), b + np.array([0, 0, -0.1 * j]), sag=0.9 + 0.2 * j)
    for k, (p, t, n) in enumerate(poles):
        if bd.unit_hash("drop", k) < 0.8:
            a = np.array([*(p + n * 0.75), 8.9])
            b = np.array([*(p - n * (6.5 + 3 * bd.unit_hash("droplen", k)) + t * 3), 6.2])
            cable(B, obj_of(p), a, b, sag=0.4)

    # Kiosks and vendor umbrellas on the sidewalks.
    mid = (k1 + w1) / 2
    for d in np.arange(12, line.length - 5, 8):
        i = min(int(d / step), len(P) - 1)
        side = 1 if bd.unit_hash("side", int(d)) < 0.5 else -1
        if any(int(d) + k in mouths[side] for k in (-2, -1, 0, 1, 2)):
            continue
        roll = bd.unit_hash("furniture", int(d))
        yaw = math.atan2(T[i][1], T[i][0])
        c = P[i] + N[i] * side * mid
        obj = obj_of(c)
        if roll < 0.22:
            front = c - N[i] * side * 0.97
            kind = "kiosk_wood" if bd.unit_hash("kk", int(d)) < 0.6 else "momo_booth"
            if not has_model(kind):
                kind = "momo_booth" if kind == "kiosk_wood" else "kiosk_wood"
            if has_model(kind):
                B.model(kind, c, SIDEWALK_Z, yaw_of(-N[i] * side))
            else:
                colour = ["M_Kiosk_Blue", "M_Kiosk_Yellow", "M_Kiosk_Red"][int(bd.unit_hash("kc", int(d)) * 3)]
                B.box(obj, colour, (c[0], c[1], SIDEWALK_Z + 1.25), (2.2, 1.9, 2.5), yaw=yaw)
                B.box(obj, "M_ShopGlass_Warm", (front[0], front[1], SIDEWALK_Z + 1.3), (1.4, 0.05, 1.0), yaw=yaw)
            B.lights.append({"x": round(float(front[0] - N[i][0] * side * 1.0), 2), "y": 2.4,
                             "z": round(float(front[1] - N[i][1] * side * 1.0), 2), "kind": "kiosk"})
            B.count("kiosks")
        elif roll < 0.42:
            fabric_rack(B, obj, c, T[i], N[i] * side, int(d))
        elif roll < 0.78:
            B.prism(obj, "M_SignFrame", tuple(c), 0.03, SIDEWALK_Z, SIDEWALK_Z + 2.3, sides=4, cap=False)
            umbrella(B, obj, c, SIDEWALK_Z + 2.3, 1.15, int(d))
            if bd.unit_hash("bulb", int(d)) < 0.55:
                # A bare bulb hanging under the umbrella: the warm point of light at a night stall.
                B.box(obj, "M_Bulb", (c[0], c[1], SIDEWALK_Z + 2.05), (0.09, 0.09, 0.12), yaw=yaw)
                B.lights.append({"x": round(float(c[0]), 2), "y": round(SIDEWALK_Z + 2.0, 2), "z": round(float(c[1]), 2), "kind": "bulb"})
                B.count("stall_bulbs")
            t = c + N[i] * side * 0.2
            B.box(obj, "M_Table", (t[0], t[1], SIDEWALK_Z + 0.4), (1.1, 0.6, 0.8), yaw=yaw)
            B.count("umbrellas")


def cable(B, obj, a, b, sag, segments=14, r=0.016):
    """A sagging cable as one continuous square tube along a parabola. (Boxes stepped down the sag read as
    black ribbons in daylight.)"""
    pts = []
    for k in range(segments + 1):
        u = k / segments
        p = a + (b - a) * u
        p[2] -= sag * 4 * u * (1 - u)
        pts.append(p)
    d = b - a
    h = math.hypot(d[0], d[1]) or 1.0
    side = np.array([-d[1] / h, d[0] / h, 0.0]) * r      # horizontal, left of the span
    up = np.array([0.0, 0.0, r])
    corners = [side + up, -side + up, -side - up, side - up]
    rings = [[tuple(p + c) for c in corners] for p in pts]
    for r0, r1 in zip(rings, rings[1:]):
        for i in range(4):
            j = (i + 1) % 4
            B.quad(obj, "M_Cable", r0[i], r0[j], r1[j], r1[i])
    B.count("cable_spans")


def fabric_rack(B, obj, c, t, n, seed):
    """A vendor's rack of wax-print cloth and dresses: two posts, a bar, hanging lengths of colour."""
    yaw = math.atan2(t[1], t[0])
    z0 = SIDEWALK_Z
    for sgn in (-1, 1):
        p = c + t * sgn * 1.0
        B.box(obj, "M_Steel", (p[0], p[1], z0 + 1.0), (0.04, 0.04, 2.0), yaw=yaw, top=False)
    B.box(obj, "M_Steel", (c[0], c[1], z0 + 2.0), (2.1, 0.04, 0.04), yaw=yaw)
    k = 0
    for u in np.linspace(-0.85, 0.85, 7):
        p = c + t * u + n * 0.03 * (k % 2)
        mat = f"M_Cloth_{1 + int(bd.unit_hash('cloth', seed, k) * 6)}"
        L = 0.9 + 0.6 * bd.unit_hash("clothlen", seed, k)
        B.box(obj, mat, (p[0], p[1], z0 + 2.0 - L / 2), (0.24, 0.02, L), yaw=yaw)
        k += 1
    B.count("fabric_racks")


def billboards(B, street, mouths, footprints):
    """Big panels on steel posts, several in the first 100 m from Danquah Circle and a few further on, facing the
    traffic, lit by floodlights on the top edge; and two tall pylon signs. Ads come from billboards-atlas.png."""
    w1 = ROAD_HALF + GUTTER + KERB + SIDEWALK
    plan = [(10, 1), (34, -1), (58, 1), (82, -1), (128, 1), (176, -1)]
    built = shapely.union_all([f.buffer(0.5) for f in footprints])
    for k, (d, side) in enumerate(plan):
        while any(int(d) + j in mouths[side] for j in range(-4, 5)) and d < street.length - 5:
            d += 3
        p = np.array(street.interpolate(d).coords[0])
        q = np.array(street.interpolate(min(d + 1, street.length)).coords[0])
        t = (q - p) / np.linalg.norm(q - p)
        nrm = np.array([-t[1], t[0]]) * side
        base = p + nrm * (w1 + 1.6)
        if built.contains(Point(*base)):
            continue
        # Face the traffic coming up the street, turned 20 degrees toward the road.
        facing = -t * (1 if k % 2 == 0 else -1)
        ang = math.atan2(facing[1], facing[0]) - side * math.radians(20) * (1 if k % 2 == 0 else -1)
        f = np.array([math.cos(ang), math.sin(ang)])          # panel normal
        u = np.array([f[1], -f[0]])                           # along the panel
        W, H, z0 = (6.0, 3.0, 5.0) if k % 3 else (4.0, 6.0, 3.2)   # landscape, or a tall portrait one
        obj = bd.cell_name("LookDev_Billboards", base[0], base[1])
        posts = [base] if W < 5 else [base - u * 1.6, base + u * 1.6]
        for pp in posts:
            B.box(obj, "M_Steel", (pp[0], pp[1], z0 / 2), (0.35, 0.35, z0), yaw=ang)
        yaw = math.atan2(u[1], u[0])
        B.box(obj, "M_BillboardBack", (base[0] - f[0] * 0.15, base[1] - f[1] * 0.15, z0 + H / 2), (W + 0.2, 0.25, H + 0.2), yaw=yaw)
        cell = k % len(ADS)
        col, row = cell % 2, cell // 2
        u0, u1, v0, v1 = col / 2 + 0.002, (col + 1) / 2 - 0.002, 1 - (row + 1) / 4 + 0.002, 1 - row / 4 - 0.002
        if H > W:   # portrait panel: the left half of the cell, rotated content is not needed, just cropped
            u1 = u0 + (u1 - u0) * 0.5
        a0 = base + f * 0.01 - u * W / 2
        a1 = base + f * 0.01 + u * W / 2
        B.quad(obj, "M_Billboard", (*a1, z0), (*a0, z0), (*a0, z0 + H), (*a1, z0 + H), ((u0, v0), (u1, v0), (u1, v1), (u0, v1)))
        if W > H:
            # Most roadside panels are back to back: another ad on the far side, for the traffic going the other way.
            cell2 = (k + 3) % len(ADS)
            c2, r2 = cell2 % 2, cell2 // 2
            uv2 = ((c2 / 2 + 0.002, 1 - (r2 + 1) / 4 + 0.002), ((c2 + 1) / 2 - 0.002, 1 - (r2 + 1) / 4 + 0.002),
                   ((c2 + 1) / 2 - 0.002, 1 - r2 / 4 - 0.002), (c2 / 2 + 0.002, 1 - r2 / 4 - 0.002))
            b0 = base - f * 0.31 - u * W / 2
            b1 = base - f * 0.31 + u * W / 2
            B.quad(obj, "M_Billboard", (*b0, z0), (*b1, z0), (*b1, z0 + H), (*b0, z0 + H), uv2)
        # Floodlights: arms over the top edge, a spot light each, aimed down at the panel.
        for j in range(2 if W < 5 else 3):
            x = -W / 2 + W * (j + 0.5) / (2 if W < 5 else 3)
            arm = base + u * x
            head = arm + f * 0.9
            B.box(obj, "M_Steel", ((arm[0] + head[0]) / 2, (arm[1] + head[1]) / 2, z0 + H + 0.25), (0.05, 0.9, 0.05), yaw=yaw)
            B.box(obj, "M_Floodlight", (head[0], head[1], z0 + H + 0.2), (0.35, 0.2, 0.15), yaw=yaw)
            aim = arm + f * 0.1
            B.lights.append({"x": round(float(head[0]), 2), "y": round(z0 + H + 0.1, 2), "z": round(float(head[1]), 2),
                             "kind": "billboard", "tx": round(float(aim[0]), 2), "ty": round(z0 + H * 0.35, 2), "tz": round(float(aim[1]), 2)})
        B.count("billboards")
    # Pylon signs for a bank and a fuel station, by the side-street mouths on the west side.
    for k, d in enumerate([150.0, 192.0]):
        side = -1
        p = np.array(street.interpolate(d).coords[0])
        q = np.array(street.interpolate(min(d + 1, street.length)).coords[0])
        t = (q - p) / np.linalg.norm(q - p)
        nrm = np.array([-t[1], t[0]]) * side
        base = p + nrm * (w1 + 0.9)
        if built.contains(Point(*base)):
            continue
        obj = bd.cell_name("LookDev_Billboards", base[0], base[1])
        yaw = math.atan2(nrm[1], nrm[0])
        B.box(obj, "M_Steel", (base[0], base[1], 4.0), (0.3, 0.3, 8.0), yaw=yaw)
        B.box(obj, "M_Fascia", (base[0], base[1], 6.0), (0.5, 1.8, 4.0), yaw=yaw)
        cell = 4 if k == 0 else 5
        col, row = cell % 2, cell // 2
        u0, u1, v0, v1 = col / 2 + 0.002, col / 2 + 0.25, 1 - (row + 1) / 4 + 0.002, 1 - row / 4 - 0.002
        for sgn in (1, -1):
            fa = base + t * sgn * 0.26
            l0 = fa + nrm * -0.85
            l1 = fa + nrm * 0.85
            pts = [(*l0, 4.3), (*l1, 4.3), (*l1, 7.7), (*l0, 7.7)] if sgn > 0 else [(*l1, 4.3), (*l0, 4.3), (*l0, 7.7), (*l1, 7.7)]
            B.quad(obj, "M_Billboard", *pts, ((u0, v0), (u1, v0), (u1, v1), (u0, v1)))
        B.count("pylon_signs")


def road_surface(B, street):
    """Darker asphalt patches and manhole covers on Oxford Street, a few millimetres above the road ribbon."""
    z = bd.ROAD_LIFT["secondary"] + 0.006
    for k, d in enumerate(np.arange(4, street.length - 4, 7)):
        r = bd.unit_hash("patch", k)
        p = np.array(street.interpolate(d).coords[0])
        q = np.array(street.interpolate(min(d + 1, street.length)).coords[0])
        t = (q - p) / (np.linalg.norm(q - p) + 1e-9)
        n = np.array([-t[1], t[0]])
        obj = bd.cell_name("LookDev_Street", p[0], p[1])
        if r < 0.45:
            c = p + n * (ROAD_HALF * (2 * bd.unit_hash("px", k) - 1) * 0.8)
            L, W = 1.2 + 3 * bd.unit_hash("pl", k), 0.8 + 1.6 * bd.unit_hash("pw", k)
            a = math.atan2(t[1], t[0]) + (bd.unit_hash("pa", k) - 0.5) * 0.4
            u, v = np.array([math.cos(a), math.sin(a)]), np.array([-math.sin(a), math.cos(a)])
            pts = [c - u * L / 2 - v * W / 2, c + u * L / 2 - v * W / 2, c + u * L / 2 + v * W / 2, c - u * L / 2 + v * W / 2]
            B.quad(obj, "M_Patch", *[(*x, z) for x in pts], tuple((x[0] / 3, x[1] / 3) for x in pts))
            B.count("road_patches")
        elif r < 0.6:
            c = p + n * (ROAD_HALF * 0.45 * (1 if k % 2 else -1))
            ring = [(c[0] + 0.35 * math.cos(2 * math.pi * j / 10), c[1] + 0.35 * math.sin(2 * math.pi * j / 10)) for j in range(10)]
            for j in range(1, 9):
                B.mesh.tri(obj, "M_Manhole", (*ring[0], z + 0.004), (*ring[j], z + 0.004), (*ring[j + 1], z + 0.004))
            B.count("manholes")


def shade_tree(B, obj, base, seed):
    """A large shade tree: a trunk, a few branches, and a crown of crossed leaf cards (alpha-clipped in the engine)."""
    h = 3.2 + 1.6 * bd.unit_hash("th", seed)
    crown_r = 3.0 + 1.8 * bd.unit_hash("tr", seed)
    B.prism(obj, "M_Trunk", tuple(base), 0.3, 0, h, sides=7, cap=False)
    top = np.array([base[0], base[1], h])
    for j in range(4):
        a = 2 * math.pi * (j / 4 + bd.unit_hash("tb", seed, j) * 0.2)
        d = np.array([math.cos(a), math.sin(a)])
        end = top + np.array([*(d * crown_r * 0.55), 1.4])
        mid = (top + end) / 2
        B.box(obj, "M_Trunk", tuple(mid), (float(np.linalg.norm(end[:2] - top[:2])), 0.16, 1.4), yaw=a, top=False)
    centre = top + np.array([0, 0, crown_r * 0.55])
    n_cards = 26
    for j in range(n_cards):
        r = [bd.unit_hash("tc", seed, j, q) for q in range(5)]
        # A point in a flattened ellipsoid crown, more cards near the rim so the silhouette is ragged.
        th, ph, rad = 2 * math.pi * r[0], math.acos(2 * r[1] - 1), 0.55 + 0.45 * r[2] ** 0.5
        c = centre + np.array([crown_r * rad * math.sin(ph) * math.cos(th), crown_r * rad * math.sin(ph) * math.sin(th),
                               crown_r * 0.55 * rad * math.cos(ph)])
        size = (2.0 + 1.4 * r[3]) / 2
        yaw = 2 * math.pi * r[4]
        pitch = (r[3] - 0.5) * 1.6 if j % 4 else math.pi / 2 - 0.25   # every fourth card lies almost flat, a roof of leaves
        u = np.array([math.cos(yaw), math.sin(yaw), 0])
        w = np.array([-math.sin(yaw), math.cos(yaw), 0])
        v = w * math.sin(pitch) + np.array([0, 0, 1]) * math.cos(pitch)
        q = [c - u * size - v * size, c + u * size - v * size, c + u * size + v * size, c - u * size + v * size]
        B.quad(obj, "M_LeafCard", *map(tuple, q), ((0, 0), (1, 0), (1, 1), (0, 1)))
    B.count("shade_trees")


def coconut_palm(B, obj, base, seed):
    """A coconut palm: a slender leaning trunk in segments and a crown of drooping fronds (alpha-clipped strips)."""
    H = 8.0 + 5.0 * bd.unit_hash("ph", seed)
    lean_a = 2 * math.pi * bd.unit_hash("pl", seed)
    lean = np.array([math.cos(lean_a), math.sin(lean_a)]) * (0.6 + 1.2 * bd.unit_hash("pm", seed))
    segs = 7
    pts = [np.array([*(np.array(base) + lean * (k / segs) ** 2), H * k / segs]) for k in range(segs + 1)]
    for k in range(segs):
        a, b = pts[k], pts[k + 1]
        mid = (a + b) / 2
        d = b - a
        B.box(obj, "M_PalmTrunk", tuple(mid), (0.3 - 0.012 * k, 0.3 - 0.012 * k, float(np.linalg.norm(d)) + 0.05),
              yaw=lean_a, top=k == segs - 1)
    top = pts[-1]
    for j in range(11):
        a = 2 * math.pi * (j / 11 + 0.05 * bd.unit_hash("pf", seed, j))
        d = np.array([math.cos(a), math.sin(a), 0.0])
        side = np.array([-math.sin(a), math.cos(a), 0.0])
        L = 3.6 + 1.0 * bd.unit_hash("pfl", seed, j)
        up = 0.9 + 0.8 * bd.unit_hash("pfu", seed, j)
        prev = None
        for k in range(5):
            t = k / 4
            centre = top + d * L * t + np.array([0, 0, up * t - (up + 2.2) * t * t])
            half = 0.55 * (1 - 0.7 * t) + 0.05
            pair = (centre - side * half, centre + side * half)
            if prev is not None:
                (p0, p1), (q0, q1) = prev, pair
                v0, v1 = (k - 1) / 4, k / 4
                B.quad(obj, "M_Frond", tuple(p0), tuple(p1), tuple(q1), tuple(q0), ((0, v0), (1, v0), (1, v1), (0, v1)))
            prev = pair
    B.count("palms")


def trees(B, street, mouths, footprints, zone):
    """Shade trees and coconut palms along the frontage and behind it (research/look-reference.md: big dark canopies
    and palms, especially at the north end). Kept off buildings, side-street mouths and the road."""
    w1 = ROAD_HALF + GUTTER + KERB + SIDEWALK
    built = shapely.union_all([f.buffer(1.5) for f in footprints])
    taken = []
    def free(p, gap=5.0):
        return not built.contains(Point(*p)) and all(np.linalg.norm(p - q) > gap for q in taken)
    for side in (1, -1):
        for d in np.arange(5, street.length - 3, 13):
            d = d + 4 * (bd.unit_hash("td", side, int(d)) - 0.5)
            if any(int(d) + j in mouths[side] for j in range(-5, 6)):
                continue
            p = np.array(street.interpolate(d).coords[0])
            q = np.array(street.interpolate(min(d + 1, street.length)).coords[0])
            t = (q - p) / (np.linalg.norm(q - p) + 1e-9)
            base = p + np.array([-t[1], t[0]]) * side * (w1 + 1.0)
            r = bd.unit_hash("tk", side, int(d))
            if not free(base) or r > 0.75:
                continue
            obj = bd.cell_name("LookDev_Trees", base[0], base[1])
            palm = r < (0.45 if d < 70 else 0.2)
            kind = "palm" if palm else ("almond_tree" if bd.unit_hash("sp", side, int(d)) < 0.4 else "shade_tree")
            if kind == "almond_tree" and not has_model(kind):
                kind = "shade_tree"
            if has_model(kind):
                B.model(kind, base, 0.0, 360 * bd.unit_hash("ty", side, int(d)), 0.85 + 0.3 * bd.unit_hash("ts", side, int(d)))
                B.count("palms" if palm else "shade_trees")
            elif palm:
                coconut_palm(B, obj, base, (side, int(d)))
            else:
                shade_tree(B, obj, base, (side, int(d)))
            taken.append(base)
    # Behind the frontage: big trees in yards and lots, which carry the skyline's dark masses.
    minx, miny, maxx, maxy = zone.bounds
    k = 0
    for j in range(120):
        p = np.array([minx + (maxx - minx) * bd.unit_hash("bx", j), miny + (maxy - miny) * bd.unit_hash("by", j)])
        if not zone.contains(Point(*p)) or street.distance(Point(*p)) < w1 + 10 or not free(p, 9.0):
            continue
        obj = bd.cell_name("LookDev_Trees", p[0], p[1])
        roll = bd.unit_hash("bt", j)
        kind = "palm" if roll < 0.3 else "banana" if roll < 0.42 else "almond_tree" if roll < 0.62 else "shade_tree"
        if kind in ("banana", "almond_tree") and not has_model(kind):
            kind = "shade_tree"
        if has_model(kind):
            B.model(kind, p, 0.0, 360 * bd.unit_hash("by2", j), 0.8 + 0.4 * bd.unit_hash("bs", j))
            B.count({"palm": "palms", "shade_tree": "shade_trees", "almond_tree": "shade_trees", "banana": "banana_clumps"}[kind])
        else:
            (coconut_palm if bd.unit_hash("bt", j) < 0.3 else shade_tree)(B, obj, p, ("b", j))
        taken.append(p)
        k += 1
        if k >= 14:
            break


def billboard_atlas(path):
    """2 x 4 cells of 1024 x 512: big type on flat colour with a simple graphic, the way roadside ads read at night."""
    W, H = 2048, 2048
    cw, ch = W // 2, H // 4
    img = Image.new("RGB", (W, H), (0, 0, 0))
    bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    cond = "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"
    for k, (title, sub, bg, fg) in enumerate(ADS):
        x0, y0 = (k % 2) * cw, (k // 2) * ch
        tile = Image.new("RGB", (cw, ch), bg)          # drawn on its own tile so shapes cannot spill into a neighbour
        td = ImageDraw.Draw(tile)
        light = tuple(min(255, int(c * 1.5 + 40)) for c in bg)
        if k % 2:
            td.ellipse([cw * 0.58, -ch * 0.2, cw * 1.15, ch * 0.9], fill=light)
        else:
            td.polygon([(cw * 0.55, ch), (cw * 0.8, 0), (cw, 0), (cw, ch)], fill=light)
        size = 120
        while size > 30:
            f = ImageFont.truetype(bold, size)
            if td.textlength(title, font=f) < cw * 0.9:
                break
            size -= 4
        td.text((40, ch * 0.34), title, font=f, fill=fg, anchor="lm")
        td.text((44, ch * 0.66), sub, font=ImageFont.truetype(cond, 46), fill=fg, anchor="lm")
        td.rectangle([0, ch - 26, cw - 1, ch - 1], fill=fg)
        img.paste(tile, (x0, y0))
    img.save(path)


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
    all_roads = [(cls, name, local(shapely.from_wkb(bytes(blob))))
                 for _, cls, name, blob in bd.load(con, os.path.join(args.slice, "roads.parquet"), "id, class, name")]
    oxford = [g for cls, name, g in all_roads if name == "Oxford Street"]
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
    footprints = []
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
            s_along = street.project(poly.centroid)
            a0 = np.array(street.interpolate(s_along).coords[0])
            tv = np.array(street.interpolate(min(s_along + 1, street.length)).coords[0]) - a0
            cv = np.array(poly.centroid.coords[0]) - a0
            side = 1 if tv[0] * cv[1] - tv[1] * cv[0] > 0 else -1          # +1 east (left of a southbound street)
            kind = building_type(bid, poly, street, s_along, side, n)
            if os.environ.get("LOOKDEV_DEBUG"):
                print(f"{bid[:8]} s={s_along:.0f} side={side} dx_east={cv[0]:.1f} t=({tv[0]:.2f},{tv[1]:.2f}) d={poly.distance(street):.1f} area={poly.area:.0f} n={n} -> {kind}")
            if kind == "tower":
                n = 10
            elif kind == "house":
                n = min(n, 2)
            elif kind == "commercial":
                n = min(max(n, 2), 4)
            elif kind == "unfinished":
                n = 2 + int(bd.unit_hash(bid, "floors") * 3)
            palette = OLD_PALETTE if kind in ("house", "shed") else MAIN_PALETTE
            wall = palette[int(bd.unit_hash(bid, "paint") * len(palette))]
            if kind == "tower":
                wall = "M_Facade_White"
            if kind == "commercial" and side > 0 and 90 < s_along < 130 and not B.counts.get("orange"):
                wall = "M_Facade_Orange"                     # the one bright orange building, as on the real street
                B.count("orange")
            footprints.append(poly)
            B.count("type_" + kind)
            if kind == "unfinished":
                unfinished_frame(B, bid, poly, n, street)
                B.count("buildings")
                done = True
                continue
            facade(B, bid, poly, n * STOREY + 0.6, n, street, street_dir, rng, kind, wall)
            if kind == "tower":
                tower_balconies(B, bid, poly, n)
            B.count("buildings")
            done = True
        if done:
            ids.append(bid)

    side_roads = [(cls, g) for cls, name, g in all_roads
                  if name != "Oxford Street" and cls not in ("footway", "path", "pedestrian", "steps", "cycleway")]
    mouths = side_street_mouths(street, side_roads)
    frontage(B, street, footprints, mouths)
    street_furniture(B, street, rng, mouths)
    billboards(B, street, mouths, footprints)
    trees(B, street, mouths, footprints, zone)
    road_surface(B, street)

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
    billboard_atlas(os.path.join(args.out, "billboards-atlas.png"))
    meta = {"zone_m": ZONE_LENGTH, "street_start": list(map(lambda v: round(v, 2), street.coords[0])),
            "triangles": tris, "vertices": verts, "objects": objs, "counts": B.counts, "lights": B.lights,
            "parked": getattr(B, "parked", []), "models": B.models,
            "outline": [[round(x, 1), round(z, 1)] for x, z in zone.exterior.coords]}
    with open(base + ".json", "w") as f:
        json.dump(meta, f, indent=1)
    print(json.dumps({"triangles": tris, "lights": len(B.lights), **B.counts}))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slice", default="data/work/slice")
    ap.add_argument("--name", default="osu-poc", choices=sorted(bd.AREAS))
    ap.add_argument("--out", default="data/work/osu-poc")
    ap.add_argument("--models", nargs="?", const=DEFAULT_MODEL_DIR, default="",
                    help="leave trees, kiosks, container shops and bougainvillea to generated 3D models (listed under "
                         "'models' in the JSON): a folder of converted models (default " + DEFAULT_MODEL_DIR + ") "
                         "or a comma list of kinds; kinds without a model stay procedural")
    args = ap.parse_args()
    MODEL_KINDS.update(model_kinds(args.models) if args.models else set())
    if args.models:
        print("models for:", ", ".join(sorted(MODEL_KINDS & {"palm", "shade_tree", "almond_tree", "banana", "bougainvillea",
                                                              "kiosk_wood", "momo_booth", "container_shop"})) or "none")
    build(args)


if __name__ == "__main__":
    main()
