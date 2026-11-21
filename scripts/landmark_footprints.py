"""The landmarks circle-game builds itself, whose footprints the district build leaves out.

The game models some buildings by hand (Blender by script, circle-game docs/<name>.md) and places each on its
OpenStreetMap footprint. The districts drew those footprints too, as generic massing, so the game masked the tiles at
load (circle-game src/world/landmarks.ts). build_district.py now leaves them out of every district instead, as it
does for Jamestown's lighthouse: no massing (walls, roofs, bulkheads, frame storeys, pools), no yard and so no gate in
a compound wall for them, no solid outline (the game adds the landmark's own colliders), so nothing for the game's
facade kit to dress either. They still keep the generated things off their ground (compound walls, parked cars,
hiding places), as before: the landmark stands there. Roads, ground and every other building stay as they were.

LANDMARKS is the one list, keyed by OpenStreetMap id. Overture's buildings carry their own ids, not OpenStreetMap's,
so the footprints are matched by shape: aoi/landmark-footprints.geojson holds each listed way's or relation's outline
as OpenStreetMap maps it (ODbL 1.0, ../ATTRIBUTION.md), and a district footprint is the landmark's when the two
overlap by at least MATCH_IOU of their union (Overture takes OpenStreetMap's buildings as they are: the overlap is
about 1). An entry may also name car parks ("sheds_on"): the small roofs Overture traced on them (Google's and
Microsoft's machine-learned footprints, not on OpenStreetMap) go too, when the landmark builds its car park itself.

The file is checked in, so a build needs no network and gives the same city each time; refresh it from the Ghana
extract after an id changes here:

    python3 scripts/landmark_footprints.py --pbf data/osm/ghana.osm.pbf

build_district.py --keep-landmarks builds as before, the generic massing on the landmarks.
"""
import argparse
import json
import os

import shapely
from shapely.geometry import shape

HERE = os.path.dirname(os.path.abspath(__file__))
FOOTPRINTS = os.path.join(HERE, "..", "aoi", "landmark-footprints.geojson")

# The landmarks circle-game builds (its docs/<name>.md and src/data/<name>.json name these ids). "ways" and
# "relations": the buildings themselves; "sheds_on": car parks (ways) whose sheds go with the landmark; "places": the
# named places on the landmark whose category the data has wrong, set right (fix_places()).
LANDMARKS = [
    # The bowl and the main stand only (circle-game docs/accra-stadium.md): the rest of compound 11526929 (its
    # offices, the gates, the hockey stadium beside it) stays generic.
    {"name": "Accra Sports Stadium", "ways": [169866131, 169866120]},
    # Jubilee House, the presidency (circle-game docs/jubilee-house.md): the relation, its outer way 33912638 round
    # eight courtyards. Overture's places also pin "The Flagstaff House" (the building's old name, OpenStreetMap's
    # alt_name) on it as lodging, which the maps drew as a hotel: it is the presidency, as Overture classes "Jubilee
    # House" itself.
    {"name": "Jubilee House", "relations": [7892588],
     "places": [{"name": "The Flagstaff House", "category": "government_office"}]},
    # Accra Mall (circle-game docs/accra-mall.md): the relation, outer way 444488219, and the eight sheds Overture
    # traced on its three car parks, which the game lays out itself with its bays, lamps and palms.
    {"name": "Accra Mall", "relations": [8418096], "sheds_on": [20918723, 1337970097, 1337970096]},
    # The Signature Apartments, 80 Spintex Road (circle-game docs/signature-apartments.md): its two towers.
    {"name": "The Signature Apartments", "ways": [1124774602, 1124774603]},
    # Black Star Square (circle-game docs/black-star-square.md): its stands (building=grandstand), its gate, and the
    # Independence Arch at the seaward end (docs/independence-arch.md: way 169866122, not the gate).
    {"name": "Black Star Square's stands", "ways": [169866121, 169866123, 169866124, 169866125, 169866127, 169866128,
                                                    169866129, 169866130]},
    {"name": "Black Star Gate", "ways": [169866126]},
    {"name": "Independence Arch", "ways": [169866122]},
    # Jamestown Lighthouse and James Fort (circle-game docs/jamestown-lighthouse.md): the tower (way 316143859), the
    # keeper's house and its rear block (272612102, 777917227) and the fort (177958818). The placeholder tower
    # build_district.py draws at the bible's point (lighthouse()) stays: the game folds it where it builds its own.
    {"name": "Jamestown Lighthouse and James Fort", "ways": [316143859, 272612102, 777917227, 177958818]},
]

# A district footprint is a listed building's when they overlap by this share of their union (intersection over
# union): Overture's copy of an OpenStreetMap building is the same polygon, and a neighbour that touches it shares
# almost nothing.
MATCH_IOU = 0.5
# A shed on a listed car park: a footprint whose middle lies on it, up to this size (m2). The mall's are 14 to 72 m2
# (Overture of 2025, Google's and Microsoft's traced roofs); anything larger on a car park would be something else.
SHED_MAX_M2 = 400.0


