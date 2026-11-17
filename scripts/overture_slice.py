"""Count or extract Overture Maps buildings and roads inside a CIRCLE area of interest.

Examples
--------
Count buildings by source and roads by class for the vertical slice:

    python3 scripts/overture_slice.py count --release latest

Extract both layers to GeoParquet:

    python3 scripts/overture_slice.py export --release 2026-10-22.0 --out data/work/slice

The release name comes from https://docs.overturemaps.org/release/ (pick the
latest; the value above is only an example). Data is read straight from
Overture's public S3 bucket, so the machine needs internet access.
Use --source to point at a local copy of the release instead.

Reading uses pyarrow (S3 read anonymously) and shapely/pyproj for the clip and the lengths: nothing is downloaded at
run time but the data itself, unlike DuckDB's httpfs and spatial extensions, which DuckDB fetched from its own server on
each new machine (blocked where only the data hosts are open, and unpinned).

Overture buildings and roads are ODbL: see ../ATTRIBUTION.md.
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

S3_BUCKET = "overturemaps-us-west-2/release"
S3_REGION = "us-west-2"
LAYERS = {
    "buildings": "theme=buildings/type=building",
    "roads": "theme=transportation/type=segment",
    "places": "theme=places/type=place",
    "land_use": "theme=base/type=land_use",
    "infrastructure": "theme=base/type=infrastructure",
}
# What `export` writes (--layers picks some): buildings.parquet, roads.parquet, and places.parquet, the named places
# for the game's maps: Overture places, plus OpenStreetMap's named land use (hospitals, campuses, stadiums, parks, with
# their area) and its transport stations, from Overture's base theme. scripts/places.py chooses among them.
EXPORTS = ("buildings", "roads", "places")
# The base theme's classes that name a place a driver knows (land use by its area, stations by their name).
BASE_CLASSES = {
    "land_use": ("hospital", "clinic", "university", "college", "school", "stadium", "park", "plaza", "cemetery",
                 "military", "barracks", "military_school", "retail", "marketplace", "beach_resort", "zoo", "golf_course"),
    "infrastructure": ("bus_station", "railway_station", "international_airport", "airport", "ferry_terminal"),
}


def load_aoi(path, name):
    with open(path) as f:
        fc = json.load(f)
    for feat in fc["features"]:
        if feat["properties"]["name"] == name:
            p = feat["properties"]
            return p["west"], p["south"], p["east"], p["north"]
    sys.exit(f"AOI '{name}' not found in {path}")


LIST_URL = (
    "https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/"
    "?list-type=2&prefix=release/&delimiter=/"
)


def parse_releases(xml_text):
    """Release names from an S3 ListObjectsV2 response, oldest first."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_text)
    names = []
    for el in root.iter():
        if el.tag.endswith("Prefix") and el.text and el.text.startswith("release/"):
            name = el.text[len("release/"):].strip("/")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+", name):
                names.append(name)
    return sorted(set(names))


def latest_release():
    from urllib.request import urlopen

    with urlopen(LIST_URL, timeout=30) as resp:
        names = parse_releases(resp.read().decode("utf-8"))
    if not names:
        sys.exit("Could not find any Overture release; pass --release explicitly")
    return names[-1]


def source_dataset(args, layer):
    """The layer as a pyarrow dataset: a local copy (--source) or Overture's public bucket, read anonymously whatever
    AWS credentials the environment holds."""
    if args.source:
        return ds.dataset(os.path.join(args.source, LAYERS[layer]), format="parquet")
    if args.release == "latest":
        args.release = latest_release()
        print(f"Using Overture release {args.release}", file=sys.stderr)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+", args.release or ""):
        sys.exit("--release must look like 2026-10-22.0, or 'latest' (see docs.overturemaps.org/release)")
    from pyarrow.fs import AwsStandardS3RetryStrategy, S3FileSystem

    # The SDK's defaults give up within seconds (a name that takes 3 s to resolve, a read that stalls 3 s on macOS) and
    # after three tries: on a slow or unsteady line one file in hundreds then fails and the whole city is built again.
    # Patient timeouts and ten tries per request let that file wait and try again on its own.
    fs = S3FileSystem(anonymous=True, region=S3_REGION, connect_timeout=30, request_timeout=300,
                      retry_strategy=AwsStandardS3RetryStrategy(max_attempts=10))
    return ds.dataset(f"{S3_BUCKET}/{args.release}/{LAYERS[layer]}", filesystem=fs, format="parquet")


ARTERIAL = {"motorway", "trunk", "primary", "secondary", "tertiary"}
UTM = Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True)
# GeoParquet 1.0 metadata: WKB in lon/lat (OGC:CRS84 when no crs is given), as DuckDB's spatial COPY wrote it.
GEO_META = {b"geo": json.dumps({"version": "1.0.0", "primary_column": "geometry",
                                 "columns": {"geometry": {"encoding": "WKB", "geometry_types": []}}}).encode()}


