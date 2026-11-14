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

AREAS = {
    # About 1.3 km2 of Osu: Danquah Circle, the north half of Oxford Street, Ring Road East.
    "osu-poc": {"south": 5.5575, "north": 5.5695, "west": -0.1870, "east": -0.1780,
                "origin": (5.5669, -0.1809), "lap": ("Oxford Street", 5.5600), "district": "osu"},
    # Jamestown, Bukom and Ussher Town (aoi/slices.geojson "jamestown"; slice in data/work/slice-jamestown).
    "jamestown": {"south": 5.5300, "north": 5.5425, "west": -0.2175, "east": -0.2055,
                  "origin": JAMESTOWN_ORIGIN, "lap": JAMESTOWN_LAP, "district": "jamestown"},
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
}

ROAD_WIDTH = {"motorway": 22.0, "trunk": 18.0, "primary": 13.0, "secondary": 10.0, "tertiary": 8.5,
              "residential": 6.0, "unclassified": 6.0, "living_street": 5.0, "service": 4.5}
ROAD_LIFT = {"motorway": 0.09, "trunk": 0.08, "primary": 0.07, "secondary": 0.06, "tertiary": 0.05,
             "residential": 0.04, "unclassified": 0.04, "living_street": 0.04, "service": 0.035}
MAJOR = {"motorway", "trunk", "primary", "secondary", "tertiary"}
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


def build(args):
    area = AREAS[args.name]
    rules = DISTRICT_RULES.get(area.get("district", "osu"), DISTRICT_RULES["osu"])
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True)
    oe, on = to_utm.transform(area["origin"][1], area["origin"][0])

    def local(geom):
        return shapely.transform(geom, lambda xy: np.column_stack(to_utm.transform(xy[:, 0], xy[:, 1])) - (oe, on))

    lonlat_box = box(area["west"], area["south"], area["east"], area["north"])
    clip = local(lonlat_box)
    minx, miny, maxx, maxy = clip.bounds
    con = duckdb.connect()
    mesh = Mesh()

    # Roads first: buildings near Oxford Street get an extra storey, so its line is needed.
    roads = []
    for rid, cls, name, blob in load(con, os.path.join(args.slice, "roads.parquet"), "id, class, name"):
        if cls not in ROAD_WIDTH:
            continue
        g = shapely.from_wkb(bytes(blob))
        if not g.intersects(lonlat_box):
            continue
        roads.append((rid, cls, name or "", local(g)))
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
        if not lonlat_box.contains(g.centroid):
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

    if not args.no_massing:
        district_roads = []
        for _, cls, _, g in roads:
            g = g.intersection(clip.buffer(60))
            district_roads += [(cls, l) for l in ([g] if isinstance(g, LineString) else getattr(g, "geoms", [])) if isinstance(l, LineString)]
        massing.compounds(mesh, cell_name, yards, keep_out, district_roads, ROAD_WIDTH, counts, clip, oxford)

    # Roads: ribbons with mitred joints, a dashed centre line on the major classes.
    lights = []
    road_km = 0.0
    for rid, cls, name, g in roads:
        g = g.intersection(clip)
        lines = [g] if isinstance(g, LineString) else list(getattr(g, "geoms", []))
        for line in lines:
            if not isinstance(line, LineString) or line.length < 1:
                continue
            road_km += line.length / 1000
            w, lift = ROAD_WIDTH[cls], ROAD_LIFT[cls]
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
                lights += place_lights(rid, cls, name, line, w)

    lights = dedupe(lights, 7.0)

    # Ground under everything, with a margin.
    m = 60.0
    for e0 in np.arange(minx - m, maxx + m, CELL_M):
        for n0 in np.arange(miny - m, maxy + m, CELL_M):
            e1, n1 = min(e0 + CELL_M, maxx + m), min(n0 + CELL_M, maxy + m)
            mesh.quad("Ground", "M_Ground", (e0, n0, 0), (e1, n0, 0), (e1, n1, 0), (e0, n1, 0),
                      uv=((e0 / 8, n0 / 8), (e1 / 8, n0 / 8), (e1 / 8, n1 / 8), (e0 / 8, n1 / 8)))

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

    os.makedirs(args.out, exist_ok=True)
    base = os.path.join(args.out, args.name)
    with open(base + ".mtl", "w") as f:
        for name, (r, g, b) in MATERIALS.items():
            f.write(f"newmtl {name}\nKd {r:.3f} {g:.3f} {b:.3f}\nKs 0 0 0\nNs 10\nd 1\nillum 1\n\n")
    verts = mesh.write(base + ".obj", os.path.basename(base) + ".mtl")
    tris, objs = mesh.stats()
    meta = {
        "name": args.name,
        "bounds_wgs84": {k: area[k] for k in ("south", "north", "west", "east")},
        "origin_wgs84": {"lat": area["origin"][0], "lon": area["origin"][1]},
        "origin_utm30n": {"e": round(oe, 2), "n": round(on, 2)},
        "offset_from_world_origin_m": {"x": round(oe - WORLD_ORIGIN_UTM[0], 2), "z": round(on - WORLD_ORIGIN_UTM[1], 2)},
        "bounds_m": {"xmin": round(minx, 1), "xmax": round(maxx, 1), "zmin": round(miny, 1), "zmax": round(maxy, 1)},
        "axes": "x east, y up, z north, metres from origin",
        "stats": {"buildings": nb, "road_km": round(road_km, 2), "lights": len(lights),
                  **({"parked": len(parked)} if rules.get("parking") else {}), "triangles": tris,
                  "vertices": verts, "objects": objs, "lap_m": round(lap["length_m"], 1) if lap else None,
                  "height_m_median": round(float(np.median(heights)), 1) if heights else None,
                  "massing": {k: (round(v) if isinstance(v, float) else v) for k, v in sorted(counts.items())}},
        "lights": lights,
        "lap": lap,
        "signs": [{"name": n, "x": round(float(e - oe), 2), "z": round(float(nn - on), 2), "bearing": b}
                  for n, lat, lon, b in rules.get("signs", []) for e, nn in [to_utm.transform(lon, lat)]],
        # Only districts with a parking rule carry the key, so Osu's file is unchanged.
        **({"parked": parked} if parked else {}),
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


def place_lights(rid, cls, name, line, w):
    """Poles along major roads, following the street survey on the lap (see LAP_ROADS) and, elsewhere, one pole
    every 32 m on alternating sides, so the night has dark gaps between pools."""
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


def dedupe(lights, min_gap):
    kept = []
    for l in lights:
        if all((l["x"] - k["x"]) ** 2 + (l["z"] - k["z"]) ** 2 >= min_gap ** 2 for k in kept):
            kept.append(l)
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
    bands = shapely.STRtree([l.buffer(widths[c] / 2 + CAR_BODY_M[0] / 2, cap_style="flat") for _, c, _, l in pieces])
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
        w = widths[cls]
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
                dr.line([P(*c) for c in line.coords], fill=(60, 64, 72), width=max(1, int(ROAD_WIDTH[cls] * s)))
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
