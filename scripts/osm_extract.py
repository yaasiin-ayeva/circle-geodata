"""Every point of interest OpenStreetMap maps inside an area: osm.parquet next to the Overture slice, for the game's
maps (places.py) and its garages (build_district.py "garages").

    python3 scripts/osm_extract.py --pbf data/osm/ghana.osm.pbf --name east-accra --out data/work/slice-east
    python3 scripts/osm_extract.py --pbf data/osm/ghana.osm.pbf --to vertical-slice:data/work/slice \
        --to east-accra:data/work/slice-east       # several areas in one read of the extract

The extract is a country file in OSM's PBF format (OSM_PBF_URL in circle-game's scripts/build-city.sh: the Ghana
extract of download.openstreetmap.fr, refreshed daily; Geofabrik's works the same). One row per mapped feature that
carries one of KEYS (amenity, shop, tourism, leisure, office, craft, healthcare, historic, public_transport, club,
emergency, man_made=tower|lighthouse, sport): nodes as they are, closed ways and multipolygons at a point inside their
outline, with the outline (WKB) and its area. Named or not: the maps draw the named ones, the garages keep the others
too. Columns: id ("node/123", "way/45", "relation/6"), name, key and value (the first of KEYS the feature carries),
brand (true when OpenStreetMap names a brand: brand, brand:wikidata or operator:wikidata), lon, lat, area_m2, outline,
and the raw tags of note (shop, amenity, craft, service:vehicle:*) as "tags" JSON.

Data (c) OpenStreetMap contributors, ODbL 1.0 (SOURCES.md): the game's credits already carry it.

Needs pyosmium (requirements-district.txt), shapely, pyarrow. Offline test: tests/test_osm_extract.py.
"""
import argparse
import json
import math
import os
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from shapely.geometry import Point, Polygon, MultiPolygon

KEYS = ("amenity", "shop", "craft", "healthcare", "tourism", "leisure", "office", "historic", "public_transport",
        "club", "emergency", "sport", "man_made")
# man_made values worth a pin; the rest (pipelines, water tanks, surveillance) are not places.
MAN_MADE = {"tower", "lighthouse", "water_tower", "monument"}
# amenity values that are street furniture, not places a driver looks for.
FURNITURE = {"bench", "waste_basket", "waste_disposal", "recycling", "parking_space", "bicycle_parking", "vending_machine",
             "drinking_water", "toilets", "shelter", "telephone", "post_box", "clock", "grit_bin", "loading_dock",
             "water_point", "watering_place", "hunting_stand", "parking_entrance", "motorcycle_parking"}
NOTE = ("shop", "amenity", "craft", "service", "building")

SCHEMA = pa.schema([("id", pa.string()), ("name", pa.string()), ("key", pa.string()), ("value", pa.string()),
                    ("brand", pa.bool_()), ("lon", pa.float64()), ("lat", pa.float64()), ("area_m2", pa.float64()),
                    ("outline", pa.binary()), ("tags", pa.string())])


def load_aoi(path, name):
    for feat in json.load(open(path))["features"]:
        p = feat["properties"]
        if p["name"] == name:
            return p["west"], p["south"], p["east"], p["north"]
    sys.exit(f"AOI '{name}' not found in {path}")


def first_key(tags):
    """The first of KEYS a feature carries, with its value, or None when it is no place (street furniture, a
    man_made that is not a landmark, a disused or abandoned feature)."""
    if any(k.startswith(("disused:", "abandoned:")) for k in tags) and not any(k in tags for k in KEYS):
        return None
    for k in KEYS:
        v = tags.get(k)
        if not v or v == "no" or (v == "yes" and k not in ("shop", "office", "craft", "tourism")):
            continue
        if k == "amenity" and v in FURNITURE:
            continue
        if k == "man_made" and v not in MAN_MADE:
            continue
        if k == "public_transport" and v not in ("station",):
            continue
        return k, v
    return None


def is_brand(tags):
    return any(k in tags for k in ("brand", "brand:wikidata", "operator:wikidata"))


def area_m2(poly, lat):
    """The outline's area in m2, from degrees at this latitude (good to a fraction of a percent at Accra's size)."""
    k = 111_320.0
    return abs(poly.area) * k * k * math.cos(math.radians(lat))