def bbox_filter(w, s, e, n):
    # Overture rows carry a bbox struct; filtering on it lets the reader skip row groups by their statistics.
    b = lambda k: pc.field("bbox", k)
    return (b("xmin") <= e) & (b("xmax") >= w) & (b("ymin") <= n) & (b("ymax") >= s)


def read(dset, columns, flt):
    """Rows matching `flt` as Python dicts, with only the columns this release has."""
    have = set(dset.schema.names)
    return dset.to_table(columns=[c for c in columns if c in have], filter=flt).to_pylist()


def clipped(rows, w, s, e, n):
    """Rows whose geometry touches the box, with a shapely geometry under "_g"."""
    if not rows:
        return []
    geoms = shapely.from_wkb([r["geometry"] for r in rows])
    keep = shapely.intersects(geoms, shapely.box(w, s, e, n))
    out = []
    for r, g, k in zip(rows, geoms, keep):
        if k:
            r["_g"] = g
            out.append(r)
    return out


def first_dataset(sources):
    return (sources or [{}])[0].get("dataset") if sources else None


def primary(struct):
    return (struct or {}).get("primary")


def utm(g):
    return shapely.transform(g, lambda xy: np.column_stack(UTM.transform(xy[:, 0], xy[:, 1])))


def write(rows, schema, path):
    pq.write_table(pa.Table.from_pylist(rows, schema=schema.with_metadata(GEO_META)), path)


def count(args):
    w, s, e, n = load_aoi(args.aoi, args.name)
    flt = bbox_filter(w, s, e, n)
    by_source = defaultdict(lambda: [0, 0, 0])
    for r in read(source_dataset(args, "buildings"), ["sources", "height", "num_floors", "bbox"], flt):
        c = by_source[first_dataset(r.get("sources")) or "unknown"]
        c[0] += 1
        c[1] += r.get("height") is not None
        c[2] += r.get("num_floors") is not None
    by_class = defaultdict(int)
    for r in read(source_dataset(args, "roads"), ["class", "subtype", "bbox"], flt & (pc.field("subtype") == "road")):
        by_class[r.get("class") or "unclassified"] += 1
    b = sorted(by_source.items(), key=lambda x: -x[1][0])
    rc = sorted(by_class.items(), key=lambda x: -x[1])
    result = {
        "aoi": args.name,
        "bbox": [w, s, e, n],
        "release": args.release or args.source,
        "buildings_by_source": [
            {"source": k, "buildings": v[0], "with_height": v[1], "with_floors": v[2]} for k, v in b
        ],
        "road_segments_by_class": [{"class": k, "segments": v} for k, v in rc],
        "buildings_total": sum(v[0] for _, v in b),
        "road_segments_total": sum(v for _, v in rc),
    }
    print(json.dumps(result, indent=2))
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, f"{args.name}-counts.json"), "w") as f:
            json.dump(result, f, indent=2)


BUILDINGS = pa.schema([("id", pa.string()), ("source", pa.string()), ("height", pa.float64()),
                       ("num_floors", pa.int32()), ("class", pa.string()), ("subtype", pa.string()),
                       ("name", pa.string()), ("geometry", pa.binary())])
ROADS = pa.schema([("id", pa.string()), ("class", pa.string()), ("subclass", pa.string()), ("name", pa.string()),
                   ("length_m", pa.float64()), ("geometry", pa.binary())])
PLACES = pa.schema([("id", pa.string()), ("source", pa.string()), ("name", pa.string()), ("category", pa.string()),
                    ("confidence", pa.float64()), ("status", pa.string()), ("brand", pa.string()),
                    ("websites", pa.int64()), ("socials", pa.int64()), ("phones", pa.int64()),
                    ("datasets", pa.list_(pa.string())), ("area_m2", pa.float64()), ("geometry", pa.binary())])


