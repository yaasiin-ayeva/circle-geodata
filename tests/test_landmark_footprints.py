"""Checks the landmarks the game builds itself, left out of the districts (scripts/landmark_footprints.py): the list and
its outlines, the match of a district footprint to them, the places set right, and a district built on synthetic
data with and without the list (build_district.py --keep-landmarks), where only the listed footprints may differ:
roads, ground, lights, the traffic network and every other building stay the same.

    python3 tests/test_landmark_footprints.py
    python3 tests/test_landmark_footprints.py --before DIR --after DIR --name AREA   # two real builds, compared

The second form compares two builds of the same area made from a real slice, with --keep-landmarks and without.
"""
import argparse
import json
import math
import os
import sys
import tempfile
from collections import Counter, defaultdict

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer
from shapely.geometry import LineString, Point, Polygon, box

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import build_district as bd  # noqa: E402
import landmark_footprints as lf  # noqa: E402
import massing  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS  " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    failures += 0 if ok else 1


# What a removed landmark may change beyond its own footprint: the compound walls (and so the hiding places beside
# them) of the yards round a landmark that would have stood in one, within this distance (m) of it.
NEAR_M = 40.0
WALL_MATS = {m for m, _ in massing.BOUNDARY_PAINTS} | {"M_Gate"}


def read_obj(path):
    """The OBJ's triangles by object: {object: Counter((material, (east, north, up) x 3))}, rounded to the cm."""
    out = defaultdict(Counter)
    obj = mat = None
    vs = []
    with open(path) as f:
        for line in f:
            if line.startswith("v "):
                x, y, z = map(float, line.split()[1:])
                vs.append((round(-x, 2), round(z, 2), round(y, 2)))
            elif line.startswith("f "):
                out[obj][(mat, tuple(vs[-3:]))] += 1
            elif line.startswith("o "):
                obj = line.split(None, 1)[1].strip()
            elif line.startswith("usemtl "):
                mat = line.split(None, 1)[1].strip()
    return out


def local_outlines(meta, outlines):
    """The landmarks' outlines in the area's metres (x east, z north), from its JSON's origin."""
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True)
    oe, on = meta["origin_utm30n"]["e"], meta["origin_utm30n"]["n"]
    return [shapely.transform(o["geom"], lambda xy: np.column_stack(to_utm.transform(xy[:, 0], xy[:, 1])) - (oe, on))
            for o in outlines]