def row(fid, tags, lon, lat, outline=None):
    kv = first_key(tags)
    if kv is None:
        return None
    note = {k: v for k, v in tags.items() if k in NOTE or k.startswith("service:vehicle")}
    return {"id": fid, "name": tags.get("name") or tags.get("name:en") or "", "key": kv[0], "value": kv[1],
            "brand": is_brand(tags), "lon": round(lon, 7), "lat": round(lat, 7),
            "area_m2": round(area_m2(outline, lat), 1) if outline is not None else 0.0,
            "outline": shapely.to_wkb(outline) if outline is not None else None,
            "tags": json.dumps(note, separators=(",", ":"), sort_keys=True)}


def extract(pbf, box):
    """The rows of one area: extract_many() for one box."""
    return extract_many(pbf, [box])[0]


def extract_many(pbf, boxes):
    """The rows of each box (west, south, east, north), in one read of the extract: a list of lists of rows."""
    import osmium

    outs = [[] for _ in boxes]
    w = min(b[0] for b in boxes); s = min(b[1] for b in boxes); e = max(b[2] for b in boxes); n = max(b[3] for b in boxes)

    class Sink:
        def append(self, r):
            for (bw, bs, be, bn), o in zip(boxes, outs):
                if bw <= r["lon"] <= be and bs <= r["lat"] <= bn:
                    o.append(r)

    inside = lambda lon, lat: w <= lon <= e and s <= lat <= n
    out = Sink()

    class Handler(osmium.SimpleHandler):
        def node(self, nd):
            if not nd.tags or not nd.location.valid():
                return
            lon, lat = nd.location.lon, nd.location.lat
            if inside(lon, lat):
                r = row(f"node/{nd.id}", dict(nd.tags), lon, lat)
                if r:
                    out.append(r)

        def area(self, a):
            if not a.tags:
                return
            tags = dict(a.tags)
            if first_key(tags) is None:
                return
            polys = []
            try:
                for outer in a.outer_rings():
                    ring = [(p.lon, p.lat) for p in outer]
                    holes = [[(p.lon, p.lat) for p in inner] for inner in a.inner_rings(outer)]
                    if len(ring) >= 4:
                        polys.append(Polygon(ring, holes))
            except osmium.InvalidLocationError:
                return
            if not polys:
                return
            g = shapely.make_valid(polys[0] if len(polys) == 1 else MultiPolygon(polys))
            if g.is_empty:
                return
            pt = g.representative_point()
            if not inside(pt.x, pt.y):
                return
            fid = f"way/{a.orig_id()}" if a.from_way() else f"relation/{a.orig_id()}"
            r = row(fid, tags, pt.x, pt.y, g)
            if r:
                out.append(r)

    Handler().apply_file(pbf, locations=True, idx="flex_mem")
    return outs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pbf", required=True, help="an OpenStreetMap extract (.osm.pbf) that covers the area")
    ap.add_argument("--aoi", default="aoi/slices.geojson")
    ap.add_argument("--name", default="vertical-slice", help="the area in --aoi")
    ap.add_argument("--out", help="the slice's folder: writes osm.parquet there")
    ap.add_argument("--to", action="append", default=[], metavar="NAME:FOLDER",
                    help="an area and its slice's folder, instead of --name and --out; repeat for several areas")
    args = ap.parse_args()
    pairs = [t.split(":", 1) for t in args.to] or [(args.name, args.out)]
    if any(not o for _, o in pairs):
        ap.error("--out or --to is required")
    for (name, folder), rows in zip(pairs, extract_many(args.pbf, [load_aoi(args.aoi, n) for n, _ in pairs])):
        rows.sort(key=lambda r: r["id"])
        os.makedirs(folder, exist_ok=True)
        # Written whole or not at all (#16): a temporary name, renamed once complete.
        out = os.path.join(folder, "osm.parquet")
        pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), out + ".tmp", compression="zstd")
        os.replace(out + ".tmp", out)
        named = sum(1 for r in rows if r["name"])
        print(f"osm.parquet: {len(rows)} features ({named} named) in {name}, into {folder}")


if __name__ == "__main__":
    main()
