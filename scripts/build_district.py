"""Build a drivable district from an Overture export: buildings, roads, streetlights and a benchmark lap.

    python3 scripts/build_district.py --slice data/work/slice --out data/work/osu-poc

Writes one shared asset set for the Unity and Unreal proof of concept:

  osu-poc.obj, osu-poc.mtl  meshes in metres, chunked by 250 m cells. OBJ axes are right-handed, Y up:
                            X = west, Y = up, Z = north. Unity's OBJ import flips X, so in Unity
                            x = east, y = up, z = north. Unreal's importer converts to its Z-up frame.
  osu-poc.json              origin, bounds, streetlights, the benchmark lap (flat [x0, z0, x1, z1, ...])
                            and the spawn point, in Unity axes (x east, y up, z north), metres from the
                            district origin; "parked" cars along the streets where the district's rules ask
                            for them (DISTRICT_RULES "parking", not Osu).
  osu-poc-preview.png       top-down check: roads, buildings by height, lights, lap.

An area is a box (osu-poc, jamestown) or a corridor along named roads less the boxes of its neighbours (ring-road,
--name ring-road): see AREAS and area_outline(). A corridor lays its ground per 250 m cell, and its lap is a route
through given points (route_lap()) rather than a loop round a block.

Heights come from OSM tags where they exist, otherwise from the typology rules in storeys(); they are
placeholders until the Open Buildings 2.5D heights are merged (pipeline step 3). Each footprint is then built from
the modules in massing.py (hipped roofs, bulkheads, set-backs, frame storeys, compound walls); --no-massing keeps
plain extruded boxes. Needs shapely, pyproj,
mapbox_earcut, networkx and Pillow (requirements-district.txt).
"""
import argparse
import hashlib
from collections import Counter
import json
import math
import os
import sys

import duckdb
import mapbox_earcut as earcut
import networkx as nx
import numpy as np
import shapely
from PIL import Image, ImageDraw
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon, box
from shapely.geometry.polygon import orient

import massing

WORLD_ORIGIN_UTM = (808523.0, 616356.0)  # Kwame Nkrumah Interchange ("Circle"), see README

JAMESTOWN_ORIGIN = (5.5395, -0.2080)          # High Street by Ussher Fort, where the lap starts
JAMESTOWN_LAP = ("High Street", 5.5335)          # set from the slice's road names
OSU_ORIGIN = (5.5669, -0.1809)                   # Danquah Circle

# The vertical slice's box (aoi/slices.geojson "vertical-slice"): the Osu slice's buildings stop at its west edge.
VERTICAL_SLICE = {"south": 5.542, "north": 5.575, "west": -0.1945, "east": -0.137}
# The Ring Road through the vertical slice, by the names Overture gives its segments: from the slice's west edge on
# Ring Road Central (2.1 km short of Kwame Nkrumah Circle) through the Ako Adjei Interchange, Ring Road East and
# Danquah Circle, then Ring Road East to its end at La. Both carriageways are mapped, each one way along its geometry.
RING_ROADS = ("Ring Road Central", "Ring Road East", "Danquah Circle", "Ako Adjei Interchange")
# Roads mapped one way per carriageway, driven along their geometry: OpenStreetMap tags Ring Road East and Danquah
# Circle oneway=yes (circle-reference street-survey.md), and the slice has each carriageway of the Ring Road as its own
# segment, the two running opposite ways, westbound on the north side as Ghana drives on the right. Overture's one-way
# rules are not in the slice, so the names stand in for them.
ONE_WAY_ROADS = RING_ROADS
# The Ring Road lap (route_lap): points the route passes in order, each on one carriageway, at a vertex of its segment
# (lat, lon). From the westbound carriageway leaving Danquah Circle, west to the slice's edge, back east through the
# circle, on to La and back to the circle. The route between them follows the carriageways' directions.
RING_LAP = {"route": [(5.567666, -0.182117),     # westbound, leaving Danquah Circle
                      (5.573174, -0.192172),     # westbound, Ring Road Central past the Ako Adjei Interchange
                      (5.573673, -0.193304),     # eastbound, after turning at the west end
                      (5.564333, -0.176351),     # eastbound, Ring Road East past Danquah Circle
                      (5.560141, -0.169233),     # eastbound, before La
                      (5.556986, -0.167259),     # westbound, after turning at La
                      (5.566147, -0.179392)],    # westbound, before Danquah Circle
            "one_way": ONE_WAY_ROADS}

AREAS = {
    # About 1.3 km2 of Osu: Danquah Circle, the north half of Oxford Street, Ring Road East.
    "osu-poc": {"south": 5.5575, "north": 5.5695, "west": -0.1870, "east": -0.1780,
                "origin": OSU_ORIGIN, "lap": ("Oxford Street", 5.5600), "district": "osu"},
    # Jamestown, Bukom and Ussher Town (aoi/slices.geojson "jamestown"; slice in data/work/slice-jamestown).
    "jamestown": {"south": 5.5300, "north": 5.5425, "west": -0.2175, "east": -0.2055,
                  "origin": JAMESTOWN_ORIGIN, "lap": JAMESTOWN_LAP, "district": "jamestown"},
    # The Ring Road beyond Osu: a corridor 250 m either side of RING_ROADS inside the vertical slice, less osu-poc's
    # box, so the two areas meet without overlapping (slice in data/work/slice). It shares Osu's origin, so the game
    # loads both in one frame; its lap runs through osu-poc, on the roads of the whole corridor.
    "ring-road": {"corridor": {"roads": RING_ROADS, "half_width_m": 250.0, "within": VERTICAL_SLICE},
                  "minus": ["osu-poc"], "origin": OSU_ORIGIN, "lap": RING_LAP, "district": "ring-road"},
}

# What changes from one district to the next (circle-reference district cards, catalogue/*-identity-survey.md).
#   yards: houses stand in walled compounds with gates (Osu); in the old town buildings stand on the street line.
#   paints: the facade paints, (material, weight); None keeps the even mix of WALLS.
#   landmarks: hand-built pieces, by name, at (lat, lon).
#   storeys, storeys_from_m2: footprints of that area or more without a height draw their storeys from this
#   (storeys, weight) list instead of the typology rules, where footprint size does not predict height.
#   signs: real shop signs at their places, (name, lat, lon, bearing of the facade); the game draws the names.
#   parking: cars parked parallel to the road edge, for districts without a dressed look-dev block (place_parked()):
#   the road classes parked on and the chance a place is taken ("classes", "take"), named streets with their own
#   chance whatever their class ("streets"), how far from the lap ("lap_m"), one place every "step_m" on each side,
#   the car's centre "inset_m" inside the road edge on roads narrower than "narrow_m" and "wide_inset_m" on wider
#   ones, the clearances to junctions, buildings, the spawn and other cars, and the paints as (colour, weight).
#   Osu has none: its parked cars come from build_lookdev.py.
DISTRICT_RULES = {
    "osu": {"yards": True, "paints": None, "landmarks": []},
    # Jamestown: no compound walls; houses painted yellow, ochre, green and pale grey; the lighthouse.
    "jamestown": {"yards": False,
                  "paints": [("M_Wall_Ochre", 0.30), ("M_Wall_Mint", 0.20), ("M_Wall_Grey", 0.20), ("M_Wall_Concrete", 0.20),
                             ("M_Wall_PaleBlue", 0.10)],
                  "landmarks": [("lighthouse", 5.5331, -0.2122)],
                  # Storeys of footprints of 150 m2 or more that carry no mapped floors, drawn whatever their size:
                  # the large footprints along the lap are often a row of one-storey shops or several roofs merged
                  # into one (7 checked in Street View: 1, 1, 2, 2, 3, 4 and about 12 storeys), where the typology
                  # rules gave them 3 to 6 storeys and a flat roof. The draw is the lap count of 82 buildings (44% one
                  # storey, 40% two, 9% three, 7% four or more; circle-reference obs-jamestown-storeys-*). Footprints
                  # with OpenStreetMap levels keep them: 4 of 4 tagged one storey were one storey in Street View.
                  "storeys": [(1, 0.44), (2, 0.40), (3, 0.09), (4, 0.04), (5, 0.03)], "storeys_from_m2": 150,
                  # The shop signs read in full along the lap (circle-reference texts.csv txt-jamestown-lap-001..003),
                  # by name: the Street View point they were read from and the bearing of their facade from it.
                  # About 1 shop bay in 10 carries a sign here: the game hangs these and no others.
                  "signs": [("TWM SPORTS EQUIPMENT STORE", 5.539694, -0.207763, 175),
                            ("MAKOLA PRICES", 5.537146, -0.210161, 138),
                            ("SHIP HOUZZ", 5.538331, -0.208946, 131)],
                  # Parked cars along the streets (circle-reference jamestown-identity-survey.md: side streets about
                  # 6 m of asphalt with no kerb and no sidewalk, kiosks and tables on sandy edges; High Street wider,
                  # two lanes each way, with kerbs; vehicles.md: cars park on sandy verges). Within 250 m of the lap, a
                  # place every 6.5 m on each side, taken about one time in four on the side streets and one in three
                  # on High Street, which is classed primary and parked on by name: estimates, not counted. On roads
                  # under 7 m the car's centre is 1.0 m inside the asphalt edge, its outer side at the sand; 1.1 m on
                  # wider roads. No place within 12 m of a junction, 1.5 m of a building, 6 m of the spawn or 5.5 m of
                  # another car. Paints in the order vehicles.md gives for private cars (dark grey and black first,
                  # then silver, white, red, blue), and a taxi waiting on about 12% of the places: weights estimated.
                  "parking": {"classes": ["residential", "unclassified", "tertiary", "secondary"], "take": 0.25,
                              "streets": {"High Street": 0.35}, "lap_m": 250.0, "step_m": 6.5,
                              "inset_m": 1.0, "wide_inset_m": 1.1, "narrow_m": 7.0,
                              "junction_m": 12.0, "building_m": 1.5, "spawn_m": 6.0, "gap_m": 5.5,
                              "colours": [("grey", 0.20), ("black", 0.20), ("silver", 0.18), ("white", 0.16),
                                          ("red", 0.08), ("blue", 0.06), ("taxi", 0.12)]}},
    # The Ring Road corridor: Osu's building rules (walled yards off the main roads, Osu's roof mix and paints). The
    # street survey describes Ring Road East's frontage as 1-2 storeys behind walls with about 20% shopfronts, which
    # the yards already give; it counted no storeys there, so the typology rules stay. It has no dressed block, so
    # cars park along its side streets (street survey: "on sandy verges and concrete aprons in the residential
    # streets"), never on the Ring Road itself, a primary road with no parking lane. The chance a place is taken
    # (about one in five) is an estimate, not a count; the clearances and paints are Jamestown's.
    "ring-road": {"yards": True, "paints": None, "landmarks": [],
                  "parking": {"classes": ["residential", "unclassified", "tertiary"], "take": 0.2,
                              "streets": {}, "lap_m": None, "step_m": 6.5,
                              "inset_m": 1.0, "wide_inset_m": 1.1, "narrow_m": 7.0,
                              "junction_m": 12.0, "building_m": 1.5, "spawn_m": 6.0, "gap_m": 5.5,
                              "colours": [("grey", 0.20), ("black", 0.20), ("silver", 0.18), ("white", 0.16),
                                          ("red", 0.08), ("blue", 0.06), ("taxi", 0.12)]}},
}