def compare(before_dir, after_dir, name, label=None):
    """Compares a build with --keep-landmarks (before) and one without (after) of the same area. Returns a summary."""
    label = label or name
    b_meta = json.load(open(os.path.join(before_dir, name + ".json")))
    a_meta = json.load(open(os.path.join(after_dir, name + ".json")))
    outlines = lf.load()
    shapes = [o for o in local_outlines(b_meta, outlines)]
    tight = shapely.union_all([s.buffer(1.0) for s in shapes])          # the footprint, its overhangs and fascias
    near = shapely.union_all([s.buffer(NEAR_M) for s in shapes])
    inner = shapely.union_all([s.buffer(-0.5) for s in shapes])
    for g in (tight, near, inner):
        shapely.prepare(g)

    # The meshes: every object but the buildings' cells the same; in those, the triangles taken away stand on a
    # landmark, or are compound walls and gates near one; none added but such walls.
    bo, ao = read_obj(os.path.join(before_dir, name + ".obj")), read_obj(os.path.join(after_dir, name + ".obj"))
    other = sorted(k for k in set(bo) | set(ao) if not (k or "").startswith("Buildings_"))
    same_other = all(bo.get(k) == ao.get(k) for k in other)
    check(f"{label}: roads, ground and every other object the same", same_other,
          ", ".join(k for k in other if bo.get(k) != ao.get(k))[:200])
    removed, added = Counter(), Counter()
    for k in set(bo) | set(ao):
        if (k or "").startswith("Buildings_"):
            removed += bo.get(k, Counter()) - ao.get(k, Counter())
            added += ao.get(k, Counter()) - bo.get(k, Counter())

    def mid(tri):
        return Point(sum(p[0] for p in tri) / 3, sum(p[1] for p in tri) / 3)

    def wall_near(key):
        mat, tri = key
        return mat in WALL_MATS and max(p[2] for p in tri) <= massing.WALL_H + 0.36 and near.contains(mid(tri))

    on_lm = sum(n for key, n in removed.items() if tight.contains(mid(key[1])))
    walls_gone = sum(n for key, n in removed.items() if not tight.contains(mid(key[1])) and wall_near(key))
    stray = [key for key in removed if not tight.contains(mid(key[1])) and not wall_near(key)]
    check(f"{label}: the triangles taken away stand on a landmark, or are walls near one", not stray,
          f"{len(stray)} elsewhere, first at {mid(stray[0][1]).coords[0] if stray else ''}")
    check(f"{label}: nothing added but compound walls near a landmark", all(wall_near(k) for k in added),
          f"{sum(1 for k in added if not wall_near(k))} elsewhere")
    left = [key for o, c in ao.items() if (o or "").startswith("Buildings_") for key in c
            if inner.contains(mid(key[1])) and key[0] not in WALL_MATS | {"M_Concrete_Raw", "M_Pool"}]
    check(f"{label}: no building drawn on a landmark after", not left, f"{len(left)} triangles")

    # The JSON: the same but for the solid outlines on the landmarks, the stats, the ids left out, the places set
    # right; parked cars and hiding places the same but near a landmark whose yard walls went.
    skip = {"stats", "solid", "places", "landmarksLeftOut", "parked", "hiding"}
    diff = [k for k in set(b_meta) | set(a_meta) if k not in skip and b_meta.get(k) != a_meta.get(k)]
    check(f"{label}: roads, lights, lap, medians, garages, fuel and the rest of the JSON the same", not diff, ", ".join(diff))
    bs = Counter(tuple(r) for r in b_meta["solid"]["buildings"])
    as_ = Counter(tuple(r) for r in a_meta["solid"]["buildings"])

    def ring_mid(r):
        return Point(sum(r[0::2]) / (len(r) // 2), sum(r[1::2]) / (len(r) // 2))

    gone = bs - as_
    check(f"{label}: no solid outline added", not (as_ - bs))
    check(f"{label}: the solid outlines taken away are the landmarks'", all(tight.contains(ring_mid(r)) for r in gone),
          f"{sum(1 for r in gone if not tight.contains(ring_mid(r)))} elsewhere")
    check(f"{label}: no solid outline left on a landmark",
          not any(inner.contains(ring_mid(r)) and Polygon(list(zip(r[0::2], r[1::2]))).area > 0.5 * s.area
                  for r in as_ for s in shapes if s.contains(ring_mid(r))))
    bw, aw = Counter(map(tuple, b_meta["solid"]["walls"])), Counter(map(tuple, a_meta["solid"]["walls"]))
    wdiff = list((bw - aw) + (aw - bw))
    check(f"{label}: compound walls changed only near a landmark",
          all(near.contains(LineString(list(zip(w[0::2], w[1::2])))) for w in wdiff), f"{len(wdiff)} changed")
    for key in ("parked", "hiding"):
        bk = Counter(json.dumps(x, sort_keys=True) for x in b_meta.get(key, []))
        ak = Counter(json.dumps(x, sort_keys=True) for x in a_meta.get(key, []))
        moved = [json.loads(x) for x in list((bk - ak) + (ak - bk))]
        check(f"{label}: {key} changed only near a landmark", all(near.contains(Point(x["x"], x["z"])) for x in moved),
              f"{len(moved)} changed")
    # The places by name and position (a category set right changes its rank, and so the order).
    fixed = {f["name"] for lm in lf.LANDMARKS for f in lm.get("places", [])}
    bp = {(p["name"], p["x"], p["z"]): p for p in b_meta.get("places", [])}
    ap_ = {(p["name"], p["x"], p["z"]): p for p in a_meta.get("places", [])}
    pd = [(bp[k], ap_[k]) for k in bp if k in ap_ and bp[k] != ap_[k]]
    check(f"{label}: the places the same but those set right",
          bp.keys() == ap_.keys() and all(p["name"] in fixed and {**p, "cat": q["cat"], "rank": q["rank"]} == q for p, q in pd),
          f"{len(set(bp) ^ set(ap_))} added or gone, {len(pd)} changed")
    left_out = a_meta.get("landmarksLeftOut", [])
    check(f"{label}: the ids left out are the list's", set(left_out) <= set(lf.osm_ids()), ", ".join(left_out))
    return {"buildings": (b_meta["stats"]["buildings"], a_meta["stats"]["buildings"]),
            "solid": (sum(bs.values()), sum(as_.values())), "triangles_on_landmarks": on_lm, "wall_triangles": walls_gone,
            "walls_changed": len(wdiff), "left_out": left_out, "places_fixed": [q["name"] + ": " + p["cat"] + " -> " + q["cat"] for p, q in pd]}


def unit_checks():
    ids = lf.osm_ids()
    outlines = lf.load()
    check("every listed id has its outline in aoi/landmark-footprints.geojson", set(ids) == {o["osm"] for o in outlines},
          ", ".join(sorted(set(ids) - {o["osm"] for o in outlines})))
    check("no id listed twice", sum(len(lm.get(k, [])) for lm in lf.LANDMARKS for k in ("ways", "relations", "sheds_on")) == len(ids))
    check("the Independence Arch is way 169866122, the Black Star Gate way 169866126",
          ids.get("way/169866122", ("",))[0] == "Independence Arch" and ids.get("way/169866126", ("",))[0] == "Black Star Gate")
    check("the stadium's bowl and main stand, not its compound",
          "way/169866131" in ids and "way/169866120" in ids and "way/11526929" not in ids)
    check("Black Star Square's eight stands", sum(1 for k, v in ids.items() if v[0] == "Black Star Square's stands") == 8)
    check("each outline is a valid polygon", all(o["geom"].is_valid and o["geom"].area > 0 for o in outlines))
    jh = next((o for o in outlines if o["osm"] == "relation/7892588"), None)
    check("Jubilee House keeps its courtyards", jh is not None and sum(len(p.interiors) for p in getattr(jh["geom"], "geoms", [jh["geom"]])) == 8)

    # The match, on shapes in metres.
    m = lf.Matcher([{"osm": "way/1", "role": "building", "geom": box(0, 0, 40, 20)},
                    {"osm": "way/2", "role": "car park", "geom": box(100, 0, 200, 50)}], lambda g: g)
    check("the same footprint, a little simplified, is the landmark's", m.match(box(0.1, 0, 40, 19.9)) == "way/1")
    check("a neighbour touching it is not", m.match(box(40, 0, 50, 10)) is None)
    check("nor a small building inside a large outline", m.match(box(5, 5, 10, 10)) is None)
    check("a shed on a listed car park is", m.match(box(120, 10, 128, 16)) == "way/2")
    check("a shed half off it, its middle outside, is not", m.match(box(196, 10, 210, 16)) is None)
    check("a large building on a car park is not", m.match(box(110, 5, 190, 45)) is None)

    # The places set right: on Jubilee House, not elsewhere, not OpenStreetMap's.
    jlon, jlat = jh["geom"].representative_point().coords[0] if jh else (0, 0)
    rows = [{"source": "places", "name": "The Flagstaff House", "category": "lodging", "lon": jlon, "lat": jlat},
            {"source": "places", "name": "The Flagstaff House", "category": "lodging", "lon": jlon + 0.01, "lat": jlat},
            {"source": "osm", "name": "The Flagstaff House", "category": "lodging", "lon": jlon, "lat": jlat}]
    n = lf.fix_places(rows, outlines)
    check("The Flagstaff House on Jubilee House is no hotel", rows[0]["category"] == "government_office" and n == 1)
    check("a namesake 1 km away keeps its category", rows[1]["category"] == "lodging")


def synthetic_slice(path):
    """A slice round Black Star Square: the Arch, the Gate and the stands as Overture has them (its copy of
    OpenStreetMap's ways), small houses along two streets, a main road; a place on the square."""
    outlines = [o for o in lf.load() if o["role"] == "building" and o["geom"].intersects(box(*SYNTH_BOX))]
    rows = [{"id": f"ov-{o['osm']}", "source": "OpenStreetMap", "height": 30.0 if o["osm"] == "way/169866122" else None,
             "num_floors": None, "class": None, "subtype": None, "name": None, "geometry": shapely.to_wkb(o["geom"])}
            for o in outlines]
    keep_off = shapely.union_all([o["geom"] for o in outlines]).buffer(0.00003)
    lat0, lon0 = 5.5455, -0.1945
    dlat, dlon = 7 / 111_000, 9 / 110_600
    k = 0
    for street in (5.5470, 5.5492):
        for side in (1, -1):
            for i in range(24):
                lon = lon0 + 0.0002 + i * 0.000135
                lat = street + side * 0.00012
                g = box(lon, lat, lon + dlon, lat + side * dlat) if side > 0 else box(lon, lat - dlat, lon + dlon, lat)
                if g.intersects(keep_off):
                    continue
                k += 1
                rows.append({"id": f"house-{k}", "source": "Google Open Buildings", "height": None, "num_floors": None,
                             "class": None, "subtype": None, "name": None, "geometry": shapely.to_wkb(g)})
    pq.write_table(pa.Table.from_pylist(rows), os.path.join(path, "buildings.parquet"))
    # A block of four streets, joined at their ends (the lap runs down Square Street and back round the block).
    a, b, c, d = (-0.1942, 5.5470), (-0.1905, 5.5470), (-0.1905, 5.5492), (-0.1942, 5.5492)
    roads = [("r1", "residential", "", LineString([a, b])),
             ("p1", "primary", "Independence Avenue", LineString([b, c])),
             ("r2", "residential", "", LineString([c, d])),
             ("r3", "residential", "Square Street", LineString([d, a]))]
    pq.write_table(pa.Table.from_pylist([{"id": i, "class": c, "name": n, "geometry": shapely.to_wkb(g)} for i, c, n, g in roads]),
                   os.path.join(path, "roads.parquet"))
    pq.write_table(pa.Table.from_pylist([{"id": "p1", "source": "places", "name": "Independence Square", "category": "monument",
                                          "confidence": 0.9, "status": "open", "brand": None, "websites": 1, "socials": 0,
                                          "phones": 0, "datasets": ["meta"], "area_m2": 0.0,
                                          "geometry": shapely.to_wkb(Point(-0.1925, 5.5478))}]),
                   os.path.join(path, "places.parquet"))
    return len(outlines), k


SYNTH_BOX = (-0.1945, 5.5455, -0.1900, 5.5500)       # west, south, east, north


def synthetic_build():
    bd.AREAS["test-black-star"] = {"south": SYNTH_BOX[1], "north": SYNTH_BOX[3], "west": SYNTH_BOX[0], "east": SYNTH_BOX[2],
                                   "origin": bd.OSU_ORIGIN, "lap": ("Square Street", 5.5470), "district": "ring-road"}
    with tempfile.TemporaryDirectory() as tmp:
        sl = os.path.join(tmp, "slice")
        os.makedirs(sl)
        n_lm, n_houses = synthetic_slice(sl)
        outs = {}
        for mode, keep in (("before", True), ("after", False)):
            out = os.path.join(tmp, mode)
            args = argparse.Namespace(slice=sl, name="test-black-star", out=out, exclude=None, elevated="", no_massing=False,
                                      no_ev=True, ev_documented="", roofs=None, no_fuel=False, keep_landmarks=keep,
                                      landmark_footprints=lf.FOOTPRINTS)
            bd.build(args)
            outs[mode] = out
        s = compare(outs["before"], outs["after"], "test-black-star", "synthetic")
        a = json.load(open(os.path.join(outs["after"], "test-black-star.json")))
        b = json.load(open(os.path.join(outs["before"], "test-black-star.json")))
        check("synthetic: the landmark footprints drawn before", b["stats"]["buildings"] == n_lm + n_houses,
              f"{b['stats']['buildings']} of {n_lm} + {n_houses}")
        check("synthetic: and left out after, every house kept", a["stats"]["buildings"] == n_houses,
              f"{a['stats']['buildings']} of {n_houses}")
        check("synthetic: the ids left out named in the district's JSON", len(a.get("landmarksLeftOut", [])) == n_lm,
              ", ".join(a.get("landmarksLeftOut", [])))
        check("synthetic: --keep-landmarks names none", "landmarksLeftOut" not in b)
        check("synthetic: the landmarks' triangles went", s["triangles_on_landmarks"] > 0, str(s["triangles_on_landmarks"]))
        # (one or more each: solid_outlines() cuts a footprint the streets cross into its pieces)
        check("synthetic: their solid outlines went", s["solid"][0] - s["solid"][1] >= n_lm, f"{s['solid']}")
    del bd.AREAS["test-black-star"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", help="a build with --keep-landmarks")
    ap.add_argument("--after", help="the same area's build without")
    ap.add_argument("--name", help="the area's name (its files' names)")
    args = ap.parse_args()
    if args.before:
        print(json.dumps(compare(args.before, args.after, args.name)))
    else:
        unit_checks()
        synthetic_build()
    print("landmark footprints: " + ("all checks passed" if not failures else f"{failures} failed"))
    sys.exit(1 if failures else 0)
