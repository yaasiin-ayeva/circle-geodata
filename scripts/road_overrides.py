"""Roads OpenStreetMap tags for the public street they are not: each listed way drawn in another class, at another width.

Overture's road segments take OpenStreetMap's class as it is, and build_district.py draws each class at one width
(ROAD_WIDTH). A compound's own drive tagged `highway=residential` with no `width` is then drawn as a 6 m street and
joins the traffic network, the parked cars and the hiding places of the side streets. OVERRIDES is the one list of
such ways, keyed by OpenStreetMap id: each gives the class the build treats the way as, and the width it draws it at
(road_width() reads it first, before the class's).

Overture's segments carry their own ids, not OpenStreetMap's, so they are matched by shape: aoi/road-overrides.geojson
holds each listed way's line as OpenStreetMap maps it (ODbL 1.0, ../ATTRIBUTION.md), and a segment is the way's when
at least MATCH_SHARE of the points along it lie within MATCH_M of the way's line (Overture takes OpenStreetMap's ways
as they are: the distance is about 0). The roads the segment meets keep their own class and width.

The file is checked in, so a build needs no network and gives the same city each time; refresh it from the Ghana
extract after an id changes here:

    python3 scripts/road_overrides.py --pbf data/osm/ghana.osm.pbf
"""
import argparse
import json
import os

import numpy as np
import shapely
from shapely.geometry import LineString, shape

HERE = os.path.dirname(os.path.abspath(__file__))
LINES = os.path.join(HERE, "..", "aoi", "road-overrides.geojson")

# The ways drawn otherwise than their tags say: "way" (OpenStreetMap id), "class" (one of build_district.py's
# ROAD_WIDTH classes), "width" (m, the drawn carriageway), "why".
OVERRIDES = [
    # The Gallery's own drive (circle-game docs/mall-surroundings.md, Follow-ups 7): a loop round the building inside
    # its compound off Nii Opey Street, tagged highway=residential (version 3, 29 Oct 2026) with no width or name.
    # Drawn as a residential street (6 m) its asphalt ran 1.6 m into the building's corner, which stands 1.4 m from
    # the way's line. A compound's drive: service (out of the traffic network, no parked cars or hiding places along
    # it), 3 m wide, one car's lane, so the corner stands at most 0.15 m in its edge (circle-game's tolerance for any
    # solid is 0.3 m). The director's decision of 23 Nov 2026 (docs/mall-surroundings.md, decision 4).
    {"way": 1230501216, "class": "service", "width": 3.0,
     "why": "The Gallery's compound drive (circle-game docs/mall-surroundings.md)"},
]

# A segment is a listed way's when this share of its points (every SAMPLE_M) lie within MATCH_M of the way's line.
MATCH_M = 2.0
MATCH_SHARE = 0.9
SAMPLE_M = 5.0


def load(path=LINES):
    """The lines of aoi/road-overrides.geojson for the ways OVERRIDES lists (a way dropped from the list is dropped
    here, even before the file is refreshed), each with its override: dicts with way, class, width and geom (WGS84,
    lon/lat). An empty list without the file (the build then draws every road by its class, as before)."""
    if not path or not os.path.exists(path):
        return []
    with open(path) as f:
        feats = json.load(f)["features"]
    want = {o["way"]: o for o in OVERRIDES}
    out = []
    for ft in feats:
        w = int(str(ft["properties"]["osm"]).split("/")[-1])
        if w in want:
            out.append({"way": w, "class": want[w]["class"], "width": float(want[w]["width"]), "geom": shape(ft["geometry"])})
    return out


def match(roads, lines, local):
    """The Overture segments that are the listed ways: {road id: (class, width, way)}. roads: (id, class, name, line
    in metres); lines: load()'s list; local: WGS84 geometry -> the area's metres."""
    if not lines:
        return {}
    items = [(o, local(o["geom"])) for o in lines]
    tree = shapely.STRtree([g for _, g in items])
    out = {}
    for rid, _cls, _name, g in roads:
        if not isinstance(g, LineString) or g.length < 1.0:
            continue
        ds = np.append(np.arange(0.0, g.length, SAMPLE_M), g.length)
        pts = [g.interpolate(d) for d in ds]
        for j in tree.query(g.buffer(MATCH_M)):
            o, wg = items[int(j)]
            near = sum(1 for p in pts if wg.distance(p) <= MATCH_M)
            if near >= MATCH_SHARE * len(pts):
                out[rid] = (o["class"], o["width"], o["way"])
                break
    return out


def extract(pbf):
    """The line of every way OVERRIDES lists, from an OpenStreetMap extract, as a GeoJSON FeatureCollection."""
    import osmium

    want = {o["way"] for o in OVERRIDES}
    found = {}

    class Ways(osmium.SimpleHandler):
        def way(self, w):
            if w.id in want:
                found[w.id] = ([(n.lon, n.lat) for n in w.nodes], dict(w.tags), w.version)

    Ways().apply_file(pbf, locations=True)
    missing = sorted(want - set(found))
    if missing:
        raise SystemExit(f"not in {pbf}: {', '.join(f'way/{w}' for w in missing)}")
    feats = []
    for o in OVERRIDES:
        coords, tags, version = found[o["way"]]
        feats.append({"type": "Feature",
                      "properties": {"osm": f"way/{o['way']}", "version": version,
                                     **{k: tags[k] for k in ("highway", "service", "name", "width") if k in tags}},
                      "geometry": json.loads(shapely.to_geojson(shapely.set_precision(LineString(coords), 1e-7)))})
    return {"type": "FeatureCollection",
            "description": "Lines of the roads drawn otherwise than their tags say (scripts/road_overrides.py). "
                           "OpenStreetMap contributors, ODbL 1.0.",
            "features": feats}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pbf", required=True, help="the Ghana extract (circle-game scripts/build-city.sh downloads it)")
    ap.add_argument("--out", default=LINES)
    args = ap.parse_args()
    fc = extract(args.pbf)
    with open(args.out, "w") as f:
        f.write('{"type": "FeatureCollection",\n "description": ' + json.dumps(fc["description"]) + ',\n "features": [\n')
        f.write(",\n".join(json.dumps(ft, separators=(",", ":")) for ft in fc["features"]))
        f.write("\n]}\n")
    print(f"{len(fc['features'])} lines -> {args.out}")


if __name__ == "__main__":
    main()