ROAD_WIDTH = {"motorway": 22.0, "trunk": 18.0, "primary": 13.0, "secondary": 10.0, "tertiary": 8.5,
              "residential": 6.0, "unclassified": 6.0, "living_street": 5.0, "service": 4.5}
# The Ring Road's cross-section (circle-reference catalogue/street-survey.md, Ring Road East at 5.5679,-0.1828):
# 2 + 2 lanes, about 7 m per carriageway, either side of a grass median of about 8-10 m with twin-arm LED poles in it;
# Danquah Circle's ring about 9-10 m wide around its island. The class widths drew each carriageway 13 m wide, which
# covered the median and stood the poles on the asphalt (the user's test drive of 14 Nov 2026). Each carriageway is
# mapped as its own one-way road, so the width is the carriageway's. Ring Road Central and the Ako Adjei Interchange's
# ramps are not surveyed; they take Ring Road East's 7 m.
DUAL_CARRIAGEWAYS = ("Ring Road Central", "Ring Road East", "Ako Adjei Interchange")
CARRIAGEWAY_M = 7.0
NAMED_WIDTH = {"Danquah Circle": 9.5}


def road_width(cls, name="", widths=None):
    """The carriageway's width (m): by class, except the Ring Road's surveyed main carriageways and Danquah Circle."""
    widths = widths or ROAD_WIDTH
    if cls in MAJOR_CLASSES:
        if name in DUAL_CARRIAGEWAYS:
            return CARRIAGEWAY_M
        if name in NAMED_WIDTH:
            return NAMED_WIDTH[name]
    return widths[cls]


ROAD_LIFT = {"motorway": 0.09, "trunk": 0.08, "primary": 0.07, "secondary": 0.06, "tertiary": 0.05,
             "residential": 0.04, "unclassified": 0.04, "living_street": 0.04, "service": 0.035}
MAJOR = {"motorway", "trunk", "primary", "secondary", "tertiary"}
# The classes a surveyed width overrides: the Ring Road's slip roads (tertiary) keep theirs.
MAJOR_CLASSES = ("motorway", "trunk", "primary")
STOREY_M = 3.2
CELL_M = 250.0

MATERIALS = {
    "M_Ground": (0.20, 0.17, 0.14),
    "M_Asphalt": (0.11, 0.115, 0.125),
    "M_AsphaltMajor": (0.13, 0.135, 0.145),
    "M_RoadPaint": (0.85, 0.82, 0.74),
    "M_Roof": (0.36, 0.37, 0.38),
    "M_Wall_Concrete": (0.85, 0.83, 0.78),
    "M_Wall_Ochre": (0.79, 0.64, 0.42),
    "M_Wall_PaleBlue": (0.66, 0.75, 0.81),
    "M_Wall_Terracotta": (0.71, 0.40, 0.29),
    "M_Wall_Grey": (0.56, 0.56, 0.54),
    "M_Wall_Mint": (0.62, 0.76, 0.66),
    "M_Lighthouse_White": (0.90, 0.90, 0.88),
    "M_Lighthouse_Red": (0.62, 0.13, 0.10),
}
WALLS = [m for m in MATERIALS if m.startswith("M_Wall_")]