def export(args):
    if not args.out:
        sys.exit("export needs --out")
    layers = args.layers.split(",") if args.layers else list(EXPORTS)
    unknown = sorted(set(layers) - set(EXPORTS))
    if unknown:
        sys.exit(f"--layers takes {', '.join(EXPORTS)}, not {', '.join(unknown)}")
    w, s, e, n = load_aoi(args.aoi, args.name)
    os.makedirs(args.out, exist_ok=True)
    flt = bbox_filter(w, s, e, n)
    if "buildings" in layers:
        rows = clipped(read(source_dataset(args, "buildings"),
                            ["id", "sources", "height", "num_floors", "class", "subtype", "names", "geometry", "bbox"],
                            flt), w, s, e, n)
        write([{"id": r["id"], "source": first_dataset(r.get("sources")), "height": r.get("height"),
                "num_floors": r.get("num_floors"), "class": r.get("class"), "subtype": r.get("subtype"),
                "name": primary(r.get("names")), "geometry": r["geometry"]} for r in rows],
              BUILDINGS, os.path.join(args.out, "buildings.parquet"))
    if "places" in layers:
        export_places(args, flt, (w, s, e, n))
    if "roads" not in layers:
        print(f"wrote {', '.join(x + '.parquet' for x in layers)} in {args.out}")
        return
    rows = clipped(read(source_dataset(args, "roads"), ["id", "class", "subclass", "subtype", "names", "geometry", "bbox"],
                        flt & (pc.field("subtype") == "road")), w, s, e, n)
    roads = os.path.join(args.out, "roads.parquet")
    write([{"id": r["id"], "class": r.get("class"), "subclass": r.get("subclass"), "name": primary(r.get("names")),
            "length_m": float(shapely.length(utm(r["_g"]))), "geometry": r["geometry"]} for r in rows], ROADS, roads)
    summary = summarize_roads(roads)
    summary.update({"aoi": args.name, "release": args.release or args.source})
    with open(os.path.join(args.out, "roads-summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(f"wrote {', '.join(x + '.parquet' for x in layers)} and roads-summary.json in {args.out}; "
          f"{summary['road_km_total']} km of road, {summary['road_km_arterial']} km arterial")


def export_places(args, flt, box):
    """places.parquet: one row per named place, as a point, from three sources (column "source"):
    "places", Overture's places (Meta, Microsoft, Foursquare, AllThePlaces...), with their category, confidence and
    how much is known of them (websites, socials, phones, a brand); "land_use" and "infrastructure", OpenStreetMap's
    named land use and stations from the base theme, with their area (m2) and a confidence of 1."""
    out = []
    cols = ["id", "names", "basic_category", "taxonomy", "confidence", "operating_status", "brand", "websites",
            "socials", "phones", "sources", "geometry", "bbox"]
    for r in clipped(read(source_dataset(args, "places"), cols, flt), *box):
        name = primary(r.get("names"))
        if name is None:
            continue
        brand = r.get("brand") or {}
        out.append({"id": r["id"], "source": "places", "name": name,
                    "category": r.get("basic_category") or primary(r.get("taxonomy")),
                    "confidence": r.get("confidence"), "status": r.get("operating_status"),
                    "brand": primary(brand.get("names")) or brand.get("wikidata"),
                    "websites": len(r.get("websites") or []), "socials": len(r.get("socials") or []),
                    "phones": len(r.get("phones") or []),
                    "datasets": [x.get("dataset") for x in r.get("sources") or []], "area_m2": 0.0,
                    "geometry": r["geometry"]})
    for layer, classes in BASE_CLASSES.items():
        f = flt & pc.field("class").isin(list(classes))
        for r in clipped(read(source_dataset(args, layer), ["id", "names", "class", "geometry", "bbox"], f), *box):
            name = primary(r.get("names"))
            if name is None:
                continue
            g = r["_g"]
            out.append({"id": r["id"], "source": layer, "name": name, "category": r.get("class"), "confidence": 1.0,
                        "status": None, "brand": None, "websites": 0, "socials": 0, "phones": 0, "datasets": None,
                        "area_m2": float(shapely.area(utm(g))),
                        "geometry": shapely.to_wkb(shapely.point_on_surface(g))})
    write(out, PLACES, os.path.join(args.out, "places.parquet"))


def summarize_roads(roads):
    """Kilometres of road by class in an exported roads.parquet."""
    agg = defaultdict(lambda: [0, 0.0])
    t = pq.read_table(roads, columns=["class", "length_m"]).to_pylist()
    for r in t:
        a = agg[r["class"] or "unclassified"]
        a[0] += 1
        a[1] += r["length_m"] or 0.0
    by_class = sorted(((k, v[0], v[1]) for k, v in agg.items()), key=lambda x: -x[2])
    return {
        "note": "segments that touch the area, each measured whole",
        "road_km_total": round(sum(x[2] for x in by_class) / 1000, 1),
        "road_km_arterial": round(sum(x[2] for x in by_class if x[0] in ARTERIAL) / 1000, 1),
        "road_km_by_class": [
            {"class": x[0], "segments": x[1], "km": round(x[2] / 1000, 1)} for x in by_class
        ],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["count", "export"])
    ap.add_argument("--release", help="Overture release, for example 2026-10-22.0, or 'latest'")
    ap.add_argument("--source", help="local folder holding theme=.../type=... partitions, instead of S3")
    ap.add_argument("--aoi", default=os.path.join(os.path.dirname(__file__), "..", "aoi", "slices.geojson"))
    ap.add_argument("--name", default="vertical-slice", help="feature name in the AOI file")
    ap.add_argument("--out", help="output folder (default: print only)")
    ap.add_argument("--layers", help=f"export only these, comma-separated (default: {','.join(EXPORTS)})")
    args = ap.parse_args()
    {"count": count, "export": export}[args.mode](args)


if __name__ == "__main__":
    main()
