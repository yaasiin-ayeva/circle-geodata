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
An entry may also name its grounds ("grounds": ways of a walled site the game lays out itself, its own wall, lawns and
drives): no generated compound wall, parked car or hiding place is put on them, so none stands across the game's lawns
or the drives in from the road. The buildings on the grounds stay as they are (only the listed ones go). "grounds_out"
grows an entry's grounds by that many metres, for a plot whose generated walls stand just outside its mapped edge.

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
# named places on the landmark whose category the data has wrong, set right (fix_places()); "grounds": the site the
# game lays out itself (ways), kept clear of the generated walls, cars and hiding places (grounds()).
LANDMARKS = [
    # The bowl and the main stand only (circle-game docs/accra-stadium.md): the rest of compound 11526929 (its
    # offices, the gates, the hockey stadium beside it) stays generic.
    {"name": "Accra Sports Stadium", "ways": [169866131, 169866120]},
    # Jubilee House, the presidency (circle-game docs/jubilee-house.md): the relation, its outer way 33912638 round
    # eight courtyards. Overture's places also pin "The Flagstaff House" (the building's old name, OpenStreetMap's
    # alt_name) on it as lodging, which the maps drew as a hotel: it is the presidency, as Overture classes "Jubilee
    # House" itself.
    # Its grounds, way 1574990678 (landuse=civic_admin, name=Jubilee House), are the game's (docs/jubilee-greenery.md:
    # the perimeter wall, the lawns, the ring drive, the two drives in from Liberation Road): the generated compound
    # walls stood along those drives and along Presidential Drive inside it, solid, where the photographs show lawn.
    {"name": "Jubilee House", "relations": [7892588], "grounds": [1574990678],
     "places": [{"name": "The Flagstaff House", "category": "government_office"}]},
    # Accra Mall (circle-game docs/accra-mall.md): the relation, outer way 444488219, and the eight sheds Overture
    # traced on its three car parks, which the game lays out itself with its bays, lamps and palms.
    {"name": "Accra Mall", "relations": [8418096], "sheds_on": [20918723, 1337970097, 1337970096]},
    # The Signature Apartments, 80 Spintex Road (circle-game docs/signature-apartments.md): its two towers. Its plot,
    # way 1124774601 (landuse=residential), is the game's forecourt (docs/mall-surroundings.md: the wall and railing on
    # Nii Opey Street, the steel fence, the kiosk, open at two gates): the generated compound walls stood along its
    # boundary, from 2.1 m inside it to 3.7 m outside, one across each gate. Grown 1.5 m ("grounds_out"), with the
    # 3 m the walls keep off any grounds, those go; the neighbours' walls 6 m and more out stay.
    {"name": "The Signature Apartments", "ways": [1124774602, 1124774603], "grounds": [1124774601], "grounds_out": 1.5},
    # Accra Mall's surroundings by the Tetteh Quarshie Interchange (circle-game docs/mall-surroundings.md): the
    # Villaggio Vista towers (Alto 608141167, Azure 608141164 and its strip 608141163, its wing 608141162, Aqua
    # 608141169; their building:part ways are not buildings), City Galleria (1137453837), the African Regent Hotel
    # (512899518), The Gallery (741876793) and the commercial block on Tetteh Quarshie Road (628661574). The Accra Mall
    # Bus Terminal's yard (300519834, amenity=bus_station) is the game's too: its islands, lanes and trotros.
    {"name": "Accra Mall's surroundings", "ways": [608141167, 608141164, 608141163, 608141162, 608141169, 1137453837,
                                                   512899518, 741876793, 628661574], "grounds": [300519834]},
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
    # Osu Castle, Fort Christiansborg (circle-game docs/osu-castle.md): the relation (tagged only building=office),
    # outer way 557305324 round its courtyard 630806386.
    {"name": "Osu Castle", "relations": [8776513]},
    # Ussher Fort, Fort Crèvecœur (circle-game docs/ussher-fort.md): OpenStreetMap maps no outline of the fort, only
    # the 17 buildings within its walls (the ranges against the High Street front, the gate's passage, the cell blocks,
    # the former chapel, the sea range, the blocks of the south-west corner); the game walls them itself.
    {"name": "Ussher Fort", "ways": [883616363, 776059828, 776059829, 776059830, 776059839, 776059838, 776442379, 776442380,
                                     776442381, 776442382, 883616359, 776442383, 776442378, 776442384, 883616361, 883616360,
                                     889632301]},
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
        for w in lm.get("grounds", []):
            out[f"way/{w}"] = (lm["name"], "grounds")
    return out


def grounds(outlines, local):
    """The landmarks' grounds (load()'s outlines of role "grounds") in the area's metres: what the generated compound
    walls, parked cars and hiding places keep off, each grown by its landmark's "grounds_out" (m, none by default). No
    building is matched to them (Matcher)."""
    out_m = {lm["name"]: float(lm.get("grounds_out", 0.0)) for lm in LANDMARKS}
    out = []
    for o in outlines:
        if o["role"] != "grounds":
            continue
        g = local(o["geom"])
        d = out_m.get(o.get("landmark"), 0.0)
        if d > 0 and not g.is_empty:
            g = g.buffer(d, join_style="mitre", mitre_limit=3.0)
        if not g.is_empty:
            out.append(g)
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
        self.items = [(o["osm"], o["role"], local(o["geom"])) for o in outlines if o["role"] != "grounds"]
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