def unit_hash(*parts):
    """Deterministic number in [0, 1) from any values, so reruns give the same city."""
    h = hashlib.sha1("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2 ** 64


def storeys(area_m2, rnd, on_oxford):
    """Typology rules for footprints without a height: kiosks, houses, compounds, blocks."""
    if area_m2 < 40:
        n = 1
    elif area_m2 < 150:
        n = 1 + (rnd > 0.55)
    elif area_m2 < 500:
        n = 2 + (rnd > 0.6)
    elif area_m2 < 1500:
        n = 2 + int(rnd * 3)
    else:
        n = 3 + int(rnd * 4)
    if on_oxford:
        n = max(n, 2) + (rnd > 0.7)
    return n


class Mesh:
    """Triangles grouped by (object, material), written as OBJ with flat normals and UVs."""

    def __init__(self):
        self.groups = {}

    def tri(self, obj, mat, a, b, c, uv=None):
        """a, b, c are (east, north, up) and counter-clockwise seen from the side the face points to."""
        self.groups.setdefault((obj, mat), []).append((a, b, c, uv))

    def quad(self, obj, mat, a, b, c, d, uv=None):
        uva, uvb = (None, None) if uv is None else ((uv[0], uv[1], uv[2]), (uv[0], uv[2], uv[3]))
        self.tri(obj, mat, a, b, c, uva)
        self.tri(obj, mat, a, c, d, uvb)

    def stats(self):
        tris = sum(len(v) for v in self.groups.values())
        objs = len({k[0] for k in self.groups})
        return tris, objs

    def write(self, path, mtl_name):
        def to_obj(p):  # (east, north, up) -> OBJ (west, up, north)
            return (-p[0], p[2], p[1])

        lines = [f"# CHALE district, {len(self.groups)} groups", f"mtllib {mtl_name}"]
        v = vt = vn = 0
        for obj in sorted({k[0] for k in self.groups}):
            lines.append(f"o {obj}")
            for (o, mat), tris in sorted(self.groups.items()):
                if o != obj:
                    continue
                lines.append(f"usemtl {mat}")
                for a, b, c, uv in tris:
                    n = np.cross(np.subtract(b, a), np.subtract(c, a))
                    ln = np.linalg.norm(n)
                    if ln < 1e-9:
                        continue
                    n = to_obj(n / ln)
                    for p in (a, b, c):
                        x, y, z = to_obj(p)
                        lines.append(f"v {x:.3f} {y:.3f} {z:.3f}")
                    uvs = uv or ((a[0] / 4, a[1] / 4), (b[0] / 4, b[1] / 4), (c[0] / 4, c[1] / 4))
                    for u, w in uvs:
                        lines.append(f"vt {u:.3f} {w:.3f}")
                    lines.append(f"vn {n[0]:.4f} {n[1]:.4f} {n[2]:.4f}")
                    vn += 1
                    lines.append(f"f {v + 1}/{vt + 1}/{vn} {v + 2}/{vt + 2}/{vn} {v + 3}/{vt + 3}/{vn}")
                    v += 3
                    vt += 3
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        return v


def cell_name(prefix, e, n):
    return f"{prefix}_{int(math.floor(e / CELL_M))}_{int(math.floor(n / CELL_M))}"


def load(con, path, cols):
    return con.execute(f"SELECT {cols}, geometry::BLOB FROM '{path}'").fetchall()


def lighthouse(mesh, obj, x, y):
    """Jamestown Lighthouse (1930s): a 28 m tower, white with a broad red band, a gallery and a red-capped lantern
    (circle-reference jamestown-identity-survey.md). An octagonal prism tapering from 2.6 to 2.0 m in radius."""
    sides = 8
    def ring(r, z):
        return [(x + r * math.cos(2 * math.pi * k / sides + math.pi / 8), y + r * math.sin(2 * math.pi * k / sides + math.pi / 8), z)
                for k in range(sides)]
    def band(mat, r0, z0, r1, z1):
        a, b = ring(r0, z0), ring(r1, z1)
        for k in range(sides):
            k2 = (k + 1) % sides
            mesh.quad(obj, mat, a[k], a[k2], b[k2], b[k])
    def disc(mat, r, z, up=True):
        pts = ring(r, z)
        for k in range(1, sides - 1):
            mesh.tri(obj, mat, *((pts[0], pts[k], pts[k + 1]) if up else (pts[0], pts[k + 1], pts[k])))
    taper = lambda z: 2.6 - 0.6 * z / 25.0
    for mat, z0, z1 in (("M_Lighthouse_White", 0.0, 13.0), ("M_Lighthouse_Red", 13.0, 19.0), ("M_Lighthouse_White", 19.0, 25.0)):
        band(mat, taper(z0), z0, taper(z1), z1)
    disc("M_Lighthouse_Red", 2.9, 25.0, up=False)                      # gallery floor, seen from below
    band("M_Lighthouse_Red", 2.9, 25.0, 2.9, 25.25)
    disc("M_Lighthouse_White", 2.9, 25.25)
    band("M_Lighthouse_Red", 2.9, 25.25, 2.9, 26.2)                    # gallery railing as a solid band
    band("M_Lamp_LED", 1.3, 25.25, 1.3, 27.0)                          # lantern glass, lit at night
    band("M_Lighthouse_Red", 1.4, 27.0, 0.2, 28.4)                     # red cap
    disc("M_Lighthouse_White", 2.6, 0.0, up=False)


def area_box(area):
    """An area's box in WGS84 (lon, lat)."""
    return box(area["west"], area["south"], area["east"], area["north"])


def area_outline(area, road_rows, local):
    """Where an area's content goes, in metres from its origin, and the wider region its lap may use.

    A box area (south, north, west, east) is its box, the lap region the same. A corridor area follows named roads:
    the union of every segment named in "corridor"["roads"], buffered by "half_width_m" and kept inside the box
    "within"; that is the lap region. The content then leaves out the boxes of the areas named in "minus", so two
    areas meet along a shared edge. road_rows: (id, class, name, WGS84 geometry) of the slice. Returns
    (clip, region, minus): minus is the list of the left-out areas' boxes in WGS84."""
    if "corridor" not in area:
        clip = local(area_box(area))
        return clip, clip, []
    spec = area["corridor"]
    lines = [local(g) for _, _, name, g in road_rows if name in spec["roads"]]
    if not lines:
        sys.exit(f"no road named {', '.join(spec['roads'])} in the slice")
    region = shapely.union_all(lines).buffer(spec["half_width_m"], quad_segs=4).intersection(local(area_box(spec["within"])))
    region = shapely.make_valid(region.simplify(1.0))
    minus = [area_box(AREAS[m]) for m in area.get("minus", [])]
    clip = region
    for m in minus:
        clip = clip.difference(local(m))
    return clip, region, minus


def ground_region(clip, minus, local, margin):
    """The ground a corridor area lays: its clip widened by the margin, less the ground the left-out areas lay
    themselves (their box's bounds in metres widened by the same margin, as build() grids them), so ground never
    overlaps."""
    g = clip.buffer(margin, join_style="mitre")
    for m in minus:
        x0, y0, x1, y1 = local(m).bounds
        g = g.difference(box(x0 - margin, y0 - margin, x1 + margin, y1 + margin))
    return g


def lay_ground(mesh, region):
    """Ground triangles over a region, cut on the 250 m cell grid into objects Ground_<i>_<j> so the GLB export
    can tile them with the buildings and roads of the same cell."""
    x0, y0, x1, y1 = region.bounds
    for i in range(int(math.floor(x0 / CELL_M)), int(math.floor(x1 / CELL_M)) + 1):
        for j in range(int(math.floor(y0 / CELL_M)), int(math.floor(y1 / CELL_M)) + 1):
            piece = region.intersection(box(i * CELL_M, j * CELL_M, (i + 1) * CELL_M, (j + 1) * CELL_M))
            obj = f"Ground_{i}_{j}"
            for poly in getattr(piece, "geoms", [piece]):
                if not isinstance(poly, Polygon) or poly.area < 1:
                    continue
                poly = orient(poly, 1.0)
                rings = [poly.exterior] + list(poly.interiors)
                verts = np.array([p for r in rings for p in list(r.coords)[:-1]], dtype=np.float64)
                ends = np.cumsum([len(r.coords) - 1 for r in rings]).astype(np.uint32)
                idx = earcut.triangulate_float64(verts, ends)
                for k in range(0, len(idx), 3):
                    a, b, c = (tuple(verts[idx[k + n]]) for n in range(3))
                    if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) < 0:
                        b, c = c, b
                    mesh.tri(obj, "M_Ground", (*a, 0), (*b, 0), (*c, 0),
                             uv=((a[0] / 8, a[1] / 8), (b[0] / 8, b[1] / 8), (c[0] / 8, c[1] / 8)))