def osm_ids():
    """Every OpenStreetMap id the list names: "way/<id>" or "relation/<id>", with its landmark and role (building or
    car park)."""
    out = {}
    for lm in LANDMARKS:
        for w in lm.get("ways", []):
            out[f"way/{w}"] = (lm["name"], "building")
        for r in lm.get("relations", []):
            out[f"relation/{r}"] = (lm["name"], "building")
        for w in lm.get("sheds_on", []):
            out[f"way/{w}"] = (lm["name"], "car park")
    return out


def load(path=FOOTPRINTS):
    """The outlines of aoi/landmark-footprints.geojson for the ids LANDMARKS names (an id dropped from the list is
    dropped here, even before the file is refreshed): dicts with osm, landmark, role and geom (WGS84, lon/lat). An
    empty list without the file (the build then leaves nothing out)."""
    if not path or not os.path.exists(path):
        return []
    with open(path) as f:
        feats = json.load(f)["features"]
    want = osm_ids()
    out = []
    for ft in feats:
        p = ft["properties"]
        if p["osm"] not in want:
            continue
        g = shapely.make_valid(shape(ft["geometry"]))
        out.append({"osm": p["osm"], "landmark": want[p["osm"]][0], "role": want[p["osm"]][1], "geom": g})
    return out


class Matcher:
    """Tells which district footprints are the listed landmarks' (in the area's metres)."""

    def __init__(self, outlines, local):
        """outlines: load()'s list; local: WGS84 geometry -> the area's metres."""
        self.items = [(o["osm"], o["role"], local(o["geom"])) for o in outlines]
        self.tree = shapely.STRtree([g for _, _, g in self.items]) if self.items else None
        self.hits = {}                                   # osm id -> footprints matched

    def match(self, poly):
        """The OpenStreetMap id of the landmark footprint `poly` is (metres), or None."""
        if self.tree is None or poly.is_empty:
            return None
        mid = poly.representative_point()
        for j in self.tree.query(poly):
            osm, role, g = self.items[int(j)]
            if role == "car park":
                hit = poly.area <= SHED_MAX_M2 and g.contains(mid)
            else:
                inter = poly.intersection(g).area
                hit = inter > 0 and inter / poly.union(g).area >= MATCH_IOU
            if hit:
                self.hits[osm] = self.hits.get(osm, 0) + 1
                return osm
        return None


def fix_places(rows, outlines, reach_m=60.0):
    """Sets right the categories LANDMARKS gives ("places"): an Overture place (source "places") of that name within
    `reach_m` of its landmark's outline takes the category given. rows: places.load_places()'s dicts, changed in
    place. Returns the number of places changed."""
    fixes = [(f["name"], f["category"], lm["name"]) for lm in LANDMARKS for f in lm.get("places", [])]
    if not fixes or not rows:
        return 0
    deg = reach_m / 111_000.0
    near = {}
    for o in outlines:
        if o["role"] == "building":
            near[o["landmark"]] = near.get(o["landmark"], shapely.Point()).union(o["geom"].buffer(deg))
    n = 0
    for r in rows:
        if r.get("source") != "places":
            continue
        for name, category, lm in fixes:
            if r.get("name") == name and lm in near and near[lm].contains(shapely.Point(r["lon"], r["lat"])):
                r["category"] = category
                n += 1
    return n


def extract(pbf):
    """The outlines of every id LANDMARKS names, from an OpenStreetMap extract (pyosmium's areas: a closed way, or a
    multipolygon relation with its inner rings), as a GeoJSON FeatureCollection."""
    import osmium

    want = osm_ids()
    wkb = osmium.geom.WKBFactory()
    found = {}

    class Areas(osmium.SimpleHandler):
        def area(self, a):
            key = f"{'way' if a.from_way() else 'relation'}/{a.orig_id()}"
            if key in want:
                found[key] = (shapely.from_wkb(bytes.fromhex(wkb.create_multipolygon(a))), dict(a.tags))

    Areas().apply_file(pbf, locations=True)
    missing = sorted(set(want) - set(found))
    if missing:
        raise SystemExit(f"not in {pbf}: {', '.join(missing)}")
    feats = []
    for key in sorted(want, key=lambda k: (want[k][0], k)):
        g, tags = found[key]
        g = g.geoms[0] if len(g.geoms) == 1 else g
        feats.append({"type": "Feature",
                      "properties": {"osm": key, "landmark": want[key][0], "role": want[key][1],
                                     **{k: tags[k] for k in ("name", "building", "amenity") if k in tags}},
                      "geometry": json.loads(shapely.to_geojson(shapely.set_precision(g, 1e-7)))})
    return {"type": "FeatureCollection",
            "description": "Footprints of the landmarks circle-game builds itself (scripts/landmark_footprints.py). "
                           "OpenStreetMap contributors, ODbL 1.0.",
            "features": feats}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pbf", required=True, help="the Ghana extract (circle-game scripts/build-city.sh downloads it)")
    ap.add_argument("--out", default=FOOTPRINTS)
    args = ap.parse_args()
    fc = extract(args.pbf)
    with open(args.out, "w") as f:
        f.write('{"type": "FeatureCollection",\n "description": ' + json.dumps(fc["description"]) + ',\n "features": [\n')
        f.write(",\n".join(json.dumps(ft, separators=(",", ":")) for ft in fc["features"]))
        f.write("\n]}\n")
    print(f"{len(fc['features'])} outlines -> {args.out}")


if __name__ == "__main__":
    main()
