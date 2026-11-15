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
    "places": "theme=places/type=place/*",
    "land_use": "theme=base/type=land_use/*",
    "infrastructure": "theme=base/type=infrastructure/*",
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
        # Overture's bucket is public: read it anonymously, whatever AWS credentials the environment holds.
        con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2'; "
                    "SET s3_access_key_id=''; SET s3_secret_access_key=''; SET s3_session_token='';")
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
    layers = args.layers.split(",") if args.layers else list(EXPORTS)
    unknown = sorted(set(layers) - set(EXPORTS))
    if unknown:
        sys.exit(f"--layers takes {', '.join(EXPORTS)}, not {', '.join(unknown)}")
    w, s, e, n = load_aoi(args.aoi, args.name)
    con = connect(args, spatial=True)
    os.makedirs(args.out, exist_ok=True)
    flt = bbox_filter(w, s, e, n)
    clip = f"ST_Intersects(geometry, ST_MakeEnvelope({w}, {s}, {e}, {n}))"
    if "buildings" in layers:
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
    if "places" in layers:
        export_places(con, args, flt, clip)
    if "roads" not in layers:
        print(f"wrote {', '.join(x + '.parquet' for x in layers)} in {args.out}")
        return
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
    print(f"wrote {', '.join(x + '.parquet' for x in layers)} and roads-summary.json in {args.out}; "
          f"{summary['road_km_total']} km of road, {summary['road_km_arterial']} km arterial")


def export_places(con, args, flt, clip):
    """places.parquet: one row per named place, as a point, from three sources (column "source"):
    "places", Overture's places (Meta, Microsoft, Foursquare, AllThePlaces...), with their category, confidence and
    how much is known of them (websites, socials, phones, a brand); "land_use" and "infrastructure", OpenStreetMap's
    named land use and stations from the base theme, with their area (m2) and a confidence of 1."""
    utm = "ST_Transform(geometry, 'EPSG:4326', 'EPSG:32630', always_xy := true)"
    base = lambda layer: f"""
        SELECT id, '{layer}' AS source, names.primary AS name, class AS category, 1.0::DOUBLE AS confidence,
               NULL::VARCHAR AS status, NULL::VARCHAR AS brand, 0 AS websites, 0 AS socials, 0 AS phones,
               NULL::VARCHAR[] AS datasets, ST_Area({utm})::DOUBLE AS area_m2, ST_PointOnSurface(geometry) AS geometry
        FROM read_parquet('{source_glob(args, layer)}', hive_partitioning=1)
        WHERE {flt} AND names.primary IS NOT NULL AND class IN ({", ".join(f"'{c}'" for c in BASE_CLASSES[layer])})
          AND {clip}"""
    con.execute(
        f"""
        COPY (
          SELECT id, 'places' AS source, names.primary AS name, coalesce(basic_category, taxonomy.primary) AS category,
                 confidence, operating_status AS status, coalesce(brand.names.primary, brand.wikidata) AS brand,
                 coalesce(len(websites), 0) AS websites, coalesce(len(socials), 0) AS socials,
                 coalesce(len(phones), 0) AS phones, list_transform(sources, lambda x: x.dataset) AS datasets,
                 0.0::DOUBLE AS area_m2, geometry
          FROM read_parquet('{source_glob(args, "places")}', hive_partitioning=1)
          WHERE {flt} AND names.primary IS NOT NULL AND {clip}
          UNION ALL {base("land_use")}
          UNION ALL {base("infrastructure")}
        ) TO '{os.path.join(args.out, "places.parquet")}' (FORMAT parquet)
        """
    )


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
    ap.add_argument("--layers", help=f"export only these, comma-separated (default: {','.join(EXPORTS)})")
    args = ap.parse_args()
    {"count": count, "export": export}[args.mode](args)


if __name__ == "__main__":
    main()