def build(args):
    area = AREAS[args.name]
    rules = DISTRICT_RULES.get(area.get("district", "osu"), DISTRICT_RULES["osu"])
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True)
    to_ll = Transformer.from_crs("EPSG:32630", "EPSG:4326", always_xy=True)
    oe, on = to_utm.transform(area["origin"][1], area["origin"][0])

    def local(geom):
        return shapely.transform(geom, lambda xy: np.column_stack(to_utm.transform(xy[:, 0], xy[:, 1])) - (oe, on))

    def lonlat(geom):
        return shapely.transform(geom, lambda xy: np.column_stack(to_ll.transform(xy[:, 0] + oe, xy[:, 1] + on)))

    con = duckdb.connect()
    road_rows = [(rid, cls, name or "", shapely.from_wkb(bytes(blob)))
                 for rid, cls, name, blob in load(con, os.path.join(args.slice, "roads.parquet"), "id, class, name")]
    clip, region, minus = area_outline(area, road_rows, local)
    # Buildings belong to the area whose box (or corridor, less the boxes it leaves out) holds their centroid.
    lonlat_box = area_box(area) if "corridor" not in area else lonlat(region)
    shapely.prepare(lonlat_box)
    minx, miny, maxx, maxy = clip.bounds
    mesh = Mesh()

    # Roads first: buildings near Oxford Street get an extra storey, so its line is needed. A corridor keeps the
    # roads of its whole region, inside the areas it leaves out too, for its lap; everything drawn is cut to clip.
    roads = []
    for rid, cls, name, g in road_rows:
        if cls not in ROAD_WIDTH:
            continue
        if not g.intersects(lonlat_box):
            continue
        roads.append((rid, cls, name, local(g)))
    oxford = shapely.union_all([g for _, _, n, g in roads if n == "Oxford Street"]) if roads else None

    landmark_points = [shapely.geometry.Point(*np.array(to_utm.transform(lon, lat)) - (oe, on))
                       for _, lat, lon in rules["landmarks"]]

    # Buildings, minus those a detailed look-dev version replaces.
    exclude = set()
    if args.exclude and os.path.exists(args.exclude):
        exclude = {l.strip() for l in open(args.exclude) if l.strip()}
    nb = 0
    heights = []
    # Building modules (massing.py): who stands in a walled compound, and what keeps the walls out.
    yards, keep_out = [], []
    counts = Counter()
    for bid, src, height, floors, blob in load(con, os.path.join(args.slice, "buildings.parquet"), "id, source, height, num_floors"):
        g = shapely.from_wkb(bytes(blob))
        c = g.centroid
        if not lonlat_box.contains(c) or any(m.contains(c) for m in minus):
            continue
        g = local(g).buffer(0).simplify(0.25)
        if any(g.contains(p) for p in landmark_points):
            continue                                    # the landmark is built by hand below
        polys = list(g.geoms) if isinstance(g, MultiPolygon) else [g]
        if bid in exclude:
            keep_out += [p for p in polys if isinstance(p, Polygon)]
            continue
        for poly in polys:
            if not isinstance(poly, Polygon) or poly.area < 8:
                continue
            poly = orient(poly, 1.0)
            rnd = unit_hash(bid)
            near = oxford is not None and poly.distance(oxford) < 35
            if height:
                h = float(height)
                n = None
            else:
                if floors:
                    n = int(floors)
                elif rules.get("storeys") and poly.area >= rules["storeys_from_m2"]:
                    n = massing.pick(rules["storeys"], unit_hash(bid, "storeys"))
                else:
                    n = storeys(poly.area, rnd, near)
                h = n * STOREY_M
            parapet = 0.4 + rnd * 0.6
            h += parapet
            heights.append(h)
            c = poly.centroid
            obj = cell_name("Buildings", c.x, c.y)
            wall = (massing.pick(rules["paints"], unit_hash(bid, "paint")) if rules["paints"]
                    else WALLS[int(unit_hash(bid, "paint") * len(WALLS))])
            if not args.no_massing and n is not None:
                # Houses and small blocks off Oxford Street stand in walled yards.
                yard = rules["yards"] and not near and n <= 3 and poly.area < 600
                massing.building(mesh, obj, wall, bid, poly, n, parapet, near, yard, counts, area.get("district", "osu"))
                if yard:
                    yards.append((bid, poly))
                else:
                    keep_out.append(poly)
                nb += 1
                continue
            keep_out.append(poly)
            rings = [poly.exterior] + list(poly.interiors)
            for ring in rings:
                pts = list(ring.coords)[:-1]
                for i in range(len(pts)):
                    (ae, an), (be, bn) = pts[i], pts[(i + 1) % len(pts)]
                    edge = math.hypot(be - ae, bn - an)
                    if edge < 0.05:
                        continue
                    mesh.quad(obj, wall, (ae, an, 0), (be, bn, 0), (be, bn, h), (ae, an, h),
                              uv=((0, 0), (edge / 3, 0), (edge / 3, h / 3), (0, h / 3)))
            verts = np.array([p for r in rings for p in list(r.coords)[:-1]], dtype=np.float64)
            ends = np.cumsum([len(r.coords) - 1 for r in rings]).astype(np.uint32)
            idx = earcut.triangulate_float64(verts, ends)
            for k in range(0, len(idx), 3):
                a, b, cc = (tuple(verts[idx[k + j]]) for j in range(3))
                if (b[0] - a[0]) * (cc[1] - a[1]) - (b[1] - a[1]) * (cc[0] - a[0]) < 0:
                    b, cc = cc, b
                mesh.tri(obj, "M_Roof", (*a, h), (*b, h), (*cc, h))
            nb += 1

    for (kind, _, _), p in zip(rules["landmarks"], landmark_points):
        if kind == "lighthouse":
            lighthouse(mesh, cell_name("Landmarks", p.x, p.y), p.x, p.y)
            counts["landmarks"] += 1
            keep_out.append(p.buffer(4.0))

    compound_walls = []
    if not args.no_massing:
        district_roads = []
        for _, cls, _, g in roads:
            g = g.intersection(clip.buffer(60))
            district_roads += [(cls, l) for l in ([g] if isinstance(g, LineString) else getattr(g, "geoms", [])) if isinstance(l, LineString)]
        compound_walls = massing.compounds(mesh, cell_name, yards, keep_out, district_roads, ROAD_WIDTH, counts, clip, oxford)

    # Roads: ribbons with mitred joints, a dashed centre line on the major classes.
    lights = []
    road_km = 0.0
    # The dual carriageways, for the median's poles (place_lights).
    carriageways = [(rid, g) for rid, cls, name, g in roads if name in DUAL_CARRIAGEWAYS and cls in MAJOR_CLASSES]
    for rid, cls, name, g in roads:
        g = g.intersection(clip)
        lines = [g] if isinstance(g, LineString) else list(getattr(g, "geoms", []))
        for line in lines:
            if not isinstance(line, LineString) or line.length < 1:
                continue
            road_km += line.length / 1000
            w, lift = road_width(cls, name), ROAD_LIFT[cls]
            mat = "M_AsphaltMajor" if cls in MAJOR else "M_Asphalt"
            ribbon(mesh, line, w, lift, mat, "Roads")
            # Oxford Street has no lane markings (street survey; OSM lane_markings=no).
            if cls in ("primary", "secondary", "trunk") and name != "Oxford Street":
                for d0 in np.arange(2.0, line.length - 3.0, 6.0):
                    # Centre lines in Accra are worn: about a third of the dashes are gone.
                    if unit_hash(rid, "dash", int(d0)) < 0.35:
                        continue
                    seg = shapely.ops.substring(line, d0, d0 + 3.0)
                    if isinstance(seg, LineString) and seg.length > 0.5:
                        ribbon(mesh, seg, 0.15, lift + 0.01, "M_RoadPaint", "Roads")
            if cls in MAJOR:
                lights += place_lights(rid, cls, name, line, w, carriageways)

    # The median's twin-arm poles first, one row about a pole step apart (both carriageways place them), then the rest.
    twins = dedupe([l for l in lights if "twin" in l], RING_STEP_M * 0.75)
    lights = dedupe(twins + [l for l in lights if "twin" not in l], 7.0)
    lights, moved, dropped = off_asphalt(lights, [(g, road_width(cls, name)) for _, cls, name, g in roads])
    if moved or dropped:
        print(f"street lights off the asphalt: {moved} moved back, {dropped} dropped")
    for l in lights:
        l.pop("twin", None)

    # Ground under everything, with a margin. A corridor lays it per cell, and not under the areas it leaves out.
    m = 60.0
    if "corridor" in area:
        lay_ground(mesh, ground_region(clip, minus, local, m))
    else:
        for e0 in np.arange(minx - m, maxx + m, CELL_M):
            for n0 in np.arange(miny - m, maxy + m, CELL_M):
                e1, n1 = min(e0 + CELL_M, maxx + m), min(n0 + CELL_M, maxy + m)
                mesh.quad("Ground", "M_Ground", (e0, n0, 0), (e1, n0, 0), (e1, n1, 0), (e0, n1, 0),
                          uv=((e0 / 8, n0 / 8), (e1 / 8, n0 / 8), (e1 / 8, n1 / 8), (e0 / 8, n1 / 8)))

    if "route" in area["lap"]:
        lap = route_lap(roads, region, area["lap"], to_utm, (oe, on))
    else:
        lap = benchmark_lap(roads, clip, area["lap"], to_utm, (oe, on))

    # Parked cars along the streets, where the district has no dressed block to park them (DISTRICT_RULES "parking").
    parked = []
    if rules.get("parking"):
        pieces = []
        for rid, cls, name, g in roads:
            g = g.intersection(clip)
            pieces += [(rid, cls, name, l) for l in ([g] if isinstance(g, LineString) else list(getattr(g, "geoms", [])))
                       if isinstance(l, LineString) and l.length >= 1]
        parked = place_parked(rules["parking"], pieces, keep_out + [p for _, p in yards], lap)

    # Hiding places for the heat chase, off the side streets and away from Oxford Street's dressed block.
    hide_roads = []
    for rid, cls, _, g in roads:
        g = g.intersection(clip)
        hide_roads += [(rid, cls, l) for l in ([g] if isinstance(g, LineString) else list(getattr(g, "geoms", [])))
                       if isinstance(l, LineString) and l.length >= 1]
    hiding = hiding_places(hide_roads, keep_out + [p for _, p in yards], compound_walls, parked, lights,
                           avoid=oxford.buffer(40) if oxford is not None else None, clip=clip)

    os.makedirs(args.out, exist_ok=True)
    base = os.path.join(args.out, args.name)
    with open(base + ".mtl", "w") as f:
        for name, (r, g, b) in MATERIALS.items():
            f.write(f"newmtl {name}\nKd {r:.3f} {g:.3f} {b:.3f}\nKs 0 0 0\nNs 10\nd 1\nillum 1\n\n")
    verts = mesh.write(base + ".obj", os.path.basename(base) + ".mtl")
    tris, objs = mesh.stats()
    meta = {
        "name": args.name,
        "bounds_wgs84": ({k: area[k] for k in ("south", "north", "west", "east")} if "corridor" not in area else
                         dict(zip(("west", "south", "east", "north"), (round(v, 5) for v in lonlat(clip).bounds)))),
        # A corridor's outline (one ring per piece, the boxes it leaves out may cut it in two), and the areas it meets
        # along their edges.
        **({"outline_m": [[[round(x, 1), round(y, 1)] for x, y in p.exterior.coords]
                          for p in getattr(clip.simplify(5.0), "geoms", [clip.simplify(5.0)]) if isinstance(p, Polygon)],
            "joins": area.get("minus", [])} if "corridor" in area else {}),
        "origin_wgs84": {"lat": area["origin"][0], "lon": area["origin"][1]},
        "origin_utm30n": {"e": round(oe, 2), "n": round(on, 2)},
        "offset_from_world_origin_m": {"x": round(oe - WORLD_ORIGIN_UTM[0], 2), "z": round(on - WORLD_ORIGIN_UTM[1], 2)},
        "bounds_m": {"xmin": round(minx, 1), "xmax": round(maxx, 1), "zmin": round(miny, 1), "zmax": round(maxy, 1)},
        "axes": "x east, y up, z north, metres from origin",
        "stats": {"buildings": nb, "road_km": round(road_km, 2), "lights": len(lights),
                  **({"parked": len(parked)} if rules.get("parking") else {}), "hiding": len(hiding), "triangles": tris,
                  "vertices": verts, "objects": objs, "lap_m": round(lap["length_m"], 1) if lap else None,
                  "height_m_median": round(float(np.median(heights)), 1) if heights else None,
                  "massing": {k: (round(v) if isinstance(v, float) else v) for k, v in sorted(counts.items())}},
        "lights": lights,
        "lap": lap,
        "signs": [{"name": n, "x": round(float(e - oe), 2), "z": round(float(nn - on), 2), "bearing": b}
                  for n, lat, lon, b in rules.get("signs", []) for e, nn in [to_utm.transform(lon, lat)]],
        # Only districts with a parking rule carry the key, so Osu's file is unchanged.
        **({"parked": parked} if parked else {}),
        # The drivable network for the game's traffic (circle-game src/vehicles/traffic.ts).
        "roads": traffic_network(roads, clip, ONE_WAY_ROADS),
        # What the game's cars collide with (circle-game src/core/scene.ts addSolid).
        "solid": solid_outlines(keep_out + [p for _, p in yards], compound_walls,
                                [(cls, g, road_width(cls, name)) for _, cls, name, g in roads]),
        # Where the game's hiding places stand (circle-game src/world/hiding.ts).
        "hiding": hiding,
        "credits": "Buildings and roads: Overture Maps Foundation (ODbL), from OpenStreetMap contributors, "
                   "Google Open Buildings and Microsoft. Heights from typology rules.",
    }
    with open(base + ".json", "w") as f:
        json.dump(meta, f, indent=1)
    preview(base + "-preview.png", roads, clip, mesh, lights, lap)
    print(json.dumps(meta["stats"]))
    if rules.get("parking"):
        mix = Counter(c["colour"] for c in parked)
        print(f"parked: {len(parked)} places, " + ", ".join(f"{n} {k}" for k, n in mix.most_common()))


