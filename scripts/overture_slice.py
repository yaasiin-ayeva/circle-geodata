"""Count or extract Overture Maps buildings and roads inside a CIRCLE area of interest.

Examples
--------
Count buildings by source and roads by class for the vertical slice:

    python3 scripts/overture_slice.py count --release latest

Extract both layers to GeoParquet (needs DuckDB's spatial extension):

    python3 scripts/overture_slice.py export --release 2026-10-22.0 --out data/work/slice

The release name comes from https://docs.overturemaps.org/release/ (pick the
latest; the value above is only an example). Data is read straight from
Overture's public S3 bucket, so the machine needs internet access.
Use --source to point at a local copy of the release instead.

Overture buildings and roads are ODbL: see ../ATTRIBUTION.md.
"""
import argparse
import json
import os
import re
import sys

import duckdb

S3_ROOT = "s3://overturemaps-us-west-2/release"
LAYERS = {
    "buildings": "theme=buildings/type=building/*",
    "roads": "theme=transportation/type=segment/*",
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


def source_glob(args, layer):
    if args.source:
        return os.path.join(args.source, LAYERS[layer])
    if args.release == "latest":
        args.release = latest_release()
        print(f"Using Overture release {args.release}", file=sys.stderr)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+", args.release or ""):
        sys.exit("--release must look like 2026-10-22.0, or 'latest' (see docs.overturemaps.org/release)")
    return f"{S3_ROOT}/{args.release}/{LAYERS[layer]}"


def connect(args, spatial):
    con = duckdb.connect()
    if not args.source:
        con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    if spatial:
        con.execute("INSTALL spatial; LOAD spatial;")
    return con


ARTERIAL = {"motorway", "trunk", "primary", "secondary", "tertiary"}


def bbox_filter(w, s, e, n):
    # Overture rows carry a bbox struct; filtering on it lets DuckDB skip row groups.
    return (
        f"bbox.xmin <= {e} AND bbox.xmax >= {w} "
        f"AND bbox.ymin <= {n} AND bbox.ymax >= {s}"
    )


def count(args):
    w, s, e, n = load_aoi(args.aoi, args.name)
    con = connect(args, spatial=False)
    flt = bbox_filter(w, s, e, n)
    b = con.execute(
        f"""
        SELECT coalesce(sources[1].dataset, 'unknown') AS source,
               count(*) AS buildings,
               count(height) AS with_height,
               count(num_floors) AS with_floors
        FROM read_parquet('{source_glob(args, "buildings")}', hive_partitioning=1)
        WHERE {flt}
        GROUP BY ALL ORDER BY buildings DESC
        """
    ).fetchall()
    r = con.execute(
        f"""
        SELECT coalesce(class, 'unclassified') AS class, count(*) AS segments
        FROM read_parquet('{source_glob(args, "roads")}', hive_partitioning=1)
        WHERE subtype = 'road' AND {flt}
        GROUP BY ALL ORDER BY segments DESC
        """
    ).fetchall()
    result = {
        "aoi": args.name,
        "bbox": [w, s, e, n],
        "release": args.release or args.source,
        "buildings_by_source": [
            {"source": x[0], "buildings": x[1], "with_height": x[2], "with_floors": x[3]} for x in b
        ],
        "road_segments_by_class": [{"class": x[0], "segments": x[1]} for x in r],
        "buildings_total": sum(x[1] for x in b),
        "road_segments_total": sum(x[1] for x in r),
    }
    print(json.dumps(result, indent=2))
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, f"{args.name}-counts.json"), "w") as f:
            json.dump(result, f, indent=2)


def export(args):
    if not args.out:
        sys.exit("export needs --out")
    w, s, e, n = load_aoi(args.aoi, args.name)
    con = connect(args, spatial=True)
    os.makedirs(args.out, exist_ok=True)
    flt = bbox_filter(w, s, e, n)
    clip = f"ST_Intersects(geometry, ST_MakeEnvelope({w}, {s}, {e}, {n}))"
    con.execute(
        f"""
        COPY (
          SELECT id, sources[1].dataset AS source, height, num_floors, class, subtype,
                 names.primary AS name, geometry
          FROM read_parquet('{source_glob(args, "buildings")}', hive_partitioning=1)
          WHERE {flt} AND {clip}
        ) TO '{os.path.join(args.out, "buildings.parquet")}' (FORMAT parquet)
        """
    )
    con.execute(
        f"""
        COPY (
          SELECT id, class, subclass, names.primary AS name,
                 ST_Length(ST_Transform(geometry, 'EPSG:4326', 'EPSG:32630', always_xy := true)) AS length_m,
                 geometry
          FROM read_parquet('{source_glob(args, "roads")}', hive_partitioning=1)
          WHERE subtype = 'road' AND {flt} AND {clip}
        ) TO '{os.path.join(args.out, "roads.parquet")}' (FORMAT parquet)
        """
    )
    roads = os.path.join(args.out, "roads.parquet")
    summary = summarize_roads(con, roads)
    summary.update({"aoi": args.name, "release": args.release or args.source})
    with open(os.path.join(args.out, "roads-summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(f"wrote {args.out}/buildings.parquet, roads.parquet and roads-summary.json; "
          f"{summary['road_km_total']} km of road, {summary['road_km_arterial']} km arterial")


def summarize_roads(con, roads):
    """Kilometres of road by class in an exported roads.parquet."""
    by_class = con.execute(
        f"""
        SELECT coalesce(class, 'unclassified') AS class, count(*) AS segments, sum(length_m)::DOUBLE AS m
        FROM '{roads}' GROUP BY ALL ORDER BY m DESC
        """
    ).fetchall()
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
    args = ap.parse_args()
    {"count": count, "export": export}[args.mode](args)


if __name__ == "__main__":
    main()
