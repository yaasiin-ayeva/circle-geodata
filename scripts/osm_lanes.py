"""Fetch the lane counts OpenStreetMap maps on the main roads of the CIRCLE areas, into aoi/osm-lanes.geojson.

Overture's road segments carry no lane count, so the build drew every carriageway of a boulevard two lanes wide: 7 m,
or a width guessed from the gap between its two carriageways. OpenStreetMap tags most of Accra's main roads with
`lanes` (Liberation Road: 3 each way; the Ring Road: 2), and build_district.py's road_width() reads them from this file,
matched to each Overture segment by name, direction and distance (match_lanes()).

The file is checked in, so a build needs no network and gives the same city each time; run this again to refresh it:

    python3 scripts/osm_lanes.py                      # every area in aoi/slices.geojson
    python3 scripts/osm_lanes.py --out aoi/osm-lanes.geojson

OpenStreetMap data is ODbL 1.0 (../ATTRIBUTION.md already credits it).
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
HIGHWAYS = "motorway|trunk|primary|secondary|tertiary|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link"
KEEP = ("name", "highway", "oneway", "lanes", "lanes:forward", "lanes:backward", "width")


def union_box(path):
    with open(path) as f:
        fc = json.load(f)
    ps = [ft["properties"] for ft in fc["features"]]
    return min(p["south"] for p in ps), min(p["west"] for p in ps), max(p["north"] for p in ps), max(p["east"] for p in ps)


def fetch(box, tries=3):
    s, w, n, e = box
    query = f'[out:json][timeout:120];way["highway"~"^({HIGHWAYS})$"]["lanes"]({s},{w},{n},{e});out tags geom;'
    body = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for attempt in range(tries):
        for url in ENDPOINTS:
            try:
                req = urllib.request.Request(url, data=body, headers={"User-Agent": "circle-geodata osm_lanes.py"})
                with urllib.request.urlopen(req, timeout=150) as r:
                    return json.load(r)
            except Exception as ex:  # an endpoint busy or down: try the next one
                last = ex
                print(f"  {url}: {ex}", file=sys.stderr)
        time.sleep(5 * (attempt + 1))
    sys.exit(f"Overpass unreachable: {last}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aoi", default="aoi/slices.geojson")
    ap.add_argument("--out", default="aoi/osm-lanes.geojson")
    args = ap.parse_args()
    box = union_box(args.aoi)
    data = fetch(box)
    feats = []
    for el in sorted(data.get("elements", []), key=lambda x: x["id"]):
        geom = el.get("geometry") or []
        if el.get("type") != "way" or len(geom) < 2:
            continue
        props = {"osm_id": el["id"], **{k: el["tags"][k] for k in KEEP if k in el["tags"]}}
        coords = [[round(p["lon"], 6), round(p["lat"], 6)] for p in geom]
        feats.append({"type": "Feature", "properties": props, "geometry": {"type": "LineString", "coordinates": coords}})
    out = {"type": "FeatureCollection", "name": "osm-lanes",
           "source": "© OpenStreetMap contributors, ODbL 1.0, via the Overpass API",
           "fetched": time.strftime("%Y-%m-%d"), "bbox": [box[1], box[0], box[3], box[2]], "features": feats}
    with open(args.out, "w") as f:
        json.dump(out, f, separators=(",", ":"))
        f.write("\n")
    print(f"wrote {len(feats)} ways with a lane count to {args.out}")


if __name__ == "__main__":
    main()