def ribbon(mesh, line, w, lift, mat, prefix):
    pts = np.array(line.coords)
    if len(pts) < 2:
        return
    d = np.diff(pts, axis=0)
    seg_len = np.linalg.norm(d, axis=1)
    keep = np.concatenate([[True], seg_len > 1e-3])
    pts = pts[keep]
    if len(pts) < 2:
        return
    d = np.diff(pts, axis=0)
    t = d / np.linalg.norm(d, axis=1)[:, None]
    nrm = np.column_stack([-t[:, 1], t[:, 0]])  # left normal
    vn = np.zeros_like(pts)
    vn[0], vn[-1] = nrm[0], nrm[-1]
    for i in range(1, len(pts) - 1):
        m = nrm[i - 1] + nrm[i]
        ml = np.linalg.norm(m)
        if ml < 1e-6:
            vn[i] = nrm[i]
            continue
        m /= ml
        cos = max(float(np.dot(m, nrm[i])), 0.5)  # cap the mitre at 2x
        vn[i] = m / cos
    left = pts + vn * w / 2
    right = pts - vn * w / 2
    along = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    for i in range(len(pts) - 1):
        mid = (pts[i] + pts[i + 1]) / 2
        obj = cell_name(prefix, mid[0], mid[1])
        r0, r1, l1, l0 = right[i], right[i + 1], left[i + 1], left[i]
        mesh.quad(obj, mat, (*r0, lift), (*r1, lift), (*l1, lift), (*l0, lift),
                  uv=((0, along[i] / 4), (0, along[i + 1] / 4), (w / 4, along[i + 1] / 4), (w / 4, along[i] / 4)))


# Street lights on the lap, from circle-reference/catalogue/street-survey.md (Street View, Aug 2024):
#   - Oxford Street: one side only (east), poles 39, 142 and 84 m apart (mean 88 m), never alternating sides;
#   - Ring Road East and Danquah Circle: about 40 m apart (twin-arm poles in the median where the road is split);
#   - every head on the lap is white LED: the 2020-21 programme swapped the sodium heads on the same poles.
# Sodium survives only off the lap, on other main roads.
LAP_ROADS = ("Oxford Street", "Ring Road East", "Danquah Circle")
OXFORD_GAP_M = (35.0, 145.0)
RING_STEP_M = 40.0


# Main roads off the lap whose heads were all seen as white LED by day (circle-reference observations.csv,
# obs-accra-lights-heads-main-001, Aug-Oct 2024): they never draw sodium.
LED_ROADS = ("Cantonments Road", "Independence Avenue", "Ring Road Central", "Castle Road", "Liberation Road",
             "Kojo Thompson Road", "Kinbu Road")
# Roads lit by solar all-in-one poles: a panel on top of the pole, the LED head on a curved arm below it (3 of 3 heads
# seen on Cantonments Road, Street View Aug 2024). The game draws the panel from the lamp's "pole" field.
SOLAR_ROADS = ("Cantonments Road",)


def lamp_kind(rid, stretch, k, on_lap, name=""):
    """White LED everywhere on the lap (80% neutral, 20% cold); off the lap 10% of stretches keep sodium, the rest
    as on the lap. circle-reference counted 1 sodium-era head of 14 off the lap (Street View, Aug-Oct 2024; the
    2020-21 programme put LED on the main roads), so sodium stays a minority kept for the mix (decision of
    14 Nov 2026). Dead poles: whole dark stretches (about one in eight), and off the lap about one pole in four more.
    The city is darker than that (most poles dark on main roads in Oct 2024), but the game's nights stay lighter
    for comfort of play (decision of 14 Nov 2026)."""
    r = unit_hash(rid, stretch, "kind")
    kind = "led_cool" if r >= 0.8 else "led"
    if not on_lap and 0.7 <= r < 0.8 and name not in LED_ROADS:
        kind = "sodium"
    if unit_hash(rid, stretch) < 0.12 or (not on_lap and unit_hash(rid, "dead", k) < 0.25):
        kind = "dead"
    return kind


def light_spots(rid, name, length):
    """Distances along the road where poles stand, and the side rule for each road."""
    if name == "Oxford Street":
        out, d, k = [], 12.0, 0
        while d < length:
            out.append(d)
            u = unit_hash(rid, "gap", k)
            d += OXFORD_GAP_M[0] + (OXFORD_GAP_M[1] - OXFORD_GAP_M[0]) * u ** 1.2
            k += 1
        return out, "east"
    if name in LAP_ROADS:
        return list(np.arange(RING_STEP_M / 2, length, RING_STEP_M)), "left"
    return list(np.arange(16.0, length, 32.0)), "alternate"


def median_width(p, left, w, rid, carriageways, reach=25.0):
    """Centre to centre (m) from a point on a carriageway's centreline to the other carriageway across the median, to
    its left (the median side: Ghana drives on the right), or None when no other carriageway lies within `reach`."""
    a = p + left * (w / 2)
    probe = LineString([a, p + left * reach])
    best = None
    for other, g in carriageways:
        if other == rid or not probe.intersects(g):
            continue
        x = probe.intersection(g)
        for part in getattr(x, "geoms", [x]):
            if part.is_empty:
                continue
            d = float(Point(p).distance(part))
            if best is None or d < best:
                best = d
    return best


def place_lights(rid, cls, name, line, w, carriageways=()):
    """Poles along major roads, following the street survey on the lap (see LAP_ROADS) and, elsewhere, one pole
    every 32 m on alternating sides, so the night has dark gaps between pools. On a dual carriageway
    (DUAL_CARRIAGEWAYS, with the other carriageways in `carriageways`: (road id, line)) a pole stands in the middle
    of the median with a head over each carriageway, a twin-arm pole (two lamps on one foot sharing a "twin" key);
    where the median is under 2 m, on the outer verge."""
    out = []
    on_lap = name in LAP_ROADS
    spots, rule = light_spots(rid, name, line.length)
    step = 88.0 if name == "Oxford Street" else (RING_STEP_M if on_lap else 32.0)
    for k, d in enumerate(spots):
        stretch = int(d // (step * 6))
        kind = lamp_kind(rid, stretch, k, on_lap, name)
        p = np.array(line.interpolate(d).coords[0])
        q = np.array(line.interpolate(min(d + 1.0, line.length)).coords[0])
        if np.linalg.norm(q - p) < 1e-6:
            continue
        t = (q - p) / np.linalg.norm(q - p)
        left = np.array([-t[1], t[0]])
        if name in DUAL_CARRIAGEWAYS and carriageways:
            across = median_width(p, left, w, rid, carriageways)
            if across is not None and across - w >= 2.0:
                foot = p + left * (across / 2)
                for face in (-left, left):
                    lamp = {"x": round(float(foot[0]), 2), "y": 0.0, "z": round(float(foot[1]), 2),
                            "yaw": round(math.degrees(math.atan2(face[0], face[1])), 1),
                            "kind": kind, "road": name or cls, "twin": f"{rid}:{k}"}
                    out.append(lamp)
                continue
            if across is not None:
                # No room in the median: the outer verge, the arm over this carriageway.
                pos = p - left * (w / 2 + 1.2)
                face = left
                out.append({"x": round(float(pos[0]), 2), "y": 0.0, "z": round(float(pos[1]), 2),
                            "yaw": round(math.degrees(math.atan2(face[0], face[1])), 1),
                            "kind": kind, "road": name or cls})
                continue
        if rule == "east":
            s = 1 if left[0] >= 0 else -1
        elif rule == "left":
            s = 1
        else:
            s = 1 if k % 2 == 0 else -1
        pos = p + left * s * (w / 2 + 1.2)
        face = -left * s  # the lamp arm points over the road
        lamp = {"x": round(float(pos[0]), 2), "y": 0.0, "z": round(float(pos[1]), 2),
                "yaw": round(math.degrees(math.atan2(face[0], face[1])), 1),
                "kind": kind, "road": name or cls}
        if name in SOLAR_ROADS:
            lamp["pole"] = "solar"
        out.append(lamp)
    return out


def off_asphalt(lights, roads, margin=0.3, back=8.0, step=0.5):
    """No pole on a carriageway: where several roads meet or run side by side, one road's pole can land on another's
    asphalt (the user's test drive of 14 Nov 2026 saw poles standing on the Ring Road). roads: (line, width). A pole
    within `margin` m of any carriageway moves back, away from the road its arm reaches over, up to `back` m, until it
    is clear; if it cannot, it goes. A twin-arm pole in a median has nowhere to move: it goes whole.
    Returns (lights, moved, dropped)."""
    if not lights or not roads:
        return lights, 0, 0
    asphalt = shapely.STRtree([line.buffer(w / 2 + margin) for line, w in roads])
    on = lambda x, z: len(asphalt.query(Point(x, z), predicate="intersects")) > 0
    out, moved, dropped, gone = [], 0, 0, set()
    for l in lights:
        if not on(l["x"], l["z"]):
            out.append(l)
            continue
        if "twin" in l:
            gone.add(l["twin"])
            dropped += 1
            continue
        yaw = math.radians(l["yaw"])
        fx, fz = math.sin(yaw), math.cos(yaw)       # the way the arm reaches, over the road
        for d in np.arange(step, back + 1e-6, step):
            x, z = l["x"] - fx * d, l["z"] - fz * d
            if not on(x, z):
                out.append({**l, "x": round(float(x), 2), "z": round(float(z), 2)})
                moved += 1
                break
        else:
            dropped += 1
    # A twin pole goes whole: its other head too, wherever it came in the list.
    kept = [l for l in out if l.get("twin") not in gone]
    return kept, moved, dropped + (len(out) - len(kept))


def dedupe(lights, min_gap):
    """Lamps at least `min_gap` m from every pole kept before them; the heads of a twin-arm pole (one "twin" key) are
    kept or dropped together."""
    kept, poles, twins = [], [], set()
    for l in lights:
        tw = l.get("twin")
        if tw is not None and tw in twins:
            kept.append(l)
            continue
        if all((l["x"] - x) ** 2 + (l["z"] - z) ** 2 >= min_gap ** 2 for x, z in poles):
            kept.append(l)
            poles.append((l["x"], l["z"]))
            if tw is not None:
                twins.add(tw)
    return kept


# The game's parked car, width and length in metres (circle-game src/render/city.ts, a 1.74 x 4.3 m body).
CAR_BODY_M = (1.74, 4.3)


def junctions(lines, tol=0.05):
    """Points where three road arms or more meet: three segment ends, a segment ending on another, two crossing.
    Overture segments often run on through a junction, so their ends alone do not find them all; two segments
    meeting end to end (one street continued) are no junction."""
    tree = shapely.STRtree(lines)
    found = {}
    for i, a in enumerate(lines):
        for j in tree.query(a):
            if j <= i:
                continue
            x = a.intersection(lines[j])
            for part in getattr(x, "geoms", [x]):
                if part.is_empty:
                    continue
                # Two segments sharing a stretch meet at its ends.
                ends = [part] if part.geom_type == "Point" else [Point(part.coords[0]), Point(part.coords[-1])]
                for p in ends:
                    found.setdefault((round(p.x, 1), round(p.y, 1)), p)
    out = []
    for p in found.values():
        arms = 0
        for k in tree.query(p, predicate="dwithin", distance=tol):
            l = lines[k]
            at_end = min(p.distance(Point(l.coords[0])), p.distance(Point(l.coords[-1]))) <= tol
            arms += 1 if at_end and not l.is_closed else 2
        if arms >= 3:
            out.append(p)
    return out


TRAFFIC_CLASSES = ("motorway", "trunk", "primary", "secondary", "tertiary", "residential", "unclassified")


def solid_outlines(footprints, walls, roads=(), tolerance=0.5, kerb=0.3):
    """What the game's cars collide with: the outline of every building (look-dev ones and landmarks included) and
    the compound walls with their closed gates, simplified to `tolerance` metres and rounded to 0.1 m. Each outline
    or wall is a flat list x0, z0, x1, z1, ...; an outline is a closed ring without its first point repeated.

    roads: (class, line) or (class, line, width) of the drivable network (the width by class when not given). Mapped footprints sometimes reach onto the carriageway (kiosks,
    canopies, a misplaced centreline: 23 outlines in Osu and 57 in Jamestown lie within 1 m of one), which would
    block the street the traffic and the lap drive. The part of an outline that lies on the carriageway, up to `kerb`
    metres short of its edge, is left out, and so is what remains of the outline if under 4 m2."""
    carriage = None
    # Round ends, so the corners of the junctions, where the cars cut across, are kept clear too.
    lanes = [r[1].buffer(max(0.5, (r[2] if len(r) > 2 else ROAD_WIDTH[r[0]]) / 2 - kerb))
             for r in roads if r[0] in TRAFFIC_CLASSES]
    if lanes:
        carriage = shapely.union_all(lanes)
    rings, lines = [], []
    for p in footprints:
        if not isinstance(p, Polygon) or p.is_empty:
            continue
        if carriage is not None and p.intersects(carriage):
            p = p.difference(carriage)
        for q in (list(p.geoms) if hasattr(p, "geoms") else [p]):
            if not isinstance(q, Polygon) or q.is_empty:
                continue
            q = q.simplify(tolerance)
            if not isinstance(q, Polygon) or q.is_empty or q.area < 4:
                continue
            pts = list(q.exterior.coords)[:-1]
            if len(pts) >= 3:
                rings.append([round(float(c), 1) for xy in pts for c in xy[:2]])
    for w in walls:
        if carriage is not None and w.intersects(carriage):
            w = w.difference(carriage)
        for piece in (list(w.geoms) if hasattr(w, "geoms") else [w]):
            piece = piece.simplify(tolerance)
            if isinstance(piece, LineString) and piece.length >= 1:
                lines.append([round(float(c), 1) for xy in piece.coords for c in xy[:2]])
    return {"buildings": rings, "walls": lines}


# Hiding places (circle-bible bible/game-design.md, heat level 3: "a trotro station, car wash, market shed, a crew
# garage"): open sheds on free plots beside the side streets, where the game lets the heat cool. They are invented
# places, unnamed, on ground the map leaves empty; the game builds the shed (circle-game src/world/hiding.ts).
HIDING_KINDS = ("car-wash", "market-shed", "crew-garage")
HIDING_CLASSES = ("tertiary", "residential", "unclassified")
HIDING_SIZE_M = (9.0, 11.0)          # along the street, and deep
HIDING_SPACING_M = 200.0


def hiding_places(roads, footprints, walls, parked=(), lights=(), avoid=None, clip=None, step=6.0,
                  size=HIDING_SIZE_M, setback=1.2, spacing=HIDING_SPACING_M, widths=ROAD_WIDTH):
    """Free plots beside the side streets for the game's hiding places, at least `spacing` metres apart.

    roads: (id, class, line) in metres; a place stands beside a road of HIDING_CLASSES, its open front `setback`
    metres back from the carriageway and facing it, `size` = (width along the street, depth). The plot, with the strip
    between it and the carriageway, must be clear of footprints (0.5 m), walls (0.3 m), every carriageway (1 m), parked
    cars (2.5 m) and street-light poles (1 m), inside `clip`, and outside `avoid` (a geometry, such as the dressed
    block's street). Candidates every `step` metres on both sides are taken in a hashed order, so reruns give the same
    places, and each new one keeps `spacing` from those taken. Returns [{"kind", "x", "z", "yaw", "w", "d"}] in builder
    axes (z north), yaw in degrees clockwise from north: the way the open front faces."""
    w_along, depth = size
    blockers = [p.buffer(0.5) for p in footprints if not p.is_empty]
    blockers += [w.buffer(0.3) for w in walls if not w.is_empty]
    blockers += [Point(c["x"], c["z"]).buffer(2.5) for c in parked]
    blockers += [Point(l["x"], l["z"]).buffer(1.0) for l in lights]
    btree = shapely.STRtree(blockers) if blockers else None
    lanes = [l.buffer(widths[c] / 2 + 1.0) for _, c, l in roads if c in TRAFFIC_CLASSES]
    ltree = shapely.STRtree(lanes) if lanes else None
    candidates = []
    for rid, cls, line in roads:
        if cls not in HIDING_CLASSES or line.length < w_along + 2 * step:
            continue
        half_w = widths[cls] / 2
        for k, d in enumerate(np.arange(w_along / 2 + step, line.length - w_along / 2 - step + 1e-6, step)):
            a = np.array(line.interpolate(d - w_along / 2).coords[0])
            b = np.array(line.interpolate(d + w_along / 2).coords[0])
            t = b - a
            if np.linalg.norm(t) < w_along * 0.95:
                continue                                     # the street bends here
            t = t / np.linalg.norm(t)
            left = np.array([-t[1], t[0]])
            c = np.array(line.interpolate(d).coords[0])
            for side in (1, -1):
                n = left * side
                front = c + n * (half_w + setback)
                centre = front + n * depth / 2
                corners = [front - t * w_along / 2, front + t * w_along / 2, front + t * w_along / 2 + n * depth,
                           front - t * w_along / 2 + n * depth]
                plot = Polygon([tuple(p) for p in corners])
                # The way in: from the carriageway's edge to the plot's front.
                way_in = Polygon([tuple(p) for p in (c + n * half_w - t * w_along / 2, c + n * half_w + t * w_along / 2,
                                                     front + t * w_along / 2, front - t * w_along / 2)])
                candidates.append((unit_hash(rid, "hide", k, side), plot, way_in, centre, -n))
    candidates.sort(key=lambda x: x[0])
    taken = []
    for h, plot, way_in, centre, face in candidates:
        if any(math.hypot(centre[0] - o["x"], centre[1] - o["z"]) < spacing for o in taken):
            continue
        if clip is not None and not clip.contains(plot):
            continue
        if avoid is not None and plot.intersects(avoid):
            continue
        if btree is not None and len(btree.query(plot.union(way_in), predicate="intersects")):
            continue
        if ltree is not None and len(ltree.query(plot, predicate="intersects")):
            continue
        taken.append({"kind": HIDING_KINDS[len(taken) % len(HIDING_KINDS)], "x": round(float(centre[0]), 2),
                      "z": round(float(centre[1]), 2), "yaw": round(math.degrees(math.atan2(face[0], face[1])), 1),
                      "w": w_along, "d": depth})
    return taken


def traffic_network(roads, clip, one_way=()):
    """The drivable roads as a network for the game's traffic: every road of a traffic class inside the district,
    noded at every crossing (Overture segments often run straight through junctions), each piece with the class,
    width and name of the road it lies on, simplified to 0.5 m. Points are x east, z north, as the rest of the file.
    A piece of a road named in one_way is driven one way only: it runs the way its road's geometry runs and carries
    "oneway": true (the Ring Road's carriageways and Danquah Circle, ONE_WAY_ROADS)."""
    src = []
    for rid, cls, name, g in roads:
        if cls not in TRAFFIC_CLASSES:
            continue
        g = g.intersection(clip)
        for line in ([g] if isinstance(g, LineString) else list(getattr(g, "geoms", []))):
            if isinstance(line, LineString) and line.length >= 2:
                src.append((cls, name, line))
    if not src:
        return []
    tree = shapely.STRtree([l for _, _, l in src])
    noded = shapely.union_all([l for _, _, l in src])
    pieces = [noded] if isinstance(noded, LineString) else list(getattr(noded, "geoms", []))
    out = []
    for piece in pieces:
        if not isinstance(piece, LineString) or piece.length < 1.0:
            continue
        mid = piece.interpolate(0.5, normalized=True)
        cls, name, line = src[int(tree.nearest(mid))]
        pts = list(piece.simplify(0.5).coords)
        entry = {"cls": cls, "w": road_width(cls, name), "name": name}
        if name in one_way:
            if line.project(Point(pts[0])) > line.project(Point(pts[-1])):
                pts = pts[::-1]
            entry["oneway"] = True
        entry["pts"] = [round(float(c), 1) for xy in pts for c in xy[:2]]
        out.append(entry)
    return out


def place_parked(rule, pieces, buildings, lap, widths=ROAD_WIDTH):
    """Cars parked parallel to the road edge, for a district without a dressed block (DISTRICT_RULES "parking").

    pieces: (road id, class, name, line) of every road in the district, clipped to it, in metres; the rule's classes
    and streets are parked on, and all of them make the junctions. buildings: the footprints the cars keep clear of.
    lap: benchmark_lap()'s dict, or None for no limit around the lap. Every place is a station along the road, one
    each step_m from half a step in, on each side; it is taken when its hash falls under the street's chance and it
    clears the junctions, the buildings (the car's long axis, rule "building_m" away), the spawn and the cars already
    placed. A car also stays off every other road's asphalt (its centre half a car's width outside that carriageway),
    which keeps it out of the side street at a sharp junction and off a road running alongside. The heading is
    along the road, either way. Returns [{"x", "z", "y", "yaw", "colour"}] in builder axes, yaw in degrees
    clockwise from north, as build_lookdev.py writes its parked cars."""
    if not pieces:
        return []
    lines = [l for _, _, _, l in pieces]
    jn = junctions(lines)
    jtree = shapely.STRtree(jn) if jn else None
    btree = shapely.STRtree(buildings) if buildings else None
    # Other roads' carriageways, with flat ends so a street's next segment does not stop the cars on this one.
    bands = shapely.STRtree([l.buffer(road_width(c, n, widths) / 2 + CAR_BODY_M[0] / 2, cap_style="flat")
                             for _, c, n, l in pieces])
    lap_line = None
    if lap and lap.get("path") and rule.get("lap_m"):
        lap_line = LineString(list(zip(lap["path"][0::2], lap["path"][1::2])))
    spawn = Point(lap["spawn"]["x"], lap["spawn"]["z"]) if lap and lap.get("spawn") else None
    gap, half = rule["gap_m"], CAR_BODY_M[1] / 2
    grid, out = {}, []
    seen = Counter()

    def clear_of_cars(c):
        gx, gz = int(math.floor(c[0] / gap)), int(math.floor(c[1] / gap))
        return all((c[0] - o[0]) ** 2 + (c[1] - o[1]) ** 2 >= gap ** 2
                   for dx in (-1, 0, 1) for dz in (-1, 0, 1) for o in grid.get((gx + dx, gz + dz), []))

    for i, (rid, cls, name, line) in enumerate(pieces):
        piece = seen[rid]            # a road cut in several pieces by the district's edge
        seen[rid] += 1
        take = rule.get("streets", {}).get(name, rule["take"] if cls in rule["classes"] else 0.0)
        if take <= 0 or line.length < rule["step_m"]:
            continue
        w = road_width(cls, name, widths)
        off = w / 2 - (rule["inset_m"] if w < rule["narrow_m"] else rule["wide_inset_m"])
        for k, d in enumerate(np.arange(rule["step_m"] / 2, line.length - rule["step_m"] / 2 + 1e-6, rule["step_m"])):
            p = np.array(line.interpolate(d).coords[0])
            t = np.subtract(line.interpolate(min(d + 1.0, line.length)).coords[0], line.interpolate(max(d - 1.0, 0.0)).coords[0])
            if np.linalg.norm(t) < 1e-6:
                continue
            t = t / np.linalg.norm(t)
            left = np.array([-t[1], t[0]])
            for side in (1, -1):
                if unit_hash(rid, "park", piece, side, k) >= take:
                    continue
                c = p + left * side * off
                pt = Point(c)
                if lap_line is not None and pt.distance(lap_line) > rule["lap_m"]:
                    continue
                if spawn is not None and pt.distance(spawn) < rule["spawn_m"]:
                    continue
                if jtree is not None and len(jtree.query(pt, predicate="dwithin", distance=rule["junction_m"])):
                    continue
                axis = LineString([c - t * half, c + t * half])
                if btree is not None and len(btree.query(axis, predicate="dwithin", distance=rule["building_m"])):
                    continue
                if any(j != i for j in bands.query(pt, predicate="intersects")):
                    continue
                if not clear_of_cars(c):
                    continue
                grid.setdefault((int(math.floor(c[0] / gap)), int(math.floor(c[1] / gap))), []).append(c)
                face = t if unit_hash(rid, "parkdir", piece, side, k) < 0.5 else -t
                out.append({"x": round(float(c[0]), 2), "z": round(float(c[1]), 2), "y": 0.0,
                            "yaw": round(math.degrees(math.atan2(face[0], face[1])), 1),
                            "colour": massing.pick(rule["colours"], unit_hash(rid, "parkcol", piece, side, k))})
    return out


def benchmark_lap(roads, clip, lap_spec, to_utm, origin):
    """A loop for the scripted lap: Danquah Circle down Oxford Street, back up a parallel route."""
    street, south_lat = lap_spec
    G = nx.Graph()
    key = lambda p: (round(p[0], 1), round(p[1], 1))
    for rid, cls, name, g in roads:
        g = g.intersection(clip)
        lines = [g] if isinstance(g, LineString) else list(getattr(g, "geoms", []))
        for line in lines:
            if not isinstance(line, LineString) or line.length < 1 or cls == "service":
                continue
            a, b = key(line.coords[0]), key(line.coords[-1])
            if a == b:
                continue
            if G.has_edge(a, b) and G[a][b]["length"] <= line.length:
                continue
            G.add_edge(a, b, length=line.length, name=name, coords=list(line.coords))
    if G.number_of_nodes() == 0:
        return None
    G = G.subgraph(max(nx.connected_components(G), key=len)).copy()
    ox_nodes = [n for u, v, d in G.edges(data=True) if d["name"] == street for n in (u, v)]
    if not ox_nodes:
        return None
    _, south_n = to_utm.transform(-0.1825, south_lat)
    south_n -= origin[1]
    start = min(ox_nodes, key=lambda p: p[0] ** 2 + p[1] ** 2)  # nearest the origin, Danquah Circle
    turn = min(ox_nodes, key=lambda p: abs(p[1] - south_n))
    go = nx.shortest_path(G, start, turn, weight="length")
    used = {frozenset(e) for e in zip(go, go[1:])}
    back = nx.shortest_path(G, turn, start, weight=lambda u, v, d: d["length"] * (25 if frozenset((u, v)) in used else 1))
    pts = []
    for u, v in list(zip(go, go[1:])) + list(zip(back, back[1:])):
        c = G[u][v]["coords"]
        if key(c[0]) != u:
            c = c[::-1]
        pts.extend(c if not pts else c[1:])
    line = LineString(pts)
    wp = [line.interpolate(d).coords[0] for d in np.arange(0, line.length, 8.0)]
    names = []
    for u, v in list(zip(go, go[1:])) + list(zip(back, back[1:])):
        n = G[u][v]["name"]
        if n and (not names or names[-1] != n):
            names.append(n)
    p0, p1 = wp[0], wp[1]
    return {"length_m": line.length, "via": names,
            "spawn": {"x": round(p0[0], 2), "y": 0.5, "z": round(p0[1], 2),
                      "yaw": round(math.degrees(math.atan2(p1[0] - p0[0], p1[1] - p0[1])), 1)},
            "path": [round(v, 2) for x, z in wp for v in (x, z)]}


def split_at_shared_vertices(lines, ndigits=2):
    """Cuts each line at its inner vertices that another line also passes through: the junctions mapped as shared
    nodes, where an Overture segment often runs on. Lines that cross without sharing a vertex stay uncut, so a flyover
    or a bridge over a road never becomes a turn."""
    key = lambda p: (round(p[0], ndigits), round(p[1], ndigits))
    users = Counter()
    for line in lines:
        for k in {key(c) for c in line.coords}:
            users[k] += 1
    out = []
    for line in lines:
        cs = list(line.coords)
        cuts = [0] + [i for i in range(1, len(cs) - 1) if users[key(cs[i])] > 1] + [len(cs) - 1]
        out += [(line, LineString(cs[a:b + 1])) for a, b in zip(cuts, cuts[1:]) if b > a]
    return out


def route_lap(roads, region, spec, to_utm, origin):
    """A closed route through the points of spec["route"] (lat, lon), in order and back to the first: the lap of an
    area that follows a road rather than circling a block (the Ring Road). Roads are cut to the region at their shared
    vertices; the roads named in spec["one_way"] are driven along their geometry only (dual carriageways and
    roundabouts, mapped one way per carriageway; Ghana drives on the right), the others both ways. Each leg is the
    shortest way to the next point, at 25 times the cost on stretches already driven, so the turns at the ends go
    round a block rather than back along the same street. Service roads are left out. Returns benchmark_lap()'s
    dict, with the distance each route point was moved to reach the network ("snapped_m")."""
    one_way = set(spec.get("one_way", ()))
    G = nx.DiGraph()
    key = lambda p: (round(p[0], 2), round(p[1], 2))
    src = []
    for rid, cls, name, g in roads:
        if cls == "service":
            continue
        g = g.intersection(region)
        src += [(name, l) for l in ([g] if isinstance(g, LineString) else list(getattr(g, "geoms", [])))
                if isinstance(l, LineString) and l.length >= 1]
    names = {id(l): n for n, l in src}

    def add(a, b, coords, length, name):
        if not G.has_edge(a, b) or G[a][b]["length"] > length:
            G.add_edge(a, b, length=length, name=name, coords=coords)

    for whole, piece in split_at_shared_vertices([l for _, l in src]):
        a, b = key(piece.coords[0]), key(piece.coords[-1])
        if a == b or piece.length < 0.05:
            continue
        name = names[id(whole)]
        add(a, b, list(piece.coords), piece.length, name)
        if name not in one_way:
            add(b, a, list(piece.coords)[::-1], piece.length, name)
    if G.number_of_nodes() == 0:
        return None
    nodes = np.array(list(G.nodes))
    stops, snapped = [], []
    for lat, lon in spec["route"]:
        e, n = to_utm.transform(lon, lat)
        p = np.array([e - origin[0], n - origin[1]])
        d = np.hypot(*(nodes - p).T)
        stops.append(tuple(nodes[int(np.argmin(d))]))
        snapped.append(round(float(d.min()), 2))
    used = set()
    edges = []
    for s, t in zip(stops, stops[1:] + stops[:1]):
        leg = nx.shortest_path(G, s, t, weight=lambda u, v, d: d["length"] * (25 if frozenset((u, v)) in used else 1))
        for u, v in zip(leg, leg[1:]):
            used.add(frozenset((u, v)))
            edges.append((u, v))
    pts = []
    for u, v in edges:
        c = G[u][v]["coords"]
        pts.extend(c if not pts else c[1:])
    line = LineString(pts)
    wp = [line.interpolate(d).coords[0] for d in np.arange(0, line.length, 8.0)]
    via = []
    for u, v in edges:
        n = G[u][v]["name"]
        if n and (not via or via[-1] != n):
            via.append(n)
    p0, p1 = wp[0], wp[1]
    return {"length_m": line.length, "via": via, "snapped_m": snapped,
            "spawn": {"x": round(p0[0], 2), "y": 0.5, "z": round(p0[1], 2),
                      "yaw": round(math.degrees(math.atan2(p1[0] - p0[0], p1[1] - p0[1])), 1)},
            "path": [round(v, 2) for x, z in wp for v in (x, z)]}


def preview(path, roads, clip, mesh, lights, lap):
    minx, miny, maxx, maxy = clip.bounds
    s = 1.0
    W, H = int((maxx - minx) * s) + 1, int((maxy - miny) * s) + 1
    img = Image.new("RGB", (W, H), (19, 29, 56))
    dr = ImageDraw.Draw(img)
    P = lambda e, n: ((e - minx) * s, (maxy - n) * s)
    for (obj, mat), tris in mesh.groups.items():
        if mat == "M_Roof":
            for a, b, c, _ in tris:
                shade = int(min(255, 90 + a[2] * 6))
                dr.polygon([P(a[0], a[1]), P(b[0], b[1]), P(c[0], c[1])], fill=(shade, shade, int(shade * 0.95)))
    for rid, cls, name, g in roads:
        g = g.intersection(clip)
        for line in ([g] if isinstance(g, LineString) else list(getattr(g, "geoms", []))):
            if isinstance(line, LineString):
                dr.line([P(*c) for c in line.coords], fill=(60, 64, 72), width=max(1, int(road_width(cls, name) * s)))
    if lap:
        wp = list(zip(lap["path"][0::2], lap["path"][1::2]))
        dr.line([P(*c) for c in wp] + [P(*wp[0])], fill=(228, 0, 43), width=3)
    for l in lights:
        x, y = P(l["x"], l["z"])
        col = {"sodium": (255, 194, 13), "led_cool": (200, 225, 255), "dead": (60, 60, 60)}.get(l["kind"], (245, 240, 230))
        dr.ellipse([x - 2, y - 2, x + 2, y + 2], fill=col)
    img.save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slice", default="data/work/slice", help="folder from overture_slice.py export")
    ap.add_argument("--name", default="osu-poc", choices=sorted(AREAS))
    ap.add_argument("--out", default="data/work/osu-poc")
    ap.add_argument("--exclude", help="file of building ids to leave out (from build_lookdev.py)")
    ap.add_argument("--no-massing", action="store_true",
                    help="plain extruded footprints, no roofs, bulkheads, frames or compound walls (the old district)")
    build(ap.parse_args())


if __name__ == "__main__":
    import shapely.ops  # noqa: F401  (substring)
    main()
